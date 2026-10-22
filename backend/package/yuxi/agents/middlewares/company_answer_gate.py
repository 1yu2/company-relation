"""企业尽调回答门禁：最终回答先过引用校验，再整段交给用户。

- 模型调用带 ``TAG_NOSTREAM``，token 不推送前端；节点结束时只发出通过校验（或回退）的整段回答。
- 只用本轮（最后一条用户消息之后）``cdd_`` 工具结果建索引，上一轮的结果不能被引用。
- 不通过时附上违规原因让模型重写，最多 ``max_rewrites`` 次；仍不通过则由代码列出本轮事实，
  不输出模型写的结论。每次尝试的原文、违规项与 token 用量写入最终消息的 ``response_metadata["answer_gate"]``。
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from decimal import Decimal

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.constants import TAG_NOSTREAM

from yuxi.company_due_diligence.agent.answer_gate import validate_answer_citations

FALLBACK_NOTICE = "未能生成通过来源校验的回答。以下是本轮工具返回的事实，未经模型组织："
# 兜底事实清单的字段中文名，覆盖结构化查询与 AkShare 产出的全部事实类型；
# 财务指标 financial_metric:<指标名> 直接用指标名。
FACT_TYPE_LABELS = {
    "business_status": "经营状态",
    "established_date": "成立日期",
    "legal_representative": "法定代表人",
    "registered_capital": "注册资本",
    "actual_capital": "实缴资本",
    "credit_code": "统一社会信用代码",
    "stock_code": "股票代码",
    "business_scope": "经营范围",
    "province_code": "省份代码",
    "district_code": "区县代码",
    "ownership_ratio": "持股比例",
    "subscribed_capital_total": "认缴出资总额",
    "paid_capital_total": "实缴出资总额",
    "subscribed_contribution": "认缴出资",
    "paid_contribution": "实缴出资",
    "financing_amount": "融资金额",
    "estimated_amount": "估算融资金额",
    "pre_money_valuation": "投前估值",
    "post_money_valuation": "投后估值",
    "penalty_amount": "处罚金额",
    "overdue_amount": "欠缴金额",
    "license_status": "许可证状态",
    "patent_item": "专利",
    "trademark_item": "商标",
    "copyright_item": "著作权",
    "stock_abbreviation": "股票简称",
    "total_market_cap": "总市值",
    "circulating_market_cap": "流通市值",
    "industry": "行业",
    "listing_date": "上市时间",
    "total_shares": "总股本",
    "circulating_shares": "流通股",
}


class CompanyAnswerGateMiddleware(AgentMiddleware):
    """只挂在企业尽调 Agent 上；工具调用响应原样放行，只校验最终回答。"""

    def __init__(self, max_rewrites: int = 2):
        super().__init__()
        self.max_rewrites = max_rewrites

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        model = request.model
        request = request.override(model=model.model_copy(update={"tags": [*(model.tags or []), TAG_NOSTREAM]}))
        tool_results, company_eids = _current_turn_results(request.messages)
        attempts = []
        for _ in range(self.max_rewrites + 1):
            response = await handler(request)
            answer = response.result[-1]
            if not isinstance(answer, AIMessage) or answer.tool_calls:
                return response
            result = validate_answer_citations(answer.text, tool_results, company_eids)
            # 失败稿不进入会话历史，在这里保留原文、违规项和 token 用量，供审计和评测如实统计。
            attempts.append(
                {
                    "answer": answer.text,
                    "violations": [v.model_dump() for v in result.violations],
                    "warnings": [w.model_dump() for w in result.warnings],
                    "usage": answer.usage_metadata,
                }
            )
            if result.passed:
                answer.response_metadata["answer_gate"] = _gate_metadata(attempts, result, fallback=False)
                return response
            feedback = "\n".join(f"- 引用 {v.citation or '（无）'}：{v.message}" for v in result.violations)
            request = request.override(
                messages=[
                    *request.messages,
                    answer,
                    HumanMessage(
                        content="［来源校验未通过］上一版回答存在以下问题，请只依据本轮工具结果重写完整回答，"
                        "不要提及本条说明。引用的事实必须在同一句写出其值，数值按系统提示中的格式书写"
                        f"（例如持股比例写作保留两位小数的百分比），不要照抄原始小数：\n{feedback}"
                    ),
                ]
            )
        fallback = AIMessage(content=_fallback_answer(tool_results), id=answer.id)
        fallback.response_metadata["answer_gate"] = _gate_metadata(attempts, result, fallback=True)
        return ModelResponse(result=[fallback], structured_response=None)


def _current_turn_results(messages: list) -> tuple[list[dict], set[str]]:
    """取最后一条用户消息之后、执行成功的 cdd_ 工具结果，以及其中出现的目标企业 eid。"""
    start = max((i for i, m in enumerate(messages) if isinstance(m, HumanMessage)), default=-1) + 1
    results = [
        json.loads(m.content)
        for m in messages[start:]
        if isinstance(m, ToolMessage) and (m.name or "").startswith("cdd_") and m.status != "error"
    ]
    eids = {r["eid"] for r in results if r.get("eid")} | {
        r["company"]["eid"] for r in results if r.get("status") == "resolved" and r.get("company")
    }
    return results, eids


def _gate_metadata(attempts: list, result, *, fallback: bool) -> dict:
    return {
        "attempts": attempts,
        "rewrites": len(attempts) - 1,
        "passed": not fallback,
        "fallback": fallback,
        "citation_coverage": result.citation_coverage,
    }


def _fallback_answer(tool_results: list[dict]) -> str:
    """按主体列出本轮事实与引用，字段用中文名，值转为可读文本。"""
    names = {e["entity_id"]: e["name"] for r in tool_results for e in r.get("entities", [])}
    lines = [FALLBACK_NOTICE]
    for fact in (f for r in tool_results for f in r.get("facts", [])):
        subject = names.get(fact["subject_id"], fact["subject_id"])
        lines.append(f"- {subject}｜{_fact_label(fact['fact_type'])}：{_fact_value(fact)}［F:{fact['fact_id']}］")
    return "\n".join(lines)


def _fact_label(fact_type: str) -> str:
    if fact_type.startswith("financial_metric:"):
        return fact_type.removeprefix("financial_metric:")
    return FACT_TYPE_LABELS.get(fact_type, fact_type)


def _fact_value(fact: dict) -> str:
    """比例显示为百分数；出资项和知识产权条目的规范值是字典，取可读部分；其余用展示值，没有时用规范值。"""
    value = fact["normalized_value"]
    if fact["fact_type"] == "ownership_ratio":
        return f"{Decimal(value) * 100:.2f}%"
    if isinstance(value, dict):
        if "name" in value:
            return value["name"]
        detail = "，".join(value[key] for key in ("invest_type", "date") if value.get(key))
        amount = f"{value.get('amount', '')}{value.get('currency', '')}"
        return f"{amount}（{detail}）" if detail else amount
    return fact.get("display_value") or str(value)
