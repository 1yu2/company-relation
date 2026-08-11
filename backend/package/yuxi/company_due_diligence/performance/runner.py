"""企业尽调进程内异步性能评测 runner。"""

from __future__ import annotations

import asyncio
import math
import os
import platform
import resource
import time
from datetime import UTC, datetime

from yuxi.company_due_diligence.models import LatencyBreakdown
from yuxi.company_due_diligence.mysql import CompanyDueDiligenceMySQL, company_due_diligence_mysql
from yuxi.company_due_diligence.performance.models import (
    LatencyStatistics,
    PerformanceCachePhase,
    PerformanceEvaluationReport,
    PerformanceRegressionGate,
    PerformanceRequestResult,
    PerformanceRequestStatus,
    PerformanceRunRequest,
    PerformanceScenario,
    PerformanceScenarioResult,
    PerformanceScenarioStatus,
    PoolObservation,
    ResourceSnapshot,
)
from yuxi.company_due_diligence.performance.scenarios import (
    SCENARIO_TARGET_P95_MS,
    build_question_request,
    build_report_request,
)
from yuxi.company_due_diligence.qa.service import CompanyQuestionService, company_question_service
from yuxi.company_due_diligence.report.service import DueDiligenceReportService, due_diligence_report_service
from yuxi.config import config
from yuxi.services.task_service import TaskContext


class PerformanceEvaluationRunner:
    """按安全参数调用真实问答和报告服务并聚合性能指标。"""

    def __init__(
        self,
        *,
        qa_service: CompanyQuestionService | None = None,
        report_service: DueDiligenceReportService | None = None,
        mysql: CompanyDueDiligenceMySQL | None = None,
    ) -> None:
        self._qa_service = qa_service or company_question_service
        self._report_service = report_service or due_diligence_report_service
        self._mysql = mysql or company_due_diligence_mysql

    async def run(
        self,
        request: PerformanceRunRequest,
        *,
        run_id: str,
        context: TaskContext | None = None,
    ) -> PerformanceEvaluationReport:
        """顺序运行场景、场景内受控并发，并支持取消和部分结果查看。"""
        created_at = datetime.now(UTC)
        resources_before = _resource_snapshot()
        pool_before = self._mysql.pool_health()
        scenario_results = []
        for scenario_index, scenario in enumerate(request.scenarios, start=1):
            if context is not None:
                await context.raise_if_cancelled()
                await context.set_progress(
                    (scenario_index - 1) / len(request.scenarios) * 90,
                    f"性能场景 {scenario_index}/{len(request.scenarios)}：{scenario.value}",
                )
            if scenario == PerformanceScenario.REPORT_WITH_LLM:
                result = _skipped_llm_scenario()
            else:
                result = await self._run_scenario(
                    request,
                    scenario,
                    run_id,
                    context,
                    progress_start=(scenario_index - 1) / len(request.scenarios) * 90,
                    progress_span=90 / len(request.scenarios),
                )
            scenario_results.append(result)
            if context is not None:
                await context.set_result(
                    {
                        "completed_scenarios": scenario_index,
                        "total_scenarios": len(request.scenarios),
                        "scenarios": [item.model_dump(mode="json") for item in scenario_results],
                    }
                )

        pool_after = self._mysql.pool_health()
        pool = PoolObservation(
            before=pool_before,
            after=pool_after,
            wait_ms_delta=max(0, pool_after.wait_ms - pool_before.wait_ms),
            timeout_delta=max(0, pool_after.timeouts - pool_before.timeouts),
            leak_detected=pool_after.checked_out > pool_before.checked_out or pool_after.overflow > 0,
        )
        report = PerformanceEvaluationReport(
            run_id=run_id,
            created_at=created_at,
            completed_at=datetime.now(UTC),
            code_version=os.getenv("YUXI_VERSION", "0.7.1"),
            strategy_version=os.getenv("COMPANY_DD_PERFORMANCE_STRATEGY_VERSION", "default"),
            request=request,
            data_versions=_collect_versions(scenario_results),
            model_versions={
                "default_model": config.default_model,
                "embedding_model": config.embed_model,
            },
            resources_before=resources_before,
            resources_after=_resource_snapshot(),
            pool=pool,
            scenarios=scenario_results,
            regression_gate=_regression_gate(scenario_results, pool),
        )
        return report

    async def _run_scenario(
        self,
        request: PerformanceRunRequest,
        scenario: PerformanceScenario,
        run_id: str,
        context: TaskContext | None,
        progress_start: float,
        progress_span: float,
    ) -> PerformanceScenarioResult:
        """在持续时间和并发硬上限内执行单个场景。"""
        started_at = time.perf_counter()
        records = []
        next_index = 0
        state_lock = asyncio.Lock()

        async def worker() -> None:
            """领取有限请求并串行写入进度。"""
            nonlocal next_index
            while True:
                async with state_lock:
                    elapsed = time.perf_counter() - started_at
                    if next_index >= request.request_count or elapsed >= request.duration_seconds:
                        return
                    request_index = next_index
                    next_index += 1
                if context is not None:
                    await context.raise_if_cancelled()
                company_index = request_index % len(request.company_refs)
                record = await self._execute_request(
                    scenario,
                    request.company_refs[company_index],
                    request.target_company_ref,
                    run_id,
                    request_index,
                    company_index,
                    request.request_timeout_seconds,
                )
                async with state_lock:
                    records.append(record)
                    completed = len(records)
                    if context is not None:
                        progress = progress_start + completed / request.request_count * progress_span
                        await context.set_progress(progress, f"{scenario.value} 已完成 {completed} 个请求")
                        await context.set_result(
                            {
                                "active_scenario": scenario.value,
                                "completed_requests": completed,
                                "requested": request.request_count,
                                "requests": [item.model_dump(mode="json") for item in records],
                            }
                        )

        workers = [asyncio.create_task(worker()) for _ in range(request.concurrency)]
        try:
            await asyncio.gather(*workers)
        except BaseException:
            for worker_task in workers:
                worker_task.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
            raise

        records.sort(key=lambda item: item.request_index)
        return _summarize_scenario(scenario, records, time.perf_counter() - started_at)

    async def _execute_request(
        self,
        scenario: PerformanceScenario,
        company_ref,
        target_company_ref,
        run_id: str,
        request_index: int,
        company_index: int,
        timeout_seconds: int,
    ) -> PerformanceRequestResult:
        """执行一条真实服务调用并记录超时、异常和阶段耗时。"""
        started_at = datetime.now(UTC)
        started_perf = time.perf_counter()
        latency = LatencyBreakdown()
        versions = {}
        status = PerformanceRequestStatus.SUCCESS
        error = None
        try:
            async with asyncio.timeout(timeout_seconds):
                if scenario == PerformanceScenario.REPORT_AGGREGATE_WITHOUT_LLM:
                    report = await self._report_service.generate_inline(
                        build_report_request(company_ref),
                        report_id=f"performance-{run_id}-{request_index}",
                    )
                    latency = _report_latency(report)
                    versions = {
                        key: value
                        for key, value in {
                            "graph": report.versions.graph_version,
                            "evidence": report.versions.evidence_version,
                            "config": report.versions.config_version,
                        }.items()
                        if value
                    }
                else:
                    response = await self._qa_service.query(
                        build_question_request(scenario, company_ref, target_company_ref)
                    )
                    latency = response.latency
                    versions = response.projection_versions
        except TimeoutError:
            status = PerformanceRequestStatus.TIMEOUT
            error = "TimeoutError"
        except Exception as exc:
            status = PerformanceRequestStatus.ERROR
            error = type(exc).__name__
        duration_ms = round((time.perf_counter() - started_perf) * 1000, 3)
        latency = latency.model_copy(update={"total_ms": duration_ms})
        return PerformanceRequestResult(
            request_index=request_index,
            company_index=company_index,
            cache_phase=PerformanceCachePhase.COLD if request_index == 0 else PerformanceCachePhase.HOT,
            status=status,
            started_at=started_at,
            duration_ms=duration_ms,
            latency=latency,
            projection_versions=versions,
            error=error,
        )


