"""企业尽调管理员 HTTP 核心链路 E2E。"""

from __future__ import annotations

import asyncio
import json
import os
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text
from yuxi.company_due_diligence.mysql import CompanyDueDiligenceMySQL
from yuxi.company_due_diligence.report.models import ReportSection
from yuxi.company_due_diligence.report.service import (
    REPORT_TASK_TYPE,
    ReportTaskContext,
    due_diligence_report_service,
)
from yuxi.repositories.task_repository import TaskRepository
from yuxi.services.task_service import Tasker
from yuxi.storage.minio import get_minio_client

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e]


@pytest_asyncio.fixture
async def report_company():
    """从只读测试企业库选取可解析主体，并显式校验数据前置条件。"""
    mysql = CompanyDueDiligenceMySQL()
    try:
        async with mysql.connection() as connection:
            row = (await connection.execute(text("SELECT eid, name FROM t_enterprise ORDER BY eid LIMIT 1"))).first()
        if row is None:
            pytest.fail("报告 E2E 需要预置至少一家可解析企业")
        return row
    finally:
        await mysql.close()


async def _wait_for_detail(client, headers, path: str) -> dict:
    """轮询报告详情直到持久化任务进入终态。"""
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


async def test_main_chat_report_completes_in_worker_and_can_be_downloaded(e2e_client, e2e_headers, report_company):
    """主聊天发起的报告须在独立 worker 完成并可下载双格式产物。"""
    minio = get_minio_client()
    thread_id = None
    run_id = None
    report_id = None
    try:
        company_name = report_company.name

        thread_response = await e2e_client.post(
            "/api/chat/thread",
            json={
                "agent_id": "company-due-diligence",
                "title": f"company-dd-report-e2e-{uuid.uuid4().hex[:8]}",
                "metadata": {"test": "company-due-diligence-report"},
            },
            headers=e2e_headers,
        )
        assert thread_response.status_code == 200, thread_response.text
        thread_id = str(thread_response.json().get("thread_id") or thread_response.json().get("id"))

        run_response = await e2e_client.post(
            "/api/agent/runs",
            json={
                "query": (
                    f"请调用 investigate_company 工具为{company_name}生成企业基本信息专题报告，"
                    f"company_name 填写{company_name}，question 明确写生成专题报告。"
                    "不要只返回企业查询结果；取得报告任务编号后再回复。"
                ),
                "agent_slug": "company-due-diligence",
                "thread_id": thread_id,
                "tool_approval_mode": "always_trust",
                "meta": {"request_id": f"company-dd-report-{uuid.uuid4().hex[:16]}"},
            },
            headers=e2e_headers,
        )
        assert run_response.status_code == 200, run_response.text
        run_id = str(run_response.json()["run_id"])
        run = await _wait_for_agent_run(e2e_client, e2e_headers, run_id)
        assert run["status"] == "completed", run

        history_response = await e2e_client.get(f"/api/chat/thread/{thread_id}/history", headers=e2e_headers)
        assert history_response.status_code == 200, history_response.text
        tool_calls = [
            call
            for message in history_response.json().get("history") or []
            for call in message.get("tool_calls") or []
            if call.get("name") == "investigate_company" and call.get("status") == "success"
        ]
        assert tool_calls, "主聊天未成功调用企业尽调工具"
        report_results = []
        for call in tool_calls:
            content = (call.get("tool_call_result") or {}).get("content")
            if not content:
                continue
            try:
                result = json.loads(content)
            except json.JSONDecodeError:
                continue
            if isinstance(result, dict):
                report_results.append(result)
        accepted_reports = [
            result for result in report_results if result.get("status") == "accepted" and result.get("task")
        ]
        assert accepted_reports, "主聊天未创建企业尽调报告任务"
        report_id = accepted_reports[-1]["task"]["id"]

        detail = await _wait_for_detail(e2e_client, e2e_headers, f"/api/company-due-diligence/reports/{report_id}")
        assert detail["task"]["status"] == "success"
        assert detail["report"]["company"]["name"] == company_name
        for report_format in ("json", "markdown"):
            response = await e2e_client.get(
                f"/api/company-due-diligence/reports/{report_id}/download",
                params={"format": report_format},
                headers=e2e_headers,
            )
            assert response.status_code == 200
            assert response.content
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
        if report_id:
            await e2e_client.delete(f"/api/tasks/{report_id}", headers=e2e_headers)
            await minio.adelete_objects_by_prefix(
                os.getenv("COMPANY_DD_REPORT_BUCKET", "company-due-diligence"),
                f"reports/{report_id}/",
            )


