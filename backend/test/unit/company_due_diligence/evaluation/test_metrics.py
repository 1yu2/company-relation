"""企业尽调质量评测严格指标单元测试。"""

from datetime import UTC, datetime

from yuxi.company_due_diligence.evaluation.dataset import (
    EvaluationSample,
    EvaluationSampleType,
    ExpectedFact,
    ExpectedPath,
    ExpectedPathEdge,
    ExpectedRelation,
    GoldenExpected,
    ReviewStatus,
)
from yuxi.company_due_diligence.evaluation.metrics import aggregate_metrics, evaluate_sample
from yuxi.company_due_diligence.models import build_source_id
from yuxi.company_due_diligence.qa.models import CompanyQuestionRequest
from yuxi.company_due_diligence.report.models import DueDiligenceReportRequest, ReportSection


def _qa_sample(expected: GoldenExpected, review_status: ReviewStatus = ReviewStatus.HUMAN_VERIFIED):
    """构造使用统一企业标识的问答 Golden。"""
    review_fields = {}
    if review_status == ReviewStatus.HUMAN_VERIFIED:
        review_fields = {
            "reviewer_note": "已对照冻结来源完成复核",
            "reviewed_by": "reviewer-1",
            "reviewed_at": datetime(2026, 8, 10, tzinfo=UTC),
        }
    return EvaluationSample(
        sample_id="qa-sample",
        sample_type=EvaluationSampleType.QA,
        domain="identity",
        review_status=review_status,
        qa_request=CompanyQuestionRequest(company_ref={"eid": "eid-1"}, question="查询企业信息"),
        expected=expected,
        **review_fields,
    )


def _source(table: str = "t_enterprise", record_key: str = "record-1") -> dict:
    """构造哈希一致的来源记录。"""
    return {
        "source_id": build_source_id(table, record_key),
        "source_system": "guangdong_enterprise_mysql",
        "table": table,
        "record_key": record_key,
        "queried_at": datetime(2026, 8, 10, tzinfo=UTC).isoformat(),
    }


def _prediction(**overrides) -> dict:
    """构造已唯一解析的最小问答预测。"""
    prediction = {
        "resolution_status": "resolved",
        "facts": [],
        "relations": [],
        "paths": [],
        "evidence": [],
        "sources": [],
        "data_quality": {},
    }
    prediction.update(overrides)
    return prediction


def test_fact_comparison_ignores_order_duplicates_and_display_text():
    """事实集合比较应忽略顺序、重复和展示文案。"""
    source = _source()
    expected = GoldenExpected(
        facts=[
            ExpectedFact(subject_id="eid-1", fact_type="capital", normalized_value="100", unit="万元"),
            ExpectedFact(subject_id="eid-1", fact_type="status", normalized_value="存续"),
        ]
    )
    facts = [
        {
            "subject_id": "eid-1",
            "fact_type": "status",
            "normalized_value": "存续",
            "display_value": "企业状态：存续",
            "source_id": source["source_id"],
        },
        {
            "subject_id": "eid-1",
            "fact_type": "capital",
            "normalized_value": "100",
            "display_value": "人民币壹佰万元",
            "unit": "万元",
            "source_id": source["source_id"],
        },
        {
            "subject_id": "eid-1",
            "fact_type": "capital",
            "normalized_value": "100",
            "display_value": "100 万元",
            "unit": "万元",
            "source_id": source["source_id"],
        },
    ]

    result = evaluate_sample(_qa_sample(expected), _prediction(facts=facts, sources=[source]))

    assert result.field_exact is True
    assert result.hallucination_count == 0
    assert result.strict_passed is True


def test_fact_comparison_preserves_unit_and_date():
    """金额单位或生效日期不同必须算作严格失败和意外事实。"""
    source = _source()
    expected = GoldenExpected(
        facts=[
            ExpectedFact(
                subject_id="eid-1",
                fact_type="capital",
                normalized_value="100",
                unit="万元",
                effective_date="2026-08-09",
            )
        ]
    )
    facts = [
        {
            "subject_id": "eid-1",
            "fact_type": "capital",
            "normalized_value": "100",
            "unit": "元",
            "effective_date": "2026-08-10",
            "source_id": source["source_id"],
        }
    ]

    result = evaluate_sample(_qa_sample(expected), _prediction(facts=facts, sources=[source]))

    assert result.field_exact is False
    assert result.hallucination_count == 1
    assert result.first_error == "mysql_mapping"


