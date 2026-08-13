"""AkShare 公开财经数据 Provider。

首期支持：
- stock_profile：上市公司基本资料
- financial_metrics：上市公司主要财务指标

原始字段在 Provider 内部完成标准化，不泄漏到业务层。
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, date, datetime
from typing import Any

from yuxi.company_due_diligence.external.errors import (
    ExternalProviderError,
    ProviderConfigError,
    ProviderFormatError,
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

_SUPPORTED_TOPICS = ["profile", "financials"]

# 主题到 AkShare 接口主题的映射（首期只实现 profile/financials）
_TOPIC_API_MAP = {
    "profile": "stock_profile",
    "financials": "financial_metrics",
}

# 财务指标字段标准化映射
_FINANCIAL_METRIC_FIELDS = {
    "净资产收益率(%)": ("净资产收益率", "%"),
    "总资产利润率(%)": ("总资产利润率", "%"),
    "销售毛利率(%)": ("销售毛利率", "%"),
    "销售净利率(%)": ("销售净利率", "%"),
    "主营业务利润(元)": ("主营业务利润", "元"),
    "扣除非经常性损益后的净利润(元)": ("扣非净利润", "元"),
    "总资产(元)": ("总资产", "元"),
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

        stock_code = request.company_ref.stock_code
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
        errors: list[ExternalProviderError] = []

        for topic in request.topics:
            if topic not in self.supported_topics:
                missing_topics.append(topic)
                coverage[topic] = CoverageStatus.UNSUPPORTED
                errors.append(
                    ExternalProviderError(
                        f"AkShare 不支持主题 {topic}",
                        code=ProviderErrorCode.NOT_SUPPORTED,
                    )
                )
                continue

            try:
                topic_facts, topic_sources = await self._query_topic(
                    eid=request.company_ref.eid,
                    stock_code=stock_code,
                    topic=topic,
                    start_date=request.start_date,
                    end_date=request.end_date,
                )
            except ProviderConfigError as exc:
                coverage[topic] = CoverageStatus.UNSUPPORTED
                missing_topics.append(topic)
                errors.append(exc)
                logger.warning(f"AkShare 主题 {topic} 未配置: error_code={exc.code}")
                continue
            except (ProviderUpstreamError, ProviderFormatError) as exc:
                coverage[topic] = CoverageStatus.MISSING
                missing_topics.append(topic)
                errors.append(exc)
                logger.warning(f"AkShare 主题 {topic} 查询失败: error_code={exc.code}")
                continue

            facts.extend(topic_facts)
            sources.extend(topic_sources)
            if topic_facts:
                coverage[topic] = CoverageStatus.COMPLETE
            else:
                coverage[topic] = CoverageStatus.MISSING
                missing_topics.append(topic)

        if facts and not missing_topics:
            status = ProviderStatus.HEALTHY
        elif facts:
            status = ProviderStatus.DEGRADED
        elif errors and any(
            error.code not in {ProviderErrorCode.CONFIG_MISSING, ProviderErrorCode.NOT_SUPPORTED} for error in errors
        ):
            status = ProviderStatus.ERROR
        else:
            status = ProviderStatus.DEGRADED

        primary_error = next(
            (
                error
                for error in errors
                if error.code not in {ProviderErrorCode.CONFIG_MISSING, ProviderErrorCode.NOT_SUPPORTED}
            ),
            errors[0] if errors else None,
        )
        error_code = primary_error.code if primary_error else None if facts else ProviderErrorCode.EMPTY_RESULT
        error_message = (
            "; ".join(dict.fromkeys(str(error) for error in errors))
            if errors
            else None
            if facts
            else "当前 Provider 和时间窗口内未查询到可验证记录"
        )

        return ProviderResult(
            provider_name=self.name,
            facts=facts,
            sources=sources,
            evidence=evidence,
            coverage=coverage,
            missing_topics=missing_topics,
            status=status,
            error_code=error_code,
            error_message=error_message,
            latency_ms=(datetime.now(UTC) - started_at).total_seconds() * 1000,
            retrieved_at=datetime.now(UTC),
        )

    async def _query_topic(
        self,
        *,
        eid: str,
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
            return await self._query_stock_profile(eid, stock_code, start_date, end_date)
        if api_topic == "financial_metrics":
            return await self._query_financial_metrics(eid, stock_code, start_date, end_date)

        raise ProviderConfigError(f"主题 {topic} 尚未实现 AkShare 适配")

    async def _query_stock_profile(
        self,
        eid: str,
        stock_code: str,
        start_date: date | None,
        end_date: date | None,
    ) -> tuple[list[Fact], list[Source]]:
        """查询上市公司基本资料并标准化。"""
        # 移除市场后缀，AkShare 接口通常接受纯数字代码
        code = stock_code.split(".")[0]
        try:
            df = await asyncio.to_thread(ak.stock_individual_info_em, symbol=code)
        except Exception as exc:
            raise ProviderUpstreamError(f"AkShare 基本资料接口失败: {exc}") from exc

        if df is None or df.empty:
            return [], []
        if not {"item", "value"}.issubset(df.columns):
            raise ProviderFormatError("AkShare 基本资料响应缺少 item 或 value 字段")

        row = df.set_index("item")["value"].to_dict()
        source = _build_source(
            provider=self.name,
            topic="profile",
            record_key=f"stock_profile:{code}",
            upstream_api="stock_individual_info_em",
            business_date=None,
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
            effective_date = _parse_report_date(value) if fact_type == "listing_date" else None
            normalized_value = effective_date.isoformat() if effective_date else _normalize_value(value)
            facts.append(
                Fact(
                    fact_id=_stable_fact_id(self.name, code, fact_type),
                    subject_id=eid,
                    fact_type=fact_type,
                    normalized_value=normalized_value,
                    display_value=str(value).strip(),
                    source_id=source.source_id,
                    effective_date=effective_date,
                )
            )
        return facts, [source]

    async def _query_financial_metrics(
        self,
        eid: str,
        stock_code: str,
        start_date: date | None,
        end_date: date | None,
    ) -> tuple[list[Fact], list[Source]]:
        """查询上市公司主要财务指标并标准化。"""
        code = stock_code.split(".")[0]
        query_params = {"symbol": code}
        if start_date is not None:
            query_params["start_year"] = str(start_date.year)
        try:
            df = await asyncio.to_thread(ak.stock_financial_analysis_indicator, **query_params)
        except Exception as exc:
            raise ProviderUpstreamError(f"AkShare 财务指标接口失败: {exc}") from exc

        if df is None or df.empty:
            return [], []
        latest_row, report_date = _latest_financial_row(df)
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
        for label, (indicator_name, unit) in _FINANCIAL_METRIC_FIELDS.items():
            value = latest_row.get(label)
            if value is None or str(value).strip() == "":
                continue
            fact_type = f"financial_metric:{indicator_name}"
            facts.append(
                Fact(
                    fact_id=_stable_fact_id(self.name, code, fact_type, report_date.isoformat() if report_date else ""),
                    subject_id=eid,
                    fact_type=fact_type,
                    normalized_value=_normalize_value(value),
                    display_value=str(value).strip(),
                    unit=unit,
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
    coverage_status = (
        CoverageStatus.UNSUPPORTED
        if error_code in {ProviderErrorCode.CONFIG_MISSING, ProviderErrorCode.NOT_SUPPORTED}
        else CoverageStatus.MISSING
    )
    return ProviderResult(
        provider_name=provider_name,
        facts=[],
        sources=[],
        evidence=[],
        coverage={topic: coverage_status for topic in topics},
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
        external_metadata={"validation_status": "provider_normalized"},
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
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y-%m", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S"):
        try:
            parsed = datetime.strptime(text, fmt).date()
            return parsed
        except ValueError:
            continue
    return None


def _latest_financial_row(frame) -> tuple[dict[str, Any], date]:
    """从 AkShare 历史财务指标中选择最新可解析报告期。"""
    records = frame.to_dict(orient="records")
    dated_records = [
        (parsed_date, row)
        for row in records
        if (parsed_date := _parse_report_date(row.get("日期") or row.get("报告期"))) is not None
    ]
    if not dated_records:
        raise ProviderFormatError("AkShare 财务指标响应缺少可解析的日期字段")
    report_date, row = max(dated_records, key=lambda item: item[0])
    return row, report_date
