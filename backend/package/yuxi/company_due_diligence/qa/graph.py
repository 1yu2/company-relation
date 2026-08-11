"""企业尽调问答受控服务编排器。"""

from __future__ import annotations

import asyncio

from yuxi.company_due_diligence.evidence.models import EvidenceQuery, EvidenceQueryResult
from yuxi.company_due_diligence.evidence.query_service import EvidenceQueryService, evidence_query_service
from yuxi.company_due_diligence.graph.query_service import GraphPathQueryService, graph_path_query_service
from yuxi.company_due_diligence.graph.repository import GraphUnavailableError
from yuxi.company_due_diligence.graph.schema import GraphPathQuery, GraphPathResult
from yuxi.company_due_diligence.models import SourceStatus, StructuredQueryRequest, StructuredQueryResult
from yuxi.company_due_diligence.qa.models import (
    CompanyQuestionRequest,
    QueryPlan,
    QueryStep,
    QueryStepStatus,
)
from yuxi.company_due_diligence.structured_query_service import StructuredQueryService, structured_query_service


class QAWorkflowResult:
    """保存编排器执行后的三个底层服务结果。"""

    def __init__(
        self,
        *,
        structured: list[StructuredQueryResult],
        graph: GraphPathResult | None,
        evidence: EvidenceQueryResult | None,
        steps: list[QueryStep],
    ) -> None:
        self.structured = structured
        self.graph = graph
        self.evidence = evidence
        self.steps = steps


class QAWorkflow:
    """并行执行计划中的白名单 MySQL、Neo4j 和 Milvus 查询。"""

    def __init__(
        self,
        structured_service: StructuredQueryService | None = None,
        graph_service: GraphPathQueryService | None = None,
        evidence_service: EvidenceQueryService | None = None,
    ) -> None:
        self._structured_service = structured_service or structured_query_service
        self._graph_service = graph_service or graph_path_query_service
        self._evidence_service = evidence_service or evidence_query_service

    async def execute(
        self,
        *,
        eid: str,
        target_eid: str | None,
        request: CompanyQuestionRequest,
        plan: QueryPlan,
    ) -> QAWorkflowResult:
        """执行有限查询计划并把单源故障保留为明确步骤状态。"""
        tasks = [self._run_structured(eid, query_type, request) for query_type in plan.structured_queries]
        structured = list(await asyncio.gather(*tasks)) if tasks else []
        steps = [_structured_step(result) for result in structured]

        graph = None
        if plan.use_graph:
            try:
                graph = await self._graph_service.query(
                    GraphPathQuery(
                        start_eid=eid,
                        target_eid=target_eid,
                        relation_types=plan.graph_relation_types,
                        max_hops=request.hop_limit,
                        max_paths=request.top_k,
                        include_clues=request.include_low_confidence,
                    )
                )
                steps.append(
                    QueryStep(
                        source="neo4j",
                        operation="path_query",
                        status=QueryStepStatus.COMPLETE if graph.paths else QueryStepStatus.NO_DATA,
                        item_count=len(graph.paths),
                    )
                )
            except GraphUnavailableError:
                steps.append(
                    QueryStep(
                        source="neo4j",
                        operation="path_query",
                        status=QueryStepStatus.UNAVAILABLE,
                        message="Neo4j 关系投影不可用",
                    )
                )

        evidence = None
        if plan.evidence_types:
            evidence = await self._evidence_service.query(
                EvidenceQuery(
                    eid=eid,
                    query=request.question,
                    document_types=plan.evidence_types,
                    end_date=request.as_of,
                    top_k=request.top_k,
                )
            )
            evidence_status = (
                QueryStepStatus.UNAVAILABLE
                if evidence.data_quality.source_status == SourceStatus.UNAVAILABLE
                else QueryStepStatus.COMPLETE
                if evidence.evidence
                else QueryStepStatus.NO_DATA
            )
            steps.append(
                QueryStep(
                    source="milvus",
                    operation="evidence_query",
                    status=evidence_status,
                    item_count=len(evidence.evidence),
                    message=evidence.data_quality.warnings[0] if evidence.data_quality.warnings else None,
                )
            )

        return QAWorkflowResult(structured=structured, graph=graph, evidence=evidence, steps=steps)

    async def _run_structured(self, eid, query_type, request) -> StructuredQueryResult:
        """执行一个受控结构化查询。"""
        return await self._structured_service.query(
            StructuredQueryRequest(
                eid=eid,
                query_type=query_type,
                end_date=request.as_of,
                page_size=min(request.top_k, 100),
            )
        )


def _structured_step(result: StructuredQueryResult) -> QueryStep:
    """把结构化查询状态映射为统一执行步骤。"""
    item_count = len(result.facts) + len(result.relations)
    if result.data_quality.source_status == SourceStatus.UNAVAILABLE:
        status = QueryStepStatus.UNAVAILABLE
    elif item_count:
        status = QueryStepStatus.COMPLETE
    else:
        status = QueryStepStatus.NO_DATA
    return QueryStep(
        source="mysql",
        operation=result.query_type.value,
        status=status,
        item_count=item_count,
        message=result.data_quality.warnings[0] if result.data_quality.warnings else None,
    )
