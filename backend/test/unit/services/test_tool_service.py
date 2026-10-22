from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from yuxi.agents.toolkits import service as tool_service


def test_get_tool_metadata_includes_config_guide(monkeypatch):
    tool_service._metadata_cache.clear()

    fake_tool = SimpleNamespace(
        name="demo_tool",
        description="demo description",
        metadata={},
        args_schema=None,
    )
    fake_extra = SimpleNamespace(
        category="buildin",
        tags=["demo"],
        display_name="演示工具",
        config_guide="请先配置 DEMO_API_KEY",
    )

    monkeypatch.setattr(
        "yuxi.agents.toolkits.registry.get_all_tool_instances",
        lambda: [fake_tool],
    )
    monkeypatch.setattr(
        "yuxi.agents.toolkits.registry.get_all_extra_metadata",
        lambda: {"demo_tool": fake_extra},
    )

    result = tool_service.get_tool_metadata()

    assert result == [
        {
            "slug": "demo_tool",
            "name": "演示工具",
            "description": "demo description",
            "metadata": {},
            "args": [],
            "category": "buildin",
            "tags": ["demo"],
            "config_guide": "请先配置 DEMO_API_KEY",
        }
    ]

    tool_service._metadata_cache.clear()


async def test_resolve_configured_runtime_tools_includes_non_buildin_local_tool(monkeypatch):
    """按名称配置的领域工具必须进入智能体运行时，而不受展示分类限制。"""
    company_tool = SimpleNamespace(name="cdd_query_facts")
    buildin_tool = SimpleNamespace(name="ask_user_question")

    monkeypatch.setattr(
        tool_service,
        "get_tool_instances_by_category",
        lambda category: {
            "buildin": [buildin_tool],
            "company_due_diligence": [company_tool],
        }.get(category, []),
    )
    monkeypatch.setattr(
        "yuxi.agents.mcp.service.get_enabled_mcp_tools",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        "yuxi.agents.middlewares.skills.resolve_skill_gated_tools",
        lambda _context: [],
    )

    tools = await tool_service.resolve_configured_runtime_tools(
        SimpleNamespace(tools=["cdd_query_facts"], mcps=[])
    )

    assert tools == [company_tool]
