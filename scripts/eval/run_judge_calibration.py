"""用人工标注的校准集评估 LLM Judge：输出一致率、Cohen's κ、混淆矩阵和不一致样本。

在 api 容器中运行（需要 Judge 模型的 API Key）：
    uv run python run_judge_calibration.py <labels.json>
labels.json：[{"question", "reference_answer", "rubric_points", "answer", "human_label": "pass"|"fail"}, ...]
建议至少 30 条，且 pass/fail 两类都有。是否达标（例如 κ ≥ 0.6）由人判断，达标后再打开 llm_judge.JUDGE_CALIBRATED。
"""

from __future__ import annotations

import asyncio
import json
import sys

from yuxi.evaluation import llm_judge
from yuxi.models import select_model


async def main(labels_path: str) -> None:
    samples = json.load(open(labels_path))
    judge = select_model(llm_judge.JUDGE_MODEL_SPEC)
    pairs = []
    for sample in samples:
        prompt = llm_judge.build_prompt(
            question=sample["question"],
            reference=sample.get("reference_answer") or "",
            points=sample.get("rubric_points") or [],
            answer=sample["answer"],
        )
        verdict = llm_judge.parse_verdict((await judge.call(prompt, stream=False)).content)
        pairs.append((sample["human_label"], verdict["verdict"] if verdict else None))
    report = llm_judge.calibration_report(pairs) | {"metric_version": llm_judge.METRIC_VERSION}
    report["disagreements"] = [
        {"index": index, "question": samples[index]["question"], "human": pairs[index][0], "judge": pairs[index][1]}
        for index in report["disagreements"]
    ]
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
