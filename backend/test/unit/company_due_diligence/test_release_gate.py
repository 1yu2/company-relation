"""企业尽调发布门禁纯逻辑测试。"""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from scripts.company_due_diligence.generate_release_gate import (
    _blockers,
    _render_markdown,
    _select_human_quality_dataset,
)
from yuxi.company_due_diligence.evaluation.dataset import DatasetSourceKind


def test_online_llm_unavailable_is_a_limitation_not_a_release_blocker():
    """在线 LLM 未启用时应记录限制，但不额外阻塞当前发布口径。"""
    human_gate = {"configured": True, "eligible": True, "passed": True, "reasons": []}

    assert _blockers(human_gate, admin_e2e_passed=True) == []


def test_release_gate_selects_latest_frozen_human_dataset():
    """自动门禁应跳过开发烟测和未冻结数据集，选择最新正式 Golden。"""
    datasets = [
        _dataset("smoke", "2026-08-08T00:00:00+00:00", checksum=None, review_rate=0),
        _dataset("formal-old", "2026-08-09T00:00:00+00:00"),
        _dataset("formal-new", "2026-08-10T00:00:00+00:00"),
    ]
    repository = SimpleNamespace(list=lambda: datasets)

    selected = _select_human_quality_dataset(repository, requested_id=None)

    assert selected.dataset_id == "formal-new"


def test_release_gate_rejects_explicit_unfrozen_human_dataset():
    """显式指定的正式 Golden 未冻结时应立即失败。"""
    dataset = _dataset("unfrozen", "2026-08-10T00:00:00+00:00", checksum=None)
    repository = SimpleNamespace(get=lambda _: dataset)

    with pytest.raises(ValueError, match="已冻结 checksum"):
        _select_human_quality_dataset(repository, requested_id="unfrozen")


def _dataset(dataset_id: str, created_at: str, *, checksum: str | None = "a" * 64, review_rate: float = 1):
    """构造发布门禁选择逻辑所需的最小数据集对象。"""
    return SimpleNamespace(
        dataset_id=dataset_id,
        created_at=datetime.fromisoformat(created_at).astimezone(UTC),
        source_kind=DatasetSourceKind.GUANGDONG_ENTERPRISE,
        declared_checksum=checksum,
        human_review_rate=review_rate,
    )


def test_markdown_separates_blockers_and_limitations():
    """Markdown 应分别展示发布阻塞项和非阻塞限制。"""
    report = {
        "run_id": "release-test",
        "automated_gate_passed": True,
        "status": "blocked",
        "checks": {
            "akshare_public_baseline": {"passed": True},
            "guangdong_quality_smoke": {"passed": True},
            "human_verified_quality_gate": {
                "configured": False,
                "eligible": False,
                "passed": False,
                "reasons": ["没有正式 Golden"],
            },
            "safe_performance": {
                "passed": True,
                "scenario": {"latency": {"p95_ms": 100}},
                "pool": {"leak_detected": False},
            },
            "online_llm_report": {"status": "unavailable"},
        },
        "blockers": ["缺少人工 Golden"],
        "limitations": ["在线 LLM 未启用"],
    }

    markdown = _render_markdown(report)

    assert "## 发布阻塞项\n\n- 缺少人工 Golden" in markdown
    assert "## 当前限制\n\n- 在线 LLM 未启用" in markdown
    assert "在线 LLM 报告：unavailable（非发布阻塞）" in markdown
