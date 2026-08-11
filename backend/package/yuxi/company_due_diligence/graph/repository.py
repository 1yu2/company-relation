"""企业尽调 Neo4j 投影写入、统计和受控路径 Repository。"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from yuxi.company_due_diligence.graph.schema import (
    GraphPathQuery,
    GraphProjectionInfo,
    GraphNodeType,
    GraphRelationType,
    ProjectionManifest,
    ProjectionSnapshot,
    ProjectionStatistics,
)
from yuxi.storage.neo4j import get_shared_neo4j_connection

_NODE_LABEL = "CompanyDueDiligenceNode"
_PROJECTION_LABEL = "CompanyDueDiligenceProjection"


class GraphUnavailableError(RuntimeError):
    """表示企业尽调 Neo4j 当前不可用。"""


class CompanyDueDiligenceGraphRepository:
    """管理企业尽调独立版本投影，不接触旧 CompanyRelation 数据。"""

    def __init__(self, connection=None):
        self._connection = connection

    @property
    def driver(self):
        """按需获取共享 Neo4j Driver，并统一不可用错误。"""
        try:
            connection = self._connection or get_shared_neo4j_connection()
        except Exception as exc:
            raise GraphUnavailableError("Neo4j 当前不可用") from exc
        if not connection.driver:
            raise GraphUnavailableError("Neo4j 当前不可用")
        return connection.driver

    async def replace_projection(self, snapshot: ProjectionSnapshot, batch_size: int = 1000) -> ProjectionManifest:
        """幂等导入指定 checksum 版本并在校验成功后切换当前版本。"""
        return await asyncio.to_thread(self._replace_projection_sync, snapshot, batch_size)

    def _replace_projection_sync(self, snapshot: ProjectionSnapshot, batch_size: int) -> ProjectionManifest:
        """在同步 Neo4j Driver 上执行版本导入和失败清理。"""
        manifest = snapshot.manifest
        self._ensure_schema_sync()
        existing = self._get_manifest_sync(manifest.projection_version)
        if existing and existing.checksum == manifest.checksum and existing.statistics == manifest.statistics:
            self._activate_sync(manifest.projection_version)
            return existing

        self._delete_projection_data_sync(manifest.projection_version)
        self._write_projection_meta_sync(manifest, status="importing", current=False)
        try:
            self._write_nodes_sync(snapshot, batch_size)
            self._write_edges_sync(snapshot, batch_size)
            actual_statistics = self._statistics_sync(manifest.projection_version)
            if not _projection_statistics_match(manifest.statistics, actual_statistics):
                raise RuntimeError(
                    "Neo4j 投影统计与快照不一致: "
                    f"expected={manifest.statistics.model_dump()}, actual={actual_statistics.model_dump()}"
                )
            self._write_projection_meta_sync(manifest, status="validated", current=False)
            self._activate_sync(manifest.projection_version)
            return manifest
        except Exception:
            self._delete_projection_data_sync(manifest.projection_version)
            self._write_projection_meta_sync(manifest, status="failed", current=False)
            raise

    async def get_info(self) -> GraphProjectionInfo:
        """返回当前成功投影信息，Neo4j 故障时显式失败。"""
        return await asyncio.to_thread(self._get_info_sync)

    def _get_info_sync(self) -> GraphProjectionInfo:
        """读取当前版本元数据。"""
        with self.driver.session() as session:
            record = session.run(
                f"""
                MATCH (projection:{_PROJECTION_LABEL} {{current: true, status: 'success'}})
                RETURN projection
                ORDER BY projection.created_at DESC
                LIMIT 1
                """
            ).single()
        if record is None:
            return GraphProjectionInfo(available=True)
        return GraphProjectionInfo(available=True, current=_manifest_from_properties(dict(record["projection"])))

    async def delete_projection(self, projection_version: str) -> None:
        """删除指定派生投影，不访问或修改 MySQL。"""
        await asyncio.to_thread(self._delete_projection_sync, projection_version)

    async def activate_projection(self, projection_version: str) -> None:
        """重新激活一个已成功版本，用于显式回滚或测试环境恢复。"""
        await asyncio.to_thread(self._activate_existing_sync, projection_version)

    def _delete_projection_sync(self, projection_version: str) -> None:
        """删除版本节点、关系和元数据。"""
        self._delete_projection_data_sync(projection_version)
        with self.driver.session() as session:
            session.run(
                f"MATCH (projection:{_PROJECTION_LABEL} {{projection_version: $projection_version}}) DELETE projection",
                projection_version=projection_version,
            ).consume()

    def _activate_existing_sync(self, projection_version: str) -> None:
        """确认版本成功后切换 current 标记。"""
        manifest = self._get_manifest_sync(projection_version)
        if manifest is None:
            raise ValueError(f"投影版本不存在或未成功: {projection_version}")
        self._activate_sync(projection_version)

    async def query_paths(self, query: GraphPathQuery) -> tuple[str, list[Any]]:
        """执行最多三跳的关系类型白名单路径查询。"""
        return await asyncio.to_thread(self._query_paths_sync, query)

    def _query_paths_sync(self, query: GraphPathQuery) -> tuple[str, list[Any]]:
        """构造仅含枚举关系类型和固定跳数的 Cypher。"""
        info = self._get_info_sync()
        if info.current is None:
            raise GraphUnavailableError("Neo4j 当前没有已激活的企业尽调投影")
        version = info.current.projection_version
        relationship_types = "|".join(sorted({relation_type.value for relation_type in query.relation_types}))
        confidence_condition = "true" if query.include_clues else "relation.confidence = 'high'"
        target_condition = (
            "end:Company AND end.node_id = $target_eid" if query.target_eid else "end.node_id <> start.node_id"
        )
        cypher = f"""
            MATCH (start:{_NODE_LABEL}:Company {{projection_version: $projection_version, node_id: $start_eid}})
            MATCH path=(start)-[:{relationship_types}*1..{query.max_hops}]-(end:{_NODE_LABEL})
            WHERE {target_condition}
              AND all(node IN nodes(path) WHERE node.projection_version = $projection_version)
              AND all(
                  relation IN relationships(path)
                  WHERE relation.projection_version = $projection_version
                    AND ({confidence_condition})
                    AND size(coalesce(relation.source_ids, [])) > 0
              )
            RETURN path
            ORDER BY length(path) ASC
            LIMIT $max_paths
        """
        with self.driver.session() as session:
            records = session.run(
                cypher,
                projection_version=version,
                start_eid=query.start_eid,
                target_eid=query.target_eid,
                max_paths=query.max_paths,
            )
            return version, [record["path"] for record in records]

    def _ensure_schema_sync(self) -> None:
        """创建企业尽调专用节点约束和查询索引。"""
        statements = (
            f"""
            CREATE CONSTRAINT cdd_projection_version IF NOT EXISTS
            FOR (projection:{_PROJECTION_LABEL}) REQUIRE projection.projection_version IS UNIQUE
            """,
            f"""
            CREATE CONSTRAINT cdd_node_version_key IF NOT EXISTS
            FOR (node:{_NODE_LABEL}) REQUIRE (node.projection_version, node.node_id) IS UNIQUE
            """,
            f"""
            CREATE INDEX cdd_node_type_lookup IF NOT EXISTS
            FOR (node:{_NODE_LABEL}) ON (node.projection_version, node.node_type)
            """,
        )
        with self.driver.session() as session:
            for statement in statements:
                session.run(statement).consume()

    def _write_nodes_sync(self, snapshot: ProjectionSnapshot, batch_size: int) -> None:
        """按固定节点标签分组批量 MERGE 节点。"""
        version = snapshot.manifest.projection_version
        with self.driver.session() as session:
            for batch in snapshot.store.iter_nodes(batch_size):
                rows_by_type: dict[str, list[dict]] = defaultdict(list)
                for row in batch:
                    rows_by_type[row["node_type"]].append(row)
                for node_type, rows in rows_by_type.items():
                    if node_type not in {item.value for item in GraphNodeType}:
                        raise ValueError(f"不支持的图节点类型: {node_type}")
                    session.run(
                        f"""
                        UNWIND $rows AS row
                        MERGE (node:{_NODE_LABEL}:{node_type} {{
                            projection_version: $projection_version,
                            node_id: row.node_id
                        }})
                        SET node.node_type = row.node_type,
                            node.name = row.name,
                            node.source_ids = row.source_ids,
                            node += row.properties
                        """,
                        projection_version=version,
                        rows=rows,
                    ).consume()

    def _write_edges_sync(self, snapshot: ProjectionSnapshot, batch_size: int) -> None:
        """按固定关系类型分组批量 MERGE 带来源关系。"""
        version = snapshot.manifest.projection_version
        with self.driver.session() as session:
            for batch in snapshot.store.iter_edges(batch_size):
                rows_by_type: dict[str, list[dict]] = defaultdict(list)
                for row in batch:
                    rows_by_type[row["edge_type"]].append(row)
                for edge_type, rows in rows_by_type.items():
                    if edge_type not in {item.value for item in GraphRelationType}:
                        raise ValueError(f"不支持的图关系类型: {edge_type}")
                    session.run(
                        f"""
                        UNWIND $rows AS row
                        MATCH (source:{_NODE_LABEL} {{
                            projection_version: $projection_version,
                            node_id: row.source_node_id
                        }})
                        MATCH (target:{_NODE_LABEL} {{
                            projection_version: $projection_version,
                            node_id: row.target_node_id
                        }})
                        MERGE (source)-[relation:{edge_type} {{
                            projection_version: $projection_version,
                            edge_id: row.edge_id
                        }}]->(target)
                        SET relation.confidence = row.confidence,
                            relation.source_ids = row.source_ids,
                            relation += row.properties
                        """,
                        projection_version=version,
                        rows=rows,
                    ).consume()

    def _statistics_sync(self, projection_version: str) -> ProjectionStatistics:
        """从 Neo4j 重算节点、关系、孤儿、重复和置信度分布。"""
        with self.driver.session() as session:
            counts = session.run(
                f"""
                MATCH (node:{_NODE_LABEL} {{projection_version: $projection_version}})
                WITH count(node) AS node_count,
                     count(DISTINCT node.node_id) AS distinct_node_count,
                     sum(CASE WHEN NOT (node)--() THEN 1 ELSE 0 END) AS orphan_count
                OPTIONAL MATCH ()-[relation]->()
                WHERE relation.projection_version = $projection_version
                RETURN node_count,
                       distinct_node_count,
                       orphan_count,
                       count(relation) AS edge_count,
                       count(DISTINCT relation.edge_id) AS distinct_edge_count
                """,
                projection_version=projection_version,
            ).single()
            node_types = session.run(
                f"""
                MATCH (node:{_NODE_LABEL} {{projection_version: $projection_version}})
                RETURN node.node_type AS key, count(*) AS count
                ORDER BY key
                """,
                projection_version=projection_version,
            )
            edge_types = session.run(
                """
                MATCH ()-[relation]->()
                WHERE relation.projection_version = $projection_version
                RETURN type(relation) AS key, count(*) AS count
                ORDER BY key
                """,
                projection_version=projection_version,
            )
            confidences = session.run(
                """
                MATCH ()-[relation]->()
                WHERE relation.projection_version = $projection_version
                RETURN relation.confidence AS key, count(*) AS count
                ORDER BY key
                """,
                projection_version=projection_version,
            )
            return ProjectionStatistics(
                node_count=counts["node_count"],
                edge_count=counts["edge_count"],
                orphan_count=counts["orphan_count"],
                duplicate_node_count=counts["node_count"] - counts["distinct_node_count"],
                duplicate_edge_count=counts["edge_count"] - counts["distinct_edge_count"],
                node_type_counts={record["key"]: record["count"] for record in node_types},
                edge_type_counts={record["key"]: record["count"] for record in edge_types},
                confidence_distribution={record["key"]: record["count"] for record in confidences},
            )

    def _get_manifest_sync(self, projection_version: str) -> ProjectionManifest | None:
        """按版本读取已记录投影清单。"""
        with self.driver.session() as session:
            record = session.run(
                f"""
                MATCH (projection:{_PROJECTION_LABEL} {{projection_version: $projection_version}})
                RETURN projection
                """,
                projection_version=projection_version,
            ).single()
        if record is None or record["projection"].get("status") != "success":
            return None
        return _manifest_from_properties(dict(record["projection"]))

    def _write_projection_meta_sync(
        self,
        manifest: ProjectionManifest,
        *,
        status: str,
        current: bool,
    ) -> None:
        """保存可公开读取的版本、checksum 和统计元数据。"""
        statistics = manifest.statistics
        with self.driver.session() as session:
            session.run(
                f"""
                MERGE (projection:{_PROJECTION_LABEL} {{projection_version: $projection_version}})
                SET projection.checksum = $checksum,
                    projection.created_at = $created_at,
                    projection.scope_eids = $scope_eids,
                    projection.status = $status,
                    projection.current = $current,
                    projection.node_count = $node_count,
                    projection.edge_count = $edge_count,
                    projection.orphan_count = $orphan_count,
                    projection.duplicate_node_count = $duplicate_node_count,
                    projection.duplicate_edge_count = $duplicate_edge_count,
                    projection.node_type_counts_json = $node_type_counts_json,
                    projection.edge_type_counts_json = $edge_type_counts_json,
                    projection.confidence_distribution_json = $confidence_distribution_json
                """,
                projection_version=manifest.projection_version,
                checksum=manifest.checksum,
                created_at=manifest.created_at.isoformat(),
                scope_eids=manifest.scope_eids or [],
                status=status,
                current=current,
                node_count=statistics.node_count,
                edge_count=statistics.edge_count,
                orphan_count=statistics.orphan_count,
                duplicate_node_count=statistics.duplicate_node_count,
                duplicate_edge_count=statistics.duplicate_edge_count,
                node_type_counts_json=json.dumps(statistics.node_type_counts, sort_keys=True),
                edge_type_counts_json=json.dumps(statistics.edge_type_counts, sort_keys=True),
                confidence_distribution_json=json.dumps(statistics.confidence_distribution, sort_keys=True),
            ).consume()

    def _activate_sync(self, projection_version: str) -> None:
        """在单个事务中取消旧 current 并激活已校验版本。"""
        with self.driver.session() as session:
            session.run(
                f"""
                MATCH (target:{_PROJECTION_LABEL} {{projection_version: $projection_version}})
                OPTIONAL MATCH (other:{_PROJECTION_LABEL})
                WHERE other.projection_version <> target.projection_version
                SET other.current = false
                SET target.current = true,
                    target.status = 'success',
                    target.activated_at = $activated_at
                """,
                projection_version=projection_version,
                activated_at=datetime.now(UTC).isoformat(),
            ).consume()

    def _delete_projection_data_sync(self, projection_version: str) -> None:
        """删除指定版本的派生节点和关系，保留其他版本。"""
        with self.driver.session() as session:
            session.run(
                f"""
                MATCH (node:{_NODE_LABEL} {{projection_version: $projection_version}})
                DETACH DELETE node
                """,
                projection_version=projection_version,
            ).consume()


def _manifest_from_properties(properties: dict[str, Any]) -> ProjectionManifest:
    """把 Neo4j 元数据节点还原为公开投影清单。"""
    return ProjectionManifest(
        projection_version=properties["projection_version"],
        checksum=properties["checksum"],
        created_at=datetime.fromisoformat(properties["created_at"]),
        scope_eids=properties.get("scope_eids") or None,
        statistics=ProjectionStatistics(
            node_count=properties["node_count"],
            edge_count=properties["edge_count"],
            orphan_count=properties["orphan_count"],
            duplicate_node_count=properties["duplicate_node_count"],
            duplicate_edge_count=properties["duplicate_edge_count"],
            node_type_counts=json.loads(properties["node_type_counts_json"]),
            edge_type_counts=json.loads(properties["edge_type_counts_json"]),
            confidence_distribution=json.loads(properties["confidence_distribution_json"]),
        ),
    )


def _projection_statistics_match(expected: ProjectionStatistics, actual: ProjectionStatistics) -> bool:
    """比较去重后图结构统计，源重复尝试只保留在快照报告中。"""
    return (
        expected.node_count == actual.node_count
        and expected.edge_count == actual.edge_count
        and expected.orphan_count == actual.orphan_count
        and actual.duplicate_node_count == 0
        and actual.duplicate_edge_count == 0
        and expected.node_type_counts == actual.node_type_counts
        and expected.edge_type_counts == actual.edge_type_counts
        and expected.confidence_distribution == actual.confidence_distribution
    )
