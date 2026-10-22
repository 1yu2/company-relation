"""企业尽调智能体端到端：主聊天真实 Run，回答逐句引用工具结果并通过回答门禁。"""

from __future__ import annotations

import asyncio
import os
import re
import uuid

import pytest

from yuxi.repositories.agent_repository import COMPANY_DUE_DILIGENCE_AGENT_SLUG

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e, pytest.mark.slow]

RUN_TIMEOUT_SECONDS = int(os.getenv("E2E_RUN_TIMEOUT_SECONDS", "240"))
QUESTION = "广东纳睿雷达科技股份有限公司的注册资本是多少？第一大股东是谁，持股比例是多少？"


async def test_company_due_diligence_agent_answers_with_gated_citations(e2e_client, e2e_headers):
    """真实模型和只读 MySQL：答案引用 cdd_ 工具返回的事实，门禁元数据随最终消息落库。"""
    thread = await e2e_client.post(
        "/api/chat/thread",
        json={"agent_id": COMPANY_DUE_DILIGENCE_AGENT_SLUG, "title": f"cdd-e2e-{uuid.uuid4().hex[:8]}"},
        headers=e2e_headers,
    )
    assert thread.status_code == 200, thread.text
    thread_id = thread.json()["id"]
    run = await e2e_client.post(
        "/api/agent/runs",
        json={"query": QUESTION, "agent_slug": COMPANY_DUE_DILIGENCE_AGENT_SLUG, "thread_id": thread_id},
        headers=e2e_headers,
    )
    assert run.status_code == 200, run.text
    run_id = run.json()["run_id"]

    deadline = asyncio.get_running_loop().time() + RUN_TIMEOUT_SECONDS
    while asyncio.get_running_loop().time() < deadline:
        result = (await e2e_client.get(f"/api/agent/runs/{run_id}/result", headers=e2e_headers)).json()
        if result.get("status") in {"completed", "failed", "cancelled", "interrupted"}:
            break
        await asyncio.sleep(2)
    assert result.get("status") == "completed", result

    history = (await e2e_client.get(f"/api/chat/thread/{thread_id}/history", headers=e2e_headers)).json()["history"]
    tools = [call["name"] for message in history for call in message.get("tool_calls") or []]
    final = [message for message in history if message["type"] == "ai"][-1]
    gate = final["extra_metadata"]["response_metadata"]["answer_gate"]
    assert "cdd_resolve_company" in tools and "cdd_query_facts" in tools
    assert gate["passed"] and not gate["fallback"]
    assert re.search(r"［F:fact:[0-9a-f]+］", final["content"])
