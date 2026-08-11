"""企业尽调 Golden 质量评测运行器。"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from yuxi.company_due_diligence.evaluation.dataset import EvaluationDataset, EvaluationSample
from yuxi.company_due_diligence.evaluation.metrics import (
    QualityMetrics,
    SampleMetricResult,
    aggregate_metrics,
    evaluate_sample,
)
from yuxi.company_due_diligence.qa.service import CompanyQuestionService, company_question_service
from yuxi.company_due_diligence.report.service import DueDiligenceReportService, due_diligence_report_service
from yuxi.services.task_service import TaskContext


class QualityEvaluationRequest(BaseModel):
    """描述要运行的数据集和可选样本上限。"""

    dataset_id: str = Field(min_length=1, max_length=128)
    max_samples: int | None = Field(default=None, ge=1, le=10000)
    strategy_label: str = Field(default="default", min_length=1, max_length=128)


class QualitySampleResult(BaseModel):
    """描述单样本预测、Golden、差异、指标和错误。"""

    sample_id: str
    sample_type: str
    domain: str
    review_status: str
    input: dict
    prediction: dict | None = None
    expected: dict
    metrics: SampleMetricResult | None = None
    error: str | None = None


class QualityRegressionGate(BaseModel):
    """描述仅人工复核样本可进入的发布门禁。"""

    eligible: bool
    passed: bool
    reasons: list[str] = Field(default_factory=list)


class QualityArtifact(BaseModel):
    """描述质量评测导出文件及其完整性信息。"""

    format: str
    bucket: str
    object_name: str
    checksum: str
    size_bytes: int = Field(ge=0)


class QualityEvaluationReport(BaseModel):
    """描述可复现质量运行输入、逐题结果、聚合和门禁。"""

    run_id: str
    created_at: datetime
    completed_at: datetime
    code_version: str
    configuration_checksum: str
    dataset_id: str
    dataset_version: str
    dataset_checksum: str
    dataset_source_kind: str
    strategy_label: str
    business_as_of: str | None = None
    all_metrics: QualityMetrics
    strict_metrics: QualityMetrics
    metrics_by_domain: dict[str, QualityMetrics] = Field(default_factory=dict)
    metrics_by_sample_type: dict[str, QualityMetrics] = Field(default_factory=dict)
    metrics_by_review_status: dict[str, QualityMetrics] = Field(default_factory=dict)
    regression_gate: QualityRegressionGate
    samples: list[QualitySampleResult]
    artifacts: list[QualityArtifact] = Field(default_factory=list)


class QualityEvaluationRunner:
    """调用真实问答/报告服务并计算严格质量指标。"""

    def __init__(
        self,
        qa_service: CompanyQuestionService | None = None,
        report_service: DueDiligenceReportService | None = None,
    ) -> None:
        self._qa_service = qa_service or company_question_service
        self._report_service = report_service or due_diligence_report_service

    async def run(
        self,
        dataset: EvaluationDataset,
        *,
        run_id: str,
        max_samples: int | None = None,
        strategy_label: str = "default",
        context: TaskContext | None = None,
    ) -> QualityEvaluationReport:
        """顺序执行冻结样本，支持取消和断点查看已完成样本。"""
        created_at = datetime.now(UTC)
        samples = dataset.samples[:max_samples] if max_samples else dataset.samples
        results = []
        metrics = []
        for index, sample in enumerate(samples, start=1):
            if context is not None:
                await context.raise_if_cancelled()
                await context.set_progress((index - 1) / len(samples) * 95, f"评测样本 {index}/{len(samples)}")
            sample_result = await self._run_sample(sample, run_id)
            results.append(sample_result)
            if sample_result.metrics is not None:
                metrics.append(sample_result.metrics)
            else:
                metrics.append(
                    SampleMetricResult(
                        source_integrity=False,
                        hallucination_count=0,
                        predicted_item_count=0,
                        strict_passed=False,
                        first_error="execution",
                    )
                )
            if context is not None:
                await context.set_result(
                    {
                        "dataset_id": dataset.dataset_id,
                        "dataset_checksum": dataset.checksum,
                        "completed": index,
                        "total": len(samples),
                        "samples": [item.model_dump(mode="json") for item in results],
                    }
                )

        all_metrics, strict_metrics = aggregate_metrics(samples, metrics)
        metrics_by_domain = aggregate_grouped_metrics(samples, metrics, lambda sample: sample.domain)
        metrics_by_sample_type = aggregate_grouped_metrics(samples, metrics, lambda sample: sample.sample_type.value)
        metrics_by_review_status = aggregate_grouped_metrics(
            samples,
            metrics,
            lambda sample: sample.review_status.value,
        )
        gate = _regression_gate(strict_metrics)
        return QualityEvaluationReport(
            run_id=run_id,
            created_at=created_at,
            completed_at=datetime.now(UTC),
            code_version=os.getenv("YUXI_VERSION", "0.7.1"),
            configuration_checksum=_configuration_checksum(),
            dataset_id=dataset.dataset_id,
            dataset_version=dataset.version,
            dataset_checksum=dataset.checksum,
            dataset_source_kind=dataset.source_kind.value,
            strategy_label=strategy_label,
            business_as_of=dataset.business_as_of.isoformat() if dataset.business_as_of else None,
            all_metrics=all_metrics,
            strict_metrics=strict_metrics,
            metrics_by_domain=metrics_by_domain,
            metrics_by_sample_type=metrics_by_sample_type,
            metrics_by_review_status=metrics_by_review_status,
            regression_gate=gate,
            samples=results,
        )

    async def _run_sample(self, sample: EvaluationSample, run_id: str) -> QualitySampleResult:
        """执行单条问答或报告样本并保留错误。"""
        try:
            if sample.qa_request is not None:
                prediction = (await self._qa_service.query(sample.qa_request)).model_dump(mode="json")
            else:
                prediction = (
                    await self._report_service.generate_inline(
                        sample.report_request,
                        report_id=f"evaluation-{run_id}-{sample.sample_id}",
                    )
                ).model_dump(mode="json")
            metric = evaluate_sample(sample, prediction)
            return QualitySampleResult(
                sample_id=sample.sample_id,
                sample_type=sample.sample_type.value,
                domain=sample.domain,
                review_status=sample.review_status.value,
                input=_sample_input(sample),
                prediction=prediction,
                expected=sample.expected.model_dump(mode="json"),
                metrics=metric,
            )
        except Exception as exc:
            return QualitySampleResult(
                sample_id=sample.sample_id,
                sample_type=sample.sample_type.value,
                domain=sample.domain,
                review_status=sample.review_status.value,
                input=_sample_input(sample),
                expected=sample.expected.model_dump(mode="json"),
                error=type(exc).__name__,
            )


def _regression_gate(strict_metrics: QualityMetrics) -> QualityRegressionGate:
    """仅在存在人工复核样本时应用零幻觉和完整性门禁。"""
    if strict_metrics.sample_count == 0:
        return QualityRegressionGate(
            eligible=False,
            passed=False,
            reasons=["没有 human_verified 样本，不能形成严格发布结论"],
        )
    reasons = []
    if strict_metrics.hallucination_rate != 0:
        reasons.append("人工 Golden 幻觉率不为 0")
    if strict_metrics.evidence_coverage.total and strict_metrics.evidence_coverage.rate != 1:
        reasons.append("人工 Golden 证据覆盖率未达到 100%")
    if strict_metrics.report_completeness.total and strict_metrics.report_completeness.rate != 1:
        reasons.append("人工 Golden 报告章节完整率未达到 100%")
    if strict_metrics.strict_pass_rate.rate != 1:
        reasons.append("存在人工 Golden 严格失败样本")
    return QualityRegressionGate(eligible=True, passed=not reasons, reasons=reasons)


def aggregate_grouped_metrics(samples, results, key_builder) -> dict[str, QualityMetrics]:
    """按数据域、样本类型或复核状态聚合严格结构化指标。"""
    grouped = {}
    for sample, result in zip(samples, results, strict=True):
        key = key_builder(sample)
        grouped.setdefault(key, ([], []))
        grouped[key][0].append(sample)
        grouped[key][1].append(result)
    metrics = {}
    for key, (group_samples, group_results) in grouped.items():
        metrics[key] = aggregate_metrics(group_samples, group_results)[0]
    return metrics


def _sample_input(sample: EvaluationSample) -> dict:
    """保留逐题详情需要的脱敏问答或报告输入。"""
    request = sample.qa_request or sample.report_request
    return request.model_dump(mode="json")


def _configuration_checksum() -> str:
    """计算不包含凭据的质量评测配置校验和。"""
    payload = {
        "code_version": os.getenv("YUXI_VERSION", "0.7.1"),
        "evaluation_config_version": os.getenv("COMPANY_DD_EVALUATION_CONFIG_VERSION", "default"),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()
