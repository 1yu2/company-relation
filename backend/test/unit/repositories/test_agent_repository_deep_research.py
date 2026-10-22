from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from yuxi.repositories.agent_repository import (
    AgentRepository,
    COMPANY_DUE_DILIGENCE_AGENT_SLUG,
    COMPANY_DUE_DILIGENCE_MODEL,
    COMPANY_DUE_DILIGENCE_SYSTEM_PROMPT,
    COMPANY_DUE_DILIGENCE_TOOLS,
    LEGACY_COMPANY_DUE_DILIGENCE_REACT_SLUG,
    DEEP_RESEARCH_AGENT_SLUG,
    DEFAULT_AGENT_BACKEND_ID,
    DEFAULT_SHARE_CONFIG,
    FACT_VERIFIER_AGENT_SLUG,
    RESEARCH_EXPLORER_AGENT_SLUG,
    SUB_AGENT_BACKEND_ID,
)


class CollectingDb:
    def __init__(self):
        self.added: list = []
        self.commit = AsyncMock()
        self.refresh = AsyncMock()
        self.execute = AsyncMock()

    def add(self, item):
        self.added.append(item)


@pytest.mark.asyncio
async def test_ensure_deep_research_agents_creates_orchestrator_and_subagents(monkeypatch):
    db = CollectingDb()
    repo = AgentRepository(db)

    async def get_by_slug(_slug):
        return None

    monkeypatch.setattr(repo, "get_by_slug", get_by_slug)

    await repo.ensure_deep_research_agents()

    created = {agent.slug: agent for agent in db.added}
    assert set(created) == {
        DEEP_RESEARCH_AGENT_SLUG,
        RESEARCH_EXPLORER_AGENT_SLUG,
        FACT_VERIFIER_AGENT_SLUG,
    }

    explorer = created[RESEARCH_EXPLORER_AGENT_SLUG]
    verifier = created[FACT_VERIFIER_AGENT_SLUG]
    assert explorer.backend_id == SUB_AGENT_BACKEND_ID and explorer.is_subagent is True
    assert verifier.backend_id == SUB_AGENT_BACKEND_ID and verifier.is_subagent is True

    orchestrator = created[DEEP_RESEARCH_AGENT_SLUG]
    assert orchestrator.backend_id == DEFAULT_AGENT_BACKEND_ID
    assert orchestrator.is_subagent is False
    assert orchestrator.is_default is False
    context = orchestrator.config_json["context"]
    assert context["subagents"] == [RESEARCH_EXPLORER_AGENT_SLUG, FACT_VERIFIER_AGENT_SLUG]
    assert context["skills"] == [DEEP_RESEARCH_AGENT_SLUG]
    assert context["system_prompt"].strip()


@pytest.mark.asyncio
async def test_ensure_deep_research_agents_is_idempotent(monkeypatch):
    db = CollectingDb()
    repo = AgentRepository(db)

    async def get_by_slug(slug):
        return SimpleNamespace(slug=slug)

    monkeypatch.setattr(repo, "get_by_slug", get_by_slug)

    await repo.ensure_deep_research_agents()

    assert db.added == []
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_ensure_company_due_diligence_agent_creates_one_global_instance(monkeypatch):
    """企业尽调智能体使用唯一 slug、全局共享，并以 cdd_ 工具和回答门禁运行。"""
    db = CollectingDb()
    repo = AgentRepository(db)

    monkeypatch.setattr(repo, "get_by_slug", AsyncMock(return_value=None))

    agent = await repo.ensure_company_due_diligence_agent()

    assert agent.slug == COMPANY_DUE_DILIGENCE_AGENT_SLUG
    assert agent.backend_id == DEFAULT_AGENT_BACKEND_ID
    assert agent.is_subagent is False
    assert agent.share_config == DEFAULT_SHARE_CONFIG
    assert agent.config_json["context"]["tools"] == COMPANY_DUE_DILIGENCE_TOOLS
    assert agent.config_json["context"]["company_answer_gate"] is True


@pytest.mark.asyncio
async def test_ensure_company_due_diligence_agent_migrates_deterministic_config(monkeypatch):
    """旧配置只挂 investigate_company 时一次性覆盖为 Agent 配置；阶段 3 的独立 Agent 记录被删除。"""
    db = CollectingDb()
    repo = AgentRepository(db)
    agent = SimpleNamespace(
        slug=COMPANY_DUE_DILIGENCE_AGENT_SLUG,
        backend_id=SUB_AGENT_BACKEND_ID,
        is_subagent=True,
        share_config={"access_level": "user", "department_ids": [], "user_uids": ["u1"]},
        config_json={"context": {"model": "deepseek:deepseek-chat", "tools": ["investigate_company"]}},
        description="旧描述",
        updated_by=None,
        updated_at=None,
    )
    legacy = SimpleNamespace(slug=LEGACY_COMPANY_DUE_DILIGENCE_REACT_SLUG)
    slugs = {COMPANY_DUE_DILIGENCE_AGENT_SLUG: agent, LEGACY_COMPANY_DUE_DILIGENCE_REACT_SLUG: legacy}
    monkeypatch.setattr(repo, "get_by_slug", AsyncMock(side_effect=slugs.get))
    delete = AsyncMock()
    monkeypatch.setattr(repo, "delete", delete)

    result = await repo.ensure_company_due_diligence_agent(created_by="system")

    assert result is agent
    delete.assert_awaited_once_with(agent=legacy)
    # 阶段 3 独立 Agent 的会话改绑到企业尽调智能体，否则打开这些会话会报“智能体不存在”。
    rebind = str(db.execute.await_args_list[0].args[0].compile(compile_kwargs={"literal_binds": True}))
    assert "UPDATE conversations SET agent_id='company-due-diligence'" in rebind
    assert "agent_id = 'company-due-diligence-react'" in rebind
    assert agent.backend_id == DEFAULT_AGENT_BACKEND_ID
    assert agent.is_subagent is False
    assert agent.share_config == DEFAULT_SHARE_CONFIG
    assert agent.config_json["context"]["tools"] == COMPANY_DUE_DILIGENCE_TOOLS
    assert agent.config_json["context"]["model"] == COMPANY_DUE_DILIGENCE_MODEL
    assert agent.config_json["context"]["system_prompt"] == COMPANY_DUE_DILIGENCE_SYSTEM_PROMPT
    db.refresh.assert_awaited_once_with(agent)


@pytest.mark.asyncio
async def test_ensure_company_due_diligence_agent_keeps_admin_changes(monkeypatch):
    """迁移只针对旧确定性配置；管理员改过的 Agent 配置不会被覆盖。"""
    db = CollectingDb()
    repo = AgentRepository(db)
    context = {"model": "provider:custom", "tools": ["cdd_resolve_company"], "system_prompt": "自定义"}
    agent = SimpleNamespace(
        slug=COMPANY_DUE_DILIGENCE_AGENT_SLUG,
        backend_id=DEFAULT_AGENT_BACKEND_ID,
        is_subagent=False,
        share_config=DEFAULT_SHARE_CONFIG.copy(),
        config_json={"context": dict(context)},
    )
    monkeypatch.setattr(repo, "get_by_slug", AsyncMock(side_effect={COMPANY_DUE_DILIGENCE_AGENT_SLUG: agent}.get))

    await repo.ensure_company_due_diligence_agent()

    assert agent.config_json["context"] == context
    db.refresh.assert_not_awaited()
