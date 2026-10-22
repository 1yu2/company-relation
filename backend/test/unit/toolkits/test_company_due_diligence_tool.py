"""企业外部数据补数工具测试。"""

from unittest.mock import AsyncMock

import pytest
from langchain_core.tools import ToolException

from yuxi.agents.toolkits.company_due_diligence import tools


async def test_external_data_tool_requires_confirmed_eid():
    """外部数据 Tool 缺少 confirmed_eid 时抛工具错误，生成 error 状态的工具消息。"""
    with pytest.raises(ToolException, match="confirmed_eid"):
        await tools.query_external_company_data.coroutine(
            question="补充外部财务数据",
            company_name="示例企业有限公司",
            confirmed_eid=None,
        )


async def test_external_data_tool_forwards_request(monkeypatch):
    """外部数据 Tool 透传参数到外部数据服务。"""
    from yuxi.company_due_diligence.external.models import ExternalCompanyDataResponse

    response = ExternalCompanyDataResponse(facts=[], sources=[])
    query = AsyncMock(return_value=response)
    monkeypatch.setattr(tools.external_company_data_service, "query", query)
    monkeypatch.setattr(
        "yuxi.agents.toolkits.company_due_diligence.tools.load_external_data_config",
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
        "yuxi.agents.toolkits.company_due_diligence.tools.load_external_data_config",
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
        "yuxi.agents.toolkits.company_due_diligence.tools.load_external_data_config",
        load_config,
    )

    with pytest.raises(ToolException, match="Provider 未启用"):
        await tools.query_external_company_data.coroutine(
            question="补充外部资料",
            company_name="示例企业有限公司",
            confirmed_eid="eid-1",
            topics=["profile"],
            providers=["akshare"],
        )