async def test_admin_report_http_completes_in_worker_and_can_be_downloaded(e2e_client, e2e_headers, report_company):
    """管理员直接提交的报告应由独立 worker 完成并保留下载接口。"""
    minio = get_minio_client()
    report_id = None
    try:
        response = await e2e_client.post(
            "/api/company-due-diligence/reports",
            json={
                "company_ref": {"eid": report_company.eid},
                "sections": ["identity", "business", "data_quality"],
                "output_formats": ["json", "markdown"],
            },
            headers=e2e_headers,
        )
        assert response.status_code == 202, response.text
        report_id = response.json()["report_id"]

        detail = await _wait_for_detail(e2e_client, e2e_headers, f"/api/company-due-diligence/reports/{report_id}")
        assert detail["task"]["status"] == "success"
        assert set(detail["report"]["sections"]) == {"identity", "business", "data_quality"}

        reports_response = await e2e_client.get("/api/company-due-diligence/reports", headers=e2e_headers)
        assert reports_response.status_code == 200, reports_response.text
        assert any(item["id"] == report_id for item in reports_response.json()["reports"])

        for report_format in ("json", "markdown"):
            download = await e2e_client.get(
                f"/api/company-due-diligence/reports/{report_id}/download",
                params={"format": report_format},
                headers=e2e_headers,
            )
            assert download.status_code == 200
            assert download.content
    finally:
        if report_id:
            await e2e_client.delete(f"/api/tasks/{report_id}", headers=e2e_headers)
            await minio.adelete_objects_by_prefix(
                os.getenv("COMPANY_DD_REPORT_BUCKET", "company-due-diligence"),
                f"reports/{report_id}/",
            )


async def test_admin_report_cancel_reaches_independent_executor(e2e_client, e2e_headers):
    """API 取消请求应被另一进程的待执行或运行中报告观察。"""
    tasker = Tasker()
    repository = TaskRepository()
    for running in (False, True):
        task = await tasker.create_persisted(
            name="待取消的企业尽调报告",
            task_type=REPORT_TASK_TYPE,
            payload={},
        )
        try:
            if running:
                assert await repository.claim_pending(task.id, REPORT_TASK_TYPE) is not None

            response = await e2e_client.post(
                f"/api/company-due-diligence/reports/{task.id}/cancel",
                headers=e2e_headers,
            )
            assert response.status_code == 202, response.text
            assert response.json()["cancel_requested"] is True

            if running:
                with pytest.raises(asyncio.CancelledError):
                    await ReportTaskContext(task.id, repository).raise_if_cancelled()
            else:
                await due_diligence_report_service.execute_task(task.id)

            detail_response = await e2e_client.get(f"/api/company-due-diligence/reports/{task.id}", headers=e2e_headers)
            assert detail_response.status_code == 200, detail_response.text
            assert detail_response.json()["task"]["status"] == ("running" if running else "cancelled")
        finally:
            await tasker.delete_task(task.id)


async def test_admin_can_complete_investigation_and_report_e2e(e2e_client, e2e_headers):
    """管理员应通过真实 HTTP 完成统一问答、调查、报告和导出闭环。"""
    mysql = CompanyDueDiligenceMySQL()
    minio = get_minio_client()
    task_ids = []
    report_ids = []
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

        natural_language_response = await e2e_client.post(
            "/api/company-due-diligence/query",
            json={"question": f"{company_name} 的注册资本、法人和经营状态是什么？"},
            headers=e2e_headers,
        )
        assert natural_language_response.status_code == 200, natural_language_response.text
        assert natural_language_response.json()["resolution_status"] == "resolved"

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

    finally:
        for task_id in task_ids:
            await e2e_client.delete(f"/api/tasks/{task_id}", headers=e2e_headers)
        report_bucket = os.getenv("COMPANY_DD_REPORT_BUCKET", "company-due-diligence")
        for report_id in report_ids:
            await minio.adelete_objects_by_prefix(report_bucket, f"reports/{report_id}/")
        await mysql.close()
