"""企业尽调质量评测真实 Tasker、MySQL、报告和 MinIO 集成测试。"""

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from yuxi.company_due_diligence.evaluation.dataset import (
    DatasetSourceKind,
    EvaluationDataset,
    EvaluationDatasetRepository,
    EvaluationSample,
    EvaluationSampleType,
    GoldenExpected,
    ReviewStatus,
)
from yuxi.company_due_diligence.evaluation.runner import QualityEvaluationRequest, QualityEvaluationRunner
from yuxi.company_due_diligence.evaluation.service import QualityEvaluationService, QualityExportFormat
from yuxi.company_due_diligence.mysql import CompanyDueDiligenceMySQL
from yuxi.company_due_diligence.qa.models import CompanyQuestionRequest
from yuxi.company_due_diligence.report.models import DueDiligenceReportRequest, ReportSection
from yuxi.services.task_service import Tasker
from yuxi.storage.minio import get_minio_client

pytestmark = [pytest.mark.asyncio(loop_scope="session"), pytest.mark.integration]


async def _wait_for_terminal(tasker: Tasker, task_id: str) -> dict:
    """轮询质量任务直到进入终态。"""
    for _ in range(300):
        task = await tasker.get_task(task_id)
        if task and task["status"] in {"success", "failed", "cancelled"}:
            return task
        await asyncio.sleep(0.1)
    raise AssertionError("质量评测任务未在预期时间内完成")


async def test_quality_evaluation_persists_repeatable_artifacts(tmp_path):
    """真实问答和报告样本应通过 Tasker 运行并发布三种可校验产物。"""
    mysql = CompanyDueDiligenceMySQL()
    tasker = Tasker(worker_count=1, default_timeout_seconds=120)
    minio = get_minio_client()
    bucket_name = f"cdd-quality-pytest-{uuid.uuid4().hex[:12]}"
    task_ids = []
    try:
        async with mysql.connection() as connection:
            result = await connection.execute(text("SELECT eid FROM t_enterprise ORDER BY eid LIMIT 1"))
            eid = result.scalar_one()
        dataset = EvaluationDataset(
            dataset_id="integration-quality-v1",
            name="质量评测真实集成样本",
            version="1.0.0",
            source_kind=DatasetSourceKind.GUANGDONG_ENTERPRISE,
            business_as_of="2026-08-10",
            created_at=datetime(2026, 8, 10, tzinfo=UTC),
            samples=[
                EvaluationSample(
                    sample_id="refusal",
                    sample_type=EvaluationSampleType.QA,
                    domain="entity_resolution",
                    review_status=ReviewStatus.SOURCE_DERIVED_UNREVIEWED,
                    qa_request=CompanyQuestionRequest(
                        company_ref={"name": "__企业尽调集成测试不存在企业__"},
                        question="该企业是否存在？",
                    ),
                    expected=GoldenExpected(refused=True),
                ),
                EvaluationSample(
                    sample_id="report",
                    sample_type=EvaluationSampleType.REPORT,
                    domain="report",
                    review_status=ReviewStatus.SOURCE_DERIVED_UNREVIEWED,
                    report_request=DueDiligenceReportRequest(
                        company_ref={"eid": eid},
                        sections=[ReportSection.IDENTITY, ReportSection.DATA_QUALITY],
                    ),
                    expected=GoldenExpected(
                        required_report_sections=[ReportSection.IDENTITY, ReportSection.DATA_QUALITY]
                    ),
                ),
            ],
        )
        dataset_path = tmp_path / "dataset.json"
        dataset_path.write_text(dataset.model_dump_json(indent=2), encoding="utf-8")
        service = QualityEvaluationService(
            datasets=EvaluationDatasetRepository(tmp_path),
            runner=QualityEvaluationRunner(),
            minio_client=minio,
            bucket_name=bucket_name,
        )
        await tasker.start()

        for _ in range(2):
            task = await service.create_run(QualityEvaluationRequest(dataset_id=dataset.dataset_id), tasker)
            task_ids.append(task.id)
            terminal = await _wait_for_terminal(tasker, task.id)
            assert terminal["status"] == "success"

        first = await service.get_run(task_ids[0], tasker)
        second = await service.get_run(task_ids[1], tasker)
        assert first["report"]["dataset_checksum"] == second["report"]["dataset_checksum"]
        assert first["report"]["all_metrics"] == second["report"]["all_metrics"]
        assert first["report"]["regression_gate"]["eligible"] is False

        for export_format in QualityExportFormat:
            content, artifact = await service.download(task_ids[0], export_format)
            assert len(content) == artifact.size_bytes
            assert len(artifact.checksum) == 64
    finally:
        await tasker.shutdown()
        for task_id in task_ids:
            await tasker.delete_task(task_id)
        try:
            await minio.adelete_objects_by_prefix(bucket_name, "evaluations/")
            await minio.adelete_bucket(bucket_name)
        except Exception:
            pass
        await mysql.close()
