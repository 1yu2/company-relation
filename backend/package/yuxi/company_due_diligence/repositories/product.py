"""企业产品信息只读 Repository。"""

from __future__ import annotations

from datetime import date

from sqlalchemy import text

from yuxi.company_due_diligence.models import SortOrder
from yuxi.company_due_diligence.repositories.base import BaseReadonlyRepository


class ProductRepository(BaseReadonlyRepository):
    """按企业查询产品和线上服务记录。"""

    async def list_products(
        self,
        eid: str,
        *,
        start_date: date | None,
        end_date: date | None,
        limit: int,
        offset: int,
        sort_order: SortOrder,
    ) -> list[dict]:
        """分页查询产品，日期范围按产品创建或本地更新时间过滤。"""
        filters, parameters = _date_filters(start_date, end_date)
        direction = sort_order.value.upper()
        parameters.update({"eid": eid, "limit": limit, "offset": offset})
        return await self._fetch_all(
            text(
                f"""
                SELECT
                    p.id,
                    p.proj_id,
                    p.eid,
                    p.ename,
                    p.pro_name,
                    p.kind,
                    p.description,
                    p.domain,
                    p.links,
                    p.create_time,
                    p.local_update_time,
                    p.local_row_update_time
                FROM t_company_products p
                WHERE p.eid = :eid
                  {filters}
                ORDER BY COALESCE(p.create_time, p.local_update_time, p.local_row_update_time) {direction},
                         p.id {direction}
                LIMIT :limit OFFSET :offset
                """
            ),
            parameters,
        )


def _date_filters(start_date: date | None, end_date: date | None) -> tuple[str, dict]:
    """构造产品技术日期的参数化范围条件。"""
    expression = "DATE(COALESCE(p.create_time, p.local_update_time, p.local_row_update_time))"
    clauses: list[str] = []
    parameters: dict[str, object] = {}
    if start_date is not None:
        clauses.append(f"AND {expression} >= :start_date")
        parameters["start_date"] = start_date
    if end_date is not None:
        clauses.append(f"AND {expression} <= :end_date")
        parameters["end_date"] = end_date
    return "\n".join(clauses), parameters
