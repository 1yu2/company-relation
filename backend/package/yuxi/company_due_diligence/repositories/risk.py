"""企业风险和许可信息只读 Repository。"""

from __future__ import annotations

from datetime import date

from sqlalchemy import text

from yuxi.company_due_diligence.models import SortOrder
from yuxi.company_due_diligence.repositories.base import BaseReadonlyRepository


class RiskRepository(BaseReadonlyRepository):
    """按企业分页查询处罚、经营异常和许可记录。"""

    async def list_administrative_penalties(self, eid: str, **options) -> list[dict]:
        """查询行政处罚摘要，不返回大段处罚正文。"""
        return await self._list(
            eid,
            table="t_administrative_punishment",
            alias="r",
            fields="""
                r.id, r.md5_id, r.eid, r.number, r.illegal_type, r.department,
                r.date AS business_date, r.reason, r.status,
                r.row_update_time, r.local_row_update_time
            """,
            date_expression="r.date",
            key_expression="r.id",
            **options,
        )

    async def list_environmental_penalties(self, eid: str, **options) -> list[dict]:
        """查询环保处罚摘要，不返回处罚全文。"""
        return await self._list(
            eid,
            table="t_huanbaochufas",
            alias="r",
            fields="""
                r._id AS id, r.eid, r.document_no, r.illegal_type,
                r.punishment_title, r.punishment_reason, r.punishment_dept,
                r.punishment_type, r.punish_amnt, r.punishment_date AS business_date,
                r.local_row_update_time
            """,
            date_expression="r.punishment_date",
            key_expression="r._id",
            **options,
        )

    async def list_abnormal_operations(self, eid: str, **options) -> list[dict]:
        """查询经营异常记录及移出状态。"""
        return await self._list(
            eid,
            table="t_abnormal_enterprises",
            alias="r",
            fields="""
                r.ID AS id, r.md5, r.eid, r.judge_date AS business_date,
                r.judge_department, r.overdue_type, r.pub_date, r.reason,
                r.state, r.overdue_amount, r.is_cancel, r.is_history,
                r.row_update_time, r.local_row_update_time
            """,
            date_expression="r.judge_date",
            key_expression="r.ID",
            **options,
        )

    async def list_licenses(self, eid: str, **options) -> list[dict]:
        """查询行政许可和有效期信息。"""
        return await self._list(
            eid,
            table="t_license_info",
            alias="r",
            fields="""
                r.id, r.eid, r.u_id, r.number, r.start_date, r.end_date,
                r.department, r.name, r.status,
                COALESCE(r.license_date, r.start_date, r.end_date) AS business_date,
                r.source_department, r.license_no, r.license_category,
                r.license_name, r.row_update_time, r.local_row_update_time
            """,
            date_expression="COALESCE(r.license_date, r.start_date, r.end_date)",
            key_expression="r.id",
            **options,
        )

    async def _list(
        self,
        eid: str,
        *,
        table: str,
        alias: str,
        fields: str,
        date_expression: str,
        key_expression: str,
        start_date: date | None,
        end_date: date | None,
        limit: int,
        offset: int,
        sort_order: SortOrder,
    ) -> list[dict]:
        """执行仅由固定调用方提供表名和字段的共享风险查询。"""
        filters, parameters = _date_filters(date_expression, start_date, end_date)
        direction = sort_order.value.upper()
        parameters.update({"eid": eid, "limit": limit, "offset": offset})
        return await self._fetch_all(
            text(
                f"""
                SELECT {fields}
                FROM {table} {alias}
                WHERE {alias}.eid = :eid
                  {filters}
                ORDER BY {date_expression} {direction}, {key_expression} {direction}
                LIMIT :limit OFFSET :offset
                """
            ),
            parameters,
        )


def _date_filters(expression: str, start_date: date | None, end_date: date | None) -> tuple[str, dict]:
    """为固定业务日期表达式构造参数化范围条件。"""
    clauses: list[str] = []
    parameters: dict[str, object] = {}
    if start_date is not None:
        clauses.append(f"AND {expression} >= :start_date")
        parameters["start_date"] = start_date
    if end_date is not None:
        clauses.append(f"AND {expression} <= :end_date")
        parameters["end_date"] = end_date
    return "\n".join(clauses), parameters
