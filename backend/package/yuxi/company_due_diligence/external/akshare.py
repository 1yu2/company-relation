"""AkShare 公开财经数据 Provider。

首期支持：
- stock_profile：上市公司基本资料
- financial_metrics：上市公司主要财务指标

原始字段在 Provider 内部完成标准化，不泄漏到业务层。
"""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from typing import Any

from yuxi.company_due_diligence.external.errors import (
    ProviderConfigError,
    ProviderFormatError,
    ProviderTimeoutError,
    ProviderUpstreamError,
)
from yuxi.company_due_diligence.external.models import (
    CoverageStatus,
    ExternalCompanyDataRequest,
    ProviderErrorCode,
    ProviderHealth,
    ProviderResult,
    ProviderStatus,
)
from yuxi.company_due_diligence.external.provider import ExternalCompanyDataProvider
from yuxi.company_due_diligence.models import Evidence, Fact, Source, build_source_id
from yuxi.utils.logging_config import logger

# AkShare 是可选依赖；未安装时 Provider 返回 NOT_CONFIGURED。
try:
    import akshare as ak

    _AKSHARE_AVAILABLE = True
except Exception:  # pragma: no cover - 环境差异
    _AKSHARE_AVAILABLE = False
    ak = None

_SUPPORTED_TOPICS = ["profile", "financials", "shareholders", "news"]

# 主题到 AkShare 接口主题的映射（首期只实现 profile/financials）
_TOPIC_API_MAP = {
    "profile": "stock_profile",
    "financials": "financial_metrics",
}

# 财务指标字段标准化映射
_FINANCIAL_METRIC_FIELDS = {
    "净资产收益率": "roe",
    "总资产报酬率": "roa",
    "毛利率": "gross_margin",
    "净利率": "net_margin",
    "营业收入": "operating_revenue",
    "营业利润": "operating_profit",
    "净利润": "net_profit",
    "总资产": "total_assets",
    "总负债": "total_liabilities",
    "净资产": "net_assets",
}


