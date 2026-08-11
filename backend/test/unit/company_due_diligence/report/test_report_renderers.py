"""企业尽调报告 JSON、Markdown 和 checksum 单元测试。"""

from datetime import UTC, datetime

from yuxi.company_due_diligence.models import (
    CompanyReference,
    ConfidenceLevel,
    DataQuality,
    MatchMethod,
    ResolvedCompany,
    Source,
    build_source_id,
)
from yuxi.company_due_diligence.report.models import (
    DueDiligenceReport,
    DueDiligenceReportRequest,
    ReportSection,
    ReportSectionResult,
    ReportSectionStatus,
    ReportStatus,
    ReportVersions,
)
from yuxi.company_due_diligence.report.renderers import (
    content_checksum,
    render_report_json,
    render_report_markdown,
)


def _report() -> DueDiligenceReport:
    """构造包含一章和一个稳定来源的测试报告。"""
    source_id = build_source_id("t_enterprise", "eid-1")
    company = ResolvedCompany(
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
    now = datetime.now(UTC)
    return DueDiligenceReport(
        report_id="report-1",
        status=ReportStatus.COMPLETE,
        request=DueDiligenceReportRequest(
            company_ref=CompanyReference(eid="eid-1"),
            sections=[ReportSection.IDENTITY],
        ),
        company=company,
        versions=ReportVersions(code_version="test", config_version="config"),
        created_at=now,
        completed_at=now,
        sections={
            ReportSection.IDENTITY.value: ReportSectionResult(
                section=ReportSection.IDENTITY,
                title="企业身份",
                status=ReportSectionStatus.COMPLETE,
                summary=f"注册资本为 1000 万元 [{source_id}]",
            )
        },
        sources=[
            Source(
                source_id=source_id,
                table="t_enterprise",
                record_key="eid-1",
                queried_at=now,
            )
        ],
        data_quality=DataQuality(),
    )


def test_renderers_are_stable_and_markdown_only_uses_registered_sources():
    """JSON 应稳定，Markdown 中事实引用必须存在于来源索引。"""
    report = _report()
    first_json = render_report_json(report)
    second_json = render_report_json(report)
    markdown = render_report_markdown(report).decode()
    source_id = report.sources[0].source_id

    assert first_json == second_json
    assert content_checksum(first_json) == content_checksum(second_json)
    assert f"[{source_id}]" in markdown
    assert f"`{source_id}`" in markdown
    assert "artifacts" not in first_json.decode()
