"""企业尽调全局范围的固定白名单统计服务。"""

from __future__ import annotations

import hashlib
import time
from datetime import UTC, datetime
from enum import StrEnum

from yuxi.company_due_diligence.models import (
    DataQuality,
    LatencyBreakdown,
    Source,
    SourceStatus,
    build_source_id,
)
from yuxi.company_due_diligence.mysql import (
    CompanyDueDiligenceMySQL,
    MySQLUnavailableError,
    company_due_diligence_mysql,
)
from yuxi.company_due_diligence.qa.models import GlobalQuestionItem, GlobalQuestionResult
from yuxi.company_due_diligence.repositories.enterprise import EnterpriseRepository


class GlobalQuestionOperation(StrEnum):
    """描述当前允许的全局统计问题。"""

    CATALOG_COVERAGE = "catalog_coverage"
    SHARED_SHAREHOLDERS = "shared_shareholders"


class GlobalQuestionResponse:
    """描述一次全局统计的结果、来源和阶段耗时。"""

    def __init__(
        self,
        *,
        operation: GlobalQuestionOperation,
        result: GlobalQuestionResult,
        answer: str,
        sources: list[Source],
        data_quality: DataQuality,
        latency: LatencyBreakdown,
    ) -> None:
        self.operation = operation
        self.result = result
        self.answer = answer
        self.sources = sources
        self.data_quality = data_quality
        self.latency = latency


class GlobalQuestionService:
    """只执行预先定义的全局统计 SQL，不接受用户提供的查询语句。"""

    def __init__(self, mysql: CompanyDueDiligenceMySQL | None = None) -> None:
        self._mysql = mysql or company_due_diligence_mysql

    async def query(self, question: str, *, top_k: int = 10) -> GlobalQuestionResponse | None:
        """识别全局问题并返回固定统计；非全局问题返回 None。"""
        operation = classify_global_question(question)
        if operation is None:
            return None

        started_at = time.perf_counter()
        queried_at = datetime.now(UTC)
        try:
            async with self._mysql.connection() as connection:
                repository = EnterpriseRepository(connection)
                if operation == GlobalQuestionOperation.CATALOG_COVERAGE:
                    coverage = await repository.get_catalog_coverage()
                    companies = await repository.list_catalog(limit=top_k)
                    return _coverage_response(coverage, companies, queried_at, _elapsed_ms(started_at))
                groups = await repository.list_shared_shareholders(top_k)
                coverage = await repository.get_catalog_coverage()
                return _shared_shareholder_response(groups, coverage, queried_at, _elapsed_ms(started_at))
        except MySQLUnavailableError:
            elapsed_ms = _elapsed_ms(started_at)
            return GlobalQuestionResponse(
                operation=operation,
                result=GlobalQuestionResult(
                    operation=operation.value,
                    data_version="unavailable",
                    denominator=0,
                    skipped_reasons=["企业事实主库当前不可用"],
                ),
                answer="企业事实主库当前不可用，无法生成全局统计。",
                sources=[],
                data_quality=DataQuality(
                    source_status=SourceStatus.UNAVAILABLE,
                    warnings=["企业事实主库当前不可用"],
                ),
                latency=LatencyBreakdown(mysql_ms=elapsed_ms, total_ms=elapsed_ms),
            )


def classify_global_question(question: str) -> GlobalQuestionOperation | None:
    """按固定关键词判断是否属于全局统计问题。"""
    normalized = "".join(str(question).split())
    if any(keyword in normalized for keyword in ("共同股东", "共有股东", "哪些企业有共同股东")):
        return GlobalQuestionOperation.SHARED_SHAREHOLDERS
    if any(keyword in normalized for keyword in ("数据覆盖哪些企业", "当前覆盖哪些企业", "覆盖哪些企业", "企业总数")):
        return GlobalQuestionOperation.CATALOG_COVERAGE
    return None


