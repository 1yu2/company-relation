"""企业尽调结构化查询真实 MySQL 集成测试。"""

from dataclasses import dataclass

import pytest
from sqlalchemy import text

from yuxi.company_due_diligence.models import StructuredQueryRequest, StructuredQueryType
from yuxi.company_due_diligence.mysql import CompanyDueDiligenceMySQL
from yuxi.company_due_diligence.structured_query_service import StructuredQueryService

pytestmark = pytest.mark.asyncio


@dataclass(frozen=True)
class DomainCase:
    """描述一个真实数据域的表、过滤条件和来源表。"""

    query_type: StructuredQueryType
    table: str
    condition: str = "1 = 1"


DOMAIN_CASES = (
    DomainCase(StructuredQueryType.CURRENT_SHAREHOLDERS, "t_partners_alls", "is_history = 0"),
    DomainCase(StructuredQueryType.HISTORICAL_SHAREHOLDERS, "t_partners_alls", "is_history = 1"),
    DomainCase(StructuredQueryType.PRODUCTS, "t_company_products"),
    DomainCase(StructuredQueryType.FINANCING_HISTORY, "t_company_finance"),
    DomainCase(StructuredQueryType.ADMINISTRATIVE_PENALTIES, "t_administrative_punishment"),
    DomainCase(StructuredQueryType.ENVIRONMENTAL_PENALTIES, "t_huanbaochufas"),
    DomainCase(StructuredQueryType.ABNORMAL_OPERATIONS, "t_abnormal_enterprises"),
    DomainCase(StructuredQueryType.LICENSES, "t_license_info"),
    DomainCase(StructuredQueryType.PATENT_SUMMARY, "t_patents_relations_info"),
    DomainCase(StructuredQueryType.TRADEMARK_SUMMARY, "t_trademark_relation"),
    DomainCase(StructuredQueryType.COPYRIGHT_SUMMARY, "t_copyrights_relations"),
)


@pytest.mark.parametrize("case", DOMAIN_CASES, ids=lambda case: case.query_type.value)
async def test_real_domain_query_matches_source_count_and_record_keys(case: DomainCase):
    """每个真实数据域应返回受限行数、来源记录键和空结果企业。"""
    mysql = CompanyDueDiligenceMySQL()
    service = StructuredQueryService(mysql=mysql)
    try:
        async with mysql.connection() as connection:
            has_data = await connection.execute(
                text(
                    f"""
                    SELECT d.eid, COUNT(*) AS row_count
                    FROM {case.table} d
                    JOIN t_enterprise e ON e.eid = d.eid
                    WHERE {case.condition}
                    GROUP BY d.eid
                    ORDER BY d.eid
                    LIMIT 1
                    """
                )
            )
            has_data_row = dict(has_data.mappings().one())
            no_data = await connection.execute(
                text(
                    f"""
                    SELECT e.eid
                    FROM t_enterprise e
                    WHERE NOT EXISTS (
                        SELECT 1
                        FROM {case.table} d
                        WHERE d.eid = e.eid AND {case.condition}
                    )
                    ORDER BY e.eid
                    LIMIT 1
                    """
                )
            )
            no_data_eid = no_data.scalar_one_or_none()

        result = await service.query(
            StructuredQueryRequest(
                eid=has_data_row["eid"],
                query_type=case.query_type,
                page_size=3,
            )
        )
        domain_sources = [source for source in result.sources if source.table == case.table]

        assert result.pagination.returned == min(has_data_row["row_count"], 3)
        assert len(domain_sources) == result.pagination.returned
        assert all(source.record_key for source in domain_sources)
        assert result.pagination.has_more is (has_data_row["row_count"] > 3)

        if no_data_eid is not None:
            empty_result = await service.query(
                StructuredQueryRequest(eid=no_data_eid, query_type=case.query_type, page_size=3)
            )
            assert empty_result.pagination.returned == 0
            assert not [source for source in empty_result.sources if source.table == case.table]
    finally:
        await mysql.close()


