"""企业尽调图谱投影任务编排服务。"""

from __future__ import annotations

import hashlib

from yuxi.company_due_diligence.graph.projection import GraphProjectionBuilder
from yuxi.company_due_diligence.graph.repository import CompanyDueDiligenceGraphRepository
from yuxi.company_due_diligence.graph.schema import GraphProjectionInfo, GraphProjectionRequest, ProjectionManifest
from yuxi.services.task_service import TaskContext, Tasker

GRAPH_PROJECTION_TASK_TYPE = "company_due_diligence_graph_projection"
_ACTIVE_STATUSES = {"pending", "running"}


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
        context: TaskContext | None = None,
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

        async def run_projection(context: TaskContext):
            """执行单个 Tasker 图谱投影任务。"""
            manifest = await self.run(request, context)
            result = manifest.model_dump(mode="json")
            await context.set_result(result)
            return result

        return await tasker.enqueue_unique_by_payload(
            name="企业尽调 Neo4j 投影",
            task_type=GRAPH_PROJECTION_TASK_TYPE,
            payload={
                "scope_key": scope_key,
                "scope_size": len(request.eids) if request.eids else None,
                "batch_size": request.batch_size,
            },
            coroutine=run_projection,
            payload_match={"scope_key": scope_key},
            statuses=_ACTIVE_STATUSES,
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
