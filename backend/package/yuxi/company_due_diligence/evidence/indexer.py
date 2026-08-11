"""企业尽调证据 Milvus 索引与 MinIO 版本发布。"""

from __future__ import annotations

import asyncio
import json
import math
import os
import tempfile
from collections.abc import Callable
from contextlib import suppress
from datetime import date
from pathlib import Path
from typing import Protocol

from pymilvus import DataType, MilvusClient

from yuxi.company_due_diligence.evidence.models import EvidenceIndexManifest, EvidenceQuery, EvidenceSnapshot
from yuxi.storage.minio import MinIOClient, StorageError, get_minio_client

EVIDENCE_BUCKET = os.getenv("COMPANY_DD_EVIDENCE_BUCKET", "company-due-diligence")
CURRENT_INDEX_OBJECT = "current.json"


class EvidenceIndexUnavailableError(RuntimeError):
    """表示 Milvus、MinIO 或当前索引版本不可用。"""


class EvidenceEmbeddingTimeoutError(TimeoutError):
    """表示证据嵌入批次超过允许时间。"""


class EvidenceEmbedder(Protocol):
    """约束生产模型与测试向量器共同使用的异步接口。"""

    dimension: int | None

    async def abatch_encode(self, messages: list[str], batch_size: int | None = None) -> list[list[float]]:
        """批量生成文档向量。"""

    async def aencode_queries(self, queries: list[str] | str) -> list[list[float]]:
        """生成查询向量。"""


