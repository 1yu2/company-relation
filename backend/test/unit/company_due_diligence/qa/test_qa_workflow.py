"""企业尽调问答编排器短链路单元测试。"""

from datetime import UTC, datetime

import pytest

from yuxi.company_due_diligence.models import (
    DataQuality,
    Fact,
    LatencyBreakdown,
    Pagination,
    Source,
    StructuredQueryResult,
    StructuredQueryType,
    build_source_id,
)
from yuxi.company_due_diligence.qa.graph import QAWorkflow
from yuxi.company_due_diligence.qa.models import CompanyQuestionRequest
from yuxi.company_due_diligence.qa.planner import QuestionPlanner

pytestmark = pytest.mark.asyncio


class FakeStructuredService:
    """返回固定注册资本事实。"""

    async def query(self, request):
        """校验白名单查询并返回带来源事实。"""
        assert request.query_type == StructuredQueryType.REGISTERED_CAPITAL
        source_id = build_source_id("t_enterprise", request.eid)
        return StructuredQueryResult(
            query_type=request.query_type,
            eid=request.eid,
            facts=[
                Fact(
                    fact_id="fact-1",
                    subject_id=request.eid,
                    fact_type="registered_capital",
                    normalized_value=1000,
                    display_value="1000 万元",
                    source_id=source_id,
                )
            ],
            sources=[
                Source(
                    source_id=source_id,
                    table="t_enterprise",
                    record_key=request.eid,
                    queried_at=datetime.now(UTC),
                )
            ],
            data_quality=DataQuality(),
            pagination=Pagination(page=1, page_size=10, returned=1),
            latency=LatencyBreakdown(mysql_ms=1, total_ms=1),
        )


class ForbiddenService:
    """简单结构化问题不应调用的服务。"""

    async def query(self, _request):
        """任何调用都表示短链路路由错误。"""
        raise AssertionError("简单结构化问题不应调用该服务")


async def test_simple_question_does_not_call_graph_or_evidence():
    """简单字段问题只走 MySQL，不依赖 Neo4j 或 Milvus。"""
    request = CompanyQuestionRequest(company_ref={"eid": "eid-1"}, question="注册资本是多少？")
    plan = QuestionPlanner().plan(request.question)
    workflow = QAWorkflow(
        structured_service=FakeStructuredService(),
        graph_service=ForbiddenService(),
        evidence_service=ForbiddenService(),
    )

    result = await workflow.execute(eid="eid-1", target_eid=None, request=request, plan=plan)

    assert len(result.structured) == 1
    assert result.graph is None
    assert result.evidence is None
    assert [step.source for step in result.steps] == ["mysql"]


@pytest.mark.parametrize("all_holders,expected", [(False, 10), (True, 125)])
async def test_current_shareholders_fetch_all_pages_when_requested(all_holders, expected):
    """默认前十；明确全部时跨越百条分页，并把当前语义传给出资查询。"""
    calls = []

    class PagedService:
        """按页返回固定的 125 条事实。"""

        async def query(self, request):
            """模拟底层分页契约，不复制业务筛选。"""
            calls.append(request)
            start = (request.page - 1) * request.page_size
            end = min(start + request.page_size, 125)
            return StructuredQueryResult(
                query_type=request.query_type,
                eid=request.eid,
                facts=[
                    Fact(
                        fact_id=f"f-{i}",
                        subject_id="holder",
                        fact_type="ownership_ratio",
                        normalized_value="0.01",
                        source_id="source",
                    )
                    for i in range(start, end)
                ],
                data_quality=DataQuality(),
                pagination=Pagination(
                    page=request.page,
                    page_size=request.page_size,
                    returned=end - start,
                    has_more=end < 125,
                    truncated=end < 125,
                ),
                latency=LatencyBreakdown(),
            )

    request = CompanyQuestionRequest(question=f"查询{'全部' if all_holders else ''}当前股东和出资情况")
    result = await QAWorkflow(structured_service=PagedService()).execute(
        eid="eid-1", target_eid=None, request=request, plan=QuestionPlanner().plan(request.question)
    )
    for query_type in (StructuredQueryType.CURRENT_SHAREHOLDERS, StructuredQueryType.SHAREHOLDER_CONTRIBUTIONS):
        pages = [item for item in result.structured if item.query_type == query_type]
        assert sum(len(item.facts) for item in pages) == expected
        if all_holders:
            assert [item.pagination.page for item in pages] == [1, 2]
            assert not pages[-1].pagination.has_more
    contribution_calls = [call for call in calls if call.query_type == StructuredQueryType.SHAREHOLDER_CONTRIBUTIONS]
    assert contribution_calls and all(call.current_shareholders_only for call in contribution_calls)
