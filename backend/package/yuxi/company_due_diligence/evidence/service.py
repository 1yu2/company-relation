"""企业尽调文本证据索引任务编排。"""

from __future__ import annotations

import hashlib
import os
from contextlib import suppress

from yuxi.company_due_diligence.evidence.extractor import EvidenceExtractor
from yuxi.company_due_diligence.evidence.indexer import (
    EVIDENCE_BUCKET,
    EvidenceEmbedder,
    EvidenceIndexRepository,
)
from yuxi.company_due_diligence.evidence.models import EvidenceIndexInfo, EvidenceIndexManifest, EvidenceIndexRequest
from yuxi.config import config
from yuxi.models.embed import select_embedding_model
from yuxi.models.providers.cache import model_cache
from yuxi.services.task_service import TaskContext, Tasker

EVIDENCE_INDEX_TASK_TYPE = "company_due_diligence_evidence_index"
_ACTIVE_STATUSES = {"pending", "running"}


class EvidenceIndexService:
    """协调只读抽取、embedding、Milvus 写入和 Tasker 状态。"""

    def __init__(
        self,
        extractor: EvidenceExtractor | None = None,
        repository: EvidenceIndexRepository | None = None,
    ) -> None:
        self._extractor = extractor or EvidenceExtractor()
        self._repository = repository or EvidenceIndexRepository()

    async def run(
        self,
        request: EvidenceIndexRequest,
        context: TaskContext | None = None,
        *,
        embedder: EvidenceEmbedder | None = None,
    ) -> EvidenceIndexManifest:
        """构建证据快照并发布，任何阶段失败都不切换当前版本。"""
        model_spec = request.embedding_model_spec or config.embed_model
        resolved_embedder = embedder or select_embedding_model(model_spec)
        if embedder is None and not str(getattr(resolved_embedder, "api_key", "")).strip():
            raise ValueError(f"Embedding 模型未配置 API Key: {model_spec}")
        dimension = _resolve_dimension(model_spec, resolved_embedder)
        timeout_seconds = float(
            os.getenv("COMPANY_DD_EMBEDDING_TIMEOUT_SECONDS") or os.getenv("EMBEDDING_TIMEOUT") or 600
        )

        async def report(progress: float, message: str) -> None:
            """把索引阶段进度转发到 Tasker。"""
            if context is not None:
                await context.raise_if_cancelled()
                await context.set_progress(progress, message)

        snapshot = await self._extractor.build_snapshot(
            request,
            embedding_model_spec=model_spec,
            embedding_dimension=dimension,
            minio_bucket=self._repository.bucket_name,
            progress=report,
        )
        snapshot_path = snapshot.store.path
        try:
            manifest = await self._repository.publish(
                snapshot,
                resolved_embedder,
                batch_size=request.batch_size,
                embedding_timeout_seconds=timeout_seconds,
                progress=report,
            )
            await report(100, "证据索引校验并切换完成")
            return manifest
        finally:
            snapshot.close()
            with suppress(FileNotFoundError):
                snapshot_path.unlink()

    async def enqueue(self, request: EvidenceIndexRequest, tasker: Tasker):
        """按范围、类型和模型去重提交后台索引任务。"""
        scope_key = _scope_key(request)

        async def run_index(context: TaskContext):
            """执行单个 Tasker 文本证据索引任务。"""
            manifest = await self.run(request, context)
            result = manifest.model_dump(mode="json")
            await context.set_result(result)
            return result

        return await tasker.enqueue_unique_by_payload(
            name="企业尽调文本证据索引",
            task_type=EVIDENCE_INDEX_TASK_TYPE,
            payload={
                "scope_key": scope_key,
                "scope_size": len(request.eids) if request.eids else None,
                "document_types": [item.value for item in request.document_types],
                "embedding_model_spec": request.embedding_model_spec or config.embed_model,
                "batch_size": request.batch_size,
                "chunk_size": request.chunk_size,
            },
            coroutine=run_index,
            payload_match={"scope_key": scope_key},
            statuses=_ACTIVE_STATUSES,
        )

    async def get_task(self, task_id: str, tasker: Tasker) -> dict | None:
        """只返回企业尽调证据索引类型的 Tasker 任务。"""
        task = await tasker.get_task(task_id)
        if task is None or task["type"] != EVIDENCE_INDEX_TASK_TYPE:
            return None
        return task

    async def get_info(self) -> EvidenceIndexInfo:
        """返回当前文本证据索引版本和可用状态。"""
        return EvidenceIndexInfo(available=True, current=await self._repository.get_current())

    async def delete_version(self, projection_version: str) -> bool:
        """删除可重建的指定索引版本。"""
        return await self._repository.delete_version(projection_version)


def _resolve_dimension(model_spec: str, embedder: EvidenceEmbedder) -> int:
    """从注入向量器或模型缓存取得确定 embedding 维度。"""
    dimension = getattr(embedder, "dimension", None)
    if dimension:
        return int(dimension)
    model_info = model_cache.get_model_info(model_spec)
    if model_info and model_info.dimension:
        return int(model_info.dimension)
    raise ValueError(f"Embedding 模型未配置维度: {model_spec}")


def _scope_key(request: EvidenceIndexRequest) -> str:
    """生成不暴露企业范围明细的任务幂等键。"""
    payload = "\n".join(
        [
            *(sorted(request.eids) if request.eids else ["full"]),
            *(sorted(item.value for item in request.document_types)),
            request.embedding_model_spec or config.embed_model,
            str(request.chunk_size),
        ]
    ).encode()
    return hashlib.sha256(payload).hexdigest()[:24]


evidence_index_service = EvidenceIndexService(
    repository=EvidenceIndexRepository(bucket_name=EVIDENCE_BUCKET),
)
