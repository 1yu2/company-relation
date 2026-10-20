"""平台评测的真实 Agent runner：经统一 Run 提交链路执行一题，并把结果整理为评测产物。"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select

from yuxi.agents.backends.sandbox.provider import get_sandbox_provider
from yuxi.repositories.agent_run_repository import TERMINAL_RUN_STATUSES
from yuxi.services.agent_run_service import (
    AgentRunWaitTimeout,
    await_agent_run_result,
    request_cancel_agent_run,
)
from yuxi.services.input_message_service import build_chat_input_message
from yuxi.services.run_submission_service import RunOrigin, RunSubmissionCommand, submit_run_command
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import AgentRun, Message, ToolCall, User
from yuxi.utils.hash_utils import hash_id
from yuxi.utils.logging_config import logger

EVALUATION_RUN_SOURCE = "agent_evaluation"
EVALUATION_RUN_CHANNEL = "platform_eval"


class AgentRunnerError(RuntimeError):
    """真实 Agent 未得到可评分结果；reason_code 写入 rollout 供失败归因。"""

    def __init__(self, reason_code: str, message: str):
        super().__init__(message)
        self.reason_code = reason_code


async def run_agent_task(experiment, task, rollout_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """提交一次评测 Run，等待终态后返回 actual 结果和按执行顺序排列的 Trace 事件。"""
    config = experiment.config_snapshot or {}
    # 固定使用创建实验时解析出的模型：排队或续跑期间改了 Agent 默认模型，也不会让后续题换模型。
    # 快照之前创建的旧实验没有 captured.agent，沿用当时的请求值。
    captured_agent = (config.get("captured") or {}).get("agent")
    model_spec = captured_agent["model_spec"] if captured_agent else config.get("model_spec")
    query = str((task.input or {}).get("query") or "")
    # 续跑会在同一 rollout 上重新执行：每次执行使用独立请求和线程，避免复用中断前的 Run 或对话上下文。
    execution_id = hash_id("peval_", f"{rollout_id}:{uuid.uuid4().hex}", length=32)
    async with pg_manager.get_async_session_context() as db:
        user = (await db.execute(select(User).where(User.uid == experiment.created_by))).scalar_one()
        try:
            submitted = await submit_run_command(
                command=RunSubmissionCommand(
                    agent_slug=experiment.agent_slug,
                    thread_id=execution_id,
                    request_id=execution_id,
                    input_message=build_chat_input_message(query),
                    origin=RunOrigin(
                        source=EVALUATION_RUN_SOURCE,
                        channel=EVALUATION_RUN_CHANNEL,
                        external_id=rollout_id,
                        metadata={
                            "platform_evaluation": {
                                "experiment_id": experiment.experiment_id,
                                "task_key": task.task_key,
                            }
                        },
                    ),
                    model_spec=model_spec,
                    tool_approval_mode=config["tool_approval_mode"],
                    queue_policy="reject",
                    create_conversation=True,
                    conversation_title=f"Platform Evaluation {task.task_key}",
                ),
                current_user=user,
                db=db,
            )
        except Exception as exc:
            raise AgentRunnerError("agent_run_rejected", str(exc)) from exc

    run_id = submitted["run_id"]
    try:
        return await _wait_and_collect(run_id, user.uid, query, rollout_id)
    finally:
        # 每次执行都是新线程、新沙盒；评测线程不会再被使用，结束即释放，避免沙盒容器和 Docker 网络在空闲回收前堆积。
        try:
            await asyncio.to_thread(get_sandbox_provider().release, execution_id, uid=user.uid)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Failed to release evaluation sandbox for thread {execution_id}: {exc}")


async def _wait_and_collect(
    run_id: str, uid: str, query: str, rollout_id: str
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """等待 Run 终态并从 PG 读取工具调用、用量和时间戳，整理为评测产物。"""
    current = asyncio.current_task()
    try:
        while True:
            try:
                result = await await_agent_run_result(run_id=run_id, current_uid=uid)
            except AgentRunWaitTimeout:
                # 事件流结束但 Run 未落终态（如 worker 正在重启）不是评测结果：继续等待，
                # 总时长由实验 timeout_seconds 约束；进程退出时 rollout 保持 RUNNING，恢复后重跑。
                result = None
            # Run 事件流按 SSE 断连语义吞掉 CancelledError；补回实验超时或 worker 退出发出的取消。
            if current.cancelling():
                raise asyncio.CancelledError
            if result is not None:
                break
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        # 实验超时会取消本协程；同步取消 AgentRun，避免评测结束后 Agent 仍在沙盒里执行。
        async with pg_manager.get_async_session_context() as db:
            await request_cancel_agent_run(run_id=run_id, current_uid=uid, db=db)
        raise

    if result["status"] != "completed":
        error = result.get("error") or {}
        raise AgentRunnerError(f"agent_run_{result['status']}", f"run_id={run_id}: {error.get('message') or ''}")

    async with pg_manager.get_async_session_context() as db:
        run = await db.get(AgentRun, run_id)
        rows = (
            await db.execute(
                select(ToolCall)
                .join(Message, ToolCall.message_id == Message.id)
                .where(Message.run_id == run_id)
                .order_by(ToolCall.id)
            )
        ).scalars()
        tool_calls = [
            {
                "name": row.tool_name,
                "args": row.tool_input or {},
                "status": row.status,
                "error": row.error_message,
                "output": row.tool_output,
            }
            for row in rows
        ]
        assistant_metadata = (
            await db.execute(
                select(Message.extra_metadata)
                .where(Message.run_id == run_id, Message.role == "assistant")
                .order_by(Message.id)
            )
        ).scalars()
        usages = [item or {} for item in assistant_metadata]

    actual = {
        "final_answer": result["output"],
        "agent_run_id": run_id,
        "langfuse_trace_id": result.get("langfuse_trace_id"),
        "tool_calls": tool_calls,
        "usage": _usage_summary(usages),
        "latency": _latency(run.created_at, run.started_at, run.finished_at),
    }
    return actual, _trace_events(rollout_id, query, tool_calls, actual["final_answer"])


async def cancel_agent_runs_for_rollouts(db, rollout_ids: list[str]) -> None:
    """取消在途评测题对应的 AgentRun；评测 Run 的 external_id 就是 rollout_id。"""
    if not rollout_ids:
        return
    runs = (
        await db.execute(
            select(AgentRun).where(
                AgentRun.source == EVALUATION_RUN_SOURCE,
                AgentRun.external_id.in_(rollout_ids),
                AgentRun.status.notin_(TERMINAL_RUN_STATUSES),
            )
        )
    ).scalars()
    for run in list(runs):
        await request_cancel_agent_run(run_id=run.id, current_uid=run.uid, db=db)


def _usage_summary(message_metadata: list[dict[str, Any]]) -> dict[str, Any]:
    """汇总本次 Run 中每次 LLM 调用落库的 usage_metadata。

    字段名不含 token：rollout 输出会经过 redact_value，含 token 的键会被当作凭据脱敏。
    """
    usages = [item.get("usage_metadata") or {} for item in message_metadata]
    return {
        "llm_calls": len(message_metadata),
        "input": sum(int(item.get("input_tokens") or 0) for item in usages),
        "output": sum(int(item.get("output_tokens") or 0) for item in usages),
        "total": sum(int(item.get("total_tokens") or 0) for item in usages),
        "models": sorted(
            {str((item.get("response_metadata") or {}).get("model_name")) for item in message_metadata} - {"None"}
        ),
    }


def _latency(created_at: datetime, started_at: datetime | None, finished_at: datetime | None) -> dict[str, Any]:
    """按 AgentRun 时间戳拆分排队、执行和端到端耗时（毫秒）。"""

    def span(start: datetime | None, end: datetime | None) -> float | None:
        return round((end - start).total_seconds() * 1000, 3) if start and end else None

    return {
        "queue_ms": span(created_at, started_at),
        "execution_ms": span(started_at, finished_at),
        "total_ms": span(created_at, finished_at),
    }


def _trace_events(
    rollout_id: str, query: str, tool_calls: list[dict[str, Any]], final_answer: str
) -> list[dict[str, Any]]:
    """生成 agent → tool* → final 的 Trace 事件，结构与 fixture runner 一致。"""
    root_id = hash_id("pev_", f"{rollout_id}:1", length=32)
    steps = [("agent", "start", {"query": query}, None, None)]
    steps += [
        ("tool", item["name"], item["args"], {"status": item["status"], "output": item["output"]}, item["error"])
        for item in tool_calls
    ]
    steps.append(("final", "answer", None, {"final_answer": final_answer}, None))
    return [
        {
            "event_id": root_id if sequence == 1 else hash_id("pev_", f"{rollout_id}:{sequence}", length=32),
            "sequence": sequence,
            "component": component,
            "operation": operation,
            "parent_id": None if sequence == 1 else root_id,
            "input_summary": input_summary,
            "output_summary": output_summary,
            "reason_code": "tool_error" if error else None,
        }
        for sequence, (component, operation, input_summary, output_summary, error) in enumerate(steps, start=1)
    ]
