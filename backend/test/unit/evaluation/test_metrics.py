import pytest

from yuxi.evaluation.metrics import (
    aggregate_metric_breakdown,
    aggregate_pass_metrics,
    aggregate_rollout_performance,
    compare_paired_outcomes,
    estimate_cost,
    evaluate_task,
)


def test_aggregate_pass_metrics_distinguishes_at_k_and_power_k():
    attempts = [
        {"task_key": "fixture", "attempt": 1, "task_pass": True, "score": 1},
        {"task_key": "fixture", "attempt": 2, "task_pass": False, "score": 0},
        {"task_key": "fixture", "attempt": 3, "task_pass": False, "score": 0},
    ]

    result = aggregate_pass_metrics(attempts, k=3)

    assert result["pass@1"] == 1 / 3
    assert result["pass@k"] == 1.0
    assert result["pass^k"] == 0.0
    assert result["best@k"] == 1.0


def test_aggregate_pass_metrics_excludes_not_measured_from_denominator():
    result = aggregate_pass_metrics(
        [
            {"task_key": "measured", "attempt": 1, "task_pass": True, "status": "passed"},
            {"task_key": "unavailable", "attempt": 1, "task_pass": None, "status": "not_measured"},
        ],
        k=1,
    )

    assert result["task_count"] == 2
    assert result["measured_task_count"] == 1
    assert result["not_measured_tasks"] == 1
    assert result["pass@1"] == 1.0


def test_aggregate_pass_metrics_marks_insufficient_samples():
    result = aggregate_pass_metrics([{"task_key": "fixture", "attempt": 1, "task_pass": True}], k=3)

    assert result["pass@1"] == 1.0
    assert result["pass@k"] is None
    assert result["pass^k"] is None
    assert result["insufficient_samples"] == 1


def test_evaluate_task_checks_answer_tool_and_trace_contract():
    task = {
        "expected": {
            "final_answer": {"value": "fixture-ok: hello"},
            "tool_policy": {"required": ["fixture.echo"], "forbidden": ["filesystem.write"]},
            "trace_contract": {"required_sequence": ["agent", "tool", "final"], "required_operation": "fixture.echo"},
        }
    }
    events = [
        {"component": "agent", "operation": "start"},
        {"component": "tool", "operation": "fixture.echo"},
        {"component": "final", "operation": "answer"},
    ]

    metrics = evaluate_task(task, {"final_answer": "fixture-ok: hello"}, events)

    assert all(item["status"] == "passed" for item in metrics)


def test_evaluate_task_reports_forbidden_tool():
    task = {"expected": {"tool_policy": {"forbidden": ["filesystem.write"]}}}
    metrics = evaluate_task(task, {"final_answer": "ok"}, [{"component": "tool", "operation": "filesystem.write"}])

    tool_metric = next(item for item in metrics if item["metric_name"] == "tool_policy")
    task_metric = next(item for item in metrics if item["metric_name"] == "task_pass")
    assert tool_metric["reason_code"] == "forbidden_tool_called"
    assert task_metric["status"] == "failed"


@pytest.mark.unit
def test_rollout_performance_percentiles_and_per_item_sample_counts():
    """分位数用线性插值；只统计带 Agent 结果的 rollout，并分别给出样本量。"""
    completed = [
        {
            "status": "COMPLETED",
            "duration_ms": float(index),
            "actual_output": {
                "latency": {"total_ms": float(index), "queue_ms": 1.0, "execution_ms": float(index) - 1},
                "usage": {"llm_calls": 2, "input": 100, "output": 10, "total": 110},
                "tool_calls": [{"name": "execute"}] * (index % 3),
            },
        }
        for index in range(1, 21)
    ]
    failed = {"status": "FAILED", "duration_ms": 500.0, "actual_output": None}

    performance = aggregate_rollout_performance(completed + [failed])

    assert performance["rollout_status"] == {"COMPLETED": 20, "FAILED": 1}
    total = performance["latency_ms"]["agent_total"]
    assert (total["sample_count"], total["p50"], total["max"]) == (20, 10.5, 20.0)
    assert total["p95"] == pytest.approx(19.05)
    assert total["p99"] == pytest.approx(19.81)
    assert performance["latency_ms"]["rollout"]["sample_count"] == 21
    assert performance["usage"]["sample_count"] == 20
    assert performance["usage"]["total_sum"] == 2200
    assert performance["tool_calls"]["by_name"] == {"execute": sum(index % 3 for index in range(1, 21))}


