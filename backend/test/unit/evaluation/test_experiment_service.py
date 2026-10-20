import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from types import SimpleNamespace

from yuxi.evaluation.experiment_service import PlatformEvaluationService
from yuxi.storage.postgres.models_business import Base
from yuxi.storage.postgres.models_evaluation import (
    PlatformEvaluationDataset,
    PlatformEvaluationDatasetVersion,
    PlatformEvaluationExperiment,
    PlatformEvaluationFailure,
    PlatformEvaluationMetricResult,
    PlatformEvaluationRollout,
    PlatformEvaluationTask,
    PlatformEvaluationTraceEvent,
)


@pytest.fixture
async def evaluation_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: Base.metadata.create_all(
                sync_connection,
                tables=[
                    PlatformEvaluationDataset.__table__,
                    PlatformEvaluationDatasetVersion.__table__,
                    PlatformEvaluationTask.__table__,
                    PlatformEvaluationExperiment.__table__,
                    PlatformEvaluationRollout.__table__,
                    PlatformEvaluationTraceEvent.__table__,
                    PlatformEvaluationMetricResult.__table__,
                    PlatformEvaluationFailure.__table__,
                ],
            )
        )
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


@pytest.mark.unit
async def test_fixture_experiment_persists_trace_metrics_and_idempotent_rerun(evaluation_session):
    service = PlatformEvaluationService()
    dataset = await service.create_dataset(
        db=evaluation_session,
        name="fixture",
        description="",
        source_kind="fixture",
        created_by="admin",
    )
    version = await service.create_version(
        db=evaluation_session,
        dataset_id=dataset["dataset_id"],
        source={"type": "inline"},
        tasks=[
            {
                "task_key": "fixture_tool_echo_001",
                "input": {"query": "hello"},
                "expected": {
                    "final_answer": {"value": "fixture-ok: hello"},
                    "tool_policy": {"required": ["fixture.echo"]},
                    "trace_contract": {"required_sequence": ["agent", "tool", "final"]},
                },
            }
        ],
    )
    await service.publish_version(db=evaluation_session, version_id=version["version_id"])
    experiment = await service.create_experiment(
        db=evaluation_session,
        data={"dataset_version_id": version["version_id"], "max_attempts": 2},
        created_by="admin",
    )

    result = await service.run_experiment(db=evaluation_session, experiment_id=experiment["experiment_id"])
    rerun = await service.run_experiment(db=evaluation_session, experiment_id=experiment["experiment_id"])

    assert result["status"] == "FINISHED"
    assert len(result["rollouts"]) == 2
    assert len(result["metrics"]) == 8
    assert result["aggregate"]["pass^k"] == 1.0
    assert len(rerun["rollouts"]) == 2


@pytest.mark.unit
async def test_cancel_draft_experiment_is_terminal_and_rerun_is_idempotent(evaluation_session):
    service = PlatformEvaluationService()
    dataset = await service.create_dataset(
        db=evaluation_session, name="fixture", description="", source_kind="fixture", created_by="admin"
    )
    version = await service.create_version(
        db=evaluation_session,
        dataset_id=dataset["dataset_id"],
        source={"type": "inline"},
        tasks=[{"task_key": "cancel-1", "input": {"query": "hello"}, "expected": {}}],
    )
    await service.publish_version(db=evaluation_session, version_id=version["version_id"])
    experiment = await service.create_experiment(
        db=evaluation_session,
        data={"dataset_version_id": version["version_id"]},
        created_by="admin",
    )

    cancelled = await service.cancel_experiment(db=evaluation_session, experiment_id=experiment["experiment_id"])
    repeated = await service.cancel_experiment(db=evaluation_session, experiment_id=experiment["experiment_id"])

    assert cancelled["status"] == "CANCELLED"
    assert repeated["status"] == "CANCELLED"


async def test_domain_experiment_uses_real_dto_and_persists_quality(evaluation_session, monkeypatch):
    """领域实验必须使用问答 DTO，持久化评分和真实步骤字段。"""
    from yuxi.company_due_diligence.qa.service import company_question_service
    from yuxi.company_due_diligence.qa.models import QueryStep

    seen = []

    async def query(request):
        seen.append(request)
        return SimpleNamespace(
            steps=[QueryStep(source="mysql", operation="registered_capital", status="complete")],
            model_dump=lambda **kwargs: {
                "resolution_status": "resolved",
                "company": {"eid": "test"},
                "facts": [],
                "sources": [],
                "latency": {"total_ms": 2},
            },
        )

    monkeypatch.setattr(company_question_service, "query", query)
    service = PlatformEvaluationService()
    dataset = await service.create_dataset(
        db=evaluation_session, name="domain", description="", source_kind="company_due_diligence", created_by="test"
    )
    version = await service.create_version(
        db=evaluation_session,
        dataset_id=dataset["dataset_id"],
        source={},
        tasks=[
            {
                "task_key": "domain-one",
                "protocol": "cdd_q02_review_v1",
                "input": {"query": "注册资本是多少", "company_reference": {"eid": "test"}},
                "expected": {"status": "resolved", "company": {"eid": "test"}, "review_status": "reviewed"},
            }
        ],
    )
    await service.publish_version(db=evaluation_session, version_id=version["version_id"])
    with pytest.raises(ValueError, match="只能使用 company_due_diligence runner"):
        await service.create_experiment(
            db=evaluation_session, data={"dataset_version_id": version["version_id"]}, created_by="test"
        )
    experiment = await service.create_experiment(
        db=evaluation_session,
        data={"dataset_version_id": version["version_id"], "runner": "company_due_diligence"},
        created_by="test",
    )
    result = await service.run_experiment(db=evaluation_session, experiment_id=experiment["experiment_id"])
    assert result["rollouts"][0]["status"] == "COMPLETED"
    assert result["aggregate"]["pass@1"] == 1
    assert seen[0].enable_external_fallback is False
    assert seen[0].company_ref.eid == "test"
    trace = await service.get_rollout_trace(db=evaluation_session, rollout_id=result["rollouts"][0]["rollout_id"])
    assert trace["events"][0]["operation"] == "registered_capital"


