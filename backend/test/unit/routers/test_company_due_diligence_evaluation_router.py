"""企业尽调评测路由单元测试。"""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from server.routers.company_due_diligence_evaluation_router import company_due_diligence_evaluation_router
from server.utils.auth_middleware import get_admin_user
from yuxi.company_due_diligence.evaluation.runner import QualityArtifact
from yuxi.company_due_diligence.evaluation.service import quality_evaluation_service
from yuxi.company_due_diligence.performance.models import PerformanceArtifact
from yuxi.company_due_diligence.performance.service import performance_evaluation_service

pytestmark = pytest.mark.asyncio


class FakeTask:
    """提供评测任务 ID。"""

    id = "run-1"


async def test_quality_evaluation_routes_delegate_and_export(monkeypatch):
    """评测路由应覆盖数据集、任务、取消和三格式下载契约。"""

    async def override_admin():
        """绕过持久化用户，仅验证路由装配。"""
        return object()

    def fake_list_datasets():
        """返回分组数据集摘要。"""
        return [{"dataset_id": "dataset-1", "source_kind": "guangdong_enterprise"}]

    def fake_get_dataset(dataset_id):
        """返回固定数据集详情。"""
        return {"dataset_id": dataset_id, "checksum": "a" * 64}

    async def fake_create_run(request, _tasker):
        """校验运行请求并返回任务。"""
        assert request.dataset_id == "dataset-1"
        return FakeTask()

    async def fake_list_runs(_tasker, limit):
        """返回固定运行摘要。"""
        assert limit == 10
        return [{"id": "run-1", "status": "success"}]

    async def fake_get_run(run_id, _tasker):
        """返回固定运行详情。"""
        return {"task": {"id": run_id}, "report": {"run_id": run_id}}

    async def fake_cancel(run_id, _tasker):
        """接受取消请求。"""
        return run_id == "run-1"

    async def fake_download(run_id, export_format):
        """返回固定 Markdown 产物。"""
        assert run_id == "run-1"
        return b"# quality", QualityArtifact(
            format=export_format.value,
            bucket="test",
            object_name="report.md",
            checksum="b" * 64,
            size_bytes=9,
        )

    monkeypatch.setattr(quality_evaluation_service, "list_datasets", fake_list_datasets)
    monkeypatch.setattr(quality_evaluation_service, "get_dataset", fake_get_dataset)
    monkeypatch.setattr(quality_evaluation_service, "create_run", fake_create_run)
    monkeypatch.setattr(quality_evaluation_service, "list_runs", fake_list_runs)
    monkeypatch.setattr(quality_evaluation_service, "get_run", fake_get_run)
    monkeypatch.setattr(quality_evaluation_service, "cancel", fake_cancel)
    monkeypatch.setattr(quality_evaluation_service, "download", fake_download)
    app = FastAPI()
    app.include_router(company_due_diligence_evaluation_router, prefix="/api")
    app.dependency_overrides[get_admin_user] = override_admin

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        datasets_response = await client.get("/api/company-due-diligence/evaluations/datasets")
        dataset_response = await client.get("/api/company-due-diligence/evaluations/datasets/dataset-1")
        create_response = await client.post(
            "/api/company-due-diligence/evaluations/runs",
            json={"dataset_id": "dataset-1"},
        )
        list_response = await client.get("/api/company-due-diligence/evaluations/runs?limit=10")
        detail_response = await client.get("/api/company-due-diligence/evaluations/runs/run-1")
        cancel_response = await client.post("/api/company-due-diligence/evaluations/runs/run-1/cancel")
        download_response = await client.get(
            "/api/company-due-diligence/evaluations/runs/run-1/download?format=markdown"
        )

    assert datasets_response.json()["datasets"][0]["dataset_id"] == "dataset-1"
    assert dataset_response.json()["checksum"] == "a" * 64
    assert create_response.status_code == 202
    assert create_response.json()["run_id"] == "run-1"
    assert list_response.json()["runs"][0]["status"] == "success"
    assert detail_response.json()["report"]["run_id"] == "run-1"
    assert cancel_response.status_code == 202
    assert download_response.content == b"# quality"
    assert download_response.headers["etag"] == "b" * 64


