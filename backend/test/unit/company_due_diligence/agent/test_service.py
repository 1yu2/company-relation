"""企业尽调领域智能体统一编排单元测试。"""

from datetime import UTC, date, datetime

from yuxi.company_due_diligence.agent.models import (
    CompanyInvestigationRequest,
    InvestigationShortcut,
    InvestigationStatus,
    PresentationType,
)
from yuxi.company_due_diligence.agent.planner import InvestigationPlanner
from yuxi.company_due_diligence.agent.service import CompanyDueDiligenceAgent
from yuxi.company_due_diligence.models import (
    CompanyResolutionResult,
    ConfidenceLevel,
    DataQuality,
    Entity,
    EntityType,
    Fact,
    LatencyBreakdown,
    MatchMethod,
    ResolutionStatus,
    ResolvedCompany,
    Source,
    SourceStatus,
    StructuredQueryType,
    build_source_id,
)
from yuxi.company_due_diligence.qa.models import CompanyQuestionResponse
from yuxi.company_due_diligence.report.models import ReportSection
from yuxi.services.task_service import Task


class FakeResolver:
    """按引用名称返回固定企业解析结果。"""

    def __init__(self) -> None:
        self.calls = []

    async def resolve(self, reference):
        self.calls.append(reference)
        name = reference.name or "示例企业有限公司"
        if name == "不存在企业有限公司":
            return _resolution(ResolutionStatus.NOT_FOUND, name=name)
        eid = "eid-target" if "目标" in name else "eid-primary"
        return _resolution(ResolutionStatus.RESOLVED, eid=eid, name=name)


class FakeQuestionService:
    """记录统一智能体下发的兼容问答请求。"""

    def __init__(self) -> None:
        self.requests = []

    async def query(self, request, *, plan=None):
        self.requests.append(request)
        self.plan = plan
        source_id = build_source_id("t_partners_alls", "partner-1")
        source = Source(
            source_id=source_id,
            table="t_partners_alls",
            record_key="partner-1",
            queried_at=datetime.now(UTC),
        )
        return CompanyQuestionResponse(
            question=request.question,
            resolution_status=ResolutionStatus.RESOLVED,
            company=_company("eid-primary", "示例企业有限公司"),
            answer="当前返回 1 条股东事实。",
            entities=[Entity(entity_id="eid-primary", entity_type=EntityType.COMPANY, name="示例企业有限公司")],
            facts=[
                Fact(
                    fact_id="fact-1",
                    subject_id="eid-primary",
                    fact_type="current_shareholder",
                    normalized_value="示例股东",
                    source_id=source_id,
                )
            ],
            sources=[source],
            data_quality=DataQuality(source_status=SourceStatus.AVAILABLE),
            latency=LatencyBreakdown(resolution_ms=1, mysql_ms=2, total_ms=3),
        )


class FakeReportService:
    """记录领域智能体生成的专题或完整报告请求。"""

    def __init__(self) -> None:
        self.requests = []

    async def create(self, request, tasker):
        self.requests.append((request, tasker))
        return Task(id="task-1", name="报告", type="company_due_diligence_report")


class InvalidSourceQuestionService(FakeQuestionService):
    """返回缺失来源索引的事实，用于验证后置拒答。"""

    async def query(self, request, *, plan=None):
        result = await super().query(request, plan=plan)
        result.sources = []
        return result


async def test_missing_company_context_returns_needs_context_without_resolution():
    """没有上下文和完整企业名称时返回 needs_context，不执行身份解析。"""
    resolver = FakeResolver()
    question_service = FakeQuestionService()
    agent = CompanyDueDiligenceAgent(resolver=resolver, question_service=question_service)

    result = await agent.investigate(CompanyInvestigationRequest(message="这家企业的股东有哪些？"))

    assert result.status == InvestigationStatus.NEEDS_CONTEXT
    assert result.presentation.type == PresentationType.CLARIFICATION
    assert resolver.calls == []
    assert len(question_service.requests) == 1
    assert question_service.requests[0].company_ref is None


async def test_sync_investigation_reuses_context_and_keeps_time_scope():
    """已确认企业上下文应转换为受控问答并返回统一动态展示。"""
    resolver = FakeResolver()
    question_service = FakeQuestionService()
    planner = InvestigationPlanner(today_provider=lambda: date(2026, 8, 11))
    agent = CompanyDueDiligenceAgent(
        resolver=resolver,
        planner=planner,
        question_service=question_service,
    )

    result = await agent.investigate(
        CompanyInvestigationRequest(
            company_context={"eid": "eid-primary", "name": "示例企业有限公司"},
            message="近三年的股东有哪些？",
        )
    )

    assert result.status == InvestigationStatus.COMPLETE
    assert result.company_context.eid == "eid-primary"
    assert result.presentation.type == PresentationType.SUMMARY_WITH_LIST
    assert result.facts[0].fact_type == "current_shareholder"
    assert result.claim_ledger.validated is True
    assert result.claim_ledger.claims[0].result_id == result.facts[0].fact_id
    assert question_service.plan.structured_queries == [StructuredQueryType.CURRENT_SHAREHOLDERS]
    assert question_service.requests[0].start_date == date(2023, 8, 11)
    assert question_service.requests[0].as_of == date(2026, 8, 11)