async def test_unreviewed_domain_version_cannot_publish(evaluation_session):
    """候选集只允许浏览，不能发布成正式业务评测。"""
    service = PlatformEvaluationService()
    dataset = await service.create_dataset(
        db=evaluation_session, name="candidate", description="", source_kind="company_due_diligence", created_by="test"
    )
    version = await service.create_version(
        db=evaluation_session,
        dataset_id=dataset["dataset_id"],
        source={},
        tasks=[
            {"task_key": "candidate", "protocol": "cdd_q02_review_v1", "expected": {"review_status": "pending_review"}},
            # 已填写参考状态但仍待复核的题同样不能发布。
            {
                "task_key": "candidate-with-status",
                "protocol": "cdd_q02_review_v1",
                "expected": {"status": "resolved", "review_status": "pending_review"},
            },
        ],
    )
    with pytest.raises(ValueError, match="尚未复核"):
        await service.publish_version(db=evaluation_session, version_id=version["version_id"])

    status_only = await service.create_version(
        db=evaluation_session,
        dataset_id=dataset["dataset_id"],
        source={"case": "status-only"},
        tasks=[
            {
                "task_key": "candidate-with-status",
                "protocol": "cdd_q02_review_v1",
                "expected": {"status": "resolved", "review_status": "pending_review"},
            }
        ],
    )
    with pytest.raises(ValueError, match="尚未复核"):
        await service.publish_version(db=evaluation_session, version_id=status_only["version_id"])


@pytest.fixture
def fake_agent_snapshot(monkeypatch):
    """单测数据库没有 agents 表；替换为固定的 Agent 快照，只验证实验服务的编排。"""
    from yuxi.evaluation import experiment_service

    async def capture(db, *, agent_slug, requested_model_spec, creator_uid):
        return {"agent_slug": agent_slug, "model_spec": requested_model_spec or "provider:default-model"}

    monkeypatch.setattr(experiment_service, "capture_agent_snapshot", capture)


async def _published_agent_version(service, session, expected):
    dataset = await service.create_dataset(
        db=session, name="agent", description="", source_kind="agent", created_by="admin"
    )
    version = await service.create_version(
        db=session,
        dataset_id=dataset["dataset_id"],
        source={"type": "inline"},
        tasks=[
            {"task_key": "py-1", "protocol": "agent_final_answer_v1", "input": {"query": "1+1"}, "expected": expected}
        ],
    )
    await service.publish_version(db=session, version_id=version["version_id"])
    return version


@pytest.mark.unit
async def test_agent_run_experiment_requires_agent_and_records_approval_mode(evaluation_session, fake_agent_snapshot):
    """真实 Agent 实验必须指定 agent_slug，并把无人审批的工具模式写进配置快照。"""
    service = PlatformEvaluationService()
    version = await _published_agent_version(service, evaluation_session, {})

    with pytest.raises(ValueError, match="agent_slug"):
        await service.create_experiment(
            db=evaluation_session,
            data={"dataset_version_id": version["version_id"], "runner": "agent_run"},
            created_by="admin",
        )
    experiment = await service.create_experiment(
        db=evaluation_session,
        data={"dataset_version_id": version["version_id"], "runner": "agent_run", "agent_slug": "default-chatbot"},
        created_by="admin",
    )

    assert experiment["config_snapshot"]["tool_approval_mode"] == "always_trust"
    # 实验级评分版本由实际评分器推导，不再默认写成 deterministic-v1。
    assert experiment["evaluator_version"] == "agent-final-answer-v2"


@pytest.mark.unit
async def test_agent_run_experiment_scores_answer_and_tool_trace(evaluation_session, monkeypatch, fake_agent_snapshot):
    """Agent 结果按工具调用生成 Trace，并复用答案与工具策略评分。"""
    from yuxi.evaluation import agent_runner, experiment_service

    tool_calls = [
        {
            "name": "execute",
            "args": {"command": "python -c 'print(1+1)'"},
            "status": "success",
            "error": None,
            "output": "2",
        }
    ]

    async def fake_run(experiment, task, rollout_id):
        actual = {"final_answer": " 2 ", "tool_calls": tool_calls}
        return actual, agent_runner._trace_events(rollout_id, "1+1", tool_calls, actual["final_answer"])

    monkeypatch.setattr(experiment_service, "run_agent_task", fake_run)
    service = PlatformEvaluationService()
    version = await _published_agent_version(
        service,
        evaluation_session,
        {"final_answer": {"value": "2"}, "tool_policy": {"required": ["execute"], "forbidden": ["write_file"]}},
    )
    experiment = await service.create_experiment(
        db=evaluation_session,
        data={"dataset_version_id": version["version_id"], "runner": "agent_run", "agent_slug": "default-chatbot"},
        created_by="admin",
    )

    result = await service.run_experiment(db=evaluation_session, experiment_id=experiment["experiment_id"])
    trace = await service.get_rollout_trace(db=evaluation_session, rollout_id=result["rollouts"][0]["rollout_id"])

    assert result["aggregate"]["pass@1"] == 1.0
    assert [event["operation"] for event in trace["events"]] == ["start", "execute", "answer"]


