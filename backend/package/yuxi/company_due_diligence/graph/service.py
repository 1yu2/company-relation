"""企业尽调图谱投影任务编排服务。"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import timedelta

from yuxi.company_due_diligence.graph.projection import GraphProjectionBuilder
from yuxi.company_due_diligence.graph.repository import CompanyDueDiligenceGraphRepository
from yuxi.company_due_diligence.graph.schema import GraphProjectionInfo, GraphProjectionRequest, ProjectionManifest
from yuxi.repositories.task_repository import TaskRepository
from yuxi.services.task_service import PersistedTaskContext, Tasker
from yuxi.utils.datetime_utils import utc_now_naive

GRAPH_PROJECTION_TASK_TYPE = "company_due_diligence_graph_projection"
RECOVERY_STALE_SECONDS = 15 * 60
MAX_RECOVERY_ATTEMPTS = 1


class GraphProjectionService:
    """协调 MySQL 快照生成、Neo4j 幂等导入和 Tasker 状态。"""

    def __init__(
        self,
        builder: GraphProjectionBuilder | None = None,
        repository: CompanyDueDiligenceGraphRepository | None = None,
    ) -> None:
        self._builder = builder or GraphProjectionBuilder()
        self._repository = repository or CompanyDueDiligenceGraphRepository()

    async def run(
        self,
        request: GraphProjectionRequest,
        context: PersistedTaskContext | None = None,
    ) -> ProjectionManifest:
        """生成完整快照后写入 Neo4j，并保证快照资源最终关闭。"""

        async def report(progress: float, message: str) -> None:
            """把投影阶段进度转发到 Tasker。"""
            if context is not None:
                await context.raise_if_cancelled()
                await context.set_progress(progress, message)

        snapshot = await self._builder.build_snapshot(
            eids=request.eids,
            batch_size=request.batch_size,
            progress=report,
        )
        try:
            await report(96, "开始写入 Neo4j 投影")
            manifest = await self._repository.replace_projection(snapshot, request.batch_size)
            await report(100, "Neo4j 投影校验并切换完成")
            return manifest
        finally:
            snapshot.close()

    async def enqueue(self, request: GraphProjectionRequest, tasker: Tasker):
        """按投影范围去重提交后台任务。"""
        scope_key = _scope_key(request.eids)

        return await tasker.enqueue_persisted_arq(
            name="企业尽调 Neo4j 投影",
            task_type=GRAPH_PROJECTION_TASK_TYPE,
            payload={"scope_key": scope_key, "request": request.model_dump(mode="json")},
            job_name="process_due_diligence_graph_projection",
            job_id=f"graph:{scope_key}",
        )

    async def execute_task(self, task_id: str) -> None:
        """由 ARQ worker 认领并执行持久化图投影任务。"""
        repository = TaskRepository()
        record = await repository.claim_pending(task_id, GRAPH_PROJECTION_TASK_TYPE)
        if record is None:
            return
        context = PersistedTaskContext(task_id, repository, record.payload or {})
        try:
            request = GraphProjectionRequest.model_validate(record.payload["request"])
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
        """worker 启动时补投递仍处于 pending 的图投影任务。"""
        from yuxi.services.run_queue_service import get_arq_pool

        repository = TaskRepository()
        queue = await get_arq_pool()
        stale_before = utc_now_naive() - timedelta(seconds=RECOVERY_STALE_SECONDS)
        for record in await repository.list_by_type(GRAPH_PROJECTION_TASK_TYPE):
            if record.status == "pending":
                scope_key = (record.payload or {}).get("scope_key", record.id)
                await queue.enqueue_job(
                    "process_due_diligence_graph_projection", record.id, _job_id=f"graph:{scope_key}"
                )
            elif record.status == "running" and record.updated_at and record.updated_at <= stale_before:
                payload = dict(record.payload or {})
                attempts = int(payload.get("_recovery_attempts", 0) or 0)
                if attempts < MAX_RECOVERY_ATTEMPTS:
                    payload["_recovery_attempts"] = attempts + 1
                    changed = await repository.update_status(
                        record.id,
                        expected_status="running",
                        values={"status": "pending", "payload": payload, "message": "恢复过期图投影任务"},
                    )
                    if changed:
                        scope_key = payload.get("scope_key", record.id)
                        await queue.enqueue_job(
                            "process_due_diligence_graph_projection", record.id, _job_id=f"graph:{scope_key}"
                        )
                else:
                    await repository.update_status(
                        record.id,
                        expected_status="running",
                        values={"status": "failed", "message": "任务超过最大恢复次数", "completed_at": utc_now_naive()},
                    )

    async def get_task(self, task_id: str, tasker: Tasker) -> dict | None:
        """只返回属于企业尽调图投影类型的 Tasker 任务。"""
        task = await tasker.get_task(task_id)
        if task is None or task["type"] != GRAPH_PROJECTION_TASK_TYPE:
            return None
        return task

    async def get_info(self) -> GraphProjectionInfo:
        """返回 Neo4j 当前投影信息。"""
        return await self._repository.get_info()


def _scope_key(eids: list[str] | None) -> str:
    """生成不在任务列表暴露企业标识集合的范围键。"""
    if eids is None:
        return "full"
    payload = "\n".join(sorted(eids)).encode()
    return f"subset:{hashlib.sha256(payload).hexdigest()[:16]}"


graph_projection_service = GraphProjectionService()
