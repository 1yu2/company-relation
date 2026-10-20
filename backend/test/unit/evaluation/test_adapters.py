import pytest

from yuxi.evaluation.adapters import (
    AgentEvaluationAdapter,
    GraphEvaluationAdapter,
    RagEvaluationAdapter,
    adapt_records,
    adapt_source_record,
)


@pytest.mark.unit
def test_adapt_rag_record_preserves_domain_metrics_and_source_ref():
    record = adapt_source_record(
        "rag",
        {
            "task_key": "rag-001",
            "query": "question",
            "expected": {"gold_answer": "answer"},
            "output": {"answer": "answer"},
            "recall_at_k": 1.0,
            "f1_at_k": 0.5,
            "source_ref": {"run_id": "legacy-run"},
            "status": "completed",
        },
    )

    assert record.source_kind == "rag"
    assert record.source_ref == {"run_id": "legacy-run"}
    assert record.domain_metrics == {"recall_at_k": 1.0, "f1_at_k": 0.5}
    assert record.status == "completed"


@pytest.mark.unit
def test_adapt_graph_record_requires_task_key():
    with pytest.raises(ValueError, match="task_key"):
        adapt_source_record("graph", {"answer": "missing key"})


def test_adapt_company_due_diligence_record_keeps_domain_metrics():
    records = adapt_records(
        "company_due_diligence",
        [
            {
                "task_key": "cdd-1",
                "query": "注册资本？",
                "expected": {"review_status": "reviewed"},
                "output": {"resolution_status": "resolved"},
                "domain_metrics": {"fact_correctness": 1.0},
                "status": "completed",
            }
        ],
        run_id="run-1",
        source_version="q02-v1",
    )

    assert records[0].source_kind == "company_due_diligence"
    assert records[0].source_run_id == "run-1"
    assert records[0].source_version == "q02-v1"
    assert records[0].domain_metrics["fact_correctness"] == 1.0


@pytest.mark.unit
def test_source_adapters_map_legacy_records_without_running_sources():
    rag = RagEvaluationAdapter().adapt(
        [{"item_id": "rag-1", "query_text": "q", "generated_answer": "a", "metrics": {"recall@10": 1.0}}],
        run_id="run-rag",
        dataset_id="dataset-rag",
    )[0]
    graph = GraphEvaluationAdapter().adapt(
        [{"question_id": "graph-1", "query": "q", "expected": {}, "output": {}, "score": {"exact_match": False}}],
        run_id="run-graph",
        snapshot_checksum="snapshot-1",
    )[0]
    agent = AgentEvaluationAdapter().adapt(
        [{"item_id": "agent-1", "input": "q", "output": "a", "status": "completed"}],
        run_id="run-agent",
    )[0]

    assert rag.source_run_id == "run-rag"
    assert rag.domain_metrics["recall@10"] == 1.0
    assert graph.status == "failed"
    assert graph.failure["reason_code"] == "graph_answer_mismatch"
    assert agent.failure["reason_code"] == "trace_missing"
