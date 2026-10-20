"""平台评测 PostgreSQL 仓储。"""

from __future__ import annotations

import base64
import binascii
import json
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_evaluation import (
    PlatformEvaluationDataset,
    PlatformEvaluationDatasetVersion,
    PlatformEvaluationExperiment,
    PlatformEvaluationFailure,
    PlatformEvaluationMetricResult,
    PlatformEvaluationRollout,
    PlatformEvaluationTask,
    PlatformEvaluationTraceEvent,
)


class PlatformEvaluationRepository:
    """封装平台评测事实的创建、查询和幂等写入。"""

    def __init__(self, db: AsyncSession | None = None):
        self.db = db

    @asynccontextmanager
    async def _session(self):
        if self.db is not None:
            yield self.db
            return
        async with pg_manager.get_async_session_context() as session:
            yield session

    async def create_dataset(self, data: dict[str, Any]) -> PlatformEvaluationDataset:
        record = PlatformEvaluationDataset(**data)
        async with self._session() as session:
            session.add(record)
        return record

    async def get_dataset(self, dataset_id: str) -> PlatformEvaluationDataset | None:
        async with self._session() as session:
            result = await session.execute(
                select(PlatformEvaluationDataset).where(PlatformEvaluationDataset.dataset_id == dataset_id)
            )
            return result.scalar_one_or_none()

    async def list_datasets(self, source_kind: str | None = None, limit: int = 50) -> list[PlatformEvaluationDataset]:
        records, _ = await self.list_datasets_page(source_kind=source_kind, limit=limit)
        return records

    async def list_datasets_page(
        self, source_kind: str | None = None, limit: int = 50, cursor: str | None = None
    ) -> tuple[list[PlatformEvaluationDataset], str | None]:
        """按创建时间倒序返回数据集和下一页游标。"""
        limit = max(1, min(limit, 100))
        async with self._session() as session:
            stmt = select(PlatformEvaluationDataset).order_by(
                PlatformEvaluationDataset.created_at.desc(), PlatformEvaluationDataset.dataset_id.desc()
            )
            if source_kind:
                source_values = ("agent", "tool_mcp") if source_kind == "agent" else (source_kind,)
                stmt = stmt.where(PlatformEvaluationDataset.source_kind.in_(source_values))
            stmt = _apply_cursor(
                stmt, cursor, PlatformEvaluationDataset.created_at, PlatformEvaluationDataset.dataset_id
            )
            result = await session.execute(stmt.limit(limit + 1))
            records = list(result.scalars().all())
        return _page(records, limit, lambda item: (item.created_at, item.dataset_id))

    async def create_version(self, data: dict[str, Any]) -> PlatformEvaluationDatasetVersion:
        record = PlatformEvaluationDatasetVersion(**data)
        async with self._session() as session:
            session.add(record)
        return record

    async def get_version(self, version_id: str) -> PlatformEvaluationDatasetVersion | None:
        async with self._session() as session:
            result = await session.execute(
                select(PlatformEvaluationDatasetVersion).where(
                    PlatformEvaluationDatasetVersion.version_id == version_id
                )
            )
            return result.scalar_one_or_none()

    async def list_versions(self, dataset_id: str) -> list[PlatformEvaluationDatasetVersion]:
        async with self._session() as session:
            result = await session.execute(
                select(PlatformEvaluationDatasetVersion)
                .where(PlatformEvaluationDatasetVersion.dataset_id == dataset_id)
                .order_by(PlatformEvaluationDatasetVersion.version_no.desc())
            )
            return list(result.scalars().all())

    async def update_version(self, version_id: str, values: dict[str, Any]) -> PlatformEvaluationDatasetVersion | None:
        """更新草稿版本；发布后任何字段变更都拒绝。"""
        async with self._session() as session:
            result = await session.execute(
                select(PlatformEvaluationDatasetVersion).where(
                    PlatformEvaluationDatasetVersion.version_id == version_id
                )
            )
            record = result.scalar_one_or_none()
            if record is None:
                return None
            if record.status == "published" and any(
                getattr(record, key) != value for key, value in values.items() if key != "published_at"
            ):
                raise ValueError("已发布的数据集版本不可修改")
            for key, value in values.items():
                setattr(record, key, value)
            return record

    async def create_tasks(self, tasks: list[dict[str, Any]]) -> list[PlatformEvaluationTask]:
        records = [PlatformEvaluationTask(**data) for data in tasks]
        async with self._session() as session:
            session.add_all(records)
        return records

    async def list_tasks(self, version_id: str) -> list[PlatformEvaluationTask]:
        async with self._session() as session:
            result = await session.execute(
                select(PlatformEvaluationTask)
                .where(PlatformEvaluationTask.version_id == version_id)
                .order_by(PlatformEvaluationTask.task_key.asc())
            )
            return list(result.scalars().all())

    async def create_experiment(self, data: dict[str, Any]) -> PlatformEvaluationExperiment:
        record = PlatformEvaluationExperiment(**data)
        async with self._session() as session:
            session.add(record)
        return record

    async def get_experiment(self, experiment_id: str) -> PlatformEvaluationExperiment | None:
        async with self._session() as session:
            # 后台执行时取消请求由其他会话写入；populate_existing 保证读到已提交的最新状态。
            result = await session.execute(
                select(PlatformEvaluationExperiment)
                .where(PlatformEvaluationExperiment.experiment_id == experiment_id)
                .execution_options(populate_existing=True)
            )
            return result.scalar_one_or_none()

    async def list_experiments_by_status(self, statuses: set[str]) -> list[PlatformEvaluationExperiment]:
        async with self._session() as session:
            result = await session.execute(
                select(PlatformEvaluationExperiment)
                .where(PlatformEvaluationExperiment.status.in_(statuses))
                .order_by(PlatformEvaluationExperiment.created_at)
            )
            return list(result.scalars().all())

    async def list_experiments(
        self, source_kind: str | None = None, limit: int = 50
    ) -> list[PlatformEvaluationExperiment]:
        records, _ = await self.list_experiments_page(source_kind=source_kind, limit=limit)
        return records

    async def list_experiments_page(
        self, source_kind: str | None = None, limit: int = 50, cursor: str | None = None
    ) -> tuple[list[PlatformEvaluationExperiment], str | None]:
        """按创建时间倒序返回实验和下一页游标。"""
        limit = max(1, min(limit, 100))
        async with self._session() as session:
            stmt = select(PlatformEvaluationExperiment).order_by(
                PlatformEvaluationExperiment.created_at.desc(), PlatformEvaluationExperiment.experiment_id.desc()
            )
            if source_kind:
                source_values = ("agent", "tool_mcp") if source_kind == "agent" else (source_kind,)
                stmt = stmt.where(PlatformEvaluationExperiment.source_kind.in_(source_values))
            stmt = _apply_cursor(
                stmt, cursor, PlatformEvaluationExperiment.created_at, PlatformEvaluationExperiment.experiment_id
            )
            result = await session.execute(stmt.limit(limit + 1))
            records = list(result.scalars().all())
        return _page(records, limit, lambda item: (item.created_at, item.experiment_id))

    async def create_rollout(self, data: dict[str, Any]) -> PlatformEvaluationRollout:
        async with self._session() as session:
            existing = await session.execute(
                select(PlatformEvaluationRollout).where(
                    PlatformEvaluationRollout.idempotency_key == data["idempotency_key"]
                )
            )
            record = existing.scalar_one_or_none()
            if record is not None:
                return record
            record = PlatformEvaluationRollout(**data)
            session.add(record)
        return record

    async def get_rollout(self, rollout_id: str) -> PlatformEvaluationRollout | None:
        async with self._session() as session:
            # 取消由其他会话写入；populate_existing 保证后台执行读到已提交的最新状态。
            result = await session.execute(
                select(PlatformEvaluationRollout)
                .where(PlatformEvaluationRollout.rollout_id == rollout_id)
                .execution_options(populate_existing=True)
            )
            return result.scalar_one_or_none()

    async def get_rollout_by_idempotency(self, key: str) -> PlatformEvaluationRollout | None:
        async with self._session() as session:
            result = await session.execute(
                select(PlatformEvaluationRollout).where(PlatformEvaluationRollout.idempotency_key == key)
            )
            return result.scalar_one_or_none()

    async def update_rollout(self, rollout_id: str, values: dict[str, Any]) -> PlatformEvaluationRollout | None:
        """幂等更新单次尝试的状态和终态结果。"""
        async with self._session() as session:
            result = await session.execute(
                select(PlatformEvaluationRollout)
                .where(PlatformEvaluationRollout.rollout_id == rollout_id)
                .execution_options(populate_existing=True)
            )
            record = result.scalar_one_or_none()
            if record is None:
                return None
            if record.status in {"COMPLETED", "FAILED", "TIMEOUT", "CANCELLED"} and values.get("status") not in (
                None,
                record.status,
            ):
                raise ValueError("rollout 已处于终态，不可再次修改状态")
            for key, value in values.items():
                setattr(record, key, value)
            return record

    async def list_rollouts(self, experiment_id: str) -> list[PlatformEvaluationRollout]:
        """返回实验的全部 rollout，供聚合与取消使用；分页展示走 list_rollouts_page。"""
        async with self._session() as session:
            result = await session.execute(
                select(PlatformEvaluationRollout)
                .where(PlatformEvaluationRollout.experiment_id == experiment_id)
                .order_by(PlatformEvaluationRollout.created_at.desc(), PlatformEvaluationRollout.rollout_id.desc())
            )
            return list(result.scalars().all())

    async def list_rollouts_page(
        self, experiment_id: str, limit: int = 100, cursor: str | None = None
    ) -> tuple[list[PlatformEvaluationRollout], str | None]:
        """按创建时间和 rollout_id 稳定分页查询实验结果。"""
        limit = max(1, min(limit, 200))
        async with self._session() as session:
            stmt = (
                select(PlatformEvaluationRollout)
                .where(PlatformEvaluationRollout.experiment_id == experiment_id)
                .order_by(PlatformEvaluationRollout.created_at.desc(), PlatformEvaluationRollout.rollout_id.desc())
            )
            stmt = _apply_cursor(
                stmt, cursor, PlatformEvaluationRollout.created_at, PlatformEvaluationRollout.rollout_id
            )
            result = await session.execute(stmt.limit(limit + 1))
            records = list(result.scalars().all())
        return _page(records, limit, lambda item: (item.created_at, item.rollout_id))

    async def save_trace_events(self, events: list[dict[str, Any]]) -> list[PlatformEvaluationTraceEvent]:
        async with self._session() as session:
            records: list[PlatformEvaluationTraceEvent] = []
            for data in events:
                result = await session.execute(
                    select(PlatformEvaluationTraceEvent).where(
                        PlatformEvaluationTraceEvent.rollout_id == data["rollout_id"],
                        PlatformEvaluationTraceEvent.sequence == data["sequence"],
                    )
                )
                record = result.scalar_one_or_none()
                if record is None:
                    record = PlatformEvaluationTraceEvent(**data)
                    session.add(record)
                records.append(record)
        return records

    async def list_trace_events(self, rollout_id: str) -> list[PlatformEvaluationTraceEvent]:
        async with self._session() as session:
            result = await session.execute(
                select(PlatformEvaluationTraceEvent)
                .where(PlatformEvaluationTraceEvent.rollout_id == rollout_id)
                .order_by(PlatformEvaluationTraceEvent.sequence.asc())
            )
            return list(result.scalars().all())

    async def save_metrics(self, metrics: list[dict[str, Any]]) -> list[PlatformEvaluationMetricResult]:
        async with self._session() as session:
            records: list[PlatformEvaluationMetricResult] = []
            for data in metrics:
                result = await session.execute(
                    select(PlatformEvaluationMetricResult).where(
                        PlatformEvaluationMetricResult.rollout_id == data["rollout_id"],
                        PlatformEvaluationMetricResult.metric_name == data["metric_name"],
                        PlatformEvaluationMetricResult.metric_version == data["metric_version"],
                    )
                )
                record = result.scalar_one_or_none()
                if record is None:
                    record = PlatformEvaluationMetricResult(**data)
                    session.add(record)
                records.append(record)
        return records

    async def list_metrics(self, experiment_id: str) -> list[PlatformEvaluationMetricResult]:
        async with self._session() as session:
            result = await session.execute(
                select(PlatformEvaluationMetricResult)
                .join(
                    PlatformEvaluationRollout,
                    PlatformEvaluationMetricResult.rollout_id == PlatformEvaluationRollout.rollout_id,
                )
                .where(PlatformEvaluationRollout.experiment_id == experiment_id)
                .order_by(PlatformEvaluationMetricResult.created_at.asc())
            )
            return list(result.scalars().all())

    async def save_failures(self, failures: list[dict[str, Any]]) -> list[PlatformEvaluationFailure]:
        async with self._session() as session:
            records: list[PlatformEvaluationFailure] = []
            for data in failures:
                result = await session.execute(
                    select(PlatformEvaluationFailure).where(PlatformEvaluationFailure.failure_id == data["failure_id"])
                )
                record = result.scalar_one_or_none()
                if record is None:
                    record = PlatformEvaluationFailure(**data)
                    session.add(record)
                records.append(record)
        return records

    async def list_failures(self, experiment_id: str) -> list[PlatformEvaluationFailure]:
        async with self._session() as session:
            result = await session.execute(
                select(PlatformEvaluationFailure)
                .join(
                    PlatformEvaluationRollout,
                    PlatformEvaluationFailure.rollout_id == PlatformEvaluationRollout.rollout_id,
                )
                .where(PlatformEvaluationRollout.experiment_id == experiment_id)
                .order_by(PlatformEvaluationFailure.created_at.asc())
            )
            return list(result.scalars().all())

    async def update_experiment(
        self, experiment_id: str, values: dict[str, Any]
    ) -> PlatformEvaluationExperiment | None:
        async with self._session() as session:
            result = await session.execute(
                select(PlatformEvaluationExperiment).where(PlatformEvaluationExperiment.experiment_id == experiment_id)
            )
            record = result.scalar_one_or_none()
            if record is None:
                return None
            for key, value in values.items():
                setattr(record, key, value)
            return record


def encode_cursor(created_at: datetime, record_id: str) -> str:
    """编码不携带业务数据的稳定游标。"""
    payload = {"created_at": created_at.isoformat(), "id": record_id}
    return base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, str]:
    """校验并解码游标，非法值由调用方映射为 validation_error。"""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
        return datetime.fromisoformat(str(payload["created_at"])), str(payload["id"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, UnicodeDecodeError, binascii.Error) as exc:
        raise ValueError("cursor 格式无效") from exc


def _apply_cursor(stmt, cursor: str | None, created_column, id_column):
    if not cursor:
        return stmt
    created_at, record_id = decode_cursor(cursor)
    return stmt.where(or_(created_column < created_at, and_(created_column == created_at, id_column < record_id)))


def _page(records: list[Any], limit: int, key):
    has_more = len(records) > limit
    visible = records[:limit]
    next_cursor = encode_cursor(*key(visible[-1])) if has_more and visible else None
    return visible, next_cursor
