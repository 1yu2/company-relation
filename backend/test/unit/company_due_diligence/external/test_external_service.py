"""ExternalCompanyDataService 单元测试。"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime


from yuxi.company_due_diligence.external.models import (
    ExternalCompanyDataRequest,
    ProviderErrorCode,
    ProviderResult,
    ProviderStatus,
)
from yuxi.company_due_diligence.external.provider import ExternalCompanyDataProvider
from yuxi.company_due_diligence.external.registry import ExternalProviderRegistry
from yuxi.company_due_diligence.external.service import ExternalCompanyDataService
from yuxi.company_due_diligence.models import (
    CoverageStatus,
    DataQuality,
    Fact,
    LatencyBreakdown,
    Source,
    SourceStatus,
    StructuredQueryResult,
    StructuredQueryType,
)
from yuxi.company_due_diligence.qa.models import QueryPlan, QuestionType


def _empty_structured_result(query_type: StructuredQueryType) -> StructuredQueryResult:
    from yuxi.company_due_diligence.models import Pagination

    return StructuredQueryResult(
        query_type=query_type,
        eid="e-1",
        data_quality=DataQuality(source_status=SourceStatus.AVAILABLE),
        pagination=Pagination(page=1, page_size=20, returned=0, has_more=False),
        latency=LatencyBreakdown(total_ms=1),
    )


def _structured_result_with_facts(query_type: StructuredQueryType) -> StructuredQueryResult:
    from yuxi.company_due_diligence.models import Pagination

    source = Source(
        source_id="source:internal",
        table="t_company",
        record_key="e-1",
        queried_at=datetime.now(UTC),
        business_date=date(2026, 1, 1),
    )
    return StructuredQueryResult(
        query_type=query_type,
        eid="e-1",
        facts=[
            Fact(
                fact_id="fact:1",
                subject_id="e-1",
                fact_type="name",
                normalized_value="示例企业",
                source_id=source.source_id,
            )
        ],
        sources=[source],
        data_quality=DataQuality(source_status=SourceStatus.AVAILABLE),
        pagination=Pagination(page=1, page_size=20, returned=1, has_more=False),
        latency=LatencyBreakdown(total_ms=1),
    )


class FakeProvider(ExternalCompanyDataProvider):
    def __init__(self, name: str, facts: list[Fact] | None = None, sources: list[Source] | None = None):
        self._name = name
        self._facts = facts or []
        self._sources = sources or []

    @property
    def name(self) -> str:
        return self._name

    @property
    def supported_topics(self) -> list[str]:
        return ["profile", "financials"]

    async def health(self):
        from yuxi.company_due_diligence.external.models import ProviderHealth, ProviderStatus

        return ProviderHealth(provider_name=self._name, status=ProviderStatus.HEALTHY)

    async def query(self, request: ExternalCompanyDataRequest):
        from yuxi.company_due_diligence.external.models import CoverageStatus

        return ProviderResult(
            provider_name=self._name,
            facts=self._facts,
            sources=self._sources,
            coverage={topic: CoverageStatus.COMPLETE for topic in request.topics},
        )


def test_detect_topic_gaps_missing_when_no_data():
    """内部无数据时主题标记为 missing。"""
    service = ExternalCompanyDataService()
    plan = QueryPlan(
        question_type=QuestionType.STRUCTURED_FIELD,
        structured_queries=[StructuredQueryType.ENTERPRISE_PROFILE],
    )
    gaps = service.detect_topic_gaps(
        plan=plan,
        structured_results=[_empty_structured_result(StructuredQueryType.ENTERPRISE_PROFILE)],
    )
    assert gaps["profile"] == CoverageStatus.MISSING


def test_detect_topic_gaps_complete_when_has_data():
    """内部有数据时主题标记为 complete。"""
    service = ExternalCompanyDataService()
    plan = QueryPlan(
        question_type=QuestionType.STRUCTURED_FIELD,
        structured_queries=[StructuredQueryType.ENTERPRISE_PROFILE],
    )
    gaps = service.detect_topic_gaps(
        plan=plan,
        structured_results=[_structured_result_with_facts(StructuredQueryType.ENTERPRISE_PROFILE)],
        stale_threshold_days=365,
    )
    assert gaps["profile"] == CoverageStatus.COMPLETE


def test_should_fallback_for_eligible_statuses():
    """只有指定状态允许触发外部补数。"""
    service = ExternalCompanyDataService()
    assert service.should_fallback(CoverageStatus.MISSING)
    assert service.should_fallback(CoverageStatus.PARTIAL)
    assert service.should_fallback(CoverageStatus.STALE)
    assert service.should_fallback(CoverageStatus.CONFLICT)
    assert service.should_fallback(CoverageStatus.UNSUPPORTED)
    assert not service.should_fallback(CoverageStatus.COMPLETE)


def test_detect_topic_gaps_merges_duplicate_internal_queries():
    """同一外部主题对应多个内部查询时保留最严格缺口状态。"""
    service = ExternalCompanyDataService()
    plan = QueryPlan(
        question_type=QuestionType.STRUCTURED_LIST,
        structured_queries=[
            StructuredQueryType.CURRENT_SHAREHOLDERS,
            StructuredQueryType.HISTORICAL_SHAREHOLDERS,
        ],
    )
    gaps = service.detect_topic_gaps(
        plan=plan,
        structured_results=[
            _empty_structured_result(StructuredQueryType.CURRENT_SHAREHOLDERS),
            _structured_result_with_facts(StructuredQueryType.HISTORICAL_SHAREHOLDERS),
        ],
    )

    assert gaps["shareholders"] == CoverageStatus.MISSING


async def test_query_merges_facts_from_multiple_providers():
    """多 Provider 结果正确合并去重。"""
    source1 = Source(
        source_id="source:a",
        table="external:a:profile",
        record_key="key-a",
        queried_at=datetime.now(UTC),
    )
    source2 = Source(
        source_id="source:b",
        table="external:b:profile",
        record_key="key-b",
        queried_at=datetime.now(UTC),
    )
    fact1 = Fact(
        fact_id="fact:a",
        subject_id="e-1",
        fact_type="name",
        normalized_value="A",
        source_id=source1.source_id,
    )
    fact2 = Fact(
        fact_id="fact:b",
        subject_id="e-1",
        fact_type="industry",
        normalized_value="B",
        source_id=source2.source_id,
    )
    registry = ExternalProviderRegistry()
    registry.register(FakeProvider("a", [fact1], [source1]))
    registry.register(FakeProvider("b", [fact2], [source2]))
    service = ExternalCompanyDataService(registry=registry)

    response = await service.query(ExternalCompanyDataRequest(company_ref={"eid": "e-1"}, topics=["profile"]))

    assert len(response.facts) == 2
    assert {f.fact_id for f in response.facts} == {"fact:a", "fact:b"}
    assert len(response.sources) == 2


async def test_query_counts_facts_and_sources_per_topic():
    """多主题 Provider 结果按主题统计覆盖，不重复计数。"""
    profile_source = Source(
        source_id="source:profile",
        table="external:one:profile",
        record_key="profile",
        queried_at=datetime.now(UTC),
    )
    financial_source = Source(
        source_id="source:financials",
        table="external:one:financials",
        record_key="financials",
        queried_at=datetime.now(UTC),
    )
    facts = [
        Fact(
            fact_id="fact:profile",
            subject_id="e-1",
            fact_type="industry",
            normalized_value="软件",
            source_id=profile_source.source_id,
        ),
        Fact(
            fact_id="fact:financials",
            subject_id="e-1",
            fact_type="financial_metric:净利润",
            normalized_value=100,
            source_id=financial_source.source_id,
        ),
    ]

    class MultiTopicProvider(FakeProvider):
        async def query(self, request: ExternalCompanyDataRequest):
            return ProviderResult(
                provider_name=self.name,
                facts=facts,
                sources=[profile_source, financial_source],
                coverage={topic: CoverageStatus.COMPLETE for topic in request.topics},
            )

    registry = ExternalProviderRegistry()
    registry.register(MultiTopicProvider("one"))
    response = await ExternalCompanyDataService(registry=registry).query(
        ExternalCompanyDataRequest(
            company_ref={"eid": "e-1"},
            topics=["profile", "financials"],
        )
    )

    assert response.coverage["profile"].source_count == 1
    assert response.coverage["profile"].fact_count == 1
    assert response.coverage["financials"].source_count == 1
    assert response.coverage["financials"].fact_count == 1


async def test_query_reports_conflict_on_same_fact_id():
    """不同 fact_id 的同主体同字段事实也应识别冲突。"""
    source1 = Source(
        source_id="source:a",
        table="external:a:profile",
        record_key="key-a",
        queried_at=datetime.now(UTC),
    )
    source2 = Source(
        source_id="source:b",
        table="external:b:profile",
        record_key="key-b",
        queried_at=datetime.now(UTC),
    )
    fact1 = Fact(
        fact_id="fact:a",
        subject_id="e-1",
        fact_type="name",
        normalized_value="A",
        source_id=source1.source_id,
    )
    fact2 = Fact(
        fact_id="fact:b",
        subject_id="e-1",
        fact_type="name",
        normalized_value="B",
        source_id=source2.source_id,
    )
    registry = ExternalProviderRegistry()
    registry.register(FakeProvider("a", [fact1], [source1]))
    registry.register(FakeProvider("b", [fact2], [source2]))
    service = ExternalCompanyDataService(registry=registry)

    response = await service.query(ExternalCompanyDataRequest(company_ref={"eid": "e-1"}, topics=["profile"]))

    assert len(response.conflicts) == 1
    assert "a vs b" in response.conflicts[0]


async def test_query_deduplicates_same_semantic_fact_with_same_value():
    """不同 Provider 返回同一语义且同值的事实时只保留一条。"""
    source1 = Source(
        source_id="source:a",
        table="external:a:profile",
        record_key="key-a",
        queried_at=datetime.now(UTC),
    )
    source2 = Source(
        source_id="source:b",
        table="external:b:profile",
        record_key="key-b",
        queried_at=datetime.now(UTC),
    )
    facts = [
        Fact(
            fact_id="fact:a",
            subject_id="e-1",
            fact_type="industry",
            normalized_value="软件",
            source_id=source1.source_id,
        ),
        Fact(
            fact_id="fact:b",
            subject_id="e-1",
            fact_type="industry",
            normalized_value="软件",
            source_id=source2.source_id,
        ),
    ]
    registry = ExternalProviderRegistry()
    registry.register(FakeProvider("a", [facts[0]], [source1]))
    registry.register(FakeProvider("b", [facts[1]], [source2]))

    response = await ExternalCompanyDataService(registry=registry).query(
        ExternalCompanyDataRequest(company_ref={"eid": "e-1"}, topics=["profile"])
    )

    assert len(response.facts) == 1
    assert response.conflicts == []


async def test_query_reports_unknown_provider_instead_of_silently_ignoring_it():
    """显式指定未知 Provider 时返回配置错误和 unsupported 覆盖。"""
    service = ExternalCompanyDataService(registry=ExternalProviderRegistry())

    response = await service.query(
        ExternalCompanyDataRequest(
            company_ref={"eid": "e-1"},
            topics=["profile"],
            providers=["unknown"],
        )
    )

    assert response.provider_results[0].provider_name == "unknown"
    assert response.provider_results[0].error_code == ProviderErrorCode.CONFIG_MISSING
    assert response.coverage["profile"].status == CoverageStatus.UNSUPPORTED
    assert response.missing_topics == ["profile"]
    audit = service.audit_entries()
    assert audit[0].provider_name == "unknown"
    assert audit[0].topics == ["profile"]
    assert audit[0].error_code == ProviderErrorCode.CONFIG_MISSING
    assert "e-1" not in audit[0].model_dump_json()


async def test_query_with_empty_enabled_provider_list_does_not_call_all_providers():
    """管理端全部禁用 Provider 时不得回退为调用注册表全部 Provider。"""
    provider = FakeProvider("disabled")
    registry = ExternalProviderRegistry()
    registry.register(provider)
    service = ExternalCompanyDataService(registry=registry)

    response = await service.query(
        ExternalCompanyDataRequest(company_ref={"eid": "e-1"}, topics=["profile"]),
        enabled_providers=[],
    )

    assert response.provider_results == []
    assert response.facts == []


async def test_query_classifies_timeout_and_preserves_topic_gap():
    """Provider 超时应分类为 timeout，并保留未覆盖主题。"""

    class SlowProvider(FakeProvider):
        async def query(self, request: ExternalCompanyDataRequest):
            await asyncio.sleep(0.05)
            return await super().query(request)

    registry = ExternalProviderRegistry()
    registry.register(SlowProvider("slow"))
    service = ExternalCompanyDataService(registry=registry)

    response = await service.query(
        ExternalCompanyDataRequest(company_ref={"eid": "e-1"}, topics=["profile"]),
        timeout_seconds=0.001,
    )

    assert response.provider_results[0].provider_name == "slow"
    assert response.provider_results[0].error_code == ProviderErrorCode.TIMEOUT
    assert response.coverage["profile"].status == CoverageStatus.MISSING
    assert response.missing_topics == ["profile"]


async def test_query_enforces_provider_rate_limit():
    """刷新查询绕过缓存时仍应执行 Provider 级限流。"""
    registry = ExternalProviderRegistry()
    registry.register(FakeProvider("limited"))
    service = ExternalCompanyDataService(registry=registry, rate_limit_per_minute=1)
    request = ExternalCompanyDataRequest(
        company_ref={"eid": "e-1"},
        topics=["profile"],
        refresh=True,
    )

    first = await service.query(request)
    second = await service.query(request)

    assert first.provider_results[0].status == ProviderStatus.HEALTHY
    assert second.provider_results[0].error_code == ProviderErrorCode.RATE_LIMIT


async def test_cache_management_exposes_only_statistics():
    """缓存运维只返回统计，并支持按 Provider 清理。"""
    registry = ExternalProviderRegistry()
    registry.register(FakeProvider("cached"))
    service = ExternalCompanyDataService(registry=registry)

    await service.query(ExternalCompanyDataRequest(company_ref={"eid": "e-1"}, topics=["profile"]))

    assert service.cache_stats() == {"total": 1, "expired": 0}
    assert service.clear_cache("cached") == 1
    assert service.cache_stats() == {"total": 0, "expired": 0}
