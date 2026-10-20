"""`agent_rag_answer_v1` 协议的确定性 evaluator：知识库问答同时评检索和答案，并把失败归因到检索、排序或生成。

检索结果取自 Run 中所有 query_kb 调用的输出（SearchOutputSchema 的 JSON），按调用顺序、结果顺序拼接，
同一 chunk 只保留第一次出现的位置，即 Agent 实际看到这些片段的先后顺序。
相关性是二值的：expected.relevant_chunk_ids 中的 chunk 为相关。

结果层：tool_safety、tool_selection、answer_correct（回答包含 answer_must_contain 的全部要点），
三者都通过才算 task_pass。检索层 hit@k、recall@k、mrr、ndcg@k 只测量，不进入 task_pass；
答错时按「没有检索 → 相关片段没检索到 → 检索到但不在前 k → 检索命中但答错」归因主因。
"""

from __future__ import annotations

import json
import math
from typing import Any

from yuxi.evaluation.agent_final_answer import evaluate_agent_final_answer
from yuxi.knowledge.eval.metrics import RetrievalMetrics

METRIC_VERSION = "rag-answer-v1"
RETRIEVAL_TOOL = "query_kb"
DEFAULT_K = 5


def evaluate_rag_answer(
    task: dict[str, Any], actual: dict[str, Any], trace_events: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """计算单题指标，最后一项是 task_pass。"""
    expected = task.get("expected") or {}
    k = int(expected.get("k") or DEFAULT_K)
    relevant = [str(item) for item in expected.get("relevant_chunk_ids", [])]
    tool_calls = actual.get("tool_calls") or []
    retrieval_calls = [call for call in tool_calls if call.get("name") == RETRIEVAL_TOOL]
    ranking = retrieved_chunk_ids(retrieval_calls)

    shared = {item["metric_name"]: item for item in evaluate_agent_final_answer(task, actual, trace_events)}
    answer = str(actual.get("final_answer") or "").lower()
    missing_points = [point for point in expected.get("answer_must_contain", []) if point.lower() not in answer]
    answer_correct = _metric(
        "answer_correct",
        float(not missing_points),
        "failed" if missing_points else "passed",
        "answer_point_missing" if missing_points else None,
    )
    result = [_restamp(shared["tool_safety"]), _restamp(shared["tool_selection"]), answer_correct]
    retrieval = [
        _measured("hit_at_k", hit_at_k(ranking, relevant, k)),
        _measured("recall_at_k", RetrievalMetrics.recall_at_k(ranking, relevant, k)),
        _measured("mrr", reciprocal_rank(ranking, relevant)),
        _measured("ndcg_at_k", ndcg_at_k(ranking, relevant, k)),
    ]

    failure = next((item for item in result if item["status"] == "failed"), None)
    if failure is None:
        task_pass = _metric("task_pass", 1.0, "passed", None)
    elif failure is answer_correct:
        task_pass = _metric("task_pass", 0.0, "failed", _failure_stage(retrieval_calls, ranking, relevant, k))
    else:
        task_pass = _metric("task_pass", 0.0, "failed", failure["reason_code"])
    return result + retrieval + [task_pass]


def retrieved_chunk_ids(retrieval_calls: list[dict[str, Any]]) -> list[str]:
    """按 Agent 看到的先后顺序列出检索到的 chunk，重复出现的只保留第一次。"""
    ranking: list[str] = []
    for call in retrieval_calls:
        try:
            output = json.loads(call.get("output") or "")
        except json.JSONDecodeError:
            # 检索失败时工具返回的是错误文本（如“检索失败: …”），这次调用没有检索结果。
            continue
        for item in output.get("results", []) if isinstance(output, dict) else []:
            chunk_id = str(item.get("id") or "")
            if chunk_id and chunk_id not in ranking:
                ranking.append(chunk_id)
    return ranking


def hit_at_k(ranking: list[str], relevant: list[str], k: int) -> float:
    return float(bool(set(ranking[:k]) & set(relevant)))


def reciprocal_rank(ranking: list[str], relevant: list[str]) -> float:
    relevant_set = set(relevant)
    return next((1.0 / rank for rank, chunk_id in enumerate(ranking, start=1) if chunk_id in relevant_set), 0.0)


def ndcg_at_k(ranking: list[str], relevant: list[str], k: int) -> float:
    """二值相关性的 nDCG@k：DCG = Σ 1/log2(rank+1)，理想排序把全部相关片段排在最前面。"""
    relevant_set = set(relevant)
    dcg = sum(
        1.0 / math.log2(rank + 1) for rank, chunk_id in enumerate(ranking[:k], start=1) if chunk_id in relevant_set
    )
    ideal = sum(1.0 / math.log2(rank + 1) for rank in range(1, min(len(relevant_set), k) + 1))
    return dcg / ideal if ideal else 0.0


def _failure_stage(retrieval_calls: list[dict[str, Any]], ranking: list[str], relevant: list[str], k: int) -> str:
    if not retrieval_calls:
        return "retrieval_not_called"
    relevant_set = set(relevant)
    if not relevant_set & set(ranking):
        return "retrieval_miss"
    if not relevant_set & set(ranking[:k]):
        return "ranking_miss"
    return "generation_error"


def _restamp(metric: dict[str, Any]) -> dict[str, Any]:
    return metric | {"metric_version": METRIC_VERSION}


def _measured(name: str, value: float) -> dict[str, Any]:
    return _metric(name, round(value, 4), "measured", None)


def _metric(name: str, value: float | None, status: str, reason_code: str | None) -> dict[str, Any]:
    return {
        "metric_name": name,
        "metric_version": METRIC_VERSION,
        "value": value,
        "status": status,
        "reason_code": reason_code,
    }
