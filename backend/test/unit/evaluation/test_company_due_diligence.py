"""企业尽调评估器的事实值、拒答和未测门禁回归。"""

import pytest

from yuxi.evaluation.company_due_diligence import evaluate_company_due_diligence


def _task():
    """提供与真实 DTO 同构的人工预期。"""
    return {
        "expected": {
            "status": "resolved",
            "review_status": "reviewed",
            "company": {"eid": "eid-1"},
            "facts": [{"fact_id": "capital", "normalized_value": 100, "unit": "万元", "source_id": "source-1"}],
            "source_refs": [{"source_id": "source-1"}],
        }
    }


def _actual():
    """提供正确企业响应。"""
    return {
        "resolution_status": "resolved",
        "company": {"eid": "eid-1"},
        "facts": [{"fact_id": "capital", "normalized_value": 100, "unit": "万元", "source_id": "source-1"}],
        "sources": [{"source_id": "source-1"}],
        "latency": {"total_ms": 12.5},
    }


def test_missing_reference_is_not_measured():
    metrics = {
        item["metric_name"]: item
        for item in evaluate_company_due_diligence(
            {"expected": {"status": "pending_review", "review_status": "pending_review", "facts": []}},
            _actual(),
        )
    }
    assert metrics["task_pass"]["value"] is None
    assert metrics["task_pass"]["reason_code"] == "expected_reference_missing"
    assert metrics["request_latency"]["value"] == 12.5


@pytest.mark.parametrize("change", ["value", "unit", "source", "company"])
def test_same_fact_id_with_wrong_value_unit_source_or_company_fails(change):
    actual = _actual()
    if change == "value":
        actual["facts"][0]["normalized_value"] = 999
    elif change == "unit":
        actual["facts"][0]["unit"] = "元"
    elif change == "source":
        actual["facts"][0]["source_id"] = "missing"
    else:
        actual["company"]["eid"] = "wrong-company"
    metrics = {item["metric_name"]: item for item in evaluate_company_due_diligence(_task(), actual)}
    assert metrics["task_pass"]["status"] == "failed"


def test_reviewed_correct_facts_pass_without_treating_latency_as_quality():
    actual = _actual()
    del actual["latency"]
    metrics = {item["metric_name"]: item for item in evaluate_company_due_diligence(_task(), actual)}
    assert metrics["fact_correctness"]["status"] == "passed"
    assert metrics["source_alignment"]["status"] == "passed"
    assert metrics["request_latency"]["status"] == "not_measured"
    assert metrics["task_pass"]["status"] == "passed"


def test_refusal_with_leaked_facts_fails():
    task = {"expected": {"review_status": "reviewed", "status": "not_found"}}
    actual = {"resolution_status": "not_found", "facts": [{"fact_id": "invented"}]}
    metrics = {item["metric_name"]: item for item in evaluate_company_due_diligence(task, actual)}
    assert metrics["refusal_correctness"]["status"] == "failed"


def test_unexpected_source_outage_is_unavailable_not_quality_failure():
    metrics = {
        item["metric_name"]: item
        for item in evaluate_company_due_diligence(_task(), {"resolution_status": "source_unavailable"})
    }
    assert metrics["task_pass"]["status"] == "unavailable"
    assert metrics["task_pass"]["value"] is None


def test_extra_sourced_fact_passes_but_unsourced_or_missing_fact_fails():
    """v2：多返回且有来源的事实不算错；多返回但来源不在响应中、或缺少期望事实，都判失败。"""
    extra = {"fact_id": "paid-capital", "normalized_value": 50, "unit": "万元", "source_id": "source-1"}
    with_extra = _actual()
    with_extra["facts"].append(extra)
    unsourced = _actual()
    unsourced["facts"].append(extra | {"source_id": "not-in-response"})
    missing = _actual()
    missing["facts"] = [extra]

    def fact_metric(actual):
        return {item["metric_name"]: item for item in evaluate_company_due_diligence(_task(), actual)}[
            "fact_correctness"
        ]

    assert fact_metric(with_extra)["status"] == "passed"
    assert (fact_metric(unsourced)["status"], fact_metric(unsourced)["reason_code"]) == (
        "failed",
        "unsourced_extra_fact",
    )
    assert (fact_metric(missing)["status"], fact_metric(missing)["reason_code"]) == ("failed", "fact_value_mismatch")