@pytest.mark.unit
async def test_agent_run_failure_keeps_runner_reason_code(evaluation_session, monkeypatch, fake_agent_snapshot):
    """Agent Run 未正常完成时，rollout 记录具体原因而不是笼统的 runner_error。"""
    from yuxi.evaluation import agent_runner, experiment_service

    async def fake_run(experiment, task, rollout_id):
        raise agent_runner.AgentRunnerError("agent_run_failed", "run_id=r1: model error")

    monkeypatch.setattr(experiment_service, "run_agent_task", fake_run)
    service = PlatformEvaluationService()
    version = await _published_agent_version(service, evaluation_session, {"final_answer": {"value": "2"}})
    experiment = await service.create_experiment(
        db=evaluation_session,
        data={"dataset_version_id": version["version_id"], "runner": "agent_run", "agent_slug": "default-chatbot"},
        created_by="admin",
    )

    result = await service.run_experiment(db=evaluation_session, experiment_id=experiment["experiment_id"])

    assert result["rollouts"][0]["status"] == "FAILED"
    assert result["rollouts"][0]["reason_code"] == "agent_run_failed"
    assert result["failures"][0]["reason_code"] == "agent_run_failed"
    # Agent 自身失败计为任务失败，进入通过率，而不是作为“未评分”被排除。
    assert result["aggregate"]["pass@1"] == 0.0
    assert result["metric_breakdown"]["task_pass"]["status_counts"] == {"failed": 1}


@pytest.mark.unit
def test_agent_usage_summary_survives_rollout_redaction():
    """token 用量写入 rollout 前会脱敏，汇总字段不能被当成凭据抹掉。"""
    from yuxi.evaluation.agent_runner import _usage_summary
    from yuxi.evaluation.provenance import redact_value

    usage = _usage_summary(
        [
            {
                "usage_metadata": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
                "response_metadata": {"model_name": "model-a"},
            },
            {"usage_metadata": {"input_tokens": 50, "output_tokens": 5, "total_tokens": 55}},
        ]
    )

    assert redact_value(usage) == {"llm_calls": 2, "input": 150, "output": 25, "total": 175, "models": ["model-a"]}


async def _fixture_experiment(service, session, keys=("t1", "t2"), concurrency=1):
    dataset = await service.create_dataset(
        db=session, name="fixture", description="", source_kind="fixture", created_by="admin"
    )
    version = await service.create_version(
        db=session,
        dataset_id=dataset["dataset_id"],
        source={"type": "inline"},
        tasks=[
            {"task_key": key, "input": {"query": key}, "expected": {"final_answer": {"value": f"fixture-ok: {key}"}}}
            for key in keys
        ],
    )
    await service.publish_version(db=session, version_id=version["version_id"])
    return await service.create_experiment(
        db=session,
        data={"dataset_version_id": version["version_id"], "concurrency": concurrency},
        created_by="admin",
    )


@pytest.mark.unit
async def test_concurrency_caps_in_flight_rollouts(evaluation_session, monkeypatch):
    """concurrency=3 时同时执行的 rollout 恰好达到 3 条且不超过 3 条，全部题都完成评分。"""
    import asyncio

    from yuxi.evaluation import experiment_service

    original = experiment_service._run_fixture
    in_flight = 0
    peak = 0

    async def slow_fixture(task, rollout_id):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.05)
        in_flight -= 1
        return await original(task, rollout_id)

    monkeypatch.setattr(experiment_service, "_run_fixture", slow_fixture)
    service = PlatformEvaluationService()
    keys = tuple(f"t{index}" for index in range(1, 8))
    experiment = await _fixture_experiment(service, evaluation_session, keys=keys, concurrency=3)

    result = await service.run_experiment(db=evaluation_session, experiment_id=experiment["experiment_id"])

    assert peak == 3
    assert result["status"] == "FINISHED"
    assert [item["status"] for item in result["rollouts"]] == ["COMPLETED"] * len(keys)
    assert result["aggregate"]["pass@1"] == 1.0


@pytest.mark.unit
async def test_cancel_with_concurrency_discards_in_flight_and_skips_unstarted(evaluation_session, monkeypatch):
    """并发执行中取消：在途 rollout 的晚到结果不写入，尚未开始的题不再启动。"""
    import asyncio

    from yuxi.evaluation import experiment_service

    service = PlatformEvaluationService()
    experiment = await _fixture_experiment(service, evaluation_session, keys=("t1", "t2", "t3", "t4"), concurrency=2)
    original = experiment_service._run_fixture
    executed = []

    async def cancel_while_running(task, rollout_id):
        executed.append(task.task_key)
        await asyncio.sleep(0.02)
        if task.task_key == "t1":
            await service.cancel_experiment(db=evaluation_session, experiment_id=experiment["experiment_id"])
        await asyncio.sleep(0.02)
        return await original(task, rollout_id)

    monkeypatch.setattr(experiment_service, "_run_fixture", cancel_while_running)
    result = await service.run_experiment(db=evaluation_session, experiment_id=experiment["experiment_id"])

    assert sorted(executed) == ["t1", "t2"]
    assert result["status"] == "CANCELLED"
    assert [item["status"] for item in result["rollouts"]] == ["CANCELLED", "CANCELLED"]
    assert result["metrics"] == []


