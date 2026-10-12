"""企业尽调领域智能体的主体解析、工具路由和统一响应服务。"""

from __future__ import annotations

from yuxi.company_due_diligence.agent.models import (
    ClaimLedger,
    CompanyContext,
    CompanyInvestigationRequest,
    CompanyInvestigationResponse,
    DeliveryMethod,
    InvestigationIntent,
    InvestigationPlan,
    InvestigationStatus,
    InvestigationTask,
    PresentationType,
    UnderstoodRequest,
)
from yuxi.company_due_diligence.agent.planner import (
    InvestigationPlanner,
    effective_message,
    extract_company_references,
    investigation_planner,
    presentation_for,
)
from yuxi.company_due_diligence.agent.quality import ClaimValidationError, build_claim_ledger
from yuxi.company_due_diligence.entity_resolution import EntityResolutionService, entity_resolution_service
from yuxi.company_due_diligence.models import (
    CompanyCandidate,
    CompanyReference,
    DataQuality,
    Entity,
    EntityType,
    LatencyBreakdown,
    RequestScopeStatus,
    ResolutionStatus,
    ResolvedCompany,
    SourceStatus,
)
from yuxi.company_due_diligence.qa.models import CompanyQuestionRequest, CompanyQuestionResponse
from yuxi.company_due_diligence.qa.service import CompanyQuestionService, company_question_service
from yuxi.company_due_diligence.report.intent import report_request_from_intent
from yuxi.company_due_diligence.report.service import (
    DueDiligenceReportService,
    ReportDispatchError,
    ReportValidationError,
    due_diligence_report_service,
)
from yuxi.services.task_service import Tasker


