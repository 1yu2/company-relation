"""回归门禁规则：每条规则的通过、失败与不可判定边界。"""

from __future__ import annotations

import copy

import pytest

from yuxi.evaluation.regression_gate import FAILED, INFO, PASSED, UNDETERMINED, evaluate_gate

pytestmark = pytest.mark.unit

# 取自同配置重复实验 pex_55aa…c04 vs pex_456a…01 的真实对比结果。
NOISE_PAIR = {
    "dataset_version_id": "pev_65fa02f3f1d2345fd34ad5c5166d",
    "outcomes": {
        "unpaired_task_keys": [],
        "broken": [],
        "fixed": [],
        "pass_rate_delta": 0.0,
        "mcnemar_p_value": 1.0,
    },
    "performance_delta": {
        "agent_total_p50_ms": {"base": 5916.8, "candidate": 5877.7},
        "tokens_per_rollout_mean": {"base": 48811.6, "candidate": 52171.6},
    },
}


def _statuses(comparison, base_concurrency=1, candidate_concurrency=1) -> dict[str, str]:
    results = evaluate_gate(comparison, base_concurrency=base_concurrency, candidate_concurrency=candidate_concurrency)
    return {result.rule.split()[0]: result.status for result in results}


def _with(**updates) -> dict:
    comparison = copy.deepcopy(NOISE_PAIR)
    for path, value in updates.items():
        section, key = path.split("__")
        comparison[section][key] = value
    return comparison


def test_same_config_noise_pair_passes_every_rule():
    assert _statuses(NOISE_PAIR) == {"R0": PASSED, "R1": PASSED, "R2": PASSED, "R3": INFO, "R4": PASSED, "R5": PASSED}


@pytest.mark.parametrize(
    ("updates", "rule", "status"),
    [
        ({"outcomes__unpaired_task_keys": ["py-task-001"]}, "R0", FAILED),
        ({"outcomes__broken": ["py-task-001"]}, "R1", PASSED),
        ({"outcomes__broken": ["py-task-001", "py-task-002"]}, "R1", FAILED),
        ({"outcomes__pass_rate_delta": -1 / 16}, "R2", PASSED),
        ({"outcomes__pass_rate_delta": -2 / 16}, "R2", FAILED),
        ({"outcomes__pass_rate_delta": None}, "R2", UNDETERMINED),
        ({"performance_delta__agent_total_p50_ms": {"base": 6000.0, "candidate": 7800.0}}, "R4", PASSED),
        ({"performance_delta__agent_total_p50_ms": {"base": 6148.3, "candidate": 10473.0}}, "R4", FAILED),
        ({"performance_delta__agent_total_p50_ms": {"base": None, "candidate": 6000.0}}, "R4", UNDETERMINED),
        ({"performance_delta__tokens_per_rollout_mean": {"base": 50000.0, "candidate": 60000.0}}, "R5", PASSED),
        ({"performance_delta__tokens_per_rollout_mean": {"base": 50000.0, "candidate": 60500.0}}, "R5", FAILED),
    ],
)
def test_rule_boundaries(updates, rule, status):
    assert _statuses(_with(**updates))[rule] == status


def test_latency_is_undetermined_when_concurrency_differs():
    """并发本身会让 P50 翻倍（1 → 8 为 +120%），不同并发的延迟不可比，不判失败也不判通过。"""
    comparison = _with(performance_delta__agent_total_p50_ms={"base": 6000.0, "candidate": 13180.0})

    statuses = _statuses(comparison, base_concurrency=1, candidate_concurrency=8)

    assert statuses["R4"] == UNDETERMINED
    assert FAILED not in statuses.values()


def test_mcnemar_is_reported_but_never_decides():
    comparison = _with(outcomes__mcnemar_p_value=0.001)

    assert _statuses(comparison)["R3"] == INFO
