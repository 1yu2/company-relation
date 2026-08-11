"""企业尽调结构化 Repository SQL 约束单元测试。"""

from datetime import date

import pytest

from yuxi.company_due_diligence.models import SortOrder
from yuxi.company_due_diligence.repositories.intellectual_property import IntellectualPropertyRepository
from yuxi.company_due_diligence.repositories.risk import RiskRepository

pytestmark = pytest.mark.asyncio


class EmptyMappings:
    """模拟空 SQLAlchemy 映射结果。"""

    def mappings(self):
        """返回可迭代空映射。"""
        return []


class RecordingConnection:
    """记录 Repository 生成的 SQL 和绑定参数。"""

    def __init__(self):
        self.statement = None
        self.parameters = None

    async def execute(self, statement, parameters):
        """保存只读语句并返回空结果。"""
        self.statement = statement
        self.parameters = parameters
        return EmptyMappings()


async def test_patent_query_uses_eid_date_range_and_limit():
    """百万级专利关系查询必须由 eid、日期和 LIMIT 共同约束。"""
    connection = RecordingConnection()
    repository = IntellectualPropertyRepository(connection)

    await repository.list_patents(
        "eid-1",
        start_date=date(2020, 1, 1),
        end_date=date(2025, 12, 31),
        limit=21,
        offset=20,
        sort_order=SortOrder.DESC,
    )

    sql = connection.statement.text
    assert "WHERE i.eid = :eid" in sql
    assert "COALESCE(i.authorize_date, i.request_date) >= :start_date" in sql
    assert "LIMIT :limit OFFSET :offset" in sql
    assert connection.parameters == {
        "start_date": date(2020, 1, 1),
        "end_date": date(2025, 12, 31),
        "eid": "eid-1",
        "limit": 21,
        "offset": 20,
    }


async def test_risk_query_does_not_select_penalty_full_text():
    """结构化行政处罚查询不得加载留给证据索引的长正文。"""
    connection = RecordingConnection()
    repository = RiskRepository(connection)

    await repository.list_administrative_penalties(
        "eid-1",
        start_date=None,
        end_date=None,
        limit=20,
        offset=0,
        sort_order=SortOrder.DESC,
    )

    sql = connection.statement.text
    assert "punishment_text" not in sql
    assert "based_on" not in sql
    assert "WHERE r.eid = :eid" in sql
    assert "LIMIT :limit OFFSET :offset" in sql
