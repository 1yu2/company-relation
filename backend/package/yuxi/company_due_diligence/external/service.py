"""外部数据补数编排服务。"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

from yuxi.company_due_diligence.external.cache import ExternalProviderCache
from yuxi.company_due_diligence.external.errors import ExternalProviderError
from yuxi.company_due_diligence.external.models import (
    ExternalCompanyDataRequest,
    ExternalCompanyDataResponse,
    ProviderErrorCode,
    ProviderHealth,
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


class ExternalCompanyDataService:
    """按主题缺口检测、调用 Provider、统一去重与冲突识别。"""

    def __init__(
        self,
        *,
        registry: ExternalProviderRegistry | None = None,
        cache: ExternalProviderCache | None = None,
    ) -> None:
        self._registry = registry or external_provider_registry
        self._cache = cache or ExternalProviderCache()

    async def query(
        self,
        request: ExternalCompanyDataRequest,
        *,
        enabled_providers: list[str] | None = None,
    ) -> ExternalCompanyDataResponse:
        """执行外部数据补数查询。"""
        started_at = time.perf_counter()
        providers = self._resolve_providers(request.providers or enabled_providers)
        if not providers:
            return ExternalCompanyDataResponse(
                provider_health=[
                    ProviderHealth(
                        provider_name=name,
                        status=ProviderStatus.NOT_CONFIGURED,
                    )
                    for name in (request.providers or [])
                ],
                external_latency_ms=(time.perf_counter() - started_at) * 1000,
            )

        tasks = [self._query_provider(provider, request) for provider in providers]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        provider_results: list[ProviderResult] = []
        for result in results:
            if isinstance(result, Exception):
                provider_results.append(
                    ProviderResult(
                        provider_name="unknown",
                        status=ProviderStatus.ERROR,
                        error_code=ProviderErrorCode.UPSTREAM_ERROR,
                        error_message=str(result),
                    )
                )
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
            gaps[external_topic] = status
        return gaps

    def should_fallback(self, status: CoverageStatus) -> bool:
        """判断该状态是否允许触发外部补数。"""
        return status in _FALLBACK_ELIGIBLE_STATUSES

    async def _query_provider(
        self,
        provider: ExternalCompanyDataProvider,
        request: ExternalCompanyDataRequest,
    ) -> ProviderResult:
        """查询单个 Provider，支持缓存、超时、限流和错误分类。"""
        eid = request.company_ref.get("eid") or request.company_ref.get("stock_code") or "unknown"
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
                logger.debug(f"外部数据缓存命中: {provider.name} {eid}")
                return cached

        try:
            result = await provider.query(request)
        except ExternalProviderError as exc:
            result = ProviderResult(
                provider_name=provider.name,
                status=ProviderStatus.ERROR,
                error_code=exc.code,
                error_message=str(exc),
            )
        except Exception as exc:
            logger.warning(f"Provider {provider.name} 未分类异常: {exc}")
            result = ProviderResult(
                provider_name=provider.name,
                status=ProviderStatus.ERROR,
                error_code=ProviderErrorCode.UPSTREAM_ERROR,
                error_message=str(exc),
            )

        self._cache.set(
            cache_key[0],
            cache_key[1],
            list(cache_key[2]),
            cache_key[3],
            cache_key[4],
            result,
        )
        return result

    def _resolve_providers(
        self,
        names: list[str] | None,
    ) -> list[ExternalCompanyDataProvider]:
        """解析要使用的 Provider 列表。"""
        all_providers = self._registry.list_providers()
        if not names:
            return all_providers
        result = []
        for name in names:
            for provider in all_providers:
                if provider.name == name:
                    result.append(provider)
                    break
        return result

    def _merge_results(self, results: list[ProviderResult]) -> ExternalCompanyDataResponse:
        """合并多个 Provider 结果，去重并识别冲突。"""
        facts: dict[str, Fact] = {}
        fact_providers: dict[str, str] = {}
        sources: dict[str, Source] = {}
        evidence: dict[str, Evidence] = {}
        coverage: dict[str, TopicCoverage] = {}
        conflicts: list[str] = []
        missing_topics: set[str] = set()

        for result in results:
            for fact in result.facts:
                if fact.fact_id in facts:
                    existing = facts[fact.fact_id]
                    if existing.normalized_value != fact.normalized_value:
                        conflicts.append(
                            f"{fact.subject_id}:{fact.fact_type}: "
                            f"{fact_providers.get(fact.fact_id, 'unknown')} vs {result.provider_name}"
                        )
                else:
                    facts[fact.fact_id] = fact
                    fact_providers[fact.fact_id] = result.provider_name
            for source in result.sources:
                sources[source.source_id] = source
            for item in result.evidence:
                evidence[item.evidence_id] = item
            for topic, status in result.coverage.items():
                current = coverage.get(topic)
                if current is None:
                    coverage[topic] = TopicCoverage(
                        topic=topic,
                        status=status,
                        source_count=len(result.sources),
                        fact_count=len(result.facts),
                        provider_names=[result.provider_name],
                    )
                else:
                    # 任意 Provider 命中即为 PARTIAL 以上；全部 MISSING 才保持 MISSING
                    if status != CoverageStatus.MISSING and current.status == CoverageStatus.MISSING:
                        current.status = status
                    current.source_count += len(result.sources)
                    current.fact_count += len(result.facts)
                    if result.provider_name not in current.provider_names:
                        current.provider_names.append(result.provider_name)
            missing_topics.update(result.missing_topics)

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
            dates = [
                s.business_date
                for s in result.sources
                if isinstance(s.business_date, date)
            ]
            if dates and (date.today() - max(dates)).days > stale_threshold_days:
                return CoverageStatus.STALE

        # 字段缺失视为 partial
        if dq.missing_fields:
            return CoverageStatus.PARTIAL

        return CoverageStatus.COMPLETE


# 全局服务实例；Provider 在包初始化时注册
external_company_data_service = ExternalCompanyDataService()
