"""企业身份解析服务。"""

from __future__ import annotations

import time
import unicodedata
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy.exc import SQLAlchemyError

from yuxi.company_due_diligence.config import MySQLConfigurationError
from yuxi.company_due_diligence.models import (
    CompanyCandidate,
    CompanyReference,
    CompanyResolutionResult,
    CompanySearchResult,
    ConfidenceLevel,
    DataQuality,
    LatencyBreakdown,
    MatchMethod,
    ResolutionStatus,
    ResolvedCompany,
    Source,
    SourceStatus,
    build_source_id,
)
from yuxi.company_due_diligence.mysql import (
    CompanyDueDiligenceMySQL,
    MySQLUnavailableError,
    company_due_diligence_mysql,
)
from yuxi.company_due_diligence.repositories.enterprise import EnterpriseRepository


class EntityResolutionService:
    """按确定性优先级解析唯一企业身份。"""

    def __init__(
        self,
        mysql: CompanyDueDiligenceMySQL = company_due_diligence_mysql,
        repository_factory: Callable = EnterpriseRepository,
    ) -> None:
        self._mysql = mysql
        self._repository_factory = repository_factory

    async def resolve(self, reference: CompanyReference) -> CompanyResolutionResult:
        """解析企业引用；简称和模糊名称只返回候选。"""
        started_at = time.perf_counter()
        try:
            async with self._mysql.connection() as connection:
                repository = self._repository_factory(connection)
                method, rows = await self._query_exact_match(repository, reference)

                if rows:
                    return self._resolution_from_rows(reference, method, rows, started_at)

                if reference.name:
                    candidate_rows = await repository.search_candidates(reference.name, 20)
                    if candidate_rows:
                        return self._ambiguous_result(
                            reference,
                            self._build_candidates(candidate_rows, reference.name),
                            candidate_rows,
                            started_at,
                        )

                return self._empty_resolution(reference, ResolutionStatus.NOT_FOUND, started_at)
        except (MySQLUnavailableError, MySQLConfigurationError, SQLAlchemyError):
            return self._empty_resolution(reference, ResolutionStatus.SOURCE_UNAVAILABLE, started_at)

    async def search(self, query: str, limit: int = 10) -> CompanySearchResult:
        """按名称、简称或股票代码返回受限候选集合。"""
        started_at = time.perf_counter()
        try:
            async with self._mysql.connection() as connection:
                repository = self._repository_factory(connection)
                rows = await repository.search_candidates(query, limit)
        except (MySQLUnavailableError, MySQLConfigurationError, SQLAlchemyError):
            latency = self._latency(started_at)
            return CompanySearchResult(
                query=query,
                status=ResolutionStatus.SOURCE_UNAVAILABLE,
                candidates=[],
                sources=[],
                data_quality=DataQuality(source_status=SourceStatus.UNAVAILABLE),
                latency=latency,
            )

        candidates = self._build_candidates(rows, query)
        sources = self._build_sources(rows)
        status = ResolutionStatus.AMBIGUOUS if candidates else ResolutionStatus.NOT_FOUND
        return CompanySearchResult(
            query=query,
            status=status,
            candidates=candidates,
            sources=sources,
            data_quality=DataQuality(
                source_status=SourceStatus.AVAILABLE,
                minimum_confidence=ConfidenceLevel.LOW if candidates else None,
            ),
            latency=self._latency(started_at),
        )

    async def _query_exact_match(
        self,
        repository: EnterpriseRepository,
        reference: CompanyReference,
    ) -> tuple[MatchMethod, list[dict]]:
        """按固定优先级执行唯一企业查询。"""
        if reference.eid:
            return MatchMethod.EID, await repository.find_by_eid(reference.eid.lower())
        if reference.credit_code:
            return MatchMethod.CREDIT_CODE, await repository.find_by_credit_code(reference.credit_code.upper())
        if reference.stock_code:
            return MatchMethod.STOCK_CODE, await repository.find_by_stock_code(reference.stock_code.upper())

        name = reference.name or ""
        rows = await repository.find_by_exact_name(name)
        if rows:
            return MatchMethod.EXACT_NAME, rows
        return MatchMethod.NORMALIZED_NAME, await repository.find_by_normalized_name(normalize_company_name(name))

    def _resolution_from_rows(
        self,
        reference: CompanyReference,
        method: MatchMethod,
        rows: list[dict],
        started_at: float,
    ) -> CompanyResolutionResult:
        """把精确查询行转换为唯一或歧义结果。"""
        if len(rows) != 1:
            candidates = [self._build_candidate(row, method, ConfidenceLevel.HIGH) for row in rows]
            return self._ambiguous_result(reference, candidates, rows, started_at)

        company = ResolvedCompany(**self._build_candidate(rows[0], method, ConfidenceLevel.HIGH).model_dump())
        sources = self._build_sources(rows)
        return CompanyResolutionResult(
            status=ResolutionStatus.RESOLVED,
            reference=reference,
            company=company,
            candidates=[],
            sources=sources,
            data_quality=self._data_quality(company),
            latency=self._latency(started_at),
        )

    def _ambiguous_result(
        self,
        reference: CompanyReference,
        candidates: list[CompanyCandidate],
        rows: list[dict],
        started_at: float,
    ) -> CompanyResolutionResult:
        """装配必须由用户确认的候选结果。"""
        return CompanyResolutionResult(
            status=ResolutionStatus.AMBIGUOUS,
            reference=reference,
            candidates=candidates,
            sources=self._build_sources(rows),
            data_quality=DataQuality(
                source_status=SourceStatus.AVAILABLE,
                warnings=["存在多个或仅有低置信度候选，必须由用户确认"],
                minimum_confidence=min((item.confidence for item in candidates), default=None),
            ),
            latency=self._latency(started_at),
        )

    def _empty_resolution(
        self,
        reference: CompanyReference,
        status: ResolutionStatus,
        started_at: float,
    ) -> CompanyResolutionResult:
        """装配未找到或来源不可用结果。"""
        source_status = (
            SourceStatus.UNAVAILABLE if status == ResolutionStatus.SOURCE_UNAVAILABLE else SourceStatus.AVAILABLE
        )
        return CompanyResolutionResult(
            status=status,
            reference=reference,
            sources=[],
            data_quality=DataQuality(source_status=source_status),
            latency=self._latency(started_at),
        )

    def _build_candidates(self, rows: list[dict], query: str) -> list[CompanyCandidate]:
        """把受限搜索行转换为不会自动选中的低置信度候选。"""
        candidates = []
        for row in rows:
            method = MatchMethod.ABBREVIATION if row["stock_abbr"] == query else MatchMethod.FUZZY_NAME
            candidates.append(self._build_candidate(row, method, ConfidenceLevel.LOW))
        return candidates

    def _build_candidate(
        self,
        row: dict,
        method: MatchMethod,
        confidence: ConfidenceLevel,
    ) -> CompanyCandidate:
        """把企业身份行转换为公共候选对象。"""
        source_ids = [source.source_id for source in self._build_sources([row])]
        return CompanyCandidate(
            eid=row["eid"],
            name=row["name"],
            normalized_name=normalize_company_name(row["name"]),
            credit_code=row["credit_no"],
            stock_code=row["stock_code"],
            stock_abbreviation=row["stock_abbr"],
            match_method=method,
            confidence=confidence,
            source_ids=source_ids,
        )

    def _build_sources(self, rows: list[dict]) -> list[Source]:
        """为企业和上市映射生成稳定且去重的来源对象。"""
        queried_at = datetime.now(UTC)
        sources = {}
        for row in rows:
            enterprise_source = Source(
                source_id=build_source_id("t_enterprise", row["eid"]),
                table="t_enterprise",
                record_key=row["eid"],
                business_date=row.get("local_row_update_time") or row.get("row_update_time"),
                queried_at=queried_at,
            )
            listed_source = Source(
                source_id=build_source_id("listed_company_gd", row["eid"]),
                table="listed_company_gd",
                record_key=row["eid"],
                business_date=row.get("listed_update_time"),
                queried_at=queried_at,
            )
            sources[enterprise_source.source_id] = enterprise_source
            sources[listed_source.source_id] = listed_source
        return list(sources.values())

    def _data_quality(self, company: ResolvedCompany) -> DataQuality:
        """根据标准身份字段生成最小数据质量说明。"""
        missing_fields = [
            field
            for field, value in (
                ("name", company.name),
                ("credit_code", company.credit_code),
                ("stock_code", company.stock_code),
            )
            if not value
        ]
        return DataQuality(
            source_status=SourceStatus.AVAILABLE,
            missing_fields=missing_fields,
            minimum_confidence=company.confidence,
        )

    def _latency(self, started_at: float) -> LatencyBreakdown:
        """生成身份解析阶段耗时。"""
        elapsed_ms = round((time.perf_counter() - started_at) * 1000, 3)
        return LatencyBreakdown(resolution_ms=elapsed_ms, mysql_ms=elapsed_ms, total_ms=elapsed_ms)


def normalize_company_name(name: str) -> str:
    """保守移除空白和标点，不删除企业组织形式后缀。"""
    normalized = unicodedata.normalize("NFKC", name).strip().casefold()
    return "".join(character for character in normalized if not unicodedata.category(character).startswith(("P", "Z")))


entity_resolution_service = EntityResolutionService()
