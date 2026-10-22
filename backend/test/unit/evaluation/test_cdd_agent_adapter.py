"""企业尽调 Agent 评测接入：Run 结果转换、上下文 eid 注入、评分器版本与确定性 runner 一致。"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from yuxi.evaluation import agent_runner
from yuxi.evaluation.cdd_agent_adapter import build_cdd_agent_actual
from yuxi.evaluation.experiment_service import _evaluator_versions

COMPANY = {"eid": "eid-1", "name": "示例股份有限公司"}
RESOLVE = {"status": "resolved", "company": COMPANY, "candidates": [], "sources": [{"source_id": "s-id"}]}
FACTS = {
    "eid": "eid-1",
    "facts": [
        {"fact_id": "fact:cap", "fact_type": "registered_capital", "normalized_value": "100", "source_id": "s-cap"},
        {"fact_id": "fact:rep", "fact_type": "legal_representative", "normalized_value": "张三", "source_id": "s-rep"},
    ],
    "sources": [{"source_id": "s-cap"}, {"source_id": "s-rep"}],
}


def _call(name: str, output: dict, status: str = "success") -> dict:
    return {"name": name, "args": {}, "status": status, "error": None, "output": json.dumps(output, ensure_ascii=False)}


@pytest.mark.unit
def test_only_cited_facts_count_and_all_sources_are_kept():
    """facts 只收最终回答引用的事实；来源取本次 Run 全部 cdd_ 结果；身份取最后一次解析。"""
    not_found = {"status": "not_found", "company": None, "candidates": [], "sources": []}
    calls = [
        _call("cdd_resolve_company", not_found),
        _call("cdd_resolve_company", RESOLVE),
        _call("cdd_query_facts", FACTS),
    ]

    actual = build_cdd_agent_actual("注册资本为100元［F:fact:cap］。法定代表人为张三。", calls, {"fallback": False})

    assert (actual["resolution_status"], actual["company"]) == ("resolved", COMPANY)
    assert [fact["fact_id"] for fact in actual["facts"]] == ["fact:cap"]
    assert {source["source_id"] for source in actual["sources"]} == {"s-id", "s-cap", "s-rep"}


@pytest.mark.unit
def test_fallback_answer_counts_as_no_facts():
    """门禁回退列出的事实清单不是确定性回答，评分时按没有事实处理。"""
    calls = [_call("cdd_resolve_company", RESOLVE), _call("cdd_query_facts", FACTS)]

    actual = build_cdd_agent_actual(
        "未能生成通过来源校验的回答。\n- 注册资本：100［F:fact:cap］", calls, {"fallback": True}
    )

    assert actual["facts"] == [] and actual["resolution_status"] == "resolved"


@pytest.mark.unit
def test_failed_tool_calls_and_unknown_citations_are_ignored():
    """失败的工具调用不参与；引用了不存在的 ID 不会凭空产生事实。"""
    calls = [_call("cdd_resolve_company", RESOLVE), _call("cdd_query_facts", FACTS, status="error")]

    actual = build_cdd_agent_actual("注册资本为100元［F:fact:cap］［F:fact:missing］。", calls, None)

    assert actual["facts"] == []


@pytest.mark.unit
def test_cdd_agent_protocol_uses_same_evaluator_version_as_deterministic_runner():
    """评分器版本一致，新 Agent 实验才能与确定性基线逐题对比。"""
    tasks = [SimpleNamespace(protocol=agent_runner.CDD_AGENT_PROTOCOL)]

    assert _evaluator_versions("agent_run", tasks) == _evaluator_versions("company_due_diligence", tasks)


@pytest.mark.unit
async def test_runner_prefixes_context_eid_to_query(monkeypatch):
    """依赖“当前企业”的题，把 company_reference.eid 作为会话上下文写在问题前。"""

    class FakeDb:
        async def execute(self, _statement):
            return SimpleNamespace(scalar_one=lambda: SimpleNamespace(uid="admin"))

    @asynccontextmanager
    async def fake_session():
        yield FakeDb()

    messages = []

    async def fake_submit(*, command, **_kwargs):
        messages.append(command.input_message)
        raise RuntimeError("stop after submit")

    monkeypatch.setattr(agent_runner.pg_manager, "get_async_session_context", fake_session)
    monkeypatch.setattr(agent_runner, "submit_run_command", fake_submit)
    experiment = SimpleNamespace(
        experiment_id="exp-1",
        agent_slug="cdd",
        created_by="admin",
        config_snapshot={"tool_approval_mode": "always_trust"},
    )
    task = SimpleNamespace(
        task_key="t1",
        protocol=agent_runner.CDD_AGENT_PROTOCOL,
        input={"query": "这家公司的股东情况？", "company_reference": {"eid": "eid-1"}},
    )

    with pytest.raises(agent_runner.AgentRunnerError):
        await agent_runner.run_agent_task(experiment, task, "pro_1")

    assert messages[0].content == "（当前会话关联企业 eid：eid-1）\n这家公司的股东情况？"
