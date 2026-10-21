"""企业尽调结构化查询服务单元测试。"""

from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest

from yuxi.company_due_diligence.models import SourceStatus, StructuredQueryRequest, StructuredQueryType
from yuxi.company_due_diligence.structured_query_service import StructuredQueryService

pytestmark = pytest.mark.asyncio


def _profile_row() -> dict:
    """生成不包含真实企业数据的基本信息行。"""
    updated_at = datetime(2026, 8, 10, tzinfo=UTC)
    return {
        "eid": "eid-1",
        "credit_no": "credit-1",
        "name": "示例股份有限公司",
        "format_name": "示例股份有限公司",
        "regist_capi": "1,200.5000",
        "regist_capi_new": "1200.5000",
        "currency_unit": "CNY",
        "actual_capi": "800.00",
        "oper_name": "示例代表",
        "status": "存续",
        "start_date": "2020-01-02",
        "scope": "软件服务",
        "province_code": "44",
        "district_code": "4403",
        "row_update_time": updated_at,
        "local_row_update_time": updated_at,
        "stock_code": "000001.SZ",
        "stock_abbr": "示例股份",
        "company_name": "示例股份有限公司",
        "listed_update_time": updated_at,
    }


class FakeMySQL:
    """提供不访问数据库的异步连接上下文。"""

    @asynccontextmanager
    async def connection(self):
        """返回供假 Repository 工厂忽略的占位连接。"""
        yield object()


class FakeRepositories:
    """按查询类型返回预设行并记录分页参数。"""

    def __init__(self, rows: list[dict] | None = None, coverage: dict | None = None):
        self.rows = rows or []
        self.coverage = coverage or {"company_indicator_count": 1, "wind_metric_count": 1}
        self.calls: list[tuple[str, dict]] = []
        self.enterprise = self
        self.shareholder = self
        self.product = self
        self.finance = self
        self.risk = self
        self.intellectual_property = self
        self.financial_metric = self

    async def get_profile(self, eid: str):
        """返回固定企业基本信息。"""
        assert eid == "eid-1"
        return _profile_row()

    async def list_current(self, eid: str, **options):
        """返回当前股东行。"""
        return self._record("current", eid, options)

    async def list_historical(self, eid: str, **options):
        """返回历史股东行。"""
        return self._record("historical", eid, options)

    async def list_contributions(self, eid: str, **options):
        """返回股东出资行。"""
        return self._record("contributions", eid, options)

    async def list_financing_history(self, eid: str, **options):
        """返回融资历史行。"""
        return self._record("financing", eid, options)

    async def list_series(self, eid: str, **options):
        """返回财务指标行。"""
        return self._record("financial", eid, options)

    async def get_coverage(self, eid: str, credit_code: str | None, indicator_name: str | None):
        """返回固定财务覆盖统计。"""
        assert eid == "eid-1"
        assert credit_code == "credit-1"
        assert indicator_name is None
        return self.coverage

    def _record(self, name: str, eid: str, options: dict) -> list[dict]:
        """记录领域调用并返回预设行。"""
        assert eid == "eid-1"
        self.calls.append((name, options))
        return self.rows


def _service(repository: FakeRepositories) -> StructuredQueryService:
    """创建注入假 Repository 的结构化查询服务。"""
    return StructuredQueryService(mysql=FakeMySQL(), repository_factory=lambda _connection: repository)


async def test_profile_query_normalizes_values_and_keeps_stable_sources():
    """企业基本信息应规范化金额并保持稳定来源标识。"""
    repository = FakeRepositories()
    request = StructuredQueryRequest(eid=" eid-1 ", query_type=StructuredQueryType.ENTERPRISE_PROFILE)

    first = await _service(repository).query(request)
    second = await _service(repository).query(request)

    facts = {fact.fact_type: fact for fact in first.facts}
    assert request.eid == "eid-1"
    assert facts["registered_capital"].normalized_value == "1200.5"
    assert facts["actual_capital"].normalized_value == "800"
    assert facts["established_date"].normalized_value == "2020-01-02"
    assert first.sources[0].source_id == second.sources[0].source_id
    assert first.pagination.returned == 1


async def test_registered_capital_uses_source_amount_unit(monkeypatch):
    """注册资本按源文案换算为元；实收资本是不带单位的数值，与注册资本同单位（万元），同样换算。"""
    repository = FakeRepositories()

    async def get_profile(_eid):
        """返回带万元单位的固定源行。"""
        return {**_profile_row(), "regist_capi": "30299.5606万元人民币", "regist_capi_new": "30299.5606"}

    monkeypatch.setattr(repository, "get_profile", get_profile)
    result = await _service(repository).query(
        StructuredQueryRequest(eid="eid-1", query_type=StructuredQueryType.REGISTERED_CAPITAL)
    )
    facts = {fact.fact_type: fact for fact in result.facts}
    assert facts["registered_capital"].normalized_value == "302995606"
    assert facts["registered_capital"].unit == "CNY"
    assert facts["actual_capital"].normalized_value == "8000000"
    assert facts["actual_capital"].unit == "CNY"