class CompanyDueDiligenceAgent:
    """统一处理企业上下文、调查意图、受控查询和报告任务。"""

    def __init__(
        self,
        *,
        resolver: EntityResolutionService | None = None,
        planner: InvestigationPlanner | None = None,
        question_service: CompanyQuestionService | None = None,
        report_service: DueDiligenceReportService | None = None,
    ) -> None:
        self._resolver = resolver or entity_resolution_service
        self._planner = planner or investigation_planner
        self._question_service = question_service or company_question_service
        self._report_service = report_service or due_diligence_report_service

    async def investigate(
        self,
        request: CompanyInvestigationRequest,
        *,
        tasker: Tasker | None = None,
    ) -> CompanyInvestigationResponse:
        """执行一次统一企业调查并按意图选择同步响应或报告任务。"""
        plan = self._planner.plan(request)
        message = effective_message(request)
        primary_reference, target_reference = _references_from_request(request, message, plan)
        if primary_reference is None:
            if plan.intent.delivery == DeliveryMethod.ASYNC_TASK:
                return _clarification_response(
                    plan,
                    "生成报告需要唯一企业范围，请在问题中补充企业名称、股票代码或统一社会信用代码。",
                )
            query_result = await self._question_service.query(
                CompanyQuestionRequest(
                    question=message,
                    hop_limit=request.hop_limit,
                    top_k=request.top_k,
                    include_low_confidence=request.include_low_confidence,
                ),
                plan=plan.query_plan,
            )
            return _query_without_company_response(plan, query_result)

        primary_resolution = await self._resolver.resolve(primary_reference)
        if primary_resolution.status != ResolutionStatus.RESOLVED or primary_resolution.company is None:
            return _resolution_response(plan, primary_resolution.candidates, primary_resolution.data_quality, False)

        target_resolution = None
        if target_reference is not None:
            target_resolution = await self._resolver.resolve(target_reference)
            if target_resolution.status != ResolutionStatus.RESOLVED or target_resolution.company is None:
                return _resolution_response(plan, target_resolution.candidates, target_resolution.data_quality, True)

        if plan.intent.delivery == DeliveryMethod.ASYNC_TASK:
            return await self._create_report_response(
                request=request,
                plan=plan,
                company=primary_resolution.company,
                resolution_latency=primary_resolution.latency,
                resolution_sources=primary_resolution.sources,
                tasker=tasker,
            )

        query_request = CompanyQuestionRequest(
            company_ref={"eid": primary_resolution.company.eid},
            target_company_ref=(
                {"eid": target_resolution.company.eid}
                if target_resolution is not None and target_resolution.company is not None
                else None
            ),
            question=message,
            start_date=plan.intent.time_scope.start_date,
            as_of=plan.intent.time_scope.end_date,
            hop_limit=request.hop_limit,
            top_k=request.top_k,
            include_low_confidence=request.include_low_confidence,
        )
        query_result = await self._question_service.query(query_request, plan=plan.query_plan)
        try:
            claim_ledger = build_claim_ledger(
                facts=query_result.facts,
                relations=query_result.relations,
                paths=query_result.paths,
                evidence=query_result.evidence,
                sources=query_result.sources,
            )
        except ClaimValidationError as exc:
            return _failed_response(plan, primary_resolution.company, str(exc), query_result.latency)
        return _query_response(
            plan=plan,
            query_result=query_result,
            claim_ledger=claim_ledger,
            company=primary_resolution.company,
            target_company=target_resolution.company if target_resolution else None,
            extra_resolution_ms=(
                primary_resolution.latency.total_ms + (target_resolution.latency.total_ms if target_resolution else 0)
            ),
        )

    async def _create_report_response(
        self,
        *,
        request: CompanyInvestigationRequest,
        plan: InvestigationPlan,
        company: ResolvedCompany,
        resolution_latency: LatencyBreakdown,
        resolution_sources,
        tasker: Tasker | None,
    ) -> CompanyInvestigationResponse:
        """把专题或完整报告意图提交到持久化报告任务。"""
        if tasker is None:
            return _failed_response(plan, company, "报告任务执行器不可用。", resolution_latency)
        report_request = report_request_from_intent(
            eid=company.eid,
            intent=plan.intent,
            include_low_confidence=request.include_low_confidence,
        )
        try:
            task = await self._report_service.create(report_request, tasker)
        except (ReportValidationError, ReportDispatchError) as exc:
            return _failed_response(plan, company, str(exc), resolution_latency)
        return CompanyInvestigationResponse(
            status=InvestigationStatus.ACCEPTED,
            understood_request=_understood_request(plan.intent),
            company_context=_company_context(company),
            entities=[_company_entity(company)],
            summary=_report_acceptance_summary(plan.intent, company.name),
            findings=[],
            sources=resolution_sources,
            data_quality=DataQuality(source_status=SourceStatus.AVAILABLE),
            presentation=presentation_for(plan.intent),
            task=InvestigationTask(id=task.id, status=task.status, progress=task.progress),
            latency=LatencyBreakdown(
                resolution_ms=resolution_latency.total_ms,
                total_ms=resolution_latency.total_ms,
            ),
        )


def _references_from_request(
    request: CompanyInvestigationRequest,
    message: str,
    plan: InvestigationPlan,
) -> tuple[CompanyReference | None, CompanyReference | None]:
    """从已确认上下文和明确法律全称中选择主企业与目标企业。"""
    references = extract_company_references(message)
    primary_reference = request.company_context.to_reference() if request.company_context else None
    target_reference = request.target_company_context.to_reference() if request.target_company_context else None

    if primary_reference is None and references:
        primary_reference = references.pop(0)
    elif request.company_context and request.company_context.name:
        references = [reference for reference in references if reference.name != request.company_context.name]

    if target_reference is None and _needs_target_company(plan.intent) and references:
        target_reference = references[0]
    return primary_reference, target_reference


def _needs_target_company(intent: InvestigationIntent) -> bool:
    """仅关系调查自动把第二个企业全称解释为目标主体。"""
    from yuxi.company_due_diligence.agent.models import InvestigationTopic

    return InvestigationTopic.RELATIONS in intent.topics


