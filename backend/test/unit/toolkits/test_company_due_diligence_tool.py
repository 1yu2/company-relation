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
        message="这家企业的股东有哪些？",
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
        message="示例企业有限公司近年的风险有哪些？",
        company_name=None,
        confirmed_eid=None,
        target_company_name=None,
        target_confirmed_eid=None,
    )

    request = investigate.await_args.args[0]
    assert request.company_context is None
    assert request.message == "示例企业有限公司近年的风险有哪些？"
