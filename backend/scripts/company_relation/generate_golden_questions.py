from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from yuxi.company_relation.dataset_builder import generate_golden_questions, write_golden_questions
from yuxi.company_relation.evaluation import file_checksum
from yuxi.company_relation.paths import get_default_questions_path, get_default_snapshot_path
from yuxi.company_relation.snapshot import CompanyRelationSnapshot


def parse_args() -> argparse.Namespace:
    """解析 Golden 题库生成参数。"""
    parser = argparse.ArgumentParser(description="从冻结企业关系快照生成 120 道分层 Golden 题库。")
    parser.add_argument("--snapshot", type=Path, default=get_default_snapshot_path())
    parser.add_argument("--output", type=Path, default=get_default_questions_path())
    return parser.parse_args()


async def run() -> None:
    """生成并写入冻结 Golden 题库。"""
    args = parse_args()
    snapshot = CompanyRelationSnapshot.load(args.snapshot)
    questions = await generate_golden_questions(snapshot)
    write_golden_questions(questions, args.output)
    print(
        json.dumps(
            {
                "question_count": len(questions),
                "question_checksum": file_checksum(args.output),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    asyncio.run(run())
