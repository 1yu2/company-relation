from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import delete, select, update

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import TaskRecord
from yuxi.utils.datetime_utils import utc_now_naive


class TaskRepository:
    async def create_task(self, *, name: str, task_type: str, payload: dict[str, Any]) -> TaskRecord:
        """持久化待执行任务，供独立 worker 按任务 ID 读取。"""
        async with pg_manager.get_async_session_context() as session:
            record = TaskRecord(id=uuid.uuid4().hex, name=name, type=task_type, payload=payload)
            session.add(record)
            await session.flush()
            return record

    async def get_by_id(self, task_id: str) -> TaskRecord | None:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(select(TaskRecord).where(TaskRecord.id == task_id))
            return result.scalar_one_or_none()

    async def list_by_type(self, task_type: str) -> list[TaskRecord]:
        """读取指定类型任务，供跨进程任务列表展示。"""
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(select(TaskRecord).where(TaskRecord.type == task_type))
            return list(result.scalars().all())

    async def claim_pending(self, task_id: str, task_type: str) -> TaskRecord | None:
        """原子认领待执行任务，避免重复投递产生并行报告。"""
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                update(TaskRecord)
                .where(
                    TaskRecord.id == task_id,
                    TaskRecord.type == task_type,
                    TaskRecord.status == "pending",
                    TaskRecord.cancel_requested == 0,
                )
                .values(status="running", started_at=utc_now_naive(), updated_at=utc_now_naive())
                .returning(TaskRecord)
            )
            return result.scalar_one_or_none()

    async def update_status(
        self,
        task_id: str,
        *,
        expected_status: str,
        values: dict[str, Any],
    ) -> bool:
        """仅在任务仍处于预期状态时更新，避免覆盖其他进程的终态。"""
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                update(TaskRecord)
                .where(TaskRecord.id == task_id, TaskRecord.status == expected_status)
                .values(**values, updated_at=utc_now_naive())
            )
            return result.rowcount > 0

    async def request_cancel(self, task_id: str) -> bool:
        """跨进程持久化取消请求。"""
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                update(TaskRecord)
                .where(TaskRecord.id == task_id, TaskRecord.status.in_(("pending", "running")))
                .values(cancel_requested=1, updated_at=utc_now_naive())
            )
            return result.rowcount > 0

    async def list(self, status: str | None = None, limit: int = 100) -> list[TaskRecord]:
        async with pg_manager.get_async_session_context() as session:
            stmt = select(TaskRecord)
            if status:
                stmt = stmt.where(TaskRecord.status == status)
            stmt = stmt.order_by(TaskRecord.created_at.desc()).limit(max(limit, 0))
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def list_all(self) -> list[TaskRecord]:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(select(TaskRecord).order_by(TaskRecord.created_at.desc()))
            return list(result.scalars().all())

    async def upsert(self, task_id: str, data: dict[str, Any]) -> TaskRecord:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(select(TaskRecord).where(TaskRecord.id == task_id))
            record = result.scalar_one_or_none()
            if record is None:
                record = TaskRecord(id=task_id, **data)
                session.add(record)
                return record
            for key, value in data.items():
                setattr(record, key, value)
            return record

    async def delete(self, task_id: str) -> bool:
        """Delete a task by id. Returns True if deleted, False if not found."""
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(delete(TaskRecord).where(TaskRecord.id == task_id))
            return result.rowcount > 0

    async def delete_all(self) -> None:
        async with pg_manager.get_async_session_context() as session:
            await session.execute(delete(TaskRecord))
