"""企业尽调质量与性能评测接口。"""

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from server.utils.auth_middleware import get_admin_user
from yuxi.company_due_diligence.evaluation.catalog import AutoDatasetUnavailableError
from yuxi.company_due_diligence.evaluation.runner import QualityEvaluationRequest
from yuxi.company_due_diligence.evaluation.service import QualityExportFormat, quality_evaluation_service
from yuxi.company_due_diligence.performance.models import PerformanceRunRequest
from yuxi.company_due_diligence.performance.service import (
    PerformanceExportFormat,
    performance_evaluation_service,
)
from yuxi.services.task_service import tasker
from yuxi.storage.postgres.models_business import User

company_due_diligence_evaluation_router = APIRouter(
    prefix="/company-due-diligence/evaluations",
    tags=["company-due-diligence-evaluations"],
)


@company_due_diligence_evaluation_router.get("/datasets")
async def list_quality_datasets(current_user: User = Depends(get_admin_user)) -> dict:
    """返回广东企业与 AkShare 基线分组后的冻结数据集摘要。"""
    del current_user
    return {"datasets": quality_evaluation_service.list_datasets()}


@company_due_diligence_evaluation_router.get("/datasets/{dataset_id}")
async def get_quality_dataset(
    dataset_id: str,
    current_user: User = Depends(get_admin_user),
) -> dict:
    """返回单个冻结数据集及其 Golden 样本。"""
    del current_user
    try:
        return quality_evaluation_service.get_dataset(dataset_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@company_due_diligence_evaluation_router.post("/runs", status_code=status.HTTP_202_ACCEPTED)
async def create_quality_run(
    request: QualityEvaluationRequest,
    current_user: User = Depends(get_admin_user),
) -> dict:
    """提交使用真实问答和报告服务的质量评测任务。"""
    del current_user
    try:
        evaluation_task = await quality_evaluation_service.create_run(request, tasker)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AutoDatasetUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"run_id": evaluation_task.id, "task_id": evaluation_task.id, "status": "queued"}


@company_due_diligence_evaluation_router.post("/auto-runs", status_code=status.HTTP_202_ACCEPTED)
async def create_auto_quality_run(
    request: QualityEvaluationRequest,
    current_user: User = Depends(get_admin_user),
) -> dict:
    """从全局只读数据目录生成 source-derived 样本并提交评测。"""
    del current_user
    automatic_request = request.model_copy(update={"dataset_id": None, "scope": "global"})
    try:
        evaluation_task = await quality_evaluation_service.create_run(automatic_request, tasker)
    except AutoDatasetUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"run_id": evaluation_task.id, "task_id": evaluation_task.id, "status": "queued"}


@company_due_diligence_evaluation_router.get("/coverage")
async def get_global_evaluation_coverage(
    sampling_policy: str = Query(default="all_or_stratified"),
    current_user: User = Depends(get_admin_user),
) -> dict:
    """返回全局自动评测的数据分母、覆盖率、版本和 checksum。"""
    del current_user
    try:
        return await quality_evaluation_service.get_coverage(sampling_policy=sampling_policy)
    except AutoDatasetUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@company_due_diligence_evaluation_router.get("/runs")
async def list_quality_runs(
    limit: int = Query(default=50, ge=1, le=100),
    current_user: User = Depends(get_admin_user),
) -> dict:
    """返回质量评测后台任务摘要。"""
    del current_user
    return {"runs": await quality_evaluation_service.list_runs(tasker, limit)}


@company_due_diligence_evaluation_router.get("/runs/{run_id}")
async def get_quality_run(
    run_id: str,
    current_user: User = Depends(get_admin_user),
) -> dict:
    """返回质量运行进度、部分结果和最终报告。"""
    del current_user
    try:
        return await quality_evaluation_service.get_run(run_id, tasker)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@company_due_diligence_evaluation_router.get("/auto-runs/{run_id}")
