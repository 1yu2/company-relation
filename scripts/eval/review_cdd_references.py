"""用 AI 核对企业尽调题集的参考答案：期望结果能否正确、完整地回答问题。

按用户决定（2026-10-23）评测不引入人工，由 AI 代替人工抽查。每题把问题、上下文企业、期望状态和期望事实交给
模型，判定“ok / suspicious + 原因”；只报告，不修改题集。参考答案由源表只读 SQL 生成，这里核对的是
“口径是否对题”，不是重新计算数值。

在 api 容器中运行（会调用模型）：
    docker exec -i api-dev uv run python - <version_id> [<version_id> ...] < scripts/eval/review_cdd_references.py > review.json
"""

from __future__ import annotations

import asyncio
import json
import sys

from yuxi.company_due_diligence.entity_resolution import entity_resolution_service
from yuxi.company_due_diligence.models import CompanyReference
from yuxi.company_due_diligence.mysql import company_due_diligence_mysql
from yuxi.models.chat import select_model
from yuxi.storage.postgres.manager import pg_manager
from yuxi.repositories.platform_evaluation_repository import PlatformEvaluationRepository

MODEL_SPEC = "alibaba-cn:qwen3.7-flash"
PROMPT = """你是企业尽调评测题集的审核员。下面是一道题和它的参考答案（期望结果），请判断参考答案是否能正确、完整地回答这道题。

只关注口径是否对题：期望事实是否覆盖了问题问到的内容、有没有答非所问、期望状态（resolved / not_found）是否合理。
说明：
- 数值来自数据库，不需要核对数值本身；源数据没有的字段（例如某股东没有实缴记录、企业没有当前股东）不算缺失。
- 事实按所属主体分组；股东用“股东#序号”标识，评分按事实 ID 与数值核对，不要求列出名称。
- 身份确认题的答案在“期望企业身份”中，期望事实为空是正常的。

题目：{query}
上下文企业：{company}
期望状态：{status}
期望企业身份：{identity}
期望事实（类型：值）：
{facts}

只输出一行 JSON：{{"verdict": "ok" 或 "suspicious", "reason": "一句话理由"}}"""


async def main(version_ids: list[str]) -> None:
    pg_manager.initialize()
    repo = PlatformEvaluationRepository()
    model = select_model(MODEL_SPEC)
    names: dict[str, str] = {}
    results = []
    for version_id in version_ids:
        for task in await repo.list_tasks(version_id):
            expected = task.expected or {}
            eid = ((task.input or {}).get("company_reference") or {}).get("eid")
            if eid and eid not in names:
                resolved = await entity_resolution_service.resolve(CompanyReference(eid=eid))
                names[eid] = resolved.company.name if resolved.company else eid
            subjects: dict[str, str] = {}
            lines = []
            for fact in expected.get("facts", []):
                subject = fact["subject_id"]
                label = "企业本身" if subject == eid else subjects.setdefault(subject, f"股东#{len(subjects) + 1}")
                lines.append(f"- [{label}] {fact['fact_type']}：{fact.get('display_value') or fact['normalized_value']}")
            facts = "\n".join(lines)
            prompt = PROMPT.format(
                query=(task.input or {}).get("query"),
                company=names.get(eid, "无"),
                status=expected.get("status"),
                identity=json.dumps(expected.get("company"), ensure_ascii=False) if expected.get("company") else "无",
                facts=facts or "无",
            )
            reply = (await model.call(prompt, stream=False)).content
            try:
                verdict = json.loads(reply[reply.index("{") : reply.rindex("}") + 1])
            except ValueError:
                verdict = {"verdict": "unparsed", "reason": reply[:200]}
            results.append({"version_id": version_id, "task_key": task.task_key, **verdict})
    await company_due_diligence_mysql.close()
    json.dump(results, sys.stdout, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
