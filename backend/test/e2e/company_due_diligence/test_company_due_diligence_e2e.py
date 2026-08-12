"""企业尽调管理员 HTTP 核心链路 E2E。"""

from __future__ import annotations

import asyncio
import json
import os
import uuid

import pytest
from sqlalchemy import text

from yuxi.company_due_diligence.mysql import CompanyDueDiligenceMySQL
from yuxi.company_due_diligence.report.models import ReportSection
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


async def _wait_for_agent_run(client, headers, run_id: str) -> dict:
    """轮询主智能体 Run，直到返回完成或失败状态。"""
    for _ in range(300):
        response = await client.get(f"/api/agent/runs/{run_id}", headers=headers)
        assert response.status_code == 200, response.text
        run = response.json().get("run") or {}
        if run.get("status") in {"completed", "failed", "cancelled", "interrupted"}:
            return run
        await asyncio.sleep(1)
    raise AssertionError(f"主智能体 Run 未在预期时间内完成: {run_id}")


async def test_main_chat_can_call_global_company_due_diligence_agent(e2e_client, e2e_headers):
    """主聊天选择全局企业尽调智能体后应实际调用工具并落库工具结果。"""
    agent_slug = "company-due-diligence"
    thread_id = None
    run_id = None
    try:
        agent_response = await e2e_client.get(f"/api/agent/{agent_slug}", headers=e2e_headers)
        assert agent_response.status_code == 200, agent_response.text
        agent = agent_response.json()["agent"]
        context = (agent.get("config_json") or {}).get("context") or {}
        assert agent["slug"] == agent_slug
        assert "investigate_company" in context.get("tools", [])

        thread_response = await e2e_client.post(
            "/api/chat/thread",
            json={
                "agent_id": agent_slug,
                "title": f"company-due-diligence-e2e-{uuid.uuid4().hex[:8]}",
                "metadata": {"test": "company-due-diligence-main-chat"},
            },
            headers=e2e_headers,
        )
        assert thread_response.status_code == 200, thread_response.text
        thread_id = str(thread_response.json().get("thread_id") or thread_response.json().get("id"))
        assert thread_id and thread_id != "None"

        request_id = f"company-dd-e2e-{uuid.uuid4().hex[:16]}"
        run_response = await e2e_client.post(
            "/api/agent/runs",
            json={
                "query": "当前数据覆盖哪些企业？",
                "agent_slug": agent_slug,
                "thread_id": thread_id,
                "meta": {"request_id": request_id},
            },
            headers=e2e_headers,
        )
        assert run_response.status_code == 200, run_response.text
        run_id = str(run_response.json().get("run_id"))
        assert run_id and run_id != "None"

        run = await _wait_for_agent_run(e2e_client, e2e_headers, run_id)
        assert run["status"] == "completed", run

        result_response = await e2e_client.get(f"/api/agent/runs/{run_id}/result", headers=e2e_headers)
        assert result_response.status_code == 200, result_response.text
        result = result_response.json()
        assert result["status"] == "completed", result
        assert result["output"], result

        history_response = await e2e_client.get(f"/api/chat/thread/{thread_id}/history", headers=e2e_headers)
        assert history_response.status_code == 200, history_response.text
        history = history_response.json().get("history") or []
        tool_calls = [tool_call for message in history for tool_call in message.get("tool_calls") or []]
        investigate_calls = [call for call in tool_calls if call.get("name") == "investigate_company"]
        assert investigate_calls, json.dumps(history, ensure_ascii=False)
        assert any(call.get("status") == "success" for call in investigate_calls), json.dumps(
            investigate_calls, ensure_ascii=False
        )
    finally:
        if run_id:
            run = await e2e_client.get(f"/api/agent/runs/{run_id}", headers=e2e_headers)
            if run.status_code == 200 and (run.json().get("run") or {}).get("status") not in {
                "completed",
                "failed",
                "cancelled",
                "interrupted",
            }:
                await e2e_client.post(f"/api/agent/runs/{run_id}/cancel", headers=e2e_headers)
        if thread_id:
            await e2e_client.delete(f"/api/chat/thread/{thread_id}", headers=e2e_headers)


