"""企业尽调管理接口。"""

import re

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from server.utils.auth_middleware import get_admin_user
from yuxi.company_due_diligence.agent.models import CompanyInvestigationRequest, CompanyInvestigationResponse
from yuxi.company_due_diligence.agent.service import company_due_diligence_agent
from yuxi.company_due_diligence.evidence.indexer import EvidenceIndexUnavailableError
from yuxi.company_due_diligence.evidence.models import (
    EvidenceIndexInfo,
    EvidenceIndexRequest,
    EvidenceQuery,
    EvidenceQueryResult,
)
from yuxi.company_due_diligence.evidence.query_service import evidence_query_service
from yuxi.company_due_diligence.evidence.service import evidence_index_service
from yuxi.company_due_diligence.entity_resolution import entity_resolution_service
from yuxi.company_due_diligence.graph.repository import GraphUnavailableError
from yuxi.company_due_diligence.graph.schema import GraphProjectionInfo, GraphProjectionRequest
from yuxi.company_due_diligence.graph.service import graph_projection_service
from yuxi.company_due_diligence.models import (
    CompanyReference,
    CompanyResolutionResult,
    CompanySearchResult,
    StructuredQueryRequest,
    StructuredQueryResult,
)
from yuxi.company_due_diligence.mysql import MySQLHealth, company_due_diligence_mysql
from yuxi.company_due_diligence.qa.models import CompanyQuestionRequest, CompanyQuestionResponse
from yuxi.company_due_diligence.qa.service import company_question_service
from yuxi.company_due_diligence.report.models import DueDiligenceReportRequest, ReportFormat, ReportSection
from yuxi.company_due_diligence.report.service import (
    ReportNotFoundError,
    ReportValidationError,
    due_diligence_report_service,
)
from yuxi.company_due_diligence.structured_query_service import structured_query_service
from yuxi.services.task_service import tasker
from yuxi.storage.postgres.models_business import User

company_due_diligence_router = APIRouter(
    prefix="/company-due-diligence",
    tags=["company-due-diligence"],
)


@company_due_diligence_router.get("/health", response_model=MySQLHealth)
async def get_company_due_diligence_health(current_user: User = Depends(get_admin_user)) -> MySQLHealth:
    """返回企业尽调外部 MySQL 的脱敏只读健康状态。"""
    del current_user
    return await company_due_diligence_mysql.health()


@company_due_diligence_router.post("/companies/resolve", response_model=CompanyResolutionResult)
async def resolve_company(
    reference: CompanyReference,
    current_user: User = Depends(get_admin_user),
) -> CompanyResolutionResult:
    """按确定性优先级解析唯一企业身份。"""
    del current_user
    return await entity_resolution_service.resolve(reference)


@company_due_diligence_router.get("/companies/search", response_model=CompanySearchResult)
async def search_companies(
    q: str = Query(min_length=2, max_length=100),
    limit: int = Query(default=10, ge=1, le=20),
    current_user: User = Depends(get_admin_user),
) -> CompanySearchResult:
    """按名称、简称或股票代码返回有限企业候选。"""
    del current_user
    query = q.strip()
    if len(query) < 2:
        raise HTTPException(status_code=422, detail="企业搜索词去除空白后至少包含 2 个字符")
    return await entity_resolution_service.search(query, limit)


@company_due_diligence_router.post("/structured-query", response_model=StructuredQueryResult)
async def query_company_structure(
    request: StructuredQueryRequest,
    current_user: User = Depends(get_admin_user),
) -> StructuredQueryResult:
    """执行受控企业结构化查询并返回统一来源契约。"""
    del current_user
    return await structured_query_service.query(request)


@company_due_diligence_router.post("/query", response_model=CompanyQuestionResponse)
async def query_company(
    request: CompanyQuestionRequest,
    current_user: User = Depends(get_admin_user),
) -> CompanyQuestionResponse:
    """执行简单企业问答或最多三跳关系查询。"""
    del current_user
    return await company_question_service.query(request)


@company_due_diligence_router.post("/investigations", response_model=CompanyInvestigationResponse)
async def investigate_company(
    request: CompanyInvestigationRequest,
    current_user: User = Depends(get_admin_user),
) -> CompanyInvestigationResponse:
    """执行统一企业调查，并按意图返回同步结果或异步报告任务。"""
    del current_user
    return await company_due_diligence_agent.investigate(request, tasker=tasker)


@company_due_diligence_router.post("/reports", status_code=status.HTTP_202_ACCEPTED)
async def create_due_diligence_report(
    request: DueDiligenceReportRequest,
    current_user: User = Depends(get_admin_user),
) -> dict:
    """解析唯一企业并提交完整尽调报告任务。"""
    del current_user
    try:
        task = await due_diligence_report_service.create(request, tasker)
    except ReportValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"report_id": task.id, "task_id": task.id, "status": "queued"}


@company_due_diligence_router.get("/reports")
async def list_due_diligence_reports(
    limit: int = Query(default=50, ge=1, le=100),
    current_user: User = Depends(get_admin_user),
) -> dict:
    """返回企业尽调报告任务摘要。"""
    del current_user
    reports = await due_diligence_report_service.list_reports(tasker, limit)
    return {"reports": reports}


