"""企业尽调原子工具的服务契约、约束与真实 ToolMessage 状态。"""

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.tools import ToolException

from yuxi.agents.toolkits.company_due_diligence import atomic_tools as tools
from yuxi.agents.toolkits.company_due_diligence import tools as legacy
from yuxi.agents.toolkits.registry import get_extra_metadata
from yuxi.company_due_diligence.entity_resolution import EntityResolutionService
from yuxi.company_due_diligence.evidence.models import EvidenceQuery, EvidenceQueryResult
from yuxi.company_due_diligence.evidence.query_service import EvidenceQueryService
from yuxi.company_due_diligence.graph.query_service import GraphPathQueryService
from yuxi.company_due_diligence.graph.repository import GraphUnavailableError
from yuxi.company_due_diligence.graph.schema import GraphPathQuery, GraphPathResult
from yuxi.company_due_diligence.models import (
    CompanyReference,
    DataQuality,
    Evidence,
    LatencyBreakdown,
    Source,
    StructuredQueryRequest,
    StructuredQueryType,
)
from yuxi.company_due_diligence.report.models import DueDiligenceReportRequest, ReportSection
from yuxi.company_due_diligence.report.service import ReportDispatchError
from yuxi.company_due_diligence.structured_query_service import StructuredQueryService

pytestmark = pytest.mark.asyncio


class MemoryMySQL:
    """仅提供领域服务所需的连接上下文，不访问数据库。"""

    @asynccontextmanager
    async def connection(self):
        """交出假连接。"""
        yield None


@pytest.fixture
def domain(monkeypatch):
    """用固定数据库行构建真实解析和白名单服务。"""
    row = {
        "eid": "eid-1",
        "name": "示例股份有限公司",
        "credit_no": "credit-1",
        "stock_code": "000001.SZ",
        "stock_abbr": "示例股份",
        "regist_capi": "100万元人民币",
        "regist_capi_new": "100",
        "actual_capi": None,
        "oper_name": "示例代表",
        "status": "存续",
    }
    holders = [
        {
            "id": i,
            "eid": "eid-1",
            "stock_name": f"股东{i}",
            "stock_id": f"stock-{i}",
            "stock_percent": str(100 - i / 1000),
            "is_history": 0,
        }
        for i in range(1, 104)
    ]
    repo = SimpleNamespace(
        find_by_eid=AsyncMock(side_effect=lambda eid: [row] if eid == "eid-1" else []),
        find_by_credit_code=AsyncMock(return_value=[row]),
        find_by_stock_code=AsyncMock(return_value=[row]),
        find_by_exact_name=AsyncMock(return_value=[row]),
        get_profile=AsyncMock(return_value=row),
    )
    calls = []

    async def list_current(eid, **options):
        """模拟仓储的 limit+1 分页，保留领域服务的实际映射。"""
        calls.append((eid, options))
        return holders[options["offset"] : options["offset"] + options["limit"]]

    repo.shareholder = SimpleNamespace(list_current=list_current)
    repo.enterprise = repo
    resolver = EntityResolutionService(mysql=MemoryMySQL(), repository_factory=lambda _: repo)
    structured = StructuredQueryService(mysql=MemoryMySQL(), repository_factory=lambda _: repo)
    monkeypatch.setattr(tools, "entity_resolution_service", resolver)
    monkeypatch.setattr(tools, "structured_query_service", structured)
    return SimpleNamespace(repo=repo, resolver=resolver, structured=structured, calls=calls)


async def tool_message(tool, args):
    """通过 LangChain 的真实调用入口取得工具消息。"""
    return await tool.ainvoke({"type": "tool_call", "id": "cdd-test", "name": tool.name, "args": args})


