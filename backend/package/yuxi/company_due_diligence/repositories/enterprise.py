"""企业身份只读 Repository。"""

from __future__ import annotations

from sqlalchemy import text

from yuxi.company_due_diligence.repositories.base import BaseReadonlyRepository

_COMPANY_FIELDS = """
    e.eid,
    e.credit_no,
    e.name,
    e.format_name,
    e.row_update_time,
    e.local_row_update_time,
    l.stock_code,
    l.stock_abbr,
    l.company_name,
    l.update_time AS listed_update_time
"""


class EnterpriseRepository(BaseReadonlyRepository):
    """查询企业标准身份和受限候选集合。"""

    async def get_profile(self, eid: str) -> dict | None:
        """按唯一 eid 查询企业基本信息和上市标识。"""
        return await self._fetch_one(
            text(
                """
                SELECT
                    e.eid,
                    e.credit_no,
                    e.name,
                    e.format_name,
                    e.regist_capi,
                    e.regist_capi_new,
                    e.currency_unit,
                    e.actual_capi,
                    e.oper_name,
                    e.status,
                    e.start_date,
                    e.scope,
                    e.province_code,
                    e.district_code,
                    e.row_update_time,
                    e.local_row_update_time,
                    l.stock_code,
                    l.stock_abbr,
                    l.company_name,
                    l.update_time AS listed_update_time
                FROM t_enterprise e
                LEFT JOIN listed_company_gd l ON l.eid = e.eid
                WHERE e.eid = :eid
                LIMIT 1
                """
            ),
            {"eid": eid},
        )

    async def find_by_eid(self, eid: str) -> list[dict]:
        """按内部企业标识精确查询。"""
        return await self._fetch_all(
            text(
                f"""
                SELECT {_COMPANY_FIELDS}
                FROM t_enterprise e
                JOIN listed_company_gd l ON l.eid = e.eid
                WHERE e.eid = :eid
                LIMIT 2
                """
            ),
            {"eid": eid},
        )

    async def find_by_credit_code(self, credit_code: str) -> list[dict]:
        """按统一社会信用代码精确查询。"""
        return await self._fetch_all(
            text(
                f"""
                SELECT {_COMPANY_FIELDS}
                FROM t_enterprise e
                JOIN listed_company_gd l ON l.eid = e.eid
                WHERE e.credit_no = :credit_code OR l.code = :credit_code
                ORDER BY e.eid
                LIMIT 2
                """
            ),
            {"credit_code": credit_code},
        )

    async def find_by_stock_code(self, stock_code: str) -> list[dict]:
        """按完整或不含交易所后缀的股票代码精确查询。"""
        if "." in stock_code:
            condition = "UPPER(l.stock_code) = :stock_code"
        else:
            condition = "UPPER(SUBSTRING_INDEX(l.stock_code, '.', 1)) = :stock_code"

        return await self._fetch_all(
            text(
                f"""
                SELECT {_COMPANY_FIELDS}
                FROM t_enterprise e
                JOIN listed_company_gd l ON l.eid = e.eid
                WHERE {condition}
                ORDER BY l.stock_code
                LIMIT 20
                """
            ),
            {"stock_code": stock_code},
        )

    async def find_by_exact_name(self, name: str) -> list[dict]:
        """按企业标准全称精确查询。"""
        return await self._fetch_all(
            text(
                f"""
                SELECT {_COMPANY_FIELDS}
                FROM t_enterprise e
                JOIN listed_company_gd l ON l.eid = e.eid
                WHERE e.name = :name OR l.company_name = :name
                ORDER BY e.eid
                LIMIT 20
                """
            ),
            {"name": name},
        )

    async def find_by_normalized_name(self, normalized_name: str) -> list[dict]:
        """按移除空白和标点后的完整企业名称查询。"""
        return await self._fetch_all(
            text(
                f"""
                SELECT {_COMPANY_FIELDS}
                FROM t_enterprise e
                JOIN listed_company_gd l ON l.eid = e.eid
                WHERE REGEXP_REPLACE(LOWER(e.name), '[[:space:][:punct:]]', '') = :normalized_name
                ORDER BY e.eid
                LIMIT 20
                """
            ),
            {"normalized_name": normalized_name},
        )

    async def search_candidates(self, query: str, limit: int) -> list[dict]:
        """按简称、名称片段或股票代码返回有限候选。"""
        return await self._fetch_all(
            text(
                f"""
                SELECT {_COMPANY_FIELDS}
                FROM t_enterprise e
                JOIN listed_company_gd l ON l.eid = e.eid
                WHERE LOCATE(:query, e.name) > 0
                   OR LOCATE(:query, l.stock_abbr) > 0
                   OR LOCATE(UPPER(:query), UPPER(l.stock_code)) > 0
                ORDER BY
                    CASE
                        WHEN l.stock_abbr = :query THEN 0
                        WHEN e.name = :query THEN 1
                        WHEN e.name LIKE CONCAT(:query, '%') THEN 2
                        ELSE 3
                    END,
                    CHAR_LENGTH(e.name),
                    e.eid
                LIMIT :limit
                """
            ),
            {"query": query, "limit": limit},
        )
