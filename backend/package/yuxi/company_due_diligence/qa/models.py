"""企业尽调自然语言问答请求、计划和统一响应契约。"""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator

from yuxi.company_due_diligence.evidence.models import EvidenceDocumentType
from yuxi.company_due_diligence.graph.schema import GraphRelationType
from yuxi.company_due_diligence.models import (
    CompanyReference,
    DataQuality,
    Entity,
    Evidence,
    Fact,
    LatencyBreakdown,
    Path,
    Relation,
    ResolutionStatus,
    ResolvedCompany,
    Source,
    StructuredQueryType,
)


class QuestionMode(StrEnum):
    """描述企业查询当前支持的交互模式。"""

    QA = "qa"


class QuestionType(StrEnum):
    """描述受控问题分类结果。"""

    STRUCTURED_FIELD = "structured_field"
    STRUCTURED_LIST = "structured_list"
    GRAPH_PATH = "graph_path"
    TEXT_EVIDENCE = "text_evidence"
    MIXED = "mixed"


class QueryStepStatus(StrEnum):
    """描述查询计划中单个受控步骤的执行状态。"""

    COMPLETE = "complete"
    NO_DATA = "no_data"
    UNAVAILABLE = "unavailable"
    SKIPPED = "skipped"


class CompanyQuestionRequest(BaseModel):
    """描述企业简单问答或最多三跳关系问题。"""

    company_ref: CompanyReference
    target_company_ref: CompanyReference | None = None
    question: str = Field(min_length=1, max_length=2000)
    mode: QuestionMode = QuestionMode.QA
    as_of: date | None = None
    hop_limit: int = Field(default=3, ge=1, le=3)
    top_k: int = Field(default=10, ge=1, le=50)
    include_low_confidence: bool = False

    @field_validator("question", mode="before")
    @classmethod
    def normalize_question(cls, value):
        """去除问题两端空白并拒绝空问题。"""
        return str(value).strip()


class QueryPlan(BaseModel):
    """描述只允许调用白名单服务的确定性查询计划。"""

    question_type: QuestionType
    structured_queries: list[StructuredQueryType] = Field(default_factory=list)
    use_graph: bool = False
    graph_relation_types: list[GraphRelationType] = Field(default_factory=list)
    evidence_types: list[EvidenceDocumentType] = Field(default_factory=list)


class QueryStep(BaseModel):
    """描述问答执行过的工具、状态和结果规模。"""

    source: str
    operation: str
    status: QueryStepStatus
    item_count: int = Field(default=0, ge=0)
    message: str | None = None


class CompanyQuestionResponse(BaseModel):
    """描述包含事实、关系、路径、证据和来源的问答响应。"""

    question: str
    resolution_status: ResolutionStatus
    company: ResolvedCompany | None = None
    target_company: ResolvedCompany | None = None
    question_type: QuestionType | None = None
    plan: QueryPlan | None = None
    answer: str
    entities: list[Entity] = Field(default_factory=list)
    facts: list[Fact] = Field(default_factory=list)
    relations: list[Relation] = Field(default_factory=list)
    paths: list[Path] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    data_quality: DataQuality
    latency: LatencyBreakdown
    steps: list[QueryStep] = Field(default_factory=list)
    projection_versions: dict[str, str] = Field(default_factory=dict)
