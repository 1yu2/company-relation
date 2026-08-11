"""企业尽调报告章节并行和部分失败单元测试。"""

from datetime import UTC, datetime

import pytest

from yuxi.company_due_diligence.models import (
    ConfidenceLevel,
    DataQuality,
    Fact,
    LatencyBreakdown,
    MatchMethod,
    ResolutionStatus,
    ResolvedCompany,
    Source,
    build_source_id,
)
from yuxi.company_due_diligence.qa.models import CompanyQuestionResponse
from yuxi.company_due_diligence.report.graph import DueDiligenceReportWorkflow
from yuxi.company_due_diligence.report.models import (
    DueDiligenceReportRequest,
    ReportSection,
    ReportSectionStatus,
    ReportStatus,
    ReportVersions,
)

pytestmark = pytest.mark.asyncio


def _company() -> ResolvedCompany:
    """构造标准企业身份。"""
    source_id = build_source_id("t_enterprise", "eid-1")
    return ResolvedCompany(
        eid="eid-1",
        name="示例企业",
        normalized_name="示例企业",
        credit_code="credit-1",
        stock_code="000001.SZ",
        stock_abbreviation="示例",
        match_method=MatchMethod.EID,
        confidence=ConfidenceLevel.HIGH,
        source_ids=[source_id],
    )


class PartialQuestionService:
    """身份章节成功，关系章节抛错。"""

    async def query(self, request):
        """按固定章节问题返回数据或异常。"""
        if "关联" in request.question:
            raise RuntimeError("Neo4j failed")
        source_id = build_source_id("t_enterprise", "eid-1")
        return CompanyQuestionResponse(
            question=request.question,
            resolution_status=ResolutionStatus.RESOLVED,
            company=_company(),
            answer=f"企业状态正常 [{source_id}]",
            facts=[
                Fact(
                    fact_id="fact-1",
                    subject_id="eid-1",
                    fact_type="business_status",
                    normalized_value="存续",
                    source_id=source_id,
                )
            ],
            sources=[
                Source(
                    source_id=source_id,
                    table="t_enterprise",
                    record_key="eid-1",
                    queried_at=datetime.now(UTC),
                )
            ],
            data_quality=DataQuality(),
            latency=LatencyBreakdown(),
        )


async def test_single_section_failure_keeps_successful_sections_and_marks_partial():
    """单章节失败不得丢失其他章节，整体应为 partial。"""
    company = _company()
    source = Source(
        source_id=company.source_ids[0],
        table="t_enterprise",
        record_key="eid-1",
        queried_at=datetime.now(UTC),
    )
    request = DueDiligenceReportRequest(
        company_ref={"eid": "eid-1"},
        sections=[ReportSection.IDENTITY, ReportSection.RELATIONS, ReportSection.DATA_QUALITY],
    )
    workflow = DueDiligenceReportWorkflow(question_service=PartialQuestionService())

    report = await workflow.run(
        report_id="report-1",
        request=request,
        company=company,
        identity_sources=[source],
        versions=ReportVersions(code_version="test", config_version="config"),
        created_at=datetime.now(UTC),
    )

    assert report.status == ReportStatus.PARTIAL
    assert report.sections[ReportSection.IDENTITY.value].status == ReportSectionStatus.COMPLETE
    assert report.sections[ReportSection.RELATIONS.value].status == ReportSectionStatus.FAILED
    assert report.sections[ReportSection.DATA_QUALITY.value].status == ReportSectionStatus.PARTIAL
    assert report.sources[0].source_id == company.source_ids[0]
