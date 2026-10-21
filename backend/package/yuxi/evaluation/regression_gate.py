"""回归门禁：比较两个已完成的实验（基线 vs 候选），按固定阈值判定候选是否可以替换基线。

阈值来源见 docs/vibe/2026-10-21-regression-gate.md（2026-10-21 用户确认），只适用于
agent-python-tasks v1（16 题）：题数不同，“1 题”对应的退化幅度也不同，换数据集需要重新推导阈值。
CI 中没有模型 API Key，不能跑真实 Agent 实验；门禁在本地对已经完成的实验执行：

    docker compose exec -T api uv run python -m yuxi.evaluation.regression_gate <基线实验 ID> <候选实验 ID>

任一规则失败时退出码为 1；“不可判定”不算失败，但会醒目输出。
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from typing import Any

from yuxi.evaluation.experiment_service import PlatformEvaluationService
from yuxi.storage.postgres.manager import pg_manager

GATE_DATASET_VERSION_ID = "pev_65fa02f3f1d2345fd34ad5c5166d"  # agent-python-tasks v1，16 题
# 同配置下单题偶发翻转确实存在（P0-6 基线中 py-task-012 3 次尝试中失败 1 次），允许 1 题，2 题及以上判失败。
MAX_BROKEN_TASKS = 1
# 与上一条一致：16 题时 1 题等于 6.25 个百分点，下降超过 1 题判失败。
MAX_PASS_RATE_DROP = 1 / 16
# 同并发的重复实验中 P50 变化在 ±6% 以内；换成 qwen 时为 +70%，应被拦下。
MAX_P50_LATENCY_INCREASE = 0.30
# 同配置重复实验中每题 token 均值变化最大为 +6.9%，留出约 3 倍余量。
MAX_TOKENS_INCREASE = 0.20

PASSED, FAILED, UNDETERMINED, INFO = "通过", "失败", "不可判定", "仅报告"


@dataclass(frozen=True)
class RuleResult:
    rule: str
    status: str
    actual: str
    threshold: str


def evaluate_gate(comparison: dict[str, Any], *, base_concurrency: int, candidate_concurrency: int) -> list[RuleResult]:
    """按 R0–R5 判定一次对比结果；comparison 为 compare_experiments 的返回值。"""
    outcomes = comparison["outcomes"]
    performance = comparison["performance_delta"]
    unpaired = outcomes["unpaired_task_keys"]
    broken = outcomes["broken"]
    delta = outcomes["pass_rate_delta"]
    p_value = outcomes["mcnemar_p_value"]
    return [
        RuleResult(
            "R0 可比性",
            FAILED if unpaired else PASSED,
            f"未配对题 {len(unpaired)}" + (f"：{', '.join(unpaired)}" if unpaired else ""),
            "全部题都能配对",
        ),
        RuleResult(
            "R1 退化题数",
            FAILED if len(broken) > MAX_BROKEN_TASKS else PASSED,
            f"{len(broken)}" + (f"：{', '.join(broken)}" if broken else ""),
            f"≤ {MAX_BROKEN_TASKS}",
        ),
        RuleResult(
            "R2 通过率下降",
            UNDETERMINED if delta is None else FAILED if delta < -MAX_PASS_RATE_DROP else PASSED,
            "无配对题" if delta is None else f"{delta * 100:+.2f} 个百分点",
            f"下降不超过 {MAX_PASS_RATE_DROP * 100:.2f} 个百分点",
        ),
        RuleResult(
            "R3 McNemar",
            INFO,
            "无配对题" if p_value is None else f"p = {p_value:.4f}",
            "只报告：16 题时即使 5 题退化、0 题修复，p 也只有 0.0625",
        ),
        _relative_rule(
            "R4 Agent 端到端 P50",
            performance["agent_total_p50_ms"],
            MAX_P50_LATENCY_INCREASE,
            undetermined=(
                f"两个实验 concurrency 不同（{base_concurrency} vs {candidate_concurrency}），延迟不可比"
                if base_concurrency != candidate_concurrency
                else None
            ),
        ),
        _relative_rule("R5 每题 token 均值", performance["tokens_per_rollout_mean"], MAX_TOKENS_INCREASE),
    ]


def _relative_rule(rule: str, values: dict[str, Any], limit: float, *, undetermined: str | None = None) -> RuleResult:
    threshold = f"增幅 ≤ {limit:.0%}"
    base, candidate = values.get("base"), values.get("candidate")
    if undetermined:
        return RuleResult(rule, UNDETERMINED, undetermined, threshold)
    if not base or candidate is None:
        return RuleResult(rule, UNDETERMINED, "基线或候选缺少数据", threshold)
    change = (candidate - base) / base
    return RuleResult(
        rule,
        FAILED if change > limit else PASSED,
        f"{base:,.1f} → {candidate:,.1f}（{change:+.1%}）",
        threshold,
    )


async def run_gate(base_experiment_id: str, candidate_experiment_id: str) -> tuple[bool, list[str]]:
    """读取两个实验并判定，返回（是否通过，输出行）。对比被拒绝（数据集或评分口径不同）时直接失败。"""
    service = PlatformEvaluationService()
    async with pg_manager.get_async_session_context() as db:
        base = await service.get_experiment(db=db, experiment_id=base_experiment_id)
        candidate = await service.get_experiment(db=db, experiment_id=candidate_experiment_id)
        if base is None or candidate is None:
            missing = base_experiment_id if base is None else candidate_experiment_id
            return False, [f"门禁失败：实验 {missing} 不存在"]
        try:
            comparison = await service.compare_experiments(
                db=db, base_experiment_id=base_experiment_id, candidate_experiment_id=candidate_experiment_id
            )
        except ValueError as exc:
            return False, [f"门禁失败：R0 可比性 —— 对比接口拒绝：{exc}"]
    if comparison["dataset_version_id"] != GATE_DATASET_VERSION_ID:
        return False, [
            f"门禁失败：阈值只适用于 agent-python-tasks v1（{GATE_DATASET_VERSION_ID}），"
            f"本次数据集版本为 {comparison['dataset_version_id']}，需要先为它推导阈值"
        ]
    results = evaluate_gate(
        comparison, base_concurrency=base["concurrency"], candidate_concurrency=candidate["concurrency"]
    )
    passed = all(result.status != FAILED for result in results)
    lines = [f"基线 {base_experiment_id} → 候选 {candidate_experiment_id}"]
    lines += [f"[{result.status}] {result.rule}：{result.actual}（阈值：{result.threshold}）" for result in results]
    if any(result.status == UNDETERMINED for result in results):
        lines.append("注意：存在“不可判定”的规则，请人工确认后再采信门禁结论。")
    lines.append(f"门禁结论：{'通过' if passed else '失败'}")
    return passed, lines


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("用法：python -m yuxi.evaluation.regression_gate <基线实验 ID> <候选实验 ID>")
    gate_passed, output = asyncio.run(run_gate(sys.argv[1], sys.argv[2]))
    print("\n".join(output))
    sys.exit(0 if gate_passed else 1)
