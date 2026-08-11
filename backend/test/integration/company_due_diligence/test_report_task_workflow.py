"""企业尽调报告真实 MySQL、PostgreSQL Tasker 与 MinIO 集成测试。"""

import asyncio
import uuid

import pytest
from sqlalchemy import text

from yuxi.company_due_diligence.entity_resolution import EntityResolutionService
from yuxi.company_due_diligence.evidence.models import EvidenceIndexInfo
from yuxi.company_due_diligence.graph.schema import GraphProjectionInfo
from yuxi.company_due_diligence.mysql import CompanyDueDiligenceMySQL
from yuxi.company_due_diligence.qa.graph import QAWorkflow
from yuxi.company_due_diligence.qa.service import CompanyQuestionService
from yuxi.company_due_diligence.report.graph import DueDiligenceReportWorkflow
from yuxi.company_due_diligence.report.models import (
    DueDiligenceReportRequest,
    ReportFormat,
    ReportSection,
)
from yuxi.company_due_diligence.report.renderers import content_checksum
from yuxi.company_due_diligence.report.service import DueDiligenceReportService
from yuxi.company_due_diligence.structured_query_service import StructuredQueryService
from yuxi.services.task_service import Tasker
from yuxi.storage.minio import get_minio_client

pytestmark = [pytest.mark.asyncio(loop_scope="session"), pytest.mark.integration]


class ForbiddenService:
    """当前报告章节不应访问的图谱或证据查询服务。"""

    async def query(self, _request):
        """任何调用都表示章节计划错误。"""
        raise AssertionError("当前报告章节不应调用该服务")


class GraphInfoService:
    """返回无当前投影但基础设施可用状态。"""

    async def get_info(self):
        """返回固定图谱版本信息。"""
        return GraphProjectionInfo(available=True)


class EvidenceInfoService:
    """返回无当前索引但基础设施可用状态。"""

    async def get_info(self):
        """返回固定证据版本信息。"""
        return EvidenceIndexInfo(available=True)


async def _wait_for_terminal(tasker: Tasker, task_id: str) -> dict:
    """轮询 Tasker 直到任务进入终态。"""
    for _ in range(100):
        task = await tasker.get_task(task_id)
        if task and task["status"] in {"success", "failed", "cancelled"}:
            return task
        await asyncio.sleep(0.1)
    raise AssertionError("报告任务未在预期时间内完成")


async def test_report_task_persists_metadata_and_verifiable_artifacts(tmp_path):
    """真实报告任务应持久化状态并生成可校验 JSON/Markdown 文件。"""
    del tmp_path
    mysql = CompanyDueDiligenceMySQL()
    tasker = Tasker(worker_count=1, default_timeout_seconds=60)
    bucket_name = f"cdd-report-pytest-{uuid.uuid4().hex[:12]}"
    minio = get_minio_client()
    created_task_ids = []
    try:
        resolver = EntityResolutionService(mysql=mysql)
        question_service = CompanyQuestionService(
            resolver=resolver,
            workflow=QAWorkflow(
                structured_service=StructuredQueryService(mysql=mysql),
                graph_service=ForbiddenService(),
                evidence_service=ForbiddenService(),
            ),
        )
        service = DueDiligenceReportService(
            resolver=resolver,
            workflow=DueDiligenceReportWorkflow(question_service=question_service),
            graph_service=GraphInfoService(),
            evidence_service=EvidenceInfoService(),
            minio_client=minio,
            bucket_name=bucket_name,
        )
        async with mysql.connection() as connection:
            result = await connection.execute(text("SELECT eid FROM t_enterprise ORDER BY eid LIMIT 1"))
            eid = result.scalar_one()
        await tasker.start()
        task = await service.create(
            DueDiligenceReportRequest(
                company_ref={"eid": eid},
                sections=[ReportSection.IDENTITY, ReportSection.BUSINESS, ReportSection.DATA_QUALITY],
            ),
            tasker,
        )
        created_task_ids.append(task.id)
        terminal = await _wait_for_terminal(tasker, task.id)
        assert terminal["status"] == "success"

        detail = await service.get_report(task.id, tasker)
        report = detail["report"]
        assert report is not None
        assert set(report["sections"]) == {"identity", "business", "data_quality"}
        assert len(report["artifacts"]) == 2
        reports = await service.list_reports(tasker)
        assert any(item["id"] == task.id for item in reports)

        json_content, json_artifact = await service.download(task.id, ReportFormat.JSON)
        markdown_content, markdown_artifact = await service.download(task.id, ReportFormat.MARKDOWN)
        assert content_checksum(json_content) == json_artifact.checksum
        assert content_checksum(markdown_content) == markdown_artifact.checksum
        assert report["company"]["name"].encode() in markdown_content

        retry = await service.retry_section(task.id, ReportSection.BUSINESS, tasker)
        created_task_ids.append(retry.id)
        retry_terminal = await _wait_for_terminal(tasker, retry.id)
        assert retry_terminal["status"] == "success"
        updated = await service.get_report(task.id, tasker)
        assert updated["report"]["sections"]["business"]["status"] in {"complete", "no_data"}
    finally:
        await tasker.shutdown()
        for task_id in created_task_ids:
            await tasker.delete_task(task_id)
        try:
            await minio.adelete_objects_by_prefix(bucket_name, "reports/")
            await minio.adelete_bucket(bucket_name)
        except Exception:
            pass
        await mysql.close()
