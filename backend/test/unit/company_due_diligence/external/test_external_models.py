"""外部数据补数契约测试。"""

from __future__ import annotations

from datetime import UTC, datetime

from yuxi.company_due_diligence.external.models import (
    ExternalCompanyDataRequest,
    ExternalCompanyDataResponse,
    ProviderErrorCode,
    ProviderResult,
    ProviderStatus,
)
from yuxi.company_due_diligence.models import Fact, Source


def test_external_request_defaults():
    """请求契约默认值正确。"""
    request = ExternalCompanyDataRequest(company_ref={"eid": "e-1"}, topics=["profile"])
    assert request.company_ref["eid"] == "e-1"
    assert request.topics == ["profile"]
    assert request.refresh is False


def test_provider_result_error_state():
    """Provider 错误状态可序列化。"""
    result = ProviderResult(
        provider_name="financing",
        status=ProviderStatus.NOT_CONFIGURED,
        error_code=ProviderErrorCode.CONFIG_MISSING,
        error_message="未配置",
    )
    data = result.model_dump(mode="json")
    assert data["status"] == "not_configured"
    assert data["error_code"] == "config_missing"


def test_external_response_merges_coverage():
    """响应对象可容纳多主题覆盖状态。"""
    source = Source(
        source_id="source:test",
        table="external:akshare:profile",
        record_key="profile:000001",
        queried_at=datetime.now(UTC),
    )
    fact = Fact(
        fact_id="fact:test",
        subject_id="000001",
        fact_type="stock_abbreviation",
        normalized_value="平安银行",
        source_id=source.source_id,
    )
    response = ExternalCompanyDataResponse(
        facts=[fact],
        sources=[source],
        external_latency_ms=12.3,
    )
    assert len(response.facts) == 1
    assert response.external_latency_ms == 12.3
