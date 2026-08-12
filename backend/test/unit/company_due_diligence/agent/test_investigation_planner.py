"""企业尽调领域智能体意图解析单元测试。"""

from datetime import date

from yuxi.company_due_diligence.agent.models import (
    CompanyInvestigationRequest,
    DeliveryMethod,
    InvestigationShortcut,
    InvestigationTopic,
    PresentationType,
    TimeScopeType,
)
from yuxi.company_due_diligence.agent.planner import (
    InvestigationPlanner,
    extract_company_mentions,
    extract_company_references,
)
from yuxi.company_due_diligence.models import StructuredQueryType


def test_shareholder_question_maps_to_sync_list_presentation():
    """股东问题应走同步列表展示而不是用户选择内部模式。"""
    planner = InvestigationPlanner(today_provider=lambda: date(2026, 8, 11))

    result = planner.plan(CompanyInvestigationRequest(company_context={"name": "示例企业"}, message="股东有哪些？"))

    assert result.intent.topics == [InvestigationTopic.SHAREHOLDERS]
    assert result.intent.presentation == PresentationType.SUMMARY_WITH_LIST
    assert result.intent.delivery == DeliveryMethod.SYNC
    assert result.query_plan is not None
    assert result.query_plan.structured_queries == [StructuredQueryType.CURRENT_SHAREHOLDERS]
    assert result.query_plan.use_graph is False
    assert result.query_plan.evidence_types == []


def test_composite_report_parses_topics_and_recent_years():
    """复合报告要求应同时解析主题、时间和异步交付。"""
    planner = InvestigationPlanner(today_provider=lambda: date(2026, 8, 11))

    result = planner.plan(
        CompanyInvestigationRequest(
            company_context={"name": "示例企业"},
            message="请用报告形式分析这家企业近三年的关系和风险",
        )
    )

    assert result.intent.topics == [InvestigationTopic.RELATIONS, InvestigationTopic.RISKS]
    assert result.intent.presentation == PresentationType.TOPIC_REPORT
    assert result.intent.delivery == DeliveryMethod.ASYNC_TASK
    assert result.intent.time_scope.type == TimeScopeType.RECENT_YEARS
    assert result.intent.time_scope.years == 3
    assert result.intent.time_scope.start_date == date(2023, 8, 11)
    assert result.intent.time_scope.end_date == date(2026, 8, 11)
    assert result.intent.evidence_required is True
    assert result.query_plan is None


def test_full_report_shortcut_uses_standard_full_report_intent():
    """完整报告按钮应生成与自然语言相同的统一意图。"""
    planner = InvestigationPlanner(today_provider=lambda: date(2026, 8, 11))

    result = planner.plan(
        CompanyInvestigationRequest(
            company_context={"name": "示例企业"},
            shortcut=InvestigationShortcut.GENERATE_FULL_REPORT,
        )
    )

    assert result.intent.topics == [InvestigationTopic.FULL_DUE_DILIGENCE]
    assert result.intent.presentation == PresentationType.FULL_REPORT
    assert result.intent.delivery == DeliveryMethod.ASYNC_TASK
    assert result.intent.evidence_required is True


def test_full_report_button_and_natural_language_have_equivalent_intent():
    """完整报告按钮和等价自然语言应生成相同内部意图。"""
    planner = InvestigationPlanner(today_provider=lambda: date(2026, 8, 11))

    shortcut_plan = planner.plan(
        CompanyInvestigationRequest(
            company_context={"name": "示例企业"},
            shortcut=InvestigationShortcut.GENERATE_FULL_REPORT,
        )
    )
    natural_language_plan = planner.plan(
        CompanyInvestigationRequest(
            company_context={"name": "示例企业"},
            message="生成完整尽调报告",
        )
    )

    assert shortcut_plan.intent == natural_language_plan.intent


def test_extract_company_mentions_keeps_two_legal_names():
    """明确法律全称应按出现顺序提取为主企业和目标企业候选。"""
    message = "请分析深圳示例科技有限公司和广州目标股份有限公司有什么关系"

    assert extract_company_mentions(message) == ["深圳示例科技有限公司", "广州目标股份有限公司"]


def test_extract_company_references_supports_stock_and_credit_codes():
    """问题中的股票代码和信用代码应转换为确定性企业引用。"""
    references = extract_company_references("请查询 000001.SZ 和 914403001922038216 的风险")

    assert references[0].stock_code == "000001.SZ"
    assert references[1].credit_code == "914403001922038216"


def test_relation_question_uses_graph_without_unrelated_profile_query():
    """企业关系问题应只启用图路径工具，不附加企业概况查询。"""
    planner = InvestigationPlanner(today_provider=lambda: date(2026, 8, 11))

    result = planner.plan(CompanyInvestigationRequest(message="示例企业有限公司和目标企业有限公司有什么关系？"))

    assert result.query_plan.structured_queries == []
    assert result.query_plan.use_graph is True
    assert result.query_plan.evidence_types == []


def test_generic_risk_question_builds_structured_and_evidence_plan():
    """泛化风险问题应查询风险事实和必要证据，而不是企业概况。"""
    planner = InvestigationPlanner(today_provider=lambda: date(2026, 8, 11))

    result = planner.plan(
        CompanyInvestigationRequest(company_context={"name": "示例企业"}, message="近三年的风险有哪些？")
    )

    assert set(result.query_plan.structured_queries) == {
        StructuredQueryType.ADMINISTRATIVE_PENALTIES,
        StructuredQueryType.ENVIRONMENTAL_PENALTIES,
        StructuredQueryType.ABNORMAL_OPERATIONS,
        StructuredQueryType.LICENSES,
    }
    assert result.query_plan.use_graph is False
    assert len(result.query_plan.evidence_types) == 3
