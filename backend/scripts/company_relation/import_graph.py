from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from yuxi.company_relation.neo4j_repository import CompanyRelationNeo4jRepository
from yuxi.company_relation.paths import get_default_snapshot_path
from yuxi.company_relation.snapshot import CompanyRelationSnapshot


def parse_args() -> argparse.Namespace:
    """解析 Neo4j 导入参数。"""
    parser = argparse.ArgumentParser(description="将冻结企业关系快照导入 Neo4j。")
    parser.add_argument("--snapshot", type=Path, default=get_default_snapshot_path())
    return parser.parse_args()


async def run() -> None:
    """校验并替换导入企业关系图谱。"""
    args = parse_args()
    snapshot = CompanyRelationSnapshot.load(args.snapshot)
    repository = CompanyRelationNeo4jRepository()
    result = await repository.replace_snapshot(snapshot)
    stats = await repository.get_stats(snapshot.dataset_id)
    print(json.dumps({"imported": result, "neo4j": stats}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(run())