def _query_response(
    *,
    plan: InvestigationPlan,
    query_result: CompanyQuestionResponse,
    claim_ledger: ClaimLedger,
    company: ResolvedCompany,
    target_company: ResolvedCompany | None,
    extra_resolution_ms: float,
) -> CompanyInvestigationResponse:
    """将当前问答结果转换为统一智能体输出。"""
    has_data = bool(query_result.facts or query_result.relations or query_result.paths or query_result.evidence)
    if query_result.data_quality.source_status == SourceStatus.UNAVAILABLE:
        status = InvestigationStatus.PARTIAL if has_data else InvestigationStatus.FAILED
    elif query_result.data_quality.source_status == SourceStatus.PARTIAL:
        status = InvestigationStatus.PARTIAL
    elif has_data:
        status = InvestigationStatus.COMPLETE
    else:
        status = InvestigationStatus.NO_DATA
    latency = query_result.latency.model_copy(
        update={
            "resolution_ms": query_result.latency.resolution_ms + extra_resolution_ms,
            "total_ms": query_result.latency.total_ms + extra_resolution_ms,
        }
    )
    return CompanyInvestigationResponse(
        status=status,
        scope_status=(
            RequestScopeStatus.SOURCE_UNAVAILABLE
            if query_result.scope_status == RequestScopeStatus.SOURCE_UNAVAILABLE
            else RequestScopeStatus.RESOLVED
        ),
        understood_request=_understood_request(plan.intent),
        company_context=_company_context(company),
        target_company_context=_company_context(target_company) if target_company else None,
        entities=query_result.entities,
        summary=query_result.answer,
        findings=_findings(query_result),
        facts=query_result.facts,
        relations=query_result.relations,
        paths=query_result.paths,
        evidence=query_result.evidence,
        sources=query_result.sources,
        claim_ledger=claim_ledger,
        data_quality=query_result.data_quality,
        presentation=presentation_for(plan.intent),
        plan=plan.query_plan,
        steps=query_result.steps,
        projection_versions=query_result.projection_versions,
        latency=latency,
    )


def _resolution_response(
    plan: InvestigationPlan,
    candidates: list[CompanyCandidate],
    data_quality: DataQuality,
    target: bool,
) -> CompanyInvestigationResponse:
    """返回企业不唯一、未找到或来源不可用的澄清响应。"""
    subject = "目标企业" if target else "企业"
    if candidates:
        summary = f"{subject}存在多个候选，请确认后继续调查。"
    elif data_quality.source_status == SourceStatus.UNAVAILABLE:
        summary = f"{subject}主数据源当前不可用，未执行后续调查。"
    else:
        summary = f"未找到可确认的{subject}，请提供标准全称或重新搜索。"
    status = (
        InvestigationStatus.SOURCE_UNAVAILABLE
        if data_quality.source_status == SourceStatus.UNAVAILABLE
        else InvestigationStatus.NEEDS_CONTEXT
        if candidates
        else InvestigationStatus.NOT_FOUND
    )
    scope_status = (
        RequestScopeStatus.SOURCE_UNAVAILABLE
        if data_quality.source_status == SourceStatus.UNAVAILABLE
        else RequestScopeStatus.NEEDS_CONTEXT
        if candidates
        else RequestScopeStatus.NOT_FOUND
    )
    return _clarification_response(
        plan,
        summary,
        candidates=candidates,
        data_quality=data_quality,
        status=status,
        scope_status=scope_status,
    )


def _query_without_company_response(
    plan: InvestigationPlan,
    query_result: CompanyQuestionResponse,
) -> CompanyInvestigationResponse:
    """把全局统计或需要上下文的问答结果映射为统一智能体输出。"""
    if query_result.scope_status == RequestScopeStatus.GLOBAL_SCOPE:
        status = InvestigationStatus.GLOBAL_SCOPE
        intent = plan.intent
    elif query_result.scope_status == RequestScopeStatus.SOURCE_UNAVAILABLE:
        status = InvestigationStatus.SOURCE_UNAVAILABLE
        intent = plan.intent.model_copy(update={"presentation": PresentationType.CLARIFICATION})
    else:
        status = InvestigationStatus.NEEDS_CONTEXT
        intent = plan.intent.model_copy(update={"presentation": PresentationType.CLARIFICATION})
    return CompanyInvestigationResponse(
        status=status,
        scope_status=query_result.scope_status,
        understood_request=_understood_request(intent),
        candidates=query_result.candidates,
        global_result=query_result.global_result.model_dump(mode="json") if query_result.global_result else None,
        summary=query_result.answer,
        sources=query_result.sources,
        data_quality=query_result.data_quality,
        presentation=presentation_for(intent),
        plan=plan.query_plan,
        steps=query_result.steps,
        projection_versions=query_result.projection_versions,
        latency=query_result.latency,
    )


