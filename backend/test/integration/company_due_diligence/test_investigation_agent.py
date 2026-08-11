"""企业尽调领域智能体真实 MySQL 集成测试。"""

import pytest
from sqlalchemy import text

from yuxi.company_due_diligence.agent.models import CompanyInvestigationRequest, InvestigationStatus
from yuxi.company_due_diligence.agent.service import CompanyDueDiligenceAgent
from yuxi.company_due_diligence.entity_resolution import EntityResolutionService
from yuxi.company_due_diligence.mysql import CompanyDueDiligenceMySQL
from yuxi.company_due_diligence.qa.graph import QAWorkflow
from yuxi.company_due_diligence.qa.service import CompanyQuestionService
from yuxi.company_due_diligence.structured_query_service import StructuredQueryService

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


class ForbiddenGraphService:
    """股东问题不应调用的图谱服务。"""

    async def query(self, _request):
        """任何调用都表示工具计划错误。"""
        raise AssertionError("股东问题不应调用 Neo4j")


class ForbiddenEvidenceService:
    """股东问题不应调用的证据服务。"""

    async def query(self, _request):
        """任何调用都表示工具计划错误。"""
        raise AssertionError("股东问题不应调用 Milvus")


async def test_real_shareholder_investigation_uses_context_and_claim_ledger():
    """统一智能体应复用企业上下文、只查股东并输出完整 Claim Ledger。"""
    mysql = CompanyDueDiligenceMySQL()
    resolver = EntityResolutionService(mysql=mysql)
    question_service = CompanyQuestionService(
        resolver=resolver,
        workflow=QAWorkflow(
            structured_service=StructuredQueryService(mysql=mysql),
            graph_service=ForbiddenGraphService(),
            evidence_service=ForbiddenEvidenceService(),
        ),
    )
    agent = CompanyDueDiligenceAgent(resolver=resolver, question_service=question_service)
    try:
        async with mysql.connection() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT p.eid, e.name
                    FROM t_partners_alls p
                    JOIN t_enterprise e ON e.eid = p.eid
                    WHERE p.is_history = 0
                    ORDER BY p.eid
                    LIMIT 1
                    """
                )
            )
            company = dict(result.mappings().one())

        response = await agent.investigate(
            CompanyInvestigationRequest(
                company_context={"eid": company["eid"], "name": company["name"]},
                message="股东有哪些？",
            )
        )
        health = await mysql.health()
    finally:
        await mysql.close()

    assert response.status == InvestigationStatus.COMPLETE
    assert response.facts
    assert {step.source for step in response.steps} == {"mysql"}
    assert len(response.claim_ledger.claims) == len(response.facts) + len(response.relations)
    assert all(
        source_id in {source.source_id for source in response.sources}
        for claim in response.claim_ledger.claims
        for source_id in claim.source_ids
    )
    assert response.latency.neo4j_ms == 0
    assert response.latency.milvus_ms == 0
    assert health.pool.checked_out == 0
