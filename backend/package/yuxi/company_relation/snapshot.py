from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import networkx as nx


ALLOWED_NODE_TYPES = {"Company", "Person", "Announcement", "Industry"}
ALLOWED_EDGE_TYPES = {"HOLDS", "EXECUTIVE_OF", "BELONGS_TO", "RELEASED", "RELATED_TO"}
ATTRIBUTE_LABELS = {
    "industry": "所属行业",
    "legal_representative": "法定代表人",
    "listing_date": "上市日期",
    "registered_address": "注册地址",
}


@dataclass(frozen=True)
class SourceRecord:
    """描述一条可追溯的公开数据来源。"""

    source_id: str
    provider: str
    title: str
    url: str
    interface: str
    data_date: str | None
    collected_at: str


@dataclass(frozen=True)
class GraphNode:
    """表示企业关系快照中的标准化节点。"""

    node_id: str
    node_type: str
    name: str
    properties: dict[str, Any]
    source_ids: tuple[str, ...]


@dataclass(frozen=True)
class GraphEdge:
    """表示企业关系快照中的标准化关系。"""

    edge_id: str
    edge_type: str
    source_node_id: str
    target_node_id: str
    properties: dict[str, Any]
    source_ids: tuple[str, ...]


@dataclass(frozen=True)
class SnapshotValidationResult:
    """汇总图谱快照的结构和来源校验结果。"""

    valid: bool
    node_count: int
    edge_count: int
    duplicate_node_ids: tuple[str, ...]
    duplicate_edge_ids: tuple[str, ...]
    orphan_edge_ids: tuple[str, ...]
    invalid_node_ids: tuple[str, ...]
    invalid_edge_ids: tuple[str, ...]
    missing_source_references: tuple[str, ...]
    source_coverage: float

    def to_dict(self) -> dict[str, Any]:
        """转换为可序列化的校验结果。"""
        return {
            "valid": self.valid,
            "node_count": self.node_count,
            "edge_count": self.edge_count,
            "duplicate_node_ids": list(self.duplicate_node_ids),
            "duplicate_edge_ids": list(self.duplicate_edge_ids),
            "orphan_edge_ids": list(self.orphan_edge_ids),
            "invalid_node_ids": list(self.invalid_node_ids),
            "invalid_edge_ids": list(self.invalid_edge_ids),
            "missing_source_references": list(self.missing_source_references),
            "source_coverage": self.source_coverage,
        }