def _coverage_response(coverage: dict, companies: list[dict], queried_at: datetime, elapsed_ms: float):
    """组装数据目录覆盖统计。"""
    total = int(coverage.get("enterprise_count") or 0)
    data_version = _data_version(coverage)
    sources = []
    items = []
    for row in companies:
        source_id = build_source_id("t_enterprise", str(row["eid"]))
        sources.append(
            Source(
                source_id=source_id,
                table="t_enterprise",
                record_key=str(row["eid"]),
                business_date=row.get("local_row_update_time") or row.get("row_update_time"),
                queried_at=queried_at,
            )
        )
        items.append(
            GlobalQuestionItem(
                key=str(row["eid"]),
                label=str(row.get("name") or row["eid"]),
                value=1,
                metadata={"credit_code_present": int(bool(row.get("credit_no")))},
                source_ids=[source_id],
            )
        )
    metrics = _coverage_metrics(coverage, total)
    result = GlobalQuestionResult(
        operation=GlobalQuestionOperation.CATALOG_COVERAGE.value,
        data_version=data_version,
        denominator=total,
        items=items,
        metrics=metrics,
        skipped_reasons=[f"仅展示前 {len(items)} 家企业，完整分母为 {total} 家"] if len(items) < total else [],
    )
    visible = "、".join(item.label for item in items) or "暂无企业记录"
    answer = f"当前数据目录共有 {total} 家企业。示例企业：{visible}。"
    return GlobalQuestionResponse(
        operation=GlobalQuestionOperation.CATALOG_COVERAGE,
        result=result,
        answer=answer,
        sources=sources,
        data_quality=DataQuality(source_status=SourceStatus.AVAILABLE),
        latency=LatencyBreakdown(mysql_ms=elapsed_ms, total_ms=elapsed_ms),
    )


def _shared_shareholder_response(groups: list[dict], coverage: dict, queried_at: datetime, elapsed_ms: float):
    """组装共同股东企业关系统计。"""
    total = int(coverage.get("enterprise_count") or 0)
    sources = []
    items = []
    for row in groups:
        source_ids = []
        for record_key in str(row.get("source_record_keys") or "").split(","):
            if not record_key:
                continue
            source_id = build_source_id("t_partners_alls", record_key)
            source_ids.append(source_id)
            sources.append(
                Source(
                    source_id=source_id,
                    table="t_partners_alls",
                    record_key=record_key,
                    queried_at=queried_at,
                )
            )
        enterprise_eids = [item for item in str(row.get("enterprise_eids") or "").split(",") if item]
        enterprise_names = [item for item in str(row.get("enterprise_names") or "").split("、") if item]
        items.append(
            GlobalQuestionItem(
                key=str(row.get("shareholder_key")),
                label=str(row.get("shareholder_name") or row.get("shareholder_key")),
                value=int(row.get("enterprise_count") or 0),
                metadata={"enterprise_eids": enterprise_eids, "enterprise_names": enterprise_names},
                source_ids=source_ids,
            )
        )
    result = GlobalQuestionResult(
        operation=GlobalQuestionOperation.SHARED_SHAREHOLDERS.value,
        data_version=_data_version(coverage),
        denominator=total,
        items=items,
        metrics={"shared_shareholder_group_count": len(items)},
        skipped_reasons=[],
    )
    if items:
        answer = "发现以下共同股东关系：" + "；".join(f"{item.label}关联 {item.value} 家企业" for item in items) + "。"
    else:
        answer = "当前数据目录中没有发现由当前股东记录支持的共同股东关系。"
    return GlobalQuestionResponse(
        operation=GlobalQuestionOperation.SHARED_SHAREHOLDERS,
        result=result,
        answer=answer,
        sources=sources,
        data_quality=DataQuality(source_status=SourceStatus.AVAILABLE),
        latency=LatencyBreakdown(mysql_ms=elapsed_ms, total_ms=elapsed_ms),
    )


def _coverage_metrics(coverage: dict, total: int) -> dict[str, int | float]:
    """计算固定数据域的企业覆盖率。"""
    metrics: dict[str, int | float] = {}
    fields = {
        "named_enterprise_count": "enterprise_identity",
        "listed_company_count": "listed_company",
        "shareholders_company_count": "shareholders",
        "products_company_count": "products",
        "financing_company_count": "financing",
        "risks_company_count": "risks",
    }
    for field, domain in fields.items():
        count = int(coverage.get(field) or 0)
        metrics[f"{domain}_company_count"] = count
        metrics[f"{domain}_company_coverage_rate"] = round(count / total, 6) if total else 0.0
    for field in ("shareholders", "products", "financing", "risks"):
        metrics[f"{field}_record_count"] = int(coverage.get(f"{field}_record_count") or 0)
    return metrics


def _data_version(coverage: dict) -> str:
    """从目录分母和最新更新时间生成不包含敏感值的数据版本。"""
    payload = f"{coverage.get('enterprise_count', 0)}:{coverage.get('latest_update_time', '')}"
    return f"catalog-{hashlib.sha256(payload.encode()).hexdigest()[:16]}"


def _elapsed_ms(started_at: float) -> float:
    """返回毫秒级阶段耗时。"""
    return round((time.perf_counter() - started_at) * 1000, 3)


global_question_service = GlobalQuestionService()
