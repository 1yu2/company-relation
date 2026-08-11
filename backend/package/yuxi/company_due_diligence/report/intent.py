"""把领域智能体报告意图转换为现有报告工作流请求。"""

from __future__ import annotations

from yuxi.company_due_diligence.agent.models import InvestigationIntent, InvestigationTopic
from yuxi.company_due_diligence.report.models import DueDiligenceReportRequest, ReportSection

_TOPIC_SECTIONS = {
    InvestigationTopic.PROFILE: [ReportSection.IDENTITY, ReportSection.BUSINESS],
    InvestigationTopic.SHAREHOLDERS: [ReportSection.IDENTITY, ReportSection.SHAREHOLDERS],
    InvestigationTopic.RELATIONS: [ReportSection.IDENTITY, ReportSection.RELATIONS],
    InvestigationTopic.PRODUCTS: [ReportSection.IDENTITY, ReportSection.PRODUCTS],
    InvestigationTopic.FINANCING: [ReportSection.IDENTITY, ReportSection.FINANCING, ReportSection.RELATIONS],
    InvestigationTopic.FINANCIALS: [ReportSection.IDENTITY, ReportSection.FINANCIALS],
    InvestigationTopic.RISKS: [ReportSection.IDENTITY, ReportSection.COMPLIANCE, ReportSection.RISKS],
    InvestigationTopic.INTELLECTUAL_PROPERTY: [ReportSection.IDENTITY, ReportSection.INTELLECTUAL_PROPERTY],
    InvestigationTopic.NEWS: [ReportSection.IDENTITY, ReportSection.NEWS],
}


def report_request_from_intent(
    *,
    eid: str,
    intent: InvestigationIntent,
    include_low_confidence: bool,
) -> DueDiligenceReportRequest:
    """按专题范围或完整尽调意图生成报告请求。"""
    sections = sections_for_topics(intent.topics)
    return DueDiligenceReportRequest(
        company_ref={"eid": eid},
        sections=sections,
        start_date=intent.time_scope.start_date,
        as_of=intent.time_scope.end_date,
        include_low_confidence=include_low_confidence,
    )


def sections_for_topics(topics: list[InvestigationTopic]) -> list[ReportSection]:
    """将业务主题映射为必要报告章节，并始终追加数据质量声明。"""
    if InvestigationTopic.FULL_DUE_DILIGENCE in topics:
        return list(ReportSection)
    sections = []
    for topic in topics:
        sections.extend(_TOPIC_SECTIONS.get(topic, []))
    sections.append(ReportSection.DATA_QUALITY)
    return list(dict.fromkeys(sections))
