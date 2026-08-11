"""企业尽调报告请求、章节状态、版本和产物契约。"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator

from yuxi.company_due_diligence.models import CompanyReference, DataQuality, ResolvedCompany, Source
from yuxi.company_due_diligence.qa.models import CompanyQuestionResponse


class ReportSection(StrEnum):
    """描述首版完整尽调报告章节。"""

    IDENTITY = "identity"
    BUSINESS = "business"
    SHAREHOLDERS = "shareholders"
    RELATIONS = "relations"
    PRODUCTS = "products"
    FINANCING = "financing"
    FINANCIALS = "financials"
    COMPLIANCE = "compliance"
    INTELLECTUAL_PROPERTY = "intellectual_property"
    NEWS = "news"
    RISKS = "risks"
    DATA_QUALITY = "data_quality"


class ReportSectionStatus(StrEnum):
    """描述单个报告章节终态。"""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    PARTIAL = "partial"
    NO_DATA = "no_data"
    STALE = "stale"
    CONFLICTED = "conflicted"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ReportStatus(StrEnum):
    """描述完整报告聚合终态。"""

    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ReportFormat(StrEnum):
    """描述首版允许下载的报告格式。"""

    JSON = "json"
    MARKDOWN = "markdown"


class DueDiligenceReportRequest(BaseModel):
    """描述报告企业、章节、时点、置信度和输出格式。"""

    company_ref: CompanyReference
    sections: list[ReportSection] = Field(
        default_factory=lambda: list(ReportSection),
        min_length=1,
        max_length=len(ReportSection),
    )
    as_of: date | None = None
    include_low_confidence: bool = False
    output_formats: list[ReportFormat] = Field(
        default_factory=lambda: [ReportFormat.JSON, ReportFormat.MARKDOWN],
        min_length=1,
        max_length=len(ReportFormat),
    )

    @field_validator("sections", "output_formats")
    @classmethod
    def deduplicate_values(cls, value):
        """按请求顺序去重章节和输出格式。"""
        return list(dict.fromkeys(value))


class ReportVersions(BaseModel):
    """锁定报告生成时使用的代码、配置和派生数据版本。"""

    code_version: str
    config_version: str
    graph_version: str | None = None
    evidence_version: str | None = None


class ReportSectionResult(BaseModel):
    """描述单章节状态、模板摘要和完整查询数据包。"""

    section: ReportSection
    title: str
    status: ReportSectionStatus
    summary: str
    query_result: CompanyQuestionResponse | None = None
    error: str | None = None


class ReportArtifact(BaseModel):
    """描述可校验的 MinIO 报告文件。"""

    format: ReportFormat
    bucket: str
    object_name: str
    checksum: str
    size_bytes: int = Field(ge=0)


class DueDiligenceReport(BaseModel):
    """描述冻结版本、章节结果、来源、质量和文件产物。"""

    report_id: str
    status: ReportStatus
    request: DueDiligenceReportRequest
    company: ResolvedCompany
    versions: ReportVersions
    created_at: datetime
    completed_at: datetime
    data_as_of: date | None = None
    sections: dict[str, ReportSectionResult]
    sources: list[Source] = Field(default_factory=list)
    data_quality: DataQuality
    artifacts: list[ReportArtifact] = Field(default_factory=list)
