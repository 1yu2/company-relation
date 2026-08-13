"""外部数据补数统一契约。"""

from __future__ import annotations

from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from yuxi.company_due_diligence.models import CompanyReference, CoverageStatus, Evidence, Fact, Source


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

    company_ref: CompanyReference
    topics: list[str] = Field(min_length=1)
    start_date: date | None = None
    end_date: date | None = None
    providers: list[str] | None = None
    refresh: bool = False

    @field_validator("topics", "providers")
    @classmethod
    def normalize_names(cls, value):
        """去除空白、拒绝空名称并按请求顺序去重。"""
        if value is None:
            return None
        normalized = [str(item).strip() for item in value]
        if any(not item for item in normalized):
            raise ValueError("主题和 Provider 名称不能为空")
        return list(dict.fromkeys(normalized))

    @model_validator(mode="after")
    def validate_query_scope(self) -> ExternalCompanyDataRequest:
        """确保外部查询始终绑定已确认企业和有效时间窗口。"""
        if not self.company_ref.eid:
            raise ValueError("外部查询必须使用已确认企业的 eid")
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date 不能早于 start_date")
        return self


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


class ProviderQueryAudit(BaseModel):
    """Provider 查询脱敏审计记录。"""

    provider_name: str
    topics: list[str] = Field(default_factory=list)
    status: ProviderStatus
    error_code: ProviderErrorCode | None = None
    latency_ms: float = Field(default=0, ge=0)
    queried_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


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