def test_relation_direction_and_path_order_are_strict():
    """关系方向、节点顺序、边方向和跳数都必须匹配。"""
    source = _source("t_partners_alls", "relation-1")
    expected = GoldenExpected(
        relations=[ExpectedRelation(source_entity_id="holder", target_entity_id="eid-1", relation_type="HOLDS")],
        paths=[
            ExpectedPath(
                node_ids=["eid-1", "holder", "eid-2"],
                edges=[
                    ExpectedPathEdge(source_entity_id="holder", target_entity_id="eid-1", relation_type="HOLDS"),
                    ExpectedPathEdge(source_entity_id="holder", target_entity_id="eid-2", relation_type="HOLDS"),
                ],
                hop_count=2,
            )
        ],
    )
    prediction = _prediction(
        relations=[
            {
                "source_entity_id": "eid-1",
                "target_entity_id": "holder",
                "relation_type": "HOLDS",
                "source_ids": [source["source_id"]],
            }
        ],
        paths=[
            {
                "node_ids": ["eid-2", "holder", "eid-1"],
                "edges": [
                    {"source_entity_id": "holder", "target_entity_id": "eid-2", "relation_type": "HOLDS"},
                    {"source_entity_id": "holder", "target_entity_id": "eid-1", "relation_type": "HOLDS"},
                ],
                "hop_count": 2,
                "source_ids": [source["source_id"]],
            }
        ],
        sources=[source],
    )

    result = evaluate_sample(_qa_sample(expected), prediction)

    assert result.relation_exact is False
    assert result.path_exact is False
    assert result.first_error == "graph_projection"


def test_evidence_refusal_source_and_freshness_metrics():
    """证据、拒答、来源完整性和时效声明应独立计算。"""
    source = _source("t_news", "news-1")
    expected = GoldenExpected(
        refused=False,
        evidence_source_ids=[source["source_id"]],
        expect_stale_declaration=True,
    )
    prediction = _prediction(
        evidence=[{"source_id": source["source_id"]}],
        sources=[source],
        data_quality={"stale_fields": ["news"]},
    )

    result = evaluate_sample(_qa_sample(expected), prediction)

    assert result.evidence_covered is True
    assert result.refusal_correct is True
    assert result.freshness_correct is True
    assert result.source_integrity is True
    assert result.strict_passed is True


def test_missing_or_hash_mismatched_source_fails_integrity():
    """引用未登记来源或伪造来源哈希时必须失败。"""
    expected = GoldenExpected(facts=[])
    invalid_source = _source()
    invalid_source["source_id"] = "source:invalid"
    prediction = _prediction(
        facts=[
            {
                "subject_id": "eid-1",
                "fact_type": "status",
                "normalized_value": "存续",
                "source_id": "source:missing",
            }
        ],
        sources=[invalid_source],
    )

    result = evaluate_sample(_qa_sample(expected), prediction)

    assert result.source_integrity is False
    assert result.first_error == "mysql_mapping"
    assert "source_integrity" in result.differences


def test_report_sections_and_stale_declaration_are_evaluated():
    """报告章节完整性应排除失败章节并校验时效声明。"""
    sample = EvaluationSample(
        sample_id="report-sample",
        sample_type=EvaluationSampleType.REPORT,
        domain="report",
        review_status=ReviewStatus.HUMAN_VERIFIED,
        report_request=DueDiligenceReportRequest(
            company_ref={"eid": "eid-1"},
            sections=[ReportSection.IDENTITY, ReportSection.BUSINESS],
        ),
        expected=GoldenExpected(
            required_report_sections=[ReportSection.IDENTITY, ReportSection.BUSINESS],
            expect_stale_declaration=True,
        ),
        reviewer_note="已对照冻结来源完成复核",
        reviewed_by="reviewer-1",
        reviewed_at=datetime(2026, 8, 10, tzinfo=UTC),
    )
    prediction = {
        "report_id": "report-1",
        "status": "partial",
        "sections": {
            "identity": {"status": "complete"},
            "business": {"status": "failed"},
        },
        "data_quality": {"stale_fields": ["business"]},
    }

    result = evaluate_sample(sample, prediction)

    assert result.report_complete is False
    assert result.freshness_correct is True
    assert result.first_error == "report_orchestration"


def test_unreviewed_samples_are_excluded_from_strict_metrics():
    """程序派生样本只能进入全量指标，不能混入严格门禁。"""
    human = _qa_sample(GoldenExpected(refused=False))
    unreviewed = _qa_sample(
        GoldenExpected(refused=True),
        review_status=ReviewStatus.SOURCE_DERIVED_UNREVIEWED,
    ).model_copy(update={"sample_id": "unreviewed"})
    human_result = evaluate_sample(human, _prediction())
    unreviewed_result = evaluate_sample(unreviewed, _prediction())

    all_metrics, strict_metrics = aggregate_metrics(
        [human, unreviewed],
        [human_result, unreviewed_result],
    )

    assert all_metrics.sample_count == 2
    assert all_metrics.refusal_accuracy.rate == 0.5
    assert strict_metrics.sample_count == 1
    assert strict_metrics.refusal_accuracy.rate == 1
