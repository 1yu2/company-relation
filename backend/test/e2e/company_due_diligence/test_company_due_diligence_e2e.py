"""企业尽调管理员 HTTP 核心链路 E2E。"""

from __future__ import annotations

import asyncio
import os

import pytest
from sqlalchemy import text

from yuxi.company_due_diligence.mysql import CompanyDueDiligenceMySQL
from yuxi.storage.minio import get_minio_client

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e]


async def _wait_for_detail(client, headers, path: str) -> dict:
    """轮询报告或评测详情直到 Tasker 进入终态。"""
    for _ in range(600):
        response = await client.get(path, headers=headers)
        assert response.status_code == 200, response.text
        detail = response.json()
        if detail["task"]["status"] in {"success", "failed", "cancelled"}:
            return detail
        await asyncio.sleep(0.2)
    raise AssertionError(f"任务未在预期时间内完成: {path}")


async def test_admin_can_complete_query_report_quality_and_performance_e2e(e2e_client, e2e_headers):
    """管理员应通过真实 HTTP 完成查询、报告、质量、性能和导出闭环。"""
    mysql = CompanyDueDiligenceMySQL()
    minio = get_minio_client()
    task_ids = []
    report_ids = []
    quality_ids = []
    performance_ids = []
    try:
        me_response = await e2e_client.get("/api/auth/me", headers=e2e_headers)
        assert me_response.status_code == 200
        assert me_response.json()["role"] in {"admin", "superadmin"}

        async with mysql.connection() as connection:
            row = (await connection.execute(text("SELECT eid, name FROM t_enterprise ORDER BY eid LIMIT 1"))).one()
        eid, company_name = row

        search_response = await e2e_client.get(
            "/api/company-due-diligence/companies/search",
            params={"q": company_name, "limit": 10},
            headers=e2e_headers,
        )
        assert search_response.status_code == 200, search_response.text
        assert any(candidate["eid"] == eid for candidate in search_response.json()["candidates"])

        profile_response = await e2e_client.post(
            "/api/company-due-diligence/query",
            json={"company_ref": {"eid": eid}, "question": "企业基本信息和注册资本是什么？"},
            headers=e2e_headers,
        )
        assert profile_response.status_code == 200, profile_response.text
        profile = profile_response.json()
        assert profile["resolution_status"] == "resolved"
        assert profile["facts"]
        assert profile["sources"]
        assert {fact["source_id"] for fact in profile["facts"]} <= {
            source["source_id"] for source in profile["sources"]
        }

        for question, source_name in (
            ("该企业有哪些最多三跳的关联路径？", "neo4j"),
            ("该企业有哪些处罚、许可、专利或新闻证据？", "milvus"),
        ):
            response = await e2e_client.post(
                "/api/company-due-diligence/query",
                json={"company_ref": {"eid": eid}, "question": question},
                headers=e2e_headers,
            )
            assert response.status_code == 200, response.text
            payload = response.json()
            step = next(item for item in payload["steps"] if item["source"] == source_name)
            assert step["status"] in {"complete", "no_data", "unavailable"}
            if step["status"] == "unavailable":
                assert payload["data_quality"]["source_status"] in {"partial", "unavailable"}

        unresolved_response = await e2e_client.post(
            "/api/company-due-diligence/query",
            json={
                "company_ref": {"name": "__企业尽调E2E不存在企业__"},
                "question": "企业基本信息是什么？",
            },
            headers=e2e_headers,
        )
        assert unresolved_response.status_code == 200
        assert unresolved_response.json()["resolution_status"] == "not_found"
        assert unresolved_response.json()["steps"] == []

        report_response = await e2e_client.post(
            "/api/company-due-diligence/reports",
            json={"company_ref": {"eid": eid}, "sections": ["identity", "data_quality"]},
            headers=e2e_headers,
        )
        assert report_response.status_code == 202, report_response.text
        report_id = report_response.json()["report_id"]
        task_ids.append(report_id)
        report_ids.append(report_id)
        report_detail = await _wait_for_detail(
            e2e_client,
            e2e_headers,
            f"/api/company-due-diligence/reports/{report_id}",
        )
        assert report_detail["task"]["status"] == "success"
        assert set(report_detail["report"]["sections"]) == {"identity", "data_quality"}
        for export_format in ("json", "markdown"):
            download = await e2e_client.get(
                f"/api/company-due-diligence/reports/{report_id}/download",
                params={"format": export_format},
                headers=e2e_headers,
            )
            assert download.status_code == 200
            assert download.headers.get("etag")

        quality_response = await e2e_client.post(
            "/api/company-due-diligence/evaluations/runs",
            json={"dataset_id": "guangdong-development-smoke-v1", "strategy_label": "e2e-smoke"},
            headers=e2e_headers,
        )
        assert quality_response.status_code == 202, quality_response.text
        quality_id = quality_response.json()["run_id"]
        task_ids.append(quality_id)
        quality_ids.append(quality_id)
        quality_detail = await _wait_for_detail(
            e2e_client,
            e2e_headers,
            f"/api/company-due-diligence/evaluations/runs/{quality_id}",
        )
        assert quality_detail["task"]["status"] == "success"
        assert quality_detail["report"]["all_metrics"]["strict_pass_rate"]["rate"] == 1
        assert quality_detail["report"]["regression_gate"]["eligible"] is False
        for export_format in ("json", "jsonl", "markdown"):
            download = await e2e_client.get(
                f"/api/company-due-diligence/evaluations/runs/{quality_id}/download",
                params={"format": export_format},
                headers=e2e_headers,
            )
            assert download.status_code == 200
            assert download.headers.get("etag")

        performance_response = await e2e_client.post(
            "/api/company-due-diligence/evaluations/performance-runs",
            json={
                "scenarios": ["enterprise_profile"],
                "company_refs": [{"eid": eid}],
                "concurrency": 1,
                "request_count": 2,
                "duration_seconds": 10,
                "request_timeout_seconds": 10,
            },
            headers=e2e_headers,
        )
        assert performance_response.status_code == 202, performance_response.text
        performance_id = performance_response.json()["run_id"]
        task_ids.append(performance_id)
        performance_ids.append(performance_id)
        performance_detail = await _wait_for_detail(
            e2e_client,
            e2e_headers,
            f"/api/company-due-diligence/evaluations/performance-runs/{performance_id}",
        )
        assert performance_detail["task"]["status"] == "success"
        scenario = performance_detail["report"]["scenarios"][0]
        assert scenario["latency"]["p50_ms"] is not None
        assert scenario["latency"]["p95_ms"] is not None
        assert scenario["latency"]["p99_ms"] is not None
        assert performance_detail["report"]["pool"]["after"]["checked_out"] == 0
        for export_format in ("json", "markdown"):
            download = await e2e_client.get(
                f"/api/company-due-diligence/evaluations/performance-runs/{performance_id}/download",
                params={"format": export_format},
                headers=e2e_headers,
            )
            assert download.status_code == 200
            assert download.headers.get("etag")
    finally:
        for task_id in task_ids:
            await e2e_client.delete(f"/api/tasks/{task_id}", headers=e2e_headers)
        report_bucket = os.getenv("COMPANY_DD_REPORT_BUCKET", "company-due-diligence")
        evaluation_bucket = os.getenv("COMPANY_DD_EVALUATION_BUCKET", "company-due-diligence")
        for report_id in report_ids:
            await minio.adelete_objects_by_prefix(report_bucket, f"reports/{report_id}/")
        for run_id in quality_ids:
            await minio.adelete_objects_by_prefix(evaluation_bucket, f"evaluations/quality/{run_id}/")
        for run_id in performance_ids:
            await minio.adelete_objects_by_prefix(evaluation_bucket, f"evaluations/performance/{run_id}/")
        await mysql.close()
