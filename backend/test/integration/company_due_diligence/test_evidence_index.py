"""企业尽调文本证据真实 MySQL、Milvus 和 MinIO 集成测试。"""

import hashlib
import uuid

import pytest
from sqlalchemy import text

from yuxi.company_due_diligence.evidence.extractor import EvidenceExtractor
from yuxi.company_due_diligence.evidence.indexer import EvidenceIndexRepository
from yuxi.company_due_diligence.evidence.models import EvidenceDocumentType, EvidenceIndexRequest, EvidenceQuery
from yuxi.company_due_diligence.evidence.query_service import EvidenceQueryService
from yuxi.company_due_diligence.evidence.service import EvidenceIndexService
from yuxi.company_due_diligence.models import SourceStatus
from yuxi.company_due_diligence.mysql import CompanyDueDiligenceMySQL

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


class DeterministicEmbedder:
    """仅用于真实存储集成测试的确定性向量器。"""

    dimension = 8

    async def abatch_encode(self, messages, batch_size=None):
        """为每条文本生成稳定归一化测试向量。"""
        del batch_size
        return [self._vector(message) for message in messages]

    async def aencode_queries(self, queries):
        """为查询生成与文档相同规则的稳定向量。"""
        values = [queries] if isinstance(queries, str) else queries
        return [self._vector(value) for value in values]

    @staticmethod
    def _vector(value: str) -> list[float]:
        """把哈希字节转换为八维非零浮点向量。"""
        digest = hashlib.sha256(value.encode()).digest()[:8]
        return [(item + 1) / 256 for item in digest]