async def test_admin_can_complete_investigation_report_quality_and_performance_e2e(e2e_client, e2e_headers):
    """管理员应通过真实 HTTP 完成统一调查、报告、评测和导出闭环。"""
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
            row = (
                await connection.execute(
                    text(
                        """
                        SELECT p.eid, e.name
                        FROM t_partners_alls p
                        JOIN t_enterprise e ON e.eid = p.eid
                        WHERE p.is_history = 0
                        ORDER BY p.eid
                        LIMIT 1
                        """
                    )
                )
            ).one()
            target_row = (
                await connection.execute(
                    text("SELECT eid, name FROM t_enterprise WHERE eid != :eid ORDER BY eid LIMIT 1"),
                    {"eid": row.eid},
                )
            ).one()
        eid, company_name = row
        target_eid, target_company_name = target_row

        global_response = await e2e_client.post(
            "/api/company-due-diligence/query",
            json={"question": "当前数据覆盖哪些企业？"},
            headers=e2e_headers,
        )
        assert global_response.status_code == 200, global_response.text
        global_result = global_response.json()
        assert global_result["scope_status"] == "global_scope"
        assert global_result["global_result"]["denominator"] == 924

        needs_context_response = await e2e_client.post(
            "/api/company-due-diligence/query",
            json={"question": "注册资本是多少？"},
            headers=e2e_headers,
        )
        assert needs_context_response.status_code == 200, needs_context_response.text
        assert needs_context_response.json()["scope_status"] == "needs_context"

        coverage_response = await e2e_client.get(
            "/api/company-due-diligence/evaluations/coverage",
            headers=e2e_headers,
        )
        assert coverage_response.status_code == 200, coverage_response.text
        coverage = coverage_response.json()
        assert coverage["denominator"] == 924
        assert coverage["coverage"]["licenses_company_coverage_rate"] > 0

        natural_language_response = await e2e_client.post(
            "/api/company-due-diligence/query",
            json={"question": f"{company_name} 的注册资本、法人和经营状态是什么？"},
            headers=e2e_headers,
        )
        assert natural_language_response.status_code == 200, natural_language_response.text
        assert natural_language_response.json()["resolution_status"] == "resolved"

        auto_quality_response = await e2e_client.post(
            "/api/company-due-diligence/evaluations/auto-runs",
            json={
                "dataset_version": "latest_frozen",
                "case_families": ["refusal_and_ambiguity"],
                "max_samples": 1,
            },
            headers=e2e_headers,
        )
        assert auto_quality_response.status_code == 202, auto_quality_response.text
        auto_quality_id = auto_quality_response.json()["run_id"]
        task_ids.append(auto_quality_id)
        quality_ids.append(auto_quality_id)
        auto_quality_detail = await _wait_for_detail(
            e2e_client,
            e2e_headers,
            f"/api/company-due-diligence/evaluations/auto-runs/{auto_quality_id}",
        )
        assert auto_quality_detail["task"]["status"] == "success"
        auto_report = auto_quality_detail["report"]
        assert auto_report["dataset_scope"] == "global"
        assert auto_report["samples"][0]["review_status"] == "source_derived"

        search_response = await e2e_client.get(
            "/api/company-due-diligence/companies/search",
            params={"q": company_name, "limit": 10},
            headers=e2e_headers,
        )
        assert search_response.status_code == 200, search_response.text
        assert any(candidate["eid"] == eid for candidate in search_response.json()["candidates"])

        shareholder_response = await e2e_client.post(
            "/api/company-due-diligence/investigations",
            json={
                "company_context": {"eid": eid, "name": company_name},
                "message": "这家企业的股东有哪些？",
            },
            headers=e2e_headers,
        )
        assert shareholder_response.status_code == 200, shareholder_response.text
        shareholder = shareholder_response.json()
        assert shareholder["status"] == "complete"
        assert shareholder["company_context"]["eid"] == eid
        assert shareholder["understood_request"]["topics"] == ["shareholders"]
        assert shareholder["presentation"]["type"] == "summary_with_list"
        assert shareholder["facts"]
        assert shareholder["plan"]["structured_queries"] == ["current_shareholders"]
        assert shareholder["plan"]["use_graph"] is False
        assert shareholder["plan"]["evidence_types"] == []
        assert {step["source"] for step in shareholder["steps"]} == {"mysql"}
        assert shareholder["claim_ledger"]["validated"] is True
        assert len(shareholder["claim_ledger"]["claims"]) == len(shareholder["facts"]) + len(shareholder["relations"])
        source_ids = {source["source_id"] for source in shareholder["sources"]}
        assert all(
            source_id in source_ids
            for claim in shareholder["claim_ledger"]["claims"]
            for source_id in claim["source_ids"]
        )

        relation_response = await e2e_client.post(
            "/api/company-due-diligence/investigations",
            json={
                "company_context": shareholder["company_context"],
                "target_company_context": {"eid": target_eid, "name": target_company_name},
                "message": "这两家企业有什么关系？",
            },
            headers=e2e_headers,
        )
        assert relation_response.status_code == 200, relation_response.text
        relation = relation_response.json()
        assert relation["company_context"] == shareholder["company_context"]
        assert relation["target_company_context"]["eid"] == target_eid
        assert relation["understood_request"]["topics"] == ["relations"]
        assert relation["presentation"]["type"] == "relation_result"
        assert relation["plan"]["use_graph"] is True
        assert relation["plan"]["structured_queries"] == []
        assert any(step["source"] == "neo4j" for step in relation["steps"])

        topic_report_response = await e2e_client.post(
            "/api/company-due-diligence/investigations",
            json={
                "company_context": shareholder["company_context"],
                "message": "请用报告形式分析这家企业近三年的企业关系和风险",
            },
            headers=e2e_headers,
        )
        assert topic_report_response.status_code == 200, topic_report_response.text
        topic_report = topic_report_response.json()
        assert topic_report["status"] == "accepted"
        assert topic_report["understood_request"]["topics"] == ["relations", "risks"]
        assert topic_report["understood_request"]["time_scope"]["type"] == "recent_years"
        assert topic_report["understood_request"]["time_scope"]["years"] == 3
        assert topic_report["presentation"]["type"] == "topic_report"
        topic_report_id = topic_report["task"]["id"]
        task_ids.append(topic_report_id)
        report_ids.append(topic_report_id)
        topic_report_detail = await _wait_for_detail(
            e2e_client,
            e2e_headers,
            f"/api/company-due-diligence/reports/{topic_report_id}",
        )
        assert topic_report_detail["task"]["status"] == "success"
        assert set(topic_report_detail["report"]["sections"]) == {
            "identity",
            "relations",
            "compliance",
            "risks",
            "data_quality",
        }

        natural_full_report_response = await e2e_client.post(
            "/api/company-due-diligence/investigations",
            json={
                "company_context": shareholder["company_context"],
                "message": "请生成这家企业的完整尽调报告",
            },
            headers=e2e_headers,
        )
        shortcut_full_report_response = await e2e_client.post(
            "/api/company-due-diligence/investigations",
            json={
                "company_context": shareholder["company_context"],
                "shortcut": "generate_full_due_diligence_report",
            },
            headers=e2e_headers,
        )
        assert natural_full_report_response.status_code == 200, natural_full_report_response.text
        assert shortcut_full_report_response.status_code == 200, shortcut_full_report_response.text
        natural_full_report = natural_full_report_response.json()
        shortcut_full_report = shortcut_full_report_response.json()
        assert natural_full_report["understood_request"] == shortcut_full_report["understood_request"]
        assert natural_full_report["presentation"] == shortcut_full_report["presentation"]
        assert natural_full_report["presentation"]["type"] == "full_report"
        full_report_ids = [natural_full_report["task"]["id"], shortcut_full_report["task"]["id"]]
        task_ids.extend(full_report_ids)
        report_ids.extend(full_report_ids)
        for full_report_id in full_report_ids:
            full_report_detail = await _wait_for_detail(
                e2e_client,
                e2e_headers,
                f"/api/company-due-diligence/reports/{full_report_id}",
            )
            assert full_report_detail["task"]["status"] == "success"
            assert set(full_report_detail["report"]["sections"]) == {section.value for section in ReportSection}

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
        assert quality_detail["report"]["all_metrics"]["sample_count"] == 2
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
        assert performance_detail["report"]["request"]["company_refs"] == []
        assert performance_detail["report"]["sample_size"] > 0
        assert performance_detail["report"]["global_summary"]["by_interface"]
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
