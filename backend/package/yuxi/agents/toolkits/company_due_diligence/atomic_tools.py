"""企业尽调原子工具：只适配既有领域服务，不参与 Agent 编排。"""

from datetime import date
from typing import Annotated

from langchain_core.tools import ToolException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from yuxi.agents.toolkits.registry import tool
from yuxi.company_due_diligence.agent.models import InvestigationTopic
from yuxi.company_due_diligence.entity_resolution import entity_resolution_service
from yuxi.company_due_diligence.evidence.models import EvidenceDocumentType, EvidenceQuery
from yuxi.company_due_diligence.evidence.query_service import evidence_query_service
from yuxi.company_due_diligence.graph.query_service import graph_path_query_service
from yuxi.company_due_diligence.graph.repository import GraphUnavailableError
from yuxi.company_due_diligence.graph.schema import GraphPathQuery, GraphRelationType
from yuxi.company_due_diligence.models import (
    CompanyReference,
    ResolutionStatus,
    SourceStatus,
    StructuredQueryRequest,
    StructuredQueryType,
)
from yuxi.company_due_diligence.report.intent import sections_for_topics
from yuxi.company_due_diligence.report.models import DueDiligenceReportRequest
from yuxi.company_due_diligence.report.service import due_diligence_report_service
from yuxi.company_due_diligence.structured_query_service import structured_query_service
from yuxi.services.task_service import tasker

_LIST_ALL_QUERY_TYPES = {
    StructuredQueryType.CURRENT_SHAREHOLDERS,
    StructuredQueryType.HISTORICAL_SHAREHOLDERS,
    StructuredQueryType.SHAREHOLDER_CONTRIBUTIONS,
}

ConfirmedEid = Annotated[
    str,
    Field(
        min_length=1, max_length=64, description="仅复用 cdd_resolve_company 唯一解析结果中的 eid，不得猜测或自行生成"
    ),
]


