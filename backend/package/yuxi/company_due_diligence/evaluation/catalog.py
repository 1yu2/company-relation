"""企业尽调只读数据目录扫描与自动评测样本生成。"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from yuxi.company_due_diligence.agent.models import (
    CompanyInvestigationRequest,
    InvestigationTopic,
)
from yuxi.company_due_diligence.evaluation.dataset import (
    DatasetSourceKind,
    EvaluationDataset,
    EvaluationSample,
    EvaluationSampleType,
    GoldenExpected,
    ReviewStatus,
)
from yuxi.company_due_diligence.models import CompanyReference
from yuxi.company_due_diligence.mysql import (
    CompanyDueDiligenceMySQL,
    MySQLUnavailableError,
    company_due_diligence_mysql,
)
from yuxi.company_due_diligence.qa.models import CompanyQuestionRequest
from yuxi.company_due_diligence.report.models import DueDiligenceReportRequest, ReportSection
from yuxi.company_due_diligence.repositories.enterprise import EnterpriseRepository

_AUTO_CASE_FAMILIES = (
    "field_coverage",
    "relation_consistency",
    "evidence_provenance",
    "report_completeness",
    "refusal_and_ambiguity",
)
_FIELD_SAMPLE_SPECS = (
    ("profile", "t_enterprise", "该企业的基本信息、注册资本和经营状态是什么？"),
    ("shareholders", "t_partners_alls", "该企业的当前股东和出资情况有哪些？"),
    ("products", "t_company_products", "该企业有哪些产品或业务线？"),
    ("financing", "t_company_finance", "该企业有哪些融资历史和投资机构？"),
    ("risks", "t_administrative_punishment", "该企业有哪些行政处罚和经营风险？"),
    ("licenses", "t_license_info", "该企业有哪些行政许可？"),
    ("intellectual_property", "t_patents_relations_info", "该企业有哪些专利和知识产权？"),
)


class DataCatalogSnapshot(BaseModel):
    """描述一次全局数据目录扫描及其可复现信息。"""

    data_version: str
    scanned_at: datetime
    denominator: int = Field(ge=0)
    sampled_denominator: int = Field(ge=0)
    sampling_policy: str
    coverage: dict[str, int | float] = Field(default_factory=dict)
    source_tables: list[str] = Field(default_factory=list)
    source_record_keys: list[str] = Field(default_factory=list)
    checksum: str
    skipped_reasons: list[str] = Field(default_factory=list)


class AutoEvaluationDatasetBuilder:
    """从只读企业目录生成带 source-derived 标记的自动评测数据集。"""

    def __init__(self, mysql: CompanyDueDiligenceMySQL | None = None) -> None:
        self._mysql = mysql or company_due_diligence_mysql

    async def scan(self, *, sampling_policy: str = "all_or_stratified", max_samples: int = 1000) -> DataCatalogSnapshot:
        """扫描企业身份目录和固定数据域覆盖率。"""
        try:
            async with self._mysql.connection() as connection:
                repository = EnterpriseRepository(connection)
                coverage = await repository.get_catalog_coverage()
                denominator = int(coverage.get("enterprise_count") or 0)
                rows = await repository.list_catalog(limit=max(1, min(max_samples, 10000)))
        except MySQLUnavailableError:
            raise AutoDatasetUnavailableError("企业事实主库当前不可用，无法扫描自动评测目录") from None

        sampled_rows = _sample_rows(rows, denominator, sampling_policy, max_samples)
        source_record_keys = [str(row["eid"]) for row in sampled_rows]
        data_version = _data_version(coverage)
        skipped_reasons = []
        if len(sampled_rows) < denominator:
            skipped_reasons.append(f"目录分母 {denominator}，按 {sampling_policy} 抽取 {len(sampled_rows)} 条")
        payload = {
            "data_version": data_version,
            "denominator": denominator,
            "sampled_denominator": len(sampled_rows),
            "source_record_keys": source_record_keys,
        }
        return DataCatalogSnapshot(
            data_version=data_version,
            scanned_at=datetime.now(UTC),
            denominator=denominator,
            sampled_denominator=len(sampled_rows),
            sampling_policy=sampling_policy,
            coverage=_coverage_metrics(coverage, denominator),
            source_tables=[
                "t_enterprise",
                "t_partners_alls",
                "t_company_products",
                "t_company_finance",
                "t_administrative_punishment",
                "t_license_info",
                "t_patents_relations_info",
            ],
            source_record_keys=source_record_keys,
            checksum=hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest(),
            skipped_reasons=skipped_reasons,
        )

    async def build(
        self,
        *,
        dataset_version: str = "latest_frozen",
        sampling_policy: str = "all_or_stratified",
        max_samples: int = 1000,
        case_families: list[str] | None = None,
    ) -> EvaluationDataset:
        """扫描目录并生成可直接复用质量 runner 的自动数据集。"""
        snapshot = await self.scan(sampling_policy=sampling_policy, max_samples=max_samples)
        if snapshot.sampled_denominator == 0:
            raise AutoDatasetUnavailableError("企业数据目录为空，无法生成自动评测样本")
        selected_families = _selected_case_families(case_families)
        available_families, family_skips = _available_case_families(selected_families, snapshot.coverage)
        samples = _build_samples(snapshot, available_families, max_samples)
        if not samples:
            raise AutoDatasetUnavailableError("当前数据版本没有可生成的自动评测样本")
        return EvaluationDataset(
            dataset_id=f"auto-global-{snapshot.data_version}",
            name="全局企业尽调自动评测",
            version=dataset_version,
            source_kind=DatasetSourceKind.GUANGDONG_ENTERPRISE,
            business_as_of=snapshot.scanned_at.date(),
            created_at=snapshot.scanned_at,
            description="从只读企业目录自动生成的 source_derived 全局多样本族回归数据集。",
            scope="global",
            sampling_policy=snapshot.sampling_policy,
            denominator=snapshot.denominator,
            sampled_denominator=snapshot.sampled_denominator,
            skipped_reasons=[*snapshot.skipped_reasons, *family_skips],
            source_tables=snapshot.source_tables,
            source_record_keys=snapshot.source_record_keys,
            coverage_metrics=snapshot.coverage,
            declared_checksum=None,
            samples=samples,
        )


class AutoDatasetUnavailableError(RuntimeError):
    """表示自动评测所需的只读目录暂时不可用。"""


def _sample_rows(rows: list[dict], denominator: int, policy: str, max_samples: int) -> list[dict]:
    """按固定顺序执行 all 或稳定分层抽样。"""
    if policy == "all_or_stratified" and denominator <= max_samples:
        return rows[:max_samples]
    if not rows:
        return []
    step = max(1, len(rows) // max_samples)
    return rows[::step][:max_samples]


def _data_version(coverage: dict) -> str:
    """从目录计数与最新更新时间生成数据版本。"""
    payload = f"{coverage.get('enterprise_count', 0)}:{coverage.get('latest_update_time', '')}"
    return f"catalog-{hashlib.sha256(payload.encode()).hexdigest()[:16]}"


def _coverage_metrics(coverage: dict, denominator: int) -> dict[str, int | float]:
    """计算自动评测需要的全局覆盖指标。"""
    fields = {
        "enterprise_identity": "named_enterprise_count",
        "listed_company": "listed_company_count",
        "shareholders": "shareholders_company_count",
        "products": "products_company_count",
        "financing": "financing_company_count",
        "risks": "risks_company_count",
        "licenses": "licenses_company_count",
        "intellectual_property": "intellectual_property_company_count",
    }
    result: dict[str, int | float] = {}
    for domain, field in fields.items():
        count = int(coverage.get(field) or 0)
        result[f"{domain}_company_count"] = count
        result[f"{domain}_company_coverage_rate"] = round(count / denominator, 6) if denominator else 0.0
    for domain in ("shareholders", "products", "financing", "risks", "licenses", "intellectual_property"):
        result[f"{domain}_record_count"] = int(coverage.get(f"{domain}_record_count") or 0)
    for field in ("credit_code", "registered_capital", "legal_representative", "business_status"):
        count = int(coverage.get(f"{field}_company_count") or 0)
        result[f"{field}_non_empty_count"] = count
        result[f"{field}_non_empty_rate"] = round(count / denominator, 6) if denominator else 0.0
    return result


def _selected_case_families(case_families: list[str] | None) -> list[str]:
    """校验并保持自动样本族的请求顺序。"""
    selected = list(dict.fromkeys(case_families or _AUTO_CASE_FAMILIES))
    unsupported = [family for family in selected if family not in _AUTO_CASE_FAMILIES]
    if unsupported:
        raise ValueError(f"不支持的自动评测样本族: {', '.join(unsupported)}")
    return selected


def _available_case_families(
    selected_families: list[str],
    coverage: dict[str, int | float],
) -> tuple[list[str], list[str]]:
    """根据目录覆盖决定可生成的样本族，并记录跳过原因。"""
    availability = {
        "field_coverage": bool(coverage.get("enterprise_identity_company_count")),
        "relation_consistency": bool(coverage.get("shareholders_company_count")),
        "evidence_provenance": bool(
            coverage.get("risks_company_count")
            or coverage.get("licenses_company_count")
            or coverage.get("intellectual_property_company_count")
        ),
        "report_completeness": bool(coverage.get("enterprise_identity_company_count")),
        "refusal_and_ambiguity": True,
    }
    available = [family for family in selected_families if availability[family]]
    skipped = [
        f"{family}：当前数据版本没有可生成的来源记录" for family in selected_families if not availability[family]
    ]
    return available, skipped


def _build_samples(
    snapshot: DataCatalogSnapshot,
    case_families: list[str],
    max_samples: int,
) -> list[EvaluationSample]:
    """按固定家族顺序和企业顺序生成可复现的 source-derived 样本。"""
    if not case_families:
        return []
    eids = snapshot.source_record_keys
    sample_units = [(family, 0) for family in case_families]
    if "field_coverage" in case_families:
        sample_units.extend(("field_coverage", index) for index in range(1, len(_FIELD_SAMPLE_SPECS)))
    sample_limit = min(max_samples, max(1, len(eids)) * len(sample_units))
    samples = []
    unit_positions = {unit: 0 for unit in sample_units}
    for index in range(sample_limit):
        family, field_variant = sample_units[index % len(sample_units)]
        unit = (family, field_variant)
        family_position = unit_positions[unit]
        unit_positions[unit] += 1
        eid = eids[family_position % len(eids)]
        samples.append(_build_sample(snapshot, family, eid, family_position, field_variant))
    return samples


def _build_sample(
    snapshot: DataCatalogSnapshot,
    family: str,
    eid: str,
    family_position: int,
    field_variant: int,
) -> EvaluationSample:
    """构造一个指定样本族的请求、评测目标和来源审计字段。"""
    common = {
        "review_status": ReviewStatus.SOURCE_DERIVED,
        "expected_origin": "source_derived",
        "data_version": snapshot.data_version,
        "sampling_policy": snapshot.sampling_policy,
        "reviewer_note": "source_derived：由只读数据目录自动生成，未人工复核。",
    }
    if family == "field_coverage":
        domain, source_table, question = _FIELD_SAMPLE_SPECS[field_variant]
        return EvaluationSample(
            sample_id=f"source-derived-{family}-{domain}-{eid}",
            sample_type=EvaluationSampleType.QA,
            domain=domain,
            qa_request=CompanyQuestionRequest(company_ref=CompanyReference(eid=eid), question=question),
            expected=GoldenExpected(entity_eids=[eid], refused=False),
            source_table=source_table,
            source_record_key=eid,
            source_checksum=_source_checksum(snapshot.data_version, source_table, eid),
            **common,
        )
    if family == "relation_consistency":
        source_table = "t_partners_alls"
        return EvaluationSample(
            sample_id=f"source-derived-{family}-{family_position}",
            sample_type=EvaluationSampleType.QA,
            domain="relations",
            qa_request=CompanyQuestionRequest(question="当前数据中哪些企业存在共同股东关系？"),
            expected=GoldenExpected(refused=False),
            source_table=source_table,
            source_record_key="global",
            source_checksum=_source_checksum(snapshot.data_version, source_table, "global"),
            **common,
        )
    if family == "evidence_provenance":
        source_table = "t_administrative_punishment"
        return EvaluationSample(
            sample_id=f"source-derived-{family}-{eid}",
            sample_type=EvaluationSampleType.QA,
            domain="evidence",
            qa_request=CompanyQuestionRequest(
                company_ref=CompanyReference(eid=eid),
                question="该企业有哪些行政处罚依据或详情？",
            ),
            expected=GoldenExpected(entity_eids=[eid], refused=False),
            source_table=source_table,
            source_record_key=eid,
            source_checksum=_source_checksum(snapshot.data_version, source_table, eid),
            **common,
        )
    if family == "report_completeness":
        sections = [
            ReportSection.IDENTITY,
            ReportSection.BUSINESS,
            ReportSection.SHAREHOLDERS,
            ReportSection.DATA_QUALITY,
        ]
        return EvaluationSample(
            sample_id=f"source-derived-{family}-{eid}",
            sample_type=EvaluationSampleType.REPORT,
            domain="report",
            report_request=DueDiligenceReportRequest(
                company_ref=CompanyReference(eid=eid),
                sections=sections,
            ),
            expected=GoldenExpected(required_report_sections=sections),
            source_table="t_enterprise",
            source_record_key=eid,
            source_checksum=_source_checksum(snapshot.data_version, "t_enterprise", eid),
            **common,
        )
    return EvaluationSample(
        sample_id=f"source-derived-{family}-{family_position}",
        sample_type=EvaluationSampleType.INVESTIGATION,
        domain="refusal_and_ambiguity",
        investigation_request=CompanyInvestigationRequest(question="注册资本是多少？"),
        expected=GoldenExpected(
            refused=True,
            topics=[InvestigationTopic.PROFILE],
        ),
        **common,
    )


def _source_checksum(data_version: str, source_table: str, source_record_key: str) -> str:
    """计算来源版本、表和记录键的稳定校验和。"""
    return hashlib.sha256(f"{data_version}:{source_table}:{source_record_key}".encode()).hexdigest()


auto_evaluation_dataset_builder = AutoEvaluationDatasetBuilder()
