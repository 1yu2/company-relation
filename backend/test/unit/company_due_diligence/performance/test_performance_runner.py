"""企业尽调性能 runner 聚合、并发、超时和取消单元测试。"""

import asyncio

import pytest

from yuxi.company_due_diligence.models import LatencyBreakdown
from yuxi.company_due_diligence.mysql import MySQLPoolHealth
from yuxi.company_due_diligence.performance.models import (
    PerformanceRequestStatus,
    PerformanceRunRequest,
    PerformanceScenario,
    PerformanceScenarioStatus,
)
from yuxi.company_due_diligence.performance.runner import PerformanceEvaluationRunner, latency_statistics


class FakeQAResponse:
    """提供固定阶段耗时和投影版本。"""

    latency = LatencyBreakdown(resolution_ms=1, mysql_ms=2, total_ms=3)
    projection_versions = {"neo4j": "graph-v1"}


class TrackingQAService:
    """记录最大并发，并可模拟固定延迟。"""

    def __init__(self, delay_seconds: float = 0.01) -> None:
        self.delay_seconds = delay_seconds
        self.active = 0
        self.max_active = 0

    async def query(self, _request):
        """在睡眠期间更新当前和最大并发。"""
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(self.delay_seconds)
            return FakeQAResponse()
        finally:
            self.active -= 1


class FakeMySQL:
    """返回可控制的连接池前后快照。"""

    def __init__(self) -> None:
        self.calls = 0

    def pool_health(self):
        """第二次快照增加等待但不泄漏连接。"""
        self.calls += 1
        return MySQLPoolHealth(
            size=2,
            checked_out=0,
            overflow=0,
            wait_ms=5 if self.calls == 1 else 8,
            timeouts=0,
        )


class CancellingContext:
    """首个请求完成后触发取消。"""

    def __init__(self) -> None:
        self.cancelled = False

    async def raise_if_cancelled(self):
        """取消后抛出标准任务取消异常。"""
        if self.cancelled:
            raise asyncio.CancelledError

    async def set_progress(self, _progress, _message):
        """接受进度。"""

    async def set_result(self, result):
        """在出现首条请求结果后请求取消。"""
        if result.get("completed_requests"):
            self.cancelled = True


def test_nearest_rank_percentiles_and_average():
    """分位数应使用 nearest-rank 并返回最大值和平均值。"""
    result = latency_statistics([1, 2, 3, 4, 100])

    assert result.p50_ms == 3
    assert result.p95_ms == 100
    assert result.p99_ms == 100
    assert result.max_ms == 100
    assert result.average_ms == 22


@pytest.mark.asyncio
async def test_runner_respects_concurrency_and_records_pool_delta():
    """场景内并发不得超过请求参数，结束后应记录池等待增量。"""
    qa = TrackingQAService()
    report = await PerformanceEvaluationRunner(qa_service=qa, mysql=FakeMySQL()).run(
        PerformanceRunRequest(
            company_refs=[{"eid": "eid-1"}],
            concurrency=2,
            request_count=6,
            duration_seconds=5,
        ),
        run_id="performance-1",
    )

    scenario = report.scenarios[0]
    assert qa.max_active == 2
    assert scenario.total_requests == 6
    assert scenario.succeeded == 6
    assert scenario.latency.p95_ms is not None
    assert scenario.average_stage_latency.mysql_ms == 2
    assert report.pool.wait_ms_delta == 3
    assert report.pool.leak_detected is False
    assert report.data_versions == {"neo4j": ["graph-v1"]}


@pytest.mark.asyncio
async def test_timeout_is_counted_and_fails_gate():
    """单请求超时应单列并使门禁失败。"""
    qa = TrackingQAService(delay_seconds=1.1)
    report = await PerformanceEvaluationRunner(qa_service=qa, mysql=FakeMySQL()).run(
        PerformanceRunRequest(
            company_refs=[{"eid": "eid-1"}],
            request_count=1,
            duration_seconds=5,
            request_timeout_seconds=1,
        ),
        run_id="performance-timeout",
    )

    scenario = report.scenarios[0]
    assert scenario.requests[0].status == PerformanceRequestStatus.TIMEOUT
    assert scenario.timed_out == 1
    assert report.regression_gate.passed is False


@pytest.mark.asyncio
async def test_llm_scenario_is_skipped_instead_of_reusing_non_llm_path():
    """未接在线 LLM 时必须明确跳过含 LLM 报告场景。"""
    report = await PerformanceEvaluationRunner(qa_service=TrackingQAService(), mysql=FakeMySQL()).run(
        PerformanceRunRequest(
            company_refs=[{"eid": "eid-1"}],
            scenarios=[PerformanceScenario.REPORT_WITH_LLM],
            request_count=1,
        ),
        run_id="performance-llm",
    )

    scenario = report.scenarios[0]
    assert scenario.status == PerformanceScenarioStatus.SKIPPED
    assert scenario.total_requests == 0
    assert "未调用在线 LLM" in scenario.reason
    assert report.regression_gate.eligible is False


@pytest.mark.asyncio
async def test_cancellation_stops_workers_and_releases_concurrency():
    """取消时应终止所有场景 worker，不留下活动请求。"""
    qa = TrackingQAService(delay_seconds=0.01)
    context = CancellingContext()

    with pytest.raises(asyncio.CancelledError):
        await PerformanceEvaluationRunner(qa_service=qa, mysql=FakeMySQL()).run(
            PerformanceRunRequest(
                company_refs=[{"eid": "eid-1"}],
                concurrency=2,
                request_count=20,
                duration_seconds=5,
            ),
            run_id="performance-cancelled",
            context=context,
        )

    assert qa.active == 0
