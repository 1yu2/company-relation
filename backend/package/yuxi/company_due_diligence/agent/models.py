"""企业尽调领域智能体统一输入、意图和输出契约。"""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator, model_validator

from yuxi.company_due_diligence.models import (
    CompanyCandidate,
    CompanyReference,
    DataQuality,
    Entity,
    Evidence,
    Fact,
    LatencyBreakdown,
    Path,
    Relation,
    RequestScopeStatus,
    Source,
)
from yuxi.company_due_diligence.qa.models import QueryPlan, QueryStep


class InvestigationStatus(StrEnum):
    """描述领域智能体请求的最终状态。"""

    COMPLETE = "complete"
    PARTIAL = "partial"
    NO_DATA = "no_data"
    ACCEPTED = "accepted"
    CLARIFICATION = "clarification"
    FAILED = "failed"
    GLOBAL_SCOPE = "global_scope"
    NEEDS_CONTEXT = "needs_context"
    NOT_FOUND = "not_found"
    SOURCE_UNAVAILABLE = "source_unavailable"


class InvestigationTopic(StrEnum):
    """描述用户真正关注的企业调查主题。"""

    PROFILE = "profile"
    SHAREHOLDERS = "shareholders"
    RELATIONS = "relations"
    PRODUCTS = "products"
    FINANCING = "financing"
    FINANCIALS = "financials"
    RISKS = "risks"
    INTELLECTUAL_PROPERTY = "intellectual_property"
    NEWS = "news"
    FULL_DUE_DILIGENCE = "full_due_diligence"


class PresentationType(StrEnum):
    """描述前端应采用的结果展示形式。"""

    DIRECT_ANSWER = "direct_answer"
    SUMMARY_WITH_LIST = "summary_with_list"
    RELATION_RESULT = "relation_result"
    RISK_SUMMARY = "risk_summary"
    TOPIC_REPORT = "topic_report"
    FULL_REPORT = "full_report"
    CLARIFICATION = "clarification"


class DeliveryMethod(StrEnum):
    """描述同步响应或异步任务交付方式。"""

    SYNC = "sync"
    ASYNC_TASK = "async_task"


class InvestigationDepth(StrEnum):
    """描述用户要求的调查详细程度。"""

    CONCISE = "concise"
    STANDARD = "standard"
    DETAILED = "detailed"


class TimeScopeType(StrEnum):
    """描述调查时间范围的解析类型。"""

    LATEST_AVAILABLE = "latest_available"
    CURRENT = "current"
    RECENT_YEARS = "recent_years"
    DATE_RANGE = "date_range"
    AS_OF = "as_of"


class InvestigationShortcut(StrEnum):
    """描述工作台可提交的标准调查快捷入口。"""

    GENERATE_FULL_REPORT = "generate_full_due_diligence_report"


class CompanyRole(StrEnum):
    """描述企业在本次调查中的角色。"""

    PRIMARY = "primary_company"
    TARGET = "target_company"


class CompanyContext(BaseModel):
    """描述由搜索确认或对话继承的企业上下文。"""

    eid: str | None = Field(default=None, min_length=1, max_length=64)
    name: str | None = Field(default=None, min_length=1, max_length=256)
    credit_code: str | None = Field(default=None, min_length=1, max_length=32)
    stock_code: str | None = Field(default=None, min_length=1, max_length=32)

    @field_validator("eid", "name", "credit_code", "stock_code", mode="before")
    @classmethod
    def normalize_text(cls, value):
        """去除企业上下文字段两端空白。"""
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @model_validator(mode="after")
    def require_identifier(self) -> CompanyContext:
        """确保企业上下文包含至少一种可解析标识。"""
        if not any((self.eid, self.name, self.credit_code, self.stock_code)):
            raise ValueError("企业上下文至少包含名称、eid、信用代码或股票代码之一")
        return self

    def to_reference(self) -> CompanyReference:
        """转换为确定性企业解析服务输入。"""
        return CompanyReference(
            eid=self.eid,
            name=self.name,
            credit_code=self.credit_code,
            stock_code=self.stock_code,
        )


class IntentEntity(BaseModel):
    """描述已经确认并进入内部意图的企业主体。"""

    eid: str
    name: str
    role: CompanyRole


class TimeScope(BaseModel):
    """描述已经归一化的调查时间范围。"""

    type: TimeScopeType = TimeScopeType.LATEST_AVAILABLE
    years: int | None = Field(default=None, ge=1, le=20)
    start_date: date | None = None
    end_date: date | None = None

    @model_validator(mode="after")
    def validate_range(self) -> TimeScope:
        """拒绝倒置日期范围和缺少年数的近年范围。"""
        if self.type == TimeScopeType.RECENT_YEARS and self.years is None:
            raise ValueError("recent_years 必须包含 years")
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date 不能早于 start_date")
        return self


