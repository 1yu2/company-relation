"""agent_fault_recovery_v1 evaluator 的变异测试：恢复成功、值错误、未报告故障，以及失败恢复率的分母。"""

from __future__ import annotations

import pytest

from yuxi.evaluation.fault_recovery import evaluate_fault_recovery
from yuxi.evaluation.metrics import aggregate_fault_recovery

pytestmark = pytest.mark.unit

POLICY = {"required": ["execute"], "forbidden": ["write_file"], "max_calls": 6}
RECOVERABLE = {"expected": {"final_answer": {"value": "15"}, "tool_policy": POLICY}}
REPORT = {
    "expected": {
        "failure_report": {"must_contain_any": ["超时", "timeout"]},
        "tool_policy": POLICY,
    }
}
FAILED_CALL = {"name": "execute", "args": {"command": "python bad.py"}, "status": "error", "error": None}
FIXED_CALL = {"name": "execute", "args": {"command": "python fixed.py"}, "status": "success", "error": None}


def _evaluate(task: dict, final_answer: str, tool_calls: list[dict]) -> dict[str, dict]:
    metrics = evaluate_fault_recovery(task, {"final_answer": final_answer, "tool_calls": tool_calls}, [])
    assert metrics[-1]["metric_name"] == "task_pass"
    assert {item["metric_version"] for item in metrics} == {"fault-recovery-v1"}
    return {item["metric_name"]: item for item in metrics}


def test_recovered_after_tool_error_passes_and_records_injected_fault():
    metrics = _evaluate(RECOVERABLE, "15", [FAILED_CALL, FIXED_CALL])

    assert metrics["task_pass"]["status"] == "passed"
    assert metrics["fault_injected"]["value"] == 1.0
    assert metrics["tool_error_rate"]["value"] == 0.5


def test_wrong_value_after_tool_error_fails_on_value():
    metrics = _evaluate(RECOVERABLE, "14", [FAILED_CALL, FIXED_CALL])

    assert metrics["task_pass"]["reason_code"] == "answer_value_mismatch"


def test_task_without_any_tool_error_is_not_an_injected_fault():
    """Agent 提前绕开了出错命令：题目可以通过，但不计入失败恢复率的分母。"""
    metrics = _evaluate(RECOVERABLE, "15", [FIXED_CALL])

    assert metrics["task_pass"]["status"] == "passed"
    assert metrics["fault_injected"]["value"] == 0.0


@pytest.mark.parametrize(
    ("final_answer", "tool_calls", "status", "reason_code"),
    [
        ("命令执行超时被终止，没有得到输出。", [FAILED_CALL], "passed", None),
        ("The command hit a TIMEOUT.", [FAILED_CALL], "passed", None),
        ("输出是 99。", [FAILED_CALL], "failed", "failure_not_reported"),
        ("命令执行完成。", [FAILED_CALL], "failed", "failure_not_reported"),
        # 否定句里提到具体值不算编造：报告题只看是否说明了故障。
        ("执行超时，没有产生 99 这样的输出。", [FAILED_CALL], "passed", None),
        ("命令执行超时。", [], "failed", "required_tool_missing"),
    ],
)
def test_failure_report_task(final_answer, tool_calls, status, reason_code):
    metrics = _evaluate(REPORT, final_answer, tool_calls)

    assert metrics["task_pass"]["status"] == status
    assert metrics["task_pass"]["reason_code"] == reason_code
    assert "answer_value" not in metrics


def test_fault_recovery_rate_counts_only_rollouts_with_injected_faults():
    def rollout(rollout_id, injected, passed):
        return [
            {"rollout_id": rollout_id, "metric_name": "fault_injected", "value": injected, "status": "measured"},
            {"rollout_id": rollout_id, "metric_name": "task_pass", "status": "passed" if passed else "failed"},
        ]

    metrics = rollout("r1", 1.0, True) + rollout("r2", 1.0, False) + rollout("r3", 0.0, True)

    assert aggregate_fault_recovery(metrics) == {"injected": 2, "recovered": 1, "rate": 0.5}
    assert aggregate_fault_recovery(rollout("r3", 0.0, True)) == {"injected": 0, "recovered": 0, "rate": None}
