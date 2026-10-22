"""阶段 3 冒烟：通过真实 AgentRun 向企业尽调 Agent 提问，记录门禁结论、工具调用、延迟和 token。

问题共 22 道：
- 阶段 2 离线验证的 15 道（6 家企业各问工商、股东，前三家再问注册资本和第一大股东）；
- Q-02 v4 中确定性工作流失败的 7 道（cdd-q02-021 至 027）。原题用“这家公司”指代评测上下文中的 eid，
  聊天中没有这个上下文，所以改写成企业全称。

逐题串行执行，延迟不受并发影响。在 api 容器中运行（会调用模型，产生少量费用）：
    docker exec -i api-dev uv run python - < scripts/eval/smoke_cdd_agent.py > smoke.json
"""

from __future__ import annotations

import asyncio
import json
import sys
import time

import httpx
from dotenv import dotenv_values

from yuxi.company_due_diligence.entity_resolution import entity_resolution_service
from yuxi.company_due_diligence.models import CompanyReference
from yuxi.company_due_diligence.mysql import company_due_diligence_mysql

AGENT_SLUG = "company-due-diligence"
BASE_URL = "http://localhost:5050/api"
STAGE2_EIDS = [
    "0047ad44-ae81-4711-87e3-5662afa36c90",
    "0055a9ec-7f4f-466d-9752-f870d9770b2f",
    "00865d4e-f4fd-4451-b85a-72516bf23464",
    "00995a15-5aca-4a03-b1ba-6d088dc54d65",
    "00c41862-328c-4a7c-84f1-5ba3d385a45a",
    "00c844e2-32a8-493c-a1b7-65182a0de841",
]
Q02_TASKS = {
    "cdd-q02-021": "0047ad44-ae81-4711-87e3-5662afa36c90",
    "cdd-q02-022": "0055a9ec-7f4f-466d-9752-f870d9770b2f",
    "cdd-q02-023": "00865d4e-f4fd-4451-b85a-72516bf23464",
    "cdd-q02-024": "00995a15-5aca-4a03-b1ba-6d088dc54d65",
    "cdd-q02-025": "00c41862-328c-4a7c-84f1-5ba3d385a45a",
    "cdd-q02-026": "00c844e2-32a8-493c-a1b7-65182a0de841",
    "cdd-q02-027": "00cebbca-7909-44ea-826e-b10de56c52e1",
}
Q02_QUERY = "{name}目前有哪些核心经营信息？接着说明它的股东情况。"


async def main() -> None:
    names = {eid: await _company_name(eid) for eid in {*STAGE2_EIDS, *Q02_TASKS.values()}}
    await company_due_diligence_mysql.close()
    questions = [(f"stage2-{eid[:8]}-profile", eid, f"{names[eid]}的工商基本信息是什么？") for eid in STAGE2_EIDS]
    questions += [(f"stage2-{eid[:8]}-holders", eid, f"{names[eid]}的股东情况如何？") for eid in STAGE2_EIDS]
    questions += [
        (f"stage2-{eid[:8]}-capital", eid, f"{names[eid]}的注册资本是多少？第一大股东是谁，持股比例是多少？")
        for eid in STAGE2_EIDS[:3]
    ]
    questions += [(key, eid, Q02_QUERY.format(name=names[eid])) for key, eid in Q02_TASKS.items()]

    env = dotenv_values("/app/.env")
    with httpx.Client(base_url=BASE_URL, timeout=httpx.Timeout(30, read=900)) as client:
        token = client.post(
            "/auth/token", data={"username": env["E2E_USERNAME"], "password": env["E2E_PASSWORD"]}
        ).json()["access_token"]
        client.headers["Authorization"] = f"Bearer {token}"
        records = [_ask(client, key, eid, question) for key, eid, question in questions]
    json.dump({"agent_slug": AGENT_SLUG, "records": records}, sys.stdout, ensure_ascii=False, indent=2)


async def _company_name(eid: str) -> str:
    result = await entity_resolution_service.resolve(CompanyReference(eid=eid))
    return result.company.name


def _ask(client: httpx.Client, key: str, eid: str, question: str) -> dict:
    """新建线程提一问，等到运行结束，再从会话历史读取工具调用、门禁结论和 token。"""
    thread = client.post("/chat/thread", json={"agent_id": AGENT_SLUG, "title": f"stage3 smoke {key}"}).json()
    started = time.monotonic()
    run = client.post("/agent/runs", json={"query": question, "agent_slug": AGENT_SLUG, "thread_id": thread["id"]})
    run_id = run.json()["run_id"]
    status = None
    with client.stream("GET", f"/agent/runs/{run_id}/events") as events:
        for line in events.iter_lines():
            if line.startswith("data:") and (event := json.loads(line[5:]))["event"] == "end":
                status = event["payload"]["status"]
                break
    latency = time.monotonic() - started
    history = client.get(f"/chat/thread/{thread['id']}/history").json()["history"]
    ai_messages = [m for m in history if m["type"] == "ai"]
    final = ai_messages[-1]
    metadata = final.get("extra_metadata") or {}
    gate = (metadata.get("response_metadata") or {}).get("answer_gate")
    return {
        "key": key,
        "eid": eid,
        "question": question,
        "thread_id": thread["id"],
        "run_status": status,
        "latency_seconds": round(latency, 2),
        "tool_calls": [
            {
                "name": call["name"],
                "args": call["args"],
                "error": (call.get("tool_call_result") or {}).get("status") == "error",
            }
            for m in ai_messages
            for call in m.get("tool_calls") or []
        ],
        # 历史中的 AI 消息只含最终稿；门禁失败稿的用量在 answer_gate.attempts 中，避免重复计入最终稿。
        "history_usage": [(m.get("extra_metadata") or {}).get("usage_metadata") for m in ai_messages],
        "answer_gate": gate,
        "answer": final["content"],
    }


if __name__ == "__main__":
    asyncio.run(main())