def _skipped_llm_scenario() -> PerformanceScenarioResult:
    """明确报告在线 LLM 场景尚不可测，避免复用无 LLM 路径伪装。"""
    empty = LatencyStatistics(count=0)
    return PerformanceScenarioResult(
        scenario=PerformanceScenario.REPORT_WITH_LLM,
        status=PerformanceScenarioStatus.SKIPPED,
        reason="当前报告工作流未调用在线 LLM，不能生成 report_with_llm 性能结论",
        target_p95_ms=SCENARIO_TARGET_P95_MS[PerformanceScenario.REPORT_WITH_LLM],
        total_requests=0,
        succeeded=0,
        failed=0,
        timed_out=0,
        elapsed_seconds=0,
        throughput_rps=0,
        error_rate=0,
        timeout_rate=0,
        latency=empty,
        cold_latency=empty,
        hot_latency=empty,
        average_stage_latency=LatencyBreakdown(),
    )


def _summarize_scenario(
    scenario: PerformanceScenario,
    records: list[PerformanceRequestResult],
    elapsed_seconds: float,
) -> PerformanceScenarioResult:
    """聚合单场景分位数、吞吐、错误率和阶段均值。"""
    total = len(records)
    succeeded = sum(item.status == PerformanceRequestStatus.SUCCESS for item in records)
    timed_out = sum(item.status == PerformanceRequestStatus.TIMEOUT for item in records)
    failed = total - succeeded
    successful_records = [item for item in records if item.status == PerformanceRequestStatus.SUCCESS]
    target = SCENARIO_TARGET_P95_MS[scenario]
    latency = latency_statistics(item.duration_ms for item in successful_records)
    return PerformanceScenarioResult(
        scenario=scenario,
        status=PerformanceScenarioStatus.COMPLETE,
        target_p95_ms=target,
        target_met=latency.p95_ms is not None and latency.p95_ms < target,
        total_requests=total,
        succeeded=succeeded,
        failed=failed,
        timed_out=timed_out,
        elapsed_seconds=round(elapsed_seconds, 6),
        throughput_rps=round(total / elapsed_seconds, 6) if elapsed_seconds else 0,
        error_rate=failed / total if total else 0,
        timeout_rate=timed_out / total if total else 0,
        latency=latency,
        cold_latency=latency_statistics(
            item.duration_ms for item in successful_records if item.cache_phase == PerformanceCachePhase.COLD
        ),
        hot_latency=latency_statistics(
            item.duration_ms for item in successful_records if item.cache_phase == PerformanceCachePhase.HOT
        ),
        average_stage_latency=_average_stage_latency(successful_records),
        requests=records,
    )


