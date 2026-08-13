"""外部数据补数编排服务。"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

from yuxi.company_due_diligence.external.cache import ExternalProviderCache
from yuxi.company_due_diligence.external.errors import ExternalProviderError
from yuxi.company_due_diligence.external.models import (
    ExternalCompanyDataRequest,
    ExternalCompanyDataResponse,
    ProviderErrorCode,
    ProviderHealth,
    ProviderQueryAudit,
    ProviderResult,
    ProviderStatus,
    TopicCoverage,
)
from yuxi.company_due_diligence.external.provider import ExternalCompanyDataProvider
from yuxi.company_due_diligence.external.registry import ExternalProviderRegistry, external_provider_registry
from yuxi.company_due_diligence.models import CoverageStatus, Evidence, Fact, Source, StructuredQueryType
from yuxi.utils.logging_config import logger

if TYPE_CHECKING:
    from yuxi.company_due_diligence.qa.models import QueryPlan

# 主题映射：内部 StructuredQueryType -> 外部主题
_INTERNAL_TOPIC_MAP: dict[StructuredQueryType, str] = {
    StructuredQueryType.ENTERPRISE_PROFILE: "profile",
    StructuredQueryType.FINANCIAL_METRIC_SERIES: "financials",
    StructuredQueryType.FINANCING_HISTORY: "financing",
    StructuredQueryType.CURRENT_SHAREHOLDERS: "shareholders",
    StructuredQueryType.HISTORICAL_SHAREHOLDERS: "shareholders",
    StructuredQueryType.ADMINISTRATIVE_PENALTIES: "compliance",
    StructuredQueryType.ENVIRONMENTAL_PENALTIES: "compliance",
    StructuredQueryType.ABNORMAL_OPERATIONS: "compliance",
    StructuredQueryType.LICENSES: "compliance",
}

# 允许触发外部查询的覆盖状态
_FALLBACK_ELIGIBLE_STATUSES = {
    CoverageStatus.PARTIAL,
    CoverageStatus.MISSING,
    CoverageStatus.STALE,
    CoverageStatus.CONFLICT,
    CoverageStatus.UNSUPPORTED,
}

_DEFAULT_AUDIT_LIMIT = 200


class ExternalCompanyDataService:
    """按主题缺口检测、调用 Provider、统一去重与冲突识别。"""

    def __init__(
        self,
        *,
        registry: ExternalProviderRegistry | None = None,
        cache: ExternalProviderCache | None = None,
        timeout_seconds: float = 30.0,
        cache_ttl_seconds: float = 300.0,
        rate_limit_per_minute: int = 60,
        audit_limit: int = _DEFAULT_AUDIT_LIMIT,
    ) -> None:
        self._registry = registry or external_provider_registry
        self._cache = cache or ExternalProviderCache(default_ttl_seconds=cache_ttl_seconds)
        self._timeout_seconds = _positive_number(timeout_seconds, "timeout_seconds")
        self._cache_ttl_seconds = _non_negative_number(cache_ttl_seconds, "cache_ttl_seconds")
        self._rate_limit_per_minute = _positive_integer(rate_limit_per_minute, "rate_limit_per_minute")
        self._rate_limit_history: dict[str, deque[float]] = {}
        self._rate_limit_lock = asyncio.Lock()
        self._audit_log: deque[ProviderQueryAudit] = deque(maxlen=_positive_integer(audit_limit, "audit_limit"))

    async def query(
        self,
        request: ExternalCompanyDataRequest,
        *,
        enabled_providers: list[str] | None = None,
        timeout_seconds: float | None = None,
        cache_ttl_seconds: float | None = None,
        rate_limit_per_minute: int | None = None,
    ) -> ExternalCompanyDataResponse:
        """执行外部数据补数查询。"""
        started_at = time.perf_counter()
        requested_names = request.providers if request.providers is not None else enabled_providers
        providers, missing_names = self._resolve_providers(requested_names)
        effective_timeout = (
            self._timeout_seconds if timeout_seconds is None else _positive_number(timeout_seconds, "timeout_seconds")
        )
        effective_cache_ttl = (
            self._cache_ttl_seconds
            if cache_ttl_seconds is None
            else _non_negative_number(cache_ttl_seconds, "cache_ttl_seconds")
        )
        effective_rate_limit = (
            self._rate_limit_per_minute
            if rate_limit_per_minute is None
            else _positive_integer(rate_limit_per_minute, "rate_limit_per_minute")
        )

        provider_results = [
            ProviderResult(
                provider_name=name,
                status=ProviderStatus.ERROR,
                error_code=ProviderErrorCode.CONFIG_MISSING,
                error_message=f"未找到 Provider: {name}",
                coverage={topic: CoverageStatus.UNSUPPORTED for topic in request.topics},
                missing_topics=list(request.topics),
            )
            for name in missing_names
        ]
        for result in provider_results:
            self._record_audit(result, request.topics)

        tasks = [
            self._query_provider(
                provider,
                request,
                timeout_seconds=effective_timeout,
                cache_ttl_seconds=effective_cache_ttl,
                rate_limit_per_minute=effective_rate_limit,
            )
            for provider in providers
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for provider, result in zip(providers, results, strict=True):
            if isinstance(result, Exception):
                failed_result = ProviderResult(
                    provider_name=provider.name,
                    status=ProviderStatus.ERROR,
                    error_code=ProviderErrorCode.UPSTREAM_ERROR,
                    error_message="Provider 查询失败",
                    coverage={topic: CoverageStatus.MISSING for topic in request.topics},
                    missing_topics=list(request.topics),
                )
                provider_results.append(failed_result)
                self._record_audit(failed_result, request.topics)
            else:
                provider_results.append(result)

        merged = self._merge_results(provider_results)
        merged.provider_results = provider_results
        merged.provider_health = [
            ProviderHealth(
                provider_name=result.provider_name,
                status=result.status,
                message=result.error_message,
            )
            for result in provider_results
        ]
        merged.external_latency_ms = round((time.perf_counter() - started_at) * 1000, 3)
        return merged

    async def health(self) -> list[ProviderHealth]:
        """返回所有 Provider 健康状态。"""
        return await self._registry.health()

    def cache_stats(self) -> dict:
        """返回不包含缓存内容的统计信息。"""
        return self._cache.stats()

    def clear_cache(self, provider_name: str | None = None) -> int:
        """清理全部或指定 Provider 缓存并返回删除数量。"""
        return self._cache.invalidate(provider_name)

    def audit_entries(self, limit: int = 50) -> list[ProviderQueryAudit]:
        """按时间倒序返回最近的脱敏查询审计。"""
        normalized_limit = _positive_integer(limit, "limit")
        return list(reversed(self._audit_log))[:normalized_limit]

    def detect_topic_gaps(
        self,
        plan: QueryPlan,
        structured_results: list,
        *,
        stale_threshold_days: int | None = 180,
    ) -> dict[str, CoverageStatus]:
        """根据内部查询结果计算每个外部主题的覆盖状态。"""

        gaps: dict[str, CoverageStatus] = {}
        for query_type in plan.structured_queries:
            external_topic = _INTERNAL_TOPIC_MAP.get(query_type)
            if external_topic is None:
                continue
            status = self._coverage_for_query_type(
                query_type, structured_results, stale_threshold_days=stale_threshold_days
            )
            current = gaps.get(external_topic)
            gaps[external_topic] = status if current is None else _merge_gap_status(current, status)
        return gaps

    def should_fallback(self, status: CoverageStatus) -> bool:
        """判断该状态是否允许触发外部补数。"""
        return status in _FALLBACK_ELIGIBLE_STATUSES

    async def _query_provider(
        self,
        provider: ExternalCompanyDataProvider,
        request: ExternalCompanyDataRequest,
        *,
        timeout_seconds: float,
        cache_ttl_seconds: float,
        rate_limit_per_minute: int,
    ) -> ProviderResult:
        """查询单个 Provider，支持缓存、超时、限流和错误分类。"""
        started_at = time.perf_counter()
        eid = request.company_ref.eid or request.company_ref.stock_code or "unknown"
        cache_key = (
            provider.name,
            str(eid),
            tuple(sorted(request.topics)),
            str(request.start_date) if request.start_date else None,
            str(request.end_date) if request.end_date else None,
        )

        if not request.refresh:
            cached = self._cache.get(
                cache_key[0],
                cache_key[1],
                list(cache_key[2]),
                cache_key[3],
                cache_key[4],
            )
            if cached is not None:
                logger.debug(f"外部数据缓存命中: provider={provider.name}")
                self._record_audit(cached, request.topics, started_at=started_at)
                return cached

        if not await self._acquire_rate_limit(provider.name, rate_limit_per_minute):
            result = ProviderResult(
                provider_name=provider.name,
                status=ProviderStatus.ERROR,
                error_code=ProviderErrorCode.RATE_LIMIT,
                error_message="Provider 查询达到每分钟限流上限",
                coverage={topic: CoverageStatus.MISSING for topic in request.topics},
                missing_topics=list(request.topics),
            )
            self._record_audit(result, request.topics, started_at=started_at)
            return result

        try:
            result = await asyncio.wait_for(provider.query(request), timeout=timeout_seconds)
        except TimeoutError:
            result = ProviderResult(
                provider_name=provider.name,
                status=ProviderStatus.ERROR,
                error_code=ProviderErrorCode.TIMEOUT,
                error_message=f"Provider 查询超过 {timeout_seconds:g} 秒",
                coverage={topic: CoverageStatus.MISSING for topic in request.topics},
                missing_topics=list(request.topics),
            )
        except ExternalProviderError as exc:
            result = ProviderResult(
                provider_name=provider.name,
                status=ProviderStatus.ERROR,
                error_code=exc.code,
                error_message=str(exc),
                coverage={topic: CoverageStatus.MISSING for topic in request.topics},
                missing_topics=list(request.topics),
            )
        except Exception as exc:
            logger.warning(f"Provider {provider.name} 未分类异常: {type(exc).__name__}")
            result = ProviderResult(
                provider_name=provider.name,
                status=ProviderStatus.ERROR,
                error_code=ProviderErrorCode.UPSTREAM_ERROR,
                error_message="Provider 查询失败",
                coverage={topic: CoverageStatus.MISSING for topic in request.topics},
                missing_topics=list(request.topics),
            )

        if result.status in {ProviderStatus.HEALTHY, ProviderStatus.DEGRADED}:
            self._cache.set(
                cache_key[0],
                cache_key[1],
                list(cache_key[2]),
                cache_key[3],
                cache_key[4],
                result,
                ttl_seconds=cache_ttl_seconds,
            )
        self._record_audit(result, request.topics, started_at=started_at)
        return result

    def _record_audit(
        self,
        result: ProviderResult,
        topics: list[str],
        *,
        started_at: float | None = None,
    ) -> None:
        """仅记录 Provider、主题、状态、错误分类、耗时和时间。"""
        latency_ms = result.latency_ms
        if started_at is not None:
            latency_ms = round((time.perf_counter() - started_at) * 1000, 3)
        self._audit_log.append(
            ProviderQueryAudit(
                provider_name=result.provider_name,
                topics=list(topics),
                status=result.status,
                error_code=result.error_code,
                latency_ms=latency_ms,
            )
        )

    async def _acquire_rate_limit(self, provider_name: str, limit: int) -> bool:
        """按 Provider 记录最近一分钟调用次数。"""
        now = time.monotonic()
        async with self._rate_limit_lock:
            history = self._rate_limit_history.setdefault(provider_name, deque())
            while history and now - history[0] >= 60:
                history.popleft()
            if len(history) >= limit:
                return False
            history.append(now)
            return True

    def _resolve_providers(
        self,
        names: list[str] | None,
    ) -> tuple[list[ExternalCompanyDataProvider], list[str]]:
        """解析要使用的 Provider 列表。"""
        all_providers = self._registry.list_providers()
        if names is None:
            return all_providers, []
        if not names:
            return [], []
        providers = []
        missing_names = []
        for name in names:
            provider = next((item for item in all_providers if item.name == name), None)
            if provider is None:
                missing_names.append(name)
            elif provider not in providers:
                providers.append(provider)
        return providers, missing_names

    def _merge_results(self, results: list[ProviderResult]) -> ExternalCompanyDataResponse:
        """合并多个 Provider 结果，去重并识别冲突。"""
        facts: dict[str, Fact] = {}
        fact_providers: dict[tuple[str, str, str], str] = {}
        sources: dict[str, Source] = {}
        evidence: dict[str, Evidence] = {}
        coverage: dict[str, TopicCoverage] = {}
        conflicts: list[str] = []
        missing_topics: set[str] = set()

        for result in results:
            for fact in result.facts:
                fact_key = (
                    fact.subject_id,
                    fact.fact_type,
                    fact.effective_date.isoformat() if fact.effective_date else "",
                )
                if fact_key in fact_providers:
                    existing = next(
                        item
                        for item in facts.values()
                        if (
                            item.subject_id,
                            item.fact_type,
                            item.effective_date.isoformat() if item.effective_date else "",
                        )
                        == fact_key
                    )
                    if existing.normalized_value != fact.normalized_value:
                        conflicts.append(
                            f"{fact.subject_id}:{fact.fact_type}: {fact_providers[fact_key]} vs {result.provider_name}"
                        )
                        if fact.fact_id not in facts:
                            facts[fact.fact_id] = fact
                else:
                    facts[fact.fact_id] = fact
                    fact_providers[fact_key] = result.provider_name
            for source in result.sources:
                sources[source.source_id] = source
            for item in result.evidence:
                evidence[item.evidence_id] = item
            for topic, status in result.coverage.items():
                topic_source_ids = {
                    source.source_id
                    for source in result.sources
                    if source.table == f"external:{result.provider_name}:{topic}"
                }
                topic_fact_count = sum(1 for fact in result.facts if fact.source_id in topic_source_ids)
                current = coverage.get(topic)
                if current is None:
                    coverage[topic] = TopicCoverage(
                        topic=topic,
                        status=status,
                        source_count=len(topic_source_ids),
                        fact_count=topic_fact_count,
                        provider_names=[result.provider_name],
                    )
                else:
                    current.status = _merge_coverage_status(current.status, status)
                    current.source_count += len(topic_source_ids)
                    current.fact_count += topic_fact_count
                    if result.provider_name not in current.provider_names:
                        current.provider_names.append(result.provider_name)
            missing_topics.update(result.missing_topics)

        missing_topics = {
            topic
            for topic, item in coverage.items()
            if item.status in {CoverageStatus.PARTIAL, CoverageStatus.MISSING, CoverageStatus.UNSUPPORTED}
        }

        return ExternalCompanyDataResponse(
            facts=list(facts.values()),
            sources=list(sources.values()),
            evidence=list(evidence.values()),
            coverage=coverage,
            missing_topics=list(missing_topics),
            conflicts=conflicts,
            retrieved_at=datetime.now(UTC),
        )

    def _coverage_for_query_type(
        self,
        query_type: StructuredQueryType,
        structured_results: list,
        stale_threshold_days: int | None,
    ) -> CoverageStatus:
        """根据内部结构化结果判断覆盖状态。"""
        result = next((r for r in structured_results if r.query_type == query_type), None)
        if result is None:
            return CoverageStatus.UNSUPPORTED

        dq = result.data_quality
        if dq.conflicts:
            return CoverageStatus.CONFLICT
        if dq.source_status.value == "unavailable":
            return CoverageStatus.MISSING

        has_data = bool(result.facts or result.relations)
        if not has_data:
            return CoverageStatus.MISSING

        # 判断过期：来源业务日期早于阈值
        if stale_threshold_days and result.sources:
            dates = [s.business_date for s in result.sources if isinstance(s.business_date, date)]
            if dates and (date.today() - max(dates)).days > stale_threshold_days:
                return CoverageStatus.STALE

        # 字段缺失视为 partial
        if dq.missing_fields:
            return CoverageStatus.PARTIAL

        return CoverageStatus.COMPLETE


def _merge_coverage_status(first: CoverageStatus, second: CoverageStatus) -> CoverageStatus:
    """合并同一主题的 Provider 覆盖状态。"""
    if CoverageStatus.CONFLICT in {first, second}:
        return CoverageStatus.CONFLICT
    if CoverageStatus.COMPLETE in {first, second}:
        return CoverageStatus.COMPLETE
    for status in (CoverageStatus.PARTIAL, CoverageStatus.STALE, CoverageStatus.MISSING, CoverageStatus.UNSUPPORTED):
        if status in {first, second}:
            return status
    return second


def _merge_gap_status(first: CoverageStatus, second: CoverageStatus) -> CoverageStatus:
    """合并同主题的内部查询缺口，保留更需要补数的状态。"""
    if CoverageStatus.CONFLICT in {first, second}:
        return CoverageStatus.CONFLICT
    for status in (
        CoverageStatus.MISSING,
        CoverageStatus.PARTIAL,
        CoverageStatus.STALE,
        CoverageStatus.UNSUPPORTED,
        CoverageStatus.COMPLETE,
    ):
        if status in {first, second}:
            return status
    return second


def _positive_number(value: float, field_name: str) -> float:
    """校验正数配置。"""
    normalized = float(value)
    if normalized <= 0:
        raise ValueError(f"{field_name} 必须大于 0")
    return normalized


def _non_negative_number(value: float, field_name: str) -> float:
    """校验非负数配置。"""
    normalized = float(value)
    if normalized < 0:
        raise ValueError(f"{field_name} 不能为负数")
    return normalized


def _positive_integer(value: int, field_name: str) -> int:
    """校验正整数配置。"""
    normalized = int(value)
    if normalized <= 0:
        raise ValueError(f"{field_name} 必须大于 0")
    return normalized


# 全局服务实例；Provider 在包初始化时注册
external_company_data_service = ExternalCompanyDataService()