@pytest.mark.unit
async def test_resumed_experiment_reruns_interrupted_rollout_in_place(evaluation_session, monkeypatch):
    """worker 在某题中途退出后续跑：已提交的题不重复执行，中断题复用原 rollout 重跑。"""
    from yuxi.evaluation import experiment_service

    class WorkerCrash(BaseException):
        """模拟进程退出：不被 rollout 的 except Exception 捕获。"""

    original = experiment_service._run_fixture
    executed = []

    async def crash_on_t2(task, rollout_id):
        executed.append((task.task_key, rollout_id))
        if task.task_key == "t2" and len(executed) == 2:
            raise WorkerCrash
        return await original(task, rollout_id)

    monkeypatch.setattr(experiment_service, "_run_fixture", crash_on_t2)
    service = PlatformEvaluationService()
    experiment = await _fixture_experiment(service, evaluation_session)
    with pytest.raises(WorkerCrash):
        await service.run_experiment(db=evaluation_session, experiment_id=experiment["experiment_id"])
    await evaluation_session.rollback()

    progress = await service.list_rollouts(db=evaluation_session, experiment_id=experiment["experiment_id"])
    assert sorted(item["status"] for item in progress) == ["COMPLETED", "RUNNING"]

    result = await service.run_experiment(db=evaluation_session, experiment_id=experiment["experiment_id"])

    assert [key for key, _ in executed] == ["t1", "t2", "t2"]
    assert executed[2][1] == executed[1][1]
    assert result["status"] == "FINISHED"
    assert [item["status"] for item in result["rollouts"]] == ["COMPLETED", "COMPLETED"]


@pytest.mark.unit
async def test_start_experiment_commits_queued_before_enqueue(evaluation_session, monkeypatch):
    """HTTP 触发只写入排队状态并投递 worker，不在请求内执行实验。"""
    from yuxi.evaluation import experiment_service

    enqueued = []

    async def fake_enqueue(experiment_id):
        status = (await service.get_experiment(db=evaluation_session, experiment_id=experiment_id))["status"]
        enqueued.append((experiment_id, status, evaluation_session.in_transaction()))

    monkeypatch.setattr(experiment_service, "enqueue_experiment", fake_enqueue)
    service = PlatformEvaluationService()
    experiment = await _fixture_experiment(service, evaluation_session)

    summary = await service.start_experiment(db=evaluation_session, experiment_id=experiment["experiment_id"])

    assert summary["status"] == "QUEUED"
    assert summary["rollouts"] == []
    assert enqueued[0][:2] == (experiment["experiment_id"], "QUEUED")


@pytest.mark.unit
async def test_resume_incomplete_experiments_enqueues_by_status(evaluation_session, monkeypatch):
    """worker 启动恢复只投递未终态实验，不在启动流程中同步执行。"""
    from yuxi.evaluation import experiment_service

    enqueued = []

    async def fake_enqueue(experiment_id, *, recovery=False):
        enqueued.append((experiment_id, recovery))

    monkeypatch.setattr(experiment_service, "enqueue_experiment", fake_enqueue)
    service = PlatformEvaluationService()
    queued = await _fixture_experiment(service, evaluation_session)
    draft = await service.create_experiment(
        db=evaluation_session, data={"dataset_version_id": queued["dataset_version_id"]}, created_by="admin"
    )
    await service.start_experiment(db=evaluation_session, experiment_id=queued["experiment_id"])
    enqueued.clear()

    resumed = await service.resume_incomplete_experiments(db=evaluation_session)

    assert resumed == [queued["experiment_id"]]
    assert enqueued == [(queued["experiment_id"], True)]
    assert draft["experiment_id"] not in resumed


@pytest.mark.unit
async def test_agent_runner_timeout_cancels_run_even_if_stream_swallows_cancel(monkeypatch):
    """Run 事件流会吞掉 CancelledError；实验超时仍必须结束等待并取消 AgentRun。"""
    import asyncio
    from contextlib import asynccontextmanager

    from yuxi.evaluation import agent_runner

    cancelled_runs = []

    class FakeResult:
        def scalar_one(self):
            return SimpleNamespace(uid="admin")

    class FakeDb:
        async def execute(self, _statement):
            return FakeResult()

    @asynccontextmanager
    async def fake_session():
        yield FakeDb()

    submitted_threads = []

    async def fake_submit(*, command, **_kwargs):
        submitted_threads.append(command.thread_id)
        return {"run_id": "run-1"}

    async def swallowing_wait(**_kwargs):
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            pass
        raise agent_runner.AgentRunWaitTimeout({"status": "running", "agent_run_id": "run-1"})

    async def fake_cancel(*, run_id, current_uid, db, cascade_children):
        cancelled_runs.append((run_id, current_uid, cascade_children))

    monkeypatch.setattr(agent_runner.pg_manager, "get_async_session_context", fake_session)
    monkeypatch.setattr(agent_runner, "submit_run_command", fake_submit)
    monkeypatch.setattr(agent_runner, "await_agent_run_result", swallowing_wait)
    monkeypatch.setattr(agent_runner, "request_cancel_agent_run", fake_cancel)
    released = []
    monkeypatch.setattr(
        agent_runner,
        "get_sandbox_provider",
        lambda: SimpleNamespace(release=lambda thread_id, *, uid: released.append((thread_id, uid))),
    )
    experiment = SimpleNamespace(
        experiment_id="exp-1",
        agent_slug="default-chatbot",
        created_by="admin",
        config_snapshot={"tool_approval_mode": "always_trust"},
    )
    task = SimpleNamespace(task_key="t1", input={"query": "1+1"})

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(agent_runner.run_agent_task(experiment, task, "pro_1"), timeout=0.05)

    # 与 HTTP 取消入口一致级联取消子 Run，被测 Agent 派生的子智能体不会继续执行。
    assert cancelled_runs == [("run-1", "admin", True)]
    # 超时取消后仍释放本次执行线程的沙盒；线程 ID 与提交 Run 时使用的 execution_id 一致。
    assert len(released) == 1
    assert released[0][1] == "admin"
    assert released[0][0] == submitted_threads[0]


