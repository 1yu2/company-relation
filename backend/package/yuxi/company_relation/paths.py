from __future__ import annotations

import os
from pathlib import Path


def get_company_relation_data_dir() -> Path:
    """返回企业关系数据目录，允许部署环境显式覆盖。"""
    configured_path = os.environ.get("COMPANY_RELATION_DATA_DIR")
    if configured_path:
        return Path(configured_path).expanduser().resolve()
    return Path(__file__).resolve().parents[3] / "data" / "company_relation"


def get_default_snapshot_path() -> Path:
    """返回默认冻结图谱快照路径。"""
    return get_company_relation_data_dir() / "snapshot.json"


def get_default_questions_path() -> Path:
    """返回默认 Golden 题库路径。"""
    return get_company_relation_data_dir() / "golden_questions.jsonl"


def get_default_results_dir() -> Path:
    """返回默认评测结果目录。"""
    return get_company_relation_data_dir() / "results"
