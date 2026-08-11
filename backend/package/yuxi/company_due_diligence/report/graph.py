"""企业尽调报告章节并行聚合工作流。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime

from yuxi.company_due_diligence.agent.quality import ClaimValidationError, build_claim_ledger
from yuxi.company_due_diligence.models import DataQuality, SourceStatus
from yuxi.company_due_diligence.qa.models import CompanyQuestionRequest
from yuxi.company_due_diligence.qa.service import CompanyQuestionService, company_question_service
from yuxi.company_due_diligence.report.models import (
    DueDiligenceReport,
    DueDiligenceReportRequest,
    ReportSection,
    ReportSectionResult,
    ReportSectionStatus,
    ReportStatus,
    ReportVersions,
)

ProgressCallback = Callable[[float, str], Awaitable[None]]

_SECTION_TITLES = {
    ReportSection.IDENTITY: "企业身份",
    ReportSection.BUSINESS: "工商与业务概览",
    ReportSection.SHAREHOLDERS: "股权结构与出资",
    ReportSection.RELATIONS: "高置信度关联关系",
    ReportSection.PRODUCTS: "产品与业务线索",
    ReportSection.FINANCING: "融资历程",
    ReportSection.FINANCIALS: "财务指标与覆盖",
    ReportSection.COMPLIANCE: "合规、处罚、异常与许可",
    ReportSection.INTELLECTUAL_PROPERTY: "知识产权",
    ReportSection.NEWS: "新闻与重大事件",
    ReportSection.RISKS: "关键风险与待核验事项",
    ReportSection.DATA_QUALITY: "数据质量声明",
}

_SECTION_QUESTIONS = {
    ReportSection.IDENTITY: "企业基本信息、注册资本、法人和经营状态是什么？",
    ReportSection.BUSINESS: "企业工商信息和业务概况是什么？",
    ReportSection.SHAREHOLDERS: "当前股东、历史股东、持股比例、认缴和实缴情况是什么？",
    ReportSection.RELATIONS: "企业有哪些高置信度关联路径和关系网络？",
    ReportSection.PRODUCTS: "企业有哪些产品和业务线？",
    ReportSection.FINANCING: "企业融资历史、融资金额和投资机构是什么？",
    ReportSection.FINANCIALS: "企业财务指标、营收、利润和覆盖范围是什么？",
    ReportSection.COMPLIANCE: "企业行政处罚、环保处罚、经营异常和行政许可情况是什么？",
    ReportSection.INTELLECTUAL_PROPERTY: "企业专利、商标和著作权情况是什么？",
    ReportSection.NEWS: "企业新闻、舆情和重大事件摘要是什么？",
    ReportSection.RISKS: "企业处罚原因、许可风险、经营异常和新闻风险详情是什么？",
}


class DueDiligenceReportWorkflow:
    """并行执行独立章节并把单章节失败保留为 partial 报告。"""

    def __init__(self, question_service: CompanyQuestionService | None = None) -> None:
        self._question_service = question_service or company_question_service

    async def run(
        self,
        *,
        report_id: str,
        request: DueDiligenceReportRequest,
        company,
        identity_sources,
        versions: ReportVersions,
        created_at: datetime,
        progress: ProgressCallback | None = None,
    ) -> DueDiligenceReport:
        """并行生成请求章节，再聚合来源、时点和整体质量。"""
        query_sections = [section for section in request.sections if section != ReportSection.DATA_QUALITY]
        tasks = {
            asyncio.create_task(self.run_section(section, request, company, versions)): section
            for section in query_sections
        }
        results: dict[str, ReportSectionResult] = {}
        completed = 0
        for task in asyncio.as_completed(tasks):
            result = await task
            results[result.section.value] = result
            completed += 1
            if progress is not None:
                fraction = completed / max(len(query_sections), 1)
                await progress(10 + fraction * 75, f"章节完成：{result.title}")

        if ReportSection.DATA_QUALITY in request.sections:
            results[ReportSection.DATA_QUALITY.value] = _data_quality_section(results.values())
        report = _assemble_report(
            report_id=report_id,
            request=request,
            company=company,
            versions=versions,
            created_at=created_at,
            sections=results,
            base_sources=identity_sources,
        )
        if progress is not None:
            await progress(88, "报告章节和质量状态聚合完成")
        return report

    async def run_section(
        self,
        section: ReportSection,
        request: DueDiligenceReportRequest,
        company,
        versions: ReportVersions,
    ) -> ReportSectionResult:
        """生成单个章节并校验实际派生版本未偏离锁定版本。"""
        if section == ReportSection.DATA_QUALITY:
            return _data_quality_section([])
        question = _SECTION_QUESTIONS[section]
        try:
            result = await self._question_service.query(
                CompanyQuestionRequest(
                    company_ref={"eid": company.eid},
                    question=question,
                    start_date=request.start_date,
                    as_of=request.as_of,
                    hop_limit=3,
                    top_k=20,
                    include_low_confidence=request.include_low_confidence,
                )
            )
            _validate_locked_versions(result.projection_versions, versions)
            return ReportSectionResult(
                section=section,
                title=_SECTION_TITLES[section],
                status=_section_status(result),
                summary=result.answer,
                query_result=result,
            )
        except Exception as exc:
            return ReportSectionResult(
                section=section,
                title=_SECTION_TITLES[section],
                status=ReportSectionStatus.FAILED,
                summary="本章节生成失败，未使用模型补写缺失事实。",
                error=type(exc).__name__,
            )


def _section_status(result) -> ReportSectionStatus:
    """根据结果规模和质量状态决定章节终态。"""
    has_data = bool(result.facts or result.relations or result.paths or result.evidence)
    if result.data_quality.conflicts:
        return ReportSectionStatus.CONFLICTED
    if result.data_quality.stale_fields:
        return ReportSectionStatus.STALE
    if result.data_quality.source_status == SourceStatus.UNAVAILABLE:
        return ReportSectionStatus.PARTIAL if has_data else ReportSectionStatus.FAILED
    if result.data_quality.source_status == SourceStatus.PARTIAL:
        return ReportSectionStatus.PARTIAL
    return ReportSectionStatus.COMPLETE if has_data else ReportSectionStatus.NO_DATA


def _data_quality_section(results) -> ReportSectionResult:
    """根据其他章节状态生成不依赖 LLM 的质量声明章节。"""
    statuses = [result.status for result in results]
    failed = sum(status == ReportSectionStatus.FAILED for status in statuses)
    partial = sum(
        status in {ReportSectionStatus.PARTIAL, ReportSectionStatus.STALE, ReportSectionStatus.CONFLICTED}
        for status in statuses
    )
    summary = f"章节统计：失败 {failed} 个，部分/过期/冲突 {partial} 个；缺失内容不会由模型自由补写。"
    status = ReportSectionStatus.PARTIAL if failed or partial else ReportSectionStatus.COMPLETE
    return ReportSectionResult(
        section=ReportSection.DATA_QUALITY,
        title=_SECTION_TITLES[ReportSection.DATA_QUALITY],
        status=status,
        summary=summary,
    )


def _assemble_report(
    *, report_id, request, company, versions, created_at, sections, base_sources=()
) -> DueDiligenceReport:
    """合并章节来源、数据质量和整体终态。"""
    sources = {source.source_id: source for source in base_sources}
    warnings = []
    missing_fields = []
    stale_fields = []
    conflicts = []
    business_dates: list[date] = []
    facts = {}
    relations = {}
    paths = {}
    evidence = {}
    for result in sections.values():
        if result.error:
            warnings.append(f"{result.section.value}：{result.error}")
        if result.query_result is None:
            continue
        try:
            build_claim_ledger(
                facts=result.query_result.facts,
                relations=result.query_result.relations,
                paths=result.query_result.paths,
                evidence=result.query_result.evidence,
                sources=result.query_result.sources,
            )
        except ClaimValidationError:
            result.status = ReportSectionStatus.FAILED
            result.summary = "本章节来源校验失败，未输出未经验证事实。"
            result.error = "ClaimValidationError"
            result.query_result = None
            warnings.append(f"{result.section.value}：ClaimValidationError")
            continue
        facts.update({item.fact_id: item for item in result.query_result.facts})
        relations.update({item.relation_id: item for item in result.query_result.relations})
        paths.update({item.path_id: item for item in result.query_result.paths})
        evidence.update({item.evidence_id: item for item in result.query_result.evidence})
        sources.update({source.source_id: source for source in result.query_result.sources})
        quality = result.query_result.data_quality
        warnings.extend(quality.warnings)
        missing_fields.extend(quality.missing_fields)
        stale_fields.extend(quality.stale_fields)
        conflicts.extend(quality.conflicts)
        business_dates.extend(
            source.business_date.date() if isinstance(source.business_date, datetime) else source.business_date
            for source in result.query_result.sources
            if source.business_date is not None
        )

    statuses = [result.status for result in sections.values()]
    failed_count = sum(status == ReportSectionStatus.FAILED for status in statuses)
    if statuses and failed_count == len(statuses):
        report_status = ReportStatus.FAILED
    elif any(status not in {ReportSectionStatus.COMPLETE, ReportSectionStatus.NO_DATA} for status in statuses):
        report_status = ReportStatus.PARTIAL
    else:
        report_status = ReportStatus.COMPLETE
    source_status = (
        SourceStatus.UNAVAILABLE
        if report_status == ReportStatus.FAILED
        else SourceStatus.PARTIAL
        if report_status == ReportStatus.PARTIAL
        else SourceStatus.AVAILABLE
    )
    claim_ledger = build_claim_ledger(
        facts=list(facts.values()),
        relations=list(relations.values()),
        paths=list(paths.values()),
        evidence=list(evidence.values()),
        sources=list(sources.values()),
    )
    return DueDiligenceReport(
        report_id=report_id,
        status=report_status,
        request=request,
        company=company,
        versions=versions,
        created_at=created_at,
        completed_at=datetime.now(UTC),
        data_as_of=request.as_of or max(business_dates, default=None),
        sections=sections,
        sources=list(sources.values()),
        claim_ledger=claim_ledger,
        data_quality=DataQuality(
            source_status=source_status,
            missing_fields=list(dict.fromkeys(missing_fields)),
            stale_fields=list(dict.fromkeys(stale_fields)),
            conflicts=list(dict.fromkeys(conflicts)),
            warnings=list(dict.fromkeys(warnings)),
        ),
    )


def _validate_locked_versions(actual: dict[str, str], expected: ReportVersions) -> None:
    """拒绝报告生成期间发生的图谱或证据版本漂移。"""
    expected_versions = {"neo4j": expected.graph_version, "milvus": expected.evidence_version}
    for source, actual_version in actual.items():
        if expected_versions.get(source) != actual_version:
            raise RuntimeError(f"{source} 派生版本在报告生成期间发生变化")