class CddToolInput(BaseModel):
    """把工具边界的参数错误转换为可观察的工具失败。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    @model_validator(mode="wrap")
    @classmethod
    def validate_tool_arguments(cls, value, handler):
        """保留 JSON Schema 约束，并让 LangChain 生成 error ToolMessage。"""
        try:
            return handler(value)
        except ValidationError as exc:
            raise ToolException(f"参数错误：{exc}") from exc


class ResolveCompanyInput(CddToolInput):
    """企业名称、信用代码、股票代码三选一。"""

    name: str | None = Field(default=None, min_length=1, max_length=256, description="企业全称或待确认名称")
    credit_code: str | None = Field(default=None, min_length=1, max_length=32, description="统一社会信用代码")
    stock_code: str | None = Field(default=None, min_length=1, max_length=32, description="股票代码")

    @model_validator(mode="after")
    def require_one_identifier(self):
        """拒绝缺少身份线索或同时提供多个标识。"""
        if sum(value is not None for value in (self.name, self.credit_code, self.stock_code)) != 1:
            raise ToolException("参数错误：name、credit_code、stock_code 必须三选一")
        return self


class DatedCompanyInput(CddToolInput):
    """绑定唯一企业和可选起止日期。"""

    eid: ConfirmedEid
    start_date: date | None = Field(default=None, description="业务开始日期（含）")
    end_date: date | None = Field(default=None, description="业务结束日期（含）")

    @model_validator(mode="after")
    def validate_date_range(self):
        """拒绝倒置时间范围。"""
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ToolException("参数错误：end_date 不能早于 start_date")
        return self


class QueryFactsInput(DatedCompanyInput):
    """只暴露十七类白名单查询和默认前十条/全部的选择。"""

    query_type: StructuredQueryType = Field(
        description=(
            "白名单查询：enterprise_profile 工商信息；registered_capital 注册资本；legal_representative 法定代表人；"
            "business_status 经营状态；current_shareholders 当前股东；historical_shareholders 历史股东；"
            "shareholder_contributions 股东出资；products 产品；financing_history 融资历史；"
            "administrative_penalties 行政处罚；environmental_penalties 环保处罚；abnormal_operations 经营异常；"
            "licenses 许可；patent_summary 专利；trademark_summary 商标；copyright_summary 著作权；"
            "financial_metric_series 财务指标序列"
        )
    )
    list_all: bool = Field(
        default=False,
        description="仅股东类查询可用：默认返回前 10 条，用户明确要求全部股东时为 true，按既有分页取全",
    )

    @model_validator(mode="after")
    def limit_list_all_to_shareholders(self):
        """“全部列出”只对股东类查询开放：单家企业的专利关系最多约 32 万条、许可约 1.3 万条，
        逐页取全会把数十万条事实塞进模型上下文；股东类最多约 186 条（2026-10-21 源表统计）。"""
        if self.list_all and self.query_type not in _LIST_ALL_QUERY_TYPES:
            raise ToolException(f"参数错误：list_all 只支持股东类查询，{self.query_type.value} 请使用默认前 10 条")
        return self


class QueryRelationsInput(CddToolInput):
    """最多三跳的关系类型白名单查询。"""

    start_eid: ConfirmedEid
    target_eid: ConfirmedEid | None = None
    relation_types: list[GraphRelationType] = Field(
        default_factory=lambda: list(GraphRelationType), min_length=1, max_length=len(GraphRelationType)
    )
    max_hops: int = Field(default=3, ge=1, le=3, description="最大关系跳数，不超过 3")


class SearchEvidenceInput(DatedCompanyInput):
    """企业范围内的补充证据检索参数。"""

    query: str = Field(min_length=1, max_length=2000, description="检索语句")
    document_types: list[EvidenceDocumentType] = Field(
        default_factory=lambda: list(EvidenceDocumentType), min_length=1, max_length=len(EvidenceDocumentType)
    )
    top_k: int = Field(default=10, ge=1, le=50, description="召回片段数量")


class CreateReportInput(CddToolInput):
    """提交指定专题或默认完整尽调报告。"""

    eid: ConfirmedEid
    topics: list[InvestigationTopic] = Field(
        default_factory=lambda: [InvestigationTopic.FULL_DUE_DILIGENCE],
        min_length=1,
        description="报告专题；full_due_diligence 表示完整报告，默认完整报告",
    )


@tool(category="company_due_diligence", display_name="解析企业身份", args_schema=ResolveCompanyInput)
async def cdd_resolve_company(
    name: str | None = None, credit_code: str | None = None, stock_code: str | None = None
) -> dict:
    """解析唯一企业身份；歧义只给候选，由用户确认，不擅自选择。"""
    result = await entity_resolution_service.resolve(
        CompanyReference(name=name, credit_code=credit_code, stock_code=stock_code)
    )
    if result.status == ResolutionStatus.SOURCE_UNAVAILABLE:
        raise ToolException("source_unavailable：企业身份来源不可用")
    identity_fields = {"eid", "name", "credit_code", "stock_code", "source_ids"}
    return {
        "status": result.status.value,
        "company": result.company.model_dump(mode="json", include=identity_fields) if result.company else None,
        "candidates": [item.model_dump(mode="json", include=identity_fields) for item in result.candidates],
        "sources": _sources(result.sources),
    }


@tool(category="company_due_diligence", display_name="查询企业事实", args_schema=QueryFactsInput)
async def cdd_query_facts(
    eid: str,
    query_type: StructuredQueryType,
    start_date: date | None = None,
    end_date: date | None = None,
    list_all: bool = False,
) -> dict:
    """按白名单查询事实；保留事实、主体和来源标识，不执行自由 SQL。"""
    await _require_company(eid)
    request = StructuredQueryRequest(
        eid=eid, query_type=query_type, start_date=start_date, end_date=end_date, page_size=100 if list_all else 10
    )
    results = []
    while True:
        try:
            result = await structured_query_service.query(request)
        except Exception as exc:
            raise ToolException(f"企业事实查询失败：{type(exc).__name__}") from exc
        _require_available(result.data_quality)
        results.append(result)
        if not list_all or not result.pagination.has_more:
            break
        # 逐页走既有查询接口，不在工具内复制排序、过滤或事实归一化逻辑。
        request = StructuredQueryRequest(**{**request.model_dump(), "page": request.page + 1})
    return {
        "eid": eid,
        "query_type": result.query_type.value,
        "facts": list({f.fact_id: f.model_dump(mode="json") for r in results for f in r.facts}.values()),
        "entities": list(
            {
                e.entity_id: e.model_dump(mode="json", include={"entity_id", "entity_type", "name", "source_ids"})
                for r in results
                for e in r.entities
            }.values()
        ),
        "relations": list(
            {edge.relation_id: edge.model_dump(mode="json") for r in results for edge in r.relations}.values()
        ),
        "sources": _sources([s for r in results for s in r.sources]),
        "truncated": result.pagination.truncated,
        "warnings": list(dict.fromkeys(w for r in results for w in r.data_quality.warnings)),
    }


@tool(category="company_due_diligence", display_name="查询企业关系", args_schema=QueryRelationsInput)
async def cdd_query_relations(
    start_eid: str,
    target_eid: str | None = None,
    relation_types: list[GraphRelationType] | None = None,
    max_hops: int = 3,
) -> dict:
    """查询最多三跳的路径，保留节点、边方向、置信度和来源。"""
    await _require_company(start_eid)
    if target_eid:
        await _require_company(target_eid)
    request = GraphPathQuery(
        start_eid=start_eid,
        target_eid=target_eid,
        max_hops=max_hops,
        **({"relation_types": relation_types} if relation_types is not None else {}),
    )
    try:
        result = await graph_path_query_service.query(request)
    except GraphUnavailableError as exc:
        raise ToolException(f"source_unavailable：关系来源不可用；{exc}") from exc
    except Exception as exc:
        raise ToolException(f"企业关系查询失败：{type(exc).__name__}") from exc
    _require_available(result.data_quality)
    return {
        "projection_version": result.projection_version,
        "paths": [path.model_dump(mode="json") for path in result.paths],
        "warnings": result.data_quality.warnings,
    }


@tool(category="company_due_diligence", display_name="检索企业证据", args_schema=SearchEvidenceInput)
async def cdd_search_evidence(
    eid: str,
    query: str,
    document_types: list[EvidenceDocumentType] | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    top_k: int = 10,
) -> dict:
    """检索绑定企业的证据片段；仅作补充，不能替代确定性事实。"""
    await _require_company(eid)
    request = EvidenceQuery(
        eid=eid,
        query=query,
        start_date=start_date,
        end_date=end_date,
        top_k=top_k,
        **({"document_types": document_types} if document_types is not None else {}),
    )
    result = await evidence_query_service.query(request)
    _require_available(result.data_quality)
    if result.projection_version is None:
        raise ToolException("source_unavailable：文本证据来源不可用，尚未发布索引")
    return {
        "supplementary_only": True,
        "projection_version": result.projection_version,
        "evidence": [item.model_dump(mode="json", exclude={"projection_version"}) for item in result.evidence],
        "sources": _sources(result.sources),
        "warnings": result.data_quality.warnings,
    }


@tool(category="company_due_diligence", display_name="创建企业报告", args_schema=CreateReportInput)
async def cdd_create_report(eid: str, topics: list[InvestigationTopic] | None = None) -> dict:
    """沿既有持久化和 ARQ 链路异步生成专题或完整报告，只返回任务状态。"""
    await _require_company(eid)
    request = DueDiligenceReportRequest(
        company_ref={"eid": eid},
        sections=sections_for_topics(topics if topics is not None else [InvestigationTopic.FULL_DUE_DILIGENCE]),
    )
    try:
        task = await due_diligence_report_service.create(request, tasker)
    except Exception as exc:
        raise ToolException(f"报告提交失败：{exc}") from exc
    return {"task_id": task.id, "status": task.status}


async def _require_company(eid: str) -> None:
    """让已有解析服务确认 eid 唯一存在，不保存跨工具身份状态。"""
    result = await entity_resolution_service.resolve(CompanyReference(eid=eid))
    if result.status != ResolutionStatus.RESOLVED or result.company is None:
        raise ToolException(f"{result.status.value}：eid 无法唯一解析，请先调用 cdd_resolve_company 确认企业")


def _require_available(quality) -> None:
    """禁止将数据源失败包装为空结果。"""
    if quality.source_status == SourceStatus.UNAVAILABLE:
        raise ToolException("source_unavailable：来源不可用；" + "；".join(quality.warnings))


def _sources(sources) -> list[dict]:
    """精简并去重来源，保留回查必需的稳定键和业务日期。"""
    fields = {"source_id", "source_system", "table", "record_key", "business_date", "source_url"}
    return list({s.source_id: s.model_dump(mode="json", include=fields) for s in sources}.values())
