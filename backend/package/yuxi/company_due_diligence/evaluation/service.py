"""企业尽调质量评测 Tasker、数据集、MinIO 产物服务。"""

from __future__ import annotations

import asyncio
import os
from enum import StrEnum

from yuxi.company_due_diligence.evaluation.catalog import (
    AutoEvaluationDatasetBuilder,
    auto_evaluation_dataset_builder,
)
from yuxi.company_due_diligence.evaluation.dataset import EvaluationDatasetRepository
from yuxi.company_due_diligence.evaluation.report import (
    checksum,
    render_quality_json,
    render_quality_jsonl,
    render_quality_markdown,
)
from yuxi.company_due_diligence.evaluation.runner import (
    QualityArtifact,
    QualityEvaluationReport,
    QualityEvaluationRequest,
    QualityEvaluationRunner,
)
from yuxi.services.task_service import TaskContext, Tasker
from yuxi.storage.minio import MinIOClient, StorageError, get_minio_client

QUALITY_EVALUATION_TASK_TYPE = "company_due_diligence_quality_evaluation"
EVALUATION_BUCKET = os.getenv("COMPANY_DD_EVALUATION_BUCKET", "company-due-diligence")


class QualityExportFormat(StrEnum):
    """描述质量评测允许下载的格式。"""

    JSON = "json"
    JSONL = "jsonl"
    MARKDOWN = "markdown"


