"""平台级评测 API 的权限、分页和状态边界。"""

from __future__ import annotations

import os
import uuid

import pytest

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
    assert run.json()["data"]["status"] == "FINISHED"

    first_page = await test_client.get("/api/platform-evaluations/experiments?limit=1", headers=admin_headers)
    assert first_page.status_code == 200
    assert isinstance(first_page.json()["data"]["items"], list)
