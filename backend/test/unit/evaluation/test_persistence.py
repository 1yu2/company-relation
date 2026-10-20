import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from yuxi.evaluation.experiment_service import PlatformEvaluationService
from yuxi.repositories.platform_evaluation_repository import PlatformEvaluationRepository
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
async def persistence_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: Base.metadata.create_all(
                sync_connection,
                tables=[
                    model.__table__
                    for model in (
                        PlatformEvaluationDataset,
                        PlatformEvaluationDatasetVersion,
                        PlatformEvaluationTask,
                        PlatformEvaluationExperiment,
                        PlatformEvaluationRollout,
                        PlatformEvaluationTraceEvent,
                        PlatformEvaluationMetricResult,
                        PlatformEvaluationFailure,
                    )
                ],
            )
        )
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.mark.unit
async def test_dataset_page_includes_latest_version_task_count(persistence_session):
    repo = PlatformEvaluationRepository(persistence_session)
    await repo.create_dataset(
        {"dataset_id": "dataset-summary", "name": "summary", "description": "", "source_kind": "fixture", "created_by": "admin"}
    )
    service = PlatformEvaluationService(repository=repo)
    await service.create_version(
        db=persistence_session,
        dataset_id="dataset-summary",
        source={"marker": "test"},
        tasks=[{"task_key": "one", "input": {"query": "q"}, "expected": {}}],
    )

    page = await service.list_datasets_page(db=persistence_session)

    summary = next(item for item in page["items"] if item["dataset_id"] == "dataset-summary")
    assert summary["item_count"] == 1
    assert summary["latest_version"]
    assert summary["latest_version_status"] == "draft"


@pytest.mark.unit
async def test_cursor_page_and_idempotent_rollout_event_metric_writes(persistence_session):
    repo = PlatformEvaluationRepository(persistence_session)
    first = await repo.create_dataset(
        {"dataset_id": "dataset-1", "name": "one", "description": "", "source_kind": "fixture", "created_by": "admin"}
    )
    await repo.create_dataset(
        {"dataset_id": "dataset-2", "name": "two", "description": "", "source_kind": "rag", "created_by": "admin"}
    )
    page, cursor = await repo.list_datasets_page(limit=1)
    assert len(page) == 1
    assert cursor
    next_page, next_cursor = await repo.list_datasets_page(limit=1, cursor=cursor)
    assert len(next_page) == 1
    assert next_page[0].dataset_id != page[0].dataset_id
    assert next_cursor is None

    await repo.create_version(
        {
            "version_id": "version-1",
            "dataset_id": first.dataset_id,
            "version_no": 1,
            "checksum": "checksum-1",
            "source": {},
            "status": "published",
        }
    )
    with pytest.raises(ValueError, match="不可修改"):
        await repo.update_version("version-1", {"checksum": "changed"})

    await repo.create_experiment(
        {
            "experiment_id": "experiment-1",
            "dataset_version_id": "version-1",
            "source_kind": "fixture",
            "runner": "fixture",
            "evaluator_version": "deterministic-v1",
            "config_snapshot": {},
            "concurrency": 1,
            "timeout_seconds": 30,
            "max_attempts": 1,
            "created_by": "admin",
        }
    )
    rollout_data = {
        "rollout_id": "rollout-1",
        "experiment_id": "experiment-1",
        "task_id": "task-1",
        "attempt": 1,
        "idempotency_key": "idem-1",
        "status": "RUNNING",
    }
    first_rollout = await repo.create_rollout(rollout_data)
    second_rollout = await repo.create_rollout({**rollout_data, "rollout_id": "rollout-2"})
    assert first_rollout.rollout_id == second_rollout.rollout_id == "rollout-1"
