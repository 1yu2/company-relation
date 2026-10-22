"""从 MySQL 源表独立生成 Q-02 参考答案，并用它给当前确定性工作流预评分。

参考答案只用只读 SQL 直接计算，不经过 StructuredQueryService 的选取和映射逻辑；
只复用两类与“答案是什么”无关的约定：稳定 ID（build_source_id / build_stable_id）和值规范化格式。

口径（2026-10-20 用户确认）：
- 当前股东：is_history=0 且持股比例 > 0；默认按持股比例取前 10，问题中出现“全部”时全部列出。
- 注册资本带单位：按原文金额（含万/亿）换算为基础货币单位。
- 股东认缴/实缴总额：total_*_capi_new 以万元计，*_currency_unit 只是币种代码；按带单位原文 total_*_capi 换算为元，
  原文没有币种时取币种代码字段（2026-10-21 用户确认，v4）。
- 不存在企业 + 上下文 eid：按实际情况，问题点名的企业不存在即为 not_found。
- 源数据持股合计不足 100% 时照实返回，只在审核清单中提示。

在 api 容器中运行（需要 MYSQL_URL）：
    uv run python build_cdd_q02_reference.py <tasks.json> <output.json>
tasks.json 为平台任务导出：[{task_id, version, input, expected}, ...]。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from yuxi.company_due_diligence.config import parse_mysql_url
from yuxi.company_due_diligence.models import build_source_id, build_stable_id
from yuxi.company_due_diligence.normalization import (
    normalize_decimal,
    normalize_optional_text,
    normalize_percentage,
)
from yuxi.company_due_diligence.qa.models import CompanyQuestionRequest
from yuxi.company_due_diligence.qa.service import company_question_service
from yuxi.evaluation.company_due_diligence import evaluate_company_due_diligence

DEFAULT_TOP_N = 10
AMOUNT_PATTERN = re.compile(r"([+-]?\d+(?:\.\d+)?)(万|亿)?元?(人民币|美元|港元)?")
CURRENCY_CODES = {"人民币": "CNY", "美元": "USD", "港元": "HKD"}
NOT_FOUND_NAME_SQL = "SELECT count(*) FROM t_enterprise WHERE name = :name OR format_name = :name"
ENTERPRISE_SQL = """
    SELECT e.eid, e.credit_no, e.name, e.regist_capi, e.oper_name, e.status, e.start_date, e.scope,
           l.stock_code, l.stock_abbr
    FROM t_enterprise e LEFT JOIN listed_company_gd l ON l.eid = e.eid
    WHERE e.eid = :eid
"""
SHAREHOLDER_SQL = """
    SELECT id, stock_id, stock_name, stock_percent, total_should_capi, should_currency_unit,
           total_real_capi, real_currency_unit
    FROM t_partners_alls
    WHERE eid = :eid AND is_history = 0