@pytest.mark.parametrize("identifier", ["name", "credit_code", "stock_code", "eid"])
async def test_resolve_preserves_identity_sources(domain, identifier):
    """四个唯一标识入口（含对话上下文 eid）均保留服务返回的身份和来源。"""
    value = {"name": "示例股份有限公司", "credit_code": "credit-1", "stock_code": "000001.SZ", "eid": "eid-1"}[
        identifier
    ]
    direct = await domain.resolver.resolve(CompanyReference(**{identifier: value}))
    result = await tools.cdd_resolve_company.ainvoke({identifier: value})
    assert result["status"] == "resolved"
    for key in ("eid", "name", "credit_code", "stock_code", "source_ids"):
        assert result["company"][key] == getattr(direct.company, key)
    assert {s["source_id"] for s in result["sources"]} == {s.source_id for s in direct.sources}
    assert "latency" not in result and "reference" not in result


async def test_resolve_ambiguous_does_not_choose_company(domain):
    """即使候选置信度高，也不擅自选择企业。"""
    first = (await domain.repo.find_by_exact_name("示例"))[0]
    domain.repo.find_by_exact_name.return_value = [first, {**first, "eid": "eid-2", "name": "另一示例有限公司"}]
    result = await tools.cdd_resolve_company.ainvoke({"name": "示例"})
    assert result["status"] == "ambiguous"
    assert result["company"] is None
    assert [item["eid"] for item in result["candidates"]] == ["eid-1", "eid-2"]


async def test_resolve_not_found_is_explicit(domain):
    """未找到企业不会生成可供后续查询使用的 eid。"""
    domain.repo.find_by_stock_code.return_value = []
    result = await tools.cdd_resolve_company.ainvoke({"stock_code": "missing"})
    assert result["status"] == "not_found" and result["company"] is None


@pytest.mark.parametrize("list_all,expected", [(False, 10), (True, 103)])
async def test_facts_match_direct_service_across_pages(domain, list_all, expected):
    """默认前十条和跨页取全均保留真实领域映射的事实及来源。"""
    result = await tools.cdd_query_facts.ainvoke(
        {
            "eid": "eid-1",
            "query_type": "current_shareholders",
            "list_all": list_all,
        }
    )
    direct_facts, direct_sources = {}, set()
    pages = (1, 2) if list_all else (1,)
    for page in pages:
        direct = await domain.structured.query(
            StructuredQueryRequest(
                eid="eid-1",
                query_type="current_shareholders",
                page_size=100 if list_all else 10,
                page=page,
            )
        )
        direct_facts.update({f.fact_id: f.model_dump(mode="json") for f in direct.facts})
        direct_sources.update(s.source_id for s in direct.sources)
    assert {f["fact_id"]: f for f in result["facts"]} == direct_facts
    assert {s["source_id"] for s in result["sources"]} == direct_sources
    assert len(result["relations"]) == expected
    assert len({e["entity_id"] for e in result["entities"]}) == expected + 1
    assert result["truncated"] is (not list_all)
    assert result["entities"][1]["name"] == "股东1"
    assert "latency" not in result and "data_quality" not in result


async def test_profile_keeps_values_and_source_ids(domain):
    """工商字段的归一化值完全沿用白名单服务，不在工具中重新计算。"""
    direct = await domain.structured.query(StructuredQueryRequest(eid="eid-1", query_type="enterprise_profile"))
    result = await tools.cdd_query_facts.ainvoke({"eid": "eid-1", "query_type": "enterprise_profile"})
    assert result["facts"] == [f.model_dump(mode="json") for f in direct.facts]
    assert {s["source_id"] for s in result["sources"]} == {s.source_id for s in direct.sources}
    assert not result["truncated"]


async def test_dates_reach_structured_service(domain, monkeypatch):
    """时间范围透传至领域请求，不能被工具忽略。"""
    query = AsyncMock(wraps=domain.structured.query)
    monkeypatch.setattr(domain.structured, "query", query)
    await tools.cdd_query_facts.ainvoke(
        {
            "eid": "eid-1",
            "query_type": "enterprise_profile",
            "start_date": "2020-01-01",
            "end_date": "2025-01-01",
        }
    )
    request = query.call_args.args[0]
    assert request.start_date.isoformat() == "2020-01-01"
    assert request.end_date.isoformat() == "2025-01-01"


