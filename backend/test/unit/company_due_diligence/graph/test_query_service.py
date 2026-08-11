"""企业尽调图路径查询服务单元测试。"""

from datetime import UTC, datetime

import pytest

from yuxi.company_due_diligence.graph.query_service import GraphPathQueryService
from yuxi.company_due_diligence.graph.repository import CompanyDueDiligenceGraphRepository, GraphUnavailableError
from yuxi.company_due_diligence.graph.schema import (
    GraphPathQuery,
    GraphProjectionInfo,
    GraphRelationType,
    ProjectionManifest,
    ProjectionStatistics,
)

pytestmark = pytest.mark.asyncio


class FakeNode(dict):
    """模拟 Neo4j Node 属性访问。"""


class FakeRelationship(dict):
    """模拟 Neo4j Relationship 的方向和类型。"""

    def __init__(self, start_node, end_node, relation_type: str, **properties):
        super().__init__(properties)
        self.start_node = start_node
        self.end_node = end_node
        self.type = relation_type


class FakePath:
    """模拟 Neo4j Path 节点和关系列表。"""

    def __init__(self, nodes, relationships):
        self.nodes = nodes
        self.relationships = relationships


class FakePathRepository:
    """返回固定 Neo4j 路径。"""

    def __init__(self, paths):
        self.paths = paths

    async def query_paths(self, query):
        """返回固定版本和路径。"""
        assert query.max_hops <= 3
        return "cdd-test", self.paths


class EmptyConnection:
    """模拟缺少 Driver 的 Neo4j 连接。"""

    driver = None


async def test_path_mapping_keeps_direction_sources_and_confidence():
    """路径映射应保留原始方向、来源和最低置信度。"""
    company = FakeNode(
        node_id="eid-1",
        node_type="Company",
        name="示例企业",
        projection_version="cdd-test",
        source_ids=["source-company"],
    )
    shareholder = FakeNode(
        node_id="shareholder-1",
        node_type="Shareholder",
        name="示例股东",
        projection_version="cdd-test",
        source_ids=["source-edge"],
    )
    relationship = FakeRelationship(
        shareholder,
        company,
        "HOLDS",
        edge_id="edge-1",
        projection_version="cdd-test",
        confidence="high",
        source_ids=["source-edge"],
        effective_start="2024-01-02",
    )
    service = GraphPathQueryService(FakePathRepository([FakePath([company, shareholder], [relationship])]))

    result = await service.query(
        GraphPathQuery(start_eid="eid-1", relation_types=[GraphRelationType.HOLDS], max_hops=2)
    )

    assert result.paths[0].hop_count == 1
    assert result.paths[0].edges[0].source_entity_id == "shareholder-1"
    assert result.paths[0].edges[0].target_entity_id == "eid-1"
    assert result.paths[0].source_ids == ["source-edge"]
    assert result.paths[0].edges[0].effective_start.isoformat() == "2024-01-02"


async def test_path_mapping_rejects_missing_edge_source():
    """Neo4j 返回的边缺少来源时必须显式失败。"""
    company = FakeNode(
        node_id="eid-1",
        node_type="Company",
        name="示例企业",
        projection_version="cdd-test",
        source_ids=[],
    )
    shareholder = FakeNode(
        node_id="shareholder-1",
        node_type="Shareholder",
        name="示例股东",
        projection_version="cdd-test",
        source_ids=[],
    )
    relationship = FakeRelationship(
        shareholder,
        company,
        "HOLDS",
        edge_id="edge-1",
        projection_version="cdd-test",
        confidence="high",
        source_ids=[],
    )
    service = GraphPathQueryService(FakePathRepository([FakePath([company, shareholder], [relationship])]))

    with pytest.raises(RuntimeError, match="缺少来源"):
        await service.query(GraphPathQuery(start_eid="eid-1"))


async def test_repository_reports_unavailable_without_driver():
    """Neo4j 不可用时不得伪装为空路径。"""
    repository = CompanyDueDiligenceGraphRepository(connection=EmptyConnection())

    with pytest.raises(GraphUnavailableError, match="不可用"):
        await repository.get_info()


async def test_repository_reports_unavailable_without_active_projection(monkeypatch):
    """Neo4j 在线但没有已激活投影时应返回来源不可用。"""
    repository = CompanyDueDiligenceGraphRepository()
    monkeypatch.setattr(repository, "_get_info_sync", lambda: GraphProjectionInfo(available=True))

    with pytest.raises(GraphUnavailableError, match="没有已激活"):
        await repository.query_paths(GraphPathQuery(start_eid="eid-1"))


async def test_repository_cypher_uses_validated_types_and_hop_limit(monkeypatch):
    """路径 Cypher 只能拼接枚举关系类型和已校验跳数。"""
    captured = {}

    class FakeResult:
        """模拟空路径结果。"""

        def __iter__(self):
            return iter([])

    class FakeSession:
        """记录 Cypher 和参数。"""

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def run(self, cypher, **parameters):
            captured["cypher"] = cypher
            captured["parameters"] = parameters
            return FakeResult()

    class FakeDriver:
        """返回记录会话。"""

        def session(self):
            return FakeSession()

    class FakeConnection:
        """提供固定 Driver。"""

        driver = FakeDriver()

    repository = CompanyDueDiligenceGraphRepository(connection=FakeConnection())
    monkeypatch.setattr(repository, "_get_info_sync", lambda: _projection_info())

    repository._query_paths_sync(
        GraphPathQuery(
            start_eid="eid-1",
            relation_types=[GraphRelationType.HOLDS, GraphRelationType.OWNS_PATENT],
            max_hops=2,
            max_paths=5,
        )
    )

    assert ":HOLDS|OWNS_PATENT*1..2" in captured["cypher"]
    assert "relation.confidence = 'high'" in captured["cypher"]
    assert captured["parameters"]["max_paths"] == 5


def _projection_info() -> GraphProjectionInfo:
    """生成固定当前投影信息。"""
    return GraphProjectionInfo(
        available=True,
        current=ProjectionManifest(
            projection_version="cdd-test",
            checksum="a" * 64,
            created_at=datetime(2026, 8, 10, tzinfo=UTC),
            statistics=ProjectionStatistics(
                node_count=0,
                edge_count=0,
                orphan_count=0,
                duplicate_node_count=0,
                duplicate_edge_count=0,
            ),
        ),
    )