class EvidenceIndexRepository:
    """管理版本化 Milvus collection 和 MinIO 索引清单。"""

    def __init__(
        self,
        *,
        minio_client: MinIOClient | None = None,
        milvus_client_factory: Callable[[], MilvusClient] | None = None,
        bucket_name: str = EVIDENCE_BUCKET,
    ) -> None:
        self._minio = minio_client or get_minio_client()
        self._milvus_client_factory = milvus_client_factory or _create_milvus_client
        self._bucket_name = bucket_name

    @property
    def bucket_name(self) -> str:
        """返回证据索引专用 MinIO bucket。"""
        return self._bucket_name

    async def publish(
        self,
        snapshot: EvidenceSnapshot,
        embedder: EvidenceEmbedder,
        *,
        batch_size: int,
        embedding_timeout_seconds: float,
        progress=None,
    ) -> EvidenceIndexManifest:
        """写入并校验版本化索引，最后原子更新 MinIO 当前指针。"""
        manifest = snapshot.manifest
        client = await asyncio.to_thread(self._milvus_client_factory)
        created_collection = False
        try:
            collection_ready = await self._collection_matches(client, manifest)
            if not collection_ready:
                if await asyncio.to_thread(client.has_collection, manifest.collection_name):
                    await asyncio.to_thread(client.drop_collection, manifest.collection_name)
                await asyncio.to_thread(self._create_collection, client, manifest)
                created_collection = True
                await self._insert_chunks(
                    client,
                    snapshot,
                    embedder,
                    batch_size=batch_size,
                    timeout_seconds=embedding_timeout_seconds,
                    progress=progress,
                )
                await asyncio.to_thread(client.flush, manifest.collection_name)
                await asyncio.to_thread(client.load_collection, manifest.collection_name)

            await self._validate_collection(client, manifest)
            await self._upload_snapshot(snapshot)
            await self._write_current(manifest)
            return manifest
        except EvidenceEmbeddingTimeoutError:
            if created_collection:
                await self._drop_collection_quietly(client, manifest.collection_name)
            raise
        except Exception as exc:
            if created_collection:
                await self._drop_collection_quietly(client, manifest.collection_name)
            if isinstance(exc, EvidenceIndexUnavailableError):
                raise
            raise EvidenceIndexUnavailableError("证据索引发布失败") from exc
        finally:
            await asyncio.to_thread(client.close)

    async def get_current(self) -> EvidenceIndexManifest | None:
        """读取当前索引清单，并确认 Milvus collection 与统计一致。"""
        client = None
        try:
            client = await asyncio.to_thread(self._milvus_client_factory)
            bucket_exists = await asyncio.to_thread(self._minio.client.bucket_exists, self._bucket_name)
            if not bucket_exists:
                return None
            current_size = await self._minio.astat_file(self._bucket_name, CURRENT_INDEX_OBJECT)
            if current_size is None:
                return None
            payload = await self._minio.adownload_file(self._bucket_name, CURRENT_INDEX_OBJECT)
            manifest = EvidenceIndexManifest.model_validate_json(payload)
            await self._validate_collection(client, manifest)
            return manifest
        except (StorageError, ValueError, json.JSONDecodeError) as exc:
            raise EvidenceIndexUnavailableError("证据索引清单不可用") from exc
        except EvidenceIndexUnavailableError:
            raise
        except Exception as exc:
            raise EvidenceIndexUnavailableError("Milvus 或 MinIO 当前不可用") from exc
        finally:
            if client is not None:
                await asyncio.to_thread(client.close)

    async def search(
        self,
        manifest: EvidenceIndexManifest,
        query: EvidenceQuery,
        vector: list[float],
    ) -> list[dict]:
        """使用强制 eid、类型和日期过滤表达式执行向量召回。"""
        if len(vector) != manifest.embedding_dimension:
            raise EvidenceIndexUnavailableError("查询向量维度与当前证据索引不一致")
        client = None
        try:
            client = await asyncio.to_thread(self._milvus_client_factory)
            filter_expression = build_milvus_filter(query)
            results = await asyncio.to_thread(
                client.search,
                collection_name=manifest.collection_name,
                data=[vector],
                filter=filter_expression,
                limit=query.top_k,
                output_fields=[
                    "document_id",
                    "eid",
                    "document_type",
                    "text",
                    "source_id",
                    "source_table",
                    "source_record_key",
                    "published_at",
                    "projection_version",
                ],
                search_params={"metric_type": "COSINE", "params": {}},
            )
            return results[0] if results else []
        except EvidenceIndexUnavailableError:
            raise
        except Exception as exc:
            raise EvidenceIndexUnavailableError("Milvus 证据查询失败") from exc
        finally:
            if client is not None:
                await asyncio.to_thread(client.close)

    async def delete_version(self, projection_version: str) -> bool:
        """删除指定派生版本；当前版本同时清除指针以避免悬挂引用。"""
        client = None
        try:
            client = await asyncio.to_thread(self._milvus_client_factory)
            current = await self._read_current_without_validation()
            collection_name = f"cdd_{projection_version.replace('-', '_')}"
            existed = await asyncio.to_thread(client.has_collection, collection_name)
            if existed:
                await asyncio.to_thread(client.drop_collection, collection_name)
            deleted_objects = await self._minio.adelete_objects_by_prefix(
                self._bucket_name,
                f"indexes/{projection_version}/",
            )
            if current and current.projection_version == projection_version:
                await self._minio.adelete_file(self._bucket_name, CURRENT_INDEX_OBJECT)
            return existed or deleted_objects > 0
        except Exception as exc:
            raise EvidenceIndexUnavailableError("删除证据索引版本失败") from exc
        finally:
            if client is not None:
                await asyncio.to_thread(client.close)

    async def _insert_chunks(
        self,
        client: MilvusClient,
        snapshot: EvidenceSnapshot,
        embedder: EvidenceEmbedder,
        *,
        batch_size: int,
        timeout_seconds: float,
        progress,
    ) -> None:
        """批量嵌入并 upsert 稳定 chunk ID。"""
        total = snapshot.manifest.statistics.chunk_count
        indexed = 0
        for batch in snapshot.store.iter_chunks(batch_size):
            texts = [item["text"] for item in batch]
            try:
                vectors = await asyncio.wait_for(
                    embedder.abatch_encode(texts, batch_size=batch_size),
                    timeout=timeout_seconds,
                )
            except TimeoutError as exc:
                raise EvidenceEmbeddingTimeoutError("证据嵌入批次超时") from exc
            _validate_vectors(vectors, len(batch), snapshot.manifest.embedding_dimension)
            records = [
                _milvus_record(item, vector, snapshot.manifest.projection_version)
                for item, vector in zip(batch, vectors, strict=True)
            ]
            await asyncio.to_thread(client.upsert, snapshot.manifest.collection_name, records)
            indexed += len(records)
            if progress is not None:
                fraction = indexed / total if total else 1
                await progress(92 + fraction * 5, f"Milvus 已写入 {indexed}/{total} 个证据分块")

    async def _upload_snapshot(self, snapshot: EvidenceSnapshot) -> None:
        """在当前指针切换前上传完整文档归档和版本清单。"""
        manifest = snapshot.manifest
        with tempfile.TemporaryDirectory(prefix="cdd-evidence-") as directory:
            documents_path = Path(directory) / "documents.jsonl"
            snapshot.store.export_documents_jsonl(documents_path)
            await asyncio.to_thread(
                self._minio.upload_file_from_path,
                manifest.minio_bucket,
                f"{manifest.minio_prefix}/documents.jsonl",
                str(documents_path),
            )
        await self._minio.aupload_file(
            manifest.minio_bucket,
            f"{manifest.minio_prefix}/manifest.json",
            manifest.model_dump_json(indent=2).encode(),
            "application/json",
        )

    async def _write_current(self, manifest: EvidenceIndexManifest) -> None:
        """用单对象覆盖完成当前版本原子切换。"""
        await self._minio.aupload_file(
            manifest.minio_bucket,
            CURRENT_INDEX_OBJECT,
            manifest.model_dump_json(indent=2).encode(),
            "application/json",
        )

    async def _read_current_without_validation(self) -> EvidenceIndexManifest | None:
        """读取当前指针，仅用于删除流程判断是否需要清除。"""
        bucket_exists = await asyncio.to_thread(self._minio.client.bucket_exists, self._bucket_name)
        if not bucket_exists:
            return None
        size = await self._minio.astat_file(self._bucket_name, CURRENT_INDEX_OBJECT)
        if size is None:
            return None
        payload = await self._minio.adownload_file(self._bucket_name, CURRENT_INDEX_OBJECT)
        return EvidenceIndexManifest.model_validate_json(payload)

    async def _collection_matches(self, client: MilvusClient, manifest: EvidenceIndexManifest) -> bool:
        """判断同版本 collection 是否已经完整写入。"""
        if not await asyncio.to_thread(client.has_collection, manifest.collection_name):
            return False
        try:
            await self._validate_collection(client, manifest)
            return True
        except EvidenceIndexUnavailableError:
            return False

    async def _validate_collection(self, client: MilvusClient, manifest: EvidenceIndexManifest) -> None:
        """校验 collection 存在且实体数等于快照分块数。"""
        exists = await asyncio.to_thread(client.has_collection, manifest.collection_name)
        if not exists:
            raise EvidenceIndexUnavailableError("当前证据索引 collection 不存在")
        stats = await asyncio.to_thread(client.get_collection_stats, manifest.collection_name)
        row_count = int(stats.get("row_count", -1))
        if row_count != manifest.statistics.chunk_count:
            raise EvidenceIndexUnavailableError("当前证据索引实体数校验失败")

    @staticmethod
    def _create_collection(client: MilvusClient, manifest: EvidenceIndexManifest) -> None:
        """创建包含固定过滤字段的证据 collection。"""
        schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field("chunk_id", DataType.VARCHAR, is_primary=True, max_length=64)
        schema.add_field("vector", DataType.FLOAT_VECTOR, dim=manifest.embedding_dimension)
        schema.add_field("document_id", DataType.VARCHAR, max_length=64)
        schema.add_field("eid", DataType.VARCHAR, max_length=64)
        schema.add_field("document_type", DataType.VARCHAR, max_length=64)
        schema.add_field("text", DataType.VARCHAR, max_length=8192)
        schema.add_field("source_id", DataType.VARCHAR, max_length=64)
        schema.add_field("source_table", DataType.VARCHAR, max_length=128)
        schema.add_field("source_record_key", DataType.VARCHAR, max_length=1024)
        schema.add_field("published_at", DataType.VARCHAR, max_length=10)
        schema.add_field("published_at_ord", DataType.INT64)
        schema.add_field("projection_version", DataType.VARCHAR, max_length=64)
        index_params = MilvusClient.prepare_index_params()
        index_params.add_index(field_name="vector", index_type="AUTOINDEX", metric_type="COSINE")
        client.create_collection(
            collection_name=manifest.collection_name,
            schema=schema,
            index_params=index_params,
            consistency_level="Strong",
        )

    @staticmethod
    async def _drop_collection_quietly(client: MilvusClient, collection_name: str) -> None:
        """失败清理半成品 collection，不覆盖原始异常。"""
        with suppress(Exception):
            if await asyncio.to_thread(client.has_collection, collection_name):
                await asyncio.to_thread(client.drop_collection, collection_name)


