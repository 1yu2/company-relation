"""企业尽调事实、关系、路径、证据、拒答和报告严格指标。"""

from __future__ import annotations

import json
from collections.abc import Iterable

from pydantic import BaseModel, Field

from yuxi.company_due_diligence.evaluation.dataset import EvaluationSample, ReviewStatus
from yuxi.company_due_diligence.models import ResolutionStatus, build_source_id


class SampleMetricResult(BaseModel):
    """描述单样本各严格指标、幻觉数量和首错阶段。"""

    field_exact: bool | None = None
    relation_exact: bool | None = None
    path_exact: bool | None = None
    evidence_covered: bool | None = None
    refusal_correct: bool | None = None
    report_complete: bool | None = None
    freshness_correct: bool | None = None
    source_integrity: bool
    hallucination_count: int = Field(ge=0)
    predicted_item_count: int = Field(ge=0)
    strict_passed: bool
    first_error: str | None = None
    differences: dict[str, object] = Field(default_factory=dict)


class AggregateMetric(BaseModel):
    """描述一个可选布尔指标的分子、分母和比率。"""

    passed: int = Field(ge=0)
    total: int = Field(ge=0)
    rate: float | None = None


class QualityMetrics(BaseModel):
    """描述全样本与人工严格门禁指标。"""

    sample_count: int = Field(ge=0)
    human_verified_count: int = Field(ge=0)
    human_review_rate: float = Field(ge=0, le=1)
    field_accuracy: AggregateMetric
    relation_accuracy: AggregateMetric
    path_accuracy: AggregateMetric
    evidence_coverage: AggregateMetric
    refusal_accuracy: AggregateMetric
    report_completeness: AggregateMetric
    freshness_accuracy: AggregateMetric
    source_integrity: AggregateMetric
    hallucination_rate: float
    strict_pass_rate: AggregateMetric


def evaluate_sample(sample: EvaluationSample, prediction: dict) -> SampleMetricResult:
    """对问答或报告预测执行集合级严格比较。"""
    normalized = _normalize_prediction(prediction)
    expected = sample.expected
    differences = {}

    field_exact = _optional_exact(expected.facts, normalized["facts"], _fact_key, differences, "facts")
    relation_exact = _optional_exact(
        expected.relations,
        normalized["relations"],
        _relation_key,
        differences,
        "relations",
    )
    path_exact = _optional_exact(expected.paths, normalized["paths"], _path_key, differences, "paths")
    evidence_covered = None
    if expected.evidence_source_ids is not None:
        expected_sources = set(expected.evidence_source_ids)
        actual_sources = {item["source_id"] for item in normalized["evidence"]}
        evidence_covered = expected_sources <= actual_sources
        if not evidence_covered:
            differences["evidence_missing"] = sorted(expected_sources - actual_sources)

    refusal_correct = None
    if expected.refused is not None:
        refusal_correct = expected.refused == normalized["refused"]
        if not refusal_correct:
            differences["refused"] = {"expected": expected.refused, "actual": normalized["refused"]}

    report_complete = None
    if expected.required_report_sections is not None:
        actual_sections = set(normalized["report_sections"])
        required = {section.value for section in expected.required_report_sections}
        report_complete = required <= actual_sections
        if not report_complete:
            differences["report_sections_missing"] = sorted(required - actual_sections)

    freshness_correct = None
    if expected.expect_stale_declaration is not None:
        freshness_correct = expected.expect_stale_declaration == normalized["has_stale_declaration"]
        if not freshness_correct:
            differences["stale_declaration"] = {
                "expected": expected.expect_stale_declaration,
                "actual": normalized["has_stale_declaration"],
            }

    source_integrity = _source_integrity(normalized)
    if not source_integrity:
        differences["source_integrity"] = "存在未登记或哈希不一致的来源"

    hallucination_count, predicted_item_count = _hallucination_counts(sample, normalized)
    if hallucination_count:
        differences["hallucination_count"] = hallucination_count

    applicable = [
        value
        for value in (
            field_exact,
            relation_exact,
            path_exact,
            evidence_covered,
            refusal_correct,
            report_complete,
            freshness_correct,
        )
        if value is not None
    ]
    strict_passed = all(applicable) and source_integrity and hallucination_count == 0
    first_error = _first_error(
        normalized,
        field_exact,
        relation_exact,
        path_exact,
        evidence_covered,
        refusal_correct,
        report_complete,
        freshness_correct,
        source_integrity,
    )
    return SampleMetricResult(
        field_exact=field_exact,
        relation_exact=relation_exact,
        path_exact=path_exact,
        evidence_covered=evidence_covered,
        refusal_correct=refusal_correct,
        report_complete=report_complete,
        freshness_correct=freshness_correct,
        source_integrity=source_integrity,
        hallucination_count=hallucination_count,
        predicted_item_count=predicted_item_count,
        strict_passed=strict_passed,
        first_error=first_error,
        differences=differences,
    )


