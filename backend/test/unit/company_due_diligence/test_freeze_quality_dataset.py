"""企业尽调 Golden 冻结脚本测试。"""

import json

import pytest

from scripts.company_due_diligence.freeze_quality_dataset import freeze_dataset
from yuxi.company_due_diligence.evaluation.dataset import EvaluationDatasetRepository


def _write_dataset(path, declared_checksum=None) -> None:
    """写入最小未人工复核数据集。"""
    payload = {
        "dataset_id": "freeze-test-v1",
        "name": "冻结工具测试",
        "version": "1.0.0",
        "source_kind": "guangdong_enterprise",
        "business_as_of": "2026-08-10",
        "created_at": "2026-08-10T00:00:00Z",
        "declared_checksum": declared_checksum,
        "samples": [
            {
                "sample_id": "refusal",
                "sample_type": "qa",
                "domain": "entity_resolution",
                "review_status": "source_derived_unreviewed",
                "qa_request": {
                    "company_ref": {"name": "__不存在企业__"},
                    "question": "该企业是否存在？",
                },
                "expected": {"refused": True},
            }
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_freeze_dataset_writes_valid_checksum_without_sample_content(tmp_path):
    """冻结工具应原子写 checksum，摘要不返回样本内容。"""
    path = tmp_path / "dataset.json"
    _write_dataset(path)

    summary = freeze_dataset(path, write=True)
    loaded = EvaluationDatasetRepository(tmp_path).get("freeze-test-v1")

    assert loaded.declared_checksum == loaded.checksum == summary["checksum"]
    assert summary["release_gate_eligible"] is False
    assert "samples" not in summary


def test_freeze_dataset_rejects_stale_checksum_without_write(tmp_path):
    """只校验模式遇到陈旧 checksum 时必须显式失败。"""
    path = tmp_path / "dataset.json"
    _write_dataset(path, declared_checksum="0" * 64)

    with pytest.raises(ValueError, match="checksum 不一致"):
        freeze_dataset(path, write=False)
