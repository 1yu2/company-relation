from __future__ import annotations

import json
from itertools import combinations
from pathlib import Path
from typing import Any

import networkx as nx

from yuxi.company_relation.query_service import CompanyRelationQueryService
from yuxi.company_relation.snapshot import CompanyRelationSnapshot


QUESTION_TARGETS = {
    "attribute": 30,
    "single_hop": 30,
    "multi_hop": 40,
    "refusal": 20,
}


async def generate_golden_questions(
    snapshot: CompanyRelationSnapshot,
    *,
    targets: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """从冻结来源快照生成分层、可复现的严格 Golden 题库。"""
    requested = targets or QUESTION_TARGETS
    service = CompanyRelationQueryService(snapshot=snapshot)
    questions = []
    questions.extend(await _build_attribute_questions(service, requested.get("attribute", 0)))
    questions.extend(await _build_single_hop_questions(service, requested.get("single_hop", 0)))
    questions.extend(await _build_multi_hop_questions(service, requested.get("multi_hop", 0)))
    questions.extend(await _build_refusal_questions(service, requested.get("refusal", 0)))

    expected_count = sum(requested.values())
    if len(questions) != expected_count:
        raise ValueError(f"Golden 题库生成数量不足: {len(questions)}/{expected_count}")
    return questions


def write_golden_questions(questions: list[dict[str, Any]], path: str | Path) -> None:
    """将 Golden 题库按稳定字段顺序写为 JSONL。"""
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as file:
        for question in questions:
            file.write(json.dumps(question, ensure_ascii=False, separators=(",", ":")) + "\n")


async def _build_attribute_questions(service: CompanyRelationQueryService, count: int) -> list[dict[str, Any]]:
    templates = {
        "industry": ("{name}的行业是什么？", "请告诉我{name}所属行业。"),
        "legal_representative": ("{name}的法定代表人是谁？", "{name}法人是谁？"),
        "listing_date": ("{name}的上市日期是什么？", "{name}什么时候上市？"),
        "registered_address": ("{name}的注册地址在哪里？", "{name}注册地是什么？"),
    }
    candidates = []
    for company in service.snapshot.list_nodes("Company"):
        if not company.properties.get("stock_code"):
            continue
        for attribute, attribute_templates in templates.items():
            if company.properties.get(attribute) in (None, ""):
                continue
            for template in attribute_templates:
                candidates.append(template.format(name=company.name))
    return await _materialize_questions(service, "attribute", candidates, count)


async def _build_single_hop_questions(service: CompanyRelationQueryService, count: int) -> list[dict[str, Any]]:
    listed_companies = [
        company for company in service.snapshot.list_nodes("Company") if company.properties.get("stock_code")
    ]
    section_count = count // 3
    executive_person_ids = {
        edge.source_node_id for edge in service.snapshot.edges if edge.edge_type == "EXECUTIVE_OF"
    }
    executives = [
        person for person in service.snapshot.list_nodes("Person") if person.node_id in executive_person_ids
    ]
    candidates = [f"{company.name}的股东有哪些？" for company in listed_companies[:section_count]]
    candidates.extend(f"{company.name}发布了哪些公告？" for company in listed_companies[:section_count])
    candidates.extend(
        f"{person.name}任职哪些公司？" for person in executives[: count - len(candidates)]
    )
    return await _materialize_questions(service, "single_hop", candidates, count)


async def _build_multi_hop_questions(service: CompanyRelationQueryService, count: int) -> list[dict[str, Any]]:
    companies = [
        company for company in service.snapshot.list_nodes("Company") if company.properties.get("stock_code")
    ]
    candidates = []
    for first, second in combinations(companies, 2):
        if not _has_relation_within_depth(service.raw_relation_graph, first.node_id, second.node_id, 3):
            continue
        candidates.extend(
            (
                f"{first.name}和{second.name}有什么关联？",
                f"请给出{first.name}与{second.name}的关联路径。",
            )
        )
    return await _materialize_questions(service, "multi_hop", candidates, count)


async def _build_refusal_questions(service: CompanyRelationQueryService, count: int) -> list[dict[str, Any]]:
    candidates = []
    for index in range(count):
        if index % 2 == 0:
            candidates.append(f"不存在企业{index:02d}的股东有哪些？")
        else:
            candidates.append(f"不存在企业{index:02d}和不存在企业{index + 1:02d}有什么关联？")
    return await _materialize_questions(service, "refusal", candidates, count, require_answer=False)


async def _materialize_questions(
    service: CompanyRelationQueryService,
    category: str,
    candidates: list[str],
    count: int,
    *,
    require_answer: bool = True,
) -> list[dict[str, Any]]:
    questions = []
    for query in candidates:
        output = await service.query(query, strategy="graph_rag")
        if require_answer and output.refused:
            continue
        if not require_answer and not output.refused:
            continue
        question_index = len(questions) + 1
        questions.append(
            {
                "question_id": f"{category}-{question_index:03d}",
                "category": category,
                "query": query,
                "review_status": "source_derived_unreviewed",
                "expected": {
                    "refused": output.refused,
                    "answer_entity_ids": output.answer_entity_ids,
                    "fact_ids": output.fact_ids,
                    "paths": output.paths,
                    "evidence_ids": output.fact_ids,
                },
            }
        )
        if len(questions) == count:
            return questions
    if len(questions) < count:
        raise ValueError(f"{category} 题目不足: {len(questions)}/{count}")
    return questions


def _has_relation_within_depth(graph: nx.MultiGraph, source_id: str, target_id: str, max_depth: int) -> bool:
    try:
        return nx.shortest_path_length(graph, source_id, target_id) <= max_depth
    except nx.NetworkXNoPath:
        return False