def aggregate_metrics(
    samples: list[EvaluationSample],
    results: list[SampleMetricResult],
) -> tuple[QualityMetrics, QualityMetrics]:
    """分别聚合全部样本和仅人工复核样本指标。"""
    all_metrics = _aggregate(samples, results)
    verified_pairs = [
        (sample, result)
        for sample, result in zip(samples, results, strict=True)
        if sample.review_status == ReviewStatus.HUMAN_VERIFIED
    ]
    strict_metrics = _aggregate(
        [item[0] for item in verified_pairs],
        [item[1] for item in verified_pairs],
    )
    return all_metrics, strict_metrics


def _aggregate(samples, results) -> QualityMetrics:
    """聚合指定样本集合。"""
    predicted = sum(result.predicted_item_count for result in results)
    hallucinations = sum(result.hallucination_count for result in results)
    verified = sum(sample.review_status == ReviewStatus.HUMAN_VERIFIED for sample in samples)
    return QualityMetrics(
        sample_count=len(samples),
        human_verified_count=verified,
        human_review_rate=verified / len(samples) if samples else 0,
        field_accuracy=_boolean_metric(result.field_exact for result in results),
        relation_accuracy=_boolean_metric(result.relation_exact for result in results),
        path_accuracy=_boolean_metric(result.path_exact for result in results),
        evidence_coverage=_boolean_metric(result.evidence_covered for result in results),
        refusal_accuracy=_boolean_metric(result.refusal_correct for result in results),
        report_completeness=_boolean_metric(result.report_complete for result in results),
        freshness_accuracy=_boolean_metric(result.freshness_correct for result in results),
        source_integrity=_boolean_metric(result.source_integrity for result in results),
        hallucination_rate=hallucinations / predicted if predicted else 0,
        strict_pass_rate=_boolean_metric(result.strict_passed for result in results),
    )


def _boolean_metric(values: Iterable[bool | None]) -> AggregateMetric:
    """忽略不适用项并计算布尔比率。"""
    applicable = [value for value in values if value is not None]
    passed = sum(applicable)
    return AggregateMetric(passed=passed, total=len(applicable), rate=passed / len(applicable) if applicable else None)


def _normalize_prediction(prediction: dict) -> dict:
    """把问答或报告预测展开为统一集合。"""
    if "sections" in prediction and "report_id" in prediction:
        query_results = [
            section.get("query_result")
            for section in prediction.get("sections", {}).values()
            if section.get("query_result")
        ]
        facts = [item for result in query_results for item in result.get("facts", [])]
        relations = [item for result in query_results for item in result.get("relations", [])]
        paths = [item for result in query_results for item in result.get("paths", [])]
        evidence = [item for result in query_results for item in result.get("evidence", [])]
        sources = [item for result in query_results for item in result.get("sources", [])]
        data_quality = prediction.get("data_quality", {})
        return {
            "facts": facts,
            "relations": relations,
            "paths": paths,
            "evidence": evidence,
            "sources": sources,
            "refused": prediction.get("status") == "failed",
            "report_sections": [
                name
                for name, section in prediction.get("sections", {}).items()
                if section.get("status") not in {"failed", "cancelled"}
            ],
            "has_stale_declaration": bool(data_quality.get("stale_fields")),
            "resolution_status": None,
        }
    data_quality = prediction.get("data_quality", {})
    return {
        "facts": prediction.get("facts", []),
        "relations": prediction.get("relations", []),
        "paths": prediction.get("paths", []),
        "evidence": prediction.get("evidence", []),
        "sources": prediction.get("sources", []),
        "refused": prediction.get("resolution_status") != ResolutionStatus.RESOLVED.value,
        "report_sections": [],
        "has_stale_declaration": bool(data_quality.get("stale_fields")),
        "resolution_status": prediction.get("resolution_status"),
    }


