"""企业尽调公共契约单元测试。"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from yuxi.company_due_diligence.models import (
    CompanyReference,
    CompanyResolutionResult,
    ConfidenceLevel,
    DataQuality,
    LatencyBreakdown,
    ResolutionStatus,
    Source,
    StructuredQueryRequest,
    StructuredQueryResult,
    StructuredQueryType,
    build_source_id,
)


def test_company_reference_requires_identifier():
    """空企业引用应在 API 边界失败。"""
    with pytest.raises(ValidationError, match="至少提供"):
        CompanyReference(name="   ")


def test_source_id_is_stable_and_does_not_contain_record_key():
    """来源标识应稳定且不直接暴露原始记录键。"""
    first = build_source_id("t_enterprise", "enterprise-record-key")
    second = build_source_id("t_enterprise", "enterprise-record-key")

    assert first == second
    assert "enterprise-record-key" not in first
    assert len(first) == 31


def test_resolution_contract_serializes_dates_enums_and_latency():
    """公共解析结果应稳定序列化为前端可消费 JSON。"""
    source = Source(
        source_id=build_source_id("t_enterprise", "eid-1"),
        table="t_enterprise",
        record_key="eid-1",
        queried_at=datetime(2026, 8, 10, tzinfo=UTC),
    )
    result = CompanyResolutionResult(
        status=ResolutionStatus.NOT_FOUND,
        reference=CompanyReference(eid="eid-1"),
        sources=[source],
        data_quality=DataQuality(minimum_confidence=ConfidenceLevel.HIGH),
        latency=LatencyBreakdown(resolution_ms=1.2, mysql_ms=1.2, total_ms=1.2),
    )

    payload = result.model_dump(mode="json")
    schema = CompanyResolutionResult.model_json_schema()

    assert payload["status"] == "not_found"
    assert payload["sources"][0]["queried_at"] == "2026-08-10T00:00:00Z"
    assert schema["properties"]["status"]
    assert schema["properties"]["data_quality"]


def test_structured_query_contract_validates_range_and_page_limit():
    """结构化查询应拒绝反向日期范围和超过最大条数的请求。"""
    with pytest.raises(ValidationError, match="end_date"):
        StructuredQueryRequest(
            eid="eid-1",
            query_type=StructuredQueryType.PATENT_SUMMARY,
            start_date="2026-08-10",
            end_date="2026-08-09",
        )
    with pytest.raises(ValidationError):
        StructuredQueryRequest(
            eid="eid-1",
            query_type=StructuredQueryType.PATENT_SUMMARY,
            page_size=101,
        )

    schema = StructuredQueryResult.model_json_schema()
    assert schema["properties"]["pagination"]
    assert schema["properties"]["sources"]
