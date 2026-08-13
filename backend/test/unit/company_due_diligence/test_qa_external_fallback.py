"""问答外部补数集成测试。"""

from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock


from yuxi.company_due_diligence.models import (
    DataQuality,
    Fact,
    LatencyBreakdown,
    ResolvedCompany,
    Source,
    SourceStatus,
    StructuredQueryResult,
    StructuredQueryType,
)
from yuxi.company_due_diligence.qa.models import CompanyQuestionRequest, QueryPlan, QuestionType
from yuxi.company_due_diligence.qa.service import CompanyQuestionService


def _resolved_company() -> ResolvedCompany:
    return ResolvedCompany(
        eid="e-1",
        name="示例股份有限公司",
        normalized_name="示例股份有限公司",
        credit_code="91110000XXXX",
        stock_code="000001.SZ",
        stock_abbreviation="示例",
        match_method="eid",
        confidence="high",
        source_ids=["source:1"],
    )


def _resolution_result(company: ResolvedCompany):
    from yuxi.company_due_diligence.models import CompanyResolutionResult, ResolutionStatus

    return CompanyResolutionResult(
        status=ResolutionStatus.RESOLVED,
        reference={"eid": company.eid},
        company=company,
        sources=[],
        data_quality=DataQuality(source_status=SourceStatus.AVAILABLE),
        latency=LatencyBreakdown(total_ms=1),
    )


def _structured_result(query_type: StructuredQueryType, facts: list[Fact]) -> StructuredQueryResult:
    from yuxi.company_due_diligence.models import Pagination, build_source_id

    source_id = build_source_id("t_company", "e-1")
    for fact in facts:
        fact.source_id = source_id

    return StructuredQueryResult(
        query_type=query_type,
        eid="e-1",
        facts=facts,
        sources=[
            Source(
                source_id=source_id,
                table="t_company",
                record_key="e-1",
                queried_at=datetime.now(UTC),
                business_date=date(2026, 1, 1),
            )
        ],
        data_quality=DataQuality(source_status=SourceStatus.AVAILABLE),
        pagination=Pagination(page=1, page_size=20, returned=len(facts), has_more=False),
        latency=LatencyBreakdown(total_ms=1),
    )


async def test_complete_internal_does_not_trigger_external(monkeypatch):
    """内部数据完整时不请求外部数据。"""
    company = _resolved_company()
    resolution = _resolution_result(company)

    class _Resolver:
        async def resolve(self, ref):
            return resolution

    external_service = MagicMock()
    external_service.query = AsyncMock(return_value=None)
    external_service.detect_topic_gaps.return_value = {}

    service = CompanyQuestionService(
        resolver=_Resolver(),
        workflow=AsyncMock(),
        external_service=external_service,
    )

    plan = QueryPlan(
        question_type=QuestionType.STRUCTURED_FIELD,
        structured_queries=[StructuredQueryType.ENTERPRISE_PROFILE],
    )
    workflow_result = type("W", (), {
        "structured": [_structured_result(StructuredQueryType.ENTERPRISE_PROFILE, [])],
        "graph": None,
        "evidence": None,
        "steps": [],
    })()
    service._workflow.execute = AsyncMock(return_value=workflow_result)

    await service.query(
        CompanyQuestionRequest(
            company_ref={"eid": "e-1"},
            question="这家企业的基本资料是什么？",
        ),
        plan=plan,
    )

    external_service.query.assert_not_awaited()


async def test_missing_internal_triggers_external_and_returns_sources(monkeypatch):
    """内部融资缺失时触发外部补数并返回来源。"""
    company = _resolved_company()
    resolution = _resolution_result(company)

    class _Resolver:
        async def resolve(self, ref):
            return resolution

    from yuxi.company_due_diligence.external.models import ExternalCompanyDataResponse
    from yuxi.company_due_diligence.models import CoverageStatus

    from yuxi.company_due_diligence.models import build_source_id

    ext_source_id = build_source_id("external:financing:financing", "e-1:series-a")
    ext_source = Source(
        source_id=ext_source_id,
        table="external:financing:financing",
        record_key="e-1:series-a",
        queried_at=datetime.now(UTC),
    )
    ext_fact = Fact(
        fact_id="fact:external:financing",
        subject_id="e-1",
        fact_type="financing_round",
        normalized_value="A轮",
        source_id=ext_source.source_id,
    )
    external_response = ExternalCompanyDataResponse(
        facts=[ext_fact],
        sources=[ext_source],
    )
    external_service = MagicMock()
    external_service.query = AsyncMock(return_value=external_response)
    external_service.detect_topic_gaps.return_value = {"financing": CoverageStatus.MISSING}

    service = CompanyQuestionService(
        resolver=_Resolver(),
        workflow=AsyncMock(),
        external_service=external_service,
    )

    plan = QueryPlan(
        question_type=QuestionType.STRUCTURED_LIST,
        structured_queries=[StructuredQueryType.FINANCING_HISTORY],
    )
    workflow_result = type("W", (), {
        "structured": [_structured_result(StructuredQueryType.FINANCING_HISTORY, [])],
        "graph": None,
        "evidence": None,
        "steps": [],
    })()
    service._workflow.execute = AsyncMock(return_value=workflow_result)

    response = await service.query(
        CompanyQuestionRequest(
            company_ref={"eid": "e-1"},
            question="这家企业融资历程是什么？",
        ),
        plan=plan,
    )

    external_service.query.assert_awaited_once()
    assert any(s.source_id == ext_source.source_id for s in response.sources)
    assert any(f.fact_id == ext_fact.fact_id for f in response.facts)
    assert response.latency.external_ms >= 0


async def test_external_unavailable_returns_internal_results_with_warning(monkeypatch):
    """外部 Provider 不可用时仍返回内部结果并带降级说明。"""
    company = _resolved_company()
    resolution = _resolution_result(company)

    class _Resolver:
        async def resolve(self, ref):
            return resolution

    from yuxi.company_due_diligence.external.models import ExternalCompanyDataResponse
    from yuxi.company_due_diligence.models import CoverageStatus

    external_service = MagicMock()
    external_service.query = AsyncMock(
        return_value=ExternalCompanyDataResponse(
            facts=[],
            sources=[],
            missing_topics=["financing"],
        )
    )
    external_service.detect_topic_gaps.return_value = {"financing": CoverageStatus.MISSING}

    service = CompanyQuestionService(
        resolver=_Resolver(),
        workflow=AsyncMock(),
        external_service=external_service,
    )

    plan = QueryPlan(
        question_type=QuestionType.STRUCTURED_LIST,
        structured_queries=[StructuredQueryType.FINANCING_HISTORY],
    )
    workflow_result = type("W", (), {
        "structured": [_structured_result(StructuredQueryType.FINANCING_HISTORY, [])],
        "graph": None,
        "evidence": None,
        "steps": [],
    })()
    service._workflow.execute = AsyncMock(return_value=workflow_result)

    response = await service.query(
        CompanyQuestionRequest(
            company_ref={"eid": "e-1"},
            question="这家企业融资历程是什么？",
        ),
        plan=plan,
    )

    assert response.data_quality.warnings
    assert "外部数据源未覆盖" in response.data_quality.warnings[0]
