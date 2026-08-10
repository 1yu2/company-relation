from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from yuxi.company_relation.evaluation import CompanyRelationEvaluationService
from yuxi.company_relation.neo4j_repository import CompanyRelationNeo4jRepository
from yuxi.company_relation.paths import (
    get_default_questions_path,
    get_default_results_dir,
    get_default_snapshot_path,
)
from yuxi.company_relation.query_service import CompanyRelationQueryService
from yuxi.company_relation.snapshot import CompanyRelationSnapshot


def parse_args() -> argparse.Namespace:
    """解析企业关系整体评测参数。"""
    parser = argparse.ArgumentParser(description="运行纯向量 RAG、GraphRAG 与 Cypher 直查对照评测。")
    parser.add_argument("--snapshot", type=Path, default=get_default_snapshot_path())
    parser.add_argument("--questions", type=Path, default=get_default_questions_path())
    parser.add_argument("--output-dir", type=Path, default=get_default_results_dir())
    parser.add_argument("--skip-import", action="store_true", help="复用已导入的同 dataset_id Neo4j 数据。")
    return parser.parse_args()


async def run() -> None:
    """导入快照并执行三策略完整评测。"""
    args = parse_args()
    snapshot = CompanyRelationSnapshot.load(args.snapshot)
    repository = CompanyRelationNeo4jRepository()
    query_service = CompanyRelationQueryService(snapshot=snapshot, neo4j_repository=repository)
    if not args.skip_import:
        await query_service.import_snapshot()

    report = await CompanyRelationEvaluationService(query_service).run(
        questions_path=args.questions,
        output_dir=args.output_dir,
    )
    print(
        json.dumps(
            {
                "run": report["run"],
                "strategy_summaries": report["strategy_summaries"],
                "regression_gate": report["regression_gate"],
                "artifacts": report["artifacts"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(run())
