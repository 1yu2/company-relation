"""真实 Redis 上的平台评测执行锁：同一实验只允许一个执行者，崩溃后锁自动过期。"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from yuxi.evaluation.experiment_service import ExperimentLockLostError, experiment_executor_lock
from yuxi.services.run_queue_service import get_redis_client

# 共享的 Redis 客户端是进程级单例，绑定创建它的事件循环；本模块的用例共用同一个循环。
pytestmark = [pytest.mark.asyncio(loop_scope="module"), pytest.mark.integration]


async def test_second_executor_is_rejected_until_first_releases():
    experiment_id = f"pytest-{uuid.uuid4().hex}"
    async with experiment_executor_lock(experiment_id) as first:
        async with experiment_executor_lock(experiment_id) as second:
            assert (first, second) == (True, False)
    async with experiment_executor_lock(experiment_id) as after_release:
        assert after_release is True


async def test_heartbeat_keeps_lock_beyond_ttl_while_executor_is_alive():
    """执行时间超过 TTL 时，心跳续期使后来的执行者仍拿不到锁。"""
    experiment_id = f"pytest-{uuid.uuid4().hex}"
    async with experiment_executor_lock(experiment_id, ttl_seconds=1) as first:
        await asyncio.sleep(2.5)
        async with experiment_executor_lock(experiment_id, ttl_seconds=1) as second:
            assert (first, second) == (True, False)


async def test_lock_left_by_crashed_executor_expires_after_ttl():
    """崩溃进程不会释放锁也不会续期：TTL 过后恢复作业可以接手。"""
    experiment_id = f"pytest-{uuid.uuid4().hex}"
    redis = await get_redis_client()
    orphan = redis.lock(f"platform-eval:executor:{experiment_id}", timeout=1)
    assert await orphan.acquire(blocking=False)

    async with experiment_executor_lock(experiment_id) as while_orphaned:
        assert while_orphaned is False
    await asyncio.sleep(1.5)
    async with experiment_executor_lock(experiment_id) as after_expiry:
        assert after_expiry is True


async def test_executor_stops_within_one_heartbeat_after_losing_lock():
    """锁被删掉（过期或被接手）后，下一次心跳续期失败即取消执行者，而不是跑到结束才发现。"""
    experiment_id = f"pytest-{uuid.uuid4().hex}"
    redis = await get_redis_client()
    loop = asyncio.get_running_loop()
    with pytest.raises(ExperimentLockLostError):
        async with experiment_executor_lock(experiment_id, ttl_seconds=3):
            await redis.delete(f"platform-eval:executor:{experiment_id}")
            started = loop.time()
            await asyncio.sleep(30)
    # 心跳间隔为 TTL/3 = 1 秒。
    assert loop.time() - started < 2
    # 执行者自身没有残留的取消请求，后续标记 FAILED 等收尾操作可以正常 await。
    assert asyncio.current_task().cancelling() == 0
    await asyncio.sleep(0)
