from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any

from yuxi.company_relation.snapshot import ALLOWED_EDGE_TYPES, ALLOWED_NODE_TYPES, CompanyRelationSnapshot
from yuxi.storage.neo4j import get_shared_neo4j_connection


class CompanyRelationNeo4jRepository:
    """负责企业关系快照导入和只读 Cypher 查询。"""

    def __init__(self, connection=None):
        self.connection = connection or get_shared_neo4j_connection()

    @property
    def driver(self):
        """返回共享 Neo4j Driver。"""
        if not self.connection.driver:
            raise RuntimeError("Neo4j 未连接")
        return self.connection.driver

    async def replace_snapshot(self, snapshot: CompanyRelationSnapshot) -> dict[str, int]:
        """按 dataset_id 原子语义替换企业关系图谱快照。"""
        validation = snapshot.validate()
        if not validation.valid:
            raise ValueError(f"企业关系快照校验失败: {validation.to_dict()}")
        return await asyncio.to_thread(self._replace_snapshot_sync, snapshot)

    def _replace_snapshot_sync(self, snapshot: CompanyRelationSnapshot) -> dict[str, int]:
        with self.driver.session() as session:
            session.run(
                "CREATE CONSTRAINT company_relation_node IF NOT EXISTS "
                "FOR (node:CompanyRelation) REQUIRE (node.dataset_id, node.node_id) IS UNIQUE"
            ).consume()
            session.run(
                "MATCH (node:CompanyRelation {dataset_id: $dataset_id}) DETACH DELETE node",
                dataset_id=snapshot.dataset_id,
            ).consume()

            nodes_by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for node in snapshot.nodes:
                if node.node_type not in ALLOWED_NODE_TYPES:
                    raise ValueError(f"不支持的节点类型: {node.node_type}")
                nodes_by_type[node.node_type].append(
                    {
                        "node_id": node.node_id,
                        "name": node.name,
                        "properties": node.properties,
                        "source_ids": list(node.source_ids),
                    }
                )

            for node_type, rows in nodes_by_type.items():
                session.run(
                    f"""
                    UNWIND $rows AS row
                    MERGE (node:CompanyRelation:{node_type} {{dataset_id: $dataset_id, node_id: row.node_id}})
                    SET node.name = row.name,
                        node.source_ids = row.source_ids,
                        node += row.properties
                    """,
                    dataset_id=snapshot.dataset_id,
                    rows=rows,
                ).consume()

            edges_by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for edge in snapshot.edges:
                if edge.edge_type not in ALLOWED_EDGE_TYPES:
                    raise ValueError(f"不支持的关系类型: {edge.edge_type}")
                edges_by_type[edge.edge_type].append(
                    {
                        "edge_id": edge.edge_id,
                        "source": edge.source_node_id,
                        "target": edge.target_node_id,
                        "properties": edge.properties,
                        "source_ids": list(edge.source_ids),
                    }
                )

            for edge_type, rows in edges_by_type.items():
                session.run(
                    f"""
                    UNWIND $rows AS row
                    MATCH (source:CompanyRelation {{dataset_id: $dataset_id, node_id: row.source}})
                    MATCH (target:CompanyRelation {{dataset_id: $dataset_id, node_id: row.target}})
                    MERGE (source)-[relation:{edge_type} {{dataset_id: $dataset_id, edge_id: row.edge_id}}]->(target)
                    SET relation.source_ids = row.source_ids,
                        relation += row.properties
                    """,
                    dataset_id=snapshot.dataset_id,
                    rows=rows,
                ).consume()

        return {"node_count": len(snapshot.nodes), "edge_count": len(snapshot.edges)}

    async def get_company_attribute(self, dataset_id: str, company_id: str, field: str) -> dict[str, Any] | None:
        """查询公司单个标准属性。"""
        return await asyncio.to_thread(self._get_company_attribute_sync, dataset_id, company_id, field)

    def _get_company_attribute_sync(self, dataset_id: str, company_id: str, field: str) -> dict[str, Any] | None:
        with self.driver.session() as session:
            record = session.run(
                """
                MATCH (company:CompanyRelation:Company {dataset_id: $dataset_id, node_id: $company_id})
                RETURN company.node_id AS company_id,
                       company.name AS company_name,
                       company[$field] AS value,
                       company.source_ids AS source_ids
                """,
                dataset_id=dataset_id,
                company_id=company_id,
                field=field,
            ).single()
            return record.data() if record and record["value"] not in (None, "") else None

    async def list_shareholders(self, dataset_id: str, company_id: str) -> list[dict[str, Any]]:
        """查询公司的直接股东及持股比例。"""
        return await asyncio.to_thread(self._list_shareholders_sync, dataset_id, company_id)

    def _list_shareholders_sync(self, dataset_id: str, company_id: str) -> list[dict[str, Any]]:
        with self.driver.session() as session:
            result = session.run(
                """
                MATCH (holder:CompanyRelation)-[relation:HOLDS]->
                      (company:CompanyRelation:Company {dataset_id: $dataset_id, node_id: $company_id})
                WHERE holder.dataset_id = $dataset_id AND relation.dataset_id = $dataset_id
                RETURN holder.node_id AS entity_id,
                       holder.name AS entity_name,
                       relation.edge_id AS edge_id,
                       relation.share_ratio AS share_ratio,
                       relation.source_ids AS source_ids
                ORDER BY coalesce(relation.share_ratio, 0) DESC, holder.node_id
                """,
                dataset_id=dataset_id,
                company_id=company_id,
            )
            return [record.data() for record in result]

    async def list_executive_companies(self, dataset_id: str, person_id: str) -> list[dict[str, Any]]:
        """查询高管任职公司列表。"""
        return await asyncio.to_thread(self._list_executive_companies_sync, dataset_id, person_id)

    def _list_executive_companies_sync(self, dataset_id: str, person_id: str) -> list[dict[str, Any]]:
        with self.driver.session() as session:
            result = session.run(
                """
                MATCH (person:CompanyRelation:Person {dataset_id: $dataset_id, node_id: $person_id})-
                      [relation:EXECUTIVE_OF]->(company:CompanyRelation:Company)
                WHERE company.dataset_id = $dataset_id AND relation.dataset_id = $dataset_id
                RETURN company.node_id AS entity_id,
                       company.name AS entity_name,
                       relation.edge_id AS edge_id,
                       relation.role AS role,
                       relation.source_ids AS source_ids
                ORDER BY company.node_id
                """,
                dataset_id=dataset_id,
                person_id=person_id,
            )
            return [record.data() for record in result]

    async def list_announcements(self, dataset_id: str, company_id: str) -> list[dict[str, Any]]:
        """查询公司关联的公告列表。"""
        return await asyncio.to_thread(self._list_announcements_sync, dataset_id, company_id)

    def _list_announcements_sync(self, dataset_id: str, company_id: str) -> list[dict[str, Any]]:
        with self.driver.session() as session:
            result = session.run(
                """
                MATCH (company:CompanyRelation:Company {dataset_id: $dataset_id, node_id: $company_id})-
                      [relation:RELEASED]->(announcement:CompanyRelation:Announcement)
                WHERE announcement.dataset_id = $dataset_id AND relation.dataset_id = $dataset_id
                RETURN announcement.node_id AS entity_id,
                       announcement.name AS entity_name,
                       announcement.published_at AS published_at,
                       relation.edge_id AS edge_id,
                       relation.source_ids AS source_ids
                ORDER BY announcement.published_at DESC, announcement.node_id
                """,
                dataset_id=dataset_id,
                company_id=company_id,
            )
            return [record.data() for record in result]

    async def find_relation_path(
        self, dataset_id: str, source_company_id: str, target_company_id: str, max_depth: int = 3
    ) -> dict[str, Any] | None:
        """查询两家公司之间稳定排序的最短原始关系路径。"""
        depth = min(max(max_depth, 1), 3)
        return await asyncio.to_thread(
            self._find_relation_path_sync,
            dataset_id,
            source_company_id,
            target_company_id,
            depth,
        )

    def _find_relation_path_sync(
        self, dataset_id: str, source_company_id: str, target_company_id: str, max_depth: int
    ) -> dict[str, Any] | None:
        with self.driver.session() as session:
            record = session.run(
                f"""
                MATCH path=(source:CompanyRelation:Company {{dataset_id: $dataset_id,
                                                            node_id: $source_company_id}})-
                           [*1..{max_depth}]-(target:CompanyRelation:Company {{dataset_id: $dataset_id,
                                                                             node_id: $target_company_id}})
                WHERE all(relation IN relationships(path)
                          WHERE relation.dataset_id = $dataset_id
                            AND type(relation) IN ['HOLDS', 'EXECUTIVE_OF'])
                WITH path,
                     [node IN nodes(path) | node.node_id] AS node_ids,
                     [node IN nodes(path) | node.name] AS node_names,
                     [relation IN relationships(path) | relation.edge_id] AS edge_ids,
                     [relation IN relationships(path) | relation.source_ids] AS source_id_groups
                ORDER BY length(path), edge_ids
                LIMIT 1
                RETURN node_ids, node_names, edge_ids, source_id_groups
                """,
                dataset_id=dataset_id,
                source_company_id=source_company_id,
                target_company_id=target_company_id,
            ).single()
            return record.data() if record else None

    async def get_stats(self, dataset_id: str) -> dict[str, int]:
        """返回指定企业关系数据集在 Neo4j 中的节点和边数量。"""
        return await asyncio.to_thread(self._get_stats_sync, dataset_id)

    def _get_stats_sync(self, dataset_id: str) -> dict[str, int]:
        with self.driver.session() as session:
            record = session.run(
                """
                MATCH (node:CompanyRelation {dataset_id: $dataset_id})
                OPTIONAL MATCH (node)-[relation {dataset_id: $dataset_id}]->()
                RETURN count(DISTINCT node) AS node_count, count(DISTINCT relation) AS edge_count
                """,
                dataset_id=dataset_id,
            ).single()
            return {"node_count": record["node_count"], "edge_count": record["edge_count"]}
