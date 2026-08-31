from yuxi.evaluation.metrics import aggregate_pass_metrics, evaluate_task


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
