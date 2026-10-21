"""企业尽调结构化 Repository SQL 约束单元测试。"""

from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text

from yuxi.company_due_diligence.models import SortOrder
from yuxi.company_due_diligence.repositories.intellectual_property import IntellectualPropertyRepository
from yuxi.company_due_diligence.repositories.risk import RiskRepository
from yuxi.company_due_diligence.repositories.shareholder import ShareholderRepository, _SHAREHOLDER_FIELDS

pytestmark = pytest.mark.asyncio


@pytest.fixture
def shareholder_repository():
    """在 SQLite 中执行真实仓储 SQL，固定股东数据无需连接 MySQL。"""
    engine = create_engine("sqlite://")
    with engine.connect() as connection:
        columns = [field.strip().removeprefix("p.") for field in _SHAREHOLDER_FIELDS.split(",")]
        connection.execute(text("CREATE TABLE t_partners_alls (" + ",".join(columns) + ")"))
        for row in [
            (1, "0.685", "2000-01-01", 0),
            (2, "12%", "2025-01-01", 0),
            (3, "9%", "2026-01-01", 0),
            (4, "0", "2026-02-01", 0),
            (5, "", "2026-03-01", 0),
            (6, "90%", "2026-04-01", 1),
            (7, None, "2026-05-01", 0),
        ]:
            connection.execute(
                text(
                    "INSERT INTO t_partners_alls (id,eid,stock_percent,start_date,is_history,should_capi_items) "
                    "VALUES (:id,'eid-1',:ratio,:date,:history,'[]')"
                ),
                dict(zip(("id", "ratio", "date", "history"), row)),
            )
        connection.execute(text("UPDATE t_partners_alls SET should_capi_items = NULL WHERE id = 1"))

        async def execute(statement, parameters):
            """把异步仓储调用转发到内存数据库。"""
            return connection.execute(statement, parameters)

        yield ShareholderRepository(SimpleNamespace(execute=execute))
    engine.dispose()


async def test_current_shareholders_numeric_order_and_positive_filter(shareholder_repository):
    """持股排序必须先于分页，排除空值和零并兼容百分号格式。"""
    options = dict(start_date=None, end_date=None, sort_order=SortOrder.DESC)
    first = await shareholder_repository.list_current("eid-1", limit=2, offset=0, **options)
    rest = await shareholder_repository.list_current("eid-1", limit=10, offset=2, **options)
    assert [row["id"] for row in first] == [1, 2]
    assert [row["id"] for row in rest] == [3]


async def test_current_contributions_exclude_historical_shareholders(shareholder_repository):
    """当前出资查询必须过滤历史和零持股，普通出资查询仍保留历史。"""
    options = dict(start_date=None, end_date=None, sort_order=SortOrder.DESC, limit=20, offset=0)
    current = await shareholder_repository.list_contributions("eid-1", current_only=True, **options)
    all_rows = await shareholder_repository.list_contributions("eid-1", **options)
    assert [row["id"] for row in current] == [1, 2, 3]
    assert 6 in [row["id"] for row in all_rows]


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
