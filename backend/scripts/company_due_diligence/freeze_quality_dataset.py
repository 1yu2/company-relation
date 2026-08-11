"""校验并冻结企业尽调人工 Golden 数据集。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from yuxi.company_due_diligence.evaluation.dataset import EvaluationDataset, ReviewStatus


def main() -> None:
    """校验数据集，并按需写入当前稳定 checksum。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--write", action="store_true", help="将计算结果写入 declared_checksum")
    args = parser.parse_args()
    summary = freeze_dataset(args.path, write=args.write)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def freeze_dataset(path: Path, *, write: bool) -> dict:
    """返回不含样本内容的冻结摘要，并可原子更新声明 checksum。"""
    payload = json.loads(path.read_text(encoding="utf-8"))
    declared_checksum = payload.get("declared_checksum")
    payload["declared_checksum"] = None
    dataset = EvaluationDataset.model_validate(payload)
    checksum = dataset.checksum
    if declared_checksum and declared_checksum != checksum and not write:
        raise ValueError(f"评测数据集 checksum 不一致: {dataset.dataset_id}")

    if write:
        frozen_payload = dataset.model_dump(mode="json")
        frozen_payload["declared_checksum"] = checksum
        temporary_path = path.with_suffix(f"{path.suffix}.tmp")
        temporary_path.write_text(
            json.dumps(frozen_payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(path)

    human_review_count = sum(sample.review_status == ReviewStatus.HUMAN_VERIFIED for sample in dataset.samples)
    return {
        "dataset_id": dataset.dataset_id,
        "version": dataset.version,
        "checksum": checksum,
        "checksum_written": write,
        "sample_count": len(dataset.samples),
        "human_review_count": human_review_count,
        "human_review_rate": dataset.human_review_rate,
        "release_gate_eligible": human_review_count > 0,
    }


if __name__ == "__main__":
    main()
