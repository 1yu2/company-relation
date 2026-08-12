"""企业尽调质量评测 JSON、JSONL 和 Markdown 导出。"""

from __future__ import annotations

import hashlib
import json

from yuxi.company_due_diligence.evaluation.runner import QualityEvaluationReport


def render_quality_json(report: QualityEvaluationReport) -> bytes:
    """渲染不包含 artifact 自引用的稳定 JSON。"""
    payload = report.model_dump(mode="json", exclude={"artifacts"})
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode()


def render_quality_jsonl(report: QualityEvaluationReport) -> bytes:
    """逐样本输出 JSONL 便于差异审阅。"""
    lines = []
    for sample in report.samples:
        lines.append(json.dumps(sample.model_dump(mode="json"), ensure_ascii=False, sort_keys=True))
    return ("\n".join(lines) + "\n").encode()


def render_quality_markdown(report: QualityEvaluationReport) -> bytes:
    """渲染数据集、指标、门禁和失败样本摘要。"""
    strict = report.strict_metrics
    lines = [
        "# 企业尽调质量评测报告",
        "",
        f"- 运行 ID：`{report.run_id}`",
        f"- 数据集：`{report.dataset_id}` / `{report.dataset_version}`",
        f"- 数据集类别：`{report.dataset_source_kind}`",
        f"- checksum：`{report.dataset_checksum}`",
        f"- 全局分母：{report.denominator}",
        f"- 抽样分母：{report.sampled_denominator}",
        f"- 抽样策略：`{report.sampling_policy}`",
        f"- 人工复核样本：{strict.human_verified_count}",
        f"- 严格门禁：{'通过' if report.regression_gate.passed else '未通过'}",
        "",
        "## 严格指标",
        "",
        f"- 字段准确率：{_rate(strict.field_accuracy.rate)}",
        f"- 关系准确率：{_rate(strict.relation_accuracy.rate)}",
        f"- 路径准确率：{_rate(strict.path_accuracy.rate)}",
        f"- 证据覆盖率：{_rate(strict.evidence_coverage.rate)}",
        f"- 拒答准确率：{_rate(strict.refusal_accuracy.rate)}",
        f"- 报告完整率：{_rate(strict.report_completeness.rate)}",
        f"- 时效声明准确率：{_rate(strict.freshness_accuracy.rate)}",
        f"- 企业解析准确率：{_rate(strict.entity_resolution_accuracy.rate)}",
        f"- 意图主题准确率：{_rate(strict.topic_accuracy.rate)}",
        f"- 时间范围准确率：{_rate(strict.time_scope_accuracy.rate)}",
        f"- 展示形式匹配率：{_rate(strict.presentation_accuracy.rate)}",
        f"- 交付方式匹配率：{_rate(strict.delivery_accuracy.rate)}",
        f"- 必要工具召回率：{_rate(strict.tool_recall.rate)}",
        f"- 无关工具规避率：{_rate(strict.forbidden_tool_avoidance.rate)}",
        f"- Claim Ledger 一致率：{_rate(strict.claim_ledger_consistency.rate)}",
        f"- 主题覆盖率：{_rate(strict.topic_coverage.rate)}",
        f"- 无关主题规避率：{_rate(strict.irrelevant_topic_avoidance.rate)}",
        f"- 幻觉率：{strict.hallucination_rate:.2%}",
        "",
    ]
    if report.catalog_coverage:
        lines.extend(["## 数据目录覆盖", ""])
        lines.extend(f"- {key}：{value}" for key, value in sorted(report.catalog_coverage.items()))
        lines.append("")
    lines.extend(["## 门禁说明", ""])
    lines.extend(f"- {reason}" for reason in report.regression_gate.reasons)
    failures = [sample for sample in report.samples if _is_failure(sample)]
    lines.extend(["", "## 失败与待复核样本", ""])
    if not failures:
        lines.append("- 无")
    for sample in failures:
        first_error = sample.error or (sample.metrics.first_error if sample.metrics else "unknown")
        lines.append(f"- `{sample.sample_id}`：{first_error}")
    lines.append("")
    return "\n".join(lines).encode()


def checksum(content: bytes) -> str:
    """返回导出文件 SHA256。"""
    return hashlib.sha256(content).hexdigest()


def _rate(value: float | None) -> str:
    """格式化可能不适用的指标比率。"""
    return "不适用" if value is None else f"{value:.2%}"


def _is_failure(sample) -> bool:
    """判断逐题结果是否需要进入失败审阅列表。"""
    return bool(sample.error or not sample.metrics or not sample.metrics.strict_passed)