class AkShareProvider(ExternalCompanyDataProvider):
    """AkShare 公开财经数据 Provider。"""

    @property
    def name(self) -> str:
        return "akshare"

    @property
    def supported_topics(self) -> list[str]:
        return list(_SUPPORTED_TOPICS)

    async def health(self) -> ProviderHealth:
        if not _AKSHARE_AVAILABLE:
            return ProviderHealth(
                provider_name=self.name,
                status=ProviderStatus.NOT_CONFIGURED,
                supported_topics=self.supported_topics,
                message="AkShare 未安装或导入失败",
            )
        return ProviderHealth(
            provider_name=self.name,
            status=ProviderStatus.HEALTHY,
            supported_topics=self.supported_topics,
        )

    async def query(self, request: ExternalCompanyDataRequest) -> ProviderResult:
        """按主题调用 AkShare 接口并标准化结果。"""
        started_at = datetime.now(UTC)
        if not _AKSHARE_AVAILABLE:
            return _empty_result(
                self.name,
                request.topics,
                ProviderStatus.NOT_CONFIGURED,
                ProviderErrorCode.CONFIG_MISSING,
                "AkShare 未安装或导入失败",
            )

        stock_code = request.company_ref.get("stock_code")
        if not stock_code:
            return _empty_result(
                self.name,
                request.topics,
                ProviderStatus.UNAVAILABLE,
                ProviderErrorCode.NOT_SUPPORTED,
                "AkShare 首期仅支持通过股票代码查询上市公司",
            )

        facts: list[Fact] = []
        sources: list[Source] = []
        evidence: list[Evidence] = []
        coverage: dict[str, CoverageStatus] = {}
        missing_topics: list[str] = []

        for topic in request.topics:
            if topic not in self.supported_topics:
                missing_topics.append(topic)
                coverage[topic] = CoverageStatus.UNSUPPORTED
                continue

            try:
                topic_facts, topic_sources = await self._query_topic(
                    stock_code=stock_code,
                    topic=topic,
                    start_date=request.start_date,
                    end_date=request.end_date,
                )
            except ProviderConfigError as exc:
                coverage[topic] = CoverageStatus.UNSUPPORTED
                missing_topics.append(topic)
                logger.warning(f"AkShare 主题 {topic} 未配置: {exc}")
                continue
            except (ProviderTimeoutError, ProviderUpstreamError, ProviderFormatError) as exc:
                coverage[topic] = CoverageStatus.MISSING
                missing_topics.append(topic)
                logger.warning(f"AkShare 主题 {topic} 查询失败: {exc}")
                continue

            facts.extend(topic_facts)
            sources.extend(topic_sources)
            if topic_facts:
                coverage[topic] = CoverageStatus.COMPLETE
            else:
                coverage[topic] = CoverageStatus.MISSING
                missing_topics.append(topic)

        status = ProviderStatus.HEALTHY if facts else ProviderStatus.DEGRADED
        error_message = None if facts else "未查询到可验证记录"

        return ProviderResult(
            provider_name=self.name,
            facts=facts,
            sources=sources,
            evidence=evidence,
            coverage=coverage,
            missing_topics=missing_topics,
            status=status,
            error_message=error_message,
            latency_ms=(datetime.now(UTC) - started_at).total_seconds() * 1000,
            retrieved_at=datetime.now(UTC),
        )

    async def _query_topic(
        self,
        *,
        stock_code: str,
        topic: str,
        start_date: date | None,
        end_date: date | None,
    ) -> tuple[list[Fact], list[Source]]:
        """按主题调用对应 AkShare 接口。"""
        api_topic = _TOPIC_API_MAP.get(topic)
        if api_topic is None:
            raise ProviderConfigError(f"主题 {topic} 未映射到 AkShare 接口")

        if api_topic == "stock_profile":
            return await self._query_stock_profile(stock_code, start_date, end_date)
        if api_topic == "financial_metrics":
            return await self._query_financial_metrics(stock_code, start_date, end_date)

        raise ProviderConfigError(f"主题 {topic} 尚未实现 AkShare 适配")

    async def _query_stock_profile(
        self,
        stock_code: str,
        start_date: date | None,
        end_date: date | None,
    ) -> tuple[list[Fact], list[Source]]:
        """查询上市公司基本资料并标准化。"""
        # 移除市场后缀，AkShare 接口通常接受纯数字代码
        code = stock_code.split(".")[0]
        try:
            df = ak.stock_individual_info_em(symbol=code)
        except Exception as exc:
            raise ProviderUpstreamError(f"AkShare 基本资料接口失败: {exc}") from exc

        if df is None or df.empty:
            return [], []

        row = df.set_index("item")["value"].to_dict()
        source = _build_source(
            provider=self.name,
            topic="profile",
            record_key=f"stock_profile:{code}",
            upstream_api="stock_individual_info_em",
            business_date=end_date,
        )

        facts: list[Fact] = []
        mappings = {
            "股票简称": "stock_abbreviation",
            "总市值": "total_market_cap",
            "流通市值": "circulating_market_cap",
            "行业": "industry",
            "上市时间": "listing_date",
            "总股本": "total_shares",
            "流通股": "circulating_shares",
        }
        for label, fact_type in mappings.items():
            value = row.get(label)
            if value is None or str(value).strip() == "":
                continue
            facts.append(
                Fact(
                    fact_id=_stable_fact_id(self.name, code, fact_type),
                    subject_id=code,
                    fact_type=fact_type,
                    normalized_value=_normalize_value(value),
                    display_value=str(value).strip(),
                    source_id=source.source_id,
                    effective_date=end_date,
                )
            )
        return facts, [source]

    async def _query_financial_metrics(
        self,
        stock_code: str,
        start_date: date | None,
        end_date: date | None,
    ) -> tuple[list[Fact], list[Source]]:
        """查询上市公司主要财务指标并标准化。"""
        code = stock_code.split(".")[0]
        try:
            df = ak.stock_financial_analysis_indicator(symbol=code)
        except Exception as exc:
            raise ProviderUpstreamError(f"AkShare 财务指标接口失败: {exc}") from exc

        if df is None or df.empty:
            return [], []

        # 取最新一期报告
        latest_row = df.iloc[0]
        report_date = _parse_report_date(latest_row.get("报告期"))
        if not self._business_date_in_range(report_date, start_date, end_date):
            return [], []

        source = _build_source(
            provider=self.name,
            topic="financials",
            record_key=f"financial_metrics:{code}:{report_date.isoformat() if report_date else 'latest'}",
            upstream_api="stock_financial_analysis_indicator",
            business_date=report_date,
        )

        facts: list[Fact] = []
        for label, fact_type in _FINANCIAL_METRIC_FIELDS.items():
            value = latest_row.get(label)
            if value is None or str(value).strip() == "":
                continue
            facts.append(
                Fact(
                    fact_id=_stable_fact_id(self.name, code, fact_type, report_date.isoformat() if report_date else ""),
                    subject_id=code,
                    fact_type=fact_type,
                    normalized_value=_normalize_value(value),
                    display_value=str(value).strip(),
                    source_id=source.source_id,
                    effective_date=report_date,
                )
            )
        return facts, [source]


def _empty_result(
    provider_name: str,
    topics: list[str],
    status: ProviderStatus,
    error_code: ProviderErrorCode,
    error_message: str,
) -> ProviderResult:
    """构造空结果。"""
    return ProviderResult(
        provider_name=provider_name,
        facts=[],
        sources=[],
        evidence=[],
        coverage={topic: CoverageStatus.MISSING for topic in topics},
        missing_topics=list(topics),
        status=status,
        error_code=error_code,
        error_message=error_message,
    )


def _build_source(
    *,
    provider: str,
    topic: str,
    record_key: str,
    upstream_api: str,
    business_date: date | None,
    source_url: str | None = None,
) -> Source:
    """把 Provider 结果登记为可追溯 Source。"""
    now = datetime.now(UTC)
    table = f"external:{provider}:{topic}"
    source_id = build_source_id(table, record_key)
    return Source(
        source_id=source_id,
        source_system=f"external:{provider}",
        table=table,
        record_key=record_key,
        business_date=business_date,
        queried_at=now,
        retrieved_at=now,
        provider_name=provider,
        upstream_api=upstream_api,
        source_url=source_url,
    )


def _stable_fact_id(provider: str, code: str, fact_type: str, *parts: object) -> str:
    """生成稳定 fact_id。"""
    payload = ":".join([provider, code, fact_type, *(str(p) for p in parts)]).encode()
    return f"fact:external:{hashlib.sha256(payload).hexdigest()[:24]}"


def _normalize_value(value: Any) -> Any:
    """把常见数值类型标准化为 JSON 友好形式。"""
    if isinstance(value, (int, float, str, bool)) or value is None:
        return value
    return str(value)


def _parse_report_date(value: Any) -> date | None:
    """解析 AkShare 报告期字段。"""
    if value is None:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y-%m", "%Y/%m/%d"):
        try:
            parsed = datetime.strptime(text, fmt).date()
            return parsed
        except ValueError:
            continue
    return None
