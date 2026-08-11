"""企业身份解析服务单元测试。"""

from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest

from yuxi.company_due_diligence.entity_resolution import EntityResolutionService, normalize_company_name
from yuxi.company_due_diligence.models import CompanyReference, ConfidenceLevel, MatchMethod, ResolutionStatus
from yuxi.company_due_diligence.mysql import MySQLUnavailableError

pytestmark = pytest.mark.asyncio


def _company_row(eid: str = "eid-1", name: str = "示例股份有限公司") -> dict:
    """生成不含真实企业明细的身份行。"""
    now = datetime(2026, 8, 10, tzinfo=UTC)
    return {
        "eid": eid,
        "credit_no": f"credit-{eid}",
        "name": name,
        "format_name": name,
        "row_update_time": now,
        "local_row_update_time": now,
        "stock_code": "000001.SZ",
        "stock_abbr": "示例股份",
        "company_name": name,
        "listed_update_time": now,
    }


class FakeMySQL:
    """提供不访问真实数据库的异步连接上下文。"""

    @asynccontextmanager
    async def connection(self):
        """返回占位连接。"""
        yield object()


class UnavailableMySQL:
    """模拟外部事实主库不可用。"""

    @asynccontextmanager
    async def connection(self):
        """在签出连接时显式失败。"""
        raise MySQLUnavailableError("unavailable")
        yield


class FakeRepository:
    """记录解析优先级并返回预设企业行。"""

    def __init__(self, rows_by_method: dict[str, list[dict]] | None = None):
        self.rows_by_method = rows_by_method or {}
        self.calls = []

    async def find_by_eid(self, value):
        """返回 eid 查询结果。"""
        self.calls.append(("eid", value))
        return self.rows_by_method.get("eid", [])

    async def find_by_credit_code(self, value):
        """返回信用代码查询结果。"""
        self.calls.append(("credit_code", value))
        return self.rows_by_method.get("credit_code", [])

    async def find_by_stock_code(self, value):
        """返回股票代码查询结果。"""
        self.calls.append(("stock_code", value))
        return self.rows_by_method.get("stock_code", [])

    async def find_by_exact_name(self, value):
        """返回标准全称查询结果。"""
        self.calls.append(("exact_name", value))
        return self.rows_by_method.get("exact_name", [])

    async def find_by_normalized_name(self, value):
        """返回规范化名称查询结果。"""
        self.calls.append(("normalized_name", value))
        return self.rows_by_method.get("normalized_name", [])

    async def search_candidates(self, value, limit):
        """返回简称或模糊名称候选。"""
        self.calls.append(("search", value, limit))
        return self.rows_by_method.get("search", [])[:limit]


def _service(repository: FakeRepository, mysql=None) -> EntityResolutionService:
    """创建注入假 Repository 的解析服务。"""
    return EntityResolutionService(
        mysql=mysql or FakeMySQL(),
        repository_factory=lambda _connection: repository,
    )


async def test_eid_has_priority_over_other_identifiers():
    """同时提供多个字段时必须只使用最高优先级 eid。"""
    repository = FakeRepository({"eid": [_company_row()]})

    result = await _service(repository).resolve(
        CompanyReference(eid="EID-1", credit_code="credit-other", name="其他企业")
    )

    assert result.status == ResolutionStatus.RESOLVED
    assert result.company.match_method == MatchMethod.EID
    assert repository.calls == [("eid", "eid-1")]


async def test_normalized_full_name_resolves_without_changing_display_name():
    """规范化匹配应保留数据库中的标准展示名称。"""
    row = _company_row()
    repository = FakeRepository({"normalized_name": [row]})

    result = await _service(repository).resolve(CompanyReference(name="示例 股份（有限公司）"))

    assert result.status == ResolutionStatus.RESOLVED
    assert result.company.name == row["name"]
    assert result.company.match_method == MatchMethod.NORMALIZED_NAME
    assert repository.calls[-1] == ("normalized_name", normalize_company_name("示例 股份（有限公司）"))


async def test_abbreviation_returns_candidates_without_auto_selection():
    """简称命中只能返回低置信度候选。"""
    repository = FakeRepository({"search": [_company_row()]})

    result = await _service(repository).resolve(CompanyReference(name="示例股份"))

    assert result.status == ResolutionStatus.AMBIGUOUS
    assert result.company is None
    assert result.candidates[0].match_method == MatchMethod.ABBREVIATION
    assert result.candidates[0].confidence == ConfidenceLevel.LOW


async def test_no_match_and_source_failure_have_different_statuses():
    """真实无匹配与数据源故障必须使用不同状态。"""
    not_found = await _service(FakeRepository()).resolve(CompanyReference(eid="missing"))
    unavailable = await _service(FakeRepository(), mysql=UnavailableMySQL()).resolve(CompanyReference(eid="eid-1"))

    assert not_found.status == ResolutionStatus.NOT_FOUND
    assert not_found.data_quality.source_status == "available"
    assert unavailable.status == ResolutionStatus.SOURCE_UNAVAILABLE
    assert unavailable.data_quality.source_status == "unavailable"


async def test_multiple_exact_rows_remain_ambiguous():
    """即使是精确名称，多行结果也不得静默选择第一条。"""
    repository = FakeRepository(
        {"exact_name": [_company_row("eid-1"), _company_row("eid-2")]},
    )

    result = await _service(repository).resolve(CompanyReference(name="示例股份有限公司"))

    assert result.status == ResolutionStatus.AMBIGUOUS
    assert result.company is None
    assert [candidate.eid for candidate in result.candidates] == ["eid-1", "eid-2"]
