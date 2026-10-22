"""平台级评测数据集、实验和 Fixture Runner 用例服务。"""

from __future__ import annotations

import asyncio
import inspect
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from functools import partial
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker

from yuxi.evaluation.agent_final_answer import METRIC_VERSION as AGENT_FINAL_ANSWER_METRIC_VERSION
from yuxi.evaluation.agent_final_answer import evaluate_agent_final_answer
from yuxi.evaluation.agent_runner import (
    CDD_AGENT_PROTOCOL,
    AgentRunnerError,
    cancel_agent_runs_for_rollouts,
    run_agent_task,
)
from yuxi.evaluation.cdd_agent_adapter import ADAPTER_VERSION as CDD_AGENT_ADAPTER_VERSION
from yuxi.evaluation.company_due_diligence import METRIC_VERSION as CDD_METRIC_VERSION
from yuxi.evaluation.company_due_diligence import evaluate_company_due_diligence
from yuxi.evaluation.fault_recovery import METRIC_VERSION as FAULT_RECOVERY_METRIC_VERSION
from yuxi.evaluation.fault_recovery import evaluate_fault_recovery
from yuxi.evaluation.llm_judge import METRIC_VERSION as LLM_JUDGE_METRIC_VERSION
from yuxi.evaluation.llm_judge import evaluate_open_answer
from yuxi.evaluation.metrics import (
    METRIC_VERSION,
    aggregate_fault_recovery,
    aggregate_metric_breakdown,
    aggregate_pass_metrics,
    aggregate_rollout_performance,
    compare_paired_outcomes,
    estimate_cost,
    evaluate_task,
)
from yuxi.evaluation.rag_answer import METRIC_VERSION as RAG_ANSWER_METRIC_VERSION
from yuxi.evaluation.rag_answer import evaluate_rag_answer
from yuxi.evaluation.provenance import TraceEvent, checksum_for, redact_value
from yuxi.evaluation.snapshot import capture_agent_snapshot, capture_common_snapshot, validate_pricing
from yuxi.repositories.platform_evaluation_repository import PlatformEvaluationRepository
from yuxi.services.run_queue_service import get_arq_pool, get_redis_client
from yuxi.storage.postgres.models_evaluation import PlatformEvaluationTask
from yuxi.utils.datetime_utils import utc_now_naive
from yuxi.utils.hash_utils import hash_id

TERMINAL_EXPERIMENT_STATUSES = {"FINISHED", "FAILED", "CANCELLED"}
RUNNABLE_EXPERIMENT_STATUSES = {"DRAFT", "QUEUED", "RUNNING", "AGGREGATING"}
EXPERIMENT_JOB_NAME = "process_platform_evaluation_experiment"
# 执行锁 TTL：心跳每 1/3 TTL 续期；恢复作业延迟同样时长，等待崩溃进程遗留的锁过期。
EXPERIMENT_LOCK_TTL_SECONDS = 30
# 计为任务失败的 runner 原因：属于被测 Agent 自身的问题。取消、提交被拒、runner 异常属于评测侧，不评分。
AGENT_FAILURE_REASONS = {"timeout", "agent_run_failed", "agent_run_interrupted"}
# 锁在最后一次心跳续期后 TTL 秒过期；恢复作业多等一段余量，确保不会在旧锁过期前启动而被跳过。
RECOVERY_DEFER_MARGIN_SECONDS = 10
# fixture/agent_run 实验按任务协议选择结果 evaluator；企业尽调 runner 固定使用自己的 evaluator（本轮冻结）。
# 值为 (evaluator, metric_version)；metric_version 写入实验快照，对比实验时据此判断评分口径是否一致。
PROTOCOL_EVALUATORS = {
    "agent_tool_final_v1": (evaluate_task, METRIC_VERSION),
    "agent_final_answer_v1": (evaluate_agent_final_answer, AGENT_FINAL_ANSWER_METRIC_VERSION),
    "agent_fault_recovery_v1": (evaluate_fault_recovery, FAULT_RECOVERY_METRIC_VERSION),
    "agent_rag_answer_v1": (evaluate_rag_answer, RAG_ANSWER_METRIC_VERSION),
    "agent_open_answer_v1": (evaluate_open_answer, LLM_JUDGE_METRIC_VERSION),
    # 企业尽调 Agent：runner 先把 Run 转成企业尽调字段，评分沿用确定性 runner 的评分器和版本，便于逐题对比。
    CDD_AGENT_PROTOCOL: (lambda task, actual, events: evaluate_company_due_diligence(task, actual), CDD_METRIC_VERSION),
}
ALLOWED_SOURCE_KINDS = {"agent", "tool_mcp", "rag", "graph", "fixture", "company_due_diligence"}


class ExperimentLockLostError(RuntimeError):
    """执行中途失去实验执行锁；继续执行可能与接手的执行者重复跑同一批 rollout。"""


