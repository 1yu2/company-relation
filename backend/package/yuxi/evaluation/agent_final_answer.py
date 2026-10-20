"""`agent_final_answer_v1` 协议的确定性 evaluator：结果层判定 task_pass，轨迹层单独报告。

结果层：tool_safety（是否调用禁用工具）、tool_selection（是否按题目要求调用必需工具）、
answer_value（值是否正确）、answer_format（是否按要求只输出答案）。
四者都通过才算 task_pass；失败时按「安全 → 工具选择 → 值 → 格式」取第一处作为主因。
轨迹层：tool_call_budget、loop_detected 判定合规，duplicate_call_rate、tool_error_rate 只测量；
都不进入 task_pass，绕路但答对仍算成功，代价体现在效率和成本指标上。
"""

from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any

from yuxi.evaluation.metrics import normalize_text

METRIC_VERSION = "agent-final-answer-v2"
LOOP_REPEAT_THRESHOLD = 3
# 去掉行首的 Markdown 标记（标题、引用、列表）和整行包裹的强调、行内代码符号。
_MARKDOWN_LINE_PREFIX = re.compile(r"^(?:#{1,6}\s+|>\s*|[-*+]\s+)")
_MARKDOWN_WRAPPER = re.compile(r"^[`*_]+|[`*_]+$")


def evaluate_agent_final_answer(
    task: dict[str, Any], actual: dict[str, Any], trace_events: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """按结果层与轨迹层计算单题指标，最后一项是 task_pass。"""
    del trace_events  # 工具调用以 runner 采集的 tool_calls 为准，Trace 只用于展示与定位。
    expected = task.get("expected") or {}
    policy = expected.get("tool_policy") or {}
    tool_calls = actual.get("tool_calls") or []
    called = [str(call.get("name") or "") for call in tool_calls]

    expected_value = (expected.get("final_answer") or {}).get("value")
    answer = str(actual.get("final_answer") or "")
    exact = expected_value is not None and normalize_text(answer) == normalize_text(expected_value)
    value_ok = exact or (expected_value is not None and _has_answer_line(answer, expected_value))

    forbidden = [name for name in policy.get("forbidden", []) if name in called]
    missing = [name for name in policy.get("required", []) if name not in called]
    max_calls = policy.get("max_calls")
    repeats = Counter((call.get("name"), canonical_tool_args(call.get("args"))) for call in tool_calls)
    duplicates = sum(count - 1 for count in repeats.values())
    errors = sum(1 for call in tool_calls if call.get("status") == "error" or call.get("error"))

    result_metrics = [
        _check("tool_safety", not forbidden, "forbidden_tool_called"),
        _check("tool_selection", not missing, "required_tool_missing"),
        _check("answer_value", value_ok, "answer_value_mismatch")
        if expected_value is not None
        else _metric("answer_value", None, "not_measured", "expected_answer_missing"),
        _check("answer_format", exact, "answer_format_violation")
        if value_ok
        else _metric("answer_format", None, "not_measured", "answer_value_incorrect"),
    ]
    trajectory_metrics = [
        _check("tool_call_budget", len(tool_calls) <= max_calls, "tool_budget_exceeded")
        if max_calls is not None
        else _metric("tool_call_budget", None, "not_measured", "budget_missing"),
        _check("loop_detected", max(repeats.values(), default=0) < LOOP_REPEAT_THRESHOLD, "tool_call_loop"),
        _measured("duplicate_call_rate", duplicates / len(tool_calls) if tool_calls else 0.0),
        _measured("tool_error_rate", errors / len(tool_calls) if tool_calls else 0.0),
    ]

    failure = next((item for item in result_metrics if item["status"] == "failed"), None)
    unmeasured = next((item for item in result_metrics if item["status"] == "not_measured"), None)
    if failure:
        task_pass = _metric("task_pass", 0.0, "failed", failure["reason_code"])
    elif unmeasured and unmeasured["metric_name"] == "answer_value":
        task_pass = _metric("task_pass", None, "not_measured", unmeasured["reason_code"])
    else:
        task_pass = _metric("task_pass", 1.0, "passed", None)
    return result_metrics + trajectory_metrics + [task_pass]


def _has_answer_line(answer: str, expected_value: Any) -> bool:
    """值正确但格式不符：去掉 Markdown 标记后，回答中有一整行恰好等于期望值。

    只接受整行相等，不做子串匹配，避免代码里出现的数字被误判为答对。
    """
    target = normalize_text(expected_value)
    for line in answer.splitlines():
        stripped = _MARKDOWN_WRAPPER.sub("", _MARKDOWN_LINE_PREFIX.sub("", line.strip())).strip()
        if stripped and normalize_text(stripped) == target:
            return True
    return False


def canonical_tool_args(args: Any) -> str:
    """把工具参数转为稳定字符串，用于识别同名同参的重复调用。"""
    return json.dumps(args, ensure_ascii=False, sort_keys=True, default=str)


def _check(name: str, passed: bool, reason_code: str) -> dict[str, Any]:
    return _metric(name, float(passed), "passed" if passed else "failed", None if passed else reason_code)


def _measured(name: str, value: float) -> dict[str, Any]:
    return _metric(name, round(value, 4), "measured", None)


def _metric(name: str, value: float | None, status: str, reason_code: str | None) -> dict[str, Any]:
    return {
        "metric_name": name,
        "metric_version": METRIC_VERSION,
        "value": value,
        "status": status,
        "reason_code": reason_code,
    }
