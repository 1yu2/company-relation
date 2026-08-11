"""企业尽调图投影映射单元测试。"""

from yuxi.company_due_diligence.graph.projection import _edge
from yuxi.company_due_diligence.graph.schema import GraphRelationType
from yuxi.company_due_diligence.models import ConfidenceLevel, build_source_id


def test_projection_edge_has_stable_source_version_independent_key():
    """边 ID 和来源应只由稳定业务字段决定，不依赖投影时间。"""
    first = _edge(
        GraphRelationType.HOLDS,
        "shareholder-1",
        "eid-1",
        "t_partners_alls",
        "record-1",
        ConfidenceLevel.HIGH,
        {"is_history": False},
    )
    second = _edge(
        GraphRelationType.HOLDS,
        "shareholder-1",
        "eid-1",
        "t_partners_alls",
        "record-1",
        ConfidenceLevel.HIGH,
        {"is_history": False},
    )

    assert first.edge_id == second.edge_id
    assert first.source_ids == [build_source_id("t_partners_alls", "record-1")]
    assert first.properties["source_table"] == "t_partners_alls"
    assert first.properties["source_record_key"] == "record-1"
    assert first.confidence == ConfidenceLevel.HIGH
