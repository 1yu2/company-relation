"""企业知识产权关系只读 Repository。"""

from __future__ import annotations

from datetime import date

from sqlalchemy import text

from yuxi.company_due_diligence.models import SortOrder
from yuxi.company_due_diligence.repositories.base import BaseReadonlyRepository


class IntellectualPropertyRepository(BaseReadonlyRepository):
    """按 eid 查询专利、商标和著作权关系。"""

    async def list_patents(self, eid: str, **options) -> list[dict]:
        """使用 eid 索引分页查询专利关系。"""
        return await self._list(
            eid,
            table="t_patents_relations_info",
            alias="i",
            fields="""
                i.id, i._id, i.eid, i.role, i.history_role_tag, i.type,
                i.type_name, i.patent_name, i.authorize_tag,
                i.request_num_standard, i.request_date,
                COALESCE(i.authorize_date, i.request_date) AS business_date,
                i.last_status,
                i.is_history, i.local_row_update_time
            """,
            date_expression="COALESCE(i.authorize_date, i.request_date)",
            key_expression="i.id",
            **options,
        )

    async def list_trademarks(self, eid: str, **options) -> list[dict]:
        """使用 eid 索引分页查询商标关系。"""
        return await self._list(
            eid,
            table="t_trademark_relation",
            alias="i",
            fields="""
                i.id, i._id, i.eid, i.reg_number, i.type_num,
                i.applicant, i.local_row_update_time,
                i.local_row_update_time AS business_date
            """,
            date_expression="i.local_row_update_time",
            key_expression="i.id",
            **options,
        )

    async def list_copyrights(self, eid: str, **options) -> list[dict]:
        """使用 eid 索引分页查询著作权关系。"""
        return await self._list(
            eid,
            table="t_copyrights_relations",
            alias="i",
            fields="""
                i.id, i.eid, i.obj_id, i.type, i.type_name, i.number,
                i.short_name, i.version,
                COALESCE(i.success_date, i.approval_date) AS business_date,
                i.approval_date, i.name, i.state, i.is_history,
                i.local_row_update_time
            """,
            date_expression="COALESCE(i.success_date, i.approval_date)",
            key_expression="i.id",
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
        """执行仅由固定调用方提供表名和字段的知识产权查询。"""
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
    """为固定知识产权日期表达式构造参数化范围条件。"""
    clauses: list[str] = []
    parameters: dict[str, object] = {}
    if start_date is not None:
        clauses.append(f"AND {expression} >= :start_date")
        parameters["start_date"] = start_date
    if end_date is not None:
        clauses.append(f"AND {expression} <= :end_date")
        parameters["end_date"] = end_date
    return "\n".join(clauses), parameters