async def get_auto_quality_run(
    run_id: str,
    current_user: User = Depends(get_admin_user),
) -> dict:
    """返回自动评测任务和最终报告。"""
    del current_user
    try:
        return await quality_evaluation_service.get_run(run_id, tasker)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@company_due_diligence_evaluation_router.post("/runs/{run_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
async def cancel_quality_run(
    run_id: str,
    current_user: User = Depends(get_admin_user),
) -> dict:
    """请求取消尚未进入终态的质量评测任务。"""
    del current_user
    try:
        cancelled = await quality_evaluation_service.cancel(run_id, tasker)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not cancelled:
        raise HTTPException(status_code=409, detail="质量评测已进入终态，无法取消")
    return {"run_id": run_id, "cancel_requested": True}


@company_due_diligence_evaluation_router.get("/runs/{run_id}/download")
async def download_quality_run(
    run_id: str,
    export_format: QualityExportFormat = Query(alias="format"),
    current_user: User = Depends(get_admin_user),
) -> Response:
    """下载并校验 JSON、JSONL 或 Markdown 质量报告。"""
    del current_user
    try:
        content, artifact = await quality_evaluation_service.download(run_id, export_format)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    media_types = {
        QualityExportFormat.JSON: "application/json",
        QualityExportFormat.JSONL: "application/x-ndjson",
        QualityExportFormat.MARKDOWN: "text/markdown; charset=utf-8",
    }
    suffixes = {
        QualityExportFormat.JSON: "json",
        QualityExportFormat.JSONL: "jsonl",
        QualityExportFormat.MARKDOWN: "md",
    }
    filename = f"company-due-diligence-quality-{run_id}.{suffixes[export_format]}"
    return Response(
        content=content,
        media_type=media_types[export_format],
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "ETag": artifact.checksum,
        },
    )


@company_due_diligence_evaluation_router.post("/performance-runs", status_code=status.HTTP_202_ACCEPTED)
async def create_performance_run(
    request: PerformanceRunRequest,
    current_user: User = Depends(get_admin_user),
) -> dict:
    """提交受安全参数硬限制的性能评测任务。"""
    del current_user
    try:
        performance_task = await performance_evaluation_service.create_run(request, tasker)
    except AutoDatasetUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"run_id": performance_task.id, "task_id": performance_task.id, "status": "queued"}


@company_due_diligence_evaluation_router.get("/performance-runs")
async def list_performance_runs(
    limit: int = Query(default=50, ge=1, le=100),
    current_user: User = Depends(get_admin_user),
) -> dict:
    """返回性能评测后台任务摘要。"""
    del current_user
    return {"runs": await performance_evaluation_service.list_runs(tasker, limit)}


@company_due_diligence_evaluation_router.get("/performance-runs/global")
async def get_global_performance_summary(
    window_minutes: int = Query(default=1440, ge=1, le=43200),
    current_user: User = Depends(get_admin_user),
) -> dict:
    """返回时间窗口内脱敏的全局性能聚合。"""
    del current_user
    return await performance_evaluation_service.get_global_summary(tasker, window_minutes=window_minutes)


@company_due_diligence_evaluation_router.get("/performance-runs/{run_id}")
async def get_performance_run(
    run_id: str,
    current_user: User = Depends(get_admin_user),
) -> dict:
    """返回性能任务进度、逐请求部分结果和最终报告。"""
    del current_user
    try:
        return await performance_evaluation_service.get_run(run_id, tasker)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@company_due_diligence_evaluation_router.post(
    "/performance-runs/{run_id}/cancel",
    status_code=status.HTTP_202_ACCEPTED,
)
async def cancel_performance_run(
    run_id: str,
    current_user: User = Depends(get_admin_user),
) -> dict:
    """请求取消尚未进入终态的性能评测。"""
    del current_user
    try:
        cancelled = await performance_evaluation_service.cancel(run_id, tasker)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not cancelled:
        raise HTTPException(status_code=409, detail="性能评测已进入终态，无法取消")
    return {"run_id": run_id, "cancel_requested": True}


@company_due_diligence_evaluation_router.get("/performance-runs/{run_id}/download")
async def download_performance_run(
    run_id: str,
    export_format: PerformanceExportFormat = Query(alias="format"),
    current_user: User = Depends(get_admin_user),
) -> Response:
    """下载并校验 JSON 或 Markdown 性能报告。"""
    del current_user
    try:
        content, artifact = await performance_evaluation_service.download(run_id, export_format)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    media_type = "application/json" if export_format == PerformanceExportFormat.JSON else "text/markdown; charset=utf-8"
    suffix = "json" if export_format == PerformanceExportFormat.JSON else "md"
    filename = f"company-due-diligence-performance-{run_id}.{suffix}"
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "ETag": artifact.checksum,
        },
    )