def _clarification_response(
    plan: InvestigationPlan,
    summary: str,
    *,
    candidates: list[CompanyCandidate] | None = None,
    data_quality: DataQuality | None = None,
    status: InvestigationStatus = InvestigationStatus.NEEDS_CONTEXT,
    scope_status: RequestScopeStatus = RequestScopeStatus.NEEDS_CONTEXT,
) -> CompanyInvestigationResponse:
    """返回不执行底层事实工具的最小澄清响应。"""
    intent = plan.intent.model_copy(
        update={
            "presentation": PresentationType.CLARIFICATION,
            "delivery": DeliveryMethod.SYNC,
        }
    )
    return CompanyInvestigationResponse(
        status=status,
        scope_status=scope_status,
        understood_request=_understood_request(intent),
        candidates=candidates or [],
        summary=summary,
        data_quality=data_quality or DataQuality(source_status=SourceStatus.AVAILABLE),
        presentation=presentation_for(intent),
    )


def _failed_response(
    plan: InvestigationPlan,
    company: ResolvedCompany,
    summary: str,
    latency: LatencyBreakdown,
) -> CompanyInvestigationResponse:
    """返回报告创建失败但企业上下文仍可复用的统一响应。"""
    return CompanyInvestigationResponse(
        status=InvestigationStatus.FAILED,
        understood_request=_understood_request(plan.intent),
        company_context=_company_context(company),
        entities=[_company_entity(company)],
        summary=summary,
        data_quality=DataQuality(source_status=SourceStatus.UNAVAILABLE, warnings=[summary]),
        presentation=presentation_for(plan.intent),
        latency=latency,
    )


def _understood_request(intent: InvestigationIntent) -> UnderstoodRequest:
    """把内部意图转换为用户可核对的理解摘要。"""
    return UnderstoodRequest(
        topics=intent.topics,
        presentation=intent.presentation,
        delivery=intent.delivery,
        time_scope=intent.time_scope,
        depth=intent.depth,
    )


def _company_context(company: ResolvedCompany) -> CompanyContext:
    """把确定企业转换为后续请求可复用的企业上下文。"""
    return CompanyContext(
        eid=company.eid,
        name=company.name,
        credit_code=company.credit_code,
        stock_code=company.stock_code,
    )


def _company_entity(company: ResolvedCompany) -> Entity:
    """把确定企业转换为统一实体。"""
    return Entity(
        entity_id=company.eid,
        entity_type=EntityType.COMPANY,
        name=company.name,
        attributes={
            "credit_code": company.credit_code,
            "stock_code": company.stock_code,
            "stock_abbreviation": company.stock_abbreviation,
        },
        source_ids=company.source_ids,
    )


def _findings(result: CompanyQuestionResponse) -> list[str]:
    """生成不引入新事实的结果规模摘要。"""
    findings = []
    if result.facts:
        findings.append(f"返回 {len(result.facts)} 条结构化事实。")
    if result.paths:
        findings.append(f"返回 {len(result.paths)} 条企业关系路径。")
    if result.evidence:
        findings.append(f"返回 {len(result.evidence)} 条文本证据。")
    return findings


def _report_acceptance_summary(intent: InvestigationIntent, company_name: str) -> str:
    """生成不包含企业事实的报告任务接受说明。"""
    if intent.presentation == PresentationType.FULL_REPORT:
        return f"已开始生成 {company_name} 的完整尽调报告。"
    topics = "、".join(topic.value for topic in intent.topics)
    return f"已开始生成 {company_name} 的 {topics} 专题报告。"


company_due_diligence_agent = CompanyDueDiligenceAgent()
