"""企业尽调统一问答装配与拒答单元测试。"""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from yuxi.company_due_diligence.models import (
    CompanyResolutionResult,
    ConfidenceLevel,
    DataQuality,
    Entity,
    EntityType,
    Fact,
    LatencyBreakdown,
    MatchMethod,
    Pagination,
    ResolutionStatus,
    RequestScopeStatus,
    Relation,
    ResolvedCompany,
    Source,
    SourceStatus,
    StructuredQueryResult,
    StructuredQueryType,
    Path,
    build_source_id,
)
from yuxi.company_due_diligence.graph.schema import GraphPathResult
from yuxi.company_due_diligence.qa.graph import QAWorkflowResult
from yuxi.company_due_diligence.qa.models import (
    CompanyQuestionRequest,
    GlobalQuestionResult,
    QueryStep,
    QueryStepStatus,
)
from yuxi.company_due_diligence.qa.service import CompanyQuestionService, SourceIntegrityError

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def disable_external_fallback_config(monkeypatch):
    """装配单测不读取 PostgreSQL 的外部补数配置。"""

    async def load_config():
        """返回未启用外部补数的固定配置。"""
        return {}

    monkeypatch.setattr("yuxi.company_due_diligence.qa.service._load_external_data_config", load_config)


@pytest.mark.parametrize("missing_name", ["Q02不存在企业", "缺失科技有限公司"])
async def test_explicit_missing_company_overrides_context_eid(missing_name):
    """问题点名不存在的企业时不得查询上下文企业。"""

    class Resolver:
        """按输入身份返回固定解析结果，保留真实的优先级错误。"""

        async def resolve(self, reference):
            """上下文可解析，点名企业不存在。"""
            return _resolution(
                ResolutionStatus.NOT_FOUND if reference.name == missing_name else ResolutionStatus.RESOLVED
            )

    workflow = FakeWorkflow(build_source_id("t_enterprise", "eid-1"))
    result = await CompanyQuestionService(resolver=Resolver(), workflow=workflow).query(
        CompanyQuestionRequest(company_ref={"eid": "eid-1"}, question=f"查询企业“{missing_name}”的工商信息")
    )
    assert result.resolution_status == ResolutionStatus.NOT_FOUND
    assert result.facts == []
    assert workflow.calls == 0


def _company() -> ResolvedCompany:
    """构造带事实主库来源的标准企业身份。"""
    source_id = build_source_id("t_enterprise", "eid-1")
    return ResolvedCompany(
        eid="eid-1",
        name="示例企业",
        normalized_name="示例企业",
        credit_code="credit-1",
        stock_code="000001.SZ",
        stock_abbreviation="示例",
        match_method=MatchMethod.EID,
        confidence=ConfidenceLevel.HIGH,
        source_ids=[source_id],
    )


def _resolution(status: ResolutionStatus = ResolutionStatus.RESOLVED) -> CompanyResolutionResult:
    """构造成功或未找到的企业解析结果。"""
    company = _company() if status == ResolutionStatus.RESOLVED else None
    source_id = build_source_id("t_enterprise", "eid-1")
    return CompanyResolutionResult(
        status=status,
        reference={"eid": "eid-1"},
        company=company,
        sources=(
            [
                Source(
                    source_id=source_id,
                    table="t_enterprise",
                    record_key="eid-1",
                    queried_at=datetime.now(UTC),
                )
            ]
            if company
            else []
        ),
        data_quality=DataQuality(),
        latency=LatencyBreakdown(total_ms=1),
    )


class FakeResolver:
    """返回固定企业解析状态。"""

    def __init__(self, result) -> None:
        self.result = result
        self.calls = 0
        self.references = []

    async def resolve(self, reference):
        """记录解析调用并返回固定结果。"""
        self.calls += 1
        self.references.append(reference)
        return self.result


class FakeGlobalService:
    """返回固定全局统计或声明问题不属于全局范围。"""

    def __init__(self, response=None) -> None:
        self.response = response
        self.calls = []

    async def query(self, question, *, top_k):
        self.calls.append((question, top_k))
        return self.response


