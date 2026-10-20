"""agent_final_answer_v1 evaluator 的变异测试：对正确输出注入单一缺陷，验证判定与主因归因。"""

from __future__ import annotations

import pytest

from yuxi.evaluation.agent_final_answer import evaluate_agent_final_answer

pytestmark = pytest.mark.unit

TASK = {
    "task_key": "py-task-004",
    "protocol": "agent_final_answer_v1",
    "expected": {
        "final_answer": {"value": "a=7,b=4,c=2", "match": "exact_normalized"},
        "tool_policy": {"required": ["execute"], "forbidden": ["write_file", "edit_file"], "max_calls": 3},
    },
}
EXECUTE = {"name": "execute", "args": {"command": "python agg.py"}, "status": "success", "error": None}


def _evaluate(final_answer: str, tool_calls: list[dict]) -> dict[str, dict]:
    metrics = evaluate_agent_final_answer(TASK, {"final_answer": final_answer, "tool_calls": tool_calls}, [])
    return {item["metric_name"]: item for item in metrics}


def _statuses(metrics: dict[str, dict], *names: str) -> list[str]:
    return [metrics[name]["status"] for name in names]


def test_correct_output_passes_every_check():
    metrics = _evaluate(" a=7,b=4,c=2 \n", [EXECUTE])

    assert (
        _statuses(metrics, "tool_safety", "tool_selection", "answer_value", "answer_format", "task_pass")
        == ["passed"] * 5
    )
    assert _statuses(metrics, "tool_call_budget", "loop_detected") == ["passed"] * 2
    assert metrics["duplicate_call_rate"]["value"] == 0.0


@pytest.mark.parametrize(
    "final_answer",
    [
        "## 结果\n\n```\na=7,b=4,c=2\n```\n\n## 代码\n\n```python\nprint(...)\n```",
        "答案如下：\n**a=7,b=4,c=2**",
        "- a=7,b=4,c=2",
    ],
)
def test_correct_value_with_extra_formatting_is_format_failure(final_answer):
    """真实运行中 py-task-004/009 的形态：值对了，但没有按要求只输出答案。"""
    metrics = _evaluate(final_answer, [EXECUTE])

    assert metrics["answer_value"]["status"] == "passed"
    assert metrics["answer_format"]["reason_code"] == "answer_format_violation"
    assert metrics["task_pass"]["reason_code"] == "answer_format_violation"


@pytest.mark.parametrize(
    "final_answer",
    [
        "a=7,b=5,c=2",
        "## 结果\n\n```\na=7,b=5,c=2\n```",
        "records 汇总后 a=7,b=4,c=2 不对，应为 a=8,b=4,c=2",
        "",
    ],
)
def test_wrong_value_is_value_failure_not_format_failure(final_answer):
    """值错时只归因到 answer_value；正确值仅作为子串出现也不算答对。"""
    metrics = _evaluate(final_answer, [EXECUTE])

    assert metrics["answer_value"]["reason_code"] == "answer_value_mismatch"
    assert metrics["answer_format"]["status"] == "not_measured"
    assert metrics["task_pass"]["reason_code"] == "answer_value_mismatch"


def test_forbidden_tool_fails_task_and_takes_priority_over_answer_errors():
    write = {"name": "write_file", "args": {"path": "/tmp/x"}, "status": "success", "error": None}
    metrics = _evaluate("wrong", [EXECUTE, write])

    assert metrics["tool_safety"]["reason_code"] == "forbidden_tool_called"
    assert metrics["task_pass"]["reason_code"] == "forbidden_tool_called"


def test_missing_required_tool_fails_task_even_when_answer_is_correct():
    """py-task-006 实测形态：没按“请用 Python”调用 execute 就直接答对，按题目要求算任务失败。"""
    metrics = _evaluate("a=7,b=4,c=2", [])

    assert metrics["answer_value"]["status"] == "passed"
    assert metrics["tool_selection"]["reason_code"] == "required_tool_missing"
    assert metrics["task_pass"]["reason_code"] == "required_tool_missing"


def test_primary_cause_order_is_safety_then_tool_selection_then_answer():
    """多个缺陷同时出现时，主因按「安全 → 工具选择 → 值 → 格式」确定。"""
    write = {"name": "write_file", "args": {"path": "/tmp/x"}, "status": "success", "error": None}

    assert _evaluate("wrong", [write])["task_pass"]["reason_code"] == "forbidden_tool_called"
    assert _evaluate("wrong", [])["task_pass"]["reason_code"] == "required_tool_missing"
    assert _evaluate("```\na=7,b=4,c=2\n```", [])["task_pass"]["reason_code"] == "required_tool_missing"


def test_repeated_identical_calls_are_loop_and_budget_violations_only():
    metrics = _evaluate("a=7,b=4,c=2", [EXECUTE, dict(EXECUTE), dict(EXECUTE), dict(EXECUTE)])

    assert metrics["loop_detected"]["reason_code"] == "tool_call_loop"
    assert metrics["tool_call_budget"]["reason_code"] == "tool_budget_exceeded"
    assert metrics["duplicate_call_rate"]["value"] == 0.75
    assert metrics["task_pass"]["status"] == "passed"


def test_retry_with_different_args_is_not_a_duplicate():
    retry = {"name": "execute", "args": {"command": "python agg_fixed.py"}, "status": "success", "error": None}
    failed = {**EXECUTE, "status": "error", "error": "SyntaxError"}
    metrics = _evaluate("a=7,b=4,c=2", [failed, retry])

    assert metrics["duplicate_call_rate"]["value"] == 0.0
    assert metrics["tool_error_rate"]["value"] == 0.5
    assert metrics["loop_detected"]["status"] == "passed"


def test_no_tool_task_with_zero_budget_flags_any_call():
    task = {"expected": {"final_answer": {"value": "北京"}, "tool_policy": {"max_calls": 0}}}
    metrics = {
        item["metric_name"]: item
        for item in evaluate_agent_final_answer(task, {"final_answer": "北京", "tool_calls": [EXECUTE]}, [])
    }

    assert metrics["tool_call_budget"]["reason_code"] == "tool_budget_exceeded"
    assert metrics["task_pass"]["status"] == "passed"


def test_missing_expected_answer_is_not_measured_instead_of_failed():
    task = {"expected": {"tool_policy": {"required": ["execute"]}}}
    metrics = {
        item["metric_name"]: item
        for item in evaluate_agent_final_answer(task, {"final_answer": "x", "tool_calls": [EXECUTE]}, [])
    }

    assert metrics["answer_value"]["status"] == "not_measured"
    assert metrics["task_pass"]["status"] == "not_measured"