class PlatformEvaluationService:
    """编排平台评测资源和无副作用 Fixture 执行。"""

    def __init__(self, repository: PlatformEvaluationRepository | None = None):
        self.repository = repository

    def _repo(self, db) -> PlatformEvaluationRepository:
        return self.repository or PlatformEvaluationRepository(db)

    async def create_dataset(
        self, *, db, name: str, description: str, source_kind: str, created_by: str
    ) -> dict[str, Any]:
        source_kind = _validate_source_kind(source_kind)
        dataset = await self._repo(db).create_dataset(
            {
                "dataset_id": hash_id("ped_", uuid.uuid4().hex, length=32),
                "name": name.strip(),
                "description": description,
                "source_kind": source_kind,
                "created_by": str(created_by),
            }
        )
        return _serialize(dataset)

    async def list_datasets(self, *, db, source_kind: str | None = None) -> list[dict[str, Any]]:
        if source_kind:
            source_kind = _validate_source_kind(source_kind)
        return [_serialize(item) for item in await self._repo(db).list_datasets(source_kind=source_kind)]

    async def list_datasets_page(
        self, *, db, source_kind: str | None = None, limit: int = 50, cursor: str | None = None
    ) -> dict[str, Any]:
        """查询数据集游标分页。"""
        if source_kind:
            source_kind = _validate_source_kind(source_kind)
        items, next_cursor = await self._repo(db).list_datasets_page(
            source_kind=source_kind, limit=limit, cursor=cursor
        )
        repo = self._repo(db)
        serialized: list[dict[str, Any]] = []
        for item in items:
            versions = await repo.list_versions(item.dataset_id)
            latest = versions[0] if versions else None
            payload = _serialize(item)
            if latest is not None:
                payload.update(
                    {
                        "latest_version": latest.version_id,
                        "latest_version_status": latest.status,
                        "item_count": len(await repo.list_tasks(latest.version_id)),
                    }
                )
            else:
                payload.update({"latest_version": None, "latest_version_status": None, "item_count": 0})
            serialized.append(payload)
        return {"items": serialized, "next_cursor": next_cursor}

    async def get_dataset(self, *, db, dataset_id: str) -> dict[str, Any] | None:
        item = await self._repo(db).get_dataset(dataset_id)
        return _serialize(item) if item else None

    async def create_version(
        self,
        *,
        db,
        dataset_id: str,
        source: dict[str, Any],
        tasks: list[dict[str, Any]],
    ) -> dict[str, Any]:
        repo = self._repo(db)
        dataset = await repo.get_dataset(dataset_id)
        if dataset is None:
            raise LookupError("数据集不存在")
        versions = await repo.list_versions(dataset_id)
        version_no = max((item.version_no for item in versions), default=0) + 1
        normalized_tasks = [_normalize_task(item) for item in tasks]
        task_keys = [item["task_key"] for item in normalized_tasks]
        if len(task_keys) != len(set(task_keys)):
            raise ValueError("同一数据集版本中 task_key 不可重复")
        checksum = checksum_for({"source": source, "tasks": normalized_tasks, "source_kind": dataset.source_kind})
        version = await repo.create_version(
            {
                "version_id": hash_id("pev_", uuid.uuid4().hex, length=32),
                "dataset_id": dataset_id,
                "version_no": version_no,
                "checksum": checksum,
                "source": redact_value(source),
            }
        )
        await repo.create_tasks(
            [
                {
                    "task_id": hash_id("pet_", f"{version.version_id}:{item['task_key']}", length=32),
                    "version_id": version.version_id,
                    **item,
                }
                for item in normalized_tasks
            ]
        )
        return _serialize(version) | {"task_count": len(normalized_tasks)}

    async def publish_version(self, *, db, version_id: str) -> dict[str, Any]:
        repo = self._repo(db)
        version = await repo.get_version(version_id)
        if version is None:
            raise LookupError("数据集版本不存在")
        if version.status == "published":
            return _serialize(version)
        if version.status != "draft":
            raise ValueError("数据集版本状态不可发布")
        tasks = await repo.list_tasks(version_id)
        if not tasks:
            raise ValueError("数据集版本至少需要一条任务")
        # 候选题（企业尽调候选集、线上抽样回流题）即使已填写部分期望，只要仍是 pending_review 就不能发布。
        if any((task.expected or {}).get("review_status") == "pending_review" for task in tasks):
            raise ValueError("存在尚未复核的任务，不能发布")
        # 期望由被测服务自己的输出生成：用它评测这个服务等于自己评自己，必然高分，不能作为评测基线。
        if any((task.expected or {}).get("review_status") == "source_generated" for task in tasks):
            raise ValueError("存在由被测服务输出生成期望的任务，不能作为评测基线发布")
        domain_tasks = [task for task in tasks if task.protocol == "cdd_q02_review_v1"]
        if any(not (task.expected or {}).get("status") for task in domain_tasks):
            raise ValueError("企业尽调任务缺少可比较的参考状态")
        published = await repo.update_version(
            version_id,
            {"status": "published", "published_at": utc_now_naive()},
        )
        return _serialize(published)

    async def retire_version(self, *, db, version_id: str) -> dict[str, Any]:
        """停用已发布的版本：不能再基于它创建实验；已有实验和对比保持不变，便于回查历史结论。"""
        repo = self._repo(db)
        version = await repo.get_version(version_id)
        if version is None:
            raise LookupError("数据集版本不存在")
        if version.status == "retired":
            return _serialize(version)
        if version.status != "published":
            raise ValueError("只有已发布的数据集版本可以停用")
        return _serialize(await repo.update_version(version_id, {"status": "retired"}))

    async def get_version(self, *, db, version_id: str) -> dict[str, Any] | None:
        version = await self._repo(db).get_version(version_id)
        if version is None:
            return None
        tasks = await self._repo(db).list_tasks(version_id)
        return _serialize(version) | {"tasks": [_serialize(item) for item in tasks]}

    async def list_versions(self, *, db, dataset_id: str) -> list[dict[str, Any]]:
        """查询数据集的全部版本。"""
        repo = self._repo(db)
        if await repo.get_dataset(dataset_id) is None:
            raise LookupError("数据集不存在")
        return [_serialize(item) for item in await repo.list_versions(dataset_id)]

    async def create_experiment(self, *, db, data: dict[str, Any], created_by: str) -> dict[str, Any]:
        repo = self._repo(db)
        version = await repo.get_version(str(data["dataset_version_id"]))
        if version is None:
            raise LookupError("数据集版本不存在")
        if version.status != "published":
            raise ValueError("只能基于已发布的数据集版本创建实验")
        dataset = await repo.get_dataset(version.dataset_id)
        source_kind = _validate_source_kind(str(data.get("source_kind") or dataset.source_kind))
        runner = str(data.get("runner") or "fixture")
        if source_kind != dataset.source_kind:
            raise ValueError("实验来源必须与数据集来源一致")
        if runner not in {"fixture", "company_due_diligence", "agent_run"}:
            raise ValueError(f"不支持的评测 runner: {runner}")
        if runner == "agent_run" and not data.get("agent_slug"):
            raise ValueError("agent_run runner 需要 agent_slug")
        tasks = await repo.list_tasks(version.version_id)
        domain_tasks = any(task.protocol == "cdd_q02_review_v1" for task in tasks)
        if (source_kind == "company_due_diligence" or domain_tasks) and runner != "company_due_diligence":
            raise ValueError("企业尽调评测只能使用 company_due_diligence runner")
        unknown_protocols = {task.protocol for task in tasks} - PROTOCOL_EVALUATORS.keys()
        if runner != "company_due_diligence" and unknown_protocols:
            raise ValueError(f"没有对应 evaluator 的任务协议: {', '.join(sorted(unknown_protocols))}")
        if runner == "company_due_diligence":
            if source_kind != "company_due_diligence":
                raise ValueError("企业尽调 runner 需要企业尽调来源数据集")
            if any((task.input or {}).get("topic") == "report" for task in tasks):
                raise ValueError("当前只读企业问答 runner 不支持报告题，请选择问答题版本")
        config_snapshot = redact_value(dict(data.get("config_snapshot") or {}))
        if "pricing" in config_snapshot:
            config_snapshot["pricing"] = validate_pricing(config_snapshot["pricing"])
        evaluator_versions = _evaluator_versions(runner, tasks)
        # 服务端采集的事实统一放在 captured 下，调用方传入的同名字段不会覆盖它。
        captured = capture_common_snapshot(version=version, tasks=tasks, evaluator_versions=evaluator_versions)
        if runner == "agent_run":
            # 评测 Run 没有人在线审批，默认信任沙盒工具，并显式写入快照以便复现。
            config_snapshot.setdefault("tool_approval_mode", "always_trust")
            captured["agent"] = await capture_agent_snapshot(
                db,
                agent_slug=str(data["agent_slug"]),
                requested_model_spec=config_snapshot.get("model_spec"),
                creator_uid=str(created_by),
            )
            if any(task.protocol == CDD_AGENT_PROTOCOL for task in tasks):
                captured["cdd_agent_adapter"] = CDD_AGENT_ADAPTER_VERSION
        config_snapshot["captured"] = captured
        experiment = await repo.create_experiment(
            {
                "experiment_id": hash_id("pex_", uuid.uuid4().hex, length=32),
                "dataset_version_id": version.version_id,
                "source_kind": source_kind,
                "agent_slug": data.get("agent_slug"),
                "runner": runner,
                # 由本实验实际使用的评分器推导，调用方不能另行指定；按协议的明细见 captured.evaluators。
                "evaluator_version": ",".join(sorted(set(evaluator_versions.values()))),
                "config_snapshot": config_snapshot,
                "concurrency": max(1, min(int(data.get("concurrency") or 1), 32)),
                "timeout_seconds": max(1, min(int(data.get("timeout_seconds") or 30), 3600)),
                "max_attempts": max(1, min(int(data.get("max_attempts") or 1), 20)),
                "created_by": str(created_by),
            }
        )
        return _serialize(experiment)

    async def list_experiments(self, *, db, source_kind: str | None = None) -> list[dict[str, Any]]:
        if source_kind:
            source_kind = _validate_source_kind(source_kind)
        return [_serialize(item) for item in await self._repo(db).list_experiments(source_kind=source_kind)]

    async def list_experiments_page(
        self, *, db, source_kind: str | None = None, limit: int = 50, cursor: str | None = None
    ) -> dict[str, Any]:
        """查询实验游标分页。"""
        if source_kind:
            source_kind = _validate_source_kind(source_kind)
        items, next_cursor = await self._repo(db).list_experiments_page(
            source_kind=source_kind, limit=limit, cursor=cursor
        )
        return {"items": [_serialize(item) for item in items], "next_cursor": next_cursor}

    async def get_experiment(self, *, db, experiment_id: str) -> dict[str, Any] | None:
        experiment = await self._repo(db).get_experiment(experiment_id)
        return _serialize(experiment) if experiment else None

    async def list_rollouts(self, *, db, experiment_id: str) -> list[dict[str, Any]]:
        """查询实验的逐题尝试。"""
        repo = self._repo(db)
        if await repo.get_experiment(experiment_id) is None:
            raise LookupError("实验不存在")
        return [_serialize(item) for item in await repo.list_rollouts(experiment_id)]

    async def list_rollouts_page(
        self, *, db, experiment_id: str, limit: int = 100, cursor: str | None = None
    ) -> dict[str, Any]:
        """查询实验 rollout；rollout 使用创建时间游标，保持 task/attempt 稳定排序。"""
        if await self._repo(db).get_experiment(experiment_id) is None:
            raise LookupError("实验不存在")
        items, next_cursor = await self._repo(db).list_rollouts_page(experiment_id, limit=limit, cursor=cursor)
        return {"items": [_serialize(item) for item in items], "next_cursor": next_cursor}

    async def get_rollout_trace(self, *, db, rollout_id: str) -> dict[str, Any] | None:
        """查询单次尝试和其脱敏事件。"""
        repo = self._repo(db)
        rollout = await repo.get_rollout(rollout_id)
        if rollout is None:
            return None
        return {
            "rollout": _serialize(rollout),
            "events": [_serialize(item) for item in await repo.list_trace_events(rollout_id)],
        }

    async def list_metrics(self, *, db, experiment_id: str) -> list[dict[str, Any]]:
        """查询实验逐条指标。"""
        if await self._repo(db).get_experiment(experiment_id) is None:
            raise LookupError("实验不存在")
        return [_serialize(item) for item in await self._repo(db).list_metrics(experiment_id)]

    async def list_failures(self, *, db, experiment_id: str) -> list[dict[str, Any]]:
        """查询实验失败原因。"""
        if await self._repo(db).get_experiment(experiment_id) is None:
            raise LookupError("实验不存在")
        return [_serialize(item) for item in await self._repo(db).list_failures(experiment_id)]

    async def start_experiment(self, *, db, experiment_id: str) -> dict[str, Any]:
        """把实验标记为排队并投递到 worker；先提交数据库状态，再投递 ARQ。"""
        repo = self._repo(db)
        experiment = await repo.get_experiment(experiment_id)
        if experiment is None:
            raise LookupError("实验不存在")
        # FAILED 可以重新运行：已完成的题按幂等键跳过，只补跑未完成的题。
        if experiment.status in TERMINAL_EXPERIMENT_STATUSES - {"FAILED"}:
            return await self.experiment_summary(db=db, experiment_id=experiment_id)
        if experiment.status not in RUNNABLE_EXPERIMENT_STATUSES | {"FAILED"}:
            raise ValueError(f"实验状态不可运行: {experiment.status}")
        if experiment.status in {"DRAFT", "FAILED"}:
            await repo.update_experiment(experiment_id, {"status": "QUEUED", "error_message": None})
            await db.commit()
        # 运行中的实验也允许再次投递：卡住时可以从页面继续，执行锁保证不会出现第二个执行者。
        await enqueue_experiment(experiment_id)
        return await self.experiment_summary(db=db, experiment_id=experiment_id)

    async def run_experiment(self, *, db, experiment_id: str) -> dict[str, Any]:
        """在 worker 中执行实验：最多 concurrency 条 rollout 同时执行，每条单独提交，中断后可按幂等键续跑。"""
        repo = self._repo(db)
        experiment = await repo.get_experiment(experiment_id)
        if experiment is None:
            raise LookupError("实验不存在")
        if experiment.status in TERMINAL_EXPERIMENT_STATUSES:
            return await self.experiment_summary(db=db, experiment_id=experiment_id)
        if experiment.status not in RUNNABLE_EXPERIMENT_STATUSES:
            raise ValueError(f"实验状态不可运行: {experiment.status}")
        tasks = await repo.list_tasks(experiment.dataset_version_id)
        # 创建实验之后评分器升级过：继续执行会让新版本的分数挂在旧快照下，对比时按快照判断口径会混比新旧分数。
        snapshot_versions = (experiment.config_snapshot.get("captured") or {}).get("evaluators")
        current_versions = _evaluator_versions(experiment.runner, tasks)
        if snapshot_versions is not None and snapshot_versions != current_versions:
            raise ValueError(
                f"评分器版本与创建实验时不同（创建时 {snapshot_versions}，当前 {current_versions}），请重新创建实验"
            )
        await repo.update_experiment(
            experiment_id, {"status": "RUNNING", "started_at": experiment.started_at or utc_now_naive()}
        )
        await db.commit()
        pending = []
        for task in tasks:
            for attempt in range(1, experiment.max_attempts + 1):
                key = checksum_for(
                    {
                        "experiment_id": experiment_id,
                        "task_id": task.task_id,
                        "attempt": attempt,
                        "runner": experiment.runner,
                    }
                )
                existing = await repo.get_rollout_by_idempotency(key)
                # RUNNING 说明上次执行在该题中途中断：复用同一 rollout 重跑；其余状态已是终态结果。
                if existing is None or existing.status == "RUNNING":
                    pending.append((task, attempt, key))

        # 每条 rollout 使用同一引擎上的独立会话：并发的 rollout 各自提交，互不共享 ORM 状态。
        # TaskGroup 中任一条 rollout 抛出未处理异常时，其余在途 rollout 一并取消，不会在后台继续执行。
        open_session = async_sessionmaker(db.bind, expire_on_commit=False)
        slots = asyncio.Semaphore(experiment.concurrency)

        async def run_pending(task, attempt: int, key: str) -> None:
            async with slots, open_session() as session:
                # 取消请求到达后，尚未开始的 rollout 不再启动；在途 rollout 由取消流程中止 AgentRun。
                if await _cancel_requested(self._repo(session), experiment_id):
                    return
                await self._run_one_rollout(db=session, experiment=experiment, task=task, attempt=attempt, key=key)

        try:
            async with asyncio.TaskGroup() as group:
                for task, attempt, key in pending:
                    group.create_task(run_pending(task, attempt, key))
        except BaseExceptionGroup as errors:
            # 抛出首条 rollout 的原始异常：worker 记录到实验的是真实原因，而不是 TaskGroup 的包装信息。
            raise errors.exceptions[0] from None
        # rollout 结果写在各自的会话里；主会话中派发时加载的对象已过期，汇总前丢弃。
        db.expire_all()
        # 取消可能落在最后一题执行期间：此时不能再汇总为 FINISHED 覆盖取消状态。
        if await _cancel_requested(repo, experiment_id):
            return await self._finish_cancelled(db=db, experiment_id=experiment_id)
        await repo.update_experiment(experiment_id, {"status": "AGGREGATING"})
        summary = await self.experiment_summary(db=db, experiment_id=experiment_id)
        await repo.update_experiment(experiment_id, {"status": "FINISHED", "completed_at": utc_now_naive()})
        await db.commit()
        summary["status"] = "FINISHED"
        return summary

    async def cancel_experiment(self, *, db, experiment_id: str) -> dict[str, Any]:
        """请求并完成实验取消，清理所有未终态 rollout。"""
        repo = self._repo(db)
        experiment = await repo.get_experiment(experiment_id)
        if experiment is None:
            raise LookupError("实验不存在")
        if experiment.status in TERMINAL_EXPERIMENT_STATUSES:
            return await self.experiment_summary(db=db, experiment_id=experiment_id)
        if experiment.status not in {"DRAFT", "QUEUED", "RUNNING", "AGGREGATING", "CANCELLING"}:
            raise ValueError(f"实验状态不可取消: {experiment.status}")
        await repo.update_experiment(experiment_id, {"status": "CANCELLING"})
        in_flight = []
        for rollout in await repo.list_rollouts(experiment_id):
            if rollout.status not in {"COMPLETED", "FAILED", "TIMEOUT", "CANCELLED"}:
                in_flight.append(rollout.rollout_id)
                await repo.update_rollout(
                    rollout.rollout_id,
                    {
                        "status": "CANCELLED",
                        "failure_type": "cancelled",
                        "reason_code": "cancelled",
                        "finished_at": utc_now_naive(),
                    },
                )
                await repo.save_failures(
                    [_failure_row({"reason_code": "cancelled", "metric_name": "task_pass"}, rollout.rollout_id, None)]
                )
        if experiment.runner == "agent_run":
            # 在途题的 AgentRun 也要取消：否则 Agent 会继续执行到结束，后台执行者也要一直等待它。
            await cancel_agent_runs_for_rollouts(db, in_flight)
        return await self._finish_cancelled(db=db, experiment_id=experiment_id)

    async def mark_failed(self, *, db, experiment_id: str, error_message: str) -> None:
        """后台作业异常退出时把实验标记为 FAILED，避免停在 RUNNING；之后可以重新运行。"""
        await self._repo(db).update_experiment(
            experiment_id, {"status": "FAILED", "error_message": error_message[:2000], "completed_at": utc_now_naive()}
        )

    async def resume_incomplete_experiments(self, *, db) -> list[str]:
        """worker 启动时重新投递未终态实验；已完成的 rollout 由幂等键跳过。

        恢复作业延迟到崩溃进程遗留的执行锁过期之后；若原作业仍在执行或排队，后到的作业拿不到锁或
        看到实验已终态时会直接结束。
        """
        experiments = await self._repo(db).list_experiments_by_status({"QUEUED", "RUNNING", "AGGREGATING"})
        for experiment in experiments:
            await enqueue_experiment(experiment.experiment_id, recovery=True)
        return [experiment.experiment_id for experiment in experiments]

    async def _finish_cancelled(self, *, db, experiment_id: str) -> dict[str, Any]:
        await self._repo(db).update_experiment(experiment_id, {"status": "CANCELLED", "completed_at": utc_now_naive()})
        summary = await self.experiment_summary(db=db, experiment_id=experiment_id)
        summary["status"] = "CANCELLED"
        return summary

    async def _run_one_rollout(self, *, db, experiment, task, attempt: int, key: str) -> None:
        repo = self._repo(db)
        started = utc_now_naive()
        rollout = await repo.create_rollout(
            {
                "rollout_id": hash_id("pro_", uuid.uuid4().hex, length=32),
                "experiment_id": experiment.experiment_id,
                "task_id": task.task_id,
                "attempt": attempt,
                "idempotency_key": key,
                "status": "RUNNING",
                "started_at": started,
            }
        )
        rollout_id = rollout.rollout_id
        await db.commit()
        # 派发前的取消检查与创建 rollout 之间有窗口：取消恰好在这期间完成时看不到这条 rollout。
        # 创建后再查一次，按取消流程收尾；此后到达的取消能看到这条 RUNNING rollout，由“取消优先”处理。
        if await _cancel_requested(repo, experiment.experiment_id):
            await repo.update_rollout(
                rollout_id,
                {
                    "status": "CANCELLED",
                    "failure_type": "cancelled",
                    "reason_code": "cancelled",
                    "finished_at": utc_now_naive(),
                },
            )
            await repo.save_failures(
                [_failure_row({"reason_code": "cancelled", "metric_name": "task_pass"}, rollout_id, None)]
            )
            await db.commit()
            return
        try:
            if experiment.runner == "fixture":
                runner = _run_fixture
            elif experiment.runner == "company_due_diligence":
                runner = _run_company_due_diligence
            elif experiment.runner == "agent_run":
                runner = partial(run_agent_task, experiment)
            else:
                raise ValueError(f"不支持的评测 runner: {experiment.runner}")
            actual, events = await asyncio.wait_for(runner(task, rollout_id), timeout=experiment.timeout_seconds)
            if await _rollout_cancelled(repo, rollout_id):
                await db.commit()
                return
            metric_values = (
                evaluate_company_due_diligence(_task_dict(task), actual)
                if experiment.runner == "company_due_diligence"
                else PROTOCOL_EVALUATORS[task.protocol][0](_task_dict(task), actual, events)
            )
            # LLM Judge 类 evaluator 需要调用模型，是协程；确定性 evaluator 直接返回结果。
            if inspect.isawaitable(metric_values):
                metric_values = await metric_values
            task_pass = bool(metric_values[-1]["value"])
            duration_ms = (utc_now_naive() - started).total_seconds() * 1000
            await repo.update_rollout(
                rollout_id,
                {
                    "status": "COMPLETED",
                    "actual_output": redact_value(actual),
                    "output_summary": redact_value(actual),
                    "reason_code": None if task_pass else metric_values[-1]["reason_code"],
                    "duration_ms": round(duration_ms, 3),
                    "finished_at": utc_now_naive(),
                },
            )
            event_rows = [_event_row(event, rollout_id) for event in events]
            metric_rows = [_metric_row(item, rollout_id, task, actual) for item in metric_values]
            await repo.save_trace_events(event_rows)
            saved_metrics = await repo.save_metrics(metric_rows)
            failures = [
                _failure_row(
                    item,
                    rollout_id,
                    saved_metrics[index].metric_id,
                    trace_event_id=event_rows[-1]["event_id"] if event_rows else None,
                )
                for index, item in enumerate(metric_values)
                if item["status"] == "failed"
            ]
            if failures:
                await repo.save_failures(failures)
        except TimeoutError:
            if await _rollout_cancelled(repo, rollout_id):
                await db.commit()
                return
            await _record_runner_failure(repo, experiment, task, rollout_id, "TIMEOUT", "timeout", "timeout", None)
        except Exception as exc:
            if await _rollout_cancelled(repo, rollout_id):
                await db.commit()
                return
            reason_code = exc.reason_code if isinstance(exc, AgentRunnerError) else "runner_error"
            await _record_runner_failure(
                repo, experiment, task, rollout_id, "FAILED", "runner_error", reason_code, str(exc)
            )
        await db.commit()

    async def experiment_summary(self, *, db, experiment_id: str) -> dict[str, Any]:
        repo = self._repo(db)
        experiment = await repo.get_experiment(experiment_id)
        if experiment is None:
            raise LookupError("实验不存在")
        rollouts = await repo.list_rollouts(experiment_id)
        tasks = await repo.list_tasks(experiment.dataset_version_id)
        task_keys = {task.task_id: task.task_key for task in tasks}
        metrics = await repo.list_metrics(experiment_id)
        failures = await repo.list_failures(experiment_id)
        task_pass_by_rollout = {item.rollout_id: item for item in metrics if item.metric_name == "task_pass"}
        attempts = [
            {
                "task_key": task_keys.get(rollout.task_id, rollout.task_id),
                "attempt": rollout.attempt,
                "task_pass": None
                if metric is None or metric.status in {"not_measured", "unavailable"}
                else bool(metric.value),
                "status": metric.status if metric else "not_measured",
                "score": metric.value if metric and metric.value is not None else 0,
            }
            for rollout in rollouts
            for metric in [task_pass_by_rollout.get(rollout.rollout_id)]
        ]
        performance = aggregate_rollout_performance([_serialize(item) for item in rollouts])
        performance["estimated_cost"] = estimate_cost(performance["usage"], experiment.config_snapshot.get("pricing"))
        return _serialize(experiment) | {
            "rollouts": [_serialize(item) for item in rollouts],
            "metrics": [_serialize(item) for item in metrics],
            "failures": [_serialize(item) for item in failures],
            "aggregate": aggregate_pass_metrics(attempts, k=experiment.max_attempts),
            "performance": performance,
            "metric_breakdown": aggregate_metric_breakdown([_serialize(item) for item in metrics]),
            "fault_recovery": aggregate_fault_recovery([_serialize(item) for item in metrics]),
        }

    async def compare_experiments(self, *, db, base_experiment_id: str, candidate_experiment_id: str) -> dict[str, Any]:
        """逐题对比两次实验：只允许同一数据集版本、同一评分口径，结果包含修复/退化、显著性和性能差异。"""
        base = await self.experiment_summary(db=db, experiment_id=base_experiment_id)
        candidate = await self.experiment_summary(db=db, experiment_id=candidate_experiment_id)
        if base["dataset_version_id"] != candidate["dataset_version_id"]:
            raise ValueError("两次实验的数据集版本不同，不能逐题对比")
        base_versions, candidate_versions = _scoring_versions(base), _scoring_versions(candidate)
        if not base_versions or not candidate_versions:
            raise ValueError("实验还没有评分结果，不能逐题对比")
        if base_versions != candidate_versions:
            raise ValueError(f"两次实验的评分口径不同: {sorted(base_versions)} vs {sorted(candidate_versions)}")
        tasks = await self._repo(db).list_tasks(base["dataset_version_id"])
        task_keys = {task.task_id: task.task_key for task in tasks}
        return {
            "base_experiment_id": base_experiment_id,
            "candidate_experiment_id": candidate_experiment_id,
            "dataset_version_id": base["dataset_version_id"],
            "metric_versions": sorted(base_versions),
            "outcomes": compare_paired_outcomes(
                _first_attempt_outcomes(base, task_keys), _first_attempt_outcomes(candidate, task_keys)
            ),
            "performance_delta": _performance_delta(base["performance"], candidate["performance"]),
            "config_diff": _config_diff(base["config_snapshot"], candidate["config_snapshot"]),
        }


