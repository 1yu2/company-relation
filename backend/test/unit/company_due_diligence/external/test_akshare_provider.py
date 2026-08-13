"""AkShare Provider 单元测试。"""

from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace

from yuxi.company_due_diligence.external import akshare as akshare_module
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


class _FakeSeries(dict):
    """测试用的最小行对象。"""


class _FakeFrame:
    """测试用的最小 DataFrame 形状，避免单元测试依赖 pandas。"""

    def __init__(self, rows: list[dict], columns: list[str]):
        self._rows = rows
        self.columns = columns
        self.empty = not rows
        self.iloc = [_FakeSeries(row) for row in rows]

    def set_index(self, key: str):
        return _FakeIndexedFrame(self._rows, key)

    def to_dict(self, *, orient: str):
        assert orient == "records"
        return list(self._rows)


class _FakeIndexedFrame:
    """支持 AkShare 基本资料映射的测试对象。"""

    def __init__(self, rows: list[dict], key: str):
        self._rows = rows
        self._key = key

    def __getitem__(self, key: str):
        return _FakeColumn(self._rows, self._key, key)


class _FakeColumn:
    """支持 to_dict 的测试列对象。"""

    def __init__(self, rows: list[dict], index_key: str, value_key: str):
        self._values = {row[index_key]: row[value_key] for row in rows}

    def to_dict(self):
        return self._values


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
    assert result.coverage.get("profile") == CoverageStatus.UNSUPPORTED


async def test_akshare_unsupported_topic_is_explicit():
    """不支持的 topic 标记为 unsupported 并不中断整体结果。"""
    provider = AkShareProvider()
    result = await provider.query(
        ExternalCompanyDataRequest(
            company_ref={"eid": "e-1", "stock_code": "000001.SZ"},
            topics=["nonexistent"],
        )
    )
    assert "nonexistent" in result.missing_topics
    assert result.coverage.get("nonexistent") == CoverageStatus.UNSUPPORTED


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
    assert _parse_report_date("2025-12-31") == date(2025, 12, 31)
    assert _parse_report_date("20251231") == date(2025, 12, 31)
    assert _parse_report_date("2025/12/31") == date(2025, 12, 31)
    assert _parse_report_date(datetime(2025, 12, 31, 8, 30)) == date(2025, 12, 31)
    assert _parse_report_date("invalid") is None
    assert _parse_report_date(None) is None


def test_normalize_value_handles_numeric():
    """数值和字符串直接保留，其他类型转字符串。"""
    assert _normalize_value(123) == 123
    assert _normalize_value(1.5) == 1.5
    assert _normalize_value("text") == "text"
    assert _normalize_value([1, 2]) == "[1, 2]"


async def test_akshare_profile_binds_facts_to_confirmed_eid(monkeypatch):
    """AkShare 事实必须绑定内部已确认 eid，而不是股票代码。"""
    frame = _FakeFrame(
        [
            {"item": "股票简称", "value": "示例股份"},
            {"item": "行业", "value": "软件服务"},
        ],
        ["item", "value"],
    )
    monkeypatch.setattr(akshare_module, "_AKSHARE_AVAILABLE", True)
    monkeypatch.setattr(
        akshare_module,
        "ak",
        SimpleNamespace(stock_individual_info_em=lambda **kwargs: frame),
    )

    result = await AkShareProvider().query(
        ExternalCompanyDataRequest(
            company_ref={"eid": "eid-confirmed", "stock_code": "000001.SZ"},
            topics=["profile"],
        )
    )

    assert result.status == ProviderStatus.HEALTHY
    assert {fact.subject_id for fact in result.facts} == {"eid-confirmed"}
    assert all(fact.source_id in {source.source_id for source in result.sources} for fact in result.facts)


async def test_akshare_financials_use_internal_fact_type_contract(monkeypatch):
    """外部财务指标选择最新报告期并复用内部事实类型。"""
    frame = _FakeFrame(
        [
            {"日期": "2024-12-31", "净资产收益率(%)": 10.1},
            {"日期": "2025-12-31", "净资产收益率(%)": 12.3},
        ],
        ["日期", "净资产收益率(%)"],
    )
    query_params = {}

    def financial_indicator(**kwargs):
        query_params.update(kwargs)
        return frame

    monkeypatch.setattr(akshare_module, "_AKSHARE_AVAILABLE", True)
    monkeypatch.setattr(
        akshare_module,
        "ak",
        SimpleNamespace(stock_financial_analysis_indicator=financial_indicator),
    )

    result = await AkShareProvider().query(
        ExternalCompanyDataRequest(
            company_ref={"eid": "eid-confirmed", "stock_code": "000001.SZ"},
            topics=["financials"],
            start_date="2023-01-01",
        )
    )

    assert query_params == {"symbol": "000001", "start_year": "2023"}
    assert result.facts[0].subject_id == "eid-confirmed"
    assert result.facts[0].fact_type == "financial_metric:净资产收益率"
    assert result.facts[0].normalized_value == 12.3
    assert result.facts[0].unit == "%"
    assert result.facts[0].effective_date == date(2025, 12, 31)


async def test_akshare_financial_format_error_is_classified(monkeypatch):
    """财务响应缺少可解析日期时返回 format_error。"""
    frame = _FakeFrame([{"净资产收益率(%)": 12.3}], ["净资产收益率(%)"])
    monkeypatch.setattr(akshare_module, "_AKSHARE_AVAILABLE", True)
    monkeypatch.setattr(
        akshare_module,
        "ak",
        SimpleNamespace(stock_financial_analysis_indicator=lambda **kwargs: frame),
    )

    result = await AkShareProvider().query(
        ExternalCompanyDataRequest(
            company_ref={"eid": "eid-confirmed", "stock_code": "000001.SZ"},
            topics=["financials"],
        )
    )

    assert result.status == ProviderStatus.ERROR
    assert result.error_code == ProviderErrorCode.FORMAT_ERROR
    assert result.coverage["financials"] == CoverageStatus.MISSING
