"""平台级评测 API 的权限、分页和状态边界。"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from sqlalchemy import select

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_evaluation import (
    PlatformEvaluationDataset,
    PlatformEvaluationDatasetVersion,
    PlatformEvaluationExperiment,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest.mark.skipif(os.getenv("LITE_MODE", "").lower() in {"true", "1"}, reason="评测路由在 LITE_MODE 不注册")
async def test_platform_evaluation_requires_admin(test_client, standard_user):
    response = await test_client.get("/api/platform-evaluations/datasets", headers=standard_user["headers"])
    assert response.status_code == 403


@pytest.mark.skipif(os.getenv("LITE_MODE", "").lower() in {"true", "1"}, reason="评测路由在 LITE_MODE 不注册")
async def test_admin_can_run_fixture_and_use_cursor(test_client, admin_headers):
    suffix = uuid.uuid4().hex[:10]
    dataset = await test_client.post(
        "/api/platform-evaluations/datasets",
        json={"name": f"api-fixture-{suffix}", "source_kind": "fixture"},
        headers=admin_headers,
    )
    assert dataset.status_code == 200, dataset.text
    dataset_id = dataset.json()["data"]["dataset_id"]

    version = await test_client.post(
        f"/api/platform-evaluations/datasets/{dataset_id}/versions",
        json={
            "source": {"type": "inline", "uri": f"fixture://{suffix}"},
            "tasks": [
                {
                    "task_key": f"fixture-{suffix}",
                    "input": {"query": "hello"},
                    "expected": {
                        "final_answer": {"value": "fixture-ok: hello"},
                        "tool_policy": {"required": ["fixture.echo"]},
                        "trace_contract": {"required_sequence": ["agent", "tool", "final"]},
                    },
                }
            ],
        },
        headers=admin_headers,
    )
    assert version.status_code == 200, version.text
    version_id = version.json()["data"]["version_id"]
    publish = await test_client.post(f"/api/platform-evaluations/versions/{version_id}/publish", headers=admin_headers)
    assert publish.status_code == 200, publish.text

    experiment = await test_client.post(
        "/api/platform-evaluations/experiments",
        json={"dataset_version_id": version_id, "max_attempts": 2},
        headers=admin_headers,
    )
    assert experiment.status_code == 200, experiment.text
    experiment_id = experiment.json()["data"]["experiment_id"]
    run = await test_client.post(f"/api/platform-evaluations/experiments/{experiment_id}/run", headers=admin_headers)
    assert run.status_code == 200, run.text
    assert run.json()["data"]["status"] in {"QUEUED", "RUNNING", "FINISHED"}

    # 实验由 worker 后台执行，HTTP 只负责排队；轮询直到真实 worker 写入终态。
    for _ in range(60):
        detail = await test_client.get(f"/api/platform-evaluations/experiments/{experiment_id}", headers=admin_headers)
        if detail.json()["data"]["status"] in {"FINISHED", "FAILED", "CANCELLED"}:
            break
        await asyncio.sleep(1)
    assert detail.json()["data"]["status"] == "FINISHED", detail.text
    assert len(detail.json()["data"]["rollouts"]) == 2

    first_page = await test_client.get("/api/platform-evaluations/experiments?limit=1", headers=admin_headers)
    assert first_page.status_code == 200
    assert isinstance(first_page.json()["data"]["items"], list)


@pytest.fixture
async def eval_db():
    """绑定到当前测试事件循环的直连会话，在写接口返回后立刻查库，不经过下一次请求的鉴权耗时。"""
    if pg_manager.async_engine:
        await pg_manager.async_engine.dispose()
    pg_manager._initialized = False
    pg_manager.initialize()
    async with pg_manager.get_async_session_context() as session:
        yield session


@pytest.mark.skipif(os.getenv("LITE_MODE", "").lower() in {"true", "1"}, reason="评测路由在 LITE_MODE 不注册")
async def test_write_is_committed_before_response(test_client, admin_headers, eval_db):
    """写接口返回前必须已提交：响应一到，库里就能读到刚创建/发布的资源，紧接着启动实验不会 404。

    提交若放在依赖退出时，会晚于响应发送，竞态窗口只有几毫秒，所以多轮重复并在响应后直接查库。
    """
    for round_no in range(20):
        suffix = f"{uuid.uuid4().hex[:10]}-{round_no}"
        dataset = await test_client.post(
            "/api/platform-evaluations/datasets",
            json={"name": f"api-commit-{suffix}", "source_kind": "fixture"},
            headers=admin_headers,
        )
        assert dataset.status_code == 200, dataset.text
        dataset_id = dataset.json()["data"]["dataset_id"]
        assert await eval_db.scalar(
            select(PlatformEvaluationDataset.dataset_id).where(PlatformEvaluationDataset.dataset_id == dataset_id)
        ), f"第 {round_no} 轮：数据集在响应后尚未提交"

        version = await test_client.post(
            f"/api/platform-evaluations/datasets/{dataset_id}/versions",
            json={
                "source": {"type": "inline"},
                "tasks": [
                    {
                        "task_key": f"commit-{suffix}",
                        "input": {"query": "hello"},
                        "expected": {"final_answer": {"value": "fixture-ok: hello"}},
                    }
                ],
            },
            headers=admin_headers,
        )
        assert version.status_code == 200, version.text
        version_id = version.json()["data"]["version_id"]
        assert (
            await eval_db.scalar(
                select(PlatformEvaluationDatasetVersion.status).where(
                    PlatformEvaluationDatasetVersion.version_id == version_id
                )
            )
            == "draft"
        ), f"第 {round_no} 轮：数据集版本在响应后尚未提交"

        publish = await test_client.post(
            f"/api/platform-evaluations/versions/{version_id}/publish", headers=admin_headers
        )
        assert publish.status_code == 200, publish.text
        assert (
            await eval_db.scalar(
                select(PlatformEvaluationDatasetVersion.status).where(
                    PlatformEvaluationDatasetVersion.version_id == version_id
                )
            )
            == "published"
        ), f"第 {round_no} 轮：发布状态在响应后尚未提交"

        experiment = await test_client.post(
            "/api/platform-evaluations/experiments", json={"dataset_version_id": version_id}, headers=admin_headers
        )
        assert experiment.status_code == 200, experiment.text
        experiment_id = experiment.json()["data"]["experiment_id"]
        assert (
            await eval_db.scalar(
                select(PlatformEvaluationExperiment.status).where(
                    PlatformEvaluationExperiment.experiment_id == experiment_id
                )
            )
            == "DRAFT"
        ), f"第 {round_no} 轮：实验在响应后尚未提交"

        cancel = await test_client.post(
            f"/api/platform-evaluations/experiments/{experiment_id}/cancel", headers=admin_headers
        )
        assert cancel.status_code == 200, cancel.text
        assert (
            await eval_db.scalar(
                select(PlatformEvaluationExperiment.status).where(
                    PlatformEvaluationExperiment.experiment_id == experiment_id
                )
            )
            == "CANCELLED"
        ), f"第 {round_no} 轮：取消状态在响应后尚未提交"

        # 用户实际遇到的症状：创建后立刻启动返回 404。
        rerun = await test_client.post(
            "/api/platform-evaluations/experiments",
            json={"dataset_version_id": version_id},
            headers=admin_headers,
        )
        run = await test_client.post(
            f"/api/platform-evaluations/experiments/{rerun.json()['data']['experiment_id']}/run",
            headers=admin_headers,
        )
        assert run.status_code == 200, run.text


@pytest.mark.skipif(os.getenv("LITE_MODE", "").lower() in {"true", "1"}, reason="评测路由在 LITE_MODE 不注册")
async def test_agent_experiment_rejects_agent_not_runnable_by_creator(test_client, admin_headers):
    """快照与执行使用同一可见性口径：子智能体不能作为主 Agent 运行，创建实验时就被拒绝，而不是每题都提交失败。"""
    suffix = uuid.uuid4().hex[:10]
    dataset = await test_client.post(
        "/api/platform-evaluations/datasets",
        json={"name": f"api-visibility-{suffix}", "source_kind": "agent"},
        headers=admin_headers,
    )
    dataset_id = dataset.json()["data"]["dataset_id"]
    version = await test_client.post(
        f"/api/platform-evaluations/datasets/{dataset_id}/versions",
        json={
            "source": {"type": "inline"},
            "tasks": [
                {
                    "task_key": f"visibility-{suffix}",
                    "protocol": "agent_final_answer_v1",
                    "input": {"query": "1+1"},
                    "expected": {"answer": "2"},
                }
            ],
        },
        headers=admin_headers,
    )
    version_id = version.json()["data"]["version_id"]
    await test_client.post(f"/api/platform-evaluations/versions/{version_id}/publish", headers=admin_headers)

    response = await test_client.post(
        "/api/platform-evaluations/experiments",
        json={"dataset_version_id": version_id, "runner": "agent_run", "agent_slug": "research-explorer"},
        headers=admin_headers,
    )

    assert response.status_code == 404, response.text