class CompanyRelationSnapshot:
    """加载、校验并查询冻结的企业关系图谱快照。"""

    def __init__(
        self,
        *,
        dataset_id: str,
        name: str,
        as_of_date: str,
        collected_at: str,
        sources: list[SourceRecord],
        nodes: list[GraphNode],
        edges: list[GraphEdge],
        raw_payload: dict[str, Any],
    ):
        self.dataset_id = dataset_id
        self.name = name
        self.as_of_date = as_of_date
        self.collected_at = collected_at
        self.sources = sources
        self.nodes = nodes
        self.edges = edges
        self.raw_payload = raw_payload
        self.nodes_by_id = {node.node_id: node for node in nodes}
        self.sources_by_id = {source.source_id: source for source in sources}
        self.evidence_documents = self._build_evidence_documents()
        self.evidence_by_id = {item["evidence_id"]: item for item in self.evidence_documents}

    @classmethod
    def load(cls, path: str | Path) -> CompanyRelationSnapshot:
        """从 JSON 文件加载标准化企业关系快照。"""
        snapshot_path = Path(path)
        payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
        return cls.from_dict(payload)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> CompanyRelationSnapshot:
        """从字典构建企业关系快照。"""
        sources = [
            SourceRecord(
                source_id=item["source_id"],
                provider=item["provider"],
                title=item["title"],
                url=item.get("url", ""),
                interface=item.get("interface", ""),
                data_date=item.get("data_date"),
                collected_at=item["collected_at"],
            )
            for item in payload.get("sources", [])
        ]
        nodes = [
            GraphNode(
                node_id=item["id"],
                node_type=item["type"],
                name=item["name"],
                properties=item.get("properties") or {},
                source_ids=tuple(item.get("source_ids") or ()),
            )
            for item in payload.get("nodes", [])
        ]
        edges = [
            GraphEdge(
                edge_id=item["id"],
                edge_type=item["type"],
                source_node_id=item["source"],
                target_node_id=item["target"],
                properties=item.get("properties") or {},
                source_ids=tuple(item.get("source_ids") or ()),
            )
            for item in payload.get("edges", [])
        ]
        return cls(
            dataset_id=payload["dataset_id"],
            name=payload["name"],
            as_of_date=payload["as_of_date"],
            collected_at=payload["collected_at"],
            sources=sources,
            nodes=nodes,
            edges=edges,
            raw_payload=payload,
        )

    def checksum(self) -> str:
        """计算快照规范化 JSON 的 SHA-256。"""
        content = json.dumps(self.raw_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def validate(self) -> SnapshotValidationResult:
        """校验 Schema、孤儿边、重复 ID 和来源覆盖。"""
        node_ids = [node.node_id for node in self.nodes]
        edge_ids = [edge.edge_id for edge in self.edges]
        duplicate_node_ids = _find_duplicates(node_ids)
        duplicate_edge_ids = _find_duplicates(edge_ids)
        known_node_ids = set(node_ids)
        known_source_ids = {source.source_id for source in self.sources}

        invalid_node_ids = sorted(node.node_id for node in self.nodes if node.node_type not in ALLOWED_NODE_TYPES)
        invalid_edge_ids = sorted(edge.edge_id for edge in self.edges if edge.edge_type not in ALLOWED_EDGE_TYPES)
        orphan_edge_ids = sorted(
            edge.edge_id
            for edge in self.edges
            if edge.source_node_id not in known_node_ids or edge.target_node_id not in known_node_ids
        )

        missing_source_references = []
        sourced_records = 0
        records = [*self.nodes, *self.edges]
        for record in records:
            source_ids = record.source_ids
            if source_ids:
                sourced_records += 1
            for source_id in source_ids:
                if source_id not in known_source_ids:
                    record_id = record.node_id if isinstance(record, GraphNode) else record.edge_id
                    missing_source_references.append(f"{record_id}:{source_id}")

        source_coverage = sourced_records / len(records) if records else 1.0
        valid = not any(
            (
                duplicate_node_ids,
                duplicate_edge_ids,
                invalid_node_ids,
                invalid_edge_ids,
                orphan_edge_ids,
                missing_source_references,
            )
        ) and source_coverage == 1.0
        return SnapshotValidationResult(
            valid=valid,
            node_count=len(self.nodes),
            edge_count=len(self.edges),
            duplicate_node_ids=tuple(duplicate_node_ids),
            duplicate_edge_ids=tuple(duplicate_edge_ids),
            orphan_edge_ids=tuple(orphan_edge_ids),
            invalid_node_ids=tuple(invalid_node_ids),
            invalid_edge_ids=tuple(invalid_edge_ids),
            missing_source_references=tuple(sorted(missing_source_references)),
            source_coverage=round(source_coverage, 6),
        )

    def build_graph(self, *, include_derived_relations: bool = True) -> nx.MultiGraph:
        """构建用于多跳检索的无向多重图。"""
        graph = nx.MultiGraph()
        for node in self.nodes:
            graph.add_node(node.node_id, node=node)
        for edge in self.edges:
            if not include_derived_relations and edge.edge_type == "RELATED_TO":
                continue
            graph.add_edge(edge.source_node_id, edge.target_node_id, key=edge.edge_id, edge=edge)
        return graph

    def list_nodes(self, node_type: str) -> list[GraphNode]:
        """按节点类型返回稳定排序的节点列表。"""
        return sorted((node for node in self.nodes if node.node_type == node_type), key=lambda item: item.node_id)

    def get_sources(self, source_ids: list[str] | tuple[str, ...]) -> list[dict[str, Any]]:
        """返回输出证据关联的公开来源摘要。"""
        return [self.sources_by_id[source_id].__dict__ for source_id in source_ids if source_id in self.sources_by_id]

    def _build_evidence_documents(self) -> list[dict[str, Any]]:
        documents: list[dict[str, Any]] = []
        for node in self.nodes:
            if node.node_type != "Company":
                continue
            for property_name, label in ATTRIBUTE_LABELS.items():
                value = node.properties.get(property_name)
                if value in (None, ""):
                    continue
                fact_id = f"{node.node_id}#{property_name}"
                documents.append(
                    {
                        "evidence_id": fact_id,
                        "fact_ids": [fact_id],
                        "text": f"{node.name}的{label}是{value}。",
                        "source_ids": list(node.source_ids),
                        "kind": "attribute",
                        "subject_id": node.node_id,
                        "predicate": property_name,
                        "object_id": None,
                        "value": value,
                    }
                )

        for edge in self.edges:
            source = self.nodes_by_id.get(edge.source_node_id)
            target = self.nodes_by_id.get(edge.target_node_id)
            if source is None or target is None:
                continue
            documents.append(
                {
                    "evidence_id": edge.edge_id,
                    "fact_ids": [edge.edge_id],
                    "text": _edge_to_text(source, edge, target),
                    "source_ids": list(edge.source_ids),
                    "kind": "relationship",
                    "subject_id": edge.source_node_id,
                    "predicate": edge.edge_type,
                    "object_id": edge.target_node_id,
                    "value": edge.properties,
                }
            )
        return documents


def _find_duplicates(values: list[str]) -> list[str]:
    seen = set()
    duplicates = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return sorted(duplicates)


def _edge_to_text(source: GraphNode, edge: GraphEdge, target: GraphNode) -> str:
    if edge.edge_type == "HOLDS":
        ratio = edge.properties.get("share_ratio")
        ratio_text = f"，持股比例为{ratio}%" if ratio is not None else ""
        return f"{source.name}持有{target.name}股份{ratio_text}。"
    if edge.edge_type == "EXECUTIVE_OF":
        role = edge.properties.get("role") or "高管"
        return f"{source.name}在{target.name}担任{role}。"
    if edge.edge_type == "BELONGS_TO":
        return f"{source.name}属于{target.name}行业。"
    if edge.edge_type == "RELEASED":
        return f"{source.name}发布公告《{target.name}》。"
    reason = edge.properties.get("reason") or "公开关系"
    return f"{source.name}与{target.name}存在关联，原因是{reason}。"
