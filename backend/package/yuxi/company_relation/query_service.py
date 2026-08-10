from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import networkx as nx
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from yuxi.company_relation.neo4j_repository import CompanyRelationNeo4jRepository
from yuxi.company_relation.paths import get_default_snapshot_path
from yuxi.company_relation.snapshot import ATTRIBUTE_LABELS, CompanyRelationSnapshot


@dataclass(frozen=True)
class ParsedQuestion:
    """表示自然语言问题解析后的领域意图。"""

    intent: str
    entity_ids: tuple[str, ...] = ()
    attribute: str | None = None


@dataclass
class CompanyRelationQueryResult:
    """统一三种查询策略的答案、事实、证据和性能输出。"""

    strategy: str
    query: str
    intent: str
    answer: str
    refused: bool
    answer_entity_ids: list[str] = field(default_factory=list)
    fact_ids: list[str] = field(default_factory=list)
    paths: list[list[str]] = field(default_factory=list)
    retrieved_evidence_ids: list[str] = field(default_factory=list)
    source_ids: list[str] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)
    latency_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """转换为 API 和评测可直接使用的字典。"""
        return {
            "strategy": self.strategy,
            "query": self.query,
            "intent": self.intent,
            "answer": self.answer,
            "refused": self.refused,
            "answer_entity_ids": self.answer_entity_ids,
            "fact_ids": self.fact_ids,
            "paths": self.paths,
            "retrieved_evidence_ids": self.retrieved_evidence_ids,
            "source_ids": self.source_ids,
            "sources": self.sources,
            "latency_ms": self.latency_ms,
        }


class CompanyRelationQueryParser:
    """基于冻结实体词表解析企业关系常见问题。"""

    def __init__(self, snapshot: CompanyRelationSnapshot):
        self.snapshot = snapshot
        self.named_nodes = sorted(
            (
                node
                for node in snapshot.nodes
                if node.node_type in {"Company", "Person"} and node.name
            ),
            key=lambda node: (-len(node.name), node.node_id),
        )

    def parse(self, query: str) -> ParsedQuestion:
        """将自然语言问题解析为有限且显式的领域意图。"""
        matched_nodes = []
        matched_spans: list[tuple[int, int]] = []
        for node in self.named_nodes:
            start = query.find(node.name)
            if start < 0:
                continue
            span = (start, start + len(node.name))
            if any(span[0] < other[1] and other[0] < span[1] for other in matched_spans):
                continue
            matched_nodes.append((start, node))
            matched_spans.append(span)
        matched_nodes.sort(key=lambda item: item[0])

        companies = [node for _, node in matched_nodes if node.node_type == "Company"]
        people = [node for _, node in matched_nodes if node.node_type == "Person"]
        if any(keyword in query for keyword in ("有什么关联", "关联路径", "什么关系")):
            return ParsedQuestion("relation_path", tuple(node.node_id for node in companies[:2]))
        if any(keyword in query for keyword in ("同时任职", "任职哪些公司", "在哪些公司任职")):
            return ParsedQuestion("executive_companies", tuple(node.node_id for node in people[:1]))
        if "股东" in query:
            return ParsedQuestion("shareholders", tuple(node.node_id for node in companies[:1]))
        if "公告" in query:
            return ParsedQuestion("announcements", tuple(node.node_id for node in companies[:1]))
        if "行业" in query:
            return ParsedQuestion("company_attribute", tuple(node.node_id for node in companies[:1]), "industry")
        if "法定代表人" in query or "法人" in query:
            return ParsedQuestion(
                "company_attribute",
                tuple(node.node_id for node in companies[:1]),
                "legal_representative",
            )
        if "上市日期" in query or "什么时候上市" in query:
            return ParsedQuestion("company_attribute", tuple(node.node_id for node in companies[:1]), "listing_date")
        if "注册地址" in query or "注册地" in query:
            return ParsedQuestion(
                "company_attribute",
                tuple(node.node_id for node in companies[:1]),
                "registered_address",
            )
        return ParsedQuestion("unknown", tuple(node.node_id for _, node in matched_nodes))


