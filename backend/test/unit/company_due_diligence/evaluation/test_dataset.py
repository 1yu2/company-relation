"""企业尽调质量评测数据集单元测试。"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from yuxi.company_due_diligence.evaluation.dataset import (
    DatasetSourceKind,
    EvaluationDataset,
    EvaluationDatasetRepository,
    EvaluationSample,
    EvaluationSampleType,
    GoldenExpected,
    ReviewStatus,
)
from yuxi.company_due_diligence.qa.models import CompanyQuestionRequest


def _sample(sample_id: str, review_status: ReviewStatus) -> EvaluationSample:
    """构造只验证拒答的最小问答样本。"""
    review_fields = {}
    if review_status == ReviewStatus.HUMAN_VERIFIED:
        review_fields = {
            "reviewer_note": "已对照冻结来源完成复核",
            "reviewed_by": "reviewer-1",
            "reviewed_at": datetime(2026, 8, 10, tzinfo=UTC),
        }
    return EvaluationSample(
        sample_id=sample_id,
        sample_type=EvaluationSampleType.QA,
        domain="entity_resolution",
        review_status=review_status,
        qa_request=CompanyQuestionRequest(
            company_ref={"name": "不存在企业"},
            question="企业是否存在？",
        ),
        expected=GoldenExpected(refused=True),
        **review_fields,
    )


def _dataset() -> EvaluationDataset:
    """构造人工与未复核样本各一条的数据集。"""
    return EvaluationDataset(
        dataset_id="dataset-v1",
        name="测试数据集",
        version="1.0.0",
        source_kind=DatasetSourceKind.GUANGDONG_ENTERPRISE,
        business_as_of="2026-08-10",
        created_at=datetime(2026, 8, 10, tzinfo=UTC),
        samples=[
            _sample("human", ReviewStatus.HUMAN_VERIFIED),
            _sample("unreviewed", ReviewStatus.SOURCE_DERIVED_UNREVIEWED),
        ],
    )


def test_dataset_checksum_and_human_review_rate_are_stable(tmp_path):
    """冻结内容相同应得到相同 checksum，并正确计算人工复核率。"""
    dataset = _dataset()
    restored = EvaluationDataset.model_validate_json(dataset.model_dump_json())

    assert dataset.checksum == restored.checksum
    assert dataset.human_review_rate == 0.5

    path = tmp_path / "dataset.json"
    path.write_text(dataset.model_dump_json(indent=2), encoding="utf-8")
    loaded = EvaluationDatasetRepository(tmp_path).get("dataset-v1")
    assert loaded.checksum == dataset.checksum


def test_repository_rejects_declared_checksum_mismatch(tmp_path):
    """声明 checksum 与冻结内容不一致时必须显式失败。"""
    dataset = _dataset().model_copy(update={"declared_checksum": "0" * 64})
    (tmp_path / "dataset.json").write_text(dataset.model_dump_json(indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="checksum 不一致"):
        EvaluationDatasetRepository(tmp_path).list()


def test_golden_requires_at_least_one_metric_target():
    """空 Golden 不能进入评测链路。"""
    with pytest.raises(ValidationError, match="至少需要一个严格评测目标"):
        GoldenExpected()


def test_sample_type_must_match_exactly_one_request():
    """问答样本不能缺少请求或同时携带报告请求。"""
    with pytest.raises(ValidationError, match="qa 样本必须且只能提供 qa_request"):
        EvaluationSample(
            sample_id="invalid",
            sample_type=EvaluationSampleType.QA,
            domain="identity",
            review_status=ReviewStatus.SOURCE_DERIVED_UNREVIEWED,
            expected=GoldenExpected(refused=True),
        )


def test_human_verified_sample_requires_review_audit_fields():
    """人工 Golden 缺少复核人、时间或说明时不得进入数据集。"""
    with pytest.raises(ValidationError, match="reviewed_by 和 reviewed_at"):
        EvaluationSample(
            sample_id="human-without-audit",
            sample_type=EvaluationSampleType.QA,
            domain="identity",
            review_status=ReviewStatus.HUMAN_VERIFIED,
            qa_request=CompanyQuestionRequest(company_ref={"eid": "eid-1"}, question="企业是否存在？"),
            expected=GoldenExpected(refused=False),
        )


def test_unreviewed_sample_rejects_human_review_metadata():
    """未复核样本不得携带会误导门禁的人工复核元数据。"""
    with pytest.raises(ValidationError, match="未人工复核样本"):
        EvaluationSample(
            sample_id="unreviewed-with-audit",
            sample_type=EvaluationSampleType.QA,
            domain="identity",
            review_status=ReviewStatus.SOURCE_DERIVED_UNREVIEWED,
            qa_request=CompanyQuestionRequest(company_ref={"eid": "eid-1"}, question="企业是否存在？"),
            expected=GoldenExpected(refused=False),
            reviewed_by="reviewer-1",
            reviewed_at=datetime(2026, 8, 10, tzinfo=UTC),
        )
