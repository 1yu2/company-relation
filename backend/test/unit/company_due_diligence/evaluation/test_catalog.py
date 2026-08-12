"""全局数据目录扫描与自动样本生成单元测试。"""

from contextlib import asynccontextmanager
from datetime import datetime

import pytest

from yuxi.company_due_diligence.evaluation.catalog import AutoEvaluationDatasetBuilder
from yuxi.company_due_diligence.evaluation.dataset import ReviewStatus
from yuxi.company_due_diligence.repositories.enterprise import EnterpriseRepository

pytestmark = pytest.mark.asyncio


class FakeMySQL:
    """提供不访问真实数据库的只读连接上下文。"""

    @asynccontextmanager
    async def connection(self):
        yield object()


async def test_auto_dataset_records_global_denominator_sources_and_checksum(monkeypatch):
    """自动数据集应保存目录分母、来源键、抽样策略和 source-derived 标记。"""

    async def get_catalog_coverage(_repository):
        return {
            "enterprise_count": 2,
            "named_enterprise_count": 2,
            "listed_company_count": 1,
            "shareholders_company_count": 2,
            "products_company_count": 1,
            "financing_company_count": 1,
            "risks_company_count": 1,
            "licenses_company_count": 1,
            "intellectual_property_company_count": 1,
            "credit_code_company_count": 1,
            "registered_capital_company_count": 2,
            "legal_representative_company_count": 2,
            "business_status_company_count": 2,
            "shareholders_record_count": 4,
            "products_record_count": 1,
            "financing_record_count": 1,
            "risks_record_count": 1,
            "licenses_record_count": 1,
            "intellectual_property_record_count": 1,
            "latest_update_time": datetime(2026, 8, 11),
        }

    async def list_catalog(_repository, limit, offset=0):
        del limit, offset
        return [
            {"eid": "eid-1", "name": "企业一", "credit_no": "credit-1"},
            {"eid": "eid-2", "name": "企业二", "credit_no": None},
        ]

    monkeypatch.setattr(EnterpriseRepository, "get_catalog_coverage", get_catalog_coverage)
    monkeypatch.setattr(EnterpriseRepository, "list_catalog", list_catalog)
    builder = AutoEvaluationDatasetBuilder(mysql=FakeMySQL())

    dataset = await builder.build(max_samples=12)

    assert dataset.denominator == 2
    assert dataset.sampled_denominator == 2
    assert dataset.source_tables[0] == "t_enterprise"
    assert dataset.source_record_keys == ["eid-1", "eid-2"]
    assert len(dataset.checksum) == 64
    assert {sample.review_status for sample in dataset.samples} == {ReviewStatus.SOURCE_DERIVED}
    assert {sample.domain for sample in dataset.samples} == {
        "profile",
        "shareholders",
        "products",
        "financing",
        "risks",
        "licenses",
        "intellectual_property",
        "relations",
        "evidence",
        "report",
        "refusal_and_ambiguity",
    }
    assert {sample.sample_type.value for sample in dataset.samples} == {"qa", "report", "investigation"}
    assert all(len(sample.source_checksum) == 64 for sample in dataset.samples if sample.source_checksum)
    assert dataset.coverage_metrics["licenses_company_coverage_rate"] == 0.5
    assert dataset.coverage_metrics["credit_code_non_empty_rate"] == 0.5


async def test_auto_dataset_honors_selected_case_families(monkeypatch):
    """调用方选择样本族时只生成对应的自动样本。"""

    async def get_catalog_coverage(_repository):
        return {
            "enterprise_count": 1,
            "named_enterprise_count": 1,
            "shareholders_company_count": 1,
            "latest_update_time": datetime(2026, 8, 11),
        }

    async def list_catalog(_repository, limit, offset=0):
        del limit, offset
        return [{"eid": "eid-1", "name": "企业一", "credit_no": "credit-1"}]

    monkeypatch.setattr(EnterpriseRepository, "get_catalog_coverage", get_catalog_coverage)
    monkeypatch.setattr(EnterpriseRepository, "list_catalog", list_catalog)

    dataset = await AutoEvaluationDatasetBuilder(mysql=FakeMySQL()).build(
        max_samples=5,
        case_families=["relation_consistency", "refusal_and_ambiguity"],
    )

    assert {sample.domain for sample in dataset.samples} == {"relations", "refusal_and_ambiguity"}