async def test_two_explicit_company_names_become_primary_and_target():
    """关系问题中的两个法律全称应分别解析并进入图查询请求。"""
    resolver = FakeResolver()
    question_service = FakeQuestionService()
    agent = CompanyDueDiligenceAgent(resolver=resolver, question_service=question_service)

    await agent.investigate(CompanyInvestigationRequest(message="示例企业有限公司和目标企业有限公司有什么关系？"))

    assert [request.name for request in resolver.calls] == ["示例企业有限公司", "目标企业有限公司"]
    assert question_service.requests[0].company_ref.eid == "eid-primary"
    assert question_service.requests[0].target_company_ref.eid == "eid-target"


async def test_report_intent_creates_topic_report_with_required_sections():
    """自然语言专题报告应映射主题章节并返回异步任务。"""
    resolver = FakeResolver()
    report_service = FakeReportService()
    planner = InvestigationPlanner(today_provider=lambda: date(2026, 8, 11))
    agent = CompanyDueDiligenceAgent(resolver=resolver, planner=planner, report_service=report_service)
    tasker = object()

    result = await agent.investigate(
        CompanyInvestigationRequest(
            company_context={"eid": "eid-primary", "name": "示例企业有限公司"},
            message="请用报告形式分析近三年的关系和风险",
        ),
        tasker=tasker,
    )

    report_request, used_tasker = report_service.requests[0]
    assert result.status == InvestigationStatus.ACCEPTED
    assert result.presentation.type == PresentationType.TOPIC_REPORT
    assert result.presentation.components == ["report_progress", "relations", "risks", "sources"]
    assert result.task.id == "task-1"
    assert report_request.sections == [
        ReportSection.IDENTITY,
        ReportSection.RELATIONS,
        ReportSection.COMPLIANCE,
        ReportSection.RISKS,
        ReportSection.DATA_QUALITY,
    ]
    assert report_request.start_date == date(2023, 8, 11)
    assert report_request.as_of == date(2026, 8, 11)
    assert used_tasker is tasker


async def test_full_report_shortcut_uses_all_sections():
    """完整报告按钮应映射全部标准章节。"""
    report_service = FakeReportService()
    agent = CompanyDueDiligenceAgent(resolver=FakeResolver(), report_service=report_service)

    result = await agent.investigate(
        CompanyInvestigationRequest(
            company_context={"name": "示例企业有限公司"},
            shortcut=InvestigationShortcut.GENERATE_FULL_REPORT,
        ),
        tasker=object(),
    )

    report_request, _ = report_service.requests[0]
    assert result.presentation.type == PresentationType.FULL_REPORT
    assert report_request.sections == list(ReportSection)


async def test_invalid_claim_source_returns_failed_without_fact_output():
    """后置来源校验失败时不得把未经验证事实返回给用户。"""
    agent = CompanyDueDiligenceAgent(
        resolver=FakeResolver(),
        question_service=InvalidSourceQuestionService(),
    )

    result = await agent.investigate(
        CompanyInvestigationRequest(
            company_context={"name": "示例企业有限公司"},
            message="股东有哪些？",
        )
    )

    assert result.status == InvestigationStatus.FAILED
    assert result.facts == []
    assert result.claim_ledger.claims == []
    assert "来源校验失败" in result.summary


def _resolution(status: ResolutionStatus, *, eid: str = "eid-primary", name: str) -> CompanyResolutionResult:
    """构造固定企业解析结果。"""
    company = _company(eid, name) if status == ResolutionStatus.RESOLVED else None
    return CompanyResolutionResult(
        status=status,
        reference={"name": name},
        company=company,
        sources=[],
        data_quality=DataQuality(source_status=SourceStatus.AVAILABLE),
        latency=LatencyBreakdown(total_ms=1),
    )


def _company(eid: str, name: str) -> ResolvedCompany:
    """构造固定企业身份。"""
    return ResolvedCompany(
        eid=eid,
        name=name,
        normalized_name=name,
        credit_code="credit-code",
        stock_code="000001.SZ",
        stock_abbreviation="示例",
        match_method=MatchMethod.EXACT_NAME,
        confidence=ConfidenceLevel.HIGH,
        source_ids=[],
    )