@pytest.mark.unit
def test_small_samples_report_median_and_max_without_tail_percentiles():
    rollouts = [{"status": "COMPLETED", "duration_ms": value, "actual_output": {}} for value in (3.0, 1.0, 2.0)]

    rollout_latency = aggregate_rollout_performance(rollouts)["latency_ms"]["rollout"]

    assert rollout_latency == {"sample_count": 3, "p50": 2.0, "p95": None, "p99": None, "max": 3.0, "mean": 2.0}
    assert aggregate_rollout_performance(rollouts)["latency_ms"]["agent_total"]["sample_count"] == 0


@pytest.mark.unit
def test_metric_breakdown_pass_rate_excludes_not_measured():
    """“值正确率”与 task_pass 分开统计；not_measured 不进入通过率分母。"""
    metrics = [
        {"metric_name": "answer_value", "status": "passed", "value": 1.0, "reason_code": None},
        {"metric_name": "answer_value", "status": "passed", "value": 1.0, "reason_code": None},
        {"metric_name": "answer_value", "status": "failed", "value": 0.0, "reason_code": "answer_value_mismatch"},
        {"metric_name": "answer_value", "status": "not_measured", "value": None, "reason_code": "x"},
        {"metric_name": "duplicate_call_rate", "status": "measured", "value": 0.5, "reason_code": None},
        {"metric_name": "duplicate_call_rate", "status": "measured", "value": 0.0, "reason_code": None},
    ]

    breakdown = aggregate_metric_breakdown(metrics)

    assert breakdown["answer_value"]["pass_rate"] == pytest.approx(2 / 3)
    assert breakdown["answer_value"]["status_counts"] == {"failed": 1, "not_measured": 1, "passed": 2}
    assert breakdown["answer_value"]["reason_counts"] == {"answer_value_mismatch": 1, "x": 1}
    assert breakdown["duplicate_call_rate"]["pass_rate"] is None
    assert breakdown["duplicate_call_rate"]["measured_mean"] == 0.25


@pytest.mark.unit
@pytest.mark.parametrize(
    ("fixed", "broken", "expected_p"),
    [(0, 0, 1.0), (5, 0, 0.0625), (6, 1, 0.125), (2, 2, 1.0), (10, 0, 2 / 1024)],
)
def test_mcnemar_exact_p_matches_hand_computed_binomial(fixed, broken, expected_p):
    """p = 2 * P(X <= min(b, c))，X ~ Binomial(b + c, 0.5)，上限为 1。"""
    base = {f"f{i}": "failed" for i in range(fixed)} | {f"b{i}": "passed" for i in range(broken)}
    candidate = {f"f{i}": "passed" for i in range(fixed)} | {f"b{i}": "failed" for i in range(broken)}

    assert compare_paired_outcomes(base, candidate)["mcnemar_p_value"] == pytest.approx(expected_p)


@pytest.mark.unit
def test_paired_outcomes_exclude_unmeasured_and_missing_tasks():
    base = {"a": "passed", "b": "failed", "c": "passed", "d": "not_measured", "e": "passed"}
    candidate = {"a": "passed", "b": "passed", "c": "failed", "d": "passed"}

    outcomes = compare_paired_outcomes(base, candidate)

    assert outcomes["paired_count"] == 3
    assert (outcomes["fixed"], outcomes["broken"]) == (["b"], ["c"])
    assert (outcomes["both_pass"], outcomes["both_fail"]) == (1, 0)
    assert outcomes["unpaired_task_keys"] == ["d", "e"]
    assert outcomes["pass_rate_delta"] == 0.0


@pytest.mark.unit
def test_cost_is_only_estimated_with_supplied_pricing():
    usage = {"sample_count": 2, "input_sum": 2_000_000, "output_sum": 500_000}
    pricing = {"input_per_million": 1.0, "output_per_million": 4.0, "currency": "CNY"}

    assert estimate_cost(usage, None) is None
    assert estimate_cost(usage, pricing) == {
        "currency": "CNY",
        "input": 2.0,
        "output": 2.0,
        "total": 4.0,
        "per_rollout_mean": 2.0,
    }
