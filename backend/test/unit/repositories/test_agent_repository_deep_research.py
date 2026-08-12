from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from yuxi.repositories.agent_repository import (
    AgentRepository,
    COMPANY_DUE_DILIGENCE_AGENT_SLUG,
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
    """企业尽调内置 Agent 应使用唯一 slug 和全局共享配置。"""
    db = CollectingDb()
    repo = AgentRepository(db)

    monkeypatch.setattr(repo, "get_by_slug", AsyncMock(return_value=None))

    agent = await repo.ensure_company_due_diligence_agent()

    assert agent.slug == COMPANY_DUE_DILIGENCE_AGENT_SLUG
    assert agent.backend_id == DEFAULT_AGENT_BACKEND_ID
    assert agent.is_subagent is False
    assert agent.share_config == DEFAULT_SHARE_CONFIG
    assert agent.config_json["context"]["tools"] == ["investigate_company"]


@pytest.mark.asyncio
async def test_ensure_company_due_diligence_agent_repairs_legacy_runtime_config(monkeypatch):
    """历史企业尽调 Agent 缺少工具或全局权限时应在启动阶段自动修复。"""
    db = CollectingDb()
    repo = AgentRepository(db)
    agent = SimpleNamespace(
        slug=COMPANY_DUE_DILIGENCE_AGENT_SLUG,
        backend_id=SUB_AGENT_BACKEND_ID,
        is_subagent=True,
        share_config={"access_level": "user", "department_ids": [], "user_uids": ["u1"]},
        config_json={"context": {"model": "deepseek:deepseek-chat"}},
        updated_by=None,
        updated_at=None,
    )
    monkeypatch.setattr(repo, "get_by_slug", AsyncMock(return_value=agent))

    result = await repo.ensure_company_due_diligence_agent(created_by="system")

    assert result is agent
    assert agent.backend_id == DEFAULT_AGENT_BACKEND_ID
    assert agent.is_subagent is False
    assert agent.share_config == DEFAULT_SHARE_CONFIG
    assert agent.config_json["context"]["model"] == "deepseek:deepseek-chat"
    assert agent.config_json["context"]["tools"] == ["investigate_company"]
    assert agent.config_json["context"]["system_prompt"].strip()
    db.commit.assert_awaited_once()
    db.refresh.assert_awaited_once_with(agent)
