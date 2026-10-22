"""阶段 2 离线验证：用真实工具结果检验引用式回答门禁。

对 Q-02 v3 的 6 家企业真实调用 cdd_resolve_company / cdd_query_facts（工商、当前股东），然后：
- 构造四类回答：A 正确引用、B 数值或单位写错、C 未知 ID 或混入另一家企业的事实、D 不带引用；
- 把工具结果和引用格式要求交给模型，每个问题生成 1 条真实回答（15 条）；
- 全部回答过门禁，原样输出回答、工具结果摘要与门禁结论，供人工逐条核对。

在 api 容器中运行（只读 MySQL；模型调用会产生少量费用）：
    docker exec -i api-dev uv run python - < scripts/eval/validate_cdd_answer_gate.py > result.json
门禁规则调整后，用已保存的工具结果和模型回答重放（不再取数、不再调用模型）：
    docker cp result.json api-dev:/tmp/result.json
    docker exec -i api-dev uv run python - /tmp/result.json < scripts/eval/validate_cdd_answer_gate.py > replay.json
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from decimal import Decimal

from yuxi.agents.toolkits.company_due_diligence.atomic_tools import cdd_query_facts, cdd_resolve_company
from yuxi.company_due_diligence.answer_gate import validate_answer_citations
from yuxi.company_due_diligence.entity_resolution import entity_resolution_service
from yuxi.company_due_diligence.models import CompanyReference
from yuxi.company_due_diligence.mysql import company_due_diligence_mysql
from yuxi.models.chat import select_model

EIDS = [
    "0047ad44-ae81-4711-87e3-5662afa36c90",
    "0055a9ec-7f4f-466d-9752-f870d9770b2f",
    "00865d4e-f4fd-4451-b85a-72516bf23464",
    "00995a15-5aca-4a03-b1ba-6d088dc54d65",
    "00c41862-328c-4a7c-84f1-5ba3d385a45a",
    "00c844e2-32a8-493c-a1b7-65182a0de841",
]
MODEL_SPEC = "deepseek:deepseek-v4-flash"
PROMPT = """你是企业尽调助手。请只根据下面的工具结果回答用户问题，用简体中文自然语言作答。

引用规则（必须遵守）：
1. 每一句包含企业事实（名称以外的数值、日期、人名、状态、股东及持股等）的话，都要在句末用全角方括号标注引用，格式为［F:事实ID］，例如［F:fact:3a1c0d9e8b7f6a5d4c3b2a19］。
2. 一句话用到多个事实时，连写多个引用，例如［F:fact:aaa］［F:fact:bbb］。
3. 事实 ID 只能从工具结果的 facts[].fact_id 原样抄写，不得编造或改写。
4. 只写工具结果中有的事实；数值保持原值或按常用单位换算，不要估算。
5. 经营范围等长文本要么在一句中原样完整抄写，要么不展开；不得摘要、改写或拆成多条。
6. 不含具体事实的引导或总结句不需要引用。

用户问题：{question}