class FakeWorkflow:
    """返回固定受控查询数据包。"""

    def __init__(self, source_id: str, *, unavailable_graph: bool = False) -> None:
        self.source_id = source_id
        self.unavailable_graph = unavailable_graph
        self.calls = 0

    async def execute(self, **_kwargs):
        """返回注册资本事实和可选图源不可用步骤。"""
        self.calls += 1
        structured = StructuredQueryResult(
            query_type=StructuredQueryType.REGISTERED_CAPITAL,
            eid="eid-1",
            facts=[
                Fact(
                    fact_id="fact-1",
                    subject_id="eid-1",
                    fact_type="registered_capital",
                    normalized_value=1000,
                    display_value="1000 万元",
                    source_id=self.source_id,
                )
            ],
            sources=[
                Source(
                    source_id=self.source_id,
                    table="t_enterprise",
                    record_key="eid-1",
                    queried_at=datetime.now(UTC),
                )
            ],
            data_quality=DataQuality(),
            pagination=Pagination(page=1, page_size=10, returned=1),
            latency=LatencyBreakdown(mysql_ms=2, total_ms=2),
        )
        steps = [
            QueryStep(
                source="mysql",
                operation="registered_capital",
                status=QueryStepStatus.COMPLETE,
                item_count=1,
            )
        ]
        if self.unavailable_graph:
            steps.append(
                QueryStep(
                    source="neo4j",
                    operation="path_query",
                    status=QueryStepStatus.UNAVAILABLE,
                    message="Neo4j 关系投影不可用",
                )
            )
        return QAWorkflowResult(structured=[structured], graph=None, evidence=None, steps=steps)


async def test_query_without_company_returns_global_scope_for_whitelisted_question():
    """无企业输入的固定全局问题应直接返回 global_scope。"""
    global_service = FakeGlobalService(
        SimpleNamespace(
            answer="当前覆盖 924 家企业。",
            result=GlobalQuestionResult(
                operation="catalog_coverage",
                data_version="catalog-v1",
                denominator=924,
            ),
            sources=[],
            data_quality=DataQuality(source_status=SourceStatus.AVAILABLE),
            latency=LatencyBreakdown(mysql_ms=3, total_ms=3),
        )
    )
    resolver = FakeResolver(_resolution())
    service = CompanyQuestionService(resolver=resolver, global_service=global_service)

    response = await service.query(CompanyQuestionRequest(question="当前数据覆盖哪些企业？"))

    assert response.scope_status == RequestScopeStatus.GLOBAL_SCOPE
    assert response.global_result.denominator == 924
    assert resolver.calls == 0


async def test_query_without_company_returns_needs_context_for_company_question():
    """企业依赖问题缺少唯一范围时应返回 needs_context。"""
    resolver = FakeResolver(_resolution())
    service = CompanyQuestionService(resolver=resolver, global_service=FakeGlobalService())

    response = await service.query(CompanyQuestionRequest(question="注册资本是多少？"))

    assert response.scope_status == RequestScopeStatus.NEEDS_CONTEXT
    assert response.company is None
    assert resolver.calls == 0


async def test_query_extracts_company_name_from_question_before_resolution():
    """问题内企业全称应自动形成 company_ref，不要求调用方重复传参。"""
    source_id = build_source_id("t_enterprise", "eid-1")
    resolver = FakeResolver(_resolution())
    global_service = FakeGlobalService()
    service = CompanyQuestionService(
        resolver=resolver,
        workflow=FakeWorkflow(source_id),
        global_service=global_service,
    )

    response = await service.query(
        CompanyQuestionRequest(question="深圳示例科技有限公司的注册资本、法人和经营状态是什么？")
    )

    assert response.resolution_status == ResolutionStatus.RESOLVED
    assert resolver.references[0].name == "深圳示例科技有限公司"
    assert global_service.calls == []


class GraphWorkflow:
    """返回固定两跳共同股东路径。"""

    async def execute(self, **_kwargs):
        """构造带方向、置信度和事实主库来源的图路径。"""
        source_id = build_source_id("t_partners_alls", "partner-1")
        company = Entity(entity_id="eid-1", entity_type=EntityType.COMPANY, name="示例企业")
        shareholder = Entity(entity_id="shareholder-1", entity_type=EntityType.SHAREHOLDER, name="示例股东")
        target = Entity(entity_id="eid-2", entity_type=EntityType.COMPANY, name="目标企业")
        first = Relation(
            relation_id="relation-1",
            source_entity_id="shareholder-1",
            target_entity_id="eid-1",
            relation_type="HOLDS",
            properties={"source_table": "t_partners_alls", "source_record_key": "partner-1"},
            confidence=ConfidenceLevel.HIGH,
            source_ids=[source_id],
        )
        second = Relation(
            relation_id="relation-2",
            source_entity_id="shareholder-1",
            target_entity_id="eid-2",
            relation_type="HOLDS",
            properties={"source_table": "t_partners_alls", "source_record_key": "partner-1"},
            confidence=ConfidenceLevel.HIGH,
            source_ids=[source_id],
        )
        path = Path(
            path_id="path-1",
            nodes=[company, shareholder, target],
            edges=[first, second],
            hop_count=2,
            confidence=ConfidenceLevel.HIGH,
            source_ids=[source_id],
        )
        graph = GraphPathResult(
            projection_version="cdd-test",
            paths=[path],
            data_quality=DataQuality(minimum_confidence=ConfidenceLevel.HIGH),
            latency=LatencyBreakdown(neo4j_ms=3, total_ms=3),
        )
        return QAWorkflowResult(
            structured=[],
            graph=graph,
            evidence=None,
            steps=[
                QueryStep(
                    source="neo4j",
                    operation="path_query",
                    status=QueryStepStatus.COMPLETE,
                    item_count=1,
                )
            ],
        )


