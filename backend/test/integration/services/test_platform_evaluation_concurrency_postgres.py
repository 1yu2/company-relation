"""真实 PostgreSQL 上的并发 rollout：独立连接之间的取消、续跑和并发上限。

SQLite 单测只能证明编排分支，事务隔离和连接竞争只能在真实数据库上验证。
测试创建的数据集名以 pytest- 开头，执行后保留在本地库中，与其他集成测试一致。
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from yuxi.evaluation import experiment_service
from yuxi.evaluation.experiment_service import PlatformEvaluationService
from yuxi.storage.postgres.manager import pg_manager

# pg_manager 的连接池是进程级单例，绑定创建它的事件循环；本模块的用例共用同一个循环。
pytestmark = [pytest.mark.asyncio(loop_scope="module"), pytest.mark.integration]


async def _experiment(task_count: int, concurrency: int) -> str:
    service = PlatformEvaluationService()
    async with pg_manager.get_async_session_context() as db:
        dataset = await service.create_dataset(
            db=db,
            name=f"pytest-concurrency-{uuid.uuid4().hex[:8]}",
            description="",
            source_kind="fixture",
            created_by="admin",
        )
        version = await service.create_version(
            db=db,
            dataset_id=dataset["dataset_id"],
            # 版本校验和在全库唯一，题目相同的用例需要不同的 source 才能各自建版本。
            source={"type": "inline", "nonce": uuid.uuid4().hex},
            tasks=[
                {
                    "task_key": f"t{i}",
                    "input": {"query": f"q{i}"},
                    "expected": {"final_answer": {"value": f"fixture-ok: q{i}"}},
                }
                for i in range(task_count)
            ],
        )
        await service.publish_version(db=db, version_id=version["version_id"])
        experiment = await service.create_experiment(
            db=db, data={"dataset_version_id": version["version_id"], "concurrency": concurrency}, created_by="admin"
        )
    return experiment["experiment_id"]


async def _run(experiment_id: str) -> dict:
    async with pg_manager.get_async_session_context() as db:
        return await PlatformEvaluationService().run_experiment(db=db, experiment_id=experiment_id)


async def _summary(experiment_id: str) -> dict:
    async with pg_manager.get_async_session_context() as db:
        return await PlatformEvaluationService().experiment_summary(db=db, experiment_id=experiment_id)


async def test_concurrent_rollouts_respect_limit_and_all_finish(monkeypatch):
    original = experiment_service._run_fixture
    in_flight = peak = 0

    async def slow_fixture(task, rollout_id):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.1)
        in_flight -= 1
        return await original(task, rollout_id)

    monkeypatch.setattr(experiment_service, "_run_fixture", slow_fixture)
    experiment_id = await _experiment(task_count=8, concurrency=4)

    result = await _run(experiment_id)

    assert peak == 4
    assert result["status"] == "FINISHED"
    assert [item["status"] for item in result["rollouts"]] == ["COMPLETED"] * 8
    assert sum(item["metric_name"] == "task_pass" for item in result["metrics"]) == 8


async def test_cancel_from_another_connection_mid_run_leaves_no_scores_on_cancelled_rollouts(monkeypatch):
    original = experiment_service._run_fixture
    started = asyncio.Event()

    async def slow_fixture(task, rollout_id):
        started.set()
        await asyncio.sleep(0.3)
        return await original(task, rollout_id)

    monkeypatch.setattr(experiment_service, "_run_fixture", slow_fixture)
    experiment_id = await _experiment(task_count=8, concurrency=2)
    job = asyncio.create_task(_run(experiment_id))
    await started.wait()
    async with pg_manager.get_async_session_context() as db:
        await PlatformEvaluationService().cancel_experiment(db=db, experiment_id=experiment_id)

    result = await job
    summary = await _summary(experiment_id)

    assert result["status"] == summary["status"] == "CANCELLED"
    scored = {item["rollout_id"] for item in summary["metrics"]}
    cancelled = {item["rollout_id"] for item in summary["rollouts"] if item["status"] == "CANCELLED"}
    assert cancelled, "取消时在途的 rollout 应被标记为 CANCELLED"
    assert not scored & cancelled, "被取消的 rollout 不能留下评分"
    # 并发 2：取消前最多启动 2 条，其余题不再启动
    assert len(summary["rollouts"]) <= 2


async def test_resume_after_interruption_does_not_duplicate_rollouts(monkeypatch):
    class WorkerCrash(BaseException):
        """模拟进程退出：不被 rollout 的 except Exception 捕获。"""

    original = experiment_service._run_fixture
    calls = 0

    async def crash_once(task, rollout_id):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise WorkerCrash
        await asyncio.sleep(0.05)
        return await original(task, rollout_id)

    monkeypatch.setattr(experiment_service, "_run_fixture", crash_once)
    experiment_id = await _experiment(task_count=6, concurrency=2)
    with pytest.raises(WorkerCrash):
        await _run(experiment_id)

    result = await _run(experiment_id)

    assert result["status"] == "FINISHED"
    assert len(result["rollouts"]) == 6
    assert len({item["idempotency_key"] for item in result["rollouts"]}) == 6
    assert [item["status"] for item in result["rollouts"]] == ["COMPLETED"] * 6
