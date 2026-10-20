"""线上抽样：通用规则、按 Run ID 的稳定抽样、失败池写入去重，以及回流草稿题被发布门禁拦截。"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from yuxi.evaluation import online_sampling
from yuxi.evaluation.experiment_service import PlatformEvaluationService
from yuxi.storage.postgres.models_business import AgentRun, Base, Conversation, Message, MessageFeedback, ToolCall
from yuxi.storage.postgres.models_evaluation import (
    PlatformEvaluationDataset,
    PlatformEvaluationDatasetVersion,
    PlatformEvaluationOnlineFinding,
    PlatformEvaluationTask,
)

pytestmark = pytest.mark.unit
NOW = datetime(2026, 10, 20, 12, 0, 0)
EXECUTE = {"name": "execute", "args": {"command": "ls"}, "status": "success"}


@pytest.mark.parametrize(
    ("run_status", "final_answer", "tool_calls", "disliked", "expected"),
    [
        ("completed", "答案", [EXECUTE], False, []),
        ("failed", None, [], False, ["run_failed"]),
        ("completed", "  ", [], False, ["final_answer_missing"]),
        ("completed", "答案", [EXECUTE] * 3, False, ["tool_call_loop"]),
        (
            "completed",
            "答案",
            [{**EXECUTE, "args": {"command": str(i)}} for i in range(21)],
            False,
            ["tool_budget_exceeded"],
        ),
        ("completed", "答案", [{**EXECUTE, "status": "error"}], True, ["tool_error", "user_disliked"]),
    ],
)
def test_check_rules(run_status, final_answer, tool_calls, disliked, expected):
    assert (
        online_sampling.check_rules(
            run_status=run_status, final_answer=final_answer, tool_calls=tool_calls, disliked=disliked
        )
        == expected
    )


def test_sampling_is_stable_per_run_and_close_to_rate():
    run_ids = [f"run-{index}" for index in range(10000)]
    sampled = [run_id for run_id in run_ids if online_sampling.is_sampled(run_id, 0.1)]

    assert 900 < len(sampled) < 1100
    assert sampled == [run_id for run_id in run_ids if online_sampling.is_sampled(run_id, 0.1)]
    assert not any(online_sampling.is_sampled(run_id, 0.0) for run_id in run_ids[:100])
    assert all(online_sampling.is_sampled(run_id, 1.0) for run_id in run_ids[:100])


@pytest.fixture
async def sampling_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    tables = [
        Conversation.__table__,
        AgentRun.__table__,
        Message.__table__,
        ToolCall.__table__,
        MessageFeedback.__table__,
        PlatformEvaluationDataset.__table__,
        PlatformEvaluationDatasetVersion.__table__,
        PlatformEvaluationTask.__table__,
        PlatformEvaluationOnlineFinding.__table__,
    ]
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync_connection: Base.metadata.create_all(sync_connection, tables=tables))
    async with async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)() as session:
        yield session
    await engine.dispose()


async def _chat_run(session, *, status="completed", answer="答案", tool_status="success", disliked=False, minutes=30):
    run_id = str(uuid.uuid4())
    session.add(
        AgentRun(
            id=run_id,
            conversation_thread_id=f"thread-{run_id}",
            agent_slug="default-chatbot",
            uid="u1",
            status=status,
            request_id=f"req-{run_id}",
            source="chat",
            finished_at=NOW - timedelta(minutes=minutes),
        )
    )
    conversation = Conversation(thread_id=f"thread-{run_id}", uid="u1", agent_id="default-chatbot")
    session.add(conversation)
    await session.flush()
    question = Message(conversation_id=conversation.id, role="user", content="昨天 GMV 多少？", run_id=run_id)
    reply = Message(conversation_id=conversation.id, role="assistant", content=answer, run_id=run_id)
    session.add_all([question, reply])
    await session.flush()
    session.add(ToolCall(message_id=reply.id, tool_name="execute", tool_input={"command": "ls"}, status=tool_status))
    if disliked:
        session.add(MessageFeedback(message_id=reply.id, uid="u1", rating="dislike", reason="答错了"))
    run = await session.get(AgentRun, run_id)
    run.input_message_id, run.output_message_id = question.id, reply.id
    await session.commit()
    return run_id


async def test_sampling_writes_rule_hits_once_and_links_dislike(sampling_session):
    healthy = await _chat_run(sampling_session)
    failing = await _chat_run(sampling_session, tool_status="error", disliked=True)
    await _chat_run(sampling_session, status="failed", minutes=300)  # 不在回看窗口内
    await _chat_run(sampling_session, status="interrupted")  # 等待人工审批，Run 尚未结束，不抽样

    stats = await online_sampling.sample_online_runs(sampling_session, now=NOW, sample_rate=1.0)
    again = await online_sampling.sample_online_runs(sampling_session, now=NOW, sample_rate=1.0)

    assert stats == {
        "sample_rate": 1.0,
        "sampled": 2,
        "findings_created": 1,
        "hits_by_rule": {"tool_error": 1, "user_disliked": 1},
    }
    assert again["findings_created"] == 0
    findings = await online_sampling.list_findings(sampling_session)
    assert [item["run_id"] for item in findings] == [failing]
    assert findings[0]["feedback"] == {"rating": "dislike", "reason": "答错了"}
    assert healthy not in {item["run_id"] for item in findings}


async def test_promoted_findings_become_unpublishable_draft(sampling_session):
    run_id = await _chat_run(sampling_session, status="failed", answer="")
    await online_sampling.sample_online_runs(sampling_session, now=NOW, sample_rate=1.0)
    finding_id = (await online_sampling.list_findings(sampling_session))[0]["finding_id"]
    service = PlatformEvaluationService()
    dataset = await service.create_dataset(
        db=sampling_session, name="online", description="", source_kind="agent", created_by="admin"
    )

    version = await online_sampling.promote_findings(
        sampling_session, service=service, dataset_id=dataset["dataset_id"], finding_ids=[finding_id]
    )

    detail = await service.get_version(db=sampling_session, version_id=version["version_id"])
    assert detail["tasks"][0]["input"] == {"query": "昨天 GMV 多少？"}
    assert detail["tasks"][0]["expected"]["source_run_id"] == run_id
    with pytest.raises(ValueError, match="尚未复核"):
        await service.publish_version(db=sampling_session, version_id=version["version_id"])
    with pytest.raises(ValueError, match="已处理"):
        await online_sampling.promote_findings(
            sampling_session, service=service, dataset_id=dataset["dataset_id"], finding_ids=[finding_id]
        )
