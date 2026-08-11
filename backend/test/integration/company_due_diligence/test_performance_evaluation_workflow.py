"""企业尽调性能评测真实 MySQL、Tasker 和 MinIO 低负载集成测试。"""

import asyncio
import uuid

import pytest
from sqlalchemy import text

from yuxi.company_due_diligence.mysql import CompanyDueDiligenceMySQL
from yuxi.company_due_diligence.performance.models import PerformanceRunRequest, PerformanceScenario
from yuxi.company_due_diligence.performance.service import (
    PerformanceEvaluationService,
    PerformanceExportFormat,
)
from yuxi.services.task_service import Tasker
from yuxi.storage.minio import get_minio_client

pytestmark = [pytest.mark.asyncio(loop_scope="session"), pytest.mark.integration]


async def _wait_for_terminal(tasker: Tasker, task_id: str) -> dict:
    """轮询性能任务直到进入终态。"""
    for _ in range(300):
        task = await tasker.get_task(task_id)
        if task and task["status"] in {"success", "failed", "cancelled"}:
            return task
        await asyncio.sleep(0.1)
    raise AssertionError("性能评测任务未在预期时间内完成")


async def test_low_load_performance_run_persists_metrics_and_releases_pool():
    """并发 2 的短时真实运行应保存报告并归还全部 MySQL 连接。"""
    mysql = CompanyDueDiligenceMySQL()
    tasker = Tasker(worker_count=1, default_timeout_seconds=120)
    minio = get_minio_client()
    bucket_name = f"cdd-performance-pytest-{uuid.uuid4().hex[:12]}"
    task_id = None
    try:
        async with mysql.connection() as connection:
            result = await connection.execute(text("SELECT eid FROM t_enterprise ORDER BY eid LIMIT 1"))
            eid = result.scalar_one()
        service = PerformanceEvaluationService(minio_client=minio, bucket_name=bucket_name)
        await tasker.start()
        task = await service.create_run(
            PerformanceRunRequest(
                scenarios=[PerformanceScenario.ENTERPRISE_PROFILE],
                company_refs=[{"eid": eid}],
                concurrency=2,
                request_count=2,
                duration_seconds=10,
                request_timeout_seconds=10,
            ),
            tasker,
        )
        task_id = task.id
        terminal = await _wait_for_terminal(tasker, task.id)
        assert terminal["status"] == "success"

        detail = await service.get_run(task.id, tasker)
        report = detail["report"]
        scenario = report["scenarios"][0]
        assert scenario["total_requests"] == 2
        assert scenario["succeeded"] == 2
        assert scenario["latency"]["p50_ms"] is not None
        assert scenario["throughput_rps"] > 0
        assert report["pool"]["after"]["checked_out"] == 0
        assert report["pool"]["after"]["overflow"] == 0

        for export_format in PerformanceExportFormat:
            content, artifact = await service.download(task.id, export_format)
            assert len(content) == artifact.size_bytes
            assert len(artifact.checksum) == 64
    finally:
        await tasker.shutdown()
        if task_id is not None:
            await tasker.delete_task(task_id)
        try:
            await minio.adelete_objects_by_prefix(bucket_name, "evaluations/")
            await minio.adelete_bucket(bucket_name)
        except Exception:
            pass
        await mysql.close()