工具结果（JSON）：
{tool_results}
"""


async def main() -> None:
    saved = json.load(open(sys.argv[1], encoding="utf-8")) if len(sys.argv) > 1 else None
    companies = saved["companies"] if saved else [await _fetch_company(eid) for eid in EIDS]
    constructed = _constructed_samples(companies)
    for sample in constructed:
        sample["gate"] = _gate(sample["answer"], sample.pop("tool_results"), sample["eid"])
    model = None if saved else select_model(MODEL_SPEC)
    real = []
    for position, (company, question, results) in enumerate(_questions(companies)):
        if saved:
            answer = saved["real"][position]["answer"]
        else:
            prompt = PROMPT.format(question=question, tool_results=json.dumps(results, ensure_ascii=False))
            answer = (await model.call(prompt, stream=False)).content
        real.append(
            {
                "eid": company["eid"],
                "question": question,
                "answer": answer,
                "gate": _gate(answer, results, company["eid"]),
            }
        )
    await company_due_diligence_mysql.close()
    json.dump(
        {"model": MODEL_SPEC, "constructed": constructed, "real": real, "companies": companies},
        sys.stdout,
        ensure_ascii=False,
        indent=2,
    )


async def _fetch_company(eid: str) -> dict:
    """按阶段 1 的调用顺序真实取数：先名称解析，再查工商与当前股东。"""
    seed = await entity_resolution_service.resolve(CompanyReference(eid=eid))
    identity = await cdd_resolve_company.ainvoke({"name": seed.company.name})
    assert identity["status"] == "resolved" and identity["company"]["eid"] == eid
    profile = await cdd_query_facts.ainvoke({"eid": eid, "query_type": "enterprise_profile"})
    holders = await cdd_query_facts.ainvoke({"eid": eid, "query_type": "current_shareholders"})
    return {"eid": eid, "name": identity["company"]["name"], "profile": profile, "shareholders": holders}


def _constructed_samples(companies: list[dict]) -> list[dict]:
    """每家企业构造 A×3、B×3、C×2、D×2；C 的跨企业样本借用下一家企业的法定代表人事实。"""
    samples = []
    for position, company in enumerate(companies):
        other = companies[(position + 1) % len(companies)]
        p = _profile(company)
        holder_name, ratio, subscribed = _top_holder(company)
        both = [company["profile"], company["shareholders"]]
        cap_wan = p["registered_capital"]["display_value"].removesuffix("万元人民币")
        cap_yuan = f"{int(Decimal(p['registered_capital']['normalized_value'])):,}"
        percent = (Decimal(ratio["normalized_value"]) * 100).quantize(Decimal("0.01"))
        a_profile = (
            f"{company['name']}的注册资本为{cap_wan}万元人民币［F:{p['registered_capital']['fact_id']}］，"
            f"法定代表人为{p['legal_representative']['normalized_value']}［F:{p['legal_representative']['fact_id']}］。"
            f"公司成立于{p['established_date']['normalized_value']}［F:{p['established_date']['fact_id']}］。"
        )
        a_holder = (
            f"第一大股东为{holder_name}，持股{percent}%［F:{ratio['fact_id']}］，"
            f"认缴出资{subscribed['display_value']}［F:{subscribed['fact_id']}］。"
        )
        other_rep = _profile(other)["legal_representative"]
        cases = [
            ("A", "profile", a_profile, None),
            ("A", "holder", a_holder, None),
            ("A", "capital_in_yuan", f"注册资本为{cap_yuan}元［F:{p['registered_capital']['fact_id']}］。", None),
            ("B", "capital_unit", f"注册资本为{cap_wan}元［F:{p['registered_capital']['fact_id']}］。", None),
            ("B", "ratio_value", f"第一大股东{holder_name}持股{percent + 3}%［F:{ratio['fact_id']}］。", None),
            (
                "B",
                "representative",
                f"法定代表人为{other_rep['normalized_value']}［F:{p['legal_representative']['fact_id']}］。",
                None,
            ),
            (
                "C",
                "unknown_id",
                f"注册资本为{cap_wan}万元人民币［F:{p['registered_capital']['fact_id'][:-4]}0000］。",
                None,
            ),
            (
                "C",
                "other_company_fact",
                f"法定代表人为{other_rep['normalized_value']}［F:{other_rep['fact_id']}］。",
                [other["profile"]],
            ),
            ("D", "profile", _strip_citations(a_profile), None),
            ("D", "holder", _strip_citations(a_holder), None),
        ]
        for category, kind, answer, extra in cases:
            samples.append(
                {
                    "category": category,
                    "kind": kind,
                    "eid": company["eid"],
                    "answer": answer,
                    "tool_results": both + (extra or []),
                }
            )
    return samples


def _questions(companies: list[dict]):
    """每家企业问工商与股东各一题，前三家再问一题跨两类结果的组合题，共 15 题。"""
    for company in companies:
        yield company, f"{company['name']}的工商基本信息是什么？", [company["profile"]]
        yield company, f"{company['name']}的股东情况如何？", [company["shareholders"]]
    for company in companies[:3]:
        question = f"{company['name']}的注册资本是多少？第一大股东是谁，持股比例是多少？"
        yield company, question, [company["profile"], company["shareholders"]]


def _profile(company: dict) -> dict[str, dict]:
    return {fact["fact_type"]: fact for fact in company["profile"]["facts"]}


def _top_holder(company: dict) -> tuple[str, dict, dict]:
    """当前股东已按持股比例排序，第一条持股比例事实即第一大股东。"""
    holders = company["shareholders"]
    names = {entity["entity_id"]: entity["name"] for entity in holders["entities"]}
    ratio = next(f for f in holders["facts"] if f["fact_type"] == "ownership_ratio")
    subscribed = next(
        f
        for f in holders["facts"]
        if f["fact_type"] == "subscribed_capital_total" and f["subject_id"] == ratio["subject_id"]
    )
    return names[ratio["subject_id"]], ratio, subscribed


def _strip_citations(answer: str) -> str:
    return re.sub(r"［[^］]*］", "", answer)


def _gate(answer: str, tool_results: list[dict], eid: str) -> dict:
    result = validate_answer_citations(answer, tool_results, {eid})
    return {**result.model_dump(), "citation_coverage": result.citation_coverage}


if __name__ == "__main__":
    asyncio.run(main())
