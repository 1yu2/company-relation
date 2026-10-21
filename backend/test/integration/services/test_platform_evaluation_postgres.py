"""真实 PostgreSQL 上的平台评测实验创建：SQLite 单测不检查字段长度，字段约束只能在这里验证。"""

from __future__ import annotations

import uuid

import pytest

from yuxi.evaluation.experiment_service import PROTOCOL_EVALUATORS, PlatformEvaluationService
from yuxi.storage.postgres.manager import pg_manager

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_mixed_protocol_experiment_stores_full_evaluator_versions():
    """包含全部已注册协议的数据集可以创建实验，拼接后的评分版本完整落库，不被截断。"""
    service = PlatformEvaluationService()
    session = await pg_manager.get_async_session()
    try:
        dataset = await service.create_dataset(
            db=session,
            name=f"pytest-mixed-{uuid.uuid4().hex[:8]}",
            description="",
            source_kind="agent",
            created_by="admin",
        )
        version = await service.create_version(
            db=session,
            dataset_id=dataset["dataset_id"],
            source={"type": "inline"},
            tasks=[
                {"task_key": protocol, "protocol": protocol, "input": {"query": "q"}, "expected": {}}
                for protocol in PROTOCOL_EVALUATORS
            ],
        )
        await service.publish_version(db=session, version_id=version["version_id"])

        experiment = await service.create_experiment(
            db=session, data={"dataset_version_id": version["version_id"]}, created_by="admin"
        )
        await session.flush()

        expected = ",".join(sorted({metric_version for _, metric_version in PROTOCOL_EVALUATORS.values()}))
        assert len(expected) > 64
        assert experiment["evaluator_version"] == expected
    finally:
        await session.rollback()
        await session.close()