@pytest.mark.parametrize(
    "tool,args",
    [
        (tools.cdd_resolve_company, {}),
        (tools.cdd_resolve_company, {"name": "示例", "stock_code": "1"}),
        (tools.cdd_resolve_company, {"name": "  "}),
        (tools.cdd_query_facts, {"query_type": "enterprise_profile"}),
        (tools.cdd_query_facts, {"eid": "  ", "query_type": "enterprise_profile"}),
        (tools.cdd_query_facts, {"eid": "eid-1", "query_type": "DROP TABLE t_enterprise"}),
        (
            tools.cdd_query_facts,
            {"eid": "eid-1", "query_type": "enterprise_profile", "start_date": "2025-01-01", "end_date": "2020-01-01"},
        ),
        (tools.cdd_query_relations, {"start_eid": "eid-1", "max_hops": 4}),
        (tools.cdd_search_evidence, {"eid": "eid-1", "query": "风险", "top_k": 51}),
        (tools.cdd_search_evidence, {"eid": "eid-1", "query": "风险", "document_types": ["unsupported"]}),
        (
            tools.cdd_search_evidence,
            {"eid": "eid-1", "query": "风险", "start_date": "2025-01-01", "end_date": "2020-01-01"},
        ),
        (tools.cdd_create_report, {"eid": "eid-1", "topics": []}),
        (tools.cdd_query_relations, {}),
        (tools.cdd_search_evidence, {"query": "风险"}),
        (tools.cdd_create_report, {}),
    ],
)
async def test_invalid_arguments_become_error_tool_message(domain, tool, args):
    """缺失参数、非法枚举和越界输入通过真实工具调用产生 error。"""
    message = await tool_message(tool, args)
    assert message.status == "error"
    assert message.content


@pytest.mark.parametrize(
    "tool,args",
    [
        (tools.cdd_query_facts, {"eid": "missing", "query_type": "enterprise_profile"}),
        (tools.cdd_query_relations, {"start_eid": "missing"}),
        (tools.cdd_query_relations, {"start_eid": "eid-1", "target_eid": "missing"}),
        (tools.cdd_search_evidence, {"eid": "missing", "query": "风险"}),
        (tools.cdd_create_report, {"eid": "missing"}),
    ],
)
async def test_unknown_eid_rejected_before_domain_query(domain, tool, args):
    """不存在的 eid 不能被当作空事实、空关系或已接受任务。"""
    message = await tool_message(tool, args)
    assert message.status == "error" and "not_found" in message.content


async def test_source_unavailable_becomes_error(domain):
    """解析源不可用时工具报错，避免误报企业不存在。"""
    from yuxi.company_due_diligence.mysql import MySQLUnavailableError

    domain.repo.find_by_exact_name.side_effect = MySQLUnavailableError("offline")
    message = await tool_message(tools.cdd_resolve_company, {"name": "示例"})
    assert message.status == "error" and "source_unavailable" in message.content


async def test_relations_preserve_direction_confidence_and_sources(domain, monkeypatch):
    """路径来自服务契约，精简后仍能判断边方向与来源。"""
    from yuxi.company_due_diligence.models import Entity, Path, Relation

    path = Path(
        path_id="p1",
        nodes=[
            Entity(entity_id="eid-1", entity_type="company", name="示例"),
            Entity(entity_id="s1", entity_type="shareholder", name="股东"),
        ],
        edges=[
            Relation(
                relation_id="r1",
                source_entity_id="s1",
                target_entity_id="eid-1",
                relation_type="HOLDS",
                confidence="high",
                source_ids=["src1"],
            )
        ],
        hop_count=1,
        confidence="high",
        source_ids=["src1"],
    )
    result = GraphPathResult(
        projection_version="v1", paths=[path], data_quality=DataQuality(), latency=LatencyBreakdown()
    )
    service = SimpleNamespace(query=AsyncMock(return_value=result))
    monkeypatch.setattr(tools, "graph_path_query_service", service)
    output = await tools.cdd_query_relations.ainvoke({"start_eid": "eid-1", "relation_types": ["HOLDS"], "max_hops": 2})
    assert output["paths"] == [path.model_dump(mode="json")]
    assert service.query.call_args.args[0] == GraphPathQuery(start_eid="eid-1", relation_types=["HOLDS"], max_hops=2)


