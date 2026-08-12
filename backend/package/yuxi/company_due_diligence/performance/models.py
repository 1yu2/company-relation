"""企业尽调性能场景、安全参数、逐请求和报告契约。"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator, model_validator

from yuxi.company_due_diligence.models import CompanyReference, LatencyBreakdown
from yuxi.company_due_diligence.mysql import MySQLPoolHealth

REAL_DATABASE_CONFIRMATION = "CONFIRM_READONLY_REAL_DATABASE_LOAD"


class PerformanceSafetyMode(StrEnum):
    """区分日常低负载回归与受控真实库负载。"""

    SAFE_REGRESSION = "safe_regression"
    CONTROLLED_REAL_DATABASE = "controlled_real_database"


class PerformanceScenario(StrEnum):
    """描述首版六类企业尽调性能场景。"""

    ENTERPRISE_PROFILE = "enterprise_profile"
    SHAREHOLDER_LIST = "shareholder_list"
    FINANCING_AND_RISK = "financing_and_risk"
    GRAPH_PATH_3_HOPS = "graph_path_3_hops"
    REPORT_AGGREGATE_WITHOUT_LLM = "report_aggregate_without_llm"
    REPORT_WITH_LLM = "report_with_llm"


class PerformanceRunRequest(BaseModel):
    """描述性能场景、企业集合、负载参数和真实库确认。"""

    scenarios: list[PerformanceScenario] = Field(default_factory=lambda: [PerformanceScenario.ENTERPRISE_PROFILE])
    scope: str = Field(default="global", pattern="^global$")
    sampling_policy: str = Field(default="all_or_stratified", pattern="^(all|all_or_stratified|stratified)$")
    company_refs: list[CompanyReference] = Field(default_factory=list, max_length=20)
    target_company_ref: CompanyReference | None = None
    safety_mode: PerformanceSafetyMode = PerformanceSafetyMode.SAFE_REGRESSION
    concurrency: int = Field(default=1, ge=1, le=10)
    request_count: int = Field(default=5, ge=1, le=1000)
    duration_seconds: int = Field(default=30, ge=1, le=300)
    request_timeout_seconds: int = Field(default=60, ge=1, le=120)
    real_database_confirmation: str | None = Field(default=None, max_length=128)

    @field_validator("scenarios")
    @classmethod
    def deduplicate_scenarios(cls, value):
        """按请求顺序去重性能场景。"""
        return list(dict.fromkeys(value))

    @model_validator(mode="after")
    def validate_safety_limits(self):
        """强制安全模式和真实库模式的硬上限与确认口令。"""
        if self.safety_mode == PerformanceSafetyMode.SAFE_REGRESSION:
            if self.concurrency > 3:
                raise ValueError("安全回归模式并发不得超过 3")
            if self.duration_seconds > 60:
                raise ValueError("安全回归模式持续时间不得超过 60 秒")
            if self.request_count > 100:
                raise ValueError("安全回归模式请求数不得超过 100")
            return self
        if self.real_database_confirmation != REAL_DATABASE_CONFIRMATION:
            raise ValueError(f"受控真实库模式必须确认 {REAL_DATABASE_CONFIRMATION}")
        return self


class PerformanceCachePhase(StrEnum):
    """标识同一场景中的首请求和后续请求。"""

    COLD = "cold"
    HOT = "hot"


class PerformanceRequestStatus(StrEnum):
    """描述单次性能请求终态。"""

    SUCCESS = "success"
    ERROR = "error"
    TIMEOUT = "timeout"


class PerformanceRequestResult(BaseModel):
    """描述单次请求耗时、阶段耗时、版本与错误。"""

    request_index: int = Field(ge=0)
    company_index: int = Field(ge=0)
    interface: str = "company_due_diligence.query"
    intent: str = ""
    toolchain: list[str] = Field(default_factory=list)
    data_sources: list[str] = Field(default_factory=list)
    cache_phase: PerformanceCachePhase
    status: PerformanceRequestStatus
    started_at: datetime
    duration_ms: float = Field(ge=0)
    latency: LatencyBreakdown
    projection_versions: dict[str, str] = Field(default_factory=dict)
    error: str | None = None


class LatencyStatistics(BaseModel):
    """描述一组请求的分位数、最大值与平均值。"""

    count: int = Field(ge=0)
    p50_ms: float | None = None
    p95_ms: float | None = None
    p99_ms: float | None = None
    max_ms: float | None = None
    average_ms: float | None = None


class ResourceSnapshot(BaseModel):
    """描述 runner 进程的 CPU、内存和主机基础资源。"""

    captured_at: datetime
    cpu_user_seconds: float = Field(ge=0)
    cpu_system_seconds: float = Field(ge=0)
    max_rss_mb: float = Field(ge=0)
    cpu_count: int = Field(ge=1)
    load_average_1m: float | None = None


class PoolObservation(BaseModel):
    """描述运行前后连接池状态及本次增量。"""

    before: MySQLPoolHealth
    after: MySQLPoolHealth
    wait_ms_delta: float = Field(ge=0)
    timeout_delta: int = Field(ge=0)
    leak_detected: bool


class PerformanceScenarioStatus(StrEnum):
    """描述性能场景是否执行或因能力不可用跳过。"""

    COMPLETE = "complete"
    SKIPPED = "skipped"


class PerformanceScenarioResult(BaseModel):
    """描述单场景请求明细、聚合、目标和冷/热结果。"""

    scenario: PerformanceScenario
    status: PerformanceScenarioStatus
    reason: str | None = None
    target_p95_ms: float
    target_met: bool | None = None
    total_requests: int = Field(ge=0)
    succeeded: int = Field(ge=0)
    failed: int = Field(ge=0)
    timed_out: int = Field(ge=0)
    elapsed_seconds: float = Field(ge=0)
    throughput_rps: float = Field(ge=0)
    error_rate: float = Field(ge=0, le=1)
    timeout_rate: float = Field(ge=0, le=1)
    latency: LatencyStatistics
    cold_latency: LatencyStatistics
    hot_latency: LatencyStatistics
    average_stage_latency: LatencyBreakdown
    requests: list[PerformanceRequestResult] = Field(default_factory=list)


class PerformanceRegressionGate(BaseModel):
    """描述场景阈值、错误率和连接池释放门禁。"""

    eligible: bool
    passed: bool
    reasons: list[str] = Field(default_factory=list)


class PerformanceArtifact(BaseModel):
    """描述性能报告导出文件及完整性信息。"""

    format: str
    bucket: str
    object_name: str
    checksum: str
    size_bytes: int = Field(ge=0)


class GlobalPerformanceSummary(BaseModel):
    """描述跨场景的全局性能分位数、状态率和阶段耗时。"""

    total_requests: int = Field(ge=0)
    succeeded: int = Field(ge=0)
    failed: int = Field(ge=0)
    timed_out: int = Field(ge=0)
    cancelled: int = Field(default=0, ge=0)
    elapsed_seconds: float = Field(ge=0)
    throughput_rps: float = Field(ge=0)
    error_rate: float = Field(ge=0, le=1)
    timeout_rate: float = Field(ge=0, le=1)
    cancellation_rate: float = Field(ge=0, le=1)
    latency: LatencyStatistics
    average_stage_latency: LatencyBreakdown
    by_scenario: dict[str, int] = Field(default_factory=dict)
    by_status: dict[str, int] = Field(default_factory=dict)
    by_interface: dict[str, int] = Field(default_factory=dict)
    by_intent: dict[str, int] = Field(default_factory=dict)
    by_toolchain: dict[str, int] = Field(default_factory=dict)
    by_data_source: dict[str, float] = Field(default_factory=dict)
    by_data_source_requests: dict[str, int] = Field(default_factory=dict)


class PerformanceEvaluationReport(BaseModel):
    """描述可对比的性能运行参数、版本、资源和结果。"""

    run_id: str
    created_at: datetime
    completed_at: datetime
    code_version: str
    strategy_version: str
    request: PerformanceRunRequest
    sample_size: int = Field(default=0, ge=0)
    data_versions: dict[str, list[str]] = Field(default_factory=dict)
    model_versions: dict[str, str] = Field(default_factory=dict)
    resources_before: ResourceSnapshot
    resources_after: ResourceSnapshot
    pool: PoolObservation
    global_summary: GlobalPerformanceSummary
    runtime_observations: dict[str, int | float | str | bool] = Field(default_factory=dict)
    scenarios: list[PerformanceScenarioResult]
    regression_gate: PerformanceRegressionGate
    artifacts: list[PerformanceArtifact] = Field(default_factory=list)
