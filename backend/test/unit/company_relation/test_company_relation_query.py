import pytest

from yuxi.company_relation.query_service import CompanyRelationQueryService


pytestmark = pytest.mark.asyncio


async def test_graph_rag_returns_multi_hop_path_and_sources(company_relation_snapshot):
    service = CompanyRelationQueryService(snapshot=company_relation_snapshot)

    result = await service.query("甲公司和乙公司有什么关联？", strategy="graph_rag")

    assert result.refused is False
    assert result.answer_entity_ids == ["company:a", "company:holder", "company:b"]
    assert result.paths == [["holds:holder:a", "holds:holder:b"]]
    assert result.source_ids == ["source:holder"]
    assert result.sources[0]["title"] == "股东资料"


async def test_vector_rag_does_not_invent_multi_hop_path(company_relation_snapshot):
    service = CompanyRelationQueryService(snapshot=company_relation_snapshot)

    result = await service.query("甲公司和乙公司有什么关联？", strategy="vector_rag")

    assert result.refused is True
    assert result.fact_ids == []
    assert result.paths == []


async def test_graph_rag_explicitly_refuses_unknown_company(company_relation_snapshot):
    service = CompanyRelationQueryService(snapshot=company_relation_snapshot)

    result = await service.query("不存在公司和甲公司有什么关联？", strategy="graph_rag")

    assert result.refused is True
    assert result.answer == "未查询到可由当前图谱证据支持的关系。"
    assert result.source_ids == []
