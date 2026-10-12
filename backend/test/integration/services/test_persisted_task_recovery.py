from __future__ import annotations

import asyncio
import uuid

import pytest

from yuxi.repositories.task_repository import TaskRepository
from yuxi.services.task_service import PersistedTaskContext

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_persisted_task_claim_is_single_winner_and_cancel_is_cross_process():
    """真实 PostgreSQL 应只允许一个 worker 认领，并能传播取消请求。"""
    repository = TaskRepository()
    task_id: str | None = None
    task_name = f"pytest persisted task {uuid.uuid4().hex}"
    try:
        await repository.create_task(
            name=task_name,
            task_type="company_due_diligence_graph_projection",
            payload={"scope_key": f"pytest-{uuid.uuid4().hex}", "request": {"eids": None, "batch_size": 10}},
        )
        # create_task 生成随机 ID，因此取最近创建的测试任务不安全；用列表定位本次唯一名称。
        records = [record for record in await repository.list_by_type("company_due_diligence_graph_projection") if record.name == task_name]
        record = max(records, key=lambda item: item.created_at)
        task_id = record.id

        winners = await asyncio.gather(
            repository.claim_pending(task_id, "company_due_diligence_graph_projection"),
            repository.claim_pending(task_id, "company_due_diligence_graph_projection"),
        )
        assert sum(item is not None for item in winners) == 1

        assert await repository.request_cancel(task_id) is True
        context = PersistedTaskContext(task_id, repository, record.payload or {})
        with pytest.raises(asyncio.CancelledError):
            await context.raise_if_cancelled()
    finally:
        if task_id:
            await repository.delete(task_id)
