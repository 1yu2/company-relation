"""企业尽调文本证据文档、快照和查询契约。"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections.abc import Iterator
from datetime import UTC, date, datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field, field_validator, model_validator

from yuxi.company_due_diligence.models import DataQuality, Evidence, LatencyBreakdown, Source, build_stable_id


class EvidenceDocumentType(StrEnum):
    """描述首版允许索引的文本证据类型。"""

    ADMINISTRATIVE_PUNISHMENT = "administrative_punishment"
    ENVIRONMENTAL_PUNISHMENT = "environmental_punishment"
    LICENSE = "license"
    PATENT = "patent"
    PRODUCT = "product"
    NEWS = "news"


class EvidenceDocument(BaseModel):
    """描述从单条 MySQL 来源生成的完整清洗文档。"""

    document_id: str
    eid: str
    document_type: EvidenceDocumentType
    title: str
    text: str
    source_id: str
    source_table: str
    source_record_key: str
    published_at: date | None = None
    checksum: str


class EvidenceChunk(BaseModel):
    """描述可独立嵌入且不跨来源的证据片段。"""

    chunk_id: str
    document_id: str
    chunk_index: int = Field(ge=0)
    text: str
    checksum: str


class EvidenceIndexStatistics(BaseModel):
    """描述证据抽取、去重、失败和覆盖统计。"""

    extracted_count: int = Field(ge=0)
    document_count: int = Field(ge=0)
    chunk_count: int = Field(ge=0)
    skipped_empty_count: int = Field(ge=0)
    duplicate_text_count: int = Field(ge=0)
    unmapped_news_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    covered_company_count: int = Field(ge=0)
    document_type_counts: dict[str, int] = Field(default_factory=dict)


class EvidenceIndexManifest(BaseModel):
    """描述可删除重建的证据索引版本和产物位置。"""

    projection_version: str
    checksum: str
    created_at: datetime
    embedding_model_spec: str
    embedding_dimension: int = Field(gt=0)
    collection_name: str
    minio_bucket: str
    minio_prefix: str
    scope_eids: list[str] | None = None
    statistics: EvidenceIndexStatistics


class EvidenceIndexRequest(BaseModel):
    """描述管理员提交的全量或受限证据索引任务。"""

    eids: list[str] | None = Field(default=None, max_length=1000)
    document_types: list[EvidenceDocumentType] = Field(
        default_factory=lambda: list(EvidenceDocumentType),
        min_length=1,
        max_length=len(EvidenceDocumentType),
    )
    embedding_model_spec: str | None = Field(default=None, max_length=512)
    batch_size: int = Field(default=100, ge=10, le=1000)
    chunk_size: int = Field(default=800, ge=200, le=2000)

    @field_validator("eids")
    @classmethod
    def normalize_eids(cls, value: list[str] | None) -> list[str] | None:
        """去重并拒绝空企业范围。"""
        if value is None:
            return None
        normalized = list(dict.fromkeys(item.strip() for item in value if item.strip()))
        if not normalized:
            raise ValueError("eids 不能为空列表")
        return normalized

    @field_validator("document_types")
    @classmethod
    def normalize_document_types(cls, value: list[EvidenceDocumentType]) -> list[EvidenceDocumentType]:
        """按请求顺序去重文档类型。"""
        return list(dict.fromkeys(value))

    @field_validator("embedding_model_spec", mode="before")
    @classmethod
    def normalize_model_spec(cls, value):
        """去除可选模型规格两端空白。"""
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None


class EvidenceIndexInfo(BaseModel):
    """描述 MinIO 当前索引指针和 Milvus 可用状态。"""

    available: bool
    current: EvidenceIndexManifest | None = None


class EvidenceQuery(BaseModel):
    """描述必须绑定企业的文本证据向量查询。"""

    eid: str = Field(min_length=1, max_length=64)
    query: str = Field(min_length=1, max_length=2000)
    document_types: list[EvidenceDocumentType] = Field(
        default_factory=lambda: list(EvidenceDocumentType),
        min_length=1,
        max_length=len(EvidenceDocumentType),
    )
    start_date: date | None = None
    end_date: date | None = None
    top_k: int = Field(default=10, ge=1, le=50)

    @field_validator("eid", "query", mode="before")
    @classmethod
    def normalize_text(cls, value):
        """去除查询文本两端空白。"""
        normalized = str(value).strip()
        return normalized

    @model_validator(mode="after")
    def validate_date_range(self) -> EvidenceQuery:
        """拒绝结束日期早于开始日期的查询。"""
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date 不能早于 start_date")
        return self


class EvidenceQueryResult(BaseModel):
    """描述当前索引版本的证据、质量和阶段耗时。"""

    projection_version: str | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    data_quality: DataQuality
    latency: LatencyBreakdown


class EvidenceSnapshotStore:
    """使用 SQLite 保存去重文档和分块，支持大规模流式重放。"""

    def __init__(self, path: Path):
        self.path = path
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._extracted_count = 0
        self._skipped_empty_count = 0
        self._duplicate_text_count = 0
        self._unmapped_news_count = 0
        self._failed_count = 0
        self._create_schema()

    def _create_schema(self) -> None:
        """创建文档、分块和内容去重索引。"""
        self._connection.executescript(
            """
            PRAGMA journal_mode=DELETE;
            PRAGMA synchronous=NORMAL;
            CREATE TABLE IF NOT EXISTS documents (
                document_id TEXT PRIMARY KEY,
                eid TEXT NOT NULL,
                document_type TEXT NOT NULL,
                title TEXT NOT NULL,
                text TEXT NOT NULL,
                source_id TEXT NOT NULL,
                source_table TEXT NOT NULL,
                source_record_key TEXT NOT NULL,
                published_at TEXT,
                checksum TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_documents_content
                ON documents(eid, document_type, checksum);
            CREATE TABLE IF NOT EXISTS chunks (
                chunk_id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                text TEXT NOT NULL,
                checksum TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_chunks_document ON chunks(document_id, chunk_index);
            """
        )

    def record_extracted(self) -> None:
        """记录已读取的一条来源记录。"""
        self._extracted_count += 1

    def record_empty(self) -> None:
        """记录清洗后为空的来源记录。"""
        self._skipped_empty_count += 1

    def record_unmapped_news(self) -> None:
        """记录无法唯一映射 eid 的新闻。"""
        self._unmapped_news_count += 1

    def record_failed(self) -> None:
        """记录单条来源映射失败。"""
        self._failed_count += 1

    def add_document(self, document: EvidenceDocument, chunks: list[EvidenceChunk]) -> bool:
        """幂等加入文档和分块，重复内容不产生第二份向量。"""
        try:
            self._connection.execute(
                """
                INSERT INTO documents(
                    document_id, eid, document_type, title, text, source_id,
                    source_table, source_record_key, published_at, checksum
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    document.document_id,
                    document.eid,
                    document.document_type.value,
                    document.title,
                    document.text,
                    document.source_id,
                    document.source_table,
                    document.source_record_key,
                    document.published_at.isoformat() if document.published_at else None,
                    document.checksum,
                ),
            )
        except sqlite3.IntegrityError:
            self._duplicate_text_count += 1
            return False

        self._connection.executemany(
            """
            INSERT INTO chunks(chunk_id, document_id, chunk_index, text, checksum)
            VALUES (?, ?, ?, ?, ?)
            """,
            [(chunk.chunk_id, chunk.document_id, chunk.chunk_index, chunk.text, chunk.checksum) for chunk in chunks],
        )
        return True

    def commit(self) -> None:
        """提交当前抽取批次。"""
        self._connection.commit()

    def finalize(
        self,
        *,
        embedding_model_spec: str,
        embedding_dimension: int,
        scope_eids: list[str] | None,
        minio_bucket: str,
    ) -> EvidenceIndexManifest:
        """计算稳定 checksum、版本和完整抽取统计。"""
        self.commit()
        checksum = hashlib.sha256()
        checksum.update(embedding_model_spec.encode())
        checksum.update(str(embedding_dimension).encode())
        for table in ("documents", "chunks"):
            for row in self._connection.execute(f"SELECT * FROM {table} ORDER BY 1"):
                checksum.update(table.encode())
                checksum.update(_canonical_json(row).encode())
        checksum_value = checksum.hexdigest()
        projection_version = f"evidence-{checksum_value[:16]}"
        statistics = EvidenceIndexStatistics(
            extracted_count=self._extracted_count,
            document_count=self._count("documents"),
            chunk_count=self._count("chunks"),
            skipped_empty_count=self._skipped_empty_count,
            duplicate_text_count=self._duplicate_text_count,
            unmapped_news_count=self._unmapped_news_count,
            failed_count=self._failed_count,
            covered_company_count=self._covered_company_count(),
            document_type_counts=self._group_counts("document_type"),
        )
        return EvidenceIndexManifest(
            projection_version=projection_version,
            checksum=checksum_value,
            created_at=datetime.now(UTC),
            embedding_model_spec=embedding_model_spec,
            embedding_dimension=embedding_dimension,
            collection_name=f"cdd_{projection_version.replace('-', '_')}",
            minio_bucket=minio_bucket,
            minio_prefix=f"indexes/{projection_version}",
            scope_eids=scope_eids,
            statistics=statistics,
        )

    def iter_chunks(self, batch_size: int) -> Iterator[list[dict]]:
        """批量返回包含过滤元数据的待嵌入分块。"""
        cursor = self._connection.execute(
            """
            SELECT c.chunk_id, c.document_id, c.chunk_index, c.text, c.checksum,
                   d.eid, d.document_type, d.source_id, d.source_table,
                   d.source_record_key, d.published_at
            FROM chunks c
            JOIN documents d ON d.document_id = c.document_id
            ORDER BY c.chunk_id
            """
        )
        columns = [item[0] for item in cursor.description]
        while rows := cursor.fetchmany(batch_size):
            yield [dict(zip(columns, row, strict=True)) for row in rows]

    def export_documents_jsonl(self, path: Path) -> None:
        """把完整文档和来源元数据流式导出为 MinIO 归档。"""
        cursor = self._connection.execute("SELECT * FROM documents ORDER BY document_id")
        columns = [item[0] for item in cursor.description]
        with path.open("w", encoding="utf-8") as output:
            for row in cursor:
                output.write(_canonical_json(dict(zip(columns, row, strict=True))))
                output.write("\n")

    def close(self) -> None:
        """关闭 SQLite 快照连接。"""
        self._connection.close()

    def _count(self, table: str) -> int:
        """返回快照表行数。"""
        return self._connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def _covered_company_count(self) -> int:
        """返回至少有一篇文档的企业数量。"""
        return self._connection.execute("SELECT COUNT(DISTINCT eid) FROM documents").fetchone()[0]

    def _group_counts(self, field_name: str) -> dict[str, int]:
        """返回文档类型分布。"""
        return {
            key: count
            for key, count in self._connection.execute(
                f"SELECT {field_name}, COUNT(*) FROM documents GROUP BY {field_name} ORDER BY {field_name}"
            )
        }


class EvidenceSnapshot:
    """绑定证据索引清单和可流式重放的 SQLite 快照。"""

    def __init__(self, manifest: EvidenceIndexManifest, store: EvidenceSnapshotStore):
        self.manifest = manifest
        self.store = store

    def close(self) -> None:
        """关闭证据快照资源。"""
        self.store.close()


def create_evidence_store(root: Path | None = None) -> EvidenceSnapshotStore:
    """在派生数据目录创建当前任务专用快照。"""
    snapshot_root = root or Path(os.getenv("COMPANY_DD_EVIDENCE_SNAPSHOT_DIR", "saves/company_due_diligence/evidence"))
    snapshot_root.mkdir(parents=True, exist_ok=True)
    return EvidenceSnapshotStore(snapshot_root / f"staging-{os.urandom(8).hex()}.sqlite")


def publish_evidence_snapshot(store: EvidenceSnapshotStore, manifest: EvidenceIndexManifest) -> EvidenceSnapshot:
    """按 checksum 版本发布 SQLite 快照并重新打开。"""
    store.commit()
    store.close()
    destination = store.path.with_name(f"{manifest.projection_version}.sqlite")
    os.replace(store.path, destination)
    return EvidenceSnapshot(manifest, EvidenceSnapshotStore(destination))


def build_document_id(table: str, record_key: str, document_type: EvidenceDocumentType) -> str:
    """根据来源稳定键生成文档 ID。"""
    return build_stable_id("document", table, record_key, document_type.value)


def build_content_checksum(text: str) -> str:
    """生成清洗后全文的 SHA256 校验和。"""
    return hashlib.sha256(text.encode()).hexdigest()


def build_chunks(document_id: str, texts: list[str]) -> list[EvidenceChunk]:
    """根据文档 ID、顺序和文本 checksum 生成稳定分块。"""
    chunks = []
    for index, text in enumerate(texts):
        checksum = build_content_checksum(text)
        chunks.append(
            EvidenceChunk(
                chunk_id=build_stable_id("evidence", document_id, index, checksum),
                document_id=document_id,
                chunk_index=index,
                text=text,
                checksum=checksum,
            )
        )
    return chunks


def _canonical_json(value: object) -> str:
    """生成参与 checksum 的稳定紧凑 JSON。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
