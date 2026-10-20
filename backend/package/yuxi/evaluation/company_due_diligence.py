"""企业尽调业务质量的确定性评估器；来源映射检查不等同于语义证据支持。"""

from __future__ import annotations

import math
from typing import Any

METRIC_VERSION = "company-due-diligence-deterministic-v1"


def evaluate_company_due_diligence(task: dict[str, Any], actual: dict[str, Any]) -> list[dict[str, Any]]:
    """比较已复核的身份、路由、事实值和拒答，并单独记录延迟。"""
    expected = task.get("expected") or {}
    names = ("entity_resolution", "routing", "fact_correctness", "source_alignment", "refusal_correctness")
    latency = (actual.get("latency") or {}).get("total_ms")
    valid_latency = (
        isinstance(latency, (int, float)) and not isinstance(latency, bool) and math.isfinite(latency) and latency >= 0
    )
    metrics = [
        _metric(
            "request_latency",
            float(latency) if valid_latency else None,
            "measured" if valid_latency else "not_measured",
            None if valid_latency else "latency_missing",
        )
    ]
    expected_status = expected.get("status")
    if not expected_status or expected_status == "pending_review":
        return (
            [_metric(name, None, "not_measured", "expected_reference_missing") for name in names]
            + metrics
            + [_metric("task_pass", None, "not_measured", "expected_reference_missing")]
        )

    actual_status = actual.get("resolution_status") or actual.get("status")
    if actual_status == "source_unavailable" and expected.get("status") != "source_unavailable":
        return (
            [_metric(name, None, "unavailable", "source_unavailable") for name in names]
            + metrics
            + [_metric("task_pass", None, "unavailable", "source_unavailable")]
        )

    expected_status = expected.get("status")
    identity = expected.get("company")
    if identity is None and (task.get("input") or {}).get("topic") == "entity_resolution":
        identity = next((item for item in expected.get("facts", []) if item.get("eid")), None)
    identity_pass = bool(expected_status) and actual_status == expected_status
    if identity:
        identity_pass = identity_pass and all(
            (actual.get("company") or {}).get(key) == value
            for key, value in identity.items()
            if key in {"eid", "name", "credit_code", "stock_code"}
        )
    quality = [
        _metric(
            "entity_resolution",
            float(identity_pass) if expected_status else None,
            "passed" if identity_pass else "failed" if expected_status else "not_measured",
            None if identity_pass else "identity_mismatch" if expected_status else "expected_status_missing",
        )
    ]

    routing = expected.get("question_type")
    quality.append(
        _metric(
            "routing",
            float(routing == actual.get("question_type")) if routing else None,
            "passed" if routing and routing == actual.get("question_type") else "failed" if routing else "not_measured",
            None
            if routing and routing == actual.get("question_type")
            else "routing_mismatch"
            if routing
            else "expected_routing_missing",
        )
    )

    expected_facts = [item for item in expected.get("facts", []) if "fact_id" in item]
    actual_facts = actual.get("facts") or []
    if expected_facts:
        # 同一个 fact_id 的错误数值、单位、业务日期或来源也必须失败。
        fields = ("fact_id", "subject_id", "fact_type", "normalized_value", "unit", "effective_date", "source_id")
        actual_by_id = {item.get("fact_id"): item for item in actual_facts}
        passed = len(actual_by_id) == len(actual_facts) == len(expected_facts) and all(
            "normalized_value" in item
            and all(actual_by_id.get(item["fact_id"], {}).get(key) == item[key] for key in fields if key in item)
            for item in expected_facts
        )
        quality.append(
            _metric(
                "fact_correctness",
                float(passed),
                "passed" if passed else "failed",
                None if passed else "fact_value_mismatch",
            )
        )
    else:
        quality.append(_metric("fact_correctness", None, "not_measured", "expected_facts_missing"))

    expected_sources = {item["source_id"] for item in expected.get("source_refs", []) if item.get("source_id")}
    actual_sources = {item["source_id"] for item in actual.get("sources", []) if item.get("source_id")}
    claim_sources = {item.get("source_id") for item in actual_facts + (actual.get("evidence") or [])}
    if expected_sources:
        passed = expected_sources <= actual_sources and claim_sources <= actual_sources
        quality.append(
            _metric(
                "source_alignment",
                float(passed),
                "passed" if passed else "failed",
                None if passed else "claim_source_missing",
            )
        )
    else:
        quality.append(_metric("source_alignment", None, "not_measured", "expected_sources_missing"))

    refused = expected_status in {"not_found", "ambiguous", "source_unavailable", "refused"}
    if refused:
        passed = actual_status == expected_status and not any(
            actual.get(key) for key in ("facts", "relations", "paths", "evidence")
        )
        quality.append(
            _metric(
                "refusal_correctness",
                float(passed),
                "passed" if passed else "failed",
                None if passed else "refusal_output_mismatch",
            )
        )
    else:
        quality.append(_metric("refusal_correctness", None, "not_measured", "refusal_not_applicable"))

    failed = next((item for item in quality if item["status"] == "failed"), None)
    measured = any(item["status"] == "passed" for item in quality)
    task_status = "failed" if failed else "passed" if measured else "not_measured"
    return (
        quality
        + metrics
        + [
            _metric(
                "task_pass",
                0.0 if failed else 1.0 if measured else None,
                task_status,
                failed["reason_code"] if failed else None if measured else "all_domain_metrics_not_measured",
            )
        ]
    )


def _metric(name: str, value: float | None, status: str, reason_code: str | None) -> dict[str, Any]:
    """创建带版本与失败原因的指标记录。"""
    return {
        "metric_name": name,
        "metric_version": METRIC_VERSION,
        "value": value,
        "status": status,
        "reason_code": reason_code,
    }
