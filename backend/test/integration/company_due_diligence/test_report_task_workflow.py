"""企业尽调报告跨进程 ARQ、PostgreSQL 与 MinIO 集成测试。"""

import asyncio

import pytest
from sqlalchemy import text
from yuxi.company_due_diligence.mysql import CompanyDueDiligenceMySQL
from yuxi.company_due_diligence.report.models import (
    DueDiligenceReportRequest,
    ReportFormat,
    ReportSection,
)
from yuxi.company_due_diligence.report.renderers import content_checksum
from yuxi.company_due_diligence.report.service import (
    REPORT_BUCKET,
    REPORT_TASK_TYPE,
    due_diligence_report_service,
)
from yuxi.services.task_service import Tasker
from yuxi.storage.minio import get_minio_client

pytestmark = [pytest.mark.asyncio(loop_scope="session"), pytest.mark.integration]


async def _wait_for_terminal(tasker: Tasker, task_id: str) -> dict:
    """跨进程轮询 PostgreSQL 任务状态直到进入终态。"""
    for _ in range(300):
        task = await tasker.get_task(task_id)
        if task and task["status"] in {"success", "failed", "cancelled"}:
            return task
        await asyncio.sleep(0.1)
    raise AssertionError("报告任务未在预期时间内完成")


async def test_report_task_persists_metadata_and_verifiable_artifacts(tmp_path):
    """提交进程不启动 Tasker 时，worker 仍应生成可校验报告。"""
    del tmp_path
    mysql = CompanyDueDiligenceMySQL()
    tasker = Tasker(worker_count=1, default_timeout_seconds=60)
    minio = get_minio_client()
    created_task_ids = []
    task = None
    try:
        async with mysql.connection() as connection:
            result = await connection.execute(text("SELECT eid FROM t_enterprise ORDER BY eid LIMIT 1"))
            eid = result.scalar_one()
        task = await due_diligence_report_service.create(
            DueDiligenceReportRequest(
                company_ref={"eid": eid},
                sections=[ReportSection.IDENTITY, ReportSection.BUSINESS, ReportSection.DATA_QUALITY],
            ),
            tasker,
        )
        created_task_ids.append(task.id)
        terminal = await _wait_for_terminal(tasker, task.id)
        assert terminal["status"] == "success"

        detail = await due_diligence_report_service.get_report(task.id, tasker)
        report = detail["report"]
        assert report is not None
        assert set(report["sections"]) == {"identity", "business", "data_quality"}
        assert len(report["artifacts"]) == 2
        reports = await due_diligence_report_service.list_reports(tasker)
        assert any(item["id"] == task.id for item in reports)

        json_content, json_artifact = await due_diligence_report_service.download(task.id, ReportFormat.JSON)
        markdown_content, markdown_artifact = await due_diligence_report_service.download(
            task.id, ReportFormat.MARKDOWN
        )
        assert content_checksum(json_content) == json_artifact.checksum
        assert content_checksum(markdown_content) == markdown_artifact.checksum
        assert report["company"]["name"].encode() in markdown_content

        await tasker.start()
        retry = await due_diligence_report_service.retry_section(task.id, ReportSection.BUSINESS, tasker)
        created_task_ids.append(retry.id)
        retry_terminal = await _wait_for_terminal(tasker, retry.id)
        assert retry_terminal["status"] == "success"
        updated = await due_diligence_report_service.get_report(task.id, tasker)
        assert updated["report"]["sections"]["business"]["status"] in {"complete", "no_data"}
    finally:
        await tasker.shutdown()
        for task_id in created_task_ids:
            await tasker.delete_task(task_id)
        if task is not None:
            await minio.adelete_objects_by_prefix(REPORT_BUCKET, f"reports/{task.id}/")
        await mysql.close()


async def test_report_cancellation_is_visible_across_process_boundaries():
    """另一个 Tasker 实例取消待执行报告后，worker 不应开始执行。"""
    producer = Tasker()
    reader = Tasker()
    task = await producer.create_persisted(
        name="待取消的企业尽调报告",
        task_type=REPORT_TASK_TYPE,
        payload={},
    )
    try:
        assert (await reader.get_task(task.id))["status"] == "pending"
        assert await reader.cancel_task(task.id) is True

        await due_diligence_report_service.execute_task(task.id)

        detail = await reader.get_task(task.id)
        assert detail["status"] == "cancelled"
        assert detail["cancel_requested"] is True
    finally:
        await reader.delete_task(task.id)