async def test_shareholder_query_distinguishes_history_and_truncation():
    """当前和历史股东应使用不同关系类型并用 limit+1 判断截断。"""
    rows = [_shareholder_row(1, False), _shareholder_row(2, False)]
    current_repository = FakeRepositories(rows)
    current = await _service(current_repository).query(
        StructuredQueryRequest(
            eid="eid-1",
            query_type=StructuredQueryType.CURRENT_SHAREHOLDERS,
            page_size=1,
        )
    )

    historical_repository = FakeRepositories([_shareholder_row(3, True)])
    historical = await _service(historical_repository).query(
        StructuredQueryRequest(eid="eid-1", query_type=StructuredQueryType.HISTORICAL_SHAREHOLDERS)
    )

    assert current.pagination.returned == 1
    assert current.pagination.has_more is True
    assert current.pagination.truncated is True
    assert current.relations[0].relation_type == "has_shareholder"
    assert current.relations[0].properties["ownership_ratio"] == "0.125"
    assert current_repository.calls[0][1]["limit"] == 2
    assert historical.relations[0].relation_type == "had_shareholder"


async def test_contribution_json_keeps_amount_type_and_date():
    """认缴和实缴 JSON 应按源字段完整映射且保留出资方式。"""
    row = _shareholder_row(1, False)
    row["should_capi_items"] = '[{"shoud_capi":"100.00","invest_type":"货币","should_capi_date":"2024-01-02"}]'
    row["real_capi_items"] = '[{"real_capi":"80.50","invest_type":"实物","real_capi_date":"2024-03-04"}]'

    result = await _service(FakeRepositories([row])).query(
        StructuredQueryRequest(eid="eid-1", query_type=StructuredQueryType.SHAREHOLDER_CONTRIBUTIONS)
    )

    contributions = {fact.fact_type: fact for fact in result.facts if fact.fact_type.endswith("contribution")}
    assert contributions["subscribed_contribution"].normalized_value == {
        "amount": "100",
        "invest_type": "货币",
        "date": "2024-01-02",
    }
    assert contributions["paid_contribution"].normalized_value["amount"] == "80.5"
    assert contributions["paid_contribution"].normalized_value["invest_type"] == "实物"


async def test_financing_json_preserves_every_investor_type():
    """融资投资人数组中的每个名称和类型都应进入实体与关系。"""
    row = {
        "id": 10,
        "eid": "eid-1",
        "round_date": "2025-05-06",
        "round": "A轮",
        "round_type": "equity",
        "amount": "1000",
        "estimated_amount": None,
        "pre_money": None,
        "post_money": None,
        "currency": "CNY",
        "precise": 1,
        "investors_json": '[{"text":"机构甲","type":"机构"},{"text":"个人乙","type":"个人"}]',
        "newstitle": "融资新闻",
        "newslink": "https://example.invalid/news",
    }

    result = await _service(FakeRepositories([row])).query(
        StructuredQueryRequest(eid="eid-1", query_type=StructuredQueryType.FINANCING_HISTORY)
    )

    investors = [entity for entity in result.entities if entity.entity_type.value == "investor"]
    investor_relations = [relation for relation in result.relations if relation.relation_type == "financed_by"]
    assert {(entity.name, entity.attributes["investor_type"]) for entity in investors} == {
        ("机构甲", "机构"),
        ("个人乙", "个人"),
    }
    assert len(investor_relations) == 2


async def test_financial_empty_result_reports_mapping_coverage_warning():
    """财务映射均为空时不得断言企业没有财务数据。"""
    repository = FakeRepositories(coverage={"company_indicator_count": 0, "wind_metric_count": 0})

    result = await _service(repository).query(
        StructuredQueryRequest(eid="eid-1", query_type=StructuredQueryType.FINANCIAL_METRIC_SERIES)
    )

    assert result.data_quality.source_status == SourceStatus.PARTIAL
    assert any("不能解释为企业没有财务数据" in warning for warning in result.data_quality.warnings)


async def test_invalid_source_json_becomes_partial_quality_warning():
    """损坏的源 JSON 应保留其他事实并显式标记部分可用。"""
    row = _shareholder_row(1, False)
    row["should_capi_items"] = "not-json"

    result = await _service(FakeRepositories([row])).query(
        StructuredQueryRequest(eid="eid-1", query_type=StructuredQueryType.SHAREHOLDER_CONTRIBUTIONS)
    )

    assert result.data_quality.source_status == SourceStatus.PARTIAL
    assert "should_capi_items 不是合法 JSON" in result.data_quality.warnings
    assert result.relations


def _shareholder_row(record_id: int, is_history: bool) -> dict:
    """生成股东查询行。"""
    return {
        "id": record_id,
        "eid": "eid-1",
        "stock_name": f"股东{record_id}",
        "stock_id": f"shareholder-{record_id}",
        "stock_type": "企业",
        "stock_percent": "12.5%",
        "total_real_capi_new": "80",
        "real_currency_unit": "CNY",
        "total_should_capi_new": "100",
        "should_currency_unit": "CNY",
        "should_capi_items": "[]",
        "real_capi_items": "[]",
        "start_date": "2024-01-01",
        "end_date": "2025-01-01" if is_history else None,
        "is_history": int(is_history),
    }
