"""企业尽调证据索引仓储单元测试。"""

import asyncio
from datetime import date

import pytest

from yuxi.company_due_diligence.evidence.indexer import (
    EvidenceEmbeddingTimeoutError,
    EvidenceIndexRepository,
    build_milvus_filter,
)
from yuxi.company_due_diligence.evidence.models import (
    EvidenceDocument,
    EvidenceDocumentType,
    EvidenceQuery,
    EvidenceSnapshot,
    EvidenceSnapshotStore,
    build_chunks,
    build_content_checksum,
    build_document_id,
)
from yuxi.company_due_diligence.models import build_source_id


class FakeMilvusClient:
    """记录半成品 collection 是否在失败后被清理。"""

    def __init__(self) -> None:
        self.exists = False
        self.dropped = False

    def has_collection(self, _name: str) -> bool:
        """返回模拟 collection 状态。"""
        return self.exists

    def create_collection(self, **_kwargs) -> None:
        """创建模拟 collection。"""
        self.exists = True

    def drop_collection(self, _name: str) -> None:
        """删除模拟 collection。"""
        self.exists = False
        self.dropped = True

    def close(self) -> None:
        """关闭模拟客户端。"""


class UnusedMinioClient:
    """超时路径不应访问 MinIO。"""


class SlowEmbedder:
    """模拟超过任务超时的 embedding 服务。"""

    dimension = 8

    async def abatch_encode(self, messages, batch_size=None):
        """等待直到测试超时取消。"""
        del messages, batch_size
        await asyncio.sleep(1)
        return []


def _snapshot(tmp_path) -> EvidenceSnapshot:
    """创建包含一个分块的测试快照。"""
    store = EvidenceSnapshotStore(tmp_path / "snapshot.sqlite")
    text = "处罚事实：用于验证 embedding 超时清理半成品 collection 的测试文本。"
    document_id = build_document_id(
        "t_administrative_punishment",
        "1",
        EvidenceDocumentType.ADMINISTRATIVE_PUNISHMENT,
    )
    document = EvidenceDocument(
        document_id=document_id,
        eid="eid-1",
        document_type=EvidenceDocumentType.ADMINISTRATIVE_PUNISHMENT,
        title="处罚",
        text=text,
        source_id=build_source_id("t_administrative_punishment", "1"),
        source_table="t_administrative_punishment",
        source_record_key="1",
        published_at=date(2025, 1, 1),
        checksum=build_content_checksum(text),
    )
    store.add_document(document, build_chunks(document_id, [text]))
    manifest = store.finalize(
        embedding_model_spec="test:model",
        embedding_dimension=8,
        scope_eids=["eid-1"],
        minio_bucket="test",
    )
    return EvidenceSnapshot(manifest, store)


def test_filter_escapes_eid_and_applies_type_and_dates():
    """Milvus 表达式必须转义 eid，并包含全部前置过滤条件。"""
    expression = build_milvus_filter(
        EvidenceQuery(
            eid='eid-"\\-1',
            query="处罚",
            document_types=[EvidenceDocumentType.ADMINISTRATIVE_PUNISHMENT],
            start_date=date(2024, 1, 2),
            end_date=date(2025, 3, 4),
        )
    )

    assert 'eid == "eid-\\"\\\\-1"' in expression
    assert 'document_type in ["administrative_punishment"]' in expression
    assert "published_at_ord >= 20240102" in expression
    assert "published_at_ord <= 20250304" in expression


@pytest.mark.asyncio
async def test_embedding_timeout_drops_partial_collection(tmp_path):
    """嵌入超时不得留下半成品 collection 或切换 current。"""
    client = FakeMilvusClient()
    repository = EvidenceIndexRepository(
        minio_client=UnusedMinioClient(),
        milvus_client_factory=lambda: client,
        bucket_name="test",
    )
    snapshot = _snapshot(tmp_path)
    try:
        with pytest.raises(EvidenceEmbeddingTimeoutError):
            await repository.publish(
                snapshot,
                SlowEmbedder(),
                batch_size=10,
                embedding_timeout_seconds=0.01,
            )
    finally:
        snapshot.close()

    assert client.dropped is True
    assert client.exists is False