async def test_quality_evaluation_routes_map_missing_resources(monkeypatch):
    """不存在的数据集和运行应映射为 404。"""

    async def override_admin():
        """绕过持久化用户。"""
        return object()

    def missing_dataset(_dataset_id):
        """模拟数据集不存在。"""
        raise FileNotFoundError("评测数据集不存在")

    async def missing_run(_run_id, _tasker):
        """模拟运行不存在。"""
        raise FileNotFoundError("质量评测运行不存在")

    monkeypatch.setattr(quality_evaluation_service, "get_dataset", missing_dataset)
    monkeypatch.setattr(quality_evaluation_service, "get_run", missing_run)
    app = FastAPI()
    app.include_router(company_due_diligence_evaluation_router, prefix="/api")
    app.dependency_overrides[get_admin_user] = override_admin

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        dataset_response = await client.get("/api/company-due-diligence/evaluations/datasets/missing")
        run_response = await client.get("/api/company-due-diligence/evaluations/runs/missing")

    assert dataset_response.status_code == 404
    assert run_response.status_code == 404


async def test_performance_evaluation_routes_delegate_and_export(monkeypatch):
    """性能路由应覆盖运行、进度、取消和下载契约。"""

    async def override_admin():
        """绕过持久化用户，仅验证路由装配。"""
        return object()

    async def fake_create_run(request, _tasker):
        """校验安全模式请求并返回任务。"""
        assert request.concurrency == 2
        return FakeTask()

    async def fake_list_runs(_tasker, limit):
        """返回固定性能运行摘要。"""
        assert limit == 10
        return [{"id": "run-1", "status": "success"}]

    async def fake_get_run(run_id, _tasker):
        """返回固定性能报告详情。"""
        return {"task": {"id": run_id}, "report": {"run_id": run_id}}

    async def fake_cancel(run_id, _tasker):
        """接受性能运行取消。"""
        return run_id == "run-1"

    async def fake_download(run_id, export_format):
        """返回固定性能 Markdown。"""
        assert run_id == "run-1"
        return b"# performance", PerformanceArtifact(
            format=export_format.value,
            bucket="test",
            object_name="performance.md",
            checksum="c" * 64,
            size_bytes=13,
        )

    monkeypatch.setattr(performance_evaluation_service, "create_run", fake_create_run)
    monkeypatch.setattr(performance_evaluation_service, "list_runs", fake_list_runs)
    monkeypatch.setattr(performance_evaluation_service, "get_run", fake_get_run)
    monkeypatch.setattr(performance_evaluation_service, "cancel", fake_cancel)
    monkeypatch.setattr(performance_evaluation_service, "download", fake_download)
    app = FastAPI()
    app.include_router(company_due_diligence_evaluation_router, prefix="/api")
    app.dependency_overrides[get_admin_user] = override_admin

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        create_response = await client.post(
            "/api/company-due-diligence/evaluations/performance-runs",
            json={
                "company_refs": [{"eid": "eid-1"}],
                "concurrency": 2,
                "request_count": 2,
                "duration_seconds": 5,
            },
        )
        list_response = await client.get("/api/company-due-diligence/evaluations/performance-runs?limit=10")
        detail_response = await client.get("/api/company-due-diligence/evaluations/performance-runs/run-1")
        cancel_response = await client.post("/api/company-due-diligence/evaluations/performance-runs/run-1/cancel")
        download_response = await client.get(
            "/api/company-due-diligence/evaluations/performance-runs/run-1/download?format=markdown"
        )

    assert create_response.status_code == 202
    assert list_response.json()["runs"][0]["id"] == "run-1"
    assert detail_response.json()["report"]["run_id"] == "run-1"
    assert cancel_response.status_code == 202
    assert download_response.content == b"# performance"
    assert download_response.headers["etag"] == "c" * 64