def _optional_exact(expected, actual, key_builder, differences, name: str) -> bool | None:
    """对可选 Golden 集合执行去重、顺序无关严格比较。"""
    if expected is None:
        return None
    expected_keys = {key_builder(item.model_dump(mode="json")) for item in expected}
    actual_keys = {key_builder(item) for item in actual}
    if expected_keys == actual_keys:
        return True
    differences[name] = {
        "missing": sorted(expected_keys - actual_keys),
        "unexpected": sorted(actual_keys - expected_keys),
    }
    return False


def _fact_key(item: dict) -> str:
    """生成忽略展示文案、保留规范化值/单位/日期的事实键。"""
    return json.dumps(
        [
            item.get("subject_id"),
            item.get("fact_type"),
            item.get("normalized_value"),
            item.get("unit"),
            item.get("effective_date"),
        ],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _relation_key(item: dict) -> str:
    """生成保留方向的关系键。"""
    return "|".join(
        [str(item.get("source_entity_id")), str(item.get("relation_type")), str(item.get("target_entity_id"))]
    )


def _path_key(item: dict) -> str:
    """生成包含节点顺序、边方向、类型和跳数的路径键。"""
    nodes = item.get("node_ids") or [node.get("entity_id") for node in item.get("nodes", [])]
    edges = item.get("edges", [])
    edge_keys = [
        [edge.get("source_entity_id"), edge.get("relation_type"), edge.get("target_entity_id")] for edge in edges
    ]
    return json.dumps([nodes, edge_keys, item.get("hop_count")], ensure_ascii=False, separators=(",", ":"))


def _source_integrity(normalized: dict) -> bool:
    """检查所有引用来源已登记且稳定哈希一致。"""
    sources = {source.get("source_id"): source for source in normalized["sources"]}
    for source_id, source in sources.items():
        if source_id != build_source_id(str(source.get("table")), str(source.get("record_key"))):
            return False
    referenced = {fact.get("source_id") for fact in normalized["facts"]}
    referenced.update(source_id for relation in normalized["relations"] for source_id in relation.get("source_ids", []))
    referenced.update(source_id for path in normalized["paths"] for source_id in path.get("source_ids", []))
    referenced.update(item.get("source_id") for item in normalized["evidence"])
    referenced.discard(None)
    return referenced <= set(sources)


def _hallucination_counts(sample: EvaluationSample, normalized: dict) -> tuple[int, int]:
    """统计预测中不在已声明 Golden 集合的企业事实。"""
    pairs = (
        (sample.expected.facts, normalized["facts"], _fact_key),
        (sample.expected.relations, normalized["relations"], _relation_key),
        (sample.expected.paths, normalized["paths"], _path_key),
    )
    hallucinations = 0
    predicted = 0
    for expected, actual, key_builder in pairs:
        if expected is None:
            continue
        expected_keys = {key_builder(item.model_dump(mode="json")) for item in expected}
        actual_keys = {key_builder(item) for item in actual}
        hallucinations += len(actual_keys - expected_keys)
        predicted += len(actual_keys)
    return hallucinations, predicted


def _first_error(
    normalized,
    field_exact,
    relation_exact,
    path_exact,
    evidence_covered,
    refusal_correct,
    report_complete,
    freshness_correct,
    source_integrity,
) -> str | None:
    """按主链路顺序返回首个错误归因。"""
    if normalized["resolution_status"] not in {None, ResolutionStatus.RESOLVED.value} and refusal_correct is not True:
        return "entity_resolution"
    if field_exact is False:
        return "mysql_mapping"
    if relation_exact is False or path_exact is False:
        return "graph_projection"
    if evidence_covered is False:
        return "text_retrieval"
    if not source_integrity:
        return "source_missing"
    if freshness_correct is False:
        return "freshness"
    if refusal_correct is False:
        return "refusal"
    if report_complete is False:
        return "report_orchestration"
    return None
