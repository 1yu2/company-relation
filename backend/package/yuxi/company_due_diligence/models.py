"""企业尽调统一输入输出契约。"""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator


class ConfidenceLevel(StrEnum):
    """描述事实、关系或身份匹配的可信等级。"""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ResolutionStatus(StrEnum):
    """描述企业身份解析的最终状态。"""

    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    NOT_FOUND = "not_found"
    SOURCE_UNAVAILABLE = "source_unavailable"


class RequestScopeStatus(StrEnum):
    """描述请求是否已经确定单企业范围或进入全局范围。"""

    RESOLVED = "resolved"
    GLOBAL_SCOPE = "global_scope"
    NEEDS_CONTEXT = "needs_context"
    NOT_FOUND = "not_found"
    SOURCE_UNAVAILABLE = "source_unavailable"


class MatchMethod(StrEnum):
    """描述企业身份命中的字段和方式。"""

    EID = "eid"
    CREDIT_CODE = "credit_code"
    STOCK_CODE = "stock_code"
    EXACT_NAME = "exact_name"
    NORMALIZED_NAME = "normalized_name"
    ABBREVIATION = "abbreviation"
    FUZZY_NAME = "fuzzy_name"


class SourceStatus(StrEnum):
    """描述一次查询依赖的数据源状态。"""

    AVAILABLE = "available"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


class CoverageStatus(StrEnum):
    """描述某主题在当前数据源下的覆盖状态。"""

    COMPLETE = "complete"
    PARTIAL = "partial"
    MISSING = "missing"
    STALE = "stale"
    CONFLICT = "conflict"
    UNSUPPORTED = "unsupported"


class EntityType(StrEnum):
    """描述统一实体的业务类型。"""

    COMPANY = "company"
    SHAREHOLDER = "shareholder"
    INVESTOR = "investor"
    FINANCING_EVENT = "financing_event"
    RISK_EVENT = "risk_event"
    INTELLECTUAL_PROPERTY = "intellectual_property"
    REGULATOR = "regulator"
    PRODUCT = "product"
    LICENSE = "license"
    INTELLECTUAL_PROPERTY_ITEM = "intellectual_property_item"


class StructuredQueryType(StrEnum):
    """描述首版允许执行的结构化查询类型。"""

    ENTERPRISE_PROFILE = "enterprise_profile"
    REGISTERED_CAPITAL = "registered_capital"
    LEGAL_REPRESENTATIVE = "legal_representative"
    BUSINESS_STATUS = "business_status"
    CURRENT_SHAREHOLDERS = "current_shareholders"
    HISTORICAL_SHAREHOLDERS = "historical_shareholders"
    SHAREHOLDER_CONTRIBUTIONS = "shareholder_contributions"
    PRODUCTS = "products"
    FINANCING_HISTORY = "financing_history"
    ADMINISTRATIVE_PENALTIES = "administrative_penalties"
    ENVIRONMENTAL_PENALTIES = "environmental_penalties"
    ABNORMAL_OPERATIONS = "abnormal_operations"
    LICENSES = "licenses"
    PATENT_SUMMARY = "patent_summary"
    TRADEMARK_SUMMARY = "trademark_summary"
    COPYRIGHT_SUMMARY = "copyright_summary"
    FINANCIAL_METRIC_SERIES = "financial_metric_series"


class SortOrder(StrEnum):
    """描述结构化列表允许的排序方向。"""

    ASC = "asc"
    DESC = "desc"


class CompanyReference(BaseModel):
    """描述用户提供的企业唯一标识或名称。"""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"eid": "企业内部标识"},
                {"credit_code": "统一社会信用代码"},
                {"stock_code": "000001.SZ"},
                {"name": "企业标准全称"},
            ]
        }
    )

    eid: str | None = Field(default=None, min_length=1, max_length=64)
    credit_code: str | None = Field(default=None, min_length=1, max_length=32)
    stock_code: str | None = Field(default=None, min_length=1, max_length=32)
    name: str | None = Field(default=None, min_length=1, max_length=256)

    @field_validator("eid", "credit_code", "stock_code", "name", mode="before")
    @classmethod
    def normalize_empty_value(cls, value):
        """把仅含空白的输入转换为空值。"""
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @model_validator(mode="after")
    def require_identifier(self) -> CompanyReference:
        """确保至少提供一种企业标识。"""
        if not any((self.eid, self.credit_code, self.stock_code, self.name)):
            raise ValueError("至少提供 eid、信用代码、股票代码或企业名称之一")
        return self


class Source(BaseModel):
    """描述可追溯到事实主库或外部 Provider 的稳定来源。"""

    source_id: str
    source_system: str = "guangdong_enterprise_mysql"
    table: str
    record_key: str
    business_date: date | datetime | None = None
    queried_at: datetime
    retrieved_at: datetime | None = None
    provider_name: str | None = None
    upstream_api: str | None = None
    source_url: str | None = None
    external_metadata: dict[str, JsonValue] | None = None


class Entity(BaseModel):
    """描述统一企业尽调实体。"""

    entity_id: str
    entity_type: EntityType
    name: str
    attributes: dict[str, JsonValue] = Field(default_factory=dict)
    source_ids: list[str] = Field(default_factory=list)


