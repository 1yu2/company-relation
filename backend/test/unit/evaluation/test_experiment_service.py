import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

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
    assert repeated["rollouts"] == []
