"""系统主智能体企业尽调工具测试。"""

from unittest.mock import AsyncMock

from yuxi.agents.toolkits.company_due_diligence import tools
from yuxi.company_due_diligence.agent.models import (
    CompanyInvestigationResponse,
    InvestigationStatus,
    Presentation,
    PresentationType,
    UnderstoodRequest,
    DeliveryMethod,
    InvestigationDepth,
    InvestigationTopic,
)
from yuxi.company_due_diligence.models import DataQuality, SourceStatus


async def test_main_agent_tool_reuses_domain_agent_service(monkeypatch):
    """主智能体工具应原样构造上下文并调用同一个领域智能体服务。"""
    response = CompanyInvestigationResponse(
        status=InvestigationStatus.COMPLETE,
        understood_request=UnderstoodRequest(
            topics=[InvestigationTopic.SHAREHOLDERS],
            presentation=PresentationType.SUMMARY_WITH_LIST,
            delivery=DeliveryMethod.SYNC,
            depth=InvestigationDepth.STANDARD,
            time_scope={"type": "latest_available"},
        ),
        company_context={"eid": "eid-1", "name": "示例企业有限公司"},
        summary="返回股东结果。",
        data_quality=DataQuality(source_status=SourceStatus.AVAILABLE),
        presentation=Presentation(
            type=PresentationType.SUMMARY_WITH_LIST,
            components=["summary", "fact_table", "sources"],
        ),
    )
    investigate = AsyncMock(return_value=response)
    monkeypatch.setattr(tools.company_due_diligence_agent, "investigate", investigate)

    result = await tools.investigate_company.coroutine(
        question="这家企业的股东有哪些？",
        company_name="示例企业有限公司",
        confirmed_eid="eid-1",
        target_company_name=None,
        target_confirmed_eid=None,
    )

    request = investigate.await_args.args[0]
    assert request.company_context.eid == "eid-1"
    assert request.company_context.name == "示例企业有限公司"
    assert investigate.await_args.kwargs["tasker"] is tools.tasker
    assert result["presentation"]["type"] == "summary_with_list"


async def test_main_agent_tool_leaves_unconfirmed_company_for_domain_resolution(monkeypatch):
    """未提供上下文时应让领域智能体从自然语言中解析企业。"""
    response = CompanyInvestigationResponse(
        status=InvestigationStatus.CLARIFICATION,
        understood_request=UnderstoodRequest(
            topics=[InvestigationTopic.PROFILE],
            presentation=PresentationType.CLARIFICATION,
            delivery=DeliveryMethod.SYNC,
            depth=InvestigationDepth.STANDARD,
            time_scope={"type": "latest_available"},
        ),
        summary="请确认企业。",
        data_quality=DataQuality(source_status=SourceStatus.AVAILABLE),
        presentation=Presentation(
            type=PresentationType.CLARIFICATION,
            components=["clarification", "company_candidates"],
        ),
    )
    investigate = AsyncMock(return_value=response)
    monkeypatch.setattr(tools.company_due_diligence_agent, "investigate", investigate)

    await tools.investigate_company.coroutine(
        question="示例企业有限公司近年的风险有哪些？",
        company_name=None,
        confirmed_eid=None,
        target_company_name=None,
        target_confirmed_eid=None,
    )

    request = investigate.await_args.args[0]
    assert request.company_context is None
    assert request.question == "示例企业有限公司近年的风险有哪些？"


def test_main_agent_tool_schema_requires_question_and_accepts_legacy_message():
    """模型看到的 schema 应要求 question，同时兼容历史 message 参数。"""
    schema = tools.CompanyInvestigationToolInput.model_json_schema()

    assert schema["required"] == ["question"]
    assert "message" not in schema["properties"]
    assert tools.CompanyInvestigationToolInput.model_validate({"message": "查询企业风险"}).question == "查询企业风险"


async def test_external_data_tool_requires_confirmed_eid():
    """外部数据 Tool 缺少 confirmed_eid 时返回错误提示。"""
    result = await tools.query_external_company_data.coroutine(
        question="补充外部财务数据",
        company_name="示例企业有限公司",
        confirmed_eid=None,
    )
    assert "error" in result
    assert "confirmed_eid" in result["error"]


async def test_external_data_tool_forwards_request(monkeypatch):
    """外部数据 Tool 透传参数到外部数据服务。"""
    from yuxi.company_due_diligence.external.models import ExternalCompanyDataResponse

    response = ExternalCompanyDataResponse(facts=[], sources=[])
    query = AsyncMock(return_value=response)
    monkeypatch.setattr(tools.external_company_data_service, "query", query)
    monkeypatch.setattr(
        "yuxi.company_due_diligence.qa.service._load_external_data_config",
        AsyncMock(return_value={"akshare_enabled": True}),
    )

    await tools.query_external_company_data.coroutine(
        question="补充外部财务数据",
        company_name="示例企业有限公司",
        confirmed_eid="eid-1",
        stock_code="000001.SZ",
        topics=["profile", "financials"],
        providers=["akshare"],
    )

    request = query.await_args.args[0]
    assert request.company_ref.eid == "eid-1"
    assert request.company_ref.stock_code == "000001.SZ"
    assert request.topics == ["profile", "financials"]
    assert request.providers == ["akshare"]


async def test_external_data_tool_uses_enabled_providers_when_not_explicit(monkeypatch):
    """未显式指定 Provider 时，Tool 应使用管理端启用列表。"""
    from yuxi.company_due_diligence.external.models import ExternalCompanyDataResponse

    query = AsyncMock(return_value=ExternalCompanyDataResponse())
    monkeypatch.setattr(tools.external_company_data_service, "query", query)

    async def load_config():
        return {"akshare_enabled": True, "financing_enabled": False}

    monkeypatch.setattr(
        "yuxi.company_due_diligence.qa.service._load_external_data_config",
        load_config,
    )

    await tools.query_external_company_data.coroutine(
        question="补充外部资料",
        company_name="示例企业有限公司",
        confirmed_eid="eid-1",
        stock_code="000001.SZ",
        topics=["profile"],
    )

    assert query.await_args.kwargs["enabled_providers"] == ["akshare"]


async def test_external_data_tool_rejects_disabled_explicit_provider(monkeypatch):
    """显式指定已禁用 Provider 时不得绕过管理端开关。"""

    async def load_config():
        return {"akshare_enabled": False}

    monkeypatch.setattr(
        "yuxi.company_due_diligence.qa.service._load_external_data_config",
        load_config,
    )

    result = await tools.query_external_company_data.coroutine(
        question="补充外部资料",
        company_name="示例企业有限公司",
        confirmed_eid="eid-1",
        topics=["profile"],
        providers=["akshare"],
    )

    assert "Provider 未启用" in result["error"]