class Fact(BaseModel):
    """描述带规范化值和来源的结构化事实。"""

    fact_id: str
    subject_id: str
    fact_type: str
    normalized_value: JsonValue
    display_value: str | None = None
    unit: str | None = None
    effective_date: date | None = None
    confidence: ConfidenceLevel = ConfidenceLevel.HIGH
    source_id: str


class Relation(BaseModel):
    """描述有方向、有类型和来源的实体关系。"""

    relation_id: str
    source_entity_id: str
    target_entity_id: str
    relation_type: str
    properties: dict[str, JsonValue] = Field(default_factory=dict)
    confidence: ConfidenceLevel
    source_ids: list[str]
    effective_start: date | None = None
    effective_end: date | None = None


class Path(BaseModel):
    """描述最多三跳的节点和关系序列。"""

    path_id: str
    nodes: list[Entity]
    edges: list[Relation]
    hop_count: int = Field(ge=0, le=3)
    confidence: ConfidenceLevel
    source_ids: list[str]


class Evidence(BaseModel):
    """描述可回查原始记录的文本证据片段。"""

    evidence_id: str
    document_id: str
    entity_id: str
    document_type: str
    text: str
    score: float = Field(ge=0)
    published_at: date | datetime | None = None
    source_id: str
    projection_version: str


class DataQuality(BaseModel):
    """描述缺失、过期、冲突和最低置信度。"""

    source_status: SourceStatus = SourceStatus.AVAILABLE
    missing_fields: list[str] = Field(default_factory=list)
    stale_fields: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    minimum_confidence: ConfidenceLevel | None = None


class LatencyBreakdown(BaseModel):
    """描述企业尽调各检索阶段的毫秒耗时。"""

    resolution_ms: float = Field(default=0, ge=0)
    mysql_ms: float = Field(default=0, ge=0)
    neo4j_ms: float = Field(default=0, ge=0)
    milvus_ms: float = Field(default=0, ge=0)
    external_ms: float = Field(default=0, ge=0)
    llm_ms: float = Field(default=0, ge=0)
    assembly_ms: float = Field(default=0, ge=0)
    total_ms: float = Field(default=0, ge=0)


class CompanyCandidate(BaseModel):
    """描述需要用户确认的企业候选。"""

    eid: str
    name: str
    normalized_name: str
    credit_code: str
    stock_code: str
    stock_abbreviation: str
    match_method: MatchMethod
    confidence: ConfidenceLevel
    source_ids: list[str]


class ResolvedCompany(CompanyCandidate):
    """描述已经唯一确认的标准企业身份。"""


class CompanyResolutionResult(BaseModel):
    """描述企业身份解析结果及其来源和质量状态。"""

    status: ResolutionStatus
    reference: CompanyReference
    company: ResolvedCompany | None = None
    candidates: list[CompanyCandidate] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    data_quality: DataQuality
    latency: LatencyBreakdown


class CompanySearchResult(BaseModel):
    """描述受限企业名称或简称搜索结果。"""

    query: str
    status: ResolutionStatus
    candidates: list[CompanyCandidate]
    sources: list[Source]
    data_quality: DataQuality
    latency: LatencyBreakdown


class Pagination(BaseModel):
    """描述确定页码、上限和截断状态。"""

    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    returned: int = Field(ge=0)
    has_more: bool = False
    truncated: bool = False


class StructuredQueryRequest(BaseModel):
    """描述受控结构化查询服务输入。"""

    eid: str = Field(min_length=1, max_length=64)
    query_type: StructuredQueryType
    start_date: date | None = None
    end_date: date | None = None
    page: int = Field(default=1, ge=1, le=10000)
    page_size: int = Field(default=20, ge=1, le=100)
    sort_order: SortOrder = SortOrder.DESC
    indicator_name: str | None = Field(default=None, max_length=128)

    @field_validator("eid", "indicator_name", mode="before")
    @classmethod
    def normalize_query_text(cls, value):
        """去除结构化查询文本字段两端空白。"""
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @model_validator(mode="after")
    def validate_date_range(self) -> StructuredQueryRequest:
        """拒绝结束日期早于开始日期的查询。"""
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date 不能早于 start_date")
        return self


class StructuredQueryResult(BaseModel):
    """描述结构化查询返回的实体、事实、关系和来源。"""

    query_type: StructuredQueryType
    eid: str
    entities: list[Entity] = Field(default_factory=list)
    facts: list[Fact] = Field(default_factory=list)
    relations: list[Relation] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    data_quality: DataQuality
    pagination: Pagination
    latency: LatencyBreakdown


def build_source_id(table: str, record_key: str) -> str:
    """根据来源系统、表和稳定记录键生成不可逆来源标识。"""
    payload = f"guangdong_enterprise_mysql:{table}:{record_key}".encode()
    return f"source:{hashlib.sha256(payload).hexdigest()[:24]}"


def build_stable_id(prefix: str, *parts: object) -> str:
    """根据业务前缀和稳定字段生成通用不可逆标识。"""
    payload = ":".join(str(part) for part in parts).encode()
    return f"{prefix}:{hashlib.sha256(payload).hexdigest()[:24]}"