def latency_statistics(values) -> LatencyStatistics:
    """按 nearest-rank 计算 P50/P95/P99、最大值和平均值。"""
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return LatencyStatistics(count=0)
    return LatencyStatistics(
        count=len(ordered),
        p50_ms=_percentile(ordered, 0.50),
        p95_ms=_percentile(ordered, 0.95),
        p99_ms=_percentile(ordered, 0.99),
        max_ms=ordered[-1],
        average_ms=round(sum(ordered) / len(ordered), 3),
    )


def _percentile(ordered: list[float], quantile: float) -> float:
    """返回 nearest-rank 分位数。"""
    index = max(0, math.ceil(quantile * len(ordered)) - 1)
    return ordered[index]


def _average_stage_latency(records: list[PerformanceRequestResult]) -> LatencyBreakdown:
    """计算成功请求各阶段耗时平均值。"""
    if not records:
        return LatencyBreakdown()
    fields = tuple(LatencyBreakdown.model_fields)
    return LatencyBreakdown(
        **{field: round(sum(getattr(item.latency, field) for item in records) / len(records), 3) for field in fields}
    )


def _report_latency(report) -> LatencyBreakdown:
    """以并行章节关键路径最大值近似报告各检索阶段耗时。"""
    latencies = []
    for section in report.sections.values():
        if section.query_result is not None:
            latencies.append(section.query_result.latency)
    if not latencies:
        return LatencyBreakdown()
    return LatencyBreakdown(
        **{field: max(getattr(latency, field) for latency in latencies) for field in LatencyBreakdown.model_fields}
    )


def _resource_snapshot() -> ResourceSnapshot:
    """读取当前 runner 进程的 CPU、RSS 和系统负载。"""
    usage = resource.getrusage(resource.RUSAGE_SELF)
    max_rss_mb = usage.ru_maxrss / (1024 * 1024 if platform.system() == "Darwin" else 1024)
    try:
        load_average = os.getloadavg()[0]
    except OSError:
        load_average = None
    return ResourceSnapshot(
        captured_at=datetime.now(UTC),
        cpu_user_seconds=usage.ru_utime,
        cpu_system_seconds=usage.ru_stime,
        max_rss_mb=round(max_rss_mb, 3),
        cpu_count=os.cpu_count() or 1,
        load_average_1m=load_average,
    )


def _collect_versions(results: list[PerformanceScenarioResult]) -> dict[str, list[str]]:
    """聚合请求返回的图谱、证据和配置版本。"""
    versions = {}
    for result in results:
        for request in result.requests:
            for key, value in request.projection_versions.items():
                versions.setdefault(key, set()).add(value)
    return {key: sorted(values) for key, values in sorted(versions.items())}


def _regression_gate(
    scenarios: list[PerformanceScenarioResult],
    pool: PoolObservation,
) -> PerformanceRegressionGate:
    """应用场景 P95、错误率、超时和连接池释放门禁。"""
    completed = [item for item in scenarios if item.status == PerformanceScenarioStatus.COMPLETE]
    if not completed:
        return PerformanceRegressionGate(
            eligible=False,
            passed=False,
            reasons=["没有可执行场景，无法形成性能结论"],
        )
    reasons = []
    for scenario in completed:
        if scenario.target_met is not True:
            reasons.append(f"{scenario.scenario.value} P95 未达到目标")
        if scenario.error_rate >= 0.01:
            reasons.append(f"{scenario.scenario.value} 错误率未低于 1%")
        if scenario.timed_out:
            reasons.append(f"{scenario.scenario.value} 存在超时请求")
    if pool.leak_detected:
        reasons.append("运行结束后连接池仍有签出连接或溢出")
    if pool.timeout_delta:
        reasons.append("运行期间发生连接池等待超时")
    return PerformanceRegressionGate(eligible=True, passed=not reasons, reasons=reasons)
