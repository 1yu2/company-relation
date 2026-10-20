"""将现有评测来源映射到平台通用结果协议。

适配器只读取调用方已经取得的结果对象，不负责启动旧评测链路，因而不会改变
RAG、企业关系或 Agent Eval 的执行、副作用和权限边界。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Iterable, Mapping
from typing import Any, Protocol

from yuxi.evaluation.provenance import checksum_for, redact_value

SUPPORTED_SOURCE_KINDS = ("agent", "tool_mcp", "rag", "graph", "fixture", "company_due_diligence")


@dataclass(frozen=True)
class UnifiedEvaluationRecord:
    """平台页面和聚合器使用的来源无关评测记录。"""

    source_kind: str
    source_ref: dict[str, Any]
    task_key: str
    input_summary: Any
    actual_summary: Any = None
    expected_summary: Any = None
    status: str = "unknown"
    domain_metrics: dict[str, Any] = field(default_factory=dict)
    failure: dict[str, Any] | None = None
    trace_ref: dict[str, Any] | None = None
    source_run_id: str | None = None
    source_version: str | None = None
    attempt: int = 1
    input_checksum: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """转换为 API 可返回的字典。"""
        return {
            "source_kind": self.source_kind,
            "source_ref": self.source_ref,
            "task_key": self.task_key,
            "input_summary": self.input_summary,
            "actual_summary": self.actual_summary,
            "expected_summary": self.expected_summary,
            "status": self.status,
            "domain_metrics": self.domain_metrics,
            "failure": self.failure,
            "trace_ref": self.trace_ref,
            "source_run_id": self.source_run_id,
            "source_version": self.source_version,
            "attempt": self.attempt,
            "input_checksum": self.input_checksum or checksum_for(self.input_summary),
        }


def adapt_source_record(source_kind: str, record: dict[str, Any]) -> UnifiedEvaluationRecord:
    """把 Agent、RAG、Graph 等来源的已完成结果转为统一记录。"""
    normalized_kind = str(source_kind or "").strip().lower()
    if normalized_kind not in SUPPORTED_SOURCE_KINDS:
        raise ValueError(f"不支持的评测来源: {source_kind}")
    task_key = str(record.get("task_key") or record.get("question_id") or record.get("item_id") or "")
    if not task_key:
        raise ValueError("评测来源记录缺少 task_key")
    domain_metrics = dict(record.get("domain_metrics") or {})
    if normalized_kind == "rag":
        domain_metrics.update({key: record[key] for key in ("recall_at_k", "f1_at_k", "answer_score") if key in record})
    elif normalized_kind == "graph":
        domain_metrics.update(
            {key: record[key] for key in ("mrr", "evidence_alignment", "hallucination", "fact_ids") if key in record}
        )
    source_ref = dict(record.get("source_ref") or {"source_kind": normalized_kind})
    source_run_id = _optional_text(record.get("source_run_id") or source_ref.get("run_id"))
    source_version = _optional_text(record.get("source_version") or source_ref.get("version"))
    trace_ref = _trace_ref(record)
    failure = _failure_with_trace_status(record, trace_ref)
    return UnifiedEvaluationRecord(
        source_kind=normalized_kind,
        source_ref=redact_value(source_ref),
        task_key=task_key,
        input_summary=redact_value(record.get("input_summary", record.get("query"))),
        actual_summary=redact_value(record.get("actual_summary", record.get("output", record.get("answer")))),
        expected_summary=redact_value(record.get("expected_summary", record.get("expected"))),
        status=str(record.get("status") or "unknown"),
        domain_metrics=domain_metrics,
        failure=failure,
        trace_ref=trace_ref,
        source_run_id=source_run_id,
        source_version=source_version,
        attempt=max(1, int(record.get("attempt") or 1)),
        input_checksum=record.get("input_checksum") or checksum_for(record.get("input_summary", record.get("query"))),
    )


class EvaluationSourceAdapter(Protocol):
    """旧评测结果到统一记录的只读适配器协议。"""

    source_kind: str

    def adapt(self, records: Iterable[Any], **context: Any) -> list[UnifiedEvaluationRecord]:
        """把已读取的来源记录转换为统一结果。"""


class CompanyDueDiligenceEvaluationAdapter:
    """适配企业尽调 domain evaluator 的已完成记录，不启动业务查询。"""

    source_kind = "company_due_diligence"

    def adapt(self, records: Iterable[Any], **context: Any) -> list[UnifiedEvaluationRecord]:
        return [
            adapt_source_record(
                self.source_kind,
                {
                    **(dict(record) if isinstance(record, Mapping) else {}),
                    "source_run_id": context.get("run_id"),
                    "source_version": context.get("source_version"),
                },
            )
            for record in records
        ]


class RagEvaluationAdapter:
    """适配知识库 EvaluationRunItem，不改变 RAG 评分公式。"""

    source_kind = "rag"

    def adapt(self, records: Iterable[Any], **context: Any) -> list[UnifiedEvaluationRecord]:
        run_id = context.get("run_id")
        dataset_id = context.get("dataset_id")
        version = context.get("source_version")
        return [
            adapt_source_record(
                self.source_kind,
                {
                    "task_key": _read(record, "item_id") or f"item-{_read(record, 'item_index')}",
                    "query": _read(record, "query_text") or _read(record, "query"),
                    "expected": {
                        "gold_answer": _read(record, "gold_answer"),
                        "gold_chunk_ids": _read(record, "gold_chunk_ids") or [],
                    },
                    "output": {
                        "answer": _read(record, "generated_answer"),
                        "retrieved_chunks": _read(record, "retrieved_chunks") or [],
                    },
                    "domain_metrics": _read(record, "metrics") or {},
                    "status": _rag_status(record),
                    "source_run_id": run_id,
                    "source_version": version,
                    "source_ref": {"run_id": run_id, "dataset_id": dataset_id, "item_id": _read(record, "item_id")},
                    "trace_ref": _read(record, "trace_ref"),
                },
            )
            for record in records
        ]


class GraphEvaluationAdapter:
    """适配企业关系冻结评测报告中的逐题 item。"""

    source_kind = "graph"

    def adapt(self, records: Iterable[Any], **context: Any) -> list[UnifiedEvaluationRecord]:
        run_id = context.get("run_id")
        source_version = context.get("source_version") or context.get("snapshot_checksum")
        result: list[UnifiedEvaluationRecord] = []
        for record in records:
            expected = _as_dict(_read(record, "expected"))
            output = _as_dict(_read(record, "output"))
            score = _as_dict(_read(record, "score"))
            exact_match = score.get("exact_match")
            status = "completed" if exact_match is not False else "failed"
            failure = (
                None
                if status == "completed"
                else {
                    "failure_type": "domain_evaluation",
                    "reason_code": "graph_answer_mismatch",
                    "message": "企业关系严格对照结果未匹配",
                }
            )
            result.append(
                adapt_source_record(
                    self.source_kind,
                    {
                        "task_key": _read(record, "question_id"),
                        "query": _read(record, "query"),
                        "expected": expected,
                        "output": output,
                        "domain_metrics": score,
                        "status": status,
                        "failure": failure,
                        "source_run_id": run_id,
                        "source_version": source_version,
                        "source_ref": {
                            "run_id": run_id,
                            "snapshot_checksum": _read(record, "snapshot_checksum") or context.get("snapshot_checksum"),
                            "strategy": _read(record, "strategy"),
                        },
                        "trace_ref": _read(record, "trace_ref"),
                    },
                )
            )
        return result


class AgentEvaluationAdapter:
    """适配 Agent Eval/Langfuse item 的最终输出和轨迹摘要。"""

    source_kind = "agent"

    def adapt(self, records: Iterable[Any], **context: Any) -> list[UnifiedEvaluationRecord]:
        run_id = context.get("run_id")
        result: list[UnifiedEvaluationRecord] = []
        for record in records:
            events = _as_dict(_read(record, "trace_summary") or _read(record, "trace"))
            status = str(_read(record, "status") or "completed")
            trace_ref = _trace_ref(
                {"trace_ref": _read(record, "trace_ref"), "langfuse_trace_id": _read(record, "langfuse_trace_id")}
            )
            failure = None
            if status not in {"completed", "passed", "finished"}:
                failure = {
                    "failure_type": "agent_eval",
                    "reason_code": str(_read(record, "reason_code") or "agent_eval_failed"),
                    "message": str(_read(record, "error") or "Agent Eval 运行失败"),
                }
            if not trace_ref:
                failure = failure or {
                    "failure_type": "trace",
                    "reason_code": "trace_missing",
                    "message": "历史 Agent Eval 记录没有本地 Trace",
                }
            result.append(
                adapt_source_record(
                    self.source_kind,
                    {
                        "task_key": _read(record, "task_key") or _read(record, "item_id"),
                        "query": _read(record, "query") or _read(record, "input"),
                        "expected": _read(record, "expected"),
                        "output": _read(record, "output") or _read(record, "final_answer"),
                        "domain_metrics": {
                            "event_count": events.get("event_count"),
                            "tool_call_count": events.get("tool_call_count"),
                            "tool_error_count": events.get("tool_error_count"),
                            "interruption_count": events.get("interruption_count"),
                        },
                        "status": status,
                        "failure": failure,
                        "source_run_id": run_id,
                        "source_ref": {
                            "run_id": run_id,
                            "dataset_name": context.get("dataset_name") or _read(record, "dataset_name"),
                            "dataset_item_id": _read(record, "item_id"),
                        },
                        "trace_ref": trace_ref,
                    },
                )
            )
        return result


ADAPTERS: dict[str, EvaluationSourceAdapter] = {
    "agent": AgentEvaluationAdapter(),
    "tool_mcp": AgentEvaluationAdapter(),
    "rag": RagEvaluationAdapter(),
    "graph": GraphEvaluationAdapter(),
    "company_due_diligence": CompanyDueDiligenceEvaluationAdapter(),
}


def adapt_records(source_kind: str, records: Iterable[Any], **context: Any) -> list[UnifiedEvaluationRecord]:
    """按来源选择只读适配器，供服务层和回归脚本复用。"""
    normalized_kind = str(source_kind or "").strip().lower()
    adapter = ADAPTERS.get(normalized_kind)
    if adapter is None:
        raise ValueError(f"不支持的评测来源: {source_kind}")
    return adapter.adapt(records, **context)


def _read(record: Any, field: str) -> Any:
    if isinstance(record, Mapping):
        return record.get(field)
    return getattr(record, field, None)


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _optional_text(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _trace_ref(record: Mapping[str, Any]) -> dict[str, Any] | None:
    value = record.get("trace_ref")
    if isinstance(value, Mapping) and value:
        return redact_value(dict(value))
    trace_id = record.get("langfuse_trace_id")
    return {"langfuse_trace_id": str(trace_id)} if trace_id else None


def _failure_with_trace_status(record: Mapping[str, Any], trace_ref: dict[str, Any] | None) -> dict[str, Any] | None:
    failure = record.get("failure")
    if failure:
        return redact_value(dict(failure)) if isinstance(failure, Mapping) else {"message": str(failure)}
    if record.get("status") in {"completed", "passed", "finished"} and record.get("trace_ref") is None:
        return {"failure_type": "trace", "reason_code": "trace_missing", "message": "来源记录未提供 Trace"}
    return None


def _rag_status(record: Any) -> str:
    metrics = _as_dict(_read(record, "metrics"))
    if metrics.get("error"):
        return "failed"
    return str(_read(record, "status") or "completed")
