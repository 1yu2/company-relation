"""企业尽调路由单元测试。"""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from server.routers.company_due_diligence_router import company_due_diligence_router
from server.utils.auth_middleware import get_admin_user
from yuxi.company_due_diligence.entity_resolution import entity_resolution_service
from yuxi.company_due_diligence.evidence.models import EvidenceIndexInfo, EvidenceQueryResult
from yuxi.company_due_diligence.evidence.query_service import evidence_query_service
from yuxi.company_due_diligence.evidence.service import evidence_index_service
from yuxi.company_due_diligence.graph.repository import GraphUnavailableError
from yuxi.company_due_diligence.graph.schema import GraphProjectionInfo
from yuxi.company_due_diligence.graph.service import graph_projection_service
from yuxi.company_due_diligence.models import (
    CompanyReference,
    CompanyResolutionResult,
    CompanySearchResult,
    DataQuality,
    LatencyBreakdown,
    Pagination,
    ResolutionStatus,
    StructuredQueryResult,
    StructuredQueryType,
)
from yuxi.company_due_diligence.mysql import MySQLHealth, MySQLPoolHealth, company_due_diligence_mysql
from yuxi.company_due_diligence.qa.models import CompanyQuestionResponse
from yuxi.company_due_diligence.qa.service import company_question_service
from yuxi.company_due_diligence.report.service import due_diligence_report_service
from yuxi.company_due_diligence.structured_query_service import structured_query_service

pytestmark = pytest.mark.asyncio


async def test_health_route_returns_only_redacted_pool_state(monkeypatch):
    """健康接口应返回固定脱敏字段且不包含连接信息。"""

    async def override_admin():
        """绕过持久化用户，仅验证路由装配。"""
        return object()

    async def fake_health():
        """返回不依赖外部数据库的固定健康状态。"""
        return MySQLHealth(
            configured=True,
            reachable=True,
            readonly=True,
            required_tables_ready=True,
            latency_ms=12,
            pool=MySQLPoolHealth(size=2, checked_out=0, overflow=0),
        )

    monkeypatch.setattr(company_due_diligence_mysql, "health", fake_health)
    app = FastAPI()
    app.include_router(company_due_diligence_router, prefix="/api")
    app.dependency_overrides[get_admin_user] = override_admin

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/company-due-diligence/health")

    assert response.status_code == 200
    assert response.json() == {
        "configured": True,
        "reachable": True,
        "readonly": True,
        "required_tables_ready": True,
        "latency_ms": 12,
        "pool": {"size": 2, "checked_out": 0, "overflow": 0, "wait_ms": 0.0, "timeouts": 0},
    }


async def test_company_routes_delegate_to_resolution_service(monkeypatch):
    """解析和搜索接口应只负责权限、参数和响应装配。"""

    async def override_admin():
        """绕过持久化用户，仅验证路由装配。"""
        return object()

    async def fake_resolve(reference):
        """返回固定未找到解析结果。"""
        return CompanyResolutionResult(
            status=ResolutionStatus.NOT_FOUND,
            reference=reference,
            data_quality=DataQuality(),
            latency=LatencyBreakdown(),
        )

    async def fake_search(query, limit):
        """返回固定空候选搜索结果。"""
        return CompanySearchResult(
            query=query,
            status=ResolutionStatus.NOT_FOUND,
            candidates=[],
            sources=[],
            data_quality=DataQuality(),
            latency=LatencyBreakdown(),
        )

    async def fake_structured_query(request):
        """返回固定空结构化查询结果。"""
        return StructuredQueryResult(
            query_type=request.query_type,
            eid=request.eid,
            data_quality=DataQuality(),
            pagination=Pagination(page=request.page, page_size=request.page_size, returned=0),
            latency=LatencyBreakdown(),
        )

    async def fake_company_question(request):
        """返回固定拒答问答响应。"""
        return CompanyQuestionResponse(
            question=request.question,
            resolution_status=ResolutionStatus.NOT_FOUND,
            answer="企业无法唯一解析，未执行后续查询。",
            data_quality=DataQuality(),
            latency=LatencyBreakdown(),
        )

    monkeypatch.setattr(entity_resolution_service, "resolve", fake_resolve)
    monkeypatch.setattr(entity_resolution_service, "search", fake_search)
    monkeypatch.setattr(structured_query_service, "query", fake_structured_query)
    monkeypatch.setattr(company_question_service, "query", fake_company_question)
    app = FastAPI()
    app.include_router(company_due_diligence_router, prefix="/api")
    app.dependency_overrides[get_admin_user] = override_admin

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resolve_response = await client.post("/api/company-due-diligence/companies/resolve", json={"eid": "eid-1"})
        search_response = await client.get("/api/company-due-diligence/companies/search?q=示例&limit=3")
        structured_response = await client.post(
            "/api/company-due-diligence/structured-query",
            json={"eid": "eid-1", "query_type": "enterprise_profile"},
        )
        question_response = await client.post(
            "/api/company-due-diligence/query",
            json={"company_ref": {"eid": "eid-1"}, "question": "注册资本？"},
        )

    assert resolve_response.status_code == 200
    assert resolve_response.json()["reference"] == CompanyReference(eid="eid-1").model_dump()
    assert search_response.status_code == 200
    assert search_response.json()["query"] == "示例"
    assert structured_response.status_code == 200
    assert structured_response.json()["query_type"] == StructuredQueryType.ENTERPRISE_PROFILE
    assert question_response.status_code == 200
    assert question_response.json()["resolution_status"] == ResolutionStatus.NOT_FOUND


