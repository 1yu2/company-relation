"""Q-02 v4 六家企业的只读原子工具冒烟；使用已发布参考事实逐字段核对。"""

import pytest
import pytest_asyncio

from yuxi.agents.toolkits.company_due_diligence.atomic_tools import cdd_query_facts, cdd_resolve_company
from yuxi.company_due_diligence.entity_resolution import entity_resolution_service
from yuxi.company_due_diligence.models import CompanyReference
from yuxi.company_due_diligence.mysql import company_due_diligence_mysql

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]
VERSION_ID = "pev_ad05931d8a0ea8ce0747284ddd30"


@pytest_asyncio.fixture
async def q02_tasks(e2e_client, e2e_headers):
    """从真实 API 读取冻结参考答案，不用领域查询生成期望值。"""
    response = await e2e_client.get(f"/api/platform-evaluations/versions/{VERSION_ID}", headers=e2e_headers)
    assert response.status_code == 200
    version = response.json()["data"]
    assert version["status"] == "published"
    yield version["tasks"]
    # 每个用例使用独立事件循环，必须在当前循环结束前释放其连接池。
    await company_due_diligence_mysql.close()


@pytest.mark.parametrize("company_index", range(6))
async def test_six_companies_match_reference_facts(q02_tasks, company_index, record_property):
    """名称解析后查询工商和当前股东，逐个参考 fact_id 核对所有字段。"""
    tasks = [t for t in q02_tasks if t["input"]["topic"] == "structured_fact"]
    eids = sorted({t["input"]["company_reference"]["eid"] for t in tasks})
    assert len(eids) == 6
    eid = eids[company_index]
    # 数据集只有 eid；只读取得标准名称作为名称解析工具的输入，不用它构造事实期望。
    seed = await entity_resolution_service.resolve(CompanyReference(eid=eid))
    assert seed.company is not None
    identity = await cdd_resolve_company.ainvoke({"name": seed.company.name})
    assert identity["status"] == "resolved"
    assert identity["company"]["eid"] == eid
    checked = 0
    for task in tasks:
        if task["input"]["company_reference"]["eid"] != eid:
            continue
        holders = "股东" in task["input"]["query"]
        result = await cdd_query_facts.ainvoke(
            {
                "eid": identity["company"]["eid"],
                "query_type": "current_shareholders" if holders else "enterprise_profile",
            }
        )
        actual = {f["fact_id"]: f for f in result["facts"]}
        expected = task["expected"]["facts"]
        assert expected, task["task_key"]
        # 工商全量允许额外字段；股东查询应与该题前十条的事实集合完全一致。
        if holders:
            assert set(actual) == {f["fact_id"] for f in expected}, task["task_key"]
        source_ids = {s["source_id"] for s in result["sources"]}
        for fact in expected:
            assert fact["fact_id"] in actual, (task["task_key"], fact["fact_id"])
            assert {key: actual[fact["fact_id"]][key] for key in fact} == fact
            assert fact["source_id"] in source_ids
            checked += 1
    assert checked > 3
    record_property("eid", eid)
    record_property("reference_facts_checked", checked)
