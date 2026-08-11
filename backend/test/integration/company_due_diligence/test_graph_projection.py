"""企业尽调 Neo4j 投影真实集成测试。"""

from pathlib import Path

import pytest
from sqlalchemy import text

from yuxi.company_due_diligence.graph.projection import GraphProjectionBuilder
from yuxi.company_due_diligence.graph.query_service import GraphPathQueryService
from yuxi.company_due_diligence.graph.repository import CompanyDueDiligenceGraphRepository
from yuxi.company_due_diligence.graph.schema import GraphPathQuery, GraphRelationType
from yuxi.company_due_diligence.mysql import CompanyDueDiligenceMySQL

pytestmark = pytest.mark.asyncio


async def test_projection_is_idempotent_rebuildable_and_filters_clues(tmp_path: Path):
    """小范围真实投影应支持重复导入、删除重建、严格路径和线索模式。"""
    mysql = CompanyDueDiligenceMySQL()
    repository = CompanyDueDiligenceGraphRepository()
    previous_info = await repository.get_info()
    snapshot = None
    projection_version = None
    preexisting_same_version = False
    try:
        shared_eids, financing_eid = await _select_test_eids(mysql)
        projection_eids = list(dict.fromkeys([*shared_eids, financing_eid]))
        builder = GraphProjectionBuilder(mysql=mysql, snapshot_root=tmp_path)
        snapshot = await builder.build_snapshot(eids=projection_eids, batch_size=200)
        projection_version = snapshot.manifest.projection_version
        preexisting_same_version = bool(
            previous_info.current and previous_info.current.projection_version == projection_version
        )

        first = await repository.replace_projection(snapshot, batch_size=200)
        second = await repository.replace_projection(snapshot, batch_size=200)
        current = await repository.get_info()
        path_service = GraphPathQueryService(repository)
        shared_path = await path_service.query(
            GraphPathQuery(
                start_eid=shared_eids[0],
                target_eid=shared_eids[1],
                relation_types=[GraphRelationType.HOLDS],
                max_hops=2,
            )
        )
        strict_investor = await path_service.query(
            GraphPathQuery(
                start_eid=financing_eid,
                relation_types=[GraphRelationType.INVESTED_IN],
                max_hops=1,
            )
        )
        clue_investor = await path_service.query(
            GraphPathQuery(
                start_eid=financing_eid,
                relation_types=[GraphRelationType.INVESTED_IN],
                max_hops=1,
                include_clues=True,
            )
        )

        assert first.projection_version == second.projection_version
        assert first.statistics == second.statistics
        assert current.current.projection_version == projection_version
        assert shared_path.paths
        assert shared_path.paths[0].hop_count == 2
        assert all(edge.confidence.value == "high" for edge in shared_path.paths[0].edges)
        assert all(edge.source_ids for edge in shared_path.paths[0].edges)
        assert strict_investor.paths == []
        assert clue_investor.paths
        assert all(path.hop_count <= 3 for path in clue_investor.paths)

        await repository.delete_projection(projection_version)
        rebuilt = await repository.replace_projection(snapshot, batch_size=200)
        assert rebuilt.statistics == first.statistics
    finally:
        if projection_version and not preexisting_same_version:
            await repository.delete_projection(projection_version)
        if previous_info.current and previous_info.current.projection_version != projection_version:
            await repository.activate_projection(previous_info.current.projection_version)
        if snapshot is not None:
            snapshot_path = snapshot.store.path
            snapshot.close()
            snapshot_path.unlink(missing_ok=True)
        await mysql.close()


async def _select_test_eids(mysql: CompanyDueDiligenceMySQL) -> tuple[list[str], str]:
    """动态选择共同稳定股东企业和含投资人 JSON 的企业。"""
    async with mysql.connection() as connection:
        shared = await connection.execute(
            text(
                """
                SELECT stock_id
                FROM t_partners_alls
                WHERE stock_id IS NOT NULL AND stock_id <> ''
                GROUP BY stock_id
                HAVING COUNT(DISTINCT eid) >= 2
                ORDER BY stock_id
                LIMIT 1
                """
            )
        )
        stock_id = shared.scalar_one()
        companies = await connection.execute(
            text(
                """
                SELECT DISTINCT eid
                FROM t_partners_alls
                WHERE stock_id = :stock_id
                ORDER BY eid
                LIMIT 2
                """
            ),
            {"stock_id": stock_id},
        )
        shared_eids = [row[0] for row in companies]
        financing = await connection.execute(
            text(
                """
                SELECT eid
                FROM t_company_finance
                WHERE JSON_VALID(investors_json)
                  AND JSON_LENGTH(investors_json) > 0
                ORDER BY eid, id
                LIMIT 1
                """
            )
        )
        return shared_eids, financing.scalar_one()
