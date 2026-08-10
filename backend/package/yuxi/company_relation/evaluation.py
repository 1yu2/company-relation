from __future__ import annotations

import asyncio
import hashlib
import json
import math
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from typing import Any

from yuxi.company_relation.paths import get_default_questions_path, get_default_results_dir
from yuxi.company_relation.query_service import CompanyRelationQueryResult, CompanyRelationQueryService


DEFAULT_EVALUATION_STRATEGIES = ("vector_rag", "graph_rag", "cypher_direct")


class CompanyRelationEvaluationService:
    """运行企业关系冻结题库并生成严格对照评测报告。"""

    def __init__(self, query_service: CompanyRelationQueryService):
        self.query_service = query_service

    async def run(
        self,
        *,
        questions_path: str | Path | None = None,
        strategies: tuple[str, ...] | list[str] = DEFAULT_EVALUATION_STRATEGIES,
        output_dir: str | Path | None = None,
        write_artifacts: bool = True,
    ) -> dict[str, Any]:
        """执行输入、查询、评分、归因和报告落盘的完整评测链路。"""
        question_file = Path(questions_path or get_default_questions_path())
        questions = load_golden_questions(question_file)
        question_checksum = file_checksum(question_file)
        snapshot = self.query_service.snapshot
        validation = snapshot.validate()
        if not validation.valid:
            raise ValueError(f"企业关系快照校验失败: {validation.to_dict()}")

        started_at = datetime.now(UTC)
        item_results = []
        for strategy in strategies:
            for question in questions:
                output = await self.query_service.query(question["query"], strategy=strategy)
                score = score_evaluation_item(question, output)
                item_results.append(
                    {
                        "strategy": strategy,
                        "question_id": question["question_id"],
                        "category": question["category"],
                        "query": question["query"],
                        "expected": question["expected"],
                        "output": output.to_dict(),
                        "score": score,
                    }
                )

        strategy_summaries = {
            strategy: aggregate_strategy_metrics(
                [item for item in item_results if item["strategy"] == strategy],
                total_questions=len(questions),
            )
            for strategy in strategies
        }
        paired_comparison = _paired_comparison(item_results, strategies)
        concurrency_probe = await self._run_concurrency_probe(questions, strategies)
        review_summary = _review_summary(questions)
        completed_at = datetime.now(UTC)
        report = {
            "run": {
                "run_id": _build_run_id(started_at, snapshot.checksum(), question_checksum),
                "started_at": started_at.isoformat(),
                "completed_at": completed_at.isoformat(),
                "duration_seconds": round((completed_at - started_at).total_seconds(), 3),
                "strategies": list(strategies),
                "question_count": len(questions),
            },
            "inputs": {
                "dataset_id": snapshot.dataset_id,
                "dataset_name": snapshot.name,
                "as_of_date": snapshot.as_of_date,
                "snapshot_checksum": snapshot.checksum(),
                "question_checksum": question_checksum,
                "gold_standard_method": "source-derived frozen answers; manual review status is recorded per item",
                "review_summary": review_summary,
                "source_summary": _source_summary(snapshot.sources),
                "strategy_implementation": {
                    "vector_rag": (
                        "TF-IDF sparse-vector retrieval over frozen evidence with deterministic answer templates"
                    ),
                    "graph_rag": (
                        "deterministic in-memory graph traversal with evidence answer templates; "
                        "no online LLM inference"
                    ),
                    "cypher_direct": "parameterized Neo4j queries with deterministic answer templates",
                },
            },
            "graph_quality": {
                **validation.to_dict(),
                "quality_scope": "结构一致性与来源覆盖；公告实体/关系抽取 P/R/F1 尚未完成人工金标准标注",
            },
            "strategy_summaries": strategy_summaries,
            "system_metrics": {
                "interface_success_rate": 1.0,
                "concurrency_10": concurrency_probe,
                "incremental_visibility_latency": "not_measured",
            },
            "measurement_limitations": [
                "Golden answers are source-derived and manual review coverage is currently 0%",
                "announcement PDF entity and relation extraction precision/recall/F1 are not measured",
                "incremental announcement-to-graph visibility latency is not measured",
                "online LLM generation quality and latency are outside this deterministic baseline",
            ],
            "paired_comparison": paired_comparison,
            "regression_gate": _build_regression_gate(
                strategy_summaries,
                paired_comparison=paired_comparison,
                graph_quality_passed=validation.valid,
            ),
            "items": item_results,
        }

        if write_artifacts:
            artifact_paths = write_evaluation_artifacts(report, output_dir or get_default_results_dir())
            report["artifacts"] = artifact_paths
        return report

    async def _run_concurrency_probe(self, questions: list[dict[str, Any]], strategies) -> dict[str, Any]:
        """以同一条多跳问题对每种策略执行 10 并发错误率探针。"""
        probe_question = next(question["query"] for question in questions if question["category"] == "multi_hop")
        results = {}
        for strategy in strategies:
            async def run_once() -> float:
                started_at = time.perf_counter()
                if strategy == "vector_rag":
                    await asyncio.to_thread(self.query_service._query_vector, probe_question, top_k=10)
                elif strategy == "graph_rag":
                    await asyncio.to_thread(self.query_service._query_graph, probe_question)
                else:
                    await self.query_service.query(probe_question, strategy=strategy)
                return (time.perf_counter() - started_at) * 1000

            wall_started_at = time.perf_counter()
            probe_results = await asyncio.gather(*(run_once() for _ in range(10)), return_exceptions=True)
            wall_seconds = time.perf_counter() - wall_started_at
            latencies = [result for result in probe_results if isinstance(result, float)]
            error_count = len(probe_results) - len(latencies)
            results[strategy] = {
                "request_count": len(probe_results),
                "error_count": error_count,
                "error_rate": round(error_count / len(probe_results), 6),
                "p50_ms": round(median(latencies), 3) if latencies else None,
                "p95_ms": round(_percentile(latencies, 0.95), 3) if latencies else None,
                "wall_time_ms": round(wall_seconds * 1000, 3),
                "requests_per_second": round(len(latencies) / wall_seconds, 3) if wall_seconds else None,
            }
        return results


