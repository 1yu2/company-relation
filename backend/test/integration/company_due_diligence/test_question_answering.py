"""企业尽调问答真实 MySQL 与 Milvus 组合集成测试。"""

import hashlib
import uuid

import pytest
from sqlalchemy import text

from yuxi.company_due_diligence.entity_resolution import EntityResolutionService
from yuxi.company_due_diligence.evidence.extractor import EvidenceExtractor
from yuxi.company_due_diligence.evidence.indexer import EvidenceIndexRepository
from yuxi.company_due_diligence.evidence.models import EvidenceDocumentType, EvidenceIndexRequest
from yuxi.company_due_diligence.evidence.query_service import EvidenceQueryService
from yuxi.company_due_diligence.evidence.service import EvidenceIndexService
from yuxi.company_due_diligence.models import ResolutionStatus, SourceStatus, build_source_id
from yuxi.company_due_diligence.mysql import CompanyDueDiligenceMySQL
from yuxi.company_due_diligence.qa.graph import QAWorkflow
from yuxi.company_due_diligence.qa.models import CompanyQuestionRequest
from yuxi.company_due_diligence.qa.service import CompanyQuestionService
from yuxi.company_due_diligence.structured_query_service import StructuredQueryService

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


class ForbiddenGraphService:
    """非关系问题不应调用的图谱服务。"""

    async def query(self, _request):
        """任何调用都表示问题路由错误。"""
        raise AssertionError("当前问题不应调用 Neo4j")


class ForbiddenEvidenceService:
    """纯结构化问题不应调用的证据服务。"""

    async def query(self, _request):
        """任何调用都表示问题路由错误。"""
        raise AssertionError("当前问题不应调用 Milvus")


class DeterministicEmbedder:
    """仅用于真实存储集成测试的确定性向量器。"""

    dimension = 8

    async def abatch_encode(self, messages, batch_size=None):
        """批量生成稳定测试向量。"""
        del batch_size
        return [self._vector(message) for message in messages]

    async def aencode_queries(self, queries):
        """生成与文档相同规则的查询向量。"""
        values = [queries] if isinstance(queries, str) else queries
        return [self._vector(value) for value in values]

    @staticmethod
    def _vector(value: str) -> list[float]:
        """把文本哈希转换为八维非零浮点向量。"""
        return [(item + 1) / 256 for item in hashlib.sha256(value.encode()).digest()[:8]]


async def test_real_simple_question_uses_only_mysql_and_cites_sources():
    """真实简单问答应避开图谱和向量服务并引用全部事实来源。"""
    mysql = CompanyDueDiligenceMySQL()
    resolver = EntityResolutionService(mysql=mysql)
    workflow = QAWorkflow(
        structured_service=StructuredQueryService(mysql=mysql),
        graph_service=ForbiddenGraphService(),
        evidence_service=ForbiddenEvidenceService(),
    )
    service = CompanyQuestionService(resolver=resolver, workflow=workflow)
    try:
        async with mysql.connection() as connection:
            result = await connection.execute(
                text("SELECT eid FROM t_enterprise WHERE regist_capi IS NOT NULL ORDER BY eid LIMIT 1")
            )
            eid = result.scalar_one()

        response = await service.query(
            CompanyQuestionRequest(
                company_ref={"eid": eid},
                question="企业注册资本、法人和经营状态是什么？",
            )
        )
        health = await mysql.health()
    finally:
        await mysql.close()

    assert response.resolution_status == ResolutionStatus.RESOLVED
    assert {step.source for step in response.steps} == {"mysql"}
    assert response.facts
    assert all(fact.source_id in {source.source_id for source in response.sources} for fact in response.facts)
    assert all(f"[{fact.source_id}]" in response.answer for fact in response.facts)
    assert response.latency.neo4j_ms == 0
    assert response.latency.milvus_ms == 0
    assert health.pool.checked_out == 0


async def test_real_mixed_question_combines_mysql_and_filtered_evidence(tmp_path):
    """处罚原因问题应组合真实结构化记录和同企业 Milvus 文本证据。"""
    mysql = CompanyDueDiligenceMySQL()
    bucket_name = f"cdd-qa-pytest-{uuid.uuid4().hex[:12]}"
    repository = EvidenceIndexRepository(bucket_name=bucket_name)
    embedder = DeterministicEmbedder()
    index_service = EvidenceIndexService(
        extractor=EvidenceExtractor(mysql=mysql, snapshot_root=tmp_path),
        repository=repository,
    )
    manifest = None
    try:
        async with mysql.connection() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT eid
                    FROM t_administrative_punishment
                    WHERE COALESCE(content, punishment_text, reason, document, description, pdf_content) IS NOT NULL
                    ORDER BY id
                    LIMIT 1
                    """
                )
            )
            eid = result.scalar_one()
        manifest = await index_service.run(
            EvidenceIndexRequest(
                eids=[eid],
                document_types=[EvidenceDocumentType.ADMINISTRATIVE_PUNISHMENT],
                embedding_model_spec=f"pytest:qa-{uuid.uuid4().hex}",
                batch_size=10,
                chunk_size=400,
            ),
            embedder=embedder,
        )
        evidence_service = EvidenceQueryService(
            repository=repository,
            embedder_factory=lambda _spec: embedder,
            timeout_seconds=5,
        )
        service = CompanyQuestionService(
            resolver=EntityResolutionService(mysql=mysql),
            workflow=QAWorkflow(
                structured_service=StructuredQueryService(mysql=mysql),
                graph_service=ForbiddenGraphService(),
                evidence_service=evidence_service,
            ),
        )

        response = await service.query(
            CompanyQuestionRequest(
                company_ref={"eid": eid},
                question="这家公司的行政处罚原因和依据是什么？",
                top_k=5,
            )
        )

        assert response.data_quality.source_status == SourceStatus.AVAILABLE
        assert response.evidence
        assert {item.entity_id for item in response.evidence} == {eid}
        assert response.projection_versions["milvus"] == manifest.projection_version
        assert all(
            item.source_id == build_source_id(source.table, source.record_key)
            for item in response.evidence
            for source in response.sources
            if source.source_id == item.source_id
        )
        assert {step.source for step in response.steps} == {"mysql", "milvus"}
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
