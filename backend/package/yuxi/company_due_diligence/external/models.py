"""外部数据补数统一契约。"""

from __future__ import annotations

from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from yuxi.company_due_diligence.models import CoverageStatus, Evidence, Fact, Source


class ProviderStatus(StrEnum):
    """描述 Provider 的可用状态。"""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    NOT_CONFIGURED = "not_configured"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


class ProviderErrorCode(StrEnum):
    """Provider 错误分类。"""

    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    AUTH_FAILED = "auth_failed"
    FORMAT_ERROR = "format_error"
    UPSTREAM_ERROR = "upstream_error"
    CONFIG_MISSING = "config_missing"
    NOT_SUPPORTED = "not_supported"
    EMPTY_RESULT = "empty_result"


class ExternalCompanyDataRequest(BaseModel):
    """外部企业数据查询请求。"""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "company_ref": {"eid": "企业内部标识"},
                    "topics": ["profile", "financials"],
                    "start_date": "2023-01-01",
                    "end_date": "2026-08-13",
                    "providers": ["akshare"],
                    "refresh": False,
                }
            ]
        }
    )

    company_ref: dict[str, str | None] = Field(default_factory=dict)
    topics: list[str] = Field(default_factory=list)
    start_date: date | None = None
    end_date: date | None = None
    providers: list[str] | None = None
    refresh: bool = False


class TopicCoverage(BaseModel):
    """单个主题的外部覆盖结果。"""

    topic: str
    status: CoverageStatus
    source_count: int = 0
    fact_count: int = 0
    provider_names: list[str] = Field(default_factory=list)
    message: str | None = None


class ProviderResult(BaseModel):
    """单个 Provider 返回的标准结果。"""

    provider_name: str
    facts: list[Fact] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    coverage: dict[str, CoverageStatus] = Field(default_factory=dict)
    missing_topics: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    status: ProviderStatus = ProviderStatus.HEALTHY
    error_code: ProviderErrorCode | None = None
    error_message: str | None = None
    latency_ms: float = Field(default=0, ge=0)
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ProviderHealth(BaseModel):
    """Provider 健康状态快照。"""

    provider_name: str
    status: ProviderStatus
    supported_topics: list[str] = Field(default_factory=list)
    message: str | None = None
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ExternalCompanyDataResponse(BaseModel):
    """外部数据补数统一响应。"""

    facts: list[Fact] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    coverage: dict[str, TopicCoverage] = Field(default_factory=dict)
    missing_topics: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    provider_results: list[ProviderResult] = Field(default_factory=list)
    provider_health: list[ProviderHealth] = Field(default_factory=list)
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    external_latency_ms: float = Field(default=0, ge=0)

    def model_dump(self, **kwargs) -> dict[str, Any]:
        """兼容旧版 pydantic 的序列化。"""
        return super().model_dump(**kwargs)
