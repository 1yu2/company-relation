"""线上抽样评估：定期抽取真实聊天 Run，跑不依赖标准答案的通用规则，命中的写入失败池，人工复核后回流为草稿题。

抽样按 Run ID 的哈希决定（同一 Run 每次都得到同一结论，重复扫描不会重复计数），抽样率由
PLATFORM_EVAL_ONLINE_SAMPLE_RATE 配置，默认 0 即不抽样。失败池只保存 Run 标识、命中规则、计数和点踩信息，
不复制对话内容；回流为草稿题时才读取用户问题，题目标记 pending_review，人工复核后才能发布。
"""

from __future__ import annotations

import hashlib
import os
import uuid
from collections import Counter
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select

from yuxi.evaluation.agent_final_answer import LOOP_REPEAT_THRESHOLD, canonical_tool_args
from yuxi.evaluation.agent_runner import load_run_tool_calls
from yuxi.storage.postgres.models_business import AgentRun, Message, MessageFeedback
from yuxi.storage.postgres.models_evaluation import PlatformEvaluationOnlineFinding
from yuxi.utils.datetime_utils import utc_now_naive
from yuxi.utils.hash_utils import hash_id

SAMPLE_RATE_ENV = "PLATFORM_EVAL_ONLINE_SAMPLE_RATE"
# 默认抽样 10% 的线上 Run：只跑确定性规则、不调用模型，没有额外费用；设为 0 可关闭。
DEFAULT_SAMPLE_RATE = 0.1
SAMPLED_SOURCE = "chat"
# interrupted 是等待人工审批或回答（human_approval_required 等），Run 尚未结束，不是失败，不抽样。
SAMPLED_STATUSES = ("completed", "failed")
TOOL_CALL_BUDGET = 20
# 定时任务每小时运行一次；多回看一段时间，覆盖上次运行前后刚结束的 Run，重复的由 run_id 唯一约束去重。
LOOKBACK = timedelta(hours=2)


async def sample_online_runs(
    db, *, now: datetime | None = None, sample_rate: float | None = None, lookback: timedelta = LOOKBACK
) -> dict[str, Any]:
    """扫描回看窗口内结束的聊天 Run，按抽样率抽样并写入命中规则的样本，返回抽样统计。

    定时任务使用默认回看窗口；一次性回补历史数据时可以传入更长的 lookback。
    """
    rate = float(os.getenv(SAMPLE_RATE_ENV) or DEFAULT_SAMPLE_RATE) if sample_rate is None else sample_rate
    if not 0 <= rate <= 1:
        raise ValueError(f"{SAMPLE_RATE_ENV} 必须在 0 到 1 之间: {rate}")
    now = now or utc_now_naive()
    runs = (
        await db.execute(
            select(AgentRun).where(
                AgentRun.source == SAMPLED_SOURCE,
                AgentRun.status.in_(SAMPLED_STATUSES),
                AgentRun.finished_at >= now - lookback,
                AgentRun.finished_at < now,
            )
        )
    ).scalars()
    sampled = [run for run in runs if is_sampled(run.id, rate)]
    existing = set(
        (
            await db.execute(
                select(PlatformEvaluationOnlineFinding.run_id).where(
                    PlatformEvaluationOnlineFinding.run_id.in_([run.id for run in sampled])
                )
            )
        ).scalars()
    )
    hits_by_rule: Counter[str] = Counter()
    created = 0
    for run in sampled:
        if run.id in existing:
            continue
        tool_calls = await load_run_tool_calls(db, run.id)
        output = await db.get(Message, run.output_message_id) if run.output_message_id else None
        feedback = (
            (
                await db.execute(
                    select(MessageFeedback)
                    .where(MessageFeedback.message_id == run.output_message_id, MessageFeedback.rating == "dislike")
                    .order_by(MessageFeedback.id)
                )
            )
            .scalars()
            .first()
        )
        rule_hits = check_rules(
            run_status=run.status,
            final_answer=output.content if output else None,
            tool_calls=tool_calls,
            disliked=feedback is not None,
        )
        if not rule_hits:
            continue
        hits_by_rule.update(rule_hits)
        db.add(
            PlatformEvaluationOnlineFinding(
                finding_id=hash_id("pof_", uuid.uuid4().hex, length=32),
                run_id=run.id,
                agent_slug=run.agent_slug,
                run_status=run.status,
                rule_hits=rule_hits,
                summary={
                    "tool_calls": len(tool_calls),
                    "tool_errors": sum(1 for call in tool_calls if call["status"] == "error"),
                },
                feedback={"rating": feedback.rating, "reason": feedback.reason} if feedback else None,
                run_finished_at=run.finished_at,
            )
        )
        created += 1
    await db.commit()
    return {
        "sample_rate": rate,
        "sampled": len(sampled),
        "findings_created": created,
        "hits_by_rule": dict(hits_by_rule),
    }


