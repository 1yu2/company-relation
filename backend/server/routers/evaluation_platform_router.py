"""平台级评测资源 API。"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from server.utils.auth_middleware import get_admin_user, get_db
from yuxi.evaluation.experiment_service import PlatformEvaluationService
from yuxi.storage.postgres.models_business import User

evaluation_platform_router = APIRouter(prefix="/platform-evaluations", tags=["platform-evaluations"])


class DatasetCreateRequest(BaseModel):
    """创建平台评测数据集请求。"""

    name: str = Field(..., min_length=1, max_length=255)
    description: str = Field(default="", max_length=4000)
    source_kind: str = Field(default="fixture", min_length=1, max_length=32)


class DatasetVersionCreateRequest(BaseModel):
    """创建数据集版本请求。"""

    source: dict[str, Any] = Field(default_factory=dict)
    tasks: list[dict[str, Any]] = Field(default_factory=list)


class ExperimentCreateRequest(BaseModel):
    """创建评测实验请求。"""

    dataset_version_id: str = Field(..., min_length=1, max_length=64)
    source_kind: str | None = Field(default=None, max_length=32)
    agent_slug: str | None = Field(default=None, max_length=255)
    runner: str = Field(default="fixture", max_length=64)
    config_snapshot: dict[str, Any] = Field(default_factory=dict)
    concurrency: int = Field(default=1, ge=1, le=32)
    timeout_seconds: int = Field(default=30, ge=1, le=3600)
    max_attempts: int = Field(default=1, ge=1, le=20)


class PlatformEvaluationConflictError(ValueError):
    """资源已存在或已进入不可变/终态。"""


class PlatformEvaluationInvalidStateError(ValueError):
    """资源当前状态不允许执行请求。"""


def _response(data: Any) -> dict[str, Any]:
    return {"request_id": uuid.uuid4().hex, "data": data}


def _raise_for_domain_error(exc: Exception) -> None:
    if isinstance(exc, LookupError):
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": str(exc)}) from exc
    if isinstance(exc, PlatformEvaluationConflictError):
        raise HTTPException(status_code=409, detail={"code": "conflict", "message": str(exc)}) from exc
    if isinstance(exc, PlatformEvaluationInvalidStateError):
        raise HTTPException(status_code=409, detail={"code": "invalid_state", "message": str(exc)}) from exc
    if isinstance(exc, ValueError):
        message = str(exc)
        if "不可" in message or "终态" in message:
            raise HTTPException(status_code=409, detail={"code": "invalid_state", "message": message}) from exc
        raise HTTPException(status_code=400, detail={"code": "validation_error", "message": message}) from exc
    raise exc


@evaluation_platform_router.post("/datasets")
async def create_dataset(
    payload: DatasetCreateRequest,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """创建平台评测数据集草稿。"""
    try:
        data = await PlatformEvaluationService().create_dataset(
            db=db,
            name=payload.name,
            description=payload.description,
            source_kind=payload.source_kind,
            created_by=str(current_user.uid),
        )
        return _response(data)
    except Exception as exc:
        _raise_for_domain_error(exc)


@evaluation_platform_router.get("/datasets")
async def list_datasets(
    source_kind: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None),
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """查询平台评测数据集。"""
    try:
        return _response(
            await PlatformEvaluationService().list_datasets_page(
                db=db, source_kind=source_kind, limit=limit, cursor=cursor
            )
        )
    except Exception as exc:
        _raise_for_domain_error(exc)


@evaluation_platform_router.get("/datasets/{dataset_id}")
async def get_dataset(
    dataset_id: str,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """查询数据集详情。"""
    data = await PlatformEvaluationService().get_dataset(db=db, dataset_id=dataset_id)
    if data is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "数据集不存在"})
    return _response(data)


@evaluation_platform_router.post("/datasets/{dataset_id}/versions")
async def create_dataset_version(
    dataset_id: str,
    payload: DatasetVersionCreateRequest,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """为数据集创建不可变草稿版本。"""
    try:
        data = await PlatformEvaluationService().create_version(
            db=db, dataset_id=dataset_id, source=payload.source, tasks=payload.tasks
        )
        return _response(data)
    except Exception as exc:
        _raise_for_domain_error(exc)


@evaluation_platform_router.get("/datasets/{dataset_id}/versions")
async def list_dataset_versions(
    dataset_id: str,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """查询数据集版本列表。"""
    try:
        return _response(await PlatformEvaluationService().list_versions(db=db, dataset_id=dataset_id))
    except Exception as exc:
        _raise_for_domain_error(exc)


@evaluation_platform_router.get("/versions/{version_id}")
async def get_dataset_version(
    version_id: str,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """查询数据集版本和题目。"""
    data = await PlatformEvaluationService().get_version(db=db, version_id=version_id)
    if data is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "数据集版本不存在"})
    return _response(data)


@evaluation_platform_router.post("/versions/{version_id}/publish")
async def publish_dataset_version(
    version_id: str,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """发布不可变数据集版本。"""
    try:
        return _response(await PlatformEvaluationService().publish_version(db=db, version_id=version_id))
    except Exception as exc:
        _raise_for_domain_error(exc)


@evaluation_platform_router.post("/experiments")
async def create_experiment(
    payload: ExperimentCreateRequest,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """创建平台评测实验。"""
    try:
        return _response(
            await PlatformEvaluationService().create_experiment(
                db=db, data=payload.model_dump(), created_by=str(current_user.uid)
            )
        )
    except Exception as exc:
        _raise_for_domain_error(exc)


@evaluation_platform_router.get("/experiments")
async def list_experiments(
    source_kind: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None),
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """查询平台评测实验。"""
    try:
        return _response(
            await PlatformEvaluationService().list_experiments_page(
                db=db, source_kind=source_kind, limit=limit, cursor=cursor
            )
        )
    except Exception as exc:
        _raise_for_domain_error(exc)


@evaluation_platform_router.get("/experiments/compare")
async def compare_experiments(
    base_experiment_id: str = Query(..., min_length=1, max_length=64),
    candidate_experiment_id: str = Query(..., min_length=1, max_length=64),
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """逐题对比两次实验（需同一数据集版本与评分口径），返回修复/退化、McNemar 检验、性能与配置差异。"""
    try:
        return _response(
            await PlatformEvaluationService().compare_experiments(
                db=db, base_experiment_id=base_experiment_id, candidate_experiment_id=candidate_experiment_id
            )
        )
    except Exception as exc:
        _raise_for_domain_error(exc)


@evaluation_platform_router.get("/experiments/{experiment_id}")
async def get_experiment(
    experiment_id: str,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """查询实验摘要和聚合指标。"""
    try:
        data = await PlatformEvaluationService().experiment_summary(db=db, experiment_id=experiment_id)
        return _response(data)
    except Exception as exc:
        _raise_for_domain_error(exc)


@evaluation_platform_router.post("/experiments/{experiment_id}/run")
async def run_experiment(
    experiment_id: str,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """由管理员触发实验：写入排队状态并投递 worker 后台执行，立即返回当前摘要。"""
    try:
        return _response(await PlatformEvaluationService().start_experiment(db=db, experiment_id=experiment_id))
    except Exception as exc:
        _raise_for_domain_error(exc)


@evaluation_platform_router.get("/experiments/{experiment_id}/rollouts")
async def list_rollouts(
    experiment_id: str,
    limit: int = Query(default=100, ge=1, le=200),
    cursor: str | None = Query(default=None),
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """查询实验逐题尝试。"""
    try:
        return _response(
            await PlatformEvaluationService().list_rollouts_page(
                db=db, experiment_id=experiment_id, limit=limit, cursor=cursor
            )
        )
    except Exception as exc:
        _raise_for_domain_error(exc)


@evaluation_platform_router.get("/rollouts/{rollout_id}/trace")
async def get_rollout_trace(
    rollout_id: str,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """查询单次尝试的脱敏轨迹。"""
    try:
        data = await PlatformEvaluationService().get_rollout_trace(db=db, rollout_id=rollout_id)
        if data is None:
            raise LookupError("rollout 不存在")
        return _response(data)
    except Exception as exc:
        _raise_for_domain_error(exc)


@evaluation_platform_router.get("/experiments/{experiment_id}/metrics")
async def get_metrics(
    experiment_id: str,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """查询实验逐条指标和聚合结果。"""
    try:
        service = PlatformEvaluationService()
        metrics = await service.list_metrics(db=db, experiment_id=experiment_id)
        summary = await service.experiment_summary(db=db, experiment_id=experiment_id)
        return _response({"items": metrics, "aggregate": summary["aggregate"]})
    except Exception as exc:
        _raise_for_domain_error(exc)


@evaluation_platform_router.get("/experiments/{experiment_id}/failures")
async def get_failures(
    experiment_id: str,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """查询实验失败原因。"""
    try:
        return _response(await PlatformEvaluationService().list_failures(db=db, experiment_id=experiment_id))
    except Exception as exc:
        _raise_for_domain_error(exc)


@evaluation_platform_router.post("/experiments/{experiment_id}/cancel")
async def cancel_experiment(
    experiment_id: str,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """管理员内部取消入口；页面首轮不暴露此操作。"""
    try:
        return _response(await PlatformEvaluationService().cancel_experiment(db=db, experiment_id=experiment_id))
    except Exception as exc:
        _raise_for_domain_error(exc)
