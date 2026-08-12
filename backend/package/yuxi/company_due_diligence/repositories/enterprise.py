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

    async def list_catalog(self, limit: int, offset: int = 0) -> list[dict]:
        """按稳定顺序枚举企业数据目录中的身份记录。"""
        return await self._fetch_all(
            text(
                """
                SELECT e.eid, e.name, e.credit_no, e.row_update_time, e.local_row_update_time
                FROM t_enterprise e
                ORDER BY e.eid
                LIMIT :limit OFFSET :offset
                """
            ),
            {"limit": limit, "offset": offset},
        )

    async def get_catalog_coverage(self) -> dict:
        """返回固定数据域的企业覆盖、记录总数和更新时间摘要。"""
        return (
            await self._fetch_one(
                text(
                    """
                SELECT
                    COUNT(*) AS enterprise_count,
                    SUM(
                        CASE WHEN e.name IS NOT NULL AND e.name <> '' THEN 1 ELSE 0 END
                    ) AS named_enterprise_count,
                    SUM(
                        CASE WHEN e.credit_no IS NOT NULL AND e.credit_no <> '' THEN 1 ELSE 0 END
                    ) AS credit_code_company_count,
                    SUM(
                        CASE WHEN e.regist_capi IS NOT NULL AND e.regist_capi <> '' THEN 1 ELSE 0 END
                    ) AS registered_capital_company_count,
                    SUM(
                        CASE WHEN e.oper_name IS NOT NULL AND e.oper_name <> '' THEN 1 ELSE 0 END
                    ) AS legal_representative_company_count,
                    SUM(
                        CASE WHEN e.status IS NOT NULL AND e.status <> '' THEN 1 ELSE 0 END
                    ) AS business_status_company_count,
                    SUM(
                        CASE WHEN EXISTS (
                            SELECT 1 FROM t_partners_alls p WHERE p.eid = e.eid
                        ) THEN 1 ELSE 0 END
                    ) AS shareholders_company_count,
                    SUM(
                        CASE WHEN EXISTS (
                            SELECT 1 FROM t_company_products p WHERE p.eid = e.eid
                        ) THEN 1 ELSE 0 END
                    ) AS products_company_count,
                    SUM(
                        CASE WHEN EXISTS (
                            SELECT 1 FROM t_company_finance f WHERE f.eid = e.eid
                        ) THEN 1 ELSE 0 END
                    ) AS financing_company_count,
                    SUM(
                        CASE WHEN EXISTS (
                            SELECT 1 FROM t_administrative_punishment r WHERE r.eid = e.eid
                        ) THEN 1 ELSE 0 END
                    ) AS risks_company_count,
                    SUM(
                        CASE WHEN EXISTS (
                            SELECT 1 FROM t_license_info l WHERE l.eid = e.eid
                        ) THEN 1 ELSE 0 END
                    ) AS licenses_company_count,
                    SUM(
                        CASE WHEN EXISTS (
                            SELECT 1 FROM t_patents_relations_info p WHERE p.eid = e.eid
                        ) THEN 1 ELSE 0 END
                    ) AS intellectual_property_company_count,
                    SUM(
                        CASE WHEN EXISTS (
                            SELECT 1 FROM listed_company_gd l WHERE l.eid = e.eid
                        ) THEN 1 ELSE 0 END
                    ) AS listed_company_count,
                    (SELECT COUNT(*) FROM t_partners_alls) AS shareholders_record_count,
                    (SELECT COUNT(*) FROM t_company_products) AS products_record_count,
                    (SELECT COUNT(*) FROM t_company_finance) AS financing_record_count,
                    (SELECT COUNT(*) FROM t_administrative_punishment) AS risks_record_count,
                    (SELECT COUNT(*) FROM t_license_info) AS licenses_record_count,
                    (SELECT COUNT(*) FROM t_patents_relations_info) AS intellectual_property_record_count,
                    MAX(e.local_row_update_time) AS latest_update_time
                FROM t_enterprise e
                    """
                )
            )
            or {}
        )

    async def list_shared_shareholders(self, limit: int) -> list[dict]:
        """返回由当前股东记录推导出的共同股东企业集合。"""
        return await self._fetch_all(
            text(
                """
                SELECT
                    COALESCE(NULLIF(p.stock_id, ''), p.stock_name) AS shareholder_key,
                    MAX(p.stock_name) AS shareholder_name,
                    COUNT(DISTINCT p.eid) AS enterprise_count,
                    GROUP_CONCAT(DISTINCT p.eid ORDER BY p.eid SEPARATOR ',') AS enterprise_eids,
                    GROUP_CONCAT(DISTINCT e.name ORDER BY e.name SEPARATOR '、') AS enterprise_names,
                    GROUP_CONCAT(DISTINCT CAST(p.id AS CHAR) ORDER BY p.id SEPARATOR ',') AS source_record_keys
                FROM t_partners_alls p
                JOIN t_enterprise e ON e.eid = p.eid
                WHERE COALESCE(NULLIF(p.stock_id, ''), p.stock_name) IS NOT NULL
                  AND p.is_history = 0
                GROUP BY COALESCE(NULLIF(p.stock_id, ''), p.stock_name)
                HAVING COUNT(DISTINCT p.eid) > 1
                ORDER BY enterprise_count DESC, shareholder_name
                LIMIT :limit
                """
            ),
            {"limit": limit},
        )