@company_due_diligence_router.get("/reports/{report_id}")
async def get_due_diligence_report(
    report_id: str,
    current_user: User = Depends(get_admin_user),
) -> dict:
    """返回报告任务状态和已发布报告记录。"""
    del current_user
    try:
        return await due_diligence_report_service.get_report(report_id, tasker)
    except ReportNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@company_due_diligence_router.post("/reports/{report_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
async def cancel_due_diligence_report(
    report_id: str,
    current_user: User = Depends(get_admin_user),
) -> dict:
    """请求取消尚未进入终态的报告任务。"""
    del current_user
    try:
        cancelled = await due_diligence_report_service.cancel(report_id, tasker)
    except ReportNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not cancelled:
        raise HTTPException(status_code=409, detail="报告任务已进入终态，无法取消")
    return {"report_id": report_id, "cancel_requested": True}


@company_due_diligence_router.post(
    "/reports/{report_id}/sections/{section}/retry",
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_due_diligence_report_section(
    report_id: str,
    section: ReportSection,
    current_user: User = Depends(get_admin_user),
) -> dict:
    """提交单个报告章节重试任务。"""
    del current_user
    try:
        task = await due_diligence_report_service.retry_section(report_id, section, tasker)
    except ReportNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"report_id": report_id, "task_id": task.id, "status": "queued"}


@company_due_diligence_router.get("/reports/{report_id}/download")
async def download_due_diligence_report(
    report_id: str,
    report_format: ReportFormat = Query(alias="format"),
    current_user: User = Depends(get_admin_user),
) -> Response:
    """下载并校验 JSON 或 Markdown 报告文件。"""
    del current_user
    try:
        content, artifact = await due_diligence_report_service.download(report_id, report_format)
    except ReportNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    media_type = "application/json" if report_format == ReportFormat.JSON else "text/markdown; charset=utf-8"
    suffix = "json" if report_format == ReportFormat.JSON else "md"
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="company-due-diligence-{report_id}.{suffix}"',
            "ETag": artifact.checksum,
        },
    )


@company_due_diligence_router.post("/graph/projections", status_code=status.HTTP_202_ACCEPTED)
async def create_graph_projection(
    request: GraphProjectionRequest,
    current_user: User = Depends(get_admin_user),
) -> dict:
    """提交全量或受限企业尽调 Neo4j 投影任务。"""
    del current_user
    task, created = await graph_projection_service.enqueue(request, tasker)
    if not created:
        raise HTTPException(status_code=409, detail="相同范围已有正在执行的企业尽调图谱投影任务")
    return {"task_id": task.id, "status": "queued"}


@company_due_diligence_router.get("/graph/projections/{task_id}")
async def get_graph_projection_task(
    task_id: str,
    current_user: User = Depends(get_admin_user),
) -> dict:
    """返回企业尽调图谱投影后台任务状态。"""
    del current_user
    task = await graph_projection_service.get_task(task_id, tasker)
    if task is None:
        raise HTTPException(status_code=404, detail="企业尽调图谱投影任务不存在")
    return {"task": task}


@company_due_diligence_router.get("/graph/info", response_model=GraphProjectionInfo)
async def get_graph_projection_info(
    current_user: User = Depends(get_admin_user),
) -> GraphProjectionInfo:
    """返回当前企业尽调图谱投影版本和统计。"""
    del current_user
    try:
        return await graph_projection_service.get_info()
    except GraphUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@company_due_diligence_router.post("/evidence/indexes", status_code=status.HTTP_202_ACCEPTED)
async def create_evidence_index(
    request: EvidenceIndexRequest,
    current_user: User = Depends(get_admin_user),
) -> dict:
    """提交全量或受限企业文本证据索引任务。"""
    del current_user
    task, created = await evidence_index_service.enqueue(request, tasker)
    if not created:
        raise HTTPException(status_code=409, detail="相同范围已有正在执行的企业尽调证据索引任务")
    return {"task_id": task.id, "status": "queued"}


@company_due_diligence_router.get("/evidence/indexes/{task_id}")
async def get_evidence_index_task(
    task_id: str,
    current_user: User = Depends(get_admin_user),
) -> dict:
    """返回企业尽调文本证据索引后台任务状态。"""
    del current_user
    task = await evidence_index_service.get_task(task_id, tasker)
    if task is None:
        raise HTTPException(status_code=404, detail="企业尽调证据索引任务不存在")
    return {"task": task}


@company_due_diligence_router.get("/evidence/info", response_model=EvidenceIndexInfo)
async def get_evidence_index_info(
    current_user: User = Depends(get_admin_user),
) -> EvidenceIndexInfo:
    """返回当前文本证据索引版本和统计。"""
    del current_user
    try:
        return await evidence_index_service.get_info()
    except EvidenceIndexUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@company_due_diligence_router.post("/evidence/query", response_model=EvidenceQueryResult)
async def query_company_evidence(
    request: EvidenceQuery,
    current_user: User = Depends(get_admin_user),
) -> EvidenceQueryResult:
    """在指定企业、类型和日期范围内召回文本证据。"""
    del current_user
    return await evidence_query_service.query(request)


@company_due_diligence_router.delete("/evidence/versions/{projection_version}")
async def delete_evidence_index_version(
    projection_version: str,
    current_user: User = Depends(get_admin_user),
) -> dict:
    """删除可从只读 MySQL 重建的证据索引版本。"""
    del current_user
    if re.fullmatch(r"evidence-[0-9a-f]{16}", projection_version) is None:
        raise HTTPException(status_code=422, detail="证据索引版本格式非法")
    try:
        deleted = await evidence_index_service.delete_version(projection_version)
    except EvidenceIndexUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="证据索引版本不存在")
    return {"projection_version": projection_version, "deleted": True}