class QualityEvaluationService:
    """管理冻结数据集、质量后台任务和版本化导出。"""

    def __init__(
        self,
        *,
        datasets: EvaluationDatasetRepository | None = None,
        runner: QualityEvaluationRunner | None = None,
        auto_builder: AutoEvaluationDatasetBuilder | None = None,
        minio_client: MinIOClient | None = None,
        bucket_name: str = EVALUATION_BUCKET,
    ) -> None:
        self._datasets = datasets or EvaluationDatasetRepository()
        self._runner = runner or QualityEvaluationRunner()
        self._auto_builder = auto_builder or auto_evaluation_dataset_builder
        self._minio = minio_client or get_minio_client()
        self._bucket_name = bucket_name

    def list_datasets(self) -> list[dict]:
        """返回数据集版本、checksum、来源类别和人工复核率。"""
        return [
            {
                "dataset_id": dataset.dataset_id,
                "name": dataset.name,
                "version": dataset.version,
                "source_kind": dataset.source_kind.value,
                "business_as_of": dataset.business_as_of,
                "checksum": dataset.checksum,
                "sample_count": len(dataset.samples),
                "human_review_rate": dataset.human_review_rate,
            }
            for dataset in self._datasets.list()
        ]

    def get_dataset(self, dataset_id: str) -> dict:
        """返回数据集详情但不展开潜在敏感预测结果。"""
        dataset = self._datasets.get(dataset_id)
        payload = dataset.model_dump(mode="json")
        payload["checksum"] = dataset.checksum
        payload["human_review_rate"] = dataset.human_review_rate
        return payload

    async def create_run(self, request: QualityEvaluationRequest, tasker: Tasker):
        """校验数据集后提交质量评测任务。"""
        if request.dataset_id is not None:
            dataset = self._datasets.get(request.dataset_id)
        else:
            dataset = await self._auto_builder.build(
                dataset_version=request.dataset_version,
                sampling_policy=request.sampling_policy,
                max_samples=request.max_samples or 1000,
                case_families=request.case_families,
            )

        async def run_evaluation(context: TaskContext):
            """执行单个质量评测并发布三种产物。"""
            report = await self._runner.run(
                dataset,
                run_id=context.task_id,
                max_samples=request.max_samples,
                strategy_label=request.strategy_label,
                context=context,
            )
            await context.raise_if_cancelled()
            report = await self._publish(report)
            result = report.model_dump(mode="json")
            await context.set_result(result)
            return result

        return await tasker.enqueue(
            name=f"企业尽调质量评测：{dataset.name}",
            task_type=QUALITY_EVALUATION_TASK_TYPE,
            payload={
                "dataset_id": dataset.dataset_id,
                "dataset_checksum": dataset.checksum,
                "max_samples": request.max_samples,
                "strategy_label": request.strategy_label,
                "scope": dataset.scope,
                "sampling_policy": dataset.sampling_policy,
                "denominator": dataset.denominator,
                "sampled_denominator": dataset.sampled_denominator,
            },
            coroutine=run_evaluation,
        )

    async def get_coverage(self, *, sampling_policy: str = "all_or_stratified") -> dict:
        """返回无需企业输入的全局数据目录覆盖摘要。"""
        snapshot = await self._auto_builder.scan(sampling_policy=sampling_policy, max_samples=1000)
        return snapshot.model_dump(mode="json")

    async def list_runs(self, tasker: Tasker, limit: int = 100) -> list[dict]:
        """返回质量评测 Tasker 摘要。"""
        result = await tasker.list_tasks(limit=1000)
        return [task for task in result["tasks"] if task["type"] == QUALITY_EVALUATION_TASK_TYPE][:limit]

    async def get_run(self, run_id: str, tasker: Tasker) -> dict:
        """返回任务和已发布完整运行记录。"""
        task = await tasker.get_task(run_id)
        if task is None or task["type"] != QUALITY_EVALUATION_TASK_TYPE:
            raise FileNotFoundError("质量评测运行不存在")
        report = await self._read_report(run_id)
        return {"task": task, "report": report.model_dump(mode="json") if report else None}

    async def cancel(self, run_id: str, tasker: Tasker) -> bool:
        """取消非终态质量评测任务。"""
        task = await tasker.get_task(run_id)
        if task is None or task["type"] != QUALITY_EVALUATION_TASK_TYPE:
            raise FileNotFoundError("质量评测运行不存在")
        return await tasker.cancel_task(run_id)

    async def download(self, run_id: str, export_format: QualityExportFormat) -> tuple[bytes, dict]:
        """下载并校验质量评测产物。"""
        report = await self._read_report(run_id)
        if report is None:
            raise FileNotFoundError("质量评测运行尚未完成或不存在")
        artifact = next((item for item in report.artifacts if item.format == export_format.value), None)
        if artifact is None:
            raise FileNotFoundError(f"质量评测产物不存在: {export_format.value}")
        content = await self._minio.adownload_file(artifact.bucket, artifact.object_name)
        if checksum(content) != artifact.checksum:
            raise RuntimeError("质量评测产物 checksum 校验失败")
        return content, artifact

    async def _publish(self, report: QualityEvaluationReport) -> QualityEvaluationReport:
        """上传 JSON、JSONL、Markdown，最后写 record.json。"""
        contents = {
            QualityExportFormat.JSON: render_quality_json(report),
            QualityExportFormat.JSONL: render_quality_jsonl(report),
            QualityExportFormat.MARKDOWN: render_quality_markdown(report),
        }
        artifacts = []
        suffixes = {
            QualityExportFormat.JSON: "json",
            QualityExportFormat.JSONL: "jsonl",
            QualityExportFormat.MARKDOWN: "md",
        }
        for export_format, content in contents.items():
            object_name = f"evaluations/quality/{report.run_id}/report.{suffixes[export_format]}"
            await self._minio.aupload_file(self._bucket_name, object_name, content)
            artifacts.append(
                QualityArtifact(
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
            f"evaluations/quality/{report.run_id}/record.json",
            report.model_dump_json(indent=2).encode(),
            "application/json",
        )
        return report

    async def _read_report(self, run_id: str) -> QualityEvaluationReport | None:
        """读取质量评测原子记录。"""
        try:
            bucket_exists = await asyncio.to_thread(self._minio.client.bucket_exists, self._bucket_name)
            if not bucket_exists:
                return None
            object_name = f"evaluations/quality/{run_id}/record.json"
            if await self._minio.astat_file(self._bucket_name, object_name) is None:
                return None
            return QualityEvaluationReport.model_validate_json(
                await self._minio.adownload_file(self._bucket_name, object_name)
            )
        except StorageError:
            return None


quality_evaluation_service = QualityEvaluationService()
