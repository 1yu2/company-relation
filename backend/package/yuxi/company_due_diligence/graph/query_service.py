"""企业尽调最多三跳路径查询与统一契约映射。"""

from __future__ import annotations

import time
from datetime import date, datetime

from yuxi.company_due_diligence.graph.repository import CompanyDueDiligenceGraphRepository
from yuxi.company_due_diligence.graph.schema import GraphPathQuery, GraphPathResult
from yuxi.company_due_diligence.models import (
    ConfidenceLevel,
    DataQuality,
    Entity,
    EntityType,
    LatencyBreakdown,
    Path,
    Relation,
    SourceStatus,
    build_stable_id,
)

_ENTITY_TYPE_BY_NODE = {
    "Company": EntityType.COMPANY,
    "Shareholder": EntityType.SHAREHOLDER,
    "FinancingEvent": EntityType.FINANCING_EVENT,
    "Investor": EntityType.INVESTOR,
    "Patent": EntityType.INTELLECTUAL_PROPERTY_ITEM,
    "Trademark": EntityType.INTELLECTUAL_PROPERTY_ITEM,
    "Copyright": EntityType.INTELLECTUAL_PROPERTY_ITEM,
    "Punishment": EntityType.RISK_EVENT,
    "License": EntityType.LICENSE,
    "Regulator": EntityType.REGULATOR,
}


class GraphPathQueryService:
    """调用固定 Cypher 模板并校验每条边的来源和版本。"""

    def __init__(self, repository: CompanyDueDiligenceGraphRepository | None = None):
        self._repository = repository or CompanyDueDiligenceGraphRepository()

    async def query(self, query: GraphPathQuery) -> GraphPathResult:
        """查询当前版本路径，默认排除中低置信度线索。"""
        started_at = time.perf_counter()
        projection_version, raw_paths = await self._repository.query_paths(query)
        paths = [_map_path(raw_path, projection_version) for raw_path in raw_paths]
        elapsed_ms = round((time.perf_counter() - started_at) * 1000, 3)
        minimum_confidence = min(
            (path.confidence for path in paths),
            key=_confidence_rank,
            default=None,
        )
        return GraphPathResult(
            projection_version=projection_version,
            paths=paths,
            data_quality=DataQuality(
                source_status=SourceStatus.AVAILABLE,
                warnings=[] if paths else ["当前投影未找到满足约束的关系路径"],
                minimum_confidence=minimum_confidence,
            ),
            latency=LatencyBreakdown(neo4j_ms=elapsed_ms, total_ms=elapsed_ms),
        )


def _map_path(raw_path, projection_version: str) -> Path:
    """把 Neo4j Path 映射为冻结的 Entity、Relation 和 Path 契约。"""
    nodes = [_map_node(node) for node in raw_path.nodes]
    edges = [_map_relation(relation, projection_version) for relation in raw_path.relationships]
    source_ids = list(dict.fromkeys(source_id for edge in edges for source_id in edge.source_ids))
    confidence = min((edge.confidence for edge in edges), key=_confidence_rank, default=ConfidenceLevel.HIGH)
    path_id = build_stable_id(
        "path",
        projection_version,
        *(node.entity_id for node in nodes),
        *(edge.relation_id for edge in edges),
    )
    return Path(
        path_id=path_id,
        nodes=nodes,
        edges=edges,
        hop_count=len(edges),
        confidence=confidence,
        source_ids=source_ids,
    )


def _map_node(node) -> Entity:
    """把 Neo4j 节点属性转换为统一实体。"""
    properties = dict(node)
    node_type = properties.pop("node_type")
    entity_type = _ENTITY_TYPE_BY_NODE.get(node_type)
    if entity_type is None:
        raise RuntimeError(f"投影包含未知节点类型: {node_type}")
    node_id = properties.pop("node_id")
    name = properties.pop("name")
    source_ids = properties.pop("source_ids", [])
    properties.pop("projection_version", None)
    return Entity(
        entity_id=node_id,
        entity_type=entity_type,
        name=name,
        attributes={key: _json_scalar(value) for key, value in properties.items()},
        source_ids=list(source_ids),
    )


def _map_relation(relation, projection_version: str) -> Relation:
    """校验关系来源、版本和置信度后转换统一关系。"""
    properties = dict(relation)
    relation_version = properties.pop("projection_version", None)
    source_ids = list(properties.pop("source_ids", []))
    if relation_version != projection_version:
        raise RuntimeError("路径关系投影版本不一致")
    if not source_ids:
        raise RuntimeError("路径关系缺少来源")
    confidence = ConfidenceLevel(properties.pop("confidence"))
    relation_id = properties.pop("edge_id")
    effective_start = _date(properties.pop("effective_start", None))
    effective_end = _date(properties.pop("effective_end", None))
    return Relation(
        relation_id=relation_id,
        source_entity_id=relation.start_node["node_id"],
        target_entity_id=relation.end_node["node_id"],
        relation_type=relation.type,
        properties={key: _json_scalar(value) for key, value in properties.items()},
        confidence=confidence,
        source_ids=source_ids,
        effective_start=effective_start,
        effective_end=effective_end,
    )


def _date(value: object) -> date | None:
    """把投影 ISO 日期属性恢复为 date。"""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value)
    try:
        return date.fromisoformat(text[:10])
    except ValueError as exc:
        raise RuntimeError(f"投影包含非法日期: {text}") from exc


def _json_scalar(value: object):
    """把 Neo4j 标量转换为 JSON 可序列化值。"""
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, list):
        return [_json_scalar(item) for item in value]
    return str(value)


def _confidence_rank(confidence: ConfidenceLevel) -> int:
    """返回置信度从低到高的排序值。"""
    return {
        ConfidenceLevel.LOW: 0,
        ConfidenceLevel.MEDIUM: 1,
        ConfidenceLevel.HIGH: 2,
    }[confidence]


graph_path_query_service = GraphPathQueryService()