@pytest.mark.unit
async def test_cancel_agent_runs_for_rollouts_cascades_to_child_runs(monkeypatch):
    from yuxi.evaluation import agent_runner

    cancelled = []

    async def fake_cancel(*, run_id, current_uid, db, cascade_children):
        cancelled.append((run_id, current_uid, cascade_children))

    class FakeDb:
        async def execute(self, _statement):
            return SimpleNamespace(scalars=lambda: [SimpleNamespace(id="run-1", uid="admin")])

    monkeypatch.setattr(agent_runner, "request_cancel_agent_run", fake_cancel)

    await agent_runner.cancel_agent_runs_for_rollouts(FakeDb(), ["pro_1"])

    assert cancelled == [("run-1", "admin", True)]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("config_snapshot", "expected_model"),
    [
        # 快照固定的是创建时解析出的模型；Agent 默认模型之后改成什么都不影响提交值。
        ({"model_spec": None, "captured": {"agent": {"model_spec": "provider:pinned"}}}, "provider:pinned"),
        # 快照之前创建的旧实验没有 captured.agent，沿用当时的请求值。
        ({"model_spec": "provider:requested"}, "provider:requested"),
    ],
)
async def test_agent_runner_submits_snapshot_model(monkeypatch, config_snapshot, expected_model):
    from contextlib import asynccontextmanager

    from yuxi.evaluation import agent_runner

    class FakeDb:
        async def execute(self, _statement):
            return SimpleNamespace(scalar_one=lambda: SimpleNamespace(uid="admin"))

    @asynccontextmanager
    async def fake_session():
        yield FakeDb()

    submitted_models = []

    async def fake_submit(*, command, **_kwargs):
        submitted_models.append(command.model_spec)
        raise RuntimeError("stop after submit")

    monkeypatch.setattr(agent_runner.pg_manager, "get_async_session_context", fake_session)
    monkeypatch.setattr(agent_runner, "submit_run_command", fake_submit)
    experiment = SimpleNamespace(
        experiment_id="exp-1",
        agent_slug="default-chatbot",
        created_by="admin",
        config_snapshot={"tool_approval_mode": "always_trust", **config_snapshot},
    )

    with pytest.raises(agent_runner.AgentRunnerError):
        await agent_runner.run_agent_task(experiment, SimpleNamespace(task_key="t1", input={"query": "1+1"}), "pro_1")

    assert submitted_models == [expected_model]


@pytest.mark.unit
async def test_experiment_rejects_tasks_without_registered_evaluator(evaluation_session):
    """没有对应 evaluator 的协议在创建实验时就失败，而不是跑完才发现无法评分。"""
    service = PlatformEvaluationService()
    dataset = await service.create_dataset(
        db=evaluation_session, name="agent", description="", source_kind="agent", created_by="admin"
    )
    version = await service.create_version(
        db=evaluation_session,
        dataset_id=dataset["dataset_id"],
        source={"type": "inline"},
        tasks=[{"task_key": "x", "protocol": "unknown_protocol_v1", "input": {"query": "q"}, "expected": {}}],
    )
    await service.publish_version(db=evaluation_session, version_id=version["version_id"])

    with pytest.raises(ValueError, match="unknown_protocol_v1"):
        await service.create_experiment(
            db=evaluation_session,
            data={"dataset_version_id": version["version_id"], "runner": "agent_run", "agent_slug": "default-chatbot"},
            created_by="admin",
        )


@pytest.mark.unit
async def test_summary_aggregates_all_rollouts_beyond_page_size(evaluation_session):
    """聚合和分母必须覆盖全部 rollout，不能被分页默认的 100 条截断。"""
    service = PlatformEvaluationService()
    dataset = await service.create_dataset(
        db=evaluation_session, name="many", description="", source_kind="fixture", created_by="admin"
    )
    version = await service.create_version(
        db=evaluation_session,
        dataset_id=dataset["dataset_id"],
        source={"type": "inline"},
        tasks=[
            {"task_key": f"t{index}", "input": {"query": "q"}, "expected": {"final_answer": {"value": "fixture-ok: q"}}}
            for index in range(51)
        ],
    )
    await service.publish_version(db=evaluation_session, version_id=version["version_id"])
    experiment = await service.create_experiment(
        db=evaluation_session, data={"dataset_version_id": version["version_id"], "max_attempts": 2}, created_by="admin"
    )

    summary = await service.run_experiment(db=evaluation_session, experiment_id=experiment["experiment_id"])

    assert len(summary["rollouts"]) == 102
    assert summary["aggregate"]["attempt_count"] == 102
    assert summary["performance"]["rollout_status"] == {"COMPLETED": 102}
    assert summary["metric_breakdown"]["task_pass"]["status_counts"] == {"passed": 102}


@pytest.mark.unit
async def test_experiment_snapshot_captures_dataset_evaluators_and_code(evaluation_session):
    """服务端采集的快照放在 captured 下，调用方传入的同名字段不能覆盖。"""
    service = PlatformEvaluationService()
    first = await _fixture_experiment(service, evaluation_session)
    version_id = first["dataset_version_id"]
    experiment = await service.create_experiment(
        db=evaluation_session,
        data={"dataset_version_id": version_id, "config_snapshot": {"git_commit": "abc123", "captured": "spoofed"}},
        created_by="admin",
    )

    snapshot = experiment["config_snapshot"]
    assert snapshot["git_commit"] == "abc123"
    assert snapshot["captured"]["dataset"]["version_id"] == version_id
    assert snapshot["captured"]["dataset"]["task_count"] == 2
    assert snapshot["captured"]["evaluators"] == {"agent_tool_final_v1": "deterministic-v1"}
    assert len(snapshot["captured"]["code_checksum"]) == 64
    assert snapshot["captured"]["code_checksum"] == first["config_snapshot"]["captured"]["code_checksum"]


