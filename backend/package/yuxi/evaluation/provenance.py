"""评测执行的本地可追溯记录和脱敏工具。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

SENSITIVE_KEY_PARTS = (
    "password",
    "secret",
    "token",
    "api_key",
    "authorization",
    "cookie",
    "credential",
)
MAX_REDACTED_STRING_LENGTH = 2_000


def redact_value(value: Any) -> Any:
    """递归移除凭据并限制摘要长度，避免把敏感载荷写入 Trace。"""
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            key_text = str(key).lower()
            if any(part in key_text for part in SENSITIVE_KEY_PARTS):
                redacted[str(key)] = "[REDACTED]"
            else:
                redacted[str(key)] = redact_value(item)
        return redacted
    if isinstance(value, (list, tuple)):
        return [redact_value(item) for item in value]
    if isinstance(value, str) and len(value) > MAX_REDACTED_STRING_LENGTH:
        return value[:MAX_REDACTED_STRING_LENGTH] + "...[truncated]"
    return value


def canonical_json(value: Any) -> str:
    """将可序列化对象转换为稳定 JSON，供 checksum 和幂等键使用。"""
    return json.dumps(redact_value(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def checksum_for(value: Any) -> str:
    """计算脱敏后规范化值的 SHA-256。"""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class TraceEvent:
    """表示一条可排序、可回溯的评测执行事件。"""

    event_id: str
    rollout_id: str
    sequence: int
    component: str
    operation: str
    parent_id: str | None = None
    input_summary: Any = None
    output_summary: Any = None
    reason_code: str | None = None
    occurred_at: datetime | None = None
    checksum: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """转换为数据库和 API 可用字典。"""
        result = asdict(self)
        if self.occurred_at is not None:
            result["occurred_at"] = self.occurred_at.isoformat()
        result["input_summary"] = redact_value(self.input_summary)
        result["output_summary"] = redact_value(self.output_summary)
        result["checksum"] = self.checksum or checksum_for(
            {
                "rollout_id": self.rollout_id,
                "sequence": self.sequence,
                "component": self.component,
                "operation": self.operation,
                "parent_id": self.parent_id,
                "input_summary": result["input_summary"],
                "output_summary": result["output_summary"],
            }
        )
        return result


def validate_trace_events(events: list[dict[str, Any]], *, rollout_id: str | None = None) -> list[str]:
    """校验 Trace 的序号唯一性、父子关系和 rollout 归属，返回可展示的 reason code。"""
    errors: list[str] = []
    seen_sequences: set[int] = set()
    event_ids: set[str] = set()
    sequence_by_id: dict[str, int] = {}
    strict_parent_validation = all(event.get("event_id") for event in events)
    strict_sequence_validation = all(event.get("sequence") is not None for event in events)
    for event in events:
        event_id = str(event.get("event_id") or "")
        sequence = event.get("sequence")
        if strict_parent_validation and (not event_id or event_id in event_ids):
            errors.append("trace_event_id_duplicate")
        event_ids.add(event_id)
        if strict_sequence_validation and (not isinstance(sequence, int) or sequence < 1 or sequence in seen_sequences):
            errors.append("trace_sequence_invalid")
        elif isinstance(sequence, int):
            seen_sequences.add(sequence)
            sequence_by_id[event_id] = sequence
        if rollout_id and str(event.get("rollout_id") or rollout_id) != rollout_id:
            errors.append("trace_rollout_mismatch")

    if not events:
        return ["trace_missing"]
    if strict_parent_validation:
        roots = [event for event in events if event.get("parent_id") in (None, "")]
        if len(roots) != 1:
            errors.append("trace_root_invalid")
        for event in events:
            parent_id = event.get("parent_id")
            if not parent_id:
                continue
            parent_sequence = sequence_by_id.get(str(parent_id))
            if parent_sequence is None:
                errors.append("trace_parent_missing")
            elif isinstance(event.get("sequence"), int) and parent_sequence >= event["sequence"]:
                errors.append("trace_parent_order_invalid")
    return list(dict.fromkeys(errors))