async def test_real_contribution_and_financing_json_are_fully_mapped():
    """真实出资和融资 JSON 的元素数量应与映射事实或关系一致。"""
    mysql = CompanyDueDiligenceMySQL()
    service = StructuredQueryService(mysql=mysql)
    try:
        async with mysql.connection() as connection:
            contribution_row = await connection.execute(
                text(
                    """
                    SELECT eid, id, JSON_LENGTH(should_capi_items) AS subscribed_count,
                           JSON_LENGTH(real_capi_items) AS paid_count
                    FROM t_partners_alls
                    WHERE JSON_VALID(should_capi_items)
                      AND JSON_VALID(real_capi_items)
                      AND JSON_LENGTH(should_capi_items) + JSON_LENGTH(real_capi_items) > 0
                    ORDER BY id
                    LIMIT 1
                    """
                )
            )
            contribution = dict(contribution_row.mappings().one())
            finance_row = await connection.execute(
                text(
                    """
                    SELECT eid, id, JSON_LENGTH(investors_json) AS investor_count
                    FROM t_company_finance
                    WHERE JSON_VALID(investors_json)
                      AND JSON_LENGTH(investors_json) > 0
                    ORDER BY id
                    LIMIT 1
                    """
                )
            )
            financing = dict(finance_row.mappings().one())

        contribution_result = await service.query(
            StructuredQueryRequest(
                eid=contribution["eid"],
                query_type=StructuredQueryType.SHAREHOLDER_CONTRIBUTIONS,
                page_size=100,
            )
        )
        contribution_source_id = next(
            source.source_id
            for source in contribution_result.sources
            if source.table == "t_partners_alls" and source.record_key == str(contribution["id"])
        )
        contribution_facts = [
            fact
            for fact in contribution_result.facts
            if fact.source_id == contribution_source_id
            and fact.fact_type in {"subscribed_contribution", "paid_contribution"}
        ]
        assert len(contribution_facts) == contribution["subscribed_count"] + contribution["paid_count"]

        financing_result = await service.query(
            StructuredQueryRequest(
                eid=financing["eid"],
                query_type=StructuredQueryType.FINANCING_HISTORY,
                page_size=100,
            )
        )
        financing_source_id = next(
            source.source_id
            for source in financing_result.sources
            if source.table == "t_company_finance" and source.record_key == str(financing["id"])
        )
        investor_relations = [
            relation
            for relation in financing_result.relations
            if relation.source_ids == [financing_source_id] and relation.relation_type == "financed_by"
        ]
        assert len(investor_relations) == financing["investor_count"]
        assert all("investor_type" in relation.properties for relation in investor_relations)
    finally:
        await mysql.close()


async def test_real_financial_query_and_pool_return():
    """真实财务查询应返回来源或覆盖警告，并在批量查询后归还连接。"""
    mysql = CompanyDueDiligenceMySQL()
    service = StructuredQueryService(mysql=mysql)
    try:
        async with mysql.connection() as connection:
            result = await connection.execute(text("SELECT eid FROM company_indicator ORDER BY eid LIMIT 1"))
            eid = result.scalar_one()

        financial = await service.query(
            StructuredQueryRequest(
                eid=eid,
                query_type=StructuredQueryType.FINANCIAL_METRIC_SERIES,
                page_size=5,
            )
        )
        missing_metric = await service.query(
            StructuredQueryRequest(
                eid=eid,
                query_type=StructuredQueryType.FINANCIAL_METRIC_SERIES,
                indicator_name="__integration_missing_metric__",
                page_size=5,
            )
        )
        health = await mysql.health()

        assert financial.pagination.returned > 0
        assert {source.table for source in financial.sources} & {
            "company_indicator",
            "wind_listed_company_metrics",
        }
        assert missing_metric.pagination.returned == 0
        assert any("不能解释为企业没有财务数据" in warning for warning in missing_metric.data_quality.warnings)
        assert health.pool.checked_out == 0
    finally:
        await mysql.close()


async def test_patent_explain_uses_an_indexed_eid_lookup():
    """专利大表查询计划应从 eid 条件进入索引而非全表扫描。"""
    mysql = CompanyDueDiligenceMySQL()
    try:
        async with mysql.connection() as connection:
            eid_result = await connection.execute(
                text("SELECT eid FROM t_patents_relations_info WHERE eid IS NOT NULL LIMIT 1")
            )
            eid = eid_result.scalar_one()
            explain = await connection.execute(
                text(
                    """
                    EXPLAIN
                    SELECT id
                    FROM t_patents_relations_info
                    WHERE eid = :eid
                    ORDER BY COALESCE(authorize_date, request_date) DESC, id DESC
                    LIMIT 21
                    """
                ),
                {"eid": eid},
            )
            plan = dict(explain.mappings().one())

        assert plan["key"] is not None
        assert plan["type"] in {"const", "ref", "range"}
    finally:
        await mysql.close()
