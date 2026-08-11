"""企业融资历史只读 Repository。"""

from __future__ import annotations

from datetime import date

from sqlalchemy import text

from yuxi.company_due_diligence.models import SortOrder
from yuxi.company_due_diligence.repositories.base import BaseReadonlyRepository


class FinanceRepository(BaseReadonlyRepository):
    """按企业查询结构化融资事件和投资人 JSON。"""

    async def list_financing_history(
        self,
        eid: str,
        *,
        start_date: date | None,
        end_date: date | None,
        limit: int,
        offset: int,
        sort_order: SortOrder,
    ) -> list[dict]:
        """分页查询融资历史，日期范围按融资轮次日期过滤。"""
        filters, parameters = _date_filters(start_date, end_date)
        direction = sort_order.value.upper()
        parameters.update({"eid": eid, "limit": limit, "offset": offset})
        return await self._fetch_all(
            text(
                f"""
                SELECT
                    f.id,
                    f.eid,
                    f.ename,
                    f.round_date,
                    f.round,
                    f.round_type,
                    f.amount,
                    f.estimated_amount,
                    f.pre_money,
                    f.post_money,
                    f.currency,
                    f.precise,
                    f.investors,
                    f.investors_json,
                    f.publish_date,
                    f.newstitle,
                    f.newslink,
                    f.create_time,
                    f.local_update_time,
                    f.local_row_update_time
                FROM t_company_finance f
                WHERE f.eid = :eid
                  {filters}
                ORDER BY f.round_date {direction}, f.id {direction}
                LIMIT :limit OFFSET :offset
                """
            ),
            parameters,
        )


def _date_filters(start_date: date | None, end_date: date | None) -> tuple[str, dict]:
    """构造融资业务日期的参数化范围条件。"""
    clauses: list[str] = []
    parameters: dict[str, object] = {}
    if start_date is not None:
        clauses.append("AND f.round_date >= :start_date")
        parameters["start_date"] = start_date
    if end_date is not None:
        clauses.append("AND f.round_date <= :end_date")
        parameters["end_date"] = end_date
    return "\n".join(clauses), parameters
