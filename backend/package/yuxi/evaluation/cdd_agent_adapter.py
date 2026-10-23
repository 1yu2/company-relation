"""把企业尽调 Agent 的一次 Run 转成企业尽调评分器读取的字段。

与确定性 runner 的 ``_run_company_due_diligence`` 处于同一位置：只负责产出 ``actual``，评分仍由
``evaluate_company_due_diligence`` 完成，评分器版本不变，所以新旧实验可以逐题对比。

- ``resolution_status`` / ``company``：回答引用了事实时取最后一次解析成功的结果；没有引用事实时，若有
  未找到或有歧义的解析就取它。Agent 常在“点名企业未找到”后再确认上下文企业以便说明，最后一次解析
  并不代表回答的对象（v1 按最后一次解析取值，会把正确的拒答判成身份不一致）；本轮没有调用
  ``cdd_resolve_company`` 时，取最后一次成功的 ``cdd_query_facts`` 所用 eid：该工具查询前会校验 eid 能唯一
  解析，成功本身就证明企业已确认，不应因为模型省略了“先确认企业”这一步而判身份不一致（v4）；
- ``facts`` / ``evidence``：只计入最终回答用 ［F:…］/［E:…］ 引用了的结果（衡量“答了什么”，而不是“查到了什么”）；
- ``sources``：本次 Run 中 ``cdd_`` 工具返回的全部来源；
- 门禁回退时最终回答不是确定性回答，``facts`` / ``evidence`` 记为空，评分按事实缺失判失败。
"""

from __future__ import annotations

import json
import re
from typing import Any

from yuxi.company_due_diligence.answer_gate import lookup_result

# v3：引用 ID 与门禁使用同一识别规则（省略 fact: 等前缀但唯一对应时视为同一引用）。
ADAPTER_VERSION = "cdd-agent-adapter-v4"
_CITATION = re.compile(r"［([^］]*)］")
_CITATION_TOKEN = re.compile(r"^([FE]):(\S+)$")


def build_cdd_agent_actual(
    final_answer: str, tool_calls: list[dict[str, Any]], answer_gate: dict[str, Any] | None
) -> dict[str, Any]:
    """按调用顺序读取成功的 cdd_ 工具结果，结合最终回答的引用组装评分字段。"""
    results = [
        (call["name"], json.loads(call["output"]))
        for call in tool_calls
        if call["name"].startswith("cdd_") and call["status"] == "success"
    ]
    resolutions = [output for name, output in results if name == "cdd_resolve_company"]
    fallback = bool(answer_gate and answer_gate.get("fallback"))
    cited = {"F": [], "E": []}
    for block in _CITATION.findall("" if fallback else final_answer):
        for token in re.split(r"[\s,，、;；]+", block):
            if match := _CITATION_TOKEN.match(token):
                cited[match.group(1)].append(match.group(2))
    facts = {fact["fact_id"]: fact for _, output in results for fact in output.get("facts", [])}
    evidence = {item["evidence_id"]: item for _, output in results for item in output.get("evidence", [])}
    sources = {source["source_id"]: source for _, output in results for source in output.get("sources", [])}
    cited_facts = list(
        {fact["fact_id"]: fact for fact_id in cited["F"] if (fact := lookup_result(facts, fact_id))}.values()
    )
    unresolved = [output for output in resolutions if output["status"] != "resolved"]
    resolution = (unresolved if unresolved and not cited_facts else resolutions or [None])[-1]
    if resolution is None:
        queried = [output["eid"] for name, output in results if name == "cdd_query_facts"]
        resolution = {"status": "resolved", "company": {"eid": queried[-1]}} if queried else None
    return {
        "resolution_status": resolution["status"] if resolution else None,
        "company": resolution.get("company") if resolution else None,
        "facts": cited_facts,
        "evidence": list(
            {item["evidence_id"]: item for item_id in cited["E"] if (item := lookup_result(evidence, item_id))}.values()
        ),
        "sources": list(sources.values()),
        "answer_gate": answer_gate,
    }
