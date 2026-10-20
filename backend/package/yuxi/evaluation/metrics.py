"""平台评测的确定性 evaluator 和 pass 指标聚合。"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from typing import Any

from yuxi.evaluation.provenance import validate_trace_events

METRIC_VERSION = "deterministic-v1"
# 样本少于该数量时 P95/P99 没有意义，只报中位数和最大值。
PERCENTILE_MIN_SAMPLES = 20


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

    measurable_groups = {
        task_key: [item for item in values if _is_measured(item)] for task_key, values in groups.items()
    }
    measured_groups = {task_key: values for task_key, values in measurable_groups.items() if values}
    not_measured_tasks = len(groups) - len(measured_groups)
    if not measured_groups:
        return {
            "metric_version": METRIC_VERSION,
            "task_count": len(groups),
            "measured_task_count": 0,
            "not_measured_tasks": not_measured_tasks,
            "attempt_count": sum(len(values) for values in groups.values()),
            "k": effective_k,
            "insufficient_samples": 0,
            "pass@1": None,
            "pass@k": None,
            "pass^k": None,
            "best@k": None,
        }

    pass_at_one = (
        sum(sum(_is_success(item) for item in values) / len(values) for values in measured_groups.values())
        / len(measured_groups)
    )
    pass_at_k_values: list[float] = []
    pass_power_values: list[float] = []
    best_values: list[float] = []
    insufficient = 0
    for values in measured_groups.values():
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
        "measured_task_count": len(measured_groups),
        "not_measured_tasks": not_measured_tasks,
        "attempt_count": sum(len(values) for values in groups.values()),
        "k": effective_k,
        "insufficient_samples": insufficient,
        "pass@1": pass_at_one,
        "pass@k": average(pass_at_k_values),
        "pass^k": average(pass_power_values),
        "best@k": average(best_values),
    }


def aggregate_rollout_performance(rollouts: list[dict[str, Any]]) -> dict[str, Any]:
    """汇总 rollout 状态分母、耗时分位数、token 用量和工具调用次数。

    耗时、token 和工具调用只统计带 Agent 结果的 rollout（runner 失败的没有这些数据），
    每项都给出自己的样本量；样本少于 PERCENTILE_MIN_SAMPLES 时不报 P95/P99。
    """
    outputs = [item.get("actual_output") or {} for item in rollouts]
    latencies = [output.get("latency") or {} for output in outputs]
    usages = [output["usage"] for output in outputs if output.get("usage")]
    tool_calls = [output["tool_calls"] for output in outputs if "tool_calls" in output]
    by_tool: dict[str, int] = defaultdict(int)
    for calls in tool_calls:
        for call in calls:
            by_tool[str(call.get("name") or "")] += 1
    return {
        "rollout_status": dict(sorted(Counter(str(item.get("status")) for item in rollouts).items())),
        "latency_ms": {
            "agent_total": _distribution([item.get("total_ms") for item in latencies]),
            "agent_queue": _distribution([item.get("queue_ms") for item in latencies]),
            "agent_execution": _distribution([item.get("execution_ms") for item in latencies]),
            "rollout": _distribution([item.get("duration_ms") for item in rollouts]),
        },
        "usage": {
            "sample_count": len(usages),
            "total_sum": sum(item.get("total", 0) for item in usages),
            "total_per_rollout": _distribution([item.get("total") for item in usages]),
            "input_sum": sum(item.get("input", 0) for item in usages),
            "output_sum": sum(item.get("output", 0) for item in usages),
            "llm_calls_per_rollout": _distribution([item.get("llm_calls") for item in usages]),
        },
        "tool_calls": {
            "per_rollout": _distribution([len(calls) for calls in tool_calls]),
            "by_name": dict(sorted(by_tool.items())),
        },
    }


def aggregate_metric_breakdown(metrics: list[dict[str, Any]]) -> dict[str, Any]:
    """按指标名统计各状态数量；判定类给出通过率（分母为 passed+failed），测量类给出均值。"""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for metric in metrics:
        groups[str(metric.get("metric_name"))].append(metric)
    breakdown = {}
    for name, items in sorted(groups.items()):
        counts = Counter(str(item.get("status")) for item in items)
        judged = counts["passed"] + counts["failed"]
        measured = [
            float(item["value"]) for item in items if item.get("status") == "measured" and item.get("value") is not None
        ]
        breakdown[name] = {
            "status_counts": dict(sorted(counts.items())),
            "pass_rate": counts["passed"] / judged if judged else None,
            "measured_mean": sum(measured) / len(measured) if measured else None,
            "reason_counts": dict(
                sorted(Counter(str(item["reason_code"]) for item in items if item.get("reason_code")).items())
            ),
        }
    return breakdown


def compare_paired_outcomes(base: dict[str, str], candidate: dict[str, str]) -> dict[str, Any]:
    """按 task_key 配对两次实验的 task_pass 状态，统计修复/退化并做 McNemar 精确检验。

    只有两边都是 passed/failed 的题才进入配对；任一边未测或缺失的题单独计数，不参与通过率与检验。
    """
    judged = {"passed", "failed"}
    paired = sorted(key for key in base.keys() & candidate.keys() if {base[key], candidate[key]} <= judged)
    fixed = [key for key in paired if base[key] == "failed" and candidate[key] == "passed"]
    broken = [key for key in paired if base[key] == "passed" and candidate[key] == "failed"]
    base_passed = sum(base[key] == "passed" for key in paired)
    candidate_passed = sum(candidate[key] == "passed" for key in paired)
    return {
        "paired_count": len(paired),
        "unpaired_task_keys": sorted((base.keys() | candidate.keys()) - set(paired)),
        "fixed": fixed,
        "broken": broken,
        "both_pass": sum(base[key] == candidate[key] == "passed" for key in paired),
        "both_fail": sum(base[key] == candidate[key] == "failed" for key in paired),
        "base_pass_rate": base_passed / len(paired) if paired else None,
        "candidate_pass_rate": candidate_passed / len(paired) if paired else None,
        "pass_rate_delta": (candidate_passed - base_passed) / len(paired) if paired else None,
        "mcnemar_p_value": _mcnemar_exact_p(len(fixed), len(broken)),
    }


def estimate_cost(usage: dict[str, Any], pricing: dict[str, Any] | None) -> dict[str, Any] | None:
    """按调用方提供的每百万 token 单价估算成本；未提供单价时返回 None，不做猜测。

    input 中包含缓存命中的 token（如 DeepSeek 的 cache_read），按同一单价计算，结果是上限估计。
    """
    if not pricing:
        return None
    input_cost = usage.get("input_sum", 0) * pricing["input_per_million"] / 1_000_000
    output_cost = usage.get("output_sum", 0) * pricing["output_per_million"] / 1_000_000
    samples = usage.get("sample_count", 0)
    return {
        "currency": pricing["currency"],
        "input": input_cost,
        "output": output_cost,
        "total": input_cost + output_cost,
        "per_rollout_mean": (input_cost + output_cost) / samples if samples else None,
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


def _is_measured(attempt: dict[str, Any]) -> bool:
    return attempt.get("task_pass") is not None or attempt.get("status") in {"passed", "failed"}


def _is_success(attempt: dict[str, Any]) -> bool:
    return bool(attempt.get("task_pass") is True or attempt.get("status") == "passed")


def _distribution(values: list[Any]) -> dict[str, Any]:
    """计算样本量、中位数、最大值、均值，以及样本足够时的 P95/P99（线性插值）。"""
    numbers = sorted(
        float(value) for value in values if isinstance(value, (int, float)) and not isinstance(value, bool)
    )
    if not numbers:
        return {"sample_count": 0, "p50": None, "p95": None, "p99": None, "max": None, "mean": None}
    enough = len(numbers) >= PERCENTILE_MIN_SAMPLES
    return {
        "sample_count": len(numbers),
        "p50": _percentile(numbers, 50),
        "p95": _percentile(numbers, 95) if enough else None,
        "p99": _percentile(numbers, 99) if enough else None,
        "max": numbers[-1],
        "mean": sum(numbers) / len(numbers),
    }


def _percentile(sorted_values: list[float], percent: float) -> float:
    position = (len(sorted_values) - 1) * percent / 100
    lower = math.floor(position)
    upper = math.ceil(position)
    return sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * (position - lower)


def _mcnemar_exact_p(fixed: int, broken: int) -> float:
    """双侧 McNemar 精确检验：不一致对服从 Binomial(n, 0.5)，p = 2 * P(X <= min(b, c))，上限为 1。"""
    discordant = fixed + broken
    if discordant == 0:
        return 1.0
    tail = sum(math.comb(discordant, index) for index in range(min(fixed, broken) + 1)) / 2**discordant
    return min(1.0, 2 * tail)
