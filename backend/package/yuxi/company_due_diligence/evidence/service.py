"""企业尽调文本证据索引任务编排。"""

from __future__ import annotations

import asyncio
import hashlib
import os
from contextlib import suppress
from datetime import timedelta

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
from yuxi.repositories.task_repository import TaskRepository
from yuxi.services.task_service import PersistedTaskContext, Tasker
from yuxi.utils.datetime_utils import utc_now_naive

EVIDENCE_INDEX_TASK_TYPE = "company_due_diligence_evidence_index"
RECOVERY_STALE_SECONDS = 15 * 60
MAX_RECOVERY_ATTEMPTS = 1
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
        context: PersistedTaskContext | None = None,
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

        return await tasker.enqueue_persisted_arq(
            name="企业尽调文本证据索引",
            task_type=EVIDENCE_INDEX_TASK_TYPE,
            payload={"scope_key": scope_key, "request": request.model_dump(mode="json")},
            job_name="process_due_diligence_evidence_index",
            job_id=f"evidence:{scope_key}",
        )

    async def execute_task(self, task_id: str) -> None:
        """由 ARQ worker 认领并执行持久化证据索引任务。"""
        repository = TaskRepository()
        record = await repository.claim_pending(task_id, EVIDENCE_INDEX_TASK_TYPE)
        if record is None:
            return
        context = PersistedTaskContext(task_id, repository, record.payload or {})
        try:
            request = EvidenceIndexRequest.model_validate(record.payload["request"])
            manifest = await self.run(request, context)
            await repository.update_status(
                task_id,
                expected_status="running",
                values={
                    "status": "success",
                    "progress": 100.0,
                    "message": "任务已完成",
                    "result": manifest.model_dump(mode="json"),
                    "completed_at": utc_now_naive(),
                },
            )
        except asyncio.CancelledError:
            await repository.update_status(
                task_id,
                expected_status="running",
                values={"status": "cancelled", "message": "任务已取消", "completed_at": utc_now_naive()},
            )
            raise
        except Exception as exc:
            await repository.update_status(
                task_id,
                expected_status="running",
                values={
                    "status": "failed",
                    "message": "任务执行失败",
                    "error": str(exc),
                    "completed_at": utc_now_naive(),
                },
            )
            raise

    async def recover_pending_tasks(self) -> None:
        """worker 启动时补投递仍处于 pending 的证据索引任务。"""
        from yuxi.services.run_queue_service import get_arq_pool

        repository = TaskRepository()
        queue = await get_arq_pool()
        stale_before = utc_now_naive() - timedelta(seconds=RECOVERY_STALE_SECONDS)
        for record in await repository.list_by_type(EVIDENCE_INDEX_TASK_TYPE):
            if record.status == "pending":
                scope_key = (record.payload or {}).get("scope_key", record.id)
                await queue.enqueue_job(
                    "process_due_diligence_evidence_index",
                    record.id,
                    _job_id=f"evidence:{scope_key}",
                )
            elif record.status == "running" and record.updated_at and record.updated_at <= stale_before:
                payload = dict(record.payload or {})
                attempts = int(payload.get("_recovery_attempts", 0) or 0)
                if attempts < MAX_RECOVERY_ATTEMPTS:
                    payload["_recovery_attempts"] = attempts + 1
                    changed = await repository.update_status(
                        record.id,
                        expected_status="running",
                        values={"status": "pending", "payload": payload, "message": "恢复过期证据索引任务"},
                    )
                    if changed:
                        scope_key = payload.get("scope_key", record.id)
                        await queue.enqueue_job(
                            "process_due_diligence_evidence_index",
                            record.id,
                            _job_id=f"evidence:{scope_key}",
                        )
                else:
                    await repository.update_status(
                        record.id,
                        expected_status="running",
                        values={"status": "failed", "message": "任务超过最大恢复次数", "completed_at": utc_now_naive()},
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
