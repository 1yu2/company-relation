"""平台评测的确定性 evaluator 和 pass 指标聚合。"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from typing import Any

from yuxi.evaluation.provenance import validate_trace_events

METRIC_VERSION = "deterministic-v1"


def normalize_text(value: Any) -> str:
    """规范化文本中的空白和大小写，供确定性答案比较使用。"""
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def evaluate_task(
    task: dict[str, Any],
    actual: dict[str, Any],
    trace_events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """按答案、工具策略和轨迹契约计算单题指标。"""
    expected = task.get("expected") or {}
    final_expected = expected.get("final_answer") or {}
    expected_value = final_expected.get("value") if isinstance(final_expected, dict) else final_expected
    actual_value = actual.get("final_answer", actual.get("answer"))
    metrics: list[dict[str, Any]] = []

    if expected_value is None:
        metrics.append(_metric("answer_match", None, "not_measured", "expected_answer_missing"))
    else:
        passed = normalize_text(actual_value) == normalize_text(expected_value)
        metrics.append(
            _metric(
                "answer_match", float(passed), "passed" if passed else "failed", None if passed else "answer_mismatch"
            )
        )

    policy = expected.get("tool_policy") or {}
    operations = [str(event.get("operation") or "") for event in trace_events]
    required = [str(item) for item in policy.get("required", [])]
    forbidden = [str(item) for item in policy.get("forbidden", [])]
    missing = [name for name in required if name not in operations]
    blocked = [name for name in forbidden if name in operations]
    tool_pass = not missing and not blocked
    tool_reason = "forbidden_tool_called" if blocked else "required_tool_missing" if missing else None
    metrics.append(_metric("tool_policy", float(tool_pass), "passed" if tool_pass else "failed", tool_reason))

    contract = expected.get("trace_contract") or {}
    trace_errors = validate_trace_events(trace_events)
    expected_sequence = [str(item) for item in contract.get("required_sequence", [])]
    actual_sequence = [str(event.get("component") or "") for event in trace_events]
    sequence_pass = _contains_ordered_sequence(actual_sequence, expected_sequence)
    operation_expected = contract.get("required_operation")
    operation_pass = not operation_expected or str(operation_expected) in operations
    trace_pass = sequence_pass and operation_pass and not trace_errors
    trace_reason = None if trace_pass else (trace_errors[0] if trace_errors else "trace_contract_violation")
    metrics.append(_metric("trace_contract", float(trace_pass), "passed" if trace_pass else "failed", trace_reason))

    missing_metric = next((item for item in metrics if item["value"] is None), None)
    task_pass = not missing_metric and all(item["status"] == "passed" for item in metrics)
    if any(item["status"] == "failed" for item in metrics):
        task_pass = False
    has_failure = any(item["status"] == "failed" for item in metrics)
    task_status = "passed" if task_pass else "failed" if has_failure else "not_measured"
    task_reason = None if task_pass else _first_failure(metrics) if has_failure else missing_metric["reason_code"]
    metrics.append(
        _metric(
            "task_pass",
            float(task_pass) if not missing_metric else None,
            task_status,
            task_reason,
        )
    )
    return metrics


def aggregate_pass_metrics(attempts: list[dict[str, Any]], k: int | None = None) -> dict[str, Any]:
    """按 task_key 聚合 pass@1、pass@k、pass^k 和 best@k。"""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for attempt in attempts:
        task_key = str(attempt.get("task_key") or attempt.get("task_id") or "")
        if task_key:
            groups[task_key].append(attempt)

    if not groups:
        return {
            "metric_version": METRIC_VERSION,
            "task_count": 0,
            "pass@1": None,
            "pass@k": None,
            "pass^k": None,
            "best@k": None,
        }

    for values in groups.values():
        values.sort(key=lambda item: int(item.get("attempt") or 1))
    effective_k = k or max(len(values) for values in groups.values())
    if effective_k < 1:
        raise ValueError("k must be greater than zero")

    pass_at_one = sum(sum(_is_success(item) for item in values) / len(values) for values in groups.values()) / len(
        groups
    )
    pass_at_k_values: list[float] = []
    pass_power_values: list[float] = []
    best_values: list[float] = []
    insufficient = 0
    for values in groups.values():
        successes = sum(_is_success(item) for item in values)
        if len(values) < effective_k:
            insufficient += 1
            pass_at_k_values.append(float("nan"))
            pass_power_values.append(float("nan"))
            best_values.append(float("nan"))
            continue
        pass_at_k_values.append(
            1.0 - math.comb(len(values) - successes, effective_k) / math.comb(len(values), effective_k)
        )
        first_k = values[:effective_k]
        pass_power_values.append(float(all(_is_success(item) for item in first_k)))
        best_values.append(max(float(item.get("score", item.get("task_pass", 0)) or 0) for item in first_k))

    def average(values: list[float]) -> float | None:
        usable = [value for value in values if not math.isnan(value)]
        return sum(usable) / len(usable) if usable else None

    return {
        "metric_version": METRIC_VERSION,
        "task_count": len(groups),
        "attempt_count": sum(len(values) for values in groups.values()),
        "k": effective_k,
        "insufficient_samples": insufficient,
        "pass@1": pass_at_one,
        "pass@k": average(pass_at_k_values),
        "pass^k": average(pass_power_values),
        "best@k": average(best_values),
    }


def _metric(name: str, value: float | None, status: str, reason_code: str | None) -> dict[str, Any]:
    return {
        "metric_name": name,
        "metric_version": METRIC_VERSION,
        "value": value,
        "status": status,
        "reason_code": reason_code,
    }


def _contains_ordered_sequence(actual: list[str], expected: list[str]) -> bool:
    if not expected:
        return True
    position = 0
    for item in actual:
        if item == expected[position]:
            position += 1
            if position == len(expected):
                return True
    return False


def _first_failure(metrics: list[dict[str, Any]]) -> str:
    return next((str(item["reason_code"]) for item in metrics if item.get("reason_code")), "evaluation_failed")


def _is_success(attempt: dict[str, Any]) -> bool:
    return bool(attempt.get("task_pass", attempt.get("status") == "passed"))