async def test_no_graph_projection_reports_unavailable(domain, monkeypatch):
    """真实路径服务抛出的无投影异常必须报告来源不可用。"""
    service = GraphPathQueryService(
        repository=SimpleNamespace(
            query_paths=AsyncMock(side_effect=GraphUnavailableError("Neo4j 当前没有已激活的企业尽调投影"))
        )
    )
    monkeypatch.setattr(tools, "graph_path_query_service", service)
    message = await tool_message(tools.cdd_query_relations, {"start_eid": "eid-1"})
    assert message.status == "error" and "来源不可用" in message.content


async def test_evidence_preserves_snippets_and_source(domain, monkeypatch):
    """证据返回文档标识、类型、日期、片段与来源，并标注为补充证据。"""
    source = Source(source_id="src1", table="t_news", record_key="1", queried_at=datetime.now(UTC))
    evidence = Evidence(
        evidence_id="e1",
        document_id="doc1",
        entity_id="eid-1",
        document_type="news",
        text="固定证据片段",
        score=0.8,
        source_id="src1",
        projection_version="v1",
    )
    direct = EvidenceQueryResult(
        projection_version="v1",
        evidence=[evidence],
        sources=[source],
        data_quality=DataQuality(),
        latency=LatencyBreakdown(),
    )
    service = SimpleNamespace(query=AsyncMock(return_value=direct))
    monkeypatch.setattr(tools, "evidence_query_service", service)
    result = await tools.cdd_search_evidence.ainvoke(
        {"eid": "eid-1", "query": "风险", "document_types": ["news"], "top_k": 3}
    )
    assert result["evidence"] == [evidence.model_dump(mode="json", exclude={"projection_version"})]
    assert result["sources"][0]["source_id"] == direct.sources[0].source_id
    assert result["supplementary_only"] is True
    assert service.query.call_args.args[0] == EvidenceQuery(eid="eid-1", query="风险", document_types=["news"], top_k=3)


async def test_evidence_backend_failure_returns_error(domain, monkeypatch):
    """让真实证据服务处理基础设施失败，再验证工具不会把失败当成无证据。"""
    service = EvidenceQueryService(
        repository=SimpleNamespace(get_current=AsyncMock(side_effect=RuntimeError("offline")))
    )
    monkeypatch.setattr(tools, "evidence_query_service", service)
    message = await tool_message(tools.cdd_search_evidence, {"eid": "eid-1", "query": "风险"})
    assert message.status == "error" and "来源不可用" in message.content


@pytest.mark.parametrize(
    "topics,sections",
    [
        (None, list(ReportSection)),
        (["shareholders"], [ReportSection.IDENTITY, ReportSection.SHAREHOLDERS, ReportSection.DATA_QUALITY]),
    ],
)
async def test_report_uses_existing_async_submission(domain, monkeypatch, topics, sections):
    """报告工具只提交既有章节请求，并返回任务编号和当前状态。"""
    create = AsyncMock(return_value=SimpleNamespace(id="task-1", status="pending"))
    monkeypatch.setattr(tools, "due_diligence_report_service", SimpleNamespace(create=create))
    result = await tools.cdd_create_report.ainvoke({"eid": "eid-1", **({"topics": topics} if topics else {})})
    assert result == {"task_id": "task-1", "status": "pending"}
    request, tasker = create.call_args.args
    assert request == DueDiligenceReportRequest(company_ref={"eid": "eid-1"}, sections=sections)
    assert tasker is tools.tasker


async def test_report_dispatch_failure_returns_error(domain, monkeypatch):
    """报告投递失败不能返回已接受。"""
    monkeypatch.setattr(
        tools,
        "due_diligence_report_service",
        SimpleNamespace(create=AsyncMock(side_effect=ReportDispatchError("报告执行队列不可用"))),
    )
    message = await tool_message(tools.cdd_create_report, {"eid": "eid-1"})
    assert message.status == "error" and "队列不可用" in message.content