async def enqueue_experiment(experiment_id: str, *, recovery: bool = False) -> None:
    """投递实验作业；同一实验是否真正执行由 experiment_executor_lock 保证唯一。

    每次投递都用新的 Job ID：稳定 ID 会被中断作业残留的 ARQ 键静默去重，导致卡住的实验无法再次投递。
    重复投递是安全的，拿不到执行锁或实验已终态的作业会直接结束。
    启动恢复额外延迟锁 TTL 加余量，让崩溃进程遗留的执行锁先过期。
    """
    job_id = f"platform-eval:{experiment_id}:{uuid.uuid4().hex}"
    defer_by = timedelta(seconds=EXPERIMENT_LOCK_TTL_SECONDS + RECOVERY_DEFER_MARGIN_SECONDS) if recovery else None
    queue = await get_arq_pool()
    await queue.enqueue_job(EXPERIMENT_JOB_NAME, experiment_id, _job_id=job_id, _defer_by=defer_by)


@asynccontextmanager
async def experiment_executor_lock(
    experiment_id: str, *, ttl_seconds: int = EXPERIMENT_LOCK_TTL_SECONDS
) -> AsyncIterator[bool]:
    """同一实验同一时刻只允许一个执行者；返回是否拿到锁。

    锁带 TTL，由心跳每 1/3 TTL 续期；执行者进程崩溃后心跳停止，锁在 TTL 内自动过期，恢复作业即可接手。
    心跳续期失败时锁可能已过期、其他执行者可以接手：立即取消本执行者并抛出 ExperimentLockLostError，
    而不是继续执行到释放时才发现，期间与新执行者重复跑同一批 rollout。
    """
    redis = await get_redis_client()
    lock = redis.lock(f"platform-eval:executor:{experiment_id}", timeout=ttl_seconds)
    if not await lock.acquire(blocking=False):
        yield False
        return
    owner = asyncio.current_task()
    stopped_by_heartbeat: list[bool] = []
    heartbeat = asyncio.create_task(_keep_lock_alive(lock, ttl_seconds))
    stop_owner = partial(_stop_owner_on_lock_loss, owner, stopped_by_heartbeat)
    heartbeat.add_done_callback(stop_owner)
    try:
        yield True
    finally:
        # 先摘掉回调：心跳恰好在退出时失败、回调尚未执行的话，不能在离开上下文后再取消 owner。
        heartbeat.remove_done_callback(stop_owner)
        # 心跳是无限循环，未被取消就结束只可能是续期失败；锁已丢失，不能再释放。
        if heartbeat.done() and not heartbeat.cancelled():
            if stopped_by_heartbeat:
                owner.uncancel()
            # 只有心跳发出的取消转换为 ExperimentLockLostError；外部取消（如 worker 关闭）同时到达时，
            # 保留正在传播的 CancelledError，让 ARQ 按取消处理、实验保持 RUNNING，由启动恢复续跑。
            if not owner.cancelling():
                raise ExperimentLockLostError(
                    f"实验 {experiment_id} 的执行锁续期失败，已停止执行"
                ) from heartbeat.exception()
        else:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            await lock.release()


