"""agent_rag_answer_v1 evaluator：检索指标按手算结果核对，失败按检索、排序、生成归因。"""

from __future__ import annotations

import json
import math

import pytest

from yuxi.evaluation.rag_answer import evaluate_rag_answer, ndcg_at_k, reciprocal_rank, retrieved_chunk_ids

pytestmark = pytest.mark.unit


def _query_kb(*chunk_ids: str) -> dict:
    output = {"kb_id": "kb_1", "results": [{"id": chunk_id, "file_id": "f1", "content": ""} for chunk_id in chunk_ids]}
    return {"name": "query_kb", "args": {"query_text": "q"}, "output": json.dumps(output), "status": "success"}


def _task(relevant: list[str], k: int = 3) -> dict:
    return {
        "expected": {
            "relevant_chunk_ids": relevant,
            "k": k,
            "answer_must_contain": ["2025 年", "12.5%"],
            "tool_policy": {"required": ["query_kb"], "forbidden": []},
        }
    }


@pytest.mark.parametrize(
    ("ranking", "relevant", "mrr", "ndcg"),
    [
        # 首位命中：MRR = 1；nDCG@3 = (1/log2 2) / (1/log2 2) = 1
        (["c1", "c2", "c3"], ["c1"], 1.0, 1.0),
        # 第 2 位命中：MRR = 1/2；nDCG@3 = (1/log2 3) / (1/log2 2) = 0.6309
        (["c1", "c2", "c3"], ["c2"], 0.5, 1 / math.log2(3)),
        # 第 1、3 位命中：DCG = 1 + 1/log2 4 = 1.5；IDCG = 1 + 1/log2 3 = 1.6309；nDCG = 0.9197
        (["c1", "c2", "c3"], ["c1", "c3"], 1.0, 1.5 / (1 + 1 / math.log2(3))),
        # 没有命中：MRR = 0，nDCG = 0
        (["c4", "c5"], ["c1"], 0.0, 0.0),
    ],
)
def test_ranking_metrics_match_hand_calculation(ranking, relevant, mrr, ndcg):
    assert reciprocal_rank(ranking, relevant) == pytest.approx(mrr)
    assert ndcg_at_k(ranking, relevant, 3) == pytest.approx(ndcg)


def test_ranking_follows_call_order_and_skips_failed_calls():
    failed = {"name": "query_kb", "args": {}, "output": "检索失败: 知识库资源 'kb_1' 不存在", "status": "success"}

    assert retrieved_chunk_ids([_query_kb("c1", "c2"), failed, _query_kb("c2", "c3")]) == ["c1", "c2", "c3"]


def _evaluate(task: dict, answer: str, tool_calls: list[dict]) -> dict[str, dict]:
    metrics = evaluate_rag_answer(task, {"final_answer": answer, "tool_calls": tool_calls}, [])
    assert metrics[-1]["metric_name"] == "task_pass"
    assert {item["metric_version"] for item in metrics} == {"rag-answer-v1"}
    return {item["metric_name"]: item for item in metrics}


def test_correct_answer_with_relevant_chunk_on_top_passes():
    metrics = _evaluate(_task(["c1"]), "2025 年增速为 12.5%。", [_query_kb("c1", "c2")])

    assert metrics["task_pass"]["status"] == "passed"
    assert (metrics["hit_at_k"]["value"], metrics["mrr"]["value"], metrics["recall_at_k"]["value"]) == (1.0, 1.0, 1.0)


@pytest.mark.parametrize(
    ("tool_calls", "answer", "reason_code"),
    [
        ([], "2025 年增速为 12.5%。", "required_tool_missing"),
        ([_query_kb("c7", "c8")], "2025 年增速为 10%。", "retrieval_miss"),
        ([_query_kb("c7", "c8", "c9", "c1")], "2025 年增速为 10%。", "ranking_miss"),
        ([_query_kb("c1", "c2")], "2025 年增速为 10%。", "generation_error"),
    ],
)
def test_wrong_answer_is_attributed_to_the_failing_stage(tool_calls, answer, reason_code):
    metrics = _evaluate(_task(["c1"], k=3), answer, tool_calls)

    assert metrics["task_pass"]["status"] == "failed"
    assert metrics["task_pass"]["reason_code"] == reason_code


def test_answer_without_retrieval_call_is_attributed_to_missing_retrieval():
    """不要求必须调用检索工具的题，答错且没检索时归因为没有检索。"""
    task = _task(["c1"])
    task["expected"]["tool_policy"]["required"] = []

    metrics = _evaluate(task, "不知道。", [])

    assert metrics["task_pass"]["reason_code"] == "retrieval_not_called"