async def test_real_index_is_idempotent_filtered_traceable_and_rebuildable(tmp_path):
    """真实派生索引应幂等、隔离企业和日期，并支持删除重建。"""
    mysql = CompanyDueDiligenceMySQL()
    bucket_name = f"cdd-pytest-{uuid.uuid4().hex[:12]}"
    repository = EvidenceIndexRepository(bucket_name=bucket_name)
    service = EvidenceIndexService(
        extractor=EvidenceExtractor(mysql=mysql, snapshot_root=tmp_path),
        repository=repository,
    )
    embedder = DeterministicEmbedder()
    model_spec = f"pytest:deterministic-{uuid.uuid4().hex}"
    manifest = None
    try:
        async with mysql.connection() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT eid
                    FROM t_administrative_punishment
                    WHERE date IS NOT NULL
                      AND COALESCE(content, punishment_text, reason, document, description, pdf_content) IS NOT NULL
                    GROUP BY eid
                    ORDER BY eid
                    LIMIT 2
                    """
                )
            )
            eids = list(result.scalars())
        assert len(eids) == 2

        request = EvidenceIndexRequest(
            eids=eids,
            document_types=[EvidenceDocumentType.ADMINISTRATIVE_PUNISHMENT],
            embedding_model_spec=model_spec,
            batch_size=10,
            chunk_size=400,
        )
        manifest = await service.run(request, embedder=embedder)
        repeated = await service.run(request, embedder=embedder)
        assert repeated.projection_version == manifest.projection_version
        assert repeated.statistics.chunk_count == manifest.statistics.chunk_count

        query_service = EvidenceQueryService(
            repository=repository,
            embedder_factory=lambda _spec: embedder,
            timeout_seconds=5,
        )
        first = await query_service.query(
            EvidenceQuery(
                eid=eids[0],
                query="行政处罚",
                document_types=[EvidenceDocumentType.ADMINISTRATIVE_PUNISHMENT],
                top_k=10,
            )
        )
        second = await query_service.query(
            EvidenceQuery(
                eid=eids[1],
                query="行政处罚",
                document_types=[EvidenceDocumentType.ADMINISTRATIVE_PUNISHMENT],
                top_k=10,
            )
        )

        assert first.data_quality.source_status == SourceStatus.AVAILABLE
        assert second.data_quality.source_status == SourceStatus.AVAILABLE
        assert first.evidence and second.evidence
        assert {item.entity_id for item in first.evidence} == {eids[0]}
        assert {item.entity_id for item in second.evidence} == {eids[1]}
        assert all(item.source_id and item.document_id for item in first.evidence + second.evidence)

        dated = next(item for item in first.evidence + second.evidence if item.published_at is not None)
        exact_date = await query_service.query(
            EvidenceQuery(
                eid=dated.entity_id,
                query="行政处罚",
                document_types=[EvidenceDocumentType.ADMINISTRATIVE_PUNISHMENT],
                start_date=dated.published_at,
                end_date=dated.published_at,
                top_k=10,
            )
        )
        assert exact_date.evidence
        assert all(item.published_at == dated.published_at for item in exact_date.evidence)

        assert await repository.delete_version(manifest.projection_version) is True
        assert await repository.get_current() is None
        rebuilt = await service.run(request, embedder=embedder)
        assert rebuilt.projection_version == manifest.projection_version
        assert rebuilt.statistics == manifest.statistics
        health = await mysql.health()
        assert health.pool.checked_out == 0
    finally:
        if manifest is not None:
            try:
                await repository.delete_version(manifest.projection_version)
            except Exception:
                pass
        try:
            await repository._minio.adelete_bucket(bucket_name)
        except Exception:
            pass
        await mysql.close()


async def test_real_extractor_covers_each_supported_text_source(tmp_path):
    """六类首版来源都应能从真实只读库生成带来源的非空文档。"""
    mysql = CompanyDueDiligenceMySQL()
    extractor = EvidenceExtractor(mysql=mysql, snapshot_root=tmp_path)
    source_queries = {
        EvidenceDocumentType.ADMINISTRATIVE_PUNISHMENT: """
            SELECT eid FROM t_administrative_punishment
            WHERE COALESCE(content, punishment_text, reason, document, description, pdf_content) IS NOT NULL
            ORDER BY id LIMIT 1
        """,
        EvidenceDocumentType.ENVIRONMENTAL_PUNISHMENT: """
            SELECT eid FROM t_huanbaochufas
            WHERE COALESCE(punishments, punishment_reason, punishment_content, punishment_result) IS NOT NULL
            ORDER BY _id LIMIT 1
        """,
        EvidenceDocumentType.LICENSE: """
            SELECT eid FROM t_license_info
            WHERE COALESCE(content, license_name, license_category) IS NOT NULL
            ORDER BY id LIMIT 1
        """,
        EvidenceDocumentType.PATENT: """
            SELECT eid FROM t_patents_relations_info
            WHERE COALESCE(TRIM(brief), '') <> ''
            ORDER BY id LIMIT 1
        """,
        EvidenceDocumentType.PRODUCT: """
            SELECT eid FROM t_company_products
            WHERE COALESCE(TRIM(description), '') <> ''
            ORDER BY id LIMIT 1
        """,
        EvidenceDocumentType.NEWS: """
            SELECT e.eid
            FROM company_news n
            JOIN t_enterprise e ON e.name = n.company_name
            WHERE COALESCE(n.content_summary, n.content_raw, n.title) IS NOT NULL
            ORDER BY n.id LIMIT 1
        """,
    }
    snapshots = []
    try:
        async with mysql.connection() as connection:
            eids = {}
            for document_type, query in source_queries.items():
                result = await connection.execute(text(query))
                eids[document_type] = result.scalar_one()

        for document_type, eid in eids.items():
            snapshot = await extractor.build_snapshot(
                EvidenceIndexRequest(
                    eids=[eid],
                    document_types=[document_type],
                    batch_size=10,
                    chunk_size=400,
                ),
                embedding_model_spec="pytest:extractor",
                embedding_dimension=8,
                minio_bucket="pytest",
            )
            snapshots.append(snapshot)
            assert snapshot.manifest.statistics.document_count > 0
            assert snapshot.manifest.statistics.chunk_count > 0
            assert snapshot.manifest.statistics.covered_company_count == 1
            assert snapshot.manifest.statistics.document_type_counts == {
                document_type.value: snapshot.manifest.statistics.document_count
            }

        health = await mysql.health()
        assert health.pool.checked_out == 0
    finally:
        for snapshot in snapshots:
            path = snapshot.store.path
            snapshot.close()
            path.unlink(missing_ok=True)
        await mysql.close()