def is_sampled(run_id: str, rate: float) -> bool:
    """Run ID 哈希映射到 [0, 1)，小于抽样率即被抽中；同一 Run 的结论不随扫描次数变化。"""
    return int(hashlib.sha256(run_id.encode()).hexdigest()[:8], 16) / 0x100000000 < rate


def check_rules(
    *, run_status: str, final_answer: str | None, tool_calls: list[dict[str, Any]], disliked: bool
) -> list[str]:
    """不依赖标准答案的通用规则，返回命中的规则代码。"""
    repeats = Counter((call["name"], canonical_tool_args(call["args"])) for call in tool_calls)
    rules = [
        ("run_failed", run_status == "failed"),
        ("final_answer_missing", run_status == "completed" and not (final_answer or "").strip()),
        ("tool_call_loop", max(repeats.values(), default=0) >= LOOP_REPEAT_THRESHOLD),
        ("tool_budget_exceeded", len(tool_calls) > TOOL_CALL_BUDGET),
        ("tool_error", any(call["status"] == "error" for call in tool_calls)),
        ("user_disliked", disliked),
    ]
    return [code for code, hit in rules if hit]


async def promote_findings(db, *, service, dataset_id: str, finding_ids: list[str]) -> dict[str, Any]:
    """把失败池样本生成为数据集的草稿版本：只带用户问题和来源，期望待人工补写，发布门禁拦截未复核的题。"""
    findings = (
        (
            await db.execute(
                select(PlatformEvaluationOnlineFinding).where(
                    PlatformEvaluationOnlineFinding.finding_id.in_(finding_ids),
                    PlatformEvaluationOnlineFinding.status == "pending_review",
                )
            )
        )
        .scalars()
        .all()
    )
    if len(findings) != len(set(finding_ids)):
        raise ValueError("部分样本不存在或已处理")
    tasks = []
    for finding in findings:
        run = await db.get(AgentRun, finding.run_id)
        question = await db.get(Message, run.input_message_id) if run and run.input_message_id else None
        if question is None:
            raise ValueError(f"Run {finding.run_id} 的用户问题已不存在")
        tasks.append(
            {
                "task_key": f"online-{finding.run_id}",
                "protocol": "agent_final_answer_v1",
                "tags": ["online_sample", *finding.rule_hits],
                "input": {"query": question.content},
                "expected": {
                    "review_status": "pending_review",
                    "source_run_id": finding.run_id,
                    "rule_hits": finding.rule_hits,
                },
            }
        )
    version = await service.create_version(
        db=db, dataset_id=dataset_id, source={"type": "online_sample", "finding_ids": sorted(finding_ids)}, tasks=tasks
    )
    for finding in findings:
        finding.status = "promoted"
        finding.promoted_version_id = version["version_id"]
    await db.commit()
    return version


async def list_findings(db, *, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    """按时间倒序列出失败池样本。"""
    statement = select(PlatformEvaluationOnlineFinding).order_by(PlatformEvaluationOnlineFinding.created_at.desc())
    if status:
        statement = statement.where(PlatformEvaluationOnlineFinding.status == status)
    rows = (await db.execute(statement.limit(limit))).scalars()
    return [
        {
            column.name: value.isoformat() if isinstance(value, datetime) else value
            for column in row.__table__.columns
            for value in [getattr(row, column.name)]
        }
        for row in rows
    ]