async def test_unresolved_company_stops_before_downstream_query():
    """企业不唯一时必须拒答且不执行任何底层查询。"""
    resolver = FakeResolver(_resolution(ResolutionStatus.NOT_FOUND))
    workflow = FakeWorkflow(build_source_id("t_enterprise", "eid-1"))
    service = CompanyQuestionService(resolver=resolver, workflow=workflow)

    result = await service.query(CompanyQuestionRequest(company_ref={"eid": "eid-1"}, question="注册资本？"))

    assert result.resolution_status == ResolutionStatus.NOT_FOUND
    assert workflow.calls == 0
    assert "未执行后续查询" in result.answer


async def test_template_answer_cites_every_fact_source_and_keeps_latency():
    """LLM 不可用时模板答案仍应引用事实来源并返回分段耗时。"""
    source_id = build_source_id("t_enterprise", "eid-1")
    service = CompanyQuestionService(
        resolver=FakeResolver(_resolution()),
        workflow=FakeWorkflow(source_id),
    )

    result = await service.query(CompanyQuestionRequest(company_ref={"eid": "eid-1"}, question="注册资本？"))

    assert result.resolution_status == ResolutionStatus.RESOLVED
    assert result.facts[0].source_id == source_id
    assert f"[{source_id}]" in result.answer
    assert result.latency.mysql_ms == 2
    assert result.latency.llm_ms == 0
    assert result.latency.total_ms >= 0


async def test_partial_source_failure_is_distinct_from_no_data():
    """有 MySQL 事实但 Neo4j 不可用时应返回 partial。"""
    source_id = build_source_id("t_enterprise", "eid-1")
    service = CompanyQuestionService(
        resolver=FakeResolver(_resolution()),
        workflow=FakeWorkflow(source_id, unavailable_graph=True),
    )

    result = await service.query(CompanyQuestionRequest(company_ref={"eid": "eid-1"}, question="注册资本和关联关系？"))

    assert result.data_quality.source_status == SourceStatus.PARTIAL
    assert result.facts
    assert any("Neo4j" in warning for warning in result.data_quality.warnings)


async def test_missing_source_is_rejected():
    """事实引用未登记来源时不得进入最终回答。"""
    service = CompanyQuestionService(
        resolver=FakeResolver(_resolution()),
        workflow=FakeWorkflow("source:missing"),
    )

    with pytest.raises(SourceIntegrityError):
        await service.query(CompanyQuestionRequest(company_ref={"eid": "eid-1"}, question="注册资本？"))


async def test_multi_hop_response_keeps_nodes_edge_direction_and_source():
    """多跳问题应返回完整节点、边方向、跳数、置信度和来源。"""
    service = CompanyQuestionService(
        resolver=FakeResolver(_resolution()),
        workflow=GraphWorkflow(),
    )

    result = await service.query(CompanyQuestionRequest(company_ref={"eid": "eid-1"}, question="企业有哪些关联路径？"))

    assert result.paths[0].hop_count == 2
    assert [edge.source_entity_id for edge in result.paths[0].edges] == ["shareholder-1", "shareholder-1"]
    assert [edge.target_entity_id for edge in result.paths[0].edges] == ["eid-1", "eid-2"]
    assert result.data_quality.minimum_confidence == ConfidenceLevel.HIGH
    assert build_source_id("t_partners_alls", "partner-1") in {source.source_id for source in result.sources}
    assert result.projection_versions == {"neo4j": "cdd-test"}


@pytest.mark.parametrize(
    ("target_company_ref", "expected_order"),
    [
        # Agent 已从同一句话解析出主体和目标：以调用方为准，主体不能被问题里点名的目标顶替。
        ({"eid": "eid-target"}, ["eid-context", "eid-target"]),
        # 只有上下文主体：问题点名的企业作为关系目标。
        (None, ["eid-context", "腾讯科技（深圳）有限公司"]),
    ],
)
async def test_named_company_in_relation_question_is_target_not_subject(target_company_ref, expected_order):
    """关系问题中点名的企业是目标；修复前主体被替换成点名企业，变成目标企业到自身的路径查询。"""
    seen = []

    class Resolver:
        """记录解析顺序，任何身份都可解析。"""

        async def resolve(self, reference):
            """按调用顺序记录 eid 或名称。"""
            seen.append(reference.eid or reference.name)
            return _resolution()

    await CompanyQuestionService(resolver=Resolver(), workflow=GraphWorkflow()).query(
        CompanyQuestionRequest(
            company_ref={"eid": "eid-context"},
            target_company_ref=target_company_ref,
            question="它和“腾讯科技（深圳）有限公司”有什么关联路径？",
        )
    )

    assert seen == expected_order