async def test_graph_projection_routes_delegate_to_task_service(monkeypatch):
    """图投影接口应只负责权限、Tasker 装配和错误状态映射。"""

    async def override_admin():
        """绕过持久化用户，仅验证路由装配。"""
        return object()

    class FakeTask:
        """提供投影提交响应所需任务 ID。"""

        id = "task-1"

    async def fake_enqueue(request, _tasker):
        """记录规范化请求并返回新任务。"""
        assert request.eids == ["eid-1"]
        return FakeTask(), True

    async def fake_get_task(task_id, _tasker):
        """返回固定成功任务。"""
        assert task_id == "task-1"
        return {"id": task_id, "type": "company_due_diligence_graph_projection", "status": "success"}

    async def fake_get_info():
        """返回无当前版本但 Neo4j 可用的状态。"""
        return GraphProjectionInfo(available=True)

    monkeypatch.setattr(graph_projection_service, "enqueue", fake_enqueue)
    monkeypatch.setattr(graph_projection_service, "get_task", fake_get_task)
    monkeypatch.setattr(graph_projection_service, "get_info", fake_get_info)
    app = FastAPI()
    app.include_router(company_due_diligence_router, prefix="/api")
    app.dependency_overrides[get_admin_user] = override_admin

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        create_response = await client.post(
            "/api/company-due-diligence/graph/projections",
            json={"eids": [" eid-1 "]},
        )
        task_response = await client.get("/api/company-due-diligence/graph/projections/task-1")
        info_response = await client.get("/api/company-due-diligence/graph/info")

    assert create_response.status_code == 202
    assert create_response.json() == {"task_id": "task-1", "status": "queued"}
    assert task_response.json()["task"]["status"] == "success"
    assert info_response.json() == {"available": True, "current": None}


async def test_graph_info_returns_service_unavailable(monkeypatch):
    """Neo4j 不可用时图信息接口应返回 503 而不是空图。"""

    async def override_admin():
        """绕过持久化用户，仅验证错误映射。"""
        return object()

    async def fake_get_info():
        """模拟 Neo4j 连接故障。"""
        raise GraphUnavailableError("Neo4j 当前不可用")

    monkeypatch.setattr(graph_projection_service, "get_info", fake_get_info)
    app = FastAPI()
    app.include_router(company_due_diligence_router, prefix="/api")
    app.dependency_overrides[get_admin_user] = override_admin

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/company-due-diligence/graph/info")

    assert response.status_code == 503
    assert response.json()["detail"] == "Neo4j 当前不可用"