@pytest.mark.unit
@pytest.mark.parametrize(
    "pricing",
    [
        {"input_per_million": 1.0, "output_per_million": 2.0},
        {"input_per_million": -1, "output_per_million": 2.0, "currency": "CNY"},
        {"input_per_million": "1", "output_per_million": 2.0, "currency": "CNY"},
    ],
)
async def test_experiment_rejects_invalid_pricing(evaluation_session, pricing):
    service = PlatformEvaluationService()
    first = await _fixture_experiment(service, evaluation_session)

    with pytest.raises(ValueError, match="pricing"):
        await service.create_experiment(
            db=evaluation_session,
            data={"dataset_version_id": first["dataset_version_id"], "config_snapshot": {"pricing": pricing}},
            created_by="admin",
        )


@pytest.mark.unit
async def test_compare_experiments_reports_broken_task_and_config_diff(evaluation_session, monkeypatch):
    """同一数据集版本的两次实验逐题配对：候选实验答错的题记为 broken，并列出配置差异。"""
    from yuxi.evaluation import experiment_service

    service = PlatformEvaluationService()
    base = await _fixture_experiment(service, evaluation_session)
    await service.run_experiment(db=evaluation_session, experiment_id=base["experiment_id"])
    candidate = await service.create_experiment(
        db=evaluation_session,
        data={"dataset_version_id": base["dataset_version_id"], "config_snapshot": {"model_spec": "provider:new"}},
        created_by="admin",
    )
    original = experiment_service._run_fixture

    async def break_t1(task, rollout_id):
        actual, events = await original(task, rollout_id)
        if task.task_key == "t1":
            actual = {**actual, "final_answer": "wrong"}
        return actual, events

    monkeypatch.setattr(experiment_service, "_run_fixture", break_t1)
    await service.run_experiment(db=evaluation_session, experiment_id=candidate["experiment_id"])

    comparison = await service.compare_experiments(
        db=evaluation_session,
        base_experiment_id=base["experiment_id"],
        candidate_experiment_id=candidate["experiment_id"],
    )

    outcomes = comparison["outcomes"]
    assert (outcomes["fixed"], outcomes["broken"], outcomes["both_pass"]) == ([], ["t1"], 1)
    assert outcomes["pass_rate_delta"] == -0.5
    assert outcomes["mcnemar_p_value"] == 1.0
    assert comparison["metric_versions"] == ["deterministic-v1"]
    assert {"key": "model_spec", "base": None, "candidate": "provider:new"} in comparison["config_diff"]
    assert all(item["key"] != "captured.captured_at" for item in comparison["config_diff"])


@pytest.mark.unit
async def test_compare_experiments_refuses_different_dataset_versions(evaluation_session):
    service = PlatformEvaluationService()
    base = await _fixture_experiment(service, evaluation_session)
    dataset = await service.create_dataset(
        db=evaluation_session, name="other", description="", source_kind="fixture", created_by="admin"
    )
    version = await service.create_version(
        db=evaluation_session,
        dataset_id=dataset["dataset_id"],
        source={"type": "other"},
        tasks=[{"task_key": "t1", "input": {"query": "t1"}, "expected": {}}],
    )
    await service.publish_version(db=evaluation_session, version_id=version["version_id"])
    other = await service.create_experiment(
        db=evaluation_session, data={"dataset_version_id": version["version_id"]}, created_by="admin"
    )

    with pytest.raises(ValueError, match="数据集版本不同"):
        await service.compare_experiments(
            db=evaluation_session,
            base_experiment_id=base["experiment_id"],
            candidate_experiment_id=other["experiment_id"],
        )


@pytest.mark.unit
async def test_compare_experiments_refuses_different_metric_versions(evaluation_session):
    """评分口径升级后（如 agent-final-answer-v1 → v2）的实验不能直接逐题对比。"""
    from yuxi.repositories.platform_evaluation_repository import PlatformEvaluationRepository

    service = PlatformEvaluationService()
    base = await _fixture_experiment(service, evaluation_session)
    await service.run_experiment(db=evaluation_session, experiment_id=base["experiment_id"])
    candidate = await service.create_experiment(
        db=evaluation_session, data={"dataset_version_id": base["dataset_version_id"]}, created_by="admin"
    )
    await service.run_experiment(db=evaluation_session, experiment_id=candidate["experiment_id"])
    repo = PlatformEvaluationRepository(evaluation_session)
    record = await repo.get_experiment(candidate["experiment_id"])
    snapshot = dict(record.config_snapshot)
    snapshot["captured"] = {**snapshot["captured"], "evaluators": {"agent_tool_final_v1": "deterministic-v2"}}
    record.config_snapshot = snapshot
    await evaluation_session.commit()

    with pytest.raises(ValueError, match="评分口径不同"):
        await service.compare_experiments(
            db=evaluation_session,
            base_experiment_id=base["experiment_id"],
            candidate_experiment_id=candidate["experiment_id"],
        )


