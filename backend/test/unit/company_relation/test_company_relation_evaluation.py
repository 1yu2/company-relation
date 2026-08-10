from __future__ import annotations

import json

import pytest

from yuxi.company_relation.evaluation import CompanyRelationEvaluationService, score_evaluation_item
from yuxi.company_relation.query_service import CompanyRelationQueryResult, CompanyRelationQueryService


pytestmark = pytest.mark.asyncio


async def test_evaluation_chain_compares_vector_and_graph_strategies(company_relation_snapshot, tmp_path):
    query_service = CompanyRelationQueryService(snapshot=company_relation_snapshot)
    queries = [
        ("attribute-001", "attribute", "甲公司的行业是什么？"),
        ("single_hop-001", "single_hop", "甲公司的股东有哪些？"),
        ("multi_hop-001", "multi_hop", "甲公司和乙公司有什么关联？"),
        ("refusal-001", "refusal", "不存在公司的股东有哪些？"),
    ]
    question_path = tmp_path / "golden.jsonl"
    with question_path.open("w", encoding="utf-8") as file:
        for question_id, category, query in queries:
            expected_output = await query_service.query(query, strategy="graph_rag")
            file.write(
                json.dumps(
                    {
                        "question_id": question_id,
                        "category": category,
                        "query": query,
                        "review_status": "source_derived_unreviewed",
                        "expected": {
                            "refused": expected_output.refused,
                            "answer_entity_ids": expected_output.answer_entity_ids,
                            "fact_ids": expected_output.fact_ids,
                            "paths": expected_output.paths,
                            "evidence_ids": expected_output.fact_ids,
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

    report = await CompanyRelationEvaluationService(query_service).run(
        questions_path=question_path,
        strategies=["vector_rag", "graph_rag"],
        output_dir=tmp_path / "results",
    )

    assert report["strategy_summaries"]["graph_rag"]["exact_accuracy"] == 1.0
    assert report["strategy_summaries"]["vector_rag"]["multi_hop_accuracy"] == 0.0
    assert report["paired_comparison"]["net_improvement"] >= 1
    assert report["regression_gate"]["passed"] is True
    assert report["system_metrics"]["concurrency_10"]["graph_rag"]["error_rate"] == 0.0
    assert "no online LLM inference" in report["inputs"]["strategy_implementation"]["graph_rag"]
    assert len(report["measurement_limitations"]) == 4
    artifact_report = json.loads((tmp_path / "results" / f"{report['run']['run_id']}.json").read_text())
    assert artifact_report["artifacts"] == report["artifacts"]


async def test_strict_score_rejects_incomplete_fact_set():
    question = {
        "expected": {
            "refused": False,
            "answer_entity_ids": ["company:a", "company:b"],
            "fact_ids": ["edge:a", "edge:b"],
            "paths": [["edge:a", "edge:b"]],
            "evidence_ids": ["edge:a", "edge:b"],
        }
    }
    output = CompanyRelationQueryResult(
        strategy="graph_rag",
        query="测试",
        intent="relation_path",
        answer="不完整答案",
        refused=False,
        answer_entity_ids=["company:a", "company:b"],
        fact_ids=["edge:a"],
        paths=[["edge:a"]],
        retrieved_evidence_ids=["edge:a"],
    )

    score = score_evaluation_item(question, output)

    assert score["exact_match"] is False
    assert score["error_type"] == "REASONING_ERROR"
