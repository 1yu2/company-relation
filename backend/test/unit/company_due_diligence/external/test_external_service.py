"""ExternalCompanyDataService 单元测试。"""

from __future__ import annotations

from datetime import UTC, date, datetime


from yuxi.company_due_diligence.external.models import (
    ExternalCompanyDataRequest,
    ProviderResult,
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


async def test_query_reports_conflict_on_same_fact_id():
    """相同 fact_id 不同值时识别冲突。"""
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
        fact_id="fact:same",
        subject_id="e-1",
        fact_type="name",
        normalized_value="A",
        source_id=source1.source_id,
    )
    fact2 = Fact(
        fact_id="fact:same",
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