@pytest.mark.unit
async def test_cancel_during_rollout_wins_over_late_result(evaluation_session, monkeypatch):
    """实验在某题执行中被取消：该题保持 CANCELLED，晚到的结果不写入，后续题不再执行。"""
    from yuxi.evaluation import experiment_service

    service = PlatformEvaluationService()
    experiment = await _fixture_experiment(service, evaluation_session)
    original = experiment_service._run_fixture
    executed = []

    async def cancel_while_running(task, rollout_id):
        executed.append(task.task_key)
        await service.cancel_experiment(db=evaluation_session, experiment_id=experiment["experiment_id"])
        return await original(task, rollout_id)

    monkeypatch.setattr(experiment_service, "_run_fixture", cancel_while_running)
    result = await service.run_experiment(db=evaluation_session, experiment_id=experiment["experiment_id"])

    assert executed == ["t1"]
    assert result["status"] == "CANCELLED"
    assert [item["status"] for item in result["rollouts"]] == ["CANCELLED"]
    assert result["metrics"] == []
    assert [item["reason_code"] for item in result["failures"]] == ["cancelled"]


@pytest.mark.unit
async def test_cancel_during_last_task_is_not_overwritten_as_finished(evaluation_session, monkeypatch):
    """取消落在最后一题执行期间：实验最终是 CANCELLED，而不是汇总成 FINISHED。"""
    from yuxi.evaluation import experiment_service

    service = PlatformEvaluationService()
    experiment = await _fixture_experiment(service, evaluation_session)
    original = experiment_service._run_fixture

    async def cancel_on_last_task(task, rollout_id):
        if task.task_key == "t2":
            await service.cancel_experiment(db=evaluation_session, experiment_id=experiment["experiment_id"])
        return await original(task, rollout_id)

    monkeypatch.setattr(experiment_service, "_run_fixture", cancel_on_last_task)
    result = await service.run_experiment(db=evaluation_session, experiment_id=experiment["experiment_id"])
    stored = await service.get_experiment(db=evaluation_session, experiment_id=experiment["experiment_id"])

    assert result["status"] == "CANCELLED"
    assert stored["status"] == "CANCELLED"
    assert sorted(item["status"] for item in result["rollouts"]) == ["CANCELLED", "COMPLETED"]


@pytest.mark.unit
async def test_cancel_stops_remaining_attempts_of_the_same_task(evaluation_session, monkeypatch):
    """k>1 时取消发生在第 1 次尝试中，同一题的后续尝试不再执行。"""
    from yuxi.evaluation import experiment_service

    service = PlatformEvaluationService()
    base = await _fixture_experiment(service, evaluation_session)
    experiment = await service.create_experiment(
        db=evaluation_session,
        data={"dataset_version_id": base["dataset_version_id"], "max_attempts": 3},
        created_by="admin",
    )
    original = experiment_service._run_fixture
    executed = []

    async def cancel_on_first_attempt(task, rollout_id):
        executed.append(task.task_key)
        await service.cancel_experiment(db=evaluation_session, experiment_id=experiment["experiment_id"])
        return await original(task, rollout_id)

    monkeypatch.setattr(experiment_service, "_run_fixture", cancel_on_first_attempt)
    result = await service.run_experiment(db=evaluation_session, experiment_id=experiment["experiment_id"])

    assert executed == ["t1"]
    assert result["status"] == "CANCELLED"


@pytest.mark.unit
async def test_cancel_agent_experiment_cancels_in_flight_agent_runs(
    evaluation_session, monkeypatch, fake_agent_snapshot
):
    """取消 Agent 实验时，同时取消在途题对应的 AgentRun；已结束的题不受影响。"""
    from yuxi.evaluation import experiment_service
    from yuxi.repositories.platform_evaluation_repository import PlatformEvaluationRepository

    cancelled = []

    async def fake_cancel_runs(db, rollout_ids):
        cancelled.append(list(rollout_ids))

    monkeypatch.setattr(experiment_service, "cancel_agent_runs_for_rollouts", fake_cancel_runs)
    service = PlatformEvaluationService()
    version = await _published_agent_version(service, evaluation_session, {})
    experiment = await service.create_experiment(
        db=evaluation_session,
        data={"dataset_version_id": version["version_id"], "runner": "agent_run", "agent_slug": "default-chatbot"},
        created_by="admin",
    )
    repo = PlatformEvaluationRepository(evaluation_session)
    task = (await repo.list_tasks(version["version_id"]))[0]
    for rollout_id, attempt, status in (("pro_done", 1, "COMPLETED"), ("pro_running", 2, "RUNNING")):
        await repo.create_rollout(
            {
                "rollout_id": rollout_id,
                "experiment_id": experiment["experiment_id"],
                "task_id": task.task_id,
                "attempt": attempt,
                "idempotency_key": rollout_id,
                "status": status,
            }
        )
    await evaluation_session.commit()

    result = await service.cancel_experiment(db=evaluation_session, experiment_id=experiment["experiment_id"])

    assert result["status"] == "CANCELLED"
    assert cancelled == [["pro_running"]]


@pytest.mark.unit
async def test_recovery_enqueue_is_deferred_past_executor_lock_ttl(monkeypatch):
    """恢复作业延迟到执行锁 TTL 加余量之后再跑，让崩溃进程遗留的锁先过期；手动触发不延迟且每次是新 Job ID。"""
    from datetime import timedelta

    from yuxi.evaluation import experiment_service

    calls = []

    class FakeQueue:
        async def enqueue_job(self, name, experiment_id, **kwargs):
            calls.append((name, experiment_id, kwargs))

    async def fake_pool():
        return FakeQueue()

    monkeypatch.setattr(experiment_service, "get_arq_pool", fake_pool)
    await experiment_service.enqueue_experiment("pex_1")
    await experiment_service.enqueue_experiment("pex_1")
    await experiment_service.enqueue_experiment("pex_1", recovery=True)

    manual, again, recovery = calls[0], calls[1], calls[2]
    # 每次投递都是新的 Job ID，卡住的实验可以再次投递，不会被残留的 ARQ 键去重。
    assert manual[2]["_job_id"].startswith("platform-eval:pex_1:")
    assert manual[2]["_job_id"] != again[2]["_job_id"]
    assert manual[2]["_defer_by"] is None
    expected_defer = experiment_service.EXPERIMENT_LOCK_TTL_SECONDS + experiment_service.RECOVERY_DEFER_MARGIN_SECONDS
    assert recovery[2]["_defer_by"] == timedelta(seconds=expected_defer)