def _stop_owner_on_lock_loss(owner: asyncio.Task, stopped: list[bool], heartbeat: asyncio.Task) -> None:
    if not heartbeat.cancelled():
        stopped.append(True)
        owner.cancel()


async def _keep_lock_alive(lock, ttl_seconds: int) -> None:
    while True:
        await asyncio.sleep(ttl_seconds / 3)
        await lock.reacquire()


async def _cancel_requested(repo: PlatformEvaluationRepository, experiment_id: str) -> bool:
    experiment = await repo.get_experiment(experiment_id)
    if experiment is None:
        raise LookupError("实验不存在")
    return experiment.status in {"CANCELLING", "CANCELLED"}


async def _record_runner_failure(
    repo: PlatformEvaluationRepository,
    experiment,
    task: PlatformEvaluationTask,
    rollout_id: str,
    status: str,
    failure_type: str,
    reason_code: str,
    error_message: str | None,
) -> None:
    """记录没有拿到可评分结果的 rollout。

    Agent 自身的失败（超时、Run 失败或中断）是被测对象的问题，计为 task_pass=failed，进入通过率与对比；
    评测基础设施错误（提交被拒、runner 自身异常）不评分，单独记为失败原因，避免把评测故障算到 Agent 头上。
    """
    await repo.update_rollout(
        rollout_id,
        {
            "status": status,
            "failure_type": failure_type,
            "reason_code": reason_code,
            "error_message": error_message,
            "finished_at": utc_now_naive(),
        },
    )
    metric_id = None
    if reason_code in AGENT_FAILURE_REASONS:
        metric = {
            "metric_name": "task_pass",
            "metric_version": _evaluator_version(experiment, task),
            "value": 0.0,
            "status": "failed",
            "reason_code": reason_code,
        }
        saved = await repo.save_metrics([_metric_row(metric, rollout_id, task, {})])
        metric_id = saved[0].metric_id
    await repo.save_failures(
        [_failure_row({"reason_code": reason_code, "metric_name": "task_pass"}, rollout_id, metric_id)]
    )


