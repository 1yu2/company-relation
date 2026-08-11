"""企业尽调图谱节点、关系和投影清单契约。"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field, JsonValue, field_validator

from yuxi.company_due_diligence.models import ConfidenceLevel, DataQuality, LatencyBreakdown, Path as GraphResultPath


class GraphNodeType(StrEnum):
    """描述企业尽调投影允许的节点标签。"""

    COMPANY = "Company"
    SHAREHOLDER = "Shareholder"
    FINANCING_EVENT = "FinancingEvent"
    INVESTOR = "Investor"
    PATENT = "Patent"
    TRADEMARK = "Trademark"
    COPYRIGHT = "Copyright"
    PUNISHMENT = "Punishment"
    LICENSE = "License"
    REGULATOR = "Regulator"


class GraphRelationType(StrEnum):
    """描述企业尽调投影允许的关系类型。"""

    HOLDS = "HOLDS"
    RECEIVED_FINANCING = "RECEIVED_FINANCING"
    INVESTED_IN = "INVESTED_IN"
    OWNS_PATENT = "OWNS_PATENT"
    OWNS_TRADEMARK = "OWNS_TRADEMARK"
    OWNS_COPYRIGHT = "OWNS_COPYRIGHT"
    PUNISHED_BY = "PUNISHED_BY"
    LICENSED_BY = "LICENSED_BY"


class GraphNode(BaseModel):
    """描述带稳定键和来源的投影节点。"""

    node_id: str
    node_type: GraphNodeType
    name: str
    properties: dict[str, JsonValue] = Field(default_factory=dict)
    source_ids: list[str] = Field(default_factory=list)


class GraphEdge(BaseModel):
    """描述带方向、来源和置信度的投影关系。"""

    edge_id: str
    edge_type: GraphRelationType
    source_node_id: str
    target_node_id: str
    confidence: ConfidenceLevel
    properties: dict[str, JsonValue] = Field(default_factory=dict)
    source_ids: list[str]


class ProjectionStatistics(BaseModel):
    """描述投影快照和 Neo4j 校验统计。"""

    node_count: int = Field(ge=0)
    edge_count: int = Field(ge=0)
    orphan_count: int = Field(ge=0)
    duplicate_node_count: int = Field(ge=0)
    duplicate_edge_count: int = Field(ge=0)
    node_type_counts: dict[str, int] = Field(default_factory=dict)
    edge_type_counts: dict[str, int] = Field(default_factory=dict)
    confidence_distribution: dict[str, int] = Field(default_factory=dict)


class ProjectionManifest(BaseModel):
    """描述可复现投影版本、校验和、范围和统计。"""

    projection_version: str
    checksum: str
    created_at: datetime
    scope_eids: list[str] | None = None
    statistics: ProjectionStatistics


class GraphProjectionRequest(BaseModel):
    """描述管理员提交的全量或受限投影任务。"""

    eids: list[str] | None = Field(default=None, max_length=1000)
    batch_size: int = Field(default=1000, ge=100, le=5000)

    @field_validator("eids")
    @classmethod
    def normalize_eids(cls, value: list[str] | None) -> list[str] | None:
        """去重并拒绝空企业标识。"""
        if value is None:
            return None
        normalized = list(dict.fromkeys(item.strip() for item in value if item.strip()))
        if not normalized:
            raise ValueError("eids 不能为空列表")
        return normalized


class GraphProjectionInfo(BaseModel):
    """描述当前可查询投影和 Neo4j 可用状态。"""

    available: bool
    current: ProjectionManifest | None = None


class GraphPathQuery(BaseModel):
    """描述最多三跳的受控图路径查询。"""

    start_eid: str = Field(min_length=1, max_length=64)
    target_eid: str | None = Field(default=None, min_length=1, max_length=64)
    relation_types: list[GraphRelationType] = Field(
        default_factory=lambda: list(GraphRelationType),
        min_length=1,
        max_length=len(GraphRelationType),
    )
    max_hops: int = Field(default=3, ge=1, le=3)
    max_paths: int = Field(default=20, ge=1, le=50)
    include_clues: bool = False


class GraphPathResult(BaseModel):
    """描述当前投影版本上的受控路径查询结果。"""

    projection_version: str
    paths: list[GraphResultPath]
    data_quality: DataQuality
    latency: LatencyBreakdown


class ProjectionSnapshotStore:
    """使用 SQLite 保存大规模去重快照，避免全量图对象驻留内存。"""

    def __init__(self, path: Path):
        self.path = path
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._duplicate_edges = 0
        self._create_schema()

    def _create_schema(self) -> None:
        """创建派生快照所需的节点、关系和索引。"""
        self._connection.executescript(
            """
            PRAGMA journal_mode=DELETE;
            PRAGMA synchronous=NORMAL;
            CREATE TABLE IF NOT EXISTS nodes (
                node_id TEXT PRIMARY KEY,
                node_type TEXT NOT NULL,
                name TEXT NOT NULL,
                properties_json TEXT NOT NULL,
                source_ids_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS edges (
                edge_id TEXT PRIMARY KEY,
                edge_type TEXT NOT NULL,
                source_node_id TEXT NOT NULL,
                target_node_id TEXT NOT NULL,
                confidence TEXT NOT NULL,
                properties_json TEXT NOT NULL,
                source_ids_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_node_id);
            CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_node_id);
            """
        )

    def add_node(self, node: GraphNode) -> None:
        """按稳定节点键幂等写入快照。"""
        self._connection.execute(
            """
            INSERT INTO nodes(node_id, node_type, name, properties_json, source_ids_json)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(node_id) DO NOTHING
            """,
            (
                node.node_id,
                node.node_type.value,
                node.name,
                _canonical_json(node.properties),
                _canonical_json(sorted(set(node.source_ids))),
            ),
        )

    def add_edge(self, edge: GraphEdge) -> None:
        """按稳定关系键幂等写入并统计重复边。"""
        cursor = self._connection.execute(
            """
            INSERT OR IGNORE INTO edges(
                edge_id, edge_type, source_node_id, target_node_id,
                confidence, properties_json, source_ids_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                edge.edge_id,
                edge.edge_type.value,
                edge.source_node_id,
                edge.target_node_id,
                edge.confidence.value,
                _canonical_json(edge.properties),
                _canonical_json(sorted(set(edge.source_ids))),
            ),
        )
        if cursor.rowcount == 0:
            self._duplicate_edges += 1

    def commit(self) -> None:
        """提交当前批次写入。"""
        self._connection.commit()

    def finalize(self, scope_eids: list[str] | None) -> ProjectionManifest:
        """校验孤儿关系、计算确定性 checksum 并生成投影清单。"""
        self.commit()
        orphan_edges = self._connection.execute(
            """
            SELECT COUNT(*)
            FROM edges e
            LEFT JOIN nodes source ON source.node_id = e.source_node_id
            LEFT JOIN nodes target ON target.node_id = e.target_node_id
            WHERE source.node_id IS NULL OR target.node_id IS NULL
            """
        ).fetchone()[0]
        if orphan_edges:
            raise ValueError(f"投影快照存在 {orphan_edges} 条孤儿关系")

        checksum = hashlib.sha256()
        for table in ("nodes", "edges"):
            for row in self._connection.execute(f"SELECT * FROM {table} ORDER BY 1"):
                checksum.update(table.encode())
                checksum.update(_canonical_json(row).encode())
        checksum_value = checksum.hexdigest()
        statistics = ProjectionStatistics(
            node_count=self._count("nodes"),
            edge_count=self._count("edges"),
            orphan_count=self._orphan_node_count(),
            duplicate_node_count=0,
            duplicate_edge_count=self._duplicate_edges,
            node_type_counts=self._group_counts("nodes", "node_type"),
            edge_type_counts=self._group_counts("edges", "edge_type"),
            confidence_distribution=self._group_counts("edges", "confidence"),
        )
        return ProjectionManifest(
            projection_version=f"cdd-{checksum_value[:16]}",
            checksum=checksum_value,
            created_at=datetime.now(UTC),
            scope_eids=scope_eids,
            statistics=statistics,
        )

    def iter_nodes(self, batch_size: int) -> Iterator[list[dict]]:
        """按节点类型和稳定键批量读取快照。"""
        yield from self._iter_rows("nodes", "node_type, node_id", batch_size)

    def iter_edges(self, batch_size: int) -> Iterator[list[dict]]:
        """按关系类型和稳定键批量读取快照。"""
        yield from self._iter_rows("edges", "edge_type, edge_id", batch_size)

    def close(self) -> None:
        """关闭 SQLite 快照连接。"""
        self._connection.close()

    def _iter_rows(self, table: str, order_by: str, batch_size: int) -> Iterator[list[dict]]:
        """把 JSON 列还原为 Neo4j 批量参数。"""
        cursor = self._connection.execute(f"SELECT * FROM {table} ORDER BY {order_by}")
        columns = [item[0] for item in cursor.description]
        while rows := cursor.fetchmany(batch_size):
            batch = []
            for raw_row in rows:
                row = dict(zip(columns, raw_row, strict=True))
                row["properties"] = json.loads(row.pop("properties_json"))
                row["source_ids"] = json.loads(row.pop("source_ids_json"))
                batch.append(row)
            yield batch

    def _count(self, table: str) -> int:
        """返回快照表行数。"""
        return self._connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def _group_counts(self, table: str, field_name: str) -> dict[str, int]:
        """返回指定枚举字段的分布统计。"""
        return {
            key: count
            for key, count in self._connection.execute(
                f"SELECT {field_name}, COUNT(*) FROM {table} GROUP BY {field_name} ORDER BY {field_name}"
            )
        }

    def _orphan_node_count(self) -> int:
        """返回没有任何关系的投影节点数量。"""
        return self._connection.execute(
            """
            SELECT COUNT(*)
            FROM nodes n
            WHERE NOT EXISTS (
                SELECT 1 FROM edges e
                WHERE e.source_node_id = n.node_id OR e.target_node_id = n.node_id
            )
            """
        ).fetchone()[0]