class InvestigationIntent(BaseModel):
    """描述领域智能体已经理解的业务调查意图。"""

    topics: list[InvestigationTopic] = Field(min_length=1)
    presentation: PresentationType
    delivery: DeliveryMethod
    time_scope: TimeScope = Field(default_factory=TimeScope)
    depth: InvestigationDepth = InvestigationDepth.STANDARD
    evidence_required: bool = False

    @field_validator("topics")
    @classmethod
    def deduplicate_topics(cls, value: list[InvestigationTopic]) -> list[InvestigationTopic]:
        """保持主题顺序并去重。"""
        return list(dict.fromkeys(value))


class InvestigationConstraints(BaseModel):
    """描述由系统控制的查询范围和置信度约束。"""

    hop_limit: int = Field(default=3, ge=1, le=3)
    top_k: int = Field(default=10, ge=1, le=50)
    include_low_confidence: bool = False


class InvestigationPlan(BaseModel):
    """描述业务意图对应的受控工具计划。"""

    intent: InvestigationIntent
    query_plan: QueryPlan | None = None


class UnderstoodRequest(BaseModel):
    """描述返回给用户确认的调查理解结果。"""

    topics: list[InvestigationTopic]
    presentation: PresentationType
    delivery: DeliveryMethod
    time_scope: TimeScope
    depth: InvestigationDepth


class Presentation(BaseModel):
    """描述前端应动态组合的展示组件。"""

    type: PresentationType
    components: list[str] = Field(default_factory=list)


class InvestigationTask(BaseModel):
    """描述异步报告任务的最小交付状态。"""

    id: str
    status: str
    progress: float = Field(default=0, ge=0, le=100)


class ClaimType(StrEnum):
    """描述 Claim Ledger 中被验证的结果类型。"""

    FACT = "fact"
    RELATION = "relation"
    PATH = "path"
    EVIDENCE = "evidence"


class Claim(BaseModel):
    """描述一条最终结果与其来源之间的可验证映射。"""

    claim_id: str
    claim_type: ClaimType
    result_id: str
    source_ids: list[str] = Field(min_length=1)


class ClaimLedger(BaseModel):
    """描述最终输出中已通过来源完整性校验的声明集合。"""

    claims: list[Claim] = Field(default_factory=list)
    validated: bool = True


class CompanyInvestigationRequest(BaseModel):
    """描述普通用户提交的企业上下文和自然语言调查要求。"""

    conversation_id: str | None = Field(default=None, max_length=128)
    company_context: CompanyContext | None = None
    target_company_context: CompanyContext | None = None
    message: str | None = Field(default=None, max_length=2000)
    question: str | None = Field(default=None, max_length=2000)
    shortcut: InvestigationShortcut | None = None
    start_date: date | None = None
    as_of: date | None = None
    hop_limit: int = Field(default=3, ge=1, le=3)
    top_k: int = Field(default=10, ge=1, le=50)
    include_low_confidence: bool = False

    @field_validator("conversation_id", "message", "question", mode="before")
    @classmethod
    def normalize_optional_text(cls, value):
        """去除可选文本两端空白。"""
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @model_validator(mode="after")
    def validate_request(self) -> CompanyInvestigationRequest:
        """确保请求包含调查文本或标准快捷入口。"""
        if self.message is None and self.question is None and self.shortcut is None:
            raise ValueError("question、message 和 shortcut 至少提供一个")
        if self.start_date and self.as_of and self.as_of < self.start_date:
            raise ValueError("as_of 不能早于 start_date")
        return self


class CompanyInvestigationResponse(BaseModel):
    """描述从意图理解到动态展示的统一领域智能体响应。"""

    status: InvestigationStatus
    scope_status: RequestScopeStatus = RequestScopeStatus.RESOLVED
    understood_request: UnderstoodRequest
    company_context: CompanyContext | None = None
    target_company_context: CompanyContext | None = None
    candidates: list[CompanyCandidate] = Field(default_factory=list)
    global_result: dict | None = None
    entities: list[Entity] = Field(default_factory=list)
    summary: str
    findings: list[str] = Field(default_factory=list)
    facts: list[Fact] = Field(default_factory=list)
    relations: list[Relation] = Field(default_factory=list)
    paths: list[Path] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    claim_ledger: ClaimLedger = Field(default_factory=ClaimLedger)
    data_quality: DataQuality
    presentation: Presentation
    task: InvestigationTask | None = None
    plan: QueryPlan | None = None
    steps: list[QueryStep] = Field(default_factory=list)
    projection_versions: dict[str, str] = Field(default_factory=dict)
    latency: LatencyBreakdown = Field(default_factory=LatencyBreakdown)
