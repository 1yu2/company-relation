"""平台级评测数据集、实验和 Fixture Runner 用例服务。"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from typing import Any

from yuxi.evaluation.metrics import aggregate_pass_metrics, evaluate_task
from yuxi.evaluation.provenance import TraceEvent, checksum_for, redact_value
from yuxi.repositories.platform_evaluation_repository import PlatformEvaluationRepository
from yuxi.storage.postgres.models_evaluation import PlatformEvaluationTask
from yuxi.utils.datetime_utils import utc_now_naive
from yuxi.utils.hash_utils import hash_id

TERMINAL_EXPERIMENT_STATUSES = {"FINISHED", "FAILED", "CANCELLED"}
ALLOWED_SOURCE_KINDS = {"agent", "tool_mcp", "rag", "graph", "fixture"}


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
        return {"items": [_serialize(item) for item in items], "next_cursor": next_cursor}

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
        if not await repo.list_tasks(version_id):
            raise ValueError("数据集版本至少需要一条任务")
        published = await repo.update_version(
            version_id,
            {"status": "published", "published_at": utc_now_naive()},
        )
        return _serialize(published)

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
        config_snapshot = redact_value(dict(data.get("config_snapshot") or {}))
        experiment = await repo.create_experiment(
            {
                "experiment_id": hash_id("pex_", uuid.uuid4().hex, length=32),
                "dataset_version_id": version.version_id,
                "source_kind": source_kind,
                "agent_slug": data.get("agent_slug"),
                "runner": str(data.get("runner") or "fixture"),
                "evaluator_version": str(data.get("evaluator_version") or "deterministic-v1"),
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

    async def run_experiment(self, *, db, experiment_id: str) -> dict[str, Any]:
        repo = self._repo(db)
        experiment = await repo.get_experiment(experiment_id)
        if experiment is None:
            raise LookupError("实验不存在")
        if experiment.status in TERMINAL_EXPERIMENT_STATUSES:
            return await self.experiment_summary(db=db, experiment_id=experiment_id)
        if experiment.status not in {"DRAFT", "QUEUED", "RUNNING", "AGGREGATING"}:
            raise ValueError(f"实验状态不可运行: {experiment.status}")
        await repo.update_experiment(
            experiment_id, {"status": "RUNNING", "started_at": experiment.started_at or utc_now_naive()}
        )
        tasks = await repo.list_tasks(experiment.dataset_version_id)
        for task in tasks:
            current = await repo.get_experiment(experiment_id)
            if current is None:
                raise LookupError("实验不存在")
            if current.status in {"CANCELLING", "CANCELLED"}:
                return await self._finish_cancelled(db=db, experiment_id=experiment_id)
            for attempt in range(1, experiment.max_attempts + 1):
                key = checksum_for(
                    {
                        "experiment_id": experiment_id,
                        "task_id": task.task_id,
                        "attempt": attempt,
                        "runner": experiment.runner,
                    }
                )
                if await repo.get_rollout_by_idempotency(key):
                    continue
                await self._run_one_rollout(db=db, experiment=experiment, task=task, attempt=attempt, key=key)
        await repo.update_experiment(experiment_id, {"status": "AGGREGATING"})
        summary = await self.experiment_summary(db=db, experiment_id=experiment_id)
        await repo.update_experiment(experiment_id, {"status": "FINISHED", "completed_at": utc_now_naive()})
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
        for rollout in await repo.list_rollouts(experiment_id):
            if rollout.status not in {"COMPLETED", "FAILED", "TIMEOUT", "CANCELLED"}:
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
        return await self._finish_cancelled(db=db, experiment_id=experiment_id)

    async def resume_incomplete_experiments(self, *, db) -> list[dict[str, Any]]:
        """读取数据库中的未终态实验并继续执行，供 Worker/重启恢复入口调用。"""
        experiments = await self._repo(db).list_experiments()
        resumed = []
        for experiment in experiments:
            if experiment.status in {"QUEUED", "RUNNING", "AGGREGATING"}:
                resumed.append(await self.run_experiment(db=db, experiment_id=experiment.experiment_id))
        return resumed

    async def _finish_cancelled(self, *, db, experiment_id: str) -> dict[str, Any]:
        await self._repo(db).update_experiment(experiment_id, {"status": "CANCELLED", "completed_at": utc_now_naive()})
        summary = await self.experiment_summary(db=db, experiment_id=experiment_id)
        summary["status"] = "CANCELLED"
        return summary

    async def _run_one_rollout(self, *, db, experiment, task, attempt: int, key: str) -> None:
        repo = self._repo(db)
        rollout_id = hash_id("pro_", uuid.uuid4().hex, length=32)
        started = utc_now_naive()
        await repo.create_rollout(
            {
                "rollout_id": rollout_id,
                "experiment_id": experiment.experiment_id,
                "task_id": task.task_id,
                "attempt": attempt,
                "idempotency_key": key,
                "status": "RUNNING",
                "started_at": started,
            }
        )
        try:
            actual, events = await asyncio.wait_for(_run_fixture(task, rollout_id), timeout=experiment.timeout_seconds)
            metric_values = evaluate_task(_task_dict(task), actual, events)
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
            await repo.update_rollout(
                rollout_id,
                {
                    "status": "TIMEOUT",
                    "failure_type": "timeout",
                    "reason_code": "timeout",
                    "finished_at": utc_now_naive(),
                },
            )
            await repo.save_failures(
                [_failure_row({"reason_code": "timeout", "metric_name": "task_pass"}, rollout_id, None)]
            )
        except Exception as exc:
            await repo.update_rollout(
                rollout_id,
                {
                    "status": "FAILED",
                    "failure_type": "runner_error",
                    "reason_code": "runner_error",
                    "error_message": str(exc),
                    "finished_at": utc_now_naive(),
                },
            )
            await repo.save_failures(
                [_failure_row({"reason_code": "runner_error", "metric_name": "task_pass"}, rollout_id, None)]
            )

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
                "task_pass": bool(metric.value) if metric else False,
                "score": metric.value if metric else 0,
            }
            for rollout in rollouts
            for metric in [task_pass_by_rollout.get(rollout.rollout_id)]
        ]
        return _serialize(experiment) | {
            "rollouts": [_serialize(item) for item in rollouts],
            "metrics": [_serialize(item) for item in metrics],
            "failures": [_serialize(item) for item in failures],
            "aggregate": aggregate_pass_metrics(attempts, k=experiment.max_attempts),
        }


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