class CompanyRelationQueryService:
    """执行企业关系的向量、GraphRAG 和 Cypher 查询。"""

    def __init__(
        self,
        snapshot: CompanyRelationSnapshot | None = None,
        neo4j_repository: CompanyRelationNeo4jRepository | None = None,
    ):
        self.snapshot = snapshot or CompanyRelationSnapshot.load(get_default_snapshot_path())
        validation = self.snapshot.validate()
        if not validation.valid:
            raise ValueError(f"企业关系快照校验失败: {validation.to_dict()}")
        self.parser = CompanyRelationQueryParser(self.snapshot)
        self.neo4j_repository = neo4j_repository
        self.raw_relation_graph = self._build_raw_relation_graph()
        self.vector_documents = [
            document
            for document in self.snapshot.evidence_documents
            if document.get("predicate") != "RELATED_TO"
        ]
        self.vectorizer = TfidfVectorizer(analyzer="char", ngram_range=(2, 4))
        self.document_matrix = self.vectorizer.fit_transform(document["text"] for document in self.vector_documents)

    async def query(self, query: str, strategy: str = "graph_rag", top_k: int = 10) -> CompanyRelationQueryResult:
        """按指定策略执行问题并返回统一输出。"""
        if strategy == "vector_rag":
            return self._query_vector(query, top_k=top_k)
        if strategy == "graph_rag":
            return self._query_graph(query)
        if strategy == "cypher_direct":
            return await self._query_cypher(query)
        raise ValueError(f"不支持的企业关系查询策略: {strategy}")

    async def import_snapshot(self) -> dict[str, int]:
        """将当前冻结快照替换导入 Neo4j。"""
        repository = self.neo4j_repository or CompanyRelationNeo4jRepository()
        self.neo4j_repository = repository
        return await repository.replace_snapshot(self.snapshot)

    def _query_vector(self, query: str, *, top_k: int) -> CompanyRelationQueryResult:
        started_at = time.perf_counter()
        parsed = self.parser.parse(query)
        limit = min(max(top_k, 1), len(self.vector_documents))
        query_vector = self.vectorizer.transform([query])
        scores = cosine_similarity(query_vector, self.document_matrix)[0]
        ranked_indexes = sorted(range(len(scores)), key=lambda index: (-scores[index], index))[:limit]
        retrieved = [self.vector_documents[index] for index in ranked_indexes if scores[index] > 0]
        result = self._answer_from_evidence(query, parsed, retrieved, strategy="vector_rag", allow_path_traversal=False)
        result.latency_ms = round((time.perf_counter() - started_at) * 1000, 3)
        return result

    def _query_graph(self, query: str) -> CompanyRelationQueryResult:
        started_at = time.perf_counter()
        parsed = self.parser.parse(query)
        if parsed.intent == "relation_path":
            result = self._answer_graph_path(query, parsed)
        else:
            result = self._answer_from_evidence(
                query,
                parsed,
                self.snapshot.evidence_documents,
                strategy="graph_rag",
                allow_path_traversal=True,
            )
        result.latency_ms = round((time.perf_counter() - started_at) * 1000, 3)
        return result

    async def _query_cypher(self, query: str) -> CompanyRelationQueryResult:
        started_at = time.perf_counter()
        parsed = self.parser.parse(query)
        repository = self.neo4j_repository or CompanyRelationNeo4jRepository()
        self.neo4j_repository = repository

        result = await self._execute_cypher_intent(query, parsed, repository)
        result.latency_ms = round((time.perf_counter() - started_at) * 1000, 3)
        return result

    async def _execute_cypher_intent(
        self,
        query: str,
        parsed: ParsedQuestion,
        repository: CompanyRelationNeo4jRepository,
    ) -> CompanyRelationQueryResult:
        if parsed.intent == "company_attribute" and len(parsed.entity_ids) == 1 and parsed.attribute:
            row = await repository.get_company_attribute(
                self.snapshot.dataset_id,
                parsed.entity_ids[0],
                parsed.attribute,
            )
            if row:
                fact_id = f"{row['company_id']}#{parsed.attribute}"
                return self._build_result(
                    strategy="cypher_direct",
                    query=query,
                    parsed=parsed,
                    answer=f"{row['company_name']}的{ATTRIBUTE_LABELS[parsed.attribute]}是{row['value']}。",
                    answer_entity_ids=[str(row["value"])],
                    fact_ids=[fact_id],
                    evidence_ids=[fact_id],
                    source_ids=row.get("source_ids") or [],
                )
        elif parsed.intent == "shareholders" and len(parsed.entity_ids) == 1:
            rows = await repository.list_shareholders(self.snapshot.dataset_id, parsed.entity_ids[0])
            if rows:
                names = [row["entity_name"] for row in rows]
                return self._build_result(
                    strategy="cypher_direct",
                    query=query,
                    parsed=parsed,
                    answer=f"直接股东包括：{'、'.join(names)}。",
                    answer_entity_ids=[row["entity_id"] for row in rows],
                    fact_ids=[row["edge_id"] for row in rows],
                    evidence_ids=[row["edge_id"] for row in rows],
                    source_ids=_flatten_source_ids(rows),
                )
        elif parsed.intent == "executive_companies" and len(parsed.entity_ids) == 1:
            rows = await repository.list_executive_companies(self.snapshot.dataset_id, parsed.entity_ids[0])
            if rows:
                names = [row["entity_name"] for row in rows]
                return self._build_result(
                    strategy="cypher_direct",
                    query=query,
                    parsed=parsed,
                    answer=f"任职公司包括：{'、'.join(names)}。",
                    answer_entity_ids=[row["entity_id"] for row in rows],
                    fact_ids=[row["edge_id"] for row in rows],
                    evidence_ids=[row["edge_id"] for row in rows],
                    source_ids=_flatten_source_ids(rows),
                )
        elif parsed.intent == "announcements" and len(parsed.entity_ids) == 1:
            rows = await repository.list_announcements(self.snapshot.dataset_id, parsed.entity_ids[0])
            if rows:
                names = [row["entity_name"] for row in rows]
                return self._build_result(
                    strategy="cypher_direct",
                    query=query,
                    parsed=parsed,
                    answer=f"已关联公告包括：{'、'.join(names)}。",
                    answer_entity_ids=[row["entity_id"] for row in rows],
                    fact_ids=[row["edge_id"] for row in rows],
                    evidence_ids=[row["edge_id"] for row in rows],
                    source_ids=_flatten_source_ids(rows),
                )
        elif parsed.intent == "relation_path" and len(parsed.entity_ids) == 2:
            row = await repository.find_relation_path(
                self.snapshot.dataset_id,
                parsed.entity_ids[0],
                parsed.entity_ids[1],
            )
            if row:
                edge_ids = row["edge_ids"]
                source_ids = sorted({item for group in row.get("source_id_groups") or [] for item in group or []})
                return self._build_result(
                    strategy="cypher_direct",
                    query=query,
                    parsed=parsed,
                    answer=f"关联路径：{' → '.join(row['node_names'])}。",
                    answer_entity_ids=row["node_ids"],
                    fact_ids=edge_ids,
                    paths=[edge_ids],
                    evidence_ids=edge_ids,
                    source_ids=source_ids,
                )
        return self._refusal("cypher_direct", query, parsed)

    def _answer_from_evidence(
        self,
        query: str,
        parsed: ParsedQuestion,
        evidence: list[dict[str, Any]],
        *,
        strategy: str,
        allow_path_traversal: bool,
    ) -> CompanyRelationQueryResult:
        retrieved_ids = [item["evidence_id"] for item in evidence]
        if parsed.intent == "company_attribute" and len(parsed.entity_ids) == 1 and parsed.attribute:
            fact_id = f"{parsed.entity_ids[0]}#{parsed.attribute}"
            item = next((candidate for candidate in evidence if candidate["evidence_id"] == fact_id), None)
            if item:
                company = self.snapshot.nodes_by_id[parsed.entity_ids[0]]
                result_evidence_ids = [fact_id] if strategy == "graph_rag" else retrieved_ids
                return self._build_result(
                    strategy=strategy,
                    query=query,
                    parsed=parsed,
                    answer=f"{company.name}的{ATTRIBUTE_LABELS[parsed.attribute]}是{item['value']}。",
                    answer_entity_ids=[str(item["value"])],
                    fact_ids=[fact_id],
                    evidence_ids=result_evidence_ids,
                    source_ids=item["source_ids"],
                )
        elif parsed.intent == "shareholders" and len(parsed.entity_ids) == 1:
            items = [
                item
                for item in evidence
                if item.get("predicate") == "HOLDS" and item.get("object_id") == parsed.entity_ids[0]
            ]
            if items:
                items.sort(
                    key=lambda item: (
                        -float((item.get("value") or {}).get("share_ratio") or 0),
                        item["subject_id"],
                    )
                )
                names = [self.snapshot.nodes_by_id[item["subject_id"]].name for item in items]
                result_evidence_ids = (
                    [item["evidence_id"] for item in items] if strategy == "graph_rag" else retrieved_ids
                )
                return self._build_result(
                    strategy=strategy,
                    query=query,
                    parsed=parsed,
                    answer=f"直接股东包括：{'、'.join(names)}。",
                    answer_entity_ids=[item["subject_id"] for item in items],
                    fact_ids=[item["evidence_id"] for item in items],
                    evidence_ids=result_evidence_ids,
                    source_ids=_source_ids_from_evidence(items),
                )
        elif parsed.intent == "executive_companies" and len(parsed.entity_ids) == 1:
            items = [
                item
                for item in evidence
                if item.get("predicate") == "EXECUTIVE_OF" and item.get("subject_id") == parsed.entity_ids[0]
            ]
            if items:
                items.sort(key=lambda item: item["object_id"])
                names = [self.snapshot.nodes_by_id[item["object_id"]].name for item in items]
                result_evidence_ids = (
                    [item["evidence_id"] for item in items] if strategy == "graph_rag" else retrieved_ids
                )
                return self._build_result(
                    strategy=strategy,
                    query=query,
                    parsed=parsed,
                    answer=f"任职公司包括：{'、'.join(names)}。",
                    answer_entity_ids=[item["object_id"] for item in items],
                    fact_ids=[item["evidence_id"] for item in items],
                    evidence_ids=result_evidence_ids,
                    source_ids=_source_ids_from_evidence(items),
                )
        elif parsed.intent == "announcements" and len(parsed.entity_ids) == 1:
            items = [
                item
                for item in evidence
                if item.get("predicate") == "RELEASED" and item.get("subject_id") == parsed.entity_ids[0]
            ]
            if items:
                items.sort(key=lambda item: item["object_id"])
                names = [self.snapshot.nodes_by_id[item["object_id"]].name for item in items]
                result_evidence_ids = (
                    [item["evidence_id"] for item in items] if strategy == "graph_rag" else retrieved_ids
                )
                return self._build_result(
                    strategy=strategy,
                    query=query,
                    parsed=parsed,
                    answer=f"已关联公告包括：{'、'.join(names)}。",
                    answer_entity_ids=[item["object_id"] for item in items],
                    fact_ids=[item["evidence_id"] for item in items],
                    evidence_ids=result_evidence_ids,
                    source_ids=_source_ids_from_evidence(items),
                )
        elif parsed.intent == "relation_path" and allow_path_traversal:
            return self._answer_graph_path(query, parsed)
        return self._refusal(strategy, query, parsed, [] if strategy == "graph_rag" else retrieved_ids)

    def _answer_graph_path(self, query: str, parsed: ParsedQuestion) -> CompanyRelationQueryResult:
        if len(parsed.entity_ids) != 2:
            return self._refusal("graph_rag", query, parsed)
        source_id, target_id = parsed.entity_ids
        if source_id not in self.raw_relation_graph or target_id not in self.raw_relation_graph:
            return self._refusal("graph_rag", query, parsed)
        try:
            node_paths = list(nx.all_shortest_paths(self.raw_relation_graph, source_id, target_id))
        except nx.NetworkXNoPath:
            return self._refusal("graph_rag", query, parsed)

        candidates = []
        for node_path in node_paths:
            if len(node_path) - 1 > 3:
                continue
            edge_path = []
            for source_node_id, target_node_id in zip(node_path, node_path[1:]):
                edge_ids = sorted(self.raw_relation_graph[source_node_id][target_node_id])
                edge_path.append(edge_ids[0])
            candidates.append((tuple(edge_path), node_path))
        if not candidates:
            return self._refusal("graph_rag", query, parsed)

        edge_ids, node_ids = min(candidates, key=lambda item: item[0])
        node_names = [self.snapshot.nodes_by_id[node_id].name for node_id in node_ids]
        evidence = [self.snapshot.evidence_by_id[edge_id] for edge_id in edge_ids]
        return self._build_result(
            strategy="graph_rag",
            query=query,
            parsed=parsed,
            answer=f"关联路径：{' → '.join(node_names)}。",
            answer_entity_ids=list(node_ids),
            fact_ids=list(edge_ids),
            paths=[list(edge_ids)],
            evidence_ids=list(edge_ids),
            source_ids=_source_ids_from_evidence(evidence),
        )

    def _build_result(
        self,
        *,
        strategy: str,
        query: str,
        parsed: ParsedQuestion,
        answer: str,
        answer_entity_ids: list[str],
        fact_ids: list[str],
        evidence_ids: list[str],
        source_ids: list[str],
        paths: list[list[str]] | None = None,
    ) -> CompanyRelationQueryResult:
        normalized_source_ids = sorted(set(source_ids))
        return CompanyRelationQueryResult(
            strategy=strategy,
            query=query,
            intent=parsed.intent,
            answer=answer,
            refused=False,
            answer_entity_ids=answer_entity_ids,
            fact_ids=fact_ids,
            paths=paths or [],
            retrieved_evidence_ids=evidence_ids,
            source_ids=normalized_source_ids,
            sources=self.snapshot.get_sources(normalized_source_ids),
        )

    def _refusal(
        self,
        strategy: str,
        query: str,
        parsed: ParsedQuestion,
        retrieved_evidence_ids: list[str] | None = None,
    ) -> CompanyRelationQueryResult:
        return CompanyRelationQueryResult(
            strategy=strategy,
            query=query,
            intent=parsed.intent,
            answer="未查询到可由当前图谱证据支持的关系。",
            refused=True,
            retrieved_evidence_ids=retrieved_evidence_ids or [],
        )

    def _build_raw_relation_graph(self) -> nx.MultiGraph:
        graph = nx.MultiGraph()
        for node in self.snapshot.nodes:
            if node.node_type in {"Company", "Person"}:
                graph.add_node(node.node_id, node=node)
        for edge in self.snapshot.edges:
            if edge.edge_type not in {"HOLDS", "EXECUTIVE_OF"}:
                continue
            if edge.source_node_id not in graph or edge.target_node_id not in graph:
                continue
            graph.add_edge(edge.source_node_id, edge.target_node_id, key=edge.edge_id, edge=edge)
        return graph


def _source_ids_from_evidence(items: list[dict[str, Any]]) -> list[str]:
    return sorted({source_id for item in items for source_id in item.get("source_ids") or []})


def _flatten_source_ids(rows: list[dict[str, Any]]) -> list[str]:
    return sorted({source_id for row in rows for source_id in row.get("source_ids") or []})