"""


async def main(tasks_path: str, output_path: str) -> None:
    tasks = [task for task in json.load(open(tasks_path)) if task["version"] == 1]
    engine = create_async_engine(parse_mysql_url(os.environ["MYSQL_URL"]))
    queried_at = datetime.now(UTC).isoformat()
    results = []
    async with engine.connect() as conn:
        await conn.execute(text("SET SESSION TRANSACTION READ ONLY"))
        for task in tasks:
            expected, notes = await build_reference(conn, task["input"])
            if expected is not None:
                expected |= {"review_status": "reference_generated", "reference_queried_at": queried_at}
            results.append({"task_id": task["task_id"], "input": task["input"], "expected": expected, "notes": notes})
    await engine.dispose()

    for item in results:
        if item["expected"] is not None:
            item["baseline"] = await score_current_workflow(item)
    with open(output_path, "w") as file:
        json.dump(results, file, ensure_ascii=False, indent=2, default=str)
    print_summary(results)


async def build_reference(conn, task_input: dict) -> tuple[dict | None, list[str]]:
    """按题型生成参考答案；暂不支持的题型返回 None 并说明原因。"""
    topic, query = task_input["topic"], task_input["query"]
    eid = (task_input.get("company_reference") or {}).get("eid")

    if topic == "refusal_quality":
        name = query.split("“")[1].split("”")[0]
        count = (await conn.execute(text(NOT_FOUND_NAME_SQL), {"name": name})).scalar()
        if count:
            return None, [f"问题点名的企业“{name}”在源表中存在 {count} 条，需要人工定义期望"]
        return {"status": "not_found", "facts": [], "source_refs": []}, [
            f"源表中不存在“{name}”；上下文 eid 指向的是另一家真实企业，不应回答它的数据"
        ]

    if topic in {"evidence_freshness", "report"}:
        return None, [f"{topic} 依赖证据索引或报告工作流，参考答案暂不由源表生成"]

    if topic == "entity_resolution":
        name = query.split("“")[1].split("”")[0] if "“" in query else None
        if name:
            rows = (await conn.execute(text("SELECT eid FROM t_enterprise WHERE name = :name"), {"name": name})).all()
            if len(rows) != 1:
                return None, [f"名称“{name}”在源表中有 {len(rows)} 条记录，需要人工定义期望"]
            eid = rows[0].eid
        company = await fetch_enterprise(conn, eid)
        return {
            "status": "resolved",
            "company": {key: company[key] for key in ("eid", "name", "credit_code", "stock_code")},
            "facts": [],
            "source_refs": [],
        }, []

    company = await fetch_enterprise(conn, eid)
    notes: list[str] = []
    facts: list[dict] = []
    # 改写题（Q-03）不一定含“注册资本”“股东”等词，按任务显式标注的意图取参考口径；Q-02 没有该字段，沿用关键词规则。
    intent = task_input.get("intent") or legacy_intent(topic, query)
    if intent == "profile_basics":
        facts += profile_facts(company, ("registered_capital", "legal_representative", "business_status"))
    elif intent == "current_holders":
        holders, holder_notes = await fetch_current_holders(conn, eid, list_all="全部" in query)
        facts += shareholder_facts(holders, with_capital=True)
        notes += holder_notes
    elif intent == "core_info_and_holders":
        facts += profile_facts(
            company,
            ("registered_capital", "legal_representative", "business_status", "established_date", "business_scope"),
        )
        holders, holder_notes = await fetch_current_holders(conn, eid, list_all=False)
        facts += shareholder_facts(holders, with_capital=False)
        notes += holder_notes + ["“核心经营信息”暂定为注册资本、法人、经营状态、成立日期、经营范围，需要确认"]
    else:
        return None, [f"未定义参考口径的题目：{topic} / {query}"]

    sources = sorted({fact["source_id"] for fact in facts})
    return {"status": "resolved", "facts": facts, "source_refs": [{"source_id": item} for item in sources]}, notes


def legacy_intent(topic: str, query: str) -> str | None:
    """Q-02 题没有 intent 字段时，按原关键词规则推断意图，保证 Q-02 参考答案可复现。"""
    if topic == "structured_fact" and "注册资本" in query:
        return "profile_basics"
    if topic == "structured_fact" and "股东" in query:
        return "current_holders"
    if topic == "routing_context":
        return "core_info_and_holders"
    return None


async def fetch_enterprise(conn, eid: str) -> dict:
    row = (await conn.execute(text(ENTERPRISE_SQL), {"eid": eid})).mappings().one()
    return {
        "eid": row["eid"],
        "name": row["name"],
        "credit_code": normalize_optional_text(row["credit_no"]),
        "stock_code": normalize_optional_text(row["stock_code"]),
        "row": dict(row),
    }


async def fetch_current_holders(conn, eid: str, *, list_all: bool) -> tuple[list[dict], list[str]]:
    rows = [dict(row) for row in (await conn.execute(text(SHAREHOLDER_SQL), {"eid": eid})).mappings()]
    positive = [row for row in rows if Decimal(normalize_percentage(row["stock_percent"]) or "0") > 0]
    positive.sort(key=lambda row: (-Decimal(normalize_percentage(row["stock_percent"])), row["id"]))
    total = sum(Decimal(normalize_percentage(row["stock_percent"])) for row in positive)
    notes = [f"当前股东 {len(rows)} 条，其中持股 > 0 的 {len(positive)} 条，持股合计 {total:.4f}"]
    if total < Decimal("0.999"):
        notes.append("源数据持股合计不足 100%，按口径照实返回")
    selected = positive if list_all else positive[:DEFAULT_TOP_N]
    return selected, notes


def profile_facts(company: dict, fact_types: tuple[str, ...]) -> list[dict]:
    row, eid = company["row"], company["eid"]
    source_id = build_source_id("t_enterprise", eid)
    capital, currency = parse_amount(row["regist_capi"])
    values = {
        "registered_capital": (capital, currency or "CNY"),
        "legal_representative": (normalize_optional_text(row["oper_name"]), None),
        "business_status": (normalize_optional_text(row["status"]), None),
        "established_date": (normalize_optional_text(row["start_date"]), None),
        "business_scope": (normalize_optional_text(row["scope"]), None),
    }
    return [fact(eid, fact_type, *values[fact_type], source_id) for fact_type in fact_types if values[fact_type][0]]


def parse_amount(value: str) -> tuple[str, str | None]:
    """解析源表“30299.5606万元人民币”“12600.2912万人民币”格式，金额换算为基础货币单位，未写币种时返回 None。"""
    match = AMOUNT_PATTERN.fullmatch(value.replace(",", "").replace(" ", ""))
    if match is None:
        raise ValueError(f"无法解析金额: {value}")
    amount, magnitude, currency = match.groups()
    multiplier = {None: 1, "万": 10_000, "亿": 100_000_000}[magnitude]
    return normalize_decimal(Decimal(amount) * multiplier), CURRENCY_CODES.get(currency)


def shareholder_facts(holders: list[dict], *, with_capital: bool) -> list[dict]:
    facts = []
    for row in holders:
        source_id = build_source_id("t_partners_alls", str(row["id"]))
        holder_key = normalize_optional_text(row["stock_id"]) or normalize_optional_text(row["stock_name"])
        subject_id = build_stable_id("shareholder", holder_key)
        facts.append(fact(subject_id, "ownership_ratio", normalize_percentage(row["stock_percent"]), None, source_id))
        if not with_capital:
            continue
        for fact_type, amount_field, unit_field in (
            ("subscribed_capital_total", "total_should_capi", "should_currency_unit"),
            ("paid_capital_total", "total_real_capi", "real_currency_unit"),
        ):
            text_value = normalize_optional_text(row[amount_field])
            if text_value is None:
                continue
            amount, currency = parse_amount(text_value)
            unit = currency or normalize_optional_text(row[unit_field])
            facts.append(fact(subject_id, fact_type, amount, unit, source_id))
    return facts


def fact(subject_id: str, fact_type: str, value: str, unit: str | None, source_id: str) -> dict:
    return {
        "fact_id": build_stable_id("fact", subject_id, fact_type, source_id, ""),
        "subject_id": subject_id,
        "fact_type": fact_type,
        "normalized_value": value,
        "unit": unit,
        "source_id": source_id,
    }


async def score_current_workflow(item: dict) -> dict:
    """用当前确定性工作流回答，再分别按严格口径和“必需事实子集”口径评分。"""
    task_input = item["input"]
    response = await company_question_service.query(
        CompanyQuestionRequest(
            company_ref=task_input.get("company_reference") or None,
            question=task_input["query"],
            enable_external_fallback=False,
        )
    )
    actual = response.model_dump(mode="json")
    metrics = evaluate_company_due_diligence({"input": task_input, "expected": item["expected"]}, actual)
    actual_by_id = {fact["fact_id"]: fact for fact in actual.get("facts", [])}
    expected_facts = item["expected"]["facts"]
    expected_ids = {f["fact_id"] for f in expected_facts}
    keys = ("normalized_value", "unit", "source_id")
    wrong = [
        {"expected": f, "actual": {k: actual_by_id[f["fact_id"]].get(k) for k in keys}}
        for f in expected_facts
        if f["fact_id"] in actual_by_id and any(actual_by_id[f["fact_id"]].get(k) != f[k] for k in keys)
    ]
    return {
        "metrics": {m["metric_name"]: m["status"] for m in metrics},
        "actual_status": actual.get("resolution_status"),
        "missing_facts": [f for f in expected_facts if f["fact_id"] not in actual_by_id],
        "wrong_facts": wrong,
        "extra_fact_types": sorted({f["fact_type"] for f in actual["facts"] if f["fact_id"] not in expected_ids}),
    }


def print_summary(results: list[dict]) -> None:
    for item in results:
        head = f"{item['task_id'][-6:]} {item['input']['topic']:<18}"
        if item["expected"] is None:
            print(head, "SKIPPED", "; ".join(item["notes"]))
            continue
        base = item["baseline"]
        print(
            head,
            "task_pass=" + base["metrics"]["task_pass"],
            f"missing={len(base['missing_facts'])} wrong={len(base['wrong_facts'])} extra={base['extra_fact_types']}",
            "| " + "; ".join(item["notes"]),
        )


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2]))