@pytest.mark.unit
async def test_agent_failures_count_as_failed_but_infra_errors_are_not_scored(evaluation_session, monkeypatch):
    """超时与 Agent Run 失败是被测对象的问题，计入通过率；评测侧 runner 异常不评分，单独记原因。"""
    from yuxi.evaluation import experiment_service

    service = PlatformEvaluationService()
    experiment = await _fixture_experiment(service, evaluation_session)

    async def fail_by_task(task, rollout_id):
        if task.task_key == "t1":
            raise TimeoutError
        raise RuntimeError("evaluation infrastructure broke")

    monkeypatch.setattr(experiment_service, "_run_fixture", fail_by_task)
    result = await service.run_experiment(db=evaluation_session, experiment_id=experiment["experiment_id"])

    assert sorted(item["reason_code"] for item in result["rollouts"]) == ["runner_error", "timeout"]
    assert result["aggregate"]["measured_task_count"] == 1
    assert result["aggregate"]["not_measured_tasks"] == 1
    assert result["aggregate"]["pass@1"] == 0.0
    task_pass = [item for item in result["metrics"] if item["metric_name"] == "task_pass"]
    assert [(item["status"], item["reason_code"], item["metric_version"]) for item in task_pass] == [
        ("failed", "timeout", "deterministic-v1")
    ]


@pytest.mark.unit
async def test_compare_reports_candidate_timeout_as_broken(evaluation_session, monkeypatch):
    """基线通过、候选超时的题必须出现在 broken 中，退化不能因为“未评分”而被隐藏。"""
    from yuxi.evaluation import experiment_service

    service = PlatformEvaluationService()
    base = await _fixture_experiment(service, evaluation_session)
    await service.run_experiment(db=evaluation_session, experiment_id=base["experiment_id"])
    candidate = await service.create_experiment(
        db=evaluation_session, data={"dataset_version_id": base["dataset_version_id"]}, created_by="admin"
    )
    original = experiment_service._run_fixture

    async def timeout_on_t1(task, rollout_id):
        if task.task_key == "t1":
            raise TimeoutError
        return await original(task, rollout_id)

    monkeypatch.setattr(experiment_service, "_run_fixture", timeout_on_t1)
    await service.run_experiment(db=evaluation_session, experiment_id=candidate["experiment_id"])

    comparison = await service.compare_experiments(
        db=evaluation_session,
        base_experiment_id=base["experiment_id"],
        candidate_experiment_id=candidate["experiment_id"],
    )

    assert comparison["outcomes"]["broken"] == ["t1"]
    assert comparison["outcomes"]["pass_rate_delta"] == -0.5


@pytest.mark.unit
async def test_compare_uses_snapshot_versions_when_candidate_has_no_scores(evaluation_session, monkeypatch):
    """候选全部因评测侧错误没有评分时，不应报“评分口径不同”这种误导性错误；未评分的题列为未配对。"""
    from yuxi.evaluation import experiment_service

    service = PlatformEvaluationService()
    base = await _fixture_experiment(service, evaluation_session)
    await service.run_experiment(db=evaluation_session, experiment_id=base["experiment_id"])
    candidate = await service.create_experiment(
        db=evaluation_session, data={"dataset_version_id": base["dataset_version_id"]}, created_by="admin"
    )

    async def infra_failure(task, rollout_id):
        raise RuntimeError("evaluation infrastructure broke")

    monkeypatch.setattr(experiment_service, "_run_fixture", infra_failure)
    await service.run_experiment(db=evaluation_session, experiment_id=candidate["experiment_id"])

    comparison = await service.compare_experiments(
        db=evaluation_session,
        base_experiment_id=base["experiment_id"],
        candidate_experiment_id=candidate["experiment_id"],
    )

    assert comparison["outcomes"]["paired_count"] == 0
    assert comparison["outcomes"]["unpaired_task_keys"] == ["t1", "t2"]


@pytest.mark.unit
async def test_failed_experiment_can_be_started_again(evaluation_session, monkeypatch):
    """作业异常后实验标为 FAILED 并保留原因；再次运行会回到 QUEUED 并重新投递。"""
    from yuxi.evaluation import experiment_service

    enqueued = []

    async def fake_enqueue(experiment_id, *, recovery=False):
        enqueued.append(experiment_id)

    monkeypatch.setattr(experiment_service, "enqueue_experiment", fake_enqueue)
    service = PlatformEvaluationService()
    experiment = await _fixture_experiment(service, evaluation_session)
    await service.mark_failed(db=evaluation_session, experiment_id=experiment["experiment_id"], error_message="boom")
    failed = await service.get_experiment(db=evaluation_session, experiment_id=experiment["experiment_id"])

    restarted = await service.start_experiment(db=evaluation_session, experiment_id=experiment["experiment_id"])

    assert (failed["status"], failed["error_message"]) == ("FAILED", "boom")
    assert restarted["status"] == "QUEUED"
    assert restarted["error_message"] is None
    assert enqueued == [experiment["experiment_id"]]