def build_milvus_filter(query: EvidenceQuery) -> str:
    """从已验证值生成 Milvus 标量过滤表达式并转义字符串。"""
    document_types = ", ".join(f'"{_escape_filter_string(item.value)}"' for item in query.document_types)
    clauses = [
        f'eid == "{_escape_filter_string(query.eid)}"',
        f"document_type in [{document_types}]",
    ]
    if query.start_date:
        clauses.append(f"published_at_ord >= {_date_ordinal(query.start_date)}")
    if query.end_date:
        clauses.append(f"published_at_ord <= {_date_ordinal(query.end_date)}")
    return " and ".join(clauses)


def _create_milvus_client() -> MilvusClient:
    """按现有环境变量创建独立 Milvus 客户端。"""
    uri = os.getenv("MILVUS_URI") or "http://localhost:19530"
    token = os.getenv("MILVUS_TOKEN") or ""
    database = os.getenv("MILVUS_DB_NAME") or "default"
    client = MilvusClient(uri=uri, token=token)
    if database not in client.list_databases():
        client.create_database(database)
    client.use_database(database)
    return client


def _milvus_record(item: dict, vector: list[float], projection_version: str) -> dict:
    """把 SQLite 分块映射为固定 Milvus schema 记录。"""
    published_at = item["published_at"] or ""
    return {
        "chunk_id": item["chunk_id"],
        "vector": vector,
        "document_id": item["document_id"],
        "eid": item["eid"],
        "document_type": item["document_type"],
        "text": item["text"],
        "source_id": item["source_id"],
        "source_table": item["source_table"],
        "source_record_key": item["source_record_key"],
        "published_at": published_at,
        "published_at_ord": int(published_at.replace("-", "")) if published_at else 0,
        "projection_version": projection_version,
    }


def _validate_vectors(vectors: list[list[float]], expected_count: int, expected_dimension: int) -> None:
    """拒绝数量、维度或数值非法的 embedding 响应。"""
    if len(vectors) != expected_count:
        raise ValueError("Embedding 返回数量与证据分块数不一致")
    for vector in vectors:
        if len(vector) != expected_dimension:
            raise ValueError("Embedding 返回维度与索引清单不一致")
        if not all(math.isfinite(value) for value in vector):
            raise ValueError("Embedding 返回非有限数值")


def _escape_filter_string(value: str) -> str:
    """转义 Milvus 双引号字符串中的反斜杠和引号。"""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _date_ordinal(value: date) -> int:
    """把日期转换为可范围过滤的 YYYYMMDD 整数。"""
    return value.year * 10000 + value.month * 100 + value.day
