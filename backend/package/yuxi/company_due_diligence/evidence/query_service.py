"""企业尽调文本证据查询服务。"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Callable
from datetime import UTC, date, datetime

from yuxi.company_due_diligence.evidence.indexer import (
    EvidenceEmbedder,
    EvidenceIndexRepository,
    EvidenceIndexUnavailableError,
)
from yuxi.company_due_diligence.evidence.models import EvidenceQuery, EvidenceQueryResult
from yuxi.company_due_diligence.models import (
    DataQuality,
    Evidence,
    LatencyBreakdown,
    Source,
    SourceStatus,
    build_source_id,
)
from yuxi.models.embed import select_embedding_model


class EvidenceQueryService:
    """读取当前索引，在企业过滤范围内召回并校验来源。"""

    def __init__(
        self,
        repository: EvidenceIndexRepository | None = None,
        embedder_factory: Callable[[str], EvidenceEmbedder] = select_embedding_model,
        timeout_seconds: float | None = None,
    ) -> None:
        self._repository = repository or EvidenceIndexRepository()
        self._embedder_factory = embedder_factory
        self._timeout_seconds = timeout_seconds or float(
            os.getenv("COMPANY_DD_EMBEDDING_TIMEOUT_SECONDS") or os.getenv("EMBEDDING_TIMEOUT") or 600
        )

    async def query(self, query: EvidenceQuery) -> EvidenceQueryResult:
        """查询证据；基础设施或 embedding 失败时显式返回 unavailable。"""
        started_at = time.perf_counter()
        try:
            manifest = await self._repository.get_current()
            if manifest is None:
                elapsed_ms = _elapsed_ms(started_at)
                return EvidenceQueryResult(
                    data_quality=DataQuality(
                        source_status=SourceStatus.AVAILABLE,
                        warnings=["尚未发布企业尽调文本证据索引"],
                    ),
                    latency=LatencyBreakdown(milvus_ms=elapsed_ms, total_ms=elapsed_ms),
                )
            if manifest.scope_eids is not None and query.eid not in manifest.scope_eids:
                elapsed_ms = _elapsed_ms(started_at)
                return EvidenceQueryResult(
                    projection_version=manifest.projection_version,
                    data_quality=DataQuality(
                        source_status=SourceStatus.AVAILABLE,
                        warnings=["当前证据索引范围不包含该企业"],
                    ),
                    latency=LatencyBreakdown(milvus_ms=elapsed_ms, total_ms=elapsed_ms),
                )
            embedder = self._embedder_factory(manifest.embedding_model_spec)
            vectors = await asyncio.wait_for(
                embedder.aencode_queries([query.query]),
                timeout=self._timeout_seconds,
            )
            if len(vectors) != 1:
                raise EvidenceIndexUnavailableError("查询 embedding 返回数量异常")
            raw_hits = await self._repository.search(manifest, query, vectors[0])
            mapped = [_map_evidence(hit, query.eid, manifest.projection_version) for hit in raw_hits]
            evidence = [item[0] for item in mapped]
            sources = list({item[1].source_id: item[1] for item in mapped}.values())
            elapsed_ms = _elapsed_ms(started_at)
            return EvidenceQueryResult(
                projection_version=manifest.projection_version,
                evidence=evidence,
                sources=sources,
                data_quality=DataQuality(
                    source_status=SourceStatus.AVAILABLE,
                    warnings=[] if evidence else ["当前企业和过滤范围内未召回文本证据"],
                ),
                latency=LatencyBreakdown(milvus_ms=elapsed_ms, total_ms=elapsed_ms),
            )
        except Exception as exc:
            elapsed_ms = _elapsed_ms(started_at)
            return EvidenceQueryResult(
                data_quality=DataQuality(
                    source_status=SourceStatus.UNAVAILABLE,
                    warnings=[f"文本证据来源不可用：{type(exc).__name__}"],
                ),
                latency=LatencyBreakdown(milvus_ms=elapsed_ms, total_ms=elapsed_ms),
            )


def _map_evidence(hit: dict, expected_eid: str, projection_version: str) -> tuple[Evidence, Source]:
    """校验企业、来源和版本后映射统一 Evidence。"""
    entity = hit.get("entity") or {}
    if entity.get("eid") != expected_eid:
        raise EvidenceIndexUnavailableError("Milvus 返回跨企业证据")
    if entity.get("projection_version") != projection_version:
        raise EvidenceIndexUnavailableError("Milvus 证据版本与当前清单不一致")
    expected_source_id = build_source_id(entity.get("source_table", ""), entity.get("source_record_key", ""))
    if entity.get("source_id") != expected_source_id:
        raise EvidenceIndexUnavailableError("Milvus 证据来源标识校验失败")
    published_at = entity.get("published_at") or None
    evidence = Evidence(
        evidence_id=str(hit.get("id")),
        document_id=entity["document_id"],
        entity_id=entity["eid"],
        document_type=entity["document_type"],
        text=entity["text"],
        score=max(0.0, float(hit.get("distance", 0))),
        published_at=date.fromisoformat(published_at) if published_at else None,
        source_id=entity["source_id"],
        projection_version=projection_version,
    )
    source = Source(
        source_id=entity["source_id"],
        table=entity["source_table"],
        record_key=entity["source_record_key"],
        business_date=date.fromisoformat(published_at) if published_at else None,
        queried_at=datetime.now(UTC),
    )
    return evidence, source


def _elapsed_ms(started_at: float) -> float:
    """返回保留三位小数的阶段耗时。"""
    return round((time.perf_counter() - started_at) * 1000, 3)


evidence_query_service = EvidenceQueryService()
