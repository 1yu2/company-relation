"""生成 Q-03 企业尽调改写鲁棒性题集：Q-02 的 5 个意图，每个意图多种问法（问法清单经用户确认，2026-10-22）。

参考答案沿用 Q-02 的口径与生成方式（build_cdd_q02_reference.build_reference，只读 SQL），按任务的 intent 取口径，
与问法无关；同一企业同一意图的不同问法共用同一份期望事实。

把 scripts/eval 复制进 api 容器后运行（需要 MYSQL_URL）：
    docker cp scripts/eval api-dev:/tmp/eval
    docker exec api-dev uv run python /tmp/eval/build_cdd_q03_dataset.py /tmp/eval/datasets/cdd_q03_paraphrase_v1.json
    docker cp api-dev:/tmp/eval/datasets/cdd_q03_paraphrase_v1.json scripts/eval/datasets/
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import UTC, datetime

from build_cdd_q02_reference import build_reference, fetch_enterprise
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from yuxi.company_due_diligence.config import parse_mysql_url

COMPANY_EIDS = [
    "0047ad44-ae81-4711-87e3-5662afa36c90",
    "0055a9ec-7f4f-466d-9752-f870d9770b2f",
    "00865d4e-f4fd-4451-b85a-72516bf23464",
    "00995a15-5aca-4a03-b1ba-6d088dc54d65",
]
PARAPHRASES = {
    ("profile_basics", "structured_fact"): [
        "这家企业注册资本多少？法人是谁？现在还在正常经营吗？",
        "帮我查下公司的注册资本、法定代表人以及登记状态。",
        "法人代表是谁，注册资金有多少，公司是存续还是已经注销？",
        "介绍一下这家公司的工商登记信息：注册资本、法人、经营状态。",
    ],
    ("current_holders", "structured_fact"): [
        "这家公司的主要股东是谁？各自认缴出资多少？",
        "股权结构是怎样的？列一下主要股东和出资额。",
        "谁持有这家公司的股份，持股比例和出资情况如何？",
        "看一下目前登记在册的股东及其认缴、实缴出资。",
    ],
    ("core_info_and_holders", "routing_context"): [
        "先概述一下这家公司的经营基本面，再讲讲股东构成。",
        "这家公司的基本情况和股东情况分别是什么？",
        "给我一份这家公司的简要画像：注册资本、法人、经营状态、成立时间、经营范围，以及主要股东。",
        "这家企业靠谱吗？先说工商信息，再说谁是股东。",
    ],
}
IDENTITY_BY_NAME = ["“{name}”是哪家企业？给我它的信用代码和股票代码。", "帮我核实一下“{name}”这个主体。"]
IDENTITY_BY_EID = ["内部编号 {eid} 对应的是哪家公司？", "eid {eid} 是哪家企业？"]
REFUSALS = [
    "“Q03不存在企业”的股东结构怎么样？",
    "“Q03不存在企业”有没有经营风险？",
    "查一下“Q03不存在企业”的工商信息。",
    "“Q03不存在企业”的法人是谁？",
]


async def main(output_path: str) -> None:
    engine = create_async_engine(parse_mysql_url(os.environ["MYSQL_URL"]))
    queried_at = datetime.now(UTC).isoformat()
    tasks = []
    async with engine.connect() as conn:
        await conn.execute(text("SET SESSION TRANSACTION READ ONLY"))
        for key, task_input, tags in await _task_inputs(conn):
            expected, notes = await build_reference(conn, task_input)
            if expected is None:
                raise ValueError(f"{key} 无法生成参考答案：{notes}")
            expected |= {"review_status": "reference_generated", "reference_queried_at": queried_at}
            tasks.append(
                {"task_key": key, "protocol": "cdd_q02_review_v1", "input": task_input, "expected": expected, "tags": tags}
            )
    await engine.dispose()
    dataset = {
        "dataset": {
            "name": "企业尽调改写鲁棒性 Q-03",
            "source_kind": "company_due_diligence",
            "description": "Q-02 的 5 个意图各用多种问法提问，衡量问法变化对确定性工作流与企业尽调 Agent 的影响。",
        },
        "source": {
            "origin": "scripts/eval/build_cdd_q03_dataset.py",
            "paraphrases_reviewed": "问法清单经用户确认（2026-10-22）",
            "reference": "参考答案沿用 Q-02 口径，由源表只读 SQL 生成，未逐题人工核对",
            "reference_queried_at": queried_at,
        },
        "tasks": tasks,
    }
    with open(output_path, "w") as file:
        json.dump(dataset, file, ensure_ascii=False, indent=2, default=str)
    print(f"{len(tasks)} tasks -> {output_path}")


async def _task_inputs(conn) -> list[tuple[str, dict, list[str]]]:
    """I1–I3：4 种问法 × 4 家企业；I4：2 家企业 × 名称/eid 各 2 种；I5：4 种问法，上下文为不同的真实企业。"""
    inputs = []
    for (intent, topic), queries in PARAPHRASES.items():
        for index, query in enumerate(queries, start=1):
            for eid in COMPANY_EIDS:
                task_input = {"query": query, "topic": topic, "intent": intent, "company_reference": {"eid": eid}}
                inputs.append((f"cdd-q03-{intent}-p{index}-{eid[:8]}", task_input, [intent, f"p{index}"]))
    for eid in COMPANY_EIDS[:2]:
        name = (await fetch_enterprise(conn, eid))["name"]
        forms = [(q.format(name=name), {"name": name}) for q in IDENTITY_BY_NAME]
        forms += [(q.format(eid=eid), {"eid": eid}) for q in IDENTITY_BY_EID]
        for index, (query, reference) in enumerate(forms, start=1):
            task_input = {"query": query, "topic": "entity_resolution", "intent": "identity", "company_reference": reference}
            inputs.append((f"cdd-q03-identity-p{index}-{eid[:8]}", task_input, ["identity", f"p{index}"]))
    for index, (query, eid) in enumerate(zip(REFUSALS, COMPANY_EIDS, strict=True), start=1):
        task_input = {"query": query, "topic": "refusal_quality", "intent": "refusal", "company_reference": {"eid": eid}}
        inputs.append((f"cdd-q03-refusal-p{index}-{eid[:8]}", task_input, ["refusal", f"p{index}"]))
    return inputs


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
