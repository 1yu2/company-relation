"""企业尽调文本证据查询服务单元测试。"""

from datetime import UTC, datetime

import pytest

from yuxi.company_due_diligence.evidence.indexer import EvidenceIndexUnavailableError
from yuxi.company_due_diligence.evidence.models import (
    EvidenceIndexManifest,
    EvidenceIndexStatistics,
    EvidenceQuery,
)
from yuxi.company_due_diligence.evidence.query_service import EvidenceQueryService
from yuxi.company_due_diligence.models import SourceStatus, build_source_id

pytestmark = pytest.mark.asyncio


class FakeEmbedder:
    """返回固定查询向量。"""

    dimension = 3

    async def aencode_queries(self, queries):
        """校验查询文本后返回固定向量。"""
        assert queries == ["处罚"]
        return [[1.0, 0.0, 0.0]]


class FakeRepository:
    """返回固定清单和单条可追溯命中。"""

    def __init__(self, *, unavailable: bool = False, wrong_eid: bool = False) -> None:
        self.unavailable = unavailable
        self.wrong_eid = wrong_eid
        self.manifest = EvidenceIndexManifest(
            projection_version="evidence-test",
            checksum="a" * 64,
            created_at=datetime.now(UTC),
            embedding_model_spec="test:model",
            embedding_dimension=3,
            collection_name="cdd_evidence_test",
            minio_bucket="test",
            minio_prefix="indexes/evidence-test",
            scope_eids=["eid-1"],
            statistics=EvidenceIndexStatistics(
                extracted_count=1,
                document_count=1,
                chunk_count=1,
                skipped_empty_count=0,
                duplicate_text_count=0,
                unmapped_news_count=0,
                failed_count=0,
                covered_company_count=1,
            ),
        )

    async def get_current(self):
        """返回当前清单或模拟基础设施故障。"""
        if self.unavailable:
            raise EvidenceIndexUnavailableError("Milvus 不可用")
        return self.manifest

    async def search(self, manifest, query, vector):
        """校验查询参数并返回固定命中。"""
        assert manifest == self.manifest
        assert query.eid == "eid-1"
        assert vector == [1.0, 0.0, 0.0]
        source_id = build_source_id("t_administrative_punishment", "1")
        return [
            {
                "id": "evidence-1",
                "distance": 0.9,
                "entity": {
                    "document_id": "document-1",
                    "eid": "eid-2" if self.wrong_eid else "eid-1",
                    "document_type": "administrative_punishment",
                    "text": "处罚决定：罚款。",
                    "source_id": source_id,
                    "source_table": "t_administrative_punishment",
                    "source_record_key": "1",
                    "published_at": "2025-01-01",
                    "projection_version": "evidence-test",
                },
            }
        ]


async def test_query_maps_traceable_evidence():
    """正常命中应保留企业、来源、版本、日期和相似度。"""
    service = EvidenceQueryService(
        repository=FakeRepository(),
        embedder_factory=lambda _spec: FakeEmbedder(),
        timeout_seconds=1,
    )

    result = await service.query(EvidenceQuery(eid="eid-1", query="处罚"))

    assert result.data_quality.source_status == SourceStatus.AVAILABLE
    assert result.projection_version == "evidence-test"
    assert result.evidence[0].entity_id == "eid-1"
    assert result.evidence[0].source_id == build_source_id("t_administrative_punishment", "1")
    assert result.evidence[0].score == 0.9
    assert result.sources[0].table == "t_administrative_punishment"


@pytest.mark.parametrize("repository", [FakeRepository(unavailable=True), FakeRepository(wrong_eid=True)])
async def test_query_returns_unavailable_for_source_or_cross_company_failure(repository):
    """基础设施故障或跨企业命中都不得伪装为无结果。"""
    service = EvidenceQueryService(
        repository=repository,
        embedder_factory=lambda _spec: FakeEmbedder(),
        timeout_seconds=1,
    )

    result = await service.query(EvidenceQuery(eid="eid-1", query="处罚"))

    assert result.data_quality.source_status == SourceStatus.UNAVAILABLE
    assert result.evidence == []
