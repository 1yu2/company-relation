"""企业尽调性能评测 Tasker 与 MinIO 产物服务。"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from yuxi.company_due_diligence.evaluation.catalog import (
    AutoDatasetUnavailableError,
    AutoEvaluationDatasetBuilder,
    auto_evaluation_dataset_builder,
)
from yuxi.company_due_diligence.models import CompanyReference, LatencyBreakdown
from yuxi.company_due_diligence.performance.models import (
    PerformanceArtifact,
    PerformanceEvaluationReport,
    PerformanceRunRequest,
)
from yuxi.company_due_diligence.performance.report import (
    checksum,
    render_performance_json,
    render_performance_markdown,
)
from yuxi.company_due_diligence.performance.runner import PerformanceEvaluationRunner
from yuxi.company_due_diligence.performance.runner import latency_statistics
from yuxi.services.task_service import TaskContext, Tasker
from yuxi.storage.minio import MinIOClient, StorageError, get_minio_client

PERFORMANCE_EVALUATION_TASK_TYPE = "company_due_diligence_performance_evaluation"
PERFORMANCE_EVALUATION_BUCKET = os.getenv("COMPANY_DD_EVALUATION_BUCKET", "company-due-diligence")


class PerformanceExportFormat(StrEnum):
    """描述性能评测允许下载的格式。"""

    JSON = "json"
    MARKDOWN = "markdown"


class PerformanceEvaluationService:
    """管理性能后台任务、运行详情和可校验导出。"""

    def __init__(
        self,
        *,
        runner: PerformanceEvaluationRunner | None = None,
        auto_builder: AutoEvaluationDatasetBuilder | None = None,
        minio_client: MinIOClient | None = None,
        bucket_name: str = PERFORMANCE_EVALUATION_BUCKET,
    ) -> None:
        self._runner = runner or PerformanceEvaluationRunner()
        self._auto_builder = auto_builder or auto_evaluation_dataset_builder
        self._minio = minio_client or get_minio_client()
        self._bucket_name = bucket_name

    async def create_run(self, request: PerformanceRunRequest, tasker: Tasker):
        """提交经过安全参数校验的性能任务。"""
        if not request.company_refs:
            snapshot = await self._auto_builder.scan(
                sampling_policy=request.sampling_policy,
                max_samples=20,
            )
            if not snapshot.source_record_keys:
                raise AutoDatasetUnavailableError("企业数据目录为空，无法生成全局性能样本")
            request = request.model_copy(
                update={
                    "company_refs": [CompanyReference(eid=eid) for eid in snapshot.source_record_keys[:20]],
                }
            )

        async def run_evaluation(context: TaskContext):
            """执行单个性能运行并发布 JSON、Markdown。"""
            task_snapshot = await tasker.get_task(context.task_id)
            report = await self._runner.run(
                request,
                run_id=context.task_id,
                context=context,
                queue_wait_ms=_task_queue_wait_ms(task_snapshot),
                worker_count=tasker.worker_count,
            )
            await context.raise_if_cancelled()
            report = await self._publish(report)
            result = report.model_dump(mode="json")
            await context.set_result(result)
            return result

        timeout_seconds = request.duration_seconds * len(request.scenarios) + request.request_timeout_seconds + 30
        return await tasker.enqueue(
            name="企业尽调性能评测",
            task_type=PERFORMANCE_EVALUATION_TASK_TYPE,
            payload={
                "scope": request.scope,
                "sampling_policy": request.sampling_policy,
                "sample_size": len(request.company_refs),
                "scenarios": [item.value for item in request.scenarios],
                "safety_mode": request.safety_mode.value,
                "concurrency": request.concurrency,
                "request_count": request.request_count,
                "duration_seconds": request.duration_seconds,
            },
            coroutine=run_evaluation,
            timeout_seconds=timeout_seconds,
        )

    async def get_global_summary(self, tasker: Tasker, *, window_minutes: int = 1440) -> dict:
        """聚合时间窗口内全部性能运行，不返回企业身份明细。"""
        cutoff = datetime.now(UTC) - timedelta(minutes=window_minutes)
        result = await tasker.list_tasks(limit=1000)
        tasks = [task for task in result["tasks"] if task["type"] == PERFORMANCE_EVALUATION_TASK_TYPE]
        reports = []
        cancelled = 0
        for task in tasks:
            if str(task.get("status")) in {"cancelled", "canceled"}:
                cancelled += 1
            report = await self._read_report(task["id"])
            if report is not None and report.completed_at >= cutoff:
                reports.append(report)

        requests = [request for report in reports for scenario in report.scenarios for request in scenario.requests]
        total = len(requests)
        succeeded = sum(item.status.value == "success" for item in requests)
        failed = sum(item.status.value == "error" for item in requests)
        timed_out = sum(item.status.value == "timeout" for item in requests)
        elapsed_seconds = sum(report.global_summary.elapsed_seconds for report in reports)
        successful = [item for item in requests if item.status.value == "success"]
        average_stage = _average_stage_latency(successful)
        latency = latency_statistics(item.duration_ms for item in requests)
        scenario_counts: dict[str, int] = {}
        for report in reports:
            for scenario, count in report.global_summary.by_scenario.items():
                scenario_counts[scenario] = scenario_counts.get(scenario, 0) + count
        queue_waits = [float(report.runtime_observations.get("tasker_queue_wait_ms") or 0) for report in reports]
        queue_latency = latency_statistics(queue_waits)
        denominator = total + cancelled
        return {
            "window_minutes": window_minutes,
            "run_count": len(reports),
            "total_requests": total,
            "succeeded": succeeded,
            "failed": failed,
            "timed_out": timed_out,
            "cancelled_runs": cancelled,
            "throughput_rps": round(total / elapsed_seconds, 6) if elapsed_seconds else 0,
            "error_rate": failed / total if total else 0,
            "timeout_rate": timed_out / total if total else 0,
            "cancellation_rate": cancelled / denominator if denominator else 0,
            "latency": latency.model_dump(mode="json"),
            "average_stage_latency": average_stage.model_dump(mode="json"),
            "by_scenario": scenario_counts,
            "by_status": _count_values(item.status.value for item in requests),
            "by_interface": _count_values(item.interface for item in requests),
            "by_intent": _count_values(item.intent for item in requests),
            "by_toolchain": _count_values(tool for item in requests for tool in item.toolchain),
            "by_data_source": {
                "identity": average_stage.resolution_ms,
                "mysql": average_stage.mysql_ms,
                "neo4j": average_stage.neo4j_ms,
                "milvus": average_stage.milvus_ms,
                "llm": average_stage.llm_ms,
            },
            "by_data_source_requests": _count_values(source for item in requests for source in item.data_sources),
            "runtime": {
                "tasker_queue_wait_p50_ms": queue_latency.p50_ms,
                "tasker_queue_wait_p95_ms": queue_latency.p95_ms,
                "tasker_queue_wait_p99_ms": queue_latency.p99_ms,
                "tasker_worker_count": max(
                    (int(report.runtime_observations.get("tasker_worker_count") or 1) for report in reports),
                    default=0,
                ),
                "mysql_pool_wait_ms_delta": round(
                    sum(float(report.runtime_observations.get("mysql_pool_wait_ms_delta") or 0) for report in reports),
                    3,
                ),
                "mysql_pool_timeout_delta": sum(
                    int(report.runtime_observations.get("mysql_pool_timeout_delta") or 0) for report in reports
                ),
            },
        }

    async def list_runs(self, tasker: Tasker, limit: int = 100) -> list[dict]:
        """返回性能评测 Tasker 摘要。"""
        result = await tasker.list_tasks(limit=1000)
        return [task for task in result["tasks"] if task["type"] == PERFORMANCE_EVALUATION_TASK_TYPE][:limit]

    async def get_run(self, run_id: str, tasker: Tasker) -> dict:
        """返回任务部分结果和已发布最终报告。"""
        task = await tasker.get_task(run_id)
        if task is None or task["type"] != PERFORMANCE_EVALUATION_TASK_TYPE:
            raise FileNotFoundError("性能评测运行不存在")
        report = await self._read_report(run_id)
        return {"task": task, "report": report.model_dump(mode="json") if report else None}

    async def cancel(self, run_id: str, tasker: Tasker) -> bool:
        """取消非终态性能评测任务。"""
        task = await tasker.get_task(run_id)
        if task is None or task["type"] != PERFORMANCE_EVALUATION_TASK_TYPE:
            raise FileNotFoundError("性能评测运行不存在")
        return await tasker.cancel_task(run_id)

    async def download(self, run_id: str, export_format: PerformanceExportFormat):
        """下载并校验性能报告产物。"""
        report = await self._read_report(run_id)
        if report is None:
            raise FileNotFoundError("性能评测运行尚未完成或不存在")
        artifact = next((item for item in report.artifacts if item.format == export_format.value), None)
        if artifact is None:
            raise FileNotFoundError(f"性能评测产物不存在: {export_format.value}")
        content = await self._minio.adownload_file(artifact.bucket, artifact.object_name)
        if checksum(content) != artifact.checksum:
            raise RuntimeError("性能评测产物 checksum 校验失败")
        return content, artifact

    async def _publish(self, report: PerformanceEvaluationReport) -> PerformanceEvaluationReport:
        """上传 JSON、Markdown，最后写原子 record.json。"""
        contents = {
            PerformanceExportFormat.JSON: render_performance_json(report),
            PerformanceExportFormat.MARKDOWN: render_performance_markdown(report),
        }
        suffixes = {
            PerformanceExportFormat.JSON: "json",
            PerformanceExportFormat.MARKDOWN: "md",
        }
        artifacts = []
        for export_format, content in contents.items():
            object_name = f"evaluations/performance/{report.run_id}/report.{suffixes[export_format]}"
            await self._minio.aupload_file(self._bucket_name, object_name, content)
            artifacts.append(
                PerformanceArtifact(
                    format=export_format.value,
                    bucket=self._bucket_name,
                    object_name=object_name,
                    checksum=checksum(content),
                    size_bytes=len(content),
                )
            )
        report.artifacts = artifacts
        await self._minio.aupload_file(
            self._bucket_name,
            f"evaluations/performance/{report.run_id}/record.json",
            report.model_dump_json(indent=2).encode(),
            "application/json",
        )
        return report

    async def _read_report(self, run_id: str) -> PerformanceEvaluationReport | None:
        """读取性能评测原子记录。"""
        try:
            bucket_exists = await asyncio.to_thread(self._minio.client.bucket_exists, self._bucket_name)
            if not bucket_exists:
                return None
            object_name = f"evaluations/performance/{run_id}/record.json"
            if await self._minio.astat_file(self._bucket_name, object_name) is None:
                return None
            return PerformanceEvaluationReport.model_validate_json(
                await self._minio.adownload_file(self._bucket_name, object_name)
            )
        except StorageError:
            return None


performance_evaluation_service = PerformanceEvaluationService()


def _average_stage_latency(requests) -> LatencyBreakdown:
    """计算全局成功请求的阶段平均耗时。"""
    if not requests:
        return LatencyBreakdown()
    return LatencyBreakdown(
        **{
            field: round(sum(getattr(item.latency, field) for item in requests) / len(requests), 3)
            for field in LatencyBreakdown.model_fields
        }
    )


def _task_queue_wait_ms(task: dict | None) -> float:
    """根据 Tasker 的创建与开始时间计算排队等待。"""
    if not task or not task.get("created_at") or not task.get("started_at"):
        return 0
    created_at = datetime.fromisoformat(str(task["created_at"]).replace("Z", "+00:00"))
    started_at = datetime.fromisoformat(str(task["started_at"]).replace("Z", "+00:00"))
    return round(max(0, (started_at - created_at).total_seconds() * 1000), 3)


def _count_values(values) -> dict[str, int]:
    """统计全局性能报告中的非空脱敏维度。"""
    counts: dict[str, int] = {}
    for value in values:
        if not value:
            continue
        counts[value] = counts.get(value, 0) + 1
    return counts
