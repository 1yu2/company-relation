"""企业财务指标只读 Repository。"""

from __future__ import annotations

from datetime import date

from sqlalchemy import text

from yuxi.company_due_diligence.models import SortOrder
from yuxi.company_due_diligence.repositories.base import BaseReadonlyRepository


class FinancialMetricRepository(BaseReadonlyRepository):
    """查询 eid 和信用代码可映射的财务指标序列。"""

    async def list_series(
        self,
        eid: str,
        *,
        credit_code: str | None,
        indicator_name: str | None,
        start_date: date | None,
        end_date: date | None,
        limit: int,
        offset: int,
        sort_order: SortOrder,
    ) -> list[dict]:
        """合并两个可追溯财务来源并按报告期确定排序。"""
        company_filters, wind_filters, parameters = _filters(indicator_name, start_date, end_date)
        direction = sort_order.value.upper()
        parameters.update({"eid": eid, "limit": limit, "offset": offset})
        return await self._fetch_all(
            text(
                f"""
                SELECT *
                FROM (
                    SELECT
                        CONVERT('company_indicator' USING utf8mb4) COLLATE utf8mb4_unicode_ci AS source_table,
                        CONVERT(CAST(c.id AS CHAR) USING utf8mb4) COLLATE utf8mb4_unicode_ci AS record_key,
                        CONVERT(c.eid USING utf8mb4) COLLATE utf8mb4_unicode_ci AS eid,
                        CONVERT(c.stock_code USING utf8mb4) COLLATE utf8mb4_unicode_ci AS stock_code,
                        CONVERT(c.company_name USING utf8mb4) COLLATE utf8mb4_unicode_ci AS company_name,
                        CONVERT(c.indicator_name USING utf8mb4) COLLATE utf8mb4_unicode_ci AS indicator_name,
                        CONVERT(CAST(c.indicator_value AS CHAR) USING utf8mb4)
                            COLLATE utf8mb4_unicode_ci AS indicator_value,
                        CONVERT(c.indicator_unit USING utf8mb4) COLLATE utf8mb4_unicode_ci AS indicator_unit,
                        CONVERT(CAST(c.reporting_period AS CHAR) USING utf8mb4)
                            COLLATE utf8mb4_unicode_ci AS reporting_period,
                        CONVERT(c.remark USING utf8mb4) COLLATE utf8mb4_unicode_ci AS remark,
                        c.updated_at AS source_updated_at
                    FROM company_indicator c
                    WHERE c.eid = :eid
                      {company_filters}

                    UNION ALL

                    SELECT
                        CONVERT('wind_listed_company_metrics' USING utf8mb4)
                            COLLATE utf8mb4_unicode_ci AS source_table,
                        CONVERT(CAST(w.id AS CHAR) USING utf8mb4) COLLATE utf8mb4_unicode_ci AS record_key,
                        CONVERT(CAST(:eid AS CHAR) USING utf8mb4) COLLATE utf8mb4_unicode_ci AS eid,
                        CONVERT(w.stock_code USING utf8mb4) COLLATE utf8mb4_unicode_ci AS stock_code,
                        CONVERT(NULL USING utf8mb4) COLLATE utf8mb4_unicode_ci AS company_name,
                        CONVERT(w.indicator_name USING utf8mb4) COLLATE utf8mb4_unicode_ci AS indicator_name,
                        CONVERT(CAST(w.indicator_value AS CHAR) USING utf8mb4)
                            COLLATE utf8mb4_unicode_ci AS indicator_value,
                        CONVERT(w.indicator_unit USING utf8mb4) COLLATE utf8mb4_unicode_ci AS indicator_unit,
                        CONVERT(CAST(w.period_month AS CHAR) USING utf8mb4)
                            COLLATE utf8mb4_unicode_ci AS reporting_period,
                        CONVERT(NULL USING utf8mb4) COLLATE utf8mb4_unicode_ci AS remark,
                        w.updated_at AS source_updated_at
                    FROM wind_listed_company_metrics w
                    WHERE w.credit_code = :credit_code
                      {wind_filters}
                ) metrics
                ORDER BY metrics.reporting_period {direction},
                         metrics.indicator_name ASC,
                         metrics.source_table ASC,
                         metrics.record_key ASC
                LIMIT :limit OFFSET :offset
                """
            ),
            {**parameters, "credit_code": credit_code},
        )

    async def get_coverage(self, eid: str, credit_code: str | None, indicator_name: str | None) -> dict:
        """统计两个财务来源的可映射行数，用于明确覆盖警告。"""
        company_filter = "AND c.indicator_name = :indicator_name" if indicator_name else ""
        wind_filter = "AND w.indicator_name = :indicator_name" if indicator_name else ""
        return await self._fetch_one(
            text(
                f"""
                SELECT
                    (
                        SELECT COUNT(*)
                        FROM company_indicator c
                        WHERE c.eid = :eid {company_filter}
                    ) AS company_indicator_count,
                    (
                        SELECT COUNT(*)
                        FROM wind_listed_company_metrics w
                        WHERE w.credit_code = :credit_code {wind_filter}
                    ) AS wind_metric_count
                """
            ),
            {"eid": eid, "credit_code": credit_code, "indicator_name": indicator_name},
        ) or {"company_indicator_count": 0, "wind_metric_count": 0}


def _filters(
    indicator_name: str | None,
    start_date: date | None,
    end_date: date | None,
) -> tuple[str, str, dict]:
    """分别构造两张财务表的参数化指标和报告期条件。"""
    company_clauses: list[str] = []
    wind_clauses: list[str] = []
    parameters: dict[str, object] = {}
    if indicator_name:
        company_clauses.append("AND c.indicator_name = :indicator_name")
        wind_clauses.append("AND w.indicator_name = :indicator_name")
        parameters["indicator_name"] = indicator_name
    if start_date is not None:
        company_clauses.append("AND c.reporting_period >= :start_date")
        wind_clauses.append("AND w.period_month >= :start_month")
        parameters["start_date"] = start_date
        parameters["start_month"] = start_date.strftime("%Y-%m")
    if end_date is not None:
        company_clauses.append("AND c.reporting_period <= :end_date")
        wind_clauses.append("AND w.period_month <= :end_month")
        parameters["end_date"] = end_date
        parameters["end_month"] = end_date.strftime("%Y-%m")
    return "\n".join(company_clauses), "\n".join(wind_clauses), parameters
