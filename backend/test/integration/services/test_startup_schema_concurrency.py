"""真实 PostgreSQL 上 api / worker 并发执行启动 DDL 不应死锁。

api 与 worker 启动时各自执行建表、版本迁移和运行时 schema 演进。两个进程同时
跑这些 DDL 时，曾因锁升级（先持有 api_keys 的读锁、再请求 skills / api_keys
的排他锁）互相等待而触发 deadlock detected。这里用两个独立进程反复同时启动
来覆盖这一竞争。
"""

from __future__ import annotations

import asyncio
import multiprocessing

import pytest

pytestmark = pytest.mark.integration

ROUNDS = 10


def _startup_schema_loop(barrier, errors) -> None:
    from yuxi.storage.postgres.manager import pg_manager

    async def run() -> None:
        pg_manager.initialize()
        for _ in range(ROUNDS):
            await asyncio.to_thread(barrier.wait)
            # api 与 worker 启动入口的 DDL 并集。
            await pg_manager.create_tables()
            await pg_manager.run_versioned_migrations()
            await pg_manager.ensure_business_schema()
            await pg_manager.ensure_knowledge_schema()
        await pg_manager.async_engine.dispose()

    try:
        asyncio.run(run())
    except Exception as exc:
        errors.put(repr(exc))
        barrier.abort()


def test_concurrent_startup_schema_does_not_deadlock():
    ctx = multiprocessing.get_context("spawn")
    barrier = ctx.Barrier(2)
    errors = ctx.Queue()
    processes = [ctx.Process(target=_startup_schema_loop, args=(barrier, errors)) for _ in range(2)]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=120)

    failures = []
    while not errors.empty():
        failures.append(errors.get())
    assert failures == []
    assert [process.exitcode for process in processes] == [0, 0]