def load_golden_questions(path: str | Path) -> list[dict[str, Any]]:
    """加载并校验 JSONL Golden 题库。"""
    questions = []
    question_ids = set()
    with Path(path).open(encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            item = json.loads(line)
            required_fields = {"question_id", "category", "query", "expected"}
            missing_fields = required_fields - item.keys()
            if missing_fields:
                raise ValueError(f"Golden 题库第 {line_number} 行缺少字段: {sorted(missing_fields)}")
            if item["question_id"] in question_ids:
                raise ValueError(f"Golden 题库存在重复 question_id: {item['question_id']}")
            question_ids.add(item["question_id"])
            questions.append(item)
    if not questions:
        raise ValueError("Golden 题库为空")
    return questions


def file_checksum(path: str | Path) -> str:
    """计算文件原始字节的 SHA-256。"""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def score_evaluation_item(question: dict[str, Any], output: CompanyRelationQueryResult) -> dict[str, Any]:
    """按实体、事实和路径完全一致的严格口径评分。"""
    expected = question["expected"]
    expected_refused = bool(expected.get("refused"))
    expected_entities = sorted(expected.get("answer_entity_ids") or [])
    expected_facts = sorted(expected.get("fact_ids") or [])
    expected_paths = _normalize_paths(expected.get("paths") or [])
    expected_evidence = list(expected.get("evidence_ids") or expected_facts)

    actual_entities = sorted(output.answer_entity_ids)
    actual_facts = sorted(output.fact_ids)
    actual_paths = _normalize_paths(output.paths)
    if expected_refused:
        exact_match = output.refused and not actual_entities and not actual_facts and not actual_paths
    else:
        exact_match = (
            not output.refused
            and expected_entities == actual_entities
            and expected_facts == actual_facts
            and expected_paths == actual_paths
        )

    retrieved = output.retrieved_evidence_ids[:10]
    matched_evidence = set(expected_evidence) & set(retrieved)
    recall_at_10 = len(matched_evidence) / len(set(expected_evidence)) if expected_evidence else 1.0
    reciprocal_rank = 0.0
    for index, evidence_id in enumerate(retrieved, start=1):
        if evidence_id in expected_evidence:
            reciprocal_rank = 1 / index
            break

    evidence_alignment = 1.0
    if output.fact_ids:
        evidence_alignment = len(set(output.fact_ids) & set(output.retrieved_evidence_ids)) / len(set(output.fact_ids))
    error_type = _classify_error(
        exact_match=exact_match,
        expected_refused=expected_refused,
        output=output,
        recall_at_10=recall_at_10,
    )
    return {
        "exact_match": exact_match,
        "retrieval_applicable": bool(expected_evidence),
        "recall_at_10": round(recall_at_10, 6),
        "reciprocal_rank": round(reciprocal_rank, 6),
        "evidence_alignment": round(evidence_alignment, 6),
        "hallucination": expected_refused and not output.refused,
        "error_type": error_type,
    }


def aggregate_strategy_metrics(items: list[dict[str, Any]], *, total_questions: int) -> dict[str, Any]:
    """汇总单个策略的准确率、检索、证据、性能和错误归因。"""
    if len(items) != total_questions:
        raise ValueError(f"策略评测条数不完整: {len(items)}/{total_questions}")
    category_items: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        category_items[item["category"]].append(item)

    exact_matches = [float(item["score"]["exact_match"]) for item in items]
    retrieval_items = [item for item in items if item["score"]["retrieval_applicable"]]
    recalls = [item["score"]["recall_at_10"] for item in retrieval_items]
    reciprocal_ranks = [item["score"]["reciprocal_rank"] for item in retrieval_items]
    evidence_alignments = [item["score"]["evidence_alignment"] for item in items]
    latencies = [item["output"]["latency_ms"] for item in items]
    refusal_items = category_items.get("refusal", [])
    hallucinations = sum(bool(item["score"]["hallucination"]) for item in refusal_items)
    error_counts = Counter(item["score"]["error_type"] for item in items if item["score"]["error_type"])

    return {
        "question_count": len(items),
        "exact_accuracy": round(sum(exact_matches) / len(items), 6),
        "overall_score": round(sum(exact_matches) / len(items), 6),
        "category_accuracy": {
            category: round(
                sum(float(item["score"]["exact_match"]) for item in values) / len(values),
                6,
            )
            for category, values in sorted(category_items.items())
        },
        "multi_hop_accuracy": _category_accuracy(category_items.get("multi_hop", [])),
        "refusal_accuracy": _category_accuracy(refusal_items),
        "hallucination_rate": round(hallucinations / len(refusal_items), 6) if refusal_items else 0.0,
        "recall_at_10": round(sum(recalls) / len(recalls), 6),
        "mrr": round(sum(reciprocal_ranks) / len(reciprocal_ranks), 6),
        "evidence_alignment_rate": round(sum(evidence_alignments) / len(evidence_alignments), 6),
        "latency_ms": {
            "p50": round(median(latencies), 3),
            "p95": round(_percentile(latencies, 0.95), 3),
            "max": round(max(latencies), 3),
        },
        "category_latency_ms": {
            category: {
                "p50": round(median([item["output"]["latency_ms"] for item in values]), 3),
                "p95": round(_percentile([item["output"]["latency_ms"] for item in values], 0.95), 3),
            }
            for category, values in sorted(category_items.items())
        },
        "error_attribution": dict(sorted(error_counts.items())),
    }


def write_evaluation_artifacts(report: dict[str, Any], output_dir: str | Path) -> dict[str, str]:
    """写入完整 JSON、逐题 JSONL 和便于阅读的 Markdown 报告。"""
    results_dir = Path(output_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    run_id = report["run"]["run_id"]
    json_path = results_dir / f"{run_id}.json"
    jsonl_path = results_dir / f"{run_id}-items.jsonl"
    markdown_path = results_dir / f"{run_id}.md"
    artifact_paths = {
        "json": str(json_path),
        "jsonl": str(jsonl_path),
        "markdown": str(markdown_path),
    }
    report["artifacts"] = artifact_paths

    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    with jsonl_path.open("w", encoding="utf-8") as file:
        for item in report["items"]:
            file.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")
    markdown_path.write_text(render_markdown_report(report), encoding="utf-8")
    return artifact_paths


def render_markdown_report(report: dict[str, Any]) -> str:
    """渲染企业关系整体评测报告。"""
    lines = [
        "# 企业股权关联图谱整体评测报告",
        "",
        f"- 运行 ID：`{report['run']['run_id']}`",
        f"- 数据集：`{report['inputs']['dataset_id']}`（截至 {report['inputs']['as_of_date']}）",
        f"- 题目数：{report['run']['question_count']}",
        f"- 快照 checksum：`{report['inputs']['snapshot_checksum']}`",
        f"- 题库 checksum：`{report['inputs']['question_checksum']}`",
        f"- 人工复核覆盖率：{report['inputs']['review_summary']['manual_review_coverage']:.2%}",
        "",
        "## 数据质量",
        "",
        f"- 节点数：{report['graph_quality']['node_count']}",
        f"- 边数：{report['graph_quality']['edge_count']}",
        f"- 来源覆盖率：{report['graph_quality']['source_coverage']:.2%}",
        f"- 结构校验：{'通过' if report['graph_quality']['valid'] else '失败'}",
        "",
        "## 策略对比",
        "",
        "| 策略 | 严格准确率 | 多跳准确率 | 拒答准确率 | 幻觉率 | Recall@10 | MRR | 证据对齐率 | P95(ms) |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for strategy, metrics in report["strategy_summaries"].items():
        lines.append(
            f"| {strategy} | {metrics['exact_accuracy']:.2%} | {metrics['multi_hop_accuracy']:.2%} | "
            f"{metrics['refusal_accuracy']:.2%} | {metrics['hallucination_rate']:.2%} | "
            f"{metrics['recall_at_10']:.3f} | {metrics['mrr']:.3f} | "
            f"{metrics['evidence_alignment_rate']:.2%} | {metrics['latency_ms']['p95']:.3f} |"
        )

    lines.extend(
        [
            "",
            "## 分类准确率",
            "",
            "| 策略 | 属性 | 单跳 | 多跳 | 拒答 |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for strategy, metrics in report["strategy_summaries"].items():
        category = metrics["category_accuracy"]
        lines.append(
            f"| {strategy} | {category.get('attribute', 0.0):.2%} | {category.get('single_hop', 0.0):.2%} | "
            f"{category.get('multi_hop', 0.0):.2%} | {category.get('refusal', 0.0):.2%} |"
        )

    lines.extend(["", "## 错误归因", ""])
    for strategy, metrics in report["strategy_summaries"].items():
        attribution = metrics["error_attribution"]
        summary = "、".join(f"{error_type}={count}" for error_type, count in attribution.items()) or "无"
        lines.append(f"- {strategy}：{summary}")

    paired = report["paired_comparison"]
    lines.extend(
        [
            "",
            "## 配对比较",
            "",
            f"- GraphRAG 相对向量基线净改善：{paired['net_improvement']} 题",
            f"- GraphRAG 新增失败：{len(paired['graph_rag_regressed_question_ids'])} 题",
        ]
    )

    lines.extend(["", "## 数据来源", ""])
    for source in report["inputs"]["source_summary"]:
        lines.append(
            f"- {source['provider']}：{source['record_count']} 条来源记录，接口 `{source['interfaces']}`，"
            f"数据日期 {source['data_dates']}"
        )
    lines.extend(["", "## 10 并发探针", ""])
    for strategy, metrics in report["system_metrics"]["concurrency_10"].items():
        lines.append(
            f"- {strategy}：错误率 {metrics['error_rate']:.2%}，P95 {metrics['p95_ms']} ms，"
            f"吞吐 {metrics['requests_per_second']} req/s"
        )
    lines.extend(
        [
            "",
            "## 回归门禁",
            "",
            f"- 综合分不下降：{'通过' if report['regression_gate']['score_not_decreased'] else '失败'}",
            f"- GraphRAG 幻觉率为 0：{'通过' if report['regression_gate']['zero_hallucination'] else '失败'}",
            f"- 图谱数据校验：{'通过' if report['regression_gate']['graph_quality_passed'] else '失败'}",
            f"- 总门禁：{'通过' if report['regression_gate']['passed'] else '失败'}",
            "",
            "## 口径说明",
            "",
            "严格准确率要求答案实体、事实 ID 和关系路径全部与冻结金标准一致。"
            "当前金标准由公开来源快照派生并冻结，是否完成人工复核由每条题目的 `review_status` 字段标识。",
            "",
            "- `vector_rag` 是冻结证据上的 TF-IDF 稀疏向量检索基线，并使用确定性答案模板。",
            "- `graph_rag` 是内存图确定性遍历与证据模板，不包含在线 LLM 推理。",
            "- `cypher_direct` 是 Neo4j 参数化查询与确定性答案模板。",
            "- 公告 PDF 实体/关系抽取 P/R/F1、公告增量可见延迟及在线 LLM 端到端质量尚未测量。",
        ]
    )
    return "\n".join(lines) + "\n"


def _classify_error(
    *,
    exact_match: bool,
    expected_refused: bool,
    output: CompanyRelationQueryResult,
    recall_at_10: float,
) -> str | None:
    if exact_match:
        return None
    if expected_refused and not output.refused:
        return "REFUSE_FAIL"
    if not expected_refused and recall_at_10 == 0:
        return "RETRIEVAL_MISS"
    return "REASONING_ERROR"


def _normalize_paths(paths: list[list[str]]) -> list[list[str]]:
    return sorted((list(path) for path in paths), key=lambda path: tuple(path))


def _category_accuracy(items: list[dict[str, Any]]) -> float:
    if not items:
        return 0.0
    return round(sum(float(item["score"]["exact_match"]) for item in items) / len(items), 6)


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _source_summary(sources) -> list[dict[str, Any]]:
    grouped: dict[str, list[Any]] = defaultdict(list)
    for source in sources:
        grouped[source.provider].append(source)
    return [
        {
            "provider": provider,
            "record_count": len(records),
            "interfaces": ", ".join(sorted({record.interface for record in records if record.interface})),
            "data_dates": ", ".join(sorted({record.data_date for record in records if record.data_date})),
        }
        for provider, records in sorted(grouped.items())
    ]


def _paired_comparison(item_results: list[dict[str, Any]], strategies) -> dict[str, Any]:
    if "vector_rag" not in strategies or "graph_rag" not in strategies:
        return {}
    vector_by_id = {
        item["question_id"]: item for item in item_results if item["strategy"] == "vector_rag"
    }
    graph_by_id = {
        item["question_id"]: item for item in item_results if item["strategy"] == "graph_rag"
    }
    improved = []
    regressed = []
    for question_id, vector_item in vector_by_id.items():
        graph_item = graph_by_id[question_id]
        vector_passed = vector_item["score"]["exact_match"]
        graph_passed = graph_item["score"]["exact_match"]
        if graph_passed and not vector_passed:
            improved.append(question_id)
        elif vector_passed and not graph_passed:
            regressed.append(question_id)
    return {
        "graph_rag_improved_question_ids": improved,
        "graph_rag_regressed_question_ids": regressed,
        "net_improvement": len(improved) - len(regressed),
    }


def _build_regression_gate(
    strategy_summaries: dict[str, dict[str, Any]], *, paired_comparison: dict[str, Any], graph_quality_passed: bool
) -> dict[str, bool]:
    graph_metrics = strategy_summaries.get("graph_rag")
    vector_metrics = strategy_summaries.get("vector_rag")
    score_not_decreased = bool(
        graph_metrics
        and vector_metrics
        and graph_metrics["overall_score"] >= vector_metrics["overall_score"]
    )
    zero_hallucination = bool(graph_metrics and graph_metrics["hallucination_rate"] == 0)
    zero_new_failures = not paired_comparison.get("graph_rag_regressed_question_ids")
    passed = score_not_decreased and zero_new_failures and zero_hallucination and graph_quality_passed
    return {
        "score_not_decreased": score_not_decreased,
        "zero_new_failures": zero_new_failures,
        "zero_hallucination": zero_hallucination,
        "graph_quality_passed": graph_quality_passed,
        "passed": passed,
    }


def _build_run_id(started_at: datetime, snapshot_checksum: str, question_checksum: str) -> str:
    digest = hashlib.sha256(f"{snapshot_checksum}:{question_checksum}".encode()).hexdigest()[:8]
    return f"company-relation-{started_at.strftime('%Y%m%dT%H%M%SZ')}-{digest}"


def _review_summary(questions: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(question.get("review_status", "unknown") for question in questions)
    manually_reviewed = counts.get("manually_reviewed", 0)
    return {
        "status_counts": dict(sorted(counts.items())),
        "manual_review_coverage": round(manually_reviewed / len(questions), 6),
    }