async def test_external_errors_and_success_contract(monkeypatch):
    """仅两条既有约束改为工具错误，正常返回及服务入参保持原样。"""
    missing = await tool_message(legacy.query_external_company_data, {"question": "补充工商", "topics": ["profile"]})
    assert missing.status == "error" and "必须先确认" in missing.content
    from yuxi.company_due_diligence.qa import service as qa_service

    monkeypatch.setattr(
        qa_service, "_load_external_data_config", AsyncMock(return_value={"providers": {"akshare": {"enabled": True}}})
    )
    monkeypatch.setattr(qa_service, "_enabled_external_providers", lambda _: ["akshare"])
    args = {"question": "补充工商", "confirmed_eid": "eid-1", "topics": ["profile"], "providers": ["disabled"]}
    disabled = await tool_message(legacy.query_external_company_data, args)
    assert disabled.status == "error" and "Provider 未启用" in disabled.content
    payload = {"facts": [{"fact_id": "f1", "source_id": "src1"}], "sources": [{"source_id": "src1"}]}
    response = SimpleNamespace(model_dump=lambda **_: payload)
    query = AsyncMock(return_value=response)
    monkeypatch.setattr(legacy.external_company_data_service, "query", query)
    output = await legacy.query_external_company_data.ainvoke({**args, "providers": ["akshare"]})
    assert output == payload
    assert query.call_args.args[0].company_ref.eid == "eid-1"


async def test_atomic_registration_and_query_enum():
    """工具登记到领域分类，查询 schema 只允许现有十七类枚举。"""
    new_tools = [
        tools.cdd_resolve_company,
        tools.cdd_query_facts,
        tools.cdd_query_relations,
        tools.cdd_search_evidence,
        tools.cdd_create_report,
    ]
    assert all(get_extra_metadata(t.name).category == "company_due_diligence" for t in new_tools)
    schema = tools.cdd_query_facts.args_schema.model_json_schema()
    assert schema["$defs"]["StructuredQueryType"]["enum"] == [t.value for t in StructuredQueryType]
    assert len(StructuredQueryType) == 17
    assert "工商" in schema["properties"]["query_type"]["description"]
    # 直接调用 schema 同样抛 ToolException，真实工具状态断言见上面的约束用例。
    with pytest.raises(ToolException):
        tools.cdd_query_facts.args_schema.model_validate({"query_type": "unknown"})


async def test_unresolved_eid_cannot_reach_fact_service(domain, monkeypatch):
    """重复 eid 的歧义结果必须报错，不能继续取事实。"""
    row = (await domain.repo.find_by_eid("eid-1"))[0]
    domain.repo.find_by_eid.side_effect = None
    domain.repo.find_by_eid.return_value = [row, row]
    query = AsyncMock()
    monkeypatch.setattr(tools, "structured_query_service", SimpleNamespace(query=query))
    message = await tool_message(tools.cdd_query_facts, {"eid": "eid-1", "query_type": "enterprise_profile"})
    assert message.status == "error" and "ambiguous" in message.content
    query.assert_not_called()


async def test_fact_source_unavailable_is_not_empty_success(domain):
    """真实结构化服务报告主库不可用时，工具状态也必须失败。"""
    from yuxi.company_due_diligence.mysql import MySQLUnavailableError

    domain.repo.get_profile.side_effect = MySQLUnavailableError("offline")
    message = await tool_message(tools.cdd_query_facts, {"eid": "eid-1", "query_type": "enterprise_profile"})
    assert message.status == "error" and "来源不可用" in message.content


async def test_registration_does_not_enable_tools_for_agent():
    """注册到分类只提供候选能力，没有配置的 Agent 不会自动获得新工具。"""
    from yuxi.agents.toolkits.service import resolve_configured_runtime_tools

    assert await resolve_configured_runtime_tools(SimpleNamespace(tools=[], mcps=[])) == []


@pytest.mark.parametrize("query_type", ["patent_summary", "licenses", "administrative_penalties"])
async def test_list_all_is_rejected_outside_shareholder_queries(domain, query_type):
    """list_all 只对股东类开放：单家企业专利关系最多约 32 万条，逐页取全会撑爆模型上下文。"""
    message = await tool_message(tools.cdd_query_facts, {"eid": "eid-1", "query_type": query_type, "list_all": True})

    assert message.status == "error"
    assert "list_all 只支持股东类查询" in message.content
