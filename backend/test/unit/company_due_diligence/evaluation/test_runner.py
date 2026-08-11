"""企业尽调质量评测运行器单元测试。"""

from datetime import UTC, datetime

from yuxi.company_due_diligence.evaluation.dataset import (
    DatasetSourceKind,
    EvaluationDataset,
    EvaluationSample,
    EvaluationSampleType,
    GoldenExpected,
    ReviewStatus,
)
from yuxi.company_due_diligence.evaluation.runner import QualityEvaluationRunner
from yuxi.company_due_diligence.qa.models import CompanyQuestionRequest


class FakeResponse:
    """提供运行器所需的 model_dump 接口。"""

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def model_dump(self, mode: str):
        """返回固定预测。"""
        assert mode == "json"
        return self._payload


class FakeQAService:
    """按问题内容返回成功或失败结果。"""

    async def query(self, request):
        """模拟真实问答服务的成功与异常。"""
        if request.question == "失败":
            raise RuntimeError("boom")
        return FakeResponse(
            {
                "resolution_status": "not_found",
                "facts": [],
                "relations": [],
                "paths": [],
                "evidence": [],
                "sources": [],
                "data_quality": {},
            }
        )


class FakeContext:
    """记录运行器写入的进度和部分样本结果。"""

    def __init__(self) -> None:
        self.results = []

    async def raise_if_cancelled(self):
        """模拟未取消任务。"""

    async def set_progress(self, progress: float, message: str):
        """接受进度更新。"""
        assert progress >= 0
        assert message

    async def set_result(self, result: dict):
        """保存断点可见结果。"""
        self.results.append(result)


def _dataset(review_status: ReviewStatus, questions: list[str]) -> EvaluationDataset:
    """构造只验证拒答的运行器数据集。"""
    review_fields = {}
    if review_status == ReviewStatus.HUMAN_VERIFIED:
        review_fields = {
            "reviewer_note": "已对照冻结来源完成复核",
            "reviewed_by": "reviewer-1",
            "reviewed_at": datetime(2026, 8, 10, tzinfo=UTC),
        }
    return EvaluationDataset(
        dataset_id="runner-dataset",
        name="运行器测试",
        version="1.0.0",
        source_kind=DatasetSourceKind.GUANGDONG_ENTERPRISE,
        created_at=datetime(2026, 8, 10, tzinfo=UTC),
        samples=[
            EvaluationSample(
                sample_id=f"sample-{index}",
                sample_type=EvaluationSampleType.QA,
                domain="entity_resolution",
                review_status=review_status,
                qa_request=CompanyQuestionRequest(company_ref={"name": "不存在企业"}, question=question),
                expected=GoldenExpected(refused=True),
                **review_fields,
            )
            for index, question in enumerate(questions)
        ],
    )


async def test_runner_exposes_completed_samples_during_execution():
    """每完成一题都应写入可断点查看的部分结果。"""
    context = FakeContext()
    report = await QualityEvaluationRunner(qa_service=FakeQAService()).run(
        _dataset(ReviewStatus.SOURCE_DERIVED_UNREVIEWED, ["成功", "成功"]),
        run_id="run-1",
        context=context,
    )

    assert len(context.results) == 2
    assert context.results[-1]["completed"] == 2
    assert len(context.results[-1]["samples"]) == 2
    assert report.regression_gate.eligible is False
    assert len(report.configuration_checksum) == 64
    assert report.metrics_by_domain["entity_resolution"].sample_count == 2
    assert report.metrics_by_sample_type["qa"].sample_count == 2
    assert report.metrics_by_review_status["source_derived_unreviewed"].sample_count == 2
    assert report.samples[0].input["question"] == "成功"


async def test_runner_keeps_failed_sample_and_fails_human_gate():
    """单题异常应保留结果并使人工严格门禁失败。"""
    report = await QualityEvaluationRunner(qa_service=FakeQAService()).run(
        _dataset(ReviewStatus.HUMAN_VERIFIED, ["成功", "失败"]),
        run_id="run-2",
    )

    assert report.samples[0].metrics.strict_passed is True
    assert report.samples[1].error == "RuntimeError"
    assert report.strict_metrics.sample_count == 2
    assert report.regression_gate.eligible is True
    assert report.regression_gate.passed is False
