"""在已启动容器内运行企业尽调质量评测。"""

from __future__ import annotations

import argparse
import asyncio

from yuxi.company_due_diligence.evaluation.dataset import EvaluationDatasetRepository
from yuxi.company_due_diligence.evaluation.runner import QualityEvaluationRunner


async def main() -> None:
    """运行指定冻结数据集并输出 JSON 报告。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset_id")
    parser.add_argument("--max-samples", type=int)
    parser.add_argument("--strategy-label", default="default")
    args = parser.parse_args()
    dataset = EvaluationDatasetRepository().get(args.dataset_id)
    report = await QualityEvaluationRunner().run(
        dataset,
        run_id="cli-quality-evaluation",
        max_samples=args.max_samples,
        strategy_label=args.strategy_label,
    )
    print(report.model_dump_json(indent=2))


if __name__ == "__main__":
    asyncio.run(main())
