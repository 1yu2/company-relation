"""企业尽调回答门禁中间件：用脚本化的模型响应验证放行、重写、回退和本轮范围。"""

from __future__ import annotations

import json
from types import SimpleNamespace

from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.constants import TAG_NOSTREAM

from yuxi.agents.middlewares.company_answer_gate import FALLBACK_NOTICE, CompanyAnswerGateMiddleware, _fallback_answer

EID = "eid-a"
FACTS = {
    "eid": EID,
    "facts": [
        {"fact_id": "fact:rep", "subject_id": EID, "fact_type": "legal_representative", "normalized_value": "包晓军"},
        {
            "fact_id": "fact:cap",
            "subject_id": EID,
            "fact_type": "registered_capital",
            "normalized_value": "302995606",
            "display_value": "30299.5606万元人民币",
        },
    ],
    "entities": [{"entity_id": EID, "name": "纳睿雷达"}],
    "relations": [],
}
GOOD = "法定代表人为包晓军［F:fact:rep］。"
TOOLS = [SimpleNamespace(name="cdd_query_facts"), SimpleNamespace(name="write_todos"), SimpleNamespace(name="execute")]
BAD = "法定代表人为李四［F:fact:rep］。"


def _turn(*tool_results: dict) -> list:
    return [
        HumanMessage(content="纳睿雷达的法定代表人是谁？"),
        AIMessage(content="", tool_calls=[{"name": "cdd_query_facts", "args": {}, "id": "c1"}]),
        *(
            ToolMessage(content=json.dumps(r, ensure_ascii=False), name="cdd_query_facts", tool_call_id="c1")
            for r in tool_results
        ),
    ]


async def _run(messages: list, *answers: AIMessage) -> tuple[ModelResponse, list[ModelRequest]]:
    """按顺序返回脚本化的模型回答，并记录每次交给模型的请求。"""
    requests: list[ModelRequest] = []
    scripted = iter(answers)

    async def handler(request: ModelRequest) -> ModelResponse:
        requests.append(request)
        return ModelResponse(result=[next(scripted)], structured_response=None)

    model = ChatOpenAI(model="test-model", api_key="test-key", base_url="https://example.com/v1")
    response = await CompanyAnswerGateMiddleware(allowed_tools=["cdd_query_facts"]).awrap_model_call(
        ModelRequest(model=model, messages=messages, tools=TOOLS), handler
    )
    return response, requests


async def test_passing_answer_is_returned_once_without_streaming():
    """通过校验的回答原样返回；模型调用带 nostream 标记，前端只收到整段回答。"""
    response, requests = await _run(_turn(FACTS), AIMessage(content=GOOD))

    assert response.result[-1].content == GOOD
    assert len(requests) == 1 and TAG_NOSTREAM in requests[0].model.tags
    # 只把配置的工具交给模型，平台默认的 execute、write_todos 等通用工具被过滤。
    assert [tool.name for tool in requests[0].tools] == ["cdd_query_facts"]
    gate = response.result[-1].response_metadata["answer_gate"]
    assert (gate["passed"], gate["rewrites"], gate["fallback"]) == (True, 0, False)


async def test_failed_answer_is_rewritten_with_violation_feedback():
    """不通过时把违规原因交回模型重写；失败稿只出现在重写请求里。"""
    response, requests = await _run(_turn(FACTS), AIMessage(content=BAD), AIMessage(content=GOOD))

    assert response.result[-1].content == GOOD
    feedback = requests[1].messages[-1]
    assert isinstance(feedback, HumanMessage) and "fact:rep" in feedback.content
    assert requests[1].messages[-2].content == BAD
    gate = response.result[-1].response_metadata["answer_gate"]
    assert gate["rewrites"] == 1 and gate["attempts"][0]["violations"][0]["code"] == "citation_value_mismatch"
    assert gate["attempts"][0]["answer"] == BAD


async def test_three_failures_fall_back_to_fact_list():
    """重写 2 次仍不通过：不输出模型结论，改由代码列出本轮事实。"""
    response, requests = await _run(_turn(FACTS), *(AIMessage(content=BAD) for _ in range(3)))

    content = response.result[-1].content
    assert len(requests) == 3
    assert content.startswith(FALLBACK_NOTICE) and "李四" not in content
    assert "纳睿雷达｜法定代表人：包晓军［F:fact:rep］" in content
    assert "纳睿雷达｜注册资本：30299.5606万元人民币［F:fact:cap］" in content
    gate = response.result[-1].response_metadata["answer_gate"]
    assert (gate["fallback"], len(gate["attempts"])) == (True, 3)


def test_fallback_fact_list_uses_chinese_labels_and_readable_values():
    """兜底清单不出现英文字段名：比例转百分数，出资项、知识产权条目和财务指标取可读部分。"""
    holder = "shareholder-1"
    facts = [
        {"fact_id": "fact:r", "subject_id": holder, "fact_type": "ownership_ratio", "normalized_value": "0.2441755716"},
        {
            "fact_id": "fact:c",
            "subject_id": holder,
            "fact_type": "subscribed_contribution",
            "normalized_value": {"amount": "1000000", "currency": "元", "invest_type": "货币", "date": "2020-01-01"},
        },
        {
            "fact_id": "fact:p",
            "subject_id": EID,
            "fact_type": "patent_item",
            "normalized_value": {"item_id": "patent-1", "name": "一种雷达"},
        },
        {"fact_id": "fact:m", "subject_id": EID, "fact_type": "financial_metric:营业收入", "normalized_value": "100"},
    ]
    entities = [{"entity_id": EID, "name": "纳睿雷达"}, {"entity_id": holder, "name": "某投资公司"}]

    lines = _fallback_answer([{"facts": facts, "entities": entities}]).splitlines()[1:]

    assert lines == [
        "- 某投资公司｜持股比例：24.42%［F:fact:r］",
        "- 某投资公司｜认缴出资：1000000元（货币，2020-01-01）［F:fact:c］",
        "- 纳睿雷达｜专利：一种雷达［F:fact:p］",
        "- 纳睿雷达｜营业收入：100［F:fact:m］",
    ]


async def test_tool_call_response_is_not_gated():
    """模型还要调用工具时不做检查，也不写门禁元数据。"""
    call = AIMessage(content="", tool_calls=[{"name": "cdd_resolve_company", "args": {"name": "x"}, "id": "c2"}])
    response, requests = await _run([HumanMessage(content="查一下")], call)

    assert response.result[-1] is call and len(requests) == 1
    assert "answer_gate" not in call.response_metadata


async def test_answer_without_citations_passes():
    """没有引用的闲聊回答不拦截。"""
    response, _ = await _run([HumanMessage(content="你好")], AIMessage(content="你好，请告诉我要查询的企业。"))

    assert response.result[-1].response_metadata["answer_gate"]["passed"]


async def test_only_current_turn_tool_results_can_be_cited():
    """上一轮的工具结果不能被引用：同一引用在新一轮没有重新取数时视为未知 ID。"""
    messages = [*_turn(FACTS), AIMessage(content=GOOD), HumanMessage(content="再说一遍法定代表人")]
    response, requests = await _run(messages, AIMessage(content=GOOD), AIMessage(content="请稍等，我需要重新查询。"))

    assert len(requests) == 2
    gate = response.result[-1].response_metadata["answer_gate"]
    assert gate["attempts"][0]["violations"][0]["code"] == "citation_unknown"