class ProjectionSnapshot:
    """绑定已完成清单和可供 Neo4j 重放的 SQLite 快照。"""

    def __init__(self, manifest: ProjectionManifest, store: ProjectionSnapshotStore):
        self.manifest = manifest
        self.store = store

    def close(self) -> None:
        """关闭快照资源。"""
        self.store.close()


def create_staging_store(root: Path | None = None) -> ProjectionSnapshotStore:
    """在派生数据目录创建仅当前任务使用的 SQLite 快照。"""
    snapshot_root = root or Path(os.getenv("COMPANY_DD_GRAPH_SNAPSHOT_DIR", "saves/company_due_diligence/graph"))
    snapshot_root.mkdir(parents=True, exist_ok=True)
    path = snapshot_root / f"staging-{os.urandom(8).hex()}.sqlite"
    return ProjectionSnapshotStore(path)


def publish_snapshot(store: ProjectionSnapshotStore, manifest: ProjectionManifest) -> ProjectionSnapshot:
    """按 checksum 版本发布快照文件并重新打开只读重放连接。"""
    store.commit()
    store.close()
    destination = store.path.with_name(f"{manifest.projection_version}.sqlite")
    os.replace(store.path, destination)
    return ProjectionSnapshot(manifest, ProjectionSnapshotStore(destination))


def _canonical_json(value: object) -> str:
    """生成参与 checksum 的稳定紧凑 JSON。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
