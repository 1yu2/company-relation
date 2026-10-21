"""企业尽调统一问答服务和模板化事实答案。"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime

from yuxi.company_due_diligence.entity_mentions import extract_company_references
from yuxi.company_due_diligence.entity_resolution import EntityResolutionService, entity_resolution_service
from yuxi.company_due_diligence.external.models import ExternalCompanyDataRequest, ExternalCompanyDataResponse
from yuxi.company_due_diligence.external.service import ExternalCompanyDataService, external_company_data_service
from yuxi.company_due_diligence.models import (
    ConfidenceLevel,
    CoverageStatus,
    DataQuality,
    Entity,
    Evidence,
    Fact,
    LatencyBreakdown,
    Path,
    Relation,
    ResolutionStatus,
    RequestScopeStatus,
    Source,
    SourceStatus,
    build_source_id,
)
from yuxi.company_due_diligence.qa.graph import QAWorkflow
from yuxi.company_due_diligence.qa.global_service import GlobalQuestionService, global_question_service
from yuxi.company_due_diligence.qa.models import (
    CompanyQuestionRequest,
    CompanyQuestionResponse,
    QueryPlan,
    QueryStep,
    QueryStepStatus,
)
from yuxi.company_due_diligence.qa.planner import QuestionPlanner, question_planner


class SourceIntegrityError(RuntimeError):
    """表示问答数据包中存在无法回查的事实或关系来源。"""


# 允许触发外部补数的覆盖状态
_FALLBACK_ELIGIBLE_STATUSES = {
    CoverageStatus.PARTIAL,
    CoverageStatus.MISSING,
    CoverageStatus.STALE,
    CoverageStatus.CONFLICT,
    CoverageStatus.UNSUPPORTED,
}


class CompanyQuestionService:
    """解析企业、执行受控计划并生成无需 LLM 的可追溯简答。"""

    def __init__(
        self,
        resolver: EntityResolutionService | None = None,
        planner: QuestionPlanner | None = None,
        workflow: QAWorkflow | None = None,
        global_service: GlobalQuestionService | None = None,
        external_service: ExternalCompanyDataService | None = None,
    ) -> None:
        self._resolver = resolver or entity_resolution_service
        self._planner = planner or question_planner
        self._workflow = workflow or QAWorkflow()
        self._global_service = global_service or global_question_service
        self._external_service = external_service or external_company_data_service

    async def query(
        self,
        request: CompanyQuestionRequest,
        *,
        plan: QueryPlan | None = None,
    ) -> CompanyQuestionResponse:
        """执行企业问答；可复用领域智能体已校验的受控计划。"""
        started_at = time.perf_counter()
        resolved_plan = plan or self._planner.plan(request.question)
        references = extract_company_references(request.question)
        if references:
            updates = {"company_ref": references[0]}
            if request.target_company_ref is None and resolved_plan.use_graph and len(references) > 1:
                updates["target_company_ref"] = references[1]
            request = request.model_copy(update=updates)
        elif request.company_ref is None:
            global_response = await self._global_service.query(request.question, top_k=request.top_k)
            if global_response is not None:
                return _assemble_global_response(request, global_response, started_at)
            return _needs_context_response(request, started_at)

        resolution = await self._resolver.resolve(request.company_ref)
        if resolution.status != ResolutionStatus.RESOLVED or resolution.company is None:
            return _resolution_rejection(request, resolution, started_at, "企业无法唯一解析，未执行后续查询。")

        target_resolution = None
        if request.target_company_ref is not None:
            target_resolution = await self._resolver.resolve(request.target_company_ref)
            if target_resolution.status != ResolutionStatus.RESOLVED or target_resolution.company is None:
                return _resolution_rejection(
                    request,
                    target_resolution,
                    started_at,
                    "目标企业无法唯一解析，未执行后续查询。",
                )

        workflow_result = await self._workflow.execute(
            eid=resolution.company.eid,
            target_eid=target_resolution.company.eid if target_resolution and target_resolution.company else None,
            request=request,
            plan=resolved_plan,
        )
        external_response = await self._maybe_query_external(
            request=request,
            company=resolution.company,
            plan=resolved_plan,
            structured_results=workflow_result.structured,
        )
        return _assemble_response(
            request=request,
            plan=resolved_plan,
            company=resolution.company,
            target_company=target_resolution.company if target_resolution else None,
            resolution_sources=resolution.sources + (target_resolution.sources if target_resolution else []),
            resolution_ms=(
                resolution.latency.total_ms + (target_resolution.latency.total_ms if target_resolution else 0)
            ),
            workflow_result=workflow_result,
            external_response=external_response,
            started_at=started_at,
        )

    async def _maybe_query_external(
        self,
        *,
        request: CompanyQuestionRequest,
        company,
        plan: QueryPlan,
        structured_results: list,
    ) -> ExternalCompanyDataResponse | None:
        """当内部结果存在缺口且配置启用时，触发外部数据补数。"""
        if not request.enable_external_fallback:
            return None
        if not plan.structured_queries:
            return None

        config = await _load_external_data_config()
        enabled_providers = _enabled_external_providers(config)
        if not enabled_providers:
            return None

        gaps = self._external_service.detect_topic_gaps(
            plan=plan,
            structured_results=structured_results,
            stale_threshold_days=config.get("stale_threshold_days", 180),
        )
        fallback_topics = [topic for topic, status in gaps.items() if status in _FALLBACK_ELIGIBLE_STATUSES]
        if not fallback_topics:
            return None

        return await self._external_service.query(
            ExternalCompanyDataRequest(
                company_ref={
                    "eid": company.eid,
                    "name": company.name,
                    "credit_code": company.credit_code,
                    "stock_code": company.stock_code,
                },
                topics=fallback_topics,
                start_date=request.start_date,
                end_date=request.as_of,
            ),
            enabled_providers=enabled_providers,
            timeout_seconds=_config_value(config, "default_timeout_seconds", 30),
            cache_ttl_seconds=_config_value(config, "cache_ttl_seconds", 300),
            rate_limit_per_minute=_config_value(config, "rate_limit_per_minute", 60),
        )


async def _load_external_data_config() -> dict:
    """读取外部数据补数配置；失败时返回空字典使用默认值。"""
    try:
        from yuxi.config.options import external_company_data_opts

        return await external_company_data_opts.get()
    except Exception:
        return {}


def _enabled_external_providers(config: dict) -> list[str]:
    """根据配置返回启用的 Provider 列表。"""
    enabled = []
    if config.get("akshare_enabled", False):
        enabled.append("akshare")
    if config.get("public_disclosure_enabled", False):
        enabled.append("public_disclosure")
    if config.get("financing_enabled", False):
        enabled.append("financing")
    return enabled


def _config_value(config: dict, key: str, default):
    """读取允许为 0 或 False 的配置值。"""
    value = config.get(key)
    return default if value is None else value


def _assemble_response(
    *,
    request,
    plan: QueryPlan,
    company,
    target_company,
    resolution_sources,
    resolution_ms: float,
    workflow_result,
    external_response: ExternalCompanyDataResponse | None = None,
    started_at: float,
) -> CompanyQuestionResponse:
    """合并内部查询、外部补数和三个底层来源，校验完整性、冲突和阶段耗时。"""
    entities = {company.eid: _company_entity(company)}
    if target_company is not None:
        entities[target_company.eid] = _company_entity(target_company)
    facts: dict[str, Fact] = {}
    relations: dict[str, Relation] = {}
    paths: dict[str, Path] = {}
    evidence: dict[str, Evidence] = {}
    sources = {source.source_id: source for source in resolution_sources}
    warnings: list[str] = []
    missing_fields: list[str] = []
    stale_fields: list[str] = []
    inherited_conflicts: list[str] = []
    projection_versions: dict[str, str] = {}
    mysql_ms = 0.0
    neo4j_ms = 0.0
    milvus_ms = 0.0

    for result in workflow_result.structured:
        entities.update({item.entity_id: item for item in result.entities})
        facts.update({item.fact_id: item for item in result.facts})
        relations.update({item.relation_id: item for item in result.relations})
        sources.update({item.source_id: item for item in result.sources})
        warnings.extend(result.data_quality.warnings)
        missing_fields.extend(result.data_quality.missing_fields)
        stale_fields.extend(result.data_quality.stale_fields)
        inherited_conflicts.extend(result.data_quality.conflicts)
        mysql_ms += result.latency.mysql_ms

    if workflow_result.graph is not None:
        for path in workflow_result.graph.paths:
            paths[path.path_id] = path
            entities.update({item.entity_id: item for item in path.nodes})
            relations.update({item.relation_id: item for item in path.edges})
            for relation in path.edges:
                sources.update(_graph_sources(relation))
        projection_versions["neo4j"] = workflow_result.graph.projection_version
        warnings.extend(workflow_result.graph.data_quality.warnings)
        missing_fields.extend(workflow_result.graph.data_quality.missing_fields)
        stale_fields.extend(workflow_result.graph.data_quality.stale_fields)
        inherited_conflicts.extend(workflow_result.graph.data_quality.conflicts)
        neo4j_ms += workflow_result.graph.latency.neo4j_ms

    if workflow_result.evidence is not None:
        evidence.update({item.evidence_id: item for item in workflow_result.evidence.evidence})
        sources.update({item.source_id: item for item in workflow_result.evidence.sources})
        if workflow_result.evidence.projection_version:
            projection_versions["milvus"] = workflow_result.evidence.projection_version
        warnings.extend(workflow_result.evidence.data_quality.warnings)
        missing_fields.extend(workflow_result.evidence.data_quality.missing_fields)
        stale_fields.extend(workflow_result.evidence.data_quality.stale_fields)
        inherited_conflicts.extend(workflow_result.evidence.data_quality.conflicts)
        milvus_ms += workflow_result.evidence.latency.milvus_ms

    external_latency_ms = 0.0
    if external_response is not None:
        for item in external_response.facts:
            existing = facts.get(item.fact_id)
            if existing is None:
                facts[item.fact_id] = item
            elif existing.normalized_value != item.normalized_value:
                inherited_conflicts.append(f"{item.subject_id}:{item.fact_type}: internal vs external")
        sources.update({item.source_id: item for item in external_response.sources})
        evidence.update({item.evidence_id: item for item in external_response.evidence})
        inherited_conflicts.extend(external_response.conflicts)
        if external_response.missing_topics:
            warnings.append(f"外部数据源未覆盖以下主题：{', '.join(external_response.missing_topics)}")
        workflow_result.steps.append(
            QueryStep(
                source="external",
                operation="fallback",
                status=(
                    QueryStepStatus.COMPLETE
                    if external_response.facts
                    else QueryStepStatus.UNAVAILABLE
                    if any(item.status in {"error", "unavailable"} for item in external_response.provider_results)
                    else QueryStepStatus.NO_DATA
                ),
                item_count=len(external_response.facts),
                message=_external_step_message(external_response),
            )
        )
        external_latency_ms = external_response.external_latency_ms

    _validate_source_integrity(facts.values(), relations.values(), paths.values(), evidence.values(), sources)
    conflicts = list(dict.fromkeys(inherited_conflicts + _find_fact_conflicts(facts.values())))
    unavailable_steps = [step for step in workflow_result.steps if step.status.value == "unavailable"]
    has_results = bool(facts or relations or paths or evidence)
    source_status = (
        SourceStatus.PARTIAL
        if unavailable_steps and has_results
        else SourceStatus.UNAVAILABLE
        if unavailable_steps
        else SourceStatus.AVAILABLE
    )
    minimum_confidence = min(
        [relation.confidence for relation in relations.values()] + [path.confidence for path in paths.values()],
        key=_confidence_rank,
        default=None,
    )
    answer = _render_answer(company, facts.values(), relations.values(), paths.values(), evidence.values())
    total_ms = round((time.perf_counter() - started_at) * 1000, 3)
    return CompanyQuestionResponse(
        question=request.question,
        scope_status=RequestScopeStatus.RESOLVED,
        resolution_status=ResolutionStatus.RESOLVED,
        company=company,
        target_company=target_company,
        question_type=plan.question_type,
        plan=plan,
        answer=answer,
        entities=list(entities.values()),
        facts=list(facts.values()),
        relations=list(relations.values()),
        paths=list(paths.values()),
        evidence=list(evidence.values()),
        sources=list(sources.values()),
        data_quality=DataQuality(
            source_status=source_status,
            missing_fields=list(dict.fromkeys(missing_fields)),
            stale_fields=list(dict.fromkeys(stale_fields)),
            conflicts=conflicts,
            warnings=list(dict.fromkeys(warnings + [step.message for step in unavailable_steps if step.message])),
            minimum_confidence=minimum_confidence,
        ),
        latency=LatencyBreakdown(
            resolution_ms=resolution_ms,
            mysql_ms=mysql_ms,
            neo4j_ms=neo4j_ms,
            milvus_ms=milvus_ms,
            external_ms=external_latency_ms,
            total_ms=total_ms,
        ),
        steps=workflow_result.steps,
        projection_versions=projection_versions,
    )


def _resolution_rejection(request, resolution, started_at: float, answer: str) -> CompanyQuestionResponse:
    """返回身份拒答并保留候选来源和耗时。"""
    total_ms = round((time.perf_counter() - started_at) * 1000, 3)
    return CompanyQuestionResponse(
        question=request.question,
        scope_status=(
            RequestScopeStatus.SOURCE_UNAVAILABLE
            if resolution.status == ResolutionStatus.SOURCE_UNAVAILABLE
            else RequestScopeStatus.NOT_FOUND
            if resolution.status == ResolutionStatus.NOT_FOUND
            else RequestScopeStatus.NEEDS_CONTEXT
        ),
        resolution_status=resolution.status,
        company=resolution.company,
        answer=answer,
        sources=resolution.sources,
        data_quality=resolution.data_quality,
        latency=LatencyBreakdown(
            resolution_ms=resolution.latency.total_ms,
            total_ms=total_ms,
        ),
    )


def _needs_context_response(request: CompanyQuestionRequest, started_at: float) -> CompanyQuestionResponse:
    """返回不强制企业输入但明确提示需要上下文的响应。"""
    total_ms = round((time.perf_counter() - started_at) * 1000, 3)
    return CompanyQuestionResponse(
        question=request.question,
        scope_status=RequestScopeStatus.NEEDS_CONTEXT,
        resolution_status=ResolutionStatus.NOT_FOUND,
        answer="这个问题需要唯一企业范围，请在问题中补充企业名称、股票代码或统一社会信用代码。",
        data_quality=DataQuality(source_status=SourceStatus.AVAILABLE),
        latency=LatencyBreakdown(total_ms=total_ms),
    )


def _assemble_global_response(request: CompanyQuestionRequest, global_response, started_at: float):
    """把全局白名单统计转换为兼容问答响应。"""
    total_ms = round((time.perf_counter() - started_at) * 1000, 3)
    scope_status = (
        RequestScopeStatus.SOURCE_UNAVAILABLE
        if global_response.data_quality.source_status == SourceStatus.UNAVAILABLE
        else RequestScopeStatus.GLOBAL_SCOPE
    )
    return CompanyQuestionResponse(
        question=request.question,
        scope_status=scope_status,
        resolution_status=ResolutionStatus.RESOLVED,
        answer=global_response.answer,
        global_result=global_response.result,
        sources=global_response.sources,
        data_quality=global_response.data_quality,
        latency=LatencyBreakdown(
            mysql_ms=global_response.latency.mysql_ms,
            total_ms=total_ms,
        ),
    )


def _company_entity(company) -> Entity:
    """把标准企业身份转换为统一企业实体。"""
    return Entity(
        entity_id=company.eid,
        entity_type="company",
        name=company.name,
        attributes={
            "credit_code": company.credit_code,
            "stock_code": company.stock_code,
            "stock_abbreviation": company.stock_abbreviation,
        },
        source_ids=company.source_ids,
    )


def _graph_sources(relation: Relation) -> dict[str, Source]:
    """从图关系保留的来源表和记录键恢复 Source 对象。"""
    table = relation.properties.get("source_table")
    record_key = relation.properties.get("source_record_key")
    if not isinstance(table, str) or not isinstance(record_key, str):
        raise SourceIntegrityError("图关系缺少来源表或记录键")
    expected = build_source_id(table, record_key)
    if relation.source_ids != [expected]:
        raise SourceIntegrityError("图关系来源标识与事实主库记录不一致")
    return {
        expected: Source(
            source_id=expected,
            table=table,
            record_key=record_key,
            business_date=relation.effective_start,
            queried_at=datetime.now(UTC),
        )
    }


def _validate_source_integrity(facts, relations, paths, evidence, sources: dict[str, Source]) -> None:
    """确保所有输出事实、关系、路径和证据引用已登记来源。"""
    for source in sources.values():
        if source.source_id != build_source_id(source.table, source.record_key):
            raise SourceIntegrityError("来源标识与来源表、记录键不一致")
    source_ids = set(sources)
    referenced = {fact.source_id for fact in facts}
    referenced.update(source_id for relation in relations for source_id in relation.source_ids)
    referenced.update(source_id for path in paths for source_id in path.source_ids)
    referenced.update(item.source_id for item in evidence)
    missing = referenced - source_ids
    if missing:
        raise SourceIntegrityError(f"问答结果存在 {len(missing)} 个未登记来源")


def _find_fact_conflicts(facts) -> list[str]:
    """检测同主体同事实类型出现多个规范化值。"""
    values: dict[tuple[str, str], set[str]] = {}
    for fact in facts:
        key = (fact.subject_id, fact.fact_type, fact.effective_date.isoformat() if fact.effective_date else "")
        values.setdefault(key, set()).add(json.dumps(fact.normalized_value, ensure_ascii=False, sort_keys=True))
    return [
        f"{subject_id}:{fact_type}:{effective_date}"
        for (subject_id, fact_type, effective_date), items in values.items()
        if len(items) > 1
    ]


def _external_step_message(response: ExternalCompanyDataResponse) -> str | None:
    """把外部 Provider 失败分类转为问答步骤可读说明。"""
    messages = [
        f"{item.provider_name}: {item.error_code.value}"
        for item in response.provider_results
        if item.error_code is not None
    ]
    return "；".join(messages) if messages else None


def _render_answer(company, facts, relations, paths, evidence) -> str:
    """不依赖 LLM，按已验证数据生成带来源引用的简答。"""
    company_citations = " ".join(f"[{source_id}]" for source_id in company.source_ids)
    lines = [f"{company.name} {company_citations}："]
    for fact in list(facts)[:20]:
        value = fact.display_value if fact.display_value is not None else _display_value(fact.normalized_value)
        unit = f" {fact.unit}" if fact.unit else ""
        lines.append(f"- {fact.fact_type}：{value}{unit} [{fact.source_id}]")
    for relation in list(relations)[:10]:
        lines.append(
            f"- 关系 {relation.relation_type}：{relation.source_entity_id} → {relation.target_entity_id} "
            + " ".join(f"[{source_id}]" for source_id in relation.source_ids)
        )
    for path in list(paths)[:5]:
        citations = " ".join(f"[{source_id}]" for source_id in path.source_ids)
        lines.append(f"- 发现 {path.hop_count} 跳关系路径，置信度 {path.confidence.value} {citations}")
    for item in list(evidence)[:5]:
        excerpt = item.text.replace("\n", " ")[:160]
        lines.append(f"- 文本证据：{excerpt} [{item.source_id}]")
    if len(lines) == 1:
        lines.append("- 当前查询范围内没有可验证结果。")
    return "\n".join(lines)


def _display_value(value) -> str:
    """把 JSON 规范化值转换为紧凑可读文本。"""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _confidence_rank(confidence: ConfidenceLevel) -> int:
    """返回置信度从低到高的排序值。"""
    return {
        ConfidenceLevel.LOW: 0,
        ConfidenceLevel.MEDIUM: 1,
        ConfidenceLevel.HIGH: 2,
    }[confidence]


company_question_service = CompanyQuestionService()
