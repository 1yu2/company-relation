"""企业股东与出资信息只读 Repository。"""

from __future__ import annotations

from datetime import date

from sqlalchemy import text

from yuxi.company_due_diligence.models import SortOrder
from yuxi.company_due_diligence.repositories.base import BaseReadonlyRepository

_SHAREHOLDER_FIELDS = """
    p.id,
    p.eid,
    p.stock_name,
    p.stock_id,
    p.stock_type,
    p.stock_percent,
    p.total_real_capi_new,
    p.real_currency_unit,
    p.total_should_capi_new,
    p.should_currency_unit,
    p.should_capi_items,
    p.real_capi_items,
    p.start_date,
    p.end_date,
    p.is_history,
    p.row_update_time,
    p.local_row_update_time
"""


class ShareholderRepository(BaseReadonlyRepository):
    """按企业查询当前、历史股东及出资明细。"""

    async def list_current(
        self,
        eid: str,
        *,
        start_date: date | None,
        end_date: date | None,
        limit: int,
        offset: int,
        sort_order: SortOrder,
    ) -> list[dict]:
        """分页查询当前股东，日期范围按股东起始日期过滤。"""
        return await self._list_by_history(
            eid,
            is_history=False,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
            offset=offset,
            sort_order=sort_order,
        )

    async def list_historical(
        self,
        eid: str,
        *,
        start_date: date | None,
        end_date: date | None,
        limit: int,
        offset: int,
        sort_order: SortOrder,
    ) -> list[dict]:
        """分页查询历史股东，日期范围按股东起始日期过滤。"""
        return await self._list_by_history(
            eid,
            is_history=True,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
            offset=offset,
            sort_order=sort_order,
        )

    async def list_contributions(
        self,
        eid: str,
        *,
        start_date: date | None,
        end_date: date | None,
        limit: int,
        offset: int,
        sort_order: SortOrder,
    ) -> list[dict]:
        """分页查询带认缴或实缴 JSON 明细的股东记录。"""
        filters, parameters = _date_filters(start_date, end_date)
        direction = sort_order.value.upper()
        parameters.update({"eid": eid, "limit": limit, "offset": offset})
        return await self._fetch_all(
            text(
                f"""
                SELECT {_SHAREHOLDER_FIELDS}
                FROM t_partners_alls p
                WHERE p.eid = :eid
                  AND (p.should_capi_items IS NOT NULL OR p.real_capi_items IS NOT NULL)
                  {filters}
                ORDER BY p.start_date {direction}, p.id {direction}
                LIMIT :limit OFFSET :offset
                """
            ),
            parameters,
        )

    async def _list_by_history(
        self,
        eid: str,
        *,
        is_history: bool,
        start_date: date | None,
        end_date: date | None,
        limit: int,
        offset: int,
        sort_order: SortOrder,
    ) -> list[dict]:
        """按历史标记执行共享的受限股东查询。"""
        filters, parameters = _date_filters(start_date, end_date)
        direction = sort_order.value.upper()
        parameters.update(
            {
                "eid": eid,
                "is_history": int(is_history),
                "limit": limit,
                "offset": offset,
            }
        )
        return await self._fetch_all(
            text(
                f"""
                SELECT {_SHAREHOLDER_FIELDS}
                FROM t_partners_alls p
                WHERE p.eid = :eid
                  AND p.is_history = :is_history
                  {filters}
                ORDER BY p.start_date {direction}, p.id {direction}
                LIMIT :limit OFFSET :offset
                """
            ),
            parameters,
        )


def _date_filters(start_date: date | None, end_date: date | None) -> tuple[str, dict]:
    """为 ISO 日期文本字段构造参数化范围条件。"""
    clauses: list[str] = []
    parameters: dict[str, object] = {}
    if start_date is not None:
        clauses.append("AND NULLIF(p.start_date, '') >= :start_date")
        parameters["start_date"] = start_date.isoformat()
    if end_date is not None:
        clauses.append("AND NULLIF(p.start_date, '') <= :end_date")
        parameters["end_date"] = end_date.isoformat()
    return "\n".join(clauses), parameters
