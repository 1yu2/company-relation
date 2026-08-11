"""企业尽调问答确定性分类与白名单查询计划。"""

from __future__ import annotations

from yuxi.company_due_diligence.evidence.models import EvidenceDocumentType
from yuxi.company_due_diligence.graph.schema import GraphRelationType
from yuxi.company_due_diligence.models import StructuredQueryType
from yuxi.company_due_diligence.qa.models import QueryPlan, QuestionType

_STRUCTURED_RULES: tuple[tuple[tuple[str, ...], StructuredQueryType], ...] = (
    (("注册资本", "实收资本"), StructuredQueryType.REGISTERED_CAPITAL),
    (("法定代表人", "法人"), StructuredQueryType.LEGAL_REPRESENTATIVE),
    (("经营状态", "企业状态"), StructuredQueryType.BUSINESS_STATUS),
    (("历史股东",), StructuredQueryType.HISTORICAL_SHAREHOLDERS),
    (("当前股东", "现有股东", "股东结构"), StructuredQueryType.CURRENT_SHAREHOLDERS),
    (("认缴", "实缴", "出资"), StructuredQueryType.SHAREHOLDER_CONTRIBUTIONS),
    (("产品", "业务线"), StructuredQueryType.PRODUCTS),
    (("融资", "融资历史", "融资历程"), StructuredQueryType.FINANCING_HISTORY),
    (("环保处罚",), StructuredQueryType.ENVIRONMENTAL_PENALTIES),
    (("行政处罚", "处罚数量"), StructuredQueryType.ADMINISTRATIVE_PENALTIES),
    (("经营异常",), StructuredQueryType.ABNORMAL_OPERATIONS),
    (("许可", "行政许可"), StructuredQueryType.LICENSES),
    (("专利",), StructuredQueryType.PATENT_SUMMARY),
    (("商标",), StructuredQueryType.TRADEMARK_SUMMARY),
    (("著作权", "软件著作权"), StructuredQueryType.COPYRIGHT_SUMMARY),
    (("财务", "营收", "利润", "资产负债"), StructuredQueryType.FINANCIAL_METRIC_SERIES),
    (("基本信息", "工商信息", "企业概况"), StructuredQueryType.ENTERPRISE_PROFILE),
)


class QuestionPlanner:
    """通过显式关键词生成有限 SQL、图谱和证据查询计划。"""

    def plan(self, question: str) -> QueryPlan:
        """分类问题并生成不包含任意 SQL/Cypher 的白名单计划。"""
        structured = [
            query_type for keywords, query_type in _STRUCTURED_RULES if any(key in question for key in keywords)
        ]
        structured = list(dict.fromkeys(structured))
        use_graph = any(keyword in question for keyword in ("共同股东", "关联路径", "关联关系", "几跳", "关系网络"))
        evidence_types = _evidence_types(question)

        if not structured and not use_graph and not evidence_types:
            structured = [StructuredQueryType.ENTERPRISE_PROFILE]

        graph_relation_types = _graph_relation_types(question) if use_graph else []
        active_categories = sum((bool(structured), use_graph, bool(evidence_types)))
        if active_categories > 1:
            question_type = QuestionType.MIXED
        elif use_graph:
            question_type = QuestionType.GRAPH_PATH
        elif evidence_types:
            question_type = QuestionType.TEXT_EVIDENCE
        elif any(query_type in _LIST_QUERY_TYPES for query_type in structured):
            question_type = QuestionType.STRUCTURED_LIST
        else:
            question_type = QuestionType.STRUCTURED_FIELD

        return QueryPlan(
            question_type=question_type,
            structured_queries=structured,
            use_graph=use_graph,
            graph_relation_types=graph_relation_types,
            evidence_types=evidence_types,
        )


def _evidence_types(question: str) -> list[EvidenceDocumentType]:
    """只在问题要求正文、原因、摘要或新闻解释时启用文本证据。"""
    asks_for_text = any(
        keyword in question for keyword in ("原因", "依据", "详情", "正文", "摘要", "新闻", "舆情", "解读")
    )
    if not asks_for_text:
        return []
    selected = []
    rules = (
        (("环保",), EvidenceDocumentType.ENVIRONMENTAL_PUNISHMENT),
        (("处罚",), EvidenceDocumentType.ADMINISTRATIVE_PUNISHMENT),
        (("许可",), EvidenceDocumentType.LICENSE),
        (("专利",), EvidenceDocumentType.PATENT),
        (("产品",), EvidenceDocumentType.PRODUCT),
        (("新闻", "舆情", "重大事件", "解读"), EvidenceDocumentType.NEWS),
    )
    for keywords, document_type in rules:
        if any(keyword in question for keyword in keywords):
            selected.append(document_type)
    return selected or list(EvidenceDocumentType)


def _graph_relation_types(question: str) -> list[GraphRelationType]:
    """按问题语义收窄图关系白名单。"""
    if "共同股东" in question:
        return [GraphRelationType.HOLDS]
    if any(keyword in question for keyword in ("投资机构", "融资网络")):
        return [GraphRelationType.RECEIVED_FINANCING, GraphRelationType.INVESTED_IN]
    return list(GraphRelationType)


_LIST_QUERY_TYPES = {
    StructuredQueryType.CURRENT_SHAREHOLDERS,
    StructuredQueryType.HISTORICAL_SHAREHOLDERS,
    StructuredQueryType.SHAREHOLDER_CONTRIBUTIONS,
    StructuredQueryType.PRODUCTS,
    StructuredQueryType.FINANCING_HISTORY,
    StructuredQueryType.ADMINISTRATIVE_PENALTIES,
    StructuredQueryType.ENVIRONMENTAL_PENALTIES,
    StructuredQueryType.ABNORMAL_OPERATIONS,
    StructuredQueryType.LICENSES,
    StructuredQueryType.PATENT_SUMMARY,
    StructuredQueryType.TRADEMARK_SUMMARY,
    StructuredQueryType.COPYRIGHT_SUMMARY,
    StructuredQueryType.FINANCIAL_METRIC_SERIES,
}


question_planner = QuestionPlanner()