def _evaluator_version(experiment, task: PlatformEvaluationTask) -> str:
    if experiment.runner == "company_due_diligence":
        return CDD_METRIC_VERSION
    return PROTOCOL_EVALUATORS[task.protocol][1]


async def _rollout_cancelled(repo: PlatformEvaluationRepository, rollout_id: str) -> bool:
    """实验在本题执行期间被取消时，取消优先：丢弃晚到的结果，避免覆盖已写入的取消状态。"""
    rollout = await repo.get_rollout(rollout_id)
    return rollout is not None and rollout.status == "CANCELLED"


def _evaluator_versions(runner: str, tasks: list[PlatformEvaluationTask]) -> dict[str, str]:
    """本实验各协议实际使用的评分器版本；创建实验时写入快照，执行前用它核对评分器是否升级过。"""
    if runner == "company_due_diligence":
        return {task.protocol: CDD_METRIC_VERSION for task in tasks}
    return {task.protocol: PROTOCOL_EVALUATORS[task.protocol][1] for task in tasks}


def _scoring_versions(summary: dict[str, Any]) -> set[str]:
    """评分口径以创建实验时快照记录的评分器版本为准；快照之前的旧实验退回到已落库的 task_pass 指标版本。"""
    captured = (summary["config_snapshot"].get("captured") or {}).get("evaluators")
    if captured:
        return set(captured.values())
    return {item["metric_version"] for item in summary["metrics"] if item["metric_name"] == "task_pass"}


