"""企业尽调图谱 Schema 和快照单元测试。"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from yuxi.company_due_diligence.graph.schema import (
    GraphEdge,
    GraphNode,
    GraphNodeType,
    GraphPathQuery,
    GraphProjectionRequest,
    GraphRelationType,
    ProjectionSnapshotStore,
)
from yuxi.company_due_diligence.models import ConfidenceLevel


def test_projection_request_and_path_query_enforce_limits():
    """投影范围应去重，路径跳数和结果数不得越过上限。"""
    request = GraphProjectionRequest(eids=[" eid-1 ", "eid-1", "eid-2"], batch_size=100)

    assert request.eids == ["eid-1", "eid-2"]
    with pytest.raises(ValidationError):
        GraphPathQuery(start_eid="eid-1", max_hops=4)
    with pytest.raises(ValidationError):
        GraphPathQuery(start_eid="eid-1", max_paths=51)


def test_snapshot_checksum_is_stable_and_reports_duplicate_edges(tmp_path: Path):
    """相同图内容应得到相同 checksum，重复边只计入质量统计。"""
    first = _build_snapshot_store(tmp_path / "first.sqlite", duplicate_edge=True)
    second = _build_snapshot_store(tmp_path / "second.sqlite", duplicate_edge=False)
    try:
        first_manifest = first.finalize(["eid-1"])
        second_manifest = second.finalize(["eid-1"])

        assert first_manifest.checksum == second_manifest.checksum
        assert first_manifest.projection_version == second_manifest.projection_version
        assert first_manifest.statistics.node_count == 2
        assert first_manifest.statistics.edge_count == 1
        assert first_manifest.statistics.duplicate_edge_count == 1
        assert second_manifest.statistics.duplicate_edge_count == 0
    finally:
        first.close()
        second.close()


def _build_snapshot_store(path: Path, *, duplicate_edge: bool) -> ProjectionSnapshotStore:
    """构造一个企业和股东关系的最小快照。"""
    store = ProjectionSnapshotStore(path)
    company = GraphNode(node_id="eid-1", node_type=GraphNodeType.COMPANY, name="示例企业")
    shareholder = GraphNode(node_id="shareholder-1", node_type=GraphNodeType.SHAREHOLDER, name="示例股东")
    edge = GraphEdge(
        edge_id="edge-1",
        edge_type=GraphRelationType.HOLDS,
        source_node_id="shareholder-1",
        target_node_id="eid-1",
        confidence=ConfidenceLevel.HIGH,
        properties={"source_table": "t_partners_alls"},
        source_ids=["source-1"],
    )
    store.add_node(company)
    store.add_node(shareholder)
    store.add_edge(edge)
    if duplicate_edge:
        store.add_edge(edge)
    return store
