"""AkShare Provider 单元测试。"""

from __future__ import annotations


from yuxi.company_due_diligence.external.akshare import (
    AkShareProvider,
    _build_source,
    _normalize_value,
    _parse_report_date,
    _stable_fact_id,
)
from yuxi.company_due_diligence.external.models import (
    CoverageStatus,
    ExternalCompanyDataRequest,
    ProviderErrorCode,
    ProviderStatus,
)


async def test_akshare_health_reports_status():
    """健康检查返回当前环境状态，不抛异常。"""
    provider = AkShareProvider()
    health = await provider.health()
    assert health.provider_name == "akshare"
    assert health.status in {ProviderStatus.HEALTHY, ProviderStatus.NOT_CONFIGURED}


async def test_akshare_without_stock_code_returns_not_supported_or_not_configured():
    """缺少股票代码或未安装依赖时返回明确的降级状态。"""
    provider = AkShareProvider()
    result = await provider.query(
        ExternalCompanyDataRequest(company_ref={"eid": "e-1", "name": "某企业"}, topics=["profile"])
    )
    assert result.status in {ProviderStatus.UNAVAILABLE, ProviderStatus.NOT_CONFIGURED}
    assert result.error_code in {ProviderErrorCode.NOT_SUPPORTED, ProviderErrorCode.CONFIG_MISSING}
    assert result.coverage.get("profile") == CoverageStatus.MISSING


async def test_akshare_unsupported_topic_is_missing():
    """不支持的 topic 标记为 missing 并不中断整体结果。"""
    provider = AkShareProvider()
    result = await provider.query(
        ExternalCompanyDataRequest(
            company_ref={"eid": "e-1", "stock_code": "000001.SZ"},
            topics=["nonexistent"],
        )
    )
    assert "nonexistent" in result.missing_topics
    assert result.coverage.get("nonexistent") == CoverageStatus.MISSING


def test_build_source_registers_external_metadata():
    """Source 对象携带外部来源元数据。"""
    source = _build_source(
        provider="akshare",
        topic="profile",
        record_key="profile:000001",
        upstream_api="stock_individual_info_em",
        source_url="https://example.com",
        business_date=None,
    )
    assert source.source_system == "external:akshare"
    assert source.table == "external:akshare:profile"
    assert source.provider_name == "akshare"
    assert source.upstream_api == "stock_individual_info_em"
    assert source.source_url == "https://example.com"
    assert source.source_id.startswith("source:")


def test_stable_fact_id_is_deterministic():
    """相同输入生成稳定 fact_id。"""
    id1 = _stable_fact_id("akshare", "000001", "roe", "2025-12-31")
    id2 = _stable_fact_id("akshare", "000001", "roe", "2025-12-31")
    assert id1 == id2


def test_parse_report_date_variants():
    """报告期字段支持多种常见格式。"""
    from datetime import date

    assert _parse_report_date("2025-12-31") == date(2025, 12, 31)
    assert _parse_report_date("20251231") == date(2025, 12, 31)
    assert _parse_report_date("2025/12/31") == date(2025, 12, 31)
    assert _parse_report_date("invalid") is None
    assert _parse_report_date(None) is None


def test_normalize_value_handles_numeric():
    """数值和字符串直接保留，其他类型转字符串。"""
    assert _normalize_value(123) == 123
    assert _normalize_value(1.5) == 1.5
    assert _normalize_value("text") == "text"
    assert _normalize_value([1, 2]) == "[1, 2]"