def _first_attempt_outcomes(summary: dict[str, Any], task_keys: dict[str, str]) -> dict[str, str]:
    """取每题第 1 次尝试的 task_pass 状态；没有评分的 rollout（runner 失败）记为 not_measured。"""
    status_by_rollout = {
        item["rollout_id"]: item["status"] for item in summary["metrics"] if item["metric_name"] == "task_pass"
    }
    return {
        task_keys[rollout["task_id"]]: status_by_rollout.get(rollout["rollout_id"], "not_measured")
        for rollout in summary["rollouts"]
        if rollout["attempt"] == 1
    }


def _performance_delta(base: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    """列出关键性能指标在两次实验中的取值和差值（candidate - base）。"""
    fields = {
        "agent_total_p50_ms": ("latency_ms", "agent_total", "p50"),
        "agent_total_mean_ms": ("latency_ms", "agent_total", "mean"),
        "agent_total_max_ms": ("latency_ms", "agent_total", "max"),
        "tokens_per_rollout_mean": ("usage", "total_per_rollout", "mean"),
        "llm_calls_per_rollout_mean": ("usage", "llm_calls_per_rollout", "mean"),
        "tool_calls_per_rollout_mean": ("tool_calls", "per_rollout", "mean"),
    }
    delta = {}
    for name, path in fields.items():
        values = []
        for performance in (base, candidate):
            value = performance
            for key in path:
                value = value.get(key) if isinstance(value, dict) else None
            values.append(value)
        delta[name] = {
            "base": values[0],
            "candidate": values[1],
            "delta": values[1] - values[0] if None not in values else None,
        }
    return delta


def _config_diff(base: dict[str, Any], candidate: dict[str, Any]) -> list[dict[str, Any]]:
    """展开两份配置快照，列出取值不同的字段；采集时间和备注不算配置差异。"""
    ignored = {"captured.captured_at", "note"}
    flat_base, flat_candidate = _flatten(base), _flatten(candidate)
    return [
        {"key": key, "base": flat_base.get(key), "candidate": flat_candidate.get(key)}
        for key in sorted(flat_base.keys() | flat_candidate.keys())
        if key not in ignored and flat_base.get(key) != flat_candidate.get(key)
    ]


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    if not isinstance(value, dict):
        return {prefix: value}
    flat: dict[str, Any] = {}
    for key, item in value.items():
        flat.update(_flatten(item, f"{prefix}.{key}" if prefix else str(key)))
    return flat


def _validate_source_kind(source_kind: str) -> str:
    normalized = source_kind.strip().lower()
    if normalized not in ALLOWED_SOURCE_KINDS:
        raise ValueError(f"不支持的评测来源: {source_kind}")
    return normalized


def _normalize_task(item: dict[str, Any]) -> dict[str, Any]:
    task_key = str(item.get("task_key") or "").strip()
    if not task_key:
        raise ValueError("任务缺少 task_key")
    return {
        "task_key": task_key,
        "input": dict(item.get("input") or {}),
        "expected": dict(item.get("expected") or {}),
        "protocol": str(item.get("protocol") or "agent_tool_final_v1"),
        "tags": list(item.get("tags") or []),
    }


def _task_dict(task: PlatformEvaluationTask) -> dict[str, Any]:
    return {
        "task_key": task.task_key,
        "input": task.input or {},
        "expected": task.expected or {},
        "protocol": task.protocol,
    }


async def _run_company_due_diligence(
    task: PlatformEvaluationTask, rollout_id: str
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """执行只读企业尽调查询，返回业务响应和最小轨迹。"""
    from yuxi.company_due_diligence.qa.models import CompanyQuestionRequest
    from yuxi.company_due_diligence.qa.service import company_question_service

    input_data = task.input or {}
    response = await company_question_service.query(
        CompanyQuestionRequest(
            company_ref=input_data.get("company_reference") or None,
            question=str(input_data.get("query") or ""),
            enable_external_fallback=False,
            **{key: input_data[key] for key in ("start_date", "as_of", "top_k", "hop_limit") if key in input_data},
        )
    )
    events = [
        {
            "event_id": hash_id("pev_", f"{rollout_id}:{index}", length=32),
            "sequence": index,
            "component": "company_due_diligence",
            "operation": step.operation,
            "parent_id": None,
            "input_summary": {"source": step.source},
            "output_summary": {"status": step.status},
            "reason_code": None,
        }
        for index, step in enumerate(response.steps, start=1)
    ]
    return response.model_dump(mode="json"), events


async def _run_fixture(task: PlatformEvaluationTask, rollout_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    input_data = task.input or {}
    query = str(input_data.get("query") or "")
    behavior = str(input_data.get("fixture_behavior") or "success")
    if behavior == "timeout":
        await asyncio.sleep(3600)
    operation = "fixture.echo"
    if behavior == "forbidden_tool":
        operation = "filesystem.write"
    final_answer = f"fixture-ok: {query}"
    if behavior == "answer_mismatch":
        final_answer = "fixture-failed"
    root_event_id = hash_id("pev_", f"{rollout_id}:1", length=32)
    events = [
        {
            "event_id": root_event_id,
            "sequence": 1,
            "component": "agent",
            "operation": "start",
            "parent_id": None,
            "input_summary": {"query": query},
            "output_summary": None,
            "reason_code": None,
        },
        {
            "event_id": hash_id("pev_", f"{rollout_id}:2", length=32),
            "sequence": 2,
            "component": "tool",
            "operation": operation,
            "parent_id": root_event_id,
            "input_summary": {"query": query},
            "output_summary": {"ok": operation == "fixture.echo"},
            "reason_code": None,
        },
        {
            "event_id": hash_id("pev_", f"{rollout_id}:3", length=32),
            "sequence": 3,
            "component": "final",
            "operation": "answer",
            "parent_id": root_event_id,
            "input_summary": None,
            "output_summary": {"final_answer": final_answer},
            "reason_code": None,
        },
    ]
    return {"final_answer": final_answer}, events


def _event_row(event: dict[str, Any], rollout_id: str) -> dict[str, Any]:
    trace = TraceEvent(rollout_id=rollout_id, occurred_at=utc_now_naive(), checksum=None, **event)
    return trace.to_dict() | {"occurred_at": trace.occurred_at}


def _metric_row(
    metric: dict[str, Any], rollout_id: str, task: PlatformEvaluationTask, actual: dict[str, Any]
) -> dict[str, Any]:
    return {
        "metric_id": hash_id("pem_", f"{rollout_id}:{metric['metric_name']}", length=32),
        "rollout_id": rollout_id,
        "metric_name": metric["metric_name"],
        "metric_version": metric["metric_version"],
        "value": metric["value"],
        "status": metric["status"],
        "reason_code": metric["reason_code"],
        "input_checksum": checksum_for({"task": _task_dict(task), "actual": actual}),
        "details": metric,
    }


def _failure_row(
    metric: dict[str, Any], rollout_id: str, metric_id: str | None, trace_event_id: str | None = None
) -> dict[str, Any]:
    reason_code = str(metric.get("reason_code") or "evaluation_failed")
    failure_type = "policy_violation" if "forbidden" in reason_code else "evaluation_error"
    return {
        "failure_id": hash_id("pef_", f"{rollout_id}:{metric.get('metric_name')}:{reason_code}", length=32),
        "rollout_id": rollout_id,
        "metric_id": metric_id,
        "trace_event_id": trace_event_id,
        "failure_type": failure_type,
        "reason_code": reason_code,
        "message": reason_code,
        "retryable": reason_code in {"timeout", "runner_error"},
    }


def _serialize(record: Any) -> dict[str, Any]:
    if record is None:
        return {}
    values = {}
    for column in record.__table__.columns:
        value = getattr(record, column.name)
        if isinstance(value, datetime):
            value = value.isoformat()
        values[column.name] = value
    return values
