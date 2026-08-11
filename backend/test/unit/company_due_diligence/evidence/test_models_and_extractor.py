"""企业尽调证据文档和抽取辅助逻辑单元测试。"""

from datetime import date

import pytest

from yuxi.company_due_diligence.evidence.extractor import EvidenceExtractor, _safe_date, clean_text, split_semantic_text
from yuxi.company_due_diligence.evidence.models import (
    EvidenceDocument,
    EvidenceDocumentType,
    EvidenceSnapshotStore,
    EvidenceIndexRequest,
    build_chunks,
    build_content_checksum,
    build_document_id,
)
from yuxi.company_due_diligence.models import build_source_id


def _document(text: str, record_key: str = "1") -> EvidenceDocument:
    """构造固定来源的测试证据文档。"""
    return EvidenceDocument(
        document_id=build_document_id(
            "t_administrative_punishment",
            record_key,
            EvidenceDocumentType.ADMINISTRATIVE_PUNISHMENT,
        ),
        eid="eid-1",
        document_type=EvidenceDocumentType.ADMINISTRATIVE_PUNISHMENT,
        title="行政处罚",
        text=text,
        source_id=build_source_id("t_administrative_punishment", record_key),
        source_table="t_administrative_punishment",
        source_record_key=record_key,
        published_at=date(2025, 1, 2),
        checksum=build_content_checksum(text),
    )


def test_document_and_chunk_ids_are_stable():
    """相同来源和文本应生成稳定文档及分块标识。"""
    first_document = _document("违法事实：测试内容")
    second_document = _document("违法事实：测试内容")
    first_chunks = build_chunks(first_document.document_id, [first_document.text])
    second_chunks = build_chunks(second_document.document_id, [second_document.text])

    assert first_document.document_id == second_document.document_id
    assert first_document.checksum == second_document.checksum
    assert first_chunks == second_chunks


def test_snapshot_deduplicates_content_and_model_changes_version(tmp_path):
    """同企业同类型重复文本只保留一份，不同模型生成不同版本。"""
    first_store = EvidenceSnapshotStore(tmp_path / "first.sqlite")
    second_store = EvidenceSnapshotStore(tmp_path / "second.sqlite")
    document = _document("违法事实：足够长的测试处罚内容，用于验证重复文本不会产生第二份向量。")
    duplicate = _document(document.text, record_key="2")
    chunks = build_chunks(document.document_id, [document.text])

    assert first_store.add_document(document, chunks) is True
    assert first_store.add_document(duplicate, build_chunks(duplicate.document_id, [duplicate.text])) is False
    assert second_store.add_document(document, chunks) is True
    first_manifest = first_store.finalize(
        embedding_model_spec="provider:model-a",
        embedding_dimension=8,
        scope_eids=["eid-1"],
        minio_bucket="test",
    )
    second_manifest = second_store.finalize(
        embedding_model_spec="provider:model-b",
        embedding_dimension=8,
        scope_eids=["eid-1"],
        minio_bucket="test",
    )
    first_store.close()
    second_store.close()

    assert first_manifest.statistics.document_count == 1
    assert first_manifest.statistics.duplicate_text_count == 1
    assert first_manifest.projection_version != second_manifest.projection_version


def test_clean_and_semantic_split_preserve_readable_sections():
    """HTML 和模板噪声应清除，分块优先保留完整句子。"""
    cleaned = clean_text("<p>处罚事实</p>\n暂无\n处罚决定：罚款。第二句说明！")
    chunks = split_semantic_text(cleaned, max_chars=12)

    assert "<p>" not in cleaned
    assert "暂无" not in cleaned
    assert all(len(chunk) <= 12 for chunk in chunks)
    assert "".join(chunks).replace("\n", "") == cleaned.replace("\n", "")


def test_invalid_date_is_not_fabricated():
    """非法业务日期应保留为空而不是生成错误日期。"""
    assert _safe_date("not-a-date") is None


class FakeResult:
    """模拟 SQLAlchemy mappings 结果。"""

    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        """返回可迭代映射结果。"""
        return self._rows


class FakeNewsConnection:
    """先返回无法映射新闻，再返回游标结束。"""

    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, _statement, _parameters):
        """返回固定新闻批次。"""
        self.calls += 1
        if self.calls == 1:
            return FakeResult(
                [
                    {
                        "id": 1,
                        "company_name": "未映射企业",
                        "pub_time": "2025-01-01",
                        "title": "测试新闻",
                        "source": "测试来源",
                        "url": None,
                        "sentiment": None,
                        "content_summary": "这是一条不会强行绑定企业的测试新闻摘要。",
                        "content_raw": None,
                    }
                ]
            )
        return FakeResult([])


@pytest.mark.asyncio
async def test_unmapped_news_is_skipped_instead_of_forced_to_eid(tmp_path):
    """非标准全称新闻应计入未映射并且不生成文档。"""
    store = EvidenceSnapshotStore(tmp_path / "news.sqlite")
    extractor = EvidenceExtractor()
    request = EvidenceIndexRequest(
        eids=None,
        document_types=[EvidenceDocumentType.NEWS],
        batch_size=10,
        chunk_size=400,
    )
    await extractor._extract_news(
        FakeNewsConnection(),
        store,
        request,
        {"eid-1": "标准企业全称"},
    )
    manifest = store.finalize(
        embedding_model_spec="test:model",
        embedding_dimension=8,
        scope_eids=None,
        minio_bucket="test",
    )
    store.close()

    assert manifest.statistics.unmapped_news_count == 1
    assert manifest.statistics.document_count == 0