async def test_evidence_routes_delegate_to_services(monkeypatch):
    """证据路由只负责鉴权、任务装配和统一结果返回。"""

    async def override_admin():
        """绕过持久化用户，仅验证路由装配。"""
        return object()

    class FakeTask:
        """提供索引提交响应所需任务 ID。"""

        id = "task-evidence"

    async def fake_enqueue(request, _tasker):
        """记录规范化索引请求并返回新任务。"""
        assert request.eids == ["eid-1"]
        return FakeTask(), True

    async def fake_get_task(task_id, _tasker):
        """返回固定证据索引任务。"""
        return {"id": task_id, "type": "company_due_diligence_evidence_index", "status": "success"}

    async def fake_get_info():
        """返回无当前索引但基础设施可用状态。"""
        return EvidenceIndexInfo(available=True)

    async def fake_query(_request):
        """返回固定空证据结果。"""
        return EvidenceQueryResult(
            data_quality=DataQuality(),
            latency=LatencyBreakdown(),
        )

    monkeypatch.setattr(evidence_index_service, "enqueue", fake_enqueue)
    monkeypatch.setattr(evidence_index_service, "get_task", fake_get_task)
    monkeypatch.setattr(evidence_index_service, "get_info", fake_get_info)
    monkeypatch.setattr(evidence_query_service, "query", fake_query)
    app = FastAPI()
    app.include_router(company_due_diligence_router, prefix="/api")
    app.dependency_overrides[get_admin_user] = override_admin

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        create_response = await client.post(
            "/api/company-due-diligence/evidence/indexes",
            json={"eids": [" eid-1 "]},
        )
        task_response = await client.get("/api/company-due-diligence/evidence/indexes/task-evidence")
        info_response = await client.get("/api/company-due-diligence/evidence/info")
        query_response = await client.post(
            "/api/company-due-diligence/evidence/query",
            json={"eid": "eid-1", "query": "处罚"},
        )

    assert create_response.status_code == 202
    assert create_response.json() == {"task_id": "task-evidence", "status": "queued"}
    assert task_response.json()["task"]["status"] == "success"
    assert info_response.json() == {"available": True, "current": None}
    assert query_response.json()["evidence"] == []


async def test_report_routes_delegate_to_report_service(monkeypatch):
    """报告路由应只负责权限、参数、状态码和下载响应。"""

    async def override_admin():
        """绕过持久化用户，仅验证路由装配。"""
        return object()

    class FakeTask:
        """提供报告和重试任务 ID。"""

        def __init__(self, task_id: str) -> None:
            self.id = task_id

    async def fake_create(request, _tasker):
        """校验报告请求并返回主任务。"""
        assert request.sections[0].value == "identity"
        return FakeTask("report-1")

    async def fake_list(_tasker, limit):
        """返回固定报告摘要。"""
        assert limit == 10
        return [{"id": "report-1", "status": "success"}]

    async def fake_get(report_id, _tasker):
        """返回固定报告详情。"""
        return {"task": {"id": report_id}, "report": {"report_id": report_id}}

    async def fake_cancel(report_id, _tasker):
        """记录取消请求。"""
        assert report_id == "report-1"
        return True

    async def fake_retry(report_id, section, _tasker):
        """返回固定章节重试任务。"""
        assert report_id == "report-1"
        assert section.value == "identity"
        return FakeTask("retry-1")

    async def fake_download(report_id, report_format):
        """返回固定 Markdown 内容和 artifact。"""
        from yuxi.company_due_diligence.report.models import ReportArtifact

        assert report_id == "report-1"
        return b"# report", ReportArtifact(
            format=report_format,
            bucket="test",
            object_name="report.md",
            checksum="a" * 64,
            size_bytes=8,
        )

    monkeypatch.setattr(due_diligence_report_service, "create", fake_create)
    monkeypatch.setattr(due_diligence_report_service, "list_reports", fake_list)
    monkeypatch.setattr(due_diligence_report_service, "get_report", fake_get)
    monkeypatch.setattr(due_diligence_report_service, "cancel", fake_cancel)
    monkeypatch.setattr(due_diligence_report_service, "retry_section", fake_retry)
    monkeypatch.setattr(due_diligence_report_service, "download", fake_download)
    app = FastAPI()
    app.include_router(company_due_diligence_router, prefix="/api")
    app.dependency_overrides[get_admin_user] = override_admin

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        create_response = await client.post(
            "/api/company-due-diligence/reports",
            json={"company_ref": {"eid": "eid-1"}, "sections": ["identity"]},
        )
        list_response = await client.get("/api/company-due-diligence/reports?limit=10")
        detail_response = await client.get("/api/company-due-diligence/reports/report-1")
        cancel_response = await client.post("/api/company-due-diligence/reports/report-1/cancel")
        retry_response = await client.post("/api/company-due-diligence/reports/report-1/sections/identity/retry")
        download_response = await client.get("/api/company-due-diligence/reports/report-1/download?format=markdown")

    assert create_response.status_code == 202
    assert create_response.json()["report_id"] == "report-1"
    assert list_response.json()["reports"][0]["id"] == "report-1"
    assert detail_response.json()["report"]["report_id"] == "report-1"
    assert cancel_response.status_code == 202
    assert retry_response.json()["task_id"] == "retry-1"
    assert download_response.content == b"# report"
    assert download_response.headers["etag"] == "a" * 64
