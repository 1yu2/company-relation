from __future__ import annotations

from functools import lru_cache
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from server.utils.auth_middleware import get_admin_user
from yuxi.company_relation.evaluation import CompanyRelationEvaluationService
from yuxi.company_relation.query_service import CompanyRelationQueryService
from yuxi.storage.postgres.models_business import User
from yuxi.utils import logger


company_relation = APIRouter(prefix="/company-relations", tags=["company-relations"])


class CompanyRelationQueryRequest(BaseModel):
    """企业关系自然语言查询请求。"""

    query: str = Field(min_length=1, max_length=500)
    strategy: Literal["vector_rag", "graph_rag", "cypher_direct"] = "graph_rag"
    top_k: int = Field(default=10, ge=1, le=50)


class CompanyRelationEvaluationRequest(BaseModel):
    """企业关系整体评测请求。"""

    strategies: list[Literal["vector_rag", "graph_rag", "cypher_direct"]] = Field(
        default_factory=lambda: ["vector_rag", "graph_rag", "cypher_direct"]
    )
    import_snapshot: bool = True


@lru_cache(maxsize=1)
def _get_query_service() -> CompanyRelationQueryService:
    """按进程缓存已校验的企业关系查询服务。"""
    return CompanyRelationQueryService()


@company_relation.get("/info")
async def get_company_relation_info(current_user: User = Depends(get_admin_user)):
    """返回冻结数据集、来源和图谱质量摘要。"""
    del current_user
    try:
        service = _get_query_service()
        snapshot = service.snapshot
        return {
            "message": "success",
            "data": {
                "dataset_id": snapshot.dataset_id,
                "name": snapshot.name,
                "as_of_date": snapshot.as_of_date,
                "snapshot_checksum": snapshot.checksum(),
                "validation": snapshot.validate().to_dict(),
                "sources": [source.__dict__ for source in snapshot.sources],
            },
        }
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@company_relation.post("/import")
async def import_company_relation_snapshot(current_user: User = Depends(get_admin_user)):
    """校验并替换导入默认企业关系快照。"""
    del current_user
    try:
        service = _get_query_service()
        imported = await service.import_snapshot()
        return {"message": "success", "data": imported}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception(f"企业关系快照导入失败: {exc}")
        raise HTTPException(status_code=500, detail="企业关系快照导入失败") from exc


@company_relation.post("/query")
async def query_company_relation(
    request: CompanyRelationQueryRequest,
    current_user: User = Depends(get_admin_user),
):
    """执行企业关系自然语言查询并返回事实、路径和来源。"""
    del current_user
    try:
        result = await _get_query_service().query(
            request.query,
            strategy=request.strategy,
            top_k=request.top_k,
        )
        return {"message": "success", "data": result.to_dict()}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception(f"企业关系查询失败: {exc}")
        raise HTTPException(status_code=500, detail="企业关系查询失败") from exc


@company_relation.post("/evaluations/run")
async def run_company_relation_evaluation(
    request: CompanyRelationEvaluationRequest,
    current_user: User = Depends(get_admin_user),
):
    """执行三策略冻结题库评测并返回整体结果。"""
    del current_user
    if not request.strategies:
        raise HTTPException(status_code=400, detail="至少选择一种评测策略")
    try:
        query_service = _get_query_service()
        if request.import_snapshot and "cypher_direct" in request.strategies:
            await query_service.import_snapshot()
        report = await CompanyRelationEvaluationService(query_service).run(strategies=request.strategies)
        return {
            "message": "success",
            "data": {
                "run": report["run"],
                "inputs": report["inputs"],
                "graph_quality": report["graph_quality"],
                "strategy_summaries": report["strategy_summaries"],
                "paired_comparison": report["paired_comparison"],
                "regression_gate": report["regression_gate"],
                "artifacts": report.get("artifacts"),
            },
        }
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception(f"企业关系整体评测失败: {exc}")
        raise HTTPException(status_code=500, detail="企业关系整体评测失败") from exc
