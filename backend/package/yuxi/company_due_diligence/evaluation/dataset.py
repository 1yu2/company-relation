"""企业尽调 Golden 数据集格式、校验和本地冻结仓储。"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import date, datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field, JsonValue, model_validator

from yuxi.company_due_diligence.agent.models import (
    CompanyInvestigationRequest,
    DeliveryMethod,
    InvestigationDepth,
    InvestigationTopic,
    PresentationType,
    TimeScope,
)
from yuxi.company_due_diligence.qa.models import CompanyQuestionRequest
from yuxi.company_due_diligence.report.models import DueDiligenceReportRequest, ReportSection


class DatasetSourceKind(StrEnum):
    """区分广东企业新 Golden 与历史 AkShare 公开基线。"""

    GUANGDONG_ENTERPRISE = "guangdong_enterprise"
    AKSHARE_PUBLIC_BASELINE = "akshare_public_baseline"


class ReviewStatus(StrEnum):
    """描述 Golden 是否完成独立人工复核。"""

    HUMAN_VERIFIED = "human_verified"
    SOURCE_DERIVED_UNREVIEWED = "source_derived_unreviewed"


class EvaluationSampleType(StrEnum):
    """描述质量样本调用问答还是报告服务。"""

    QA = "qa"
    REPORT = "report"
    INVESTIGATION = "investigation"


class ExpectedFact(BaseModel):
    """描述集合级严格比较使用的规范化事实。"""

    subject_id: str
    fact_type: str
    normalized_value: JsonValue
    unit: str | None = None
    effective_date: date | None = None


class ExpectedRelation(BaseModel):
    """描述关系方向、类型和端点 Golden。"""

    source_entity_id: str
    target_entity_id: str
    relation_type: str


class ExpectedPathEdge(BaseModel):
    """描述路径中一条有方向边。"""

    source_entity_id: str
    target_entity_id: str
    relation_type: str


class ExpectedPath(BaseModel):
    """描述节点序列、边序列和跳数 Golden。"""

    node_ids: list[str]
    edges: list[ExpectedPathEdge]
    hop_count: int = Field(ge=1, le=3)


class GoldenExpected(BaseModel):
    """描述按样本选择启用的严格比较目标。"""

    refused: bool | None = None
    facts: list[ExpectedFact] | None = None
    relations: list[ExpectedRelation] | None = None
    paths: list[ExpectedPath] | None = None
    evidence_source_ids: list[str] | None = None
    required_report_sections: list[ReportSection] | None = None
    forbidden_report_sections: list[ReportSection] | None = None
    expect_stale_declaration: bool | None = None
    entity_eids: list[str] | None = None
    topics: list[InvestigationTopic] | None = None
    time_scope: TimeScope | None = None
    depth: InvestigationDepth | None = None
    presentation: PresentationType | None = None
    delivery: DeliveryMethod | None = None
    required_tools: list[str] | None = None
    forbidden_tools: list[str] | None = None
    claim_ledger_consistent: bool | None = None
    required_topics: list[InvestigationTopic] | None = None
    forbidden_topics: list[InvestigationTopic] | None = None

    @model_validator(mode="after")
    def require_metric_target(self):
        """拒绝没有任何可评判目标的 Golden。"""
        if all(
            value is None
            for value in (
                self.refused,
                self.facts,
                self.relations,
                self.paths,
                self.evidence_source_ids,
                self.required_report_sections,
                self.forbidden_report_sections,
                self.expect_stale_declaration,
                self.entity_eids,
                self.topics,
                self.time_scope,
                self.depth,
                self.presentation,
                self.delivery,
                self.required_tools,
                self.forbidden_tools,
                self.claim_ledger_consistent,
                self.required_topics,
                self.forbidden_topics,
            )
        ):
            raise ValueError("Golden 至少需要一个严格评测目标")
        return self


class EvaluationSample(BaseModel):
    """描述单条问答或报告 Golden 样本。"""

    sample_id: str = Field(min_length=1, max_length=128)
    sample_type: EvaluationSampleType
    domain: str = Field(min_length=1, max_length=64)
    review_status: ReviewStatus
    qa_request: CompanyQuestionRequest | None = None
    report_request: DueDiligenceReportRequest | None = None
    investigation_request: CompanyInvestigationRequest | None = None
    expected: GoldenExpected
    reviewer_note: str | None = Field(default=None, max_length=2000)
    reviewed_by: str | None = Field(default=None, min_length=1, max_length=128)
    reviewed_at: datetime | None = None

    @model_validator(mode="after")
    def validate_request_type(self):
        """确保样本类型和请求对象严格一一对应。"""
        requests = [self.qa_request, self.report_request, self.investigation_request]
        if self.sample_type == EvaluationSampleType.QA and (
            self.qa_request is None or any(request is not None for request in requests[1:])
        ):
            raise ValueError("qa 样本必须且只能提供 qa_request")
        if self.sample_type == EvaluationSampleType.REPORT and (
            self.report_request is None or self.qa_request is not None or self.investigation_request is not None
        ):
            raise ValueError("report 样本必须且只能提供 report_request")
        if self.sample_type == EvaluationSampleType.INVESTIGATION and (
            self.investigation_request is None or self.qa_request is not None or self.report_request is not None
        ):
            raise ValueError("investigation 样本必须且只能提供 investigation_request")
        if self.review_status == ReviewStatus.HUMAN_VERIFIED:
            if self.reviewed_by is None or self.reviewed_at is None:
                raise ValueError("human_verified 样本必须记录 reviewed_by 和 reviewed_at")
            if not self.reviewer_note or not self.reviewer_note.strip():
                raise ValueError("human_verified 样本必须记录 reviewer_note")
        elif self.reviewed_by is not None or self.reviewed_at is not None:
            raise ValueError("未人工复核样本不能填写 reviewed_by 或 reviewed_at")
        return self


class EvaluationDataset(BaseModel):
    """描述带版本、时点、来源类别和人工复核率的冻结数据集。"""

    dataset_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=256)
    version: str = Field(min_length=1, max_length=64)
    source_kind: DatasetSourceKind
    business_as_of: date | None = None
    created_at: datetime
    description: str = Field(default="", max_length=4000)
    declared_checksum: str | None = Field(default=None, min_length=64, max_length=64)
    samples: list[EvaluationSample] = Field(min_length=1)

    @property
    def checksum(self) -> str:
        """返回不包含 declared_checksum 的稳定数据集校验和。"""
        payload = self.model_dump(mode="json", exclude={"declared_checksum"})
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    @property
    def human_review_rate(self) -> float:
        """返回人工复核样本占比。"""
        reviewed = sum(sample.review_status == ReviewStatus.HUMAN_VERIFIED for sample in self.samples)
        return reviewed / len(self.samples)


class EvaluationDatasetRepository:
    """从仓库冻结目录读取脱敏 Golden 数据集。"""

    def __init__(self, root: Path | None = None) -> None:
        self._root = root or Path(
            os.getenv("COMPANY_DD_EVALUATION_DATASET_DIR", "data/company_due_diligence/evaluation")
        )

    def list(self) -> list[EvaluationDataset]:
        """按数据集 ID 返回全部有效 JSON 数据集。"""
        if not self._root.exists():
            return []
        datasets = [self._load(path) for path in sorted(self._root.glob("*.json"))]
        return sorted(datasets, key=lambda item: item.dataset_id)

    def get(self, dataset_id: str) -> EvaluationDataset:
        """按 ID 返回唯一数据集。"""
        matches = [dataset for dataset in self.list() if dataset.dataset_id == dataset_id]
        if not matches:
            raise FileNotFoundError(f"评测数据集不存在: {dataset_id}")
        if len(matches) > 1:
            raise ValueError(f"评测数据集 ID 重复: {dataset_id}")
        return matches[0]

    @staticmethod
    def _load(path: Path) -> EvaluationDataset:
        """加载并校验声明 checksum。"""
        dataset = EvaluationDataset.model_validate_json(path.read_bytes())
        if dataset.declared_checksum and dataset.declared_checksum != dataset.checksum:
            raise ValueError(f"评测数据集 checksum 不一致: {dataset.dataset_id}")
        sample_ids = [sample.sample_id for sample in dataset.samples]
        if len(sample_ids) != len(set(sample_ids)):
            raise ValueError(f"评测数据集样本 ID 重复: {dataset.dataset_id}")
        return dataset
