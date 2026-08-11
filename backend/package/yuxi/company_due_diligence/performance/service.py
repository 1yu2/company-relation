"""企业尽调性能评测 Tasker 与 MinIO 产物服务。"""

from __future__ import annotations

import asyncio
import os
from enum import StrEnum

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
        minio_client: MinIOClient | None = None,
        bucket_name: str = PERFORMANCE_EVALUATION_BUCKET,
    ) -> None:
        self._runner = runner or PerformanceEvaluationRunner()
        self._minio = minio_client or get_minio_client()
        self._bucket_name = bucket_name

    async def create_run(self, request: PerformanceRunRequest, tasker: Tasker):
        """提交经过安全参数校验的性能任务。"""

        async def run_evaluation(context: TaskContext):
            """执行单个性能运行并发布 JSON、Markdown。"""
            report = await self._runner.run(request, run_id=context.task_id, context=context)
            await context.raise_if_cancelled()
            report = await self._publish(report)
            result = report.model_dump(mode="json")
            await context.set_result(result)
            return result

        timeout_seconds = request.duration_seconds * len(request.scenarios) + request.request_timeout_seconds + 30
        return await tasker.enqueue(
            name="企业尽调性能评测",
            task_type=PERFORMANCE_EVALUATION_TASK_TYPE,
            payload=request.model_dump(mode="json"),
            coroutine=run_evaluation,
            timeout_seconds=timeout_seconds,
        )

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
