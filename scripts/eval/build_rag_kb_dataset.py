"""从已入库的知识库中抽取片段，由 AI 出题，生成 `agent_rag_answer_v1` 知识库问答题集。

按用户决定（2026-10-23）评测不引入人工：每题由 AI 根据单个片段出一道只用该片段就能回答的问题，
并从片段原文中摘出答案必须包含的关键词；相关片段即出题片段（relevant_chunk_ids）。关键词必须原样出现在
片段中，否则丢弃该题，避免 AI 编造标准答案。

在 api 容器中运行（会调用模型）：
    docker exec -i api-dev uv run python - <kb_id> <题数> < scripts/eval/build_rag_kb_dataset.py > dataset.json
"""

from __future__ import annotations

import asyncio
import json
import sys

from sqlalchemy import text

from yuxi.models.chat import select_model
from yuxi.storage.postgres.manager import pg_manager

MODEL_SPEC = "alibaba-cn:qwen3.7-flash"
PROMPT = """下面是知识库中的一个文本片段。请出一道只依据这个片段就能回答的具体问题，并从片段原文中摘出 1–3 个
回答时必须出现的关键词（数字或专有名词，必须与原文逐字一致，每个不超过 15 个字）。

片段：
{content}

只输出一行 JSON：{{"question": "问题", "must_contain": ["关键词1", "关键词2"]}}"""


async def main(kb_id: str, count: int) -> None:
    pg_manager.initialize()
    async with pg_manager.get_async_session_context() as db:
        kb_name = (await db.execute(text("SELECT name FROM knowledge_bases WHERE kb_id = :kb_id"), {"kb_id": kb_id})).scalar()
        rows = (
            await db.execute(
                text(
                    "SELECT chunk_id, content FROM knowledge_chunks WHERE kb_id = :kb_id AND length(content) > 200 "
                    "ORDER BY chunk_index"
                ),
                {"kb_id": kb_id},
            )
        ).all()
    # 在整个知识库中均匀取样，覆盖不同文件和段落。
    step = max(1, len(rows) // (count * 2))
    candidates = rows[::step]
    model = select_model(MODEL_SPEC)
    tasks = []
    for chunk_id, content in candidates:
        if len(tasks) >= count:
            break
        reply = (await model.call(PROMPT.format(content=content[:1500]), stream=False)).content
        try:
            item = json.loads(reply[reply.index("{") : reply.rindex("}") + 1])
        except ValueError:
            continue
        keywords = [word for word in item.get("must_contain", []) if word and word in content]
        if not item.get("question") or not keywords:
            continue
        tasks.append(
            {
                "task_key": f"rag-{len(tasks) + 1:03d}",
                "protocol": "agent_rag_answer_v1",
                # 真实使用时用户会指定知识库（界面 @ 提及）；不写明时通用 Agent 不知道该查哪里。
                "input": {"query": f"请根据知识库「{kb_name}」回答：{item['question']}"},
                "expected": {"relevant_chunk_ids": [chunk_id], "answer_must_contain": keywords, "k": 5},
                "tags": ["rag", kb_id],
            }
        )
    dataset = {
        "dataset": {
            "name": f"知识库问答 {kb_id}",
            "source_kind": "rag",
            "description": "由 AI 依据知识库片段出题，评检索（Hit@K / MRR / nDCG）与答案要点。",
        },
        "source": {"origin": "scripts/eval/build_rag_kb_dataset.py", "kb_id": kb_id, "review": "AI 出题，不经人工"},
        "tasks": tasks,
    }
    json.dump(dataset, sys.stdout, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], int(sys.argv[2])))
