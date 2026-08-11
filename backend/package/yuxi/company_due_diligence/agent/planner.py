"""企业尽调领域智能体的确定性意图理解与展示计划。"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date

from yuxi.company_due_diligence.evidence.models import EvidenceDocumentType
from yuxi.company_due_diligence.graph.schema import GraphRelationType
from yuxi.company_due_diligence.models import StructuredQueryType
from yuxi.company_due_diligence.agent.models import (
    CompanyInvestigationRequest,
    DeliveryMethod,
    InvestigationDepth,
    InvestigationIntent,
    InvestigationPlan,
    InvestigationShortcut,
    InvestigationTopic,
    Presentation,
    PresentationType,
    TimeScope,
    TimeScopeType,
)
from yuxi.company_due_diligence.qa.models import QueryPlan, QuestionType
from yuxi.company_due_diligence.qa.planner import QuestionPlanner, question_planner

_TOPIC_RULES: tuple[tuple[tuple[str, ...], InvestigationTopic], ...] = (
    (("股东", "持股", "出资", "认缴", "实缴"), InvestigationTopic.SHAREHOLDERS),
    (("关系", "关联", "共同股东", "路径", "几跳", "关系网络"), InvestigationTopic.RELATIONS),
    (("产品", "业务线"), InvestigationTopic.PRODUCTS),
    (("融资", "投资机构", "融资历程"), InvestigationTopic.FINANCING),
    (("财务", "营收", "利润", "资产负债"), InvestigationTopic.FINANCIALS),
    (("风险", "处罚", "经营异常", "合规", "许可"), InvestigationTopic.RISKS),
    (("专利", "商标", "著作权", "知识产权"), InvestigationTopic.INTELLECTUAL_PROPERTY),
    (("新闻", "舆情", "重大事件"), InvestigationTopic.NEWS),
    (("基本信息", "工商信息", "注册资本", "法人", "经营状态", "企业概况"), InvestigationTopic.PROFILE),
)

_COMPANY_PATTERN = re.compile(r"([\u4e00-\u9fffA-Za-z0-9（）()·]{2,80}?(?:股份有限公司|有限责任公司|有限公司))")
_RECENT_YEARS_PATTERN = re.compile(r"近\s*([一二三四五六七八九十\d]{1,3})\s*年")
_LEADING_MENTION_WORDS = ("请帮我", "帮我", "请分析", "请调查", "调查", "分析", "查询", "比较", "关于", "与", "和")
_CHINESE_NUMBER_VALUES = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}

_SHAREHOLDER_QUERIES = {
    StructuredQueryType.CURRENT_SHAREHOLDERS,
    StructuredQueryType.HISTORICAL_SHAREHOLDERS,
    StructuredQueryType.SHAREHOLDER_CONTRIBUTIONS,
}
_RISK_QUERIES = (
    StructuredQueryType.ADMINISTRATIVE_PENALTIES,
    StructuredQueryType.ENVIRONMENTAL_PENALTIES,
    StructuredQueryType.ABNORMAL_OPERATIONS,
    StructuredQueryType.LICENSES,
)
_INTELLECTUAL_PROPERTY_QUERIES = (
    StructuredQueryType.PATENT_SUMMARY,
    StructuredQueryType.TRADEMARK_SUMMARY,
    StructuredQueryType.COPYRIGHT_SUMMARY,
)
_LIST_QUERIES = {
    *_SHAREHOLDER_QUERIES,
    *_RISK_QUERIES,
    *_INTELLECTUAL_PROPERTY_QUERIES,
    StructuredQueryType.PRODUCTS,
    StructuredQueryType.FINANCING_HISTORY,
    StructuredQueryType.FINANCIAL_METRIC_SERIES,
}


class InvestigationPlanner:
    """把用户调查要求转换为业务意图和现有白名单查询计划。"""

    def __init__(
        self,
        question_planner_service: QuestionPlanner | None = None,
        today_provider: Callable[[], date] = date.today,
    ) -> None:
        self._question_planner = question_planner_service or question_planner
        self._today_provider = today_provider

    def plan(self, request: CompanyInvestigationRequest) -> InvestigationPlan:
        """使用规则优先生成可校验的意图、查询和展示计划。"""
        message = effective_message(request)
        topics = self._topics(message, request.shortcut)
        presentation, delivery = self._presentation(message, topics, request.shortcut)
        intent = InvestigationIntent(
            topics=topics,
            presentation=presentation,
            delivery=delivery,
            time_scope=self._time_scope(message, request),
            depth=self._depth(message, presentation),
            evidence_required=self._evidence_required(message, topics),
        )
        query_plan = None if delivery == DeliveryMethod.ASYNC_TASK else self._query_plan(message, intent)
        return InvestigationPlan(intent=intent, query_plan=query_plan)

    def _query_plan(self, message: str, intent: InvestigationIntent) -> QueryPlan:
        """按统一业务主题补全现有确定性白名单查询计划。"""
        legacy_plan = self._question_planner.plan(message)
        structured_queries = list(legacy_plan.structured_queries)
        if structured_queries == [StructuredQueryType.ENTERPRISE_PROFILE] and intent.topics != [
            InvestigationTopic.PROFILE
        ]:
            structured_queries = []

        if InvestigationTopic.PROFILE in intent.topics:
            _append_missing(structured_queries, StructuredQueryType.ENTERPRISE_PROFILE)
        if InvestigationTopic.SHAREHOLDERS in intent.topics and not _SHAREHOLDER_QUERIES.intersection(
            structured_queries
        ):
            _append_missing(structured_queries, StructuredQueryType.CURRENT_SHAREHOLDERS)
        if InvestigationTopic.PRODUCTS in intent.topics:
            _append_missing(structured_queries, StructuredQueryType.PRODUCTS)
        if InvestigationTopic.FINANCING in intent.topics:
            _append_missing(structured_queries, StructuredQueryType.FINANCING_HISTORY)
        if InvestigationTopic.FINANCIALS in intent.topics:
            _append_missing(structured_queries, StructuredQueryType.FINANCIAL_METRIC_SERIES)
        if InvestigationTopic.RISKS in intent.topics and not set(_RISK_QUERIES).intersection(structured_queries):
            structured_queries.extend(_RISK_QUERIES)
        if InvestigationTopic.INTELLECTUAL_PROPERTY in intent.topics and not set(
            _INTELLECTUAL_PROPERTY_QUERIES
        ).intersection(structured_queries):
            structured_queries.extend(_INTELLECTUAL_PROPERTY_QUERIES)

        use_graph = legacy_plan.use_graph or InvestigationTopic.RELATIONS in intent.topics
        graph_relation_types = legacy_plan.graph_relation_types or (list(GraphRelationType) if use_graph else [])
        evidence_types = list(legacy_plan.evidence_types)
        if InvestigationTopic.RISKS in intent.topics and intent.evidence_required:
            for document_type in (
                EvidenceDocumentType.ADMINISTRATIVE_PUNISHMENT,
                EvidenceDocumentType.ENVIRONMENTAL_PUNISHMENT,
                EvidenceDocumentType.LICENSE,
            ):
                _append_missing(evidence_types, document_type)
        if InvestigationTopic.NEWS in intent.topics:
            _append_missing(evidence_types, EvidenceDocumentType.NEWS)

        return QueryPlan(
            question_type=_question_type(structured_queries, use_graph, evidence_types),
            structured_queries=structured_queries,
            use_graph=use_graph,
            graph_relation_types=graph_relation_types,
            evidence_types=evidence_types,
        )

    def _topics(
        self,
        message: str,
        shortcut: InvestigationShortcut | None,
    ) -> list[InvestigationTopic]:
        """识别业务主题；完整报告快捷入口覆盖普通关键词。"""
        if shortcut == InvestigationShortcut.GENERATE_FULL_REPORT or any(
            keyword in message for keyword in ("完整尽调", "完整报告", "全面尽调")
        ):
            return [InvestigationTopic.FULL_DUE_DILIGENCE]
        topics = [topic for keywords, topic in _TOPIC_RULES if any(keyword in message for keyword in keywords)]
        return list(dict.fromkeys(topics)) or [InvestigationTopic.PROFILE]

    def _presentation(
        self,
        message: str,
        topics: list[InvestigationTopic],
        shortcut: InvestigationShortcut | None,
    ) -> tuple[PresentationType, DeliveryMethod]:
        """根据用户要求选择同步展示或异步报告。"""
        if topics == [InvestigationTopic.FULL_DUE_DILIGENCE] or shortcut == InvestigationShortcut.GENERATE_FULL_REPORT:
            return PresentationType.FULL_REPORT, DeliveryMethod.ASYNC_TASK
        if any(keyword in message for keyword in ("报告形式", "生成报告", "专题报告", "报告")):
            return PresentationType.TOPIC_REPORT, DeliveryMethod.ASYNC_TASK
        if InvestigationTopic.RELATIONS in topics:
            return PresentationType.RELATION_RESULT, DeliveryMethod.SYNC
        if InvestigationTopic.RISKS in topics:
            return PresentationType.RISK_SUMMARY, DeliveryMethod.SYNC
        if any(
            topic
            in {
                InvestigationTopic.SHAREHOLDERS,
                InvestigationTopic.PRODUCTS,
                InvestigationTopic.FINANCING,
                InvestigationTopic.INTELLECTUAL_PROPERTY,
                InvestigationTopic.NEWS,
            }
            for topic in topics
        ):
            return PresentationType.SUMMARY_WITH_LIST, DeliveryMethod.SYNC
        return PresentationType.DIRECT_ANSWER, DeliveryMethod.SYNC

    def _time_scope(self, message: str, request: CompanyInvestigationRequest) -> TimeScope:
        """解析近若干年或显式日期范围。"""
        if match := _RECENT_YEARS_PATTERN.search(message):
            years = _parse_years(match.group(1))
            end_date = request.as_of or self._today_provider()
            return TimeScope(
                type=TimeScopeType.RECENT_YEARS,
                years=years,
                start_date=_subtract_years(end_date, years),
                end_date=end_date,
            )
        if request.start_date is not None:
            return TimeScope(
                type=TimeScopeType.DATE_RANGE,
                start_date=request.start_date,
                end_date=request.as_of or self._today_provider(),
            )
        if request.as_of is not None:
            return TimeScope(type=TimeScopeType.AS_OF, end_date=request.as_of)
        if any(keyword in message for keyword in ("当前", "现在", "目前")):
            return TimeScope(type=TimeScopeType.CURRENT, end_date=self._today_provider())
        return TimeScope(type=TimeScopeType.LATEST_AVAILABLE)

    @staticmethod
    def _depth(message: str, presentation: PresentationType) -> InvestigationDepth:
        """根据明确用词和报告形式确定调查深度。"""
        if presentation in {PresentationType.TOPIC_REPORT, PresentationType.FULL_REPORT} or any(
            keyword in message for keyword in ("详细", "深入", "全面")
        ):
            return InvestigationDepth.DETAILED
        if any(keyword in message for keyword in ("简要", "简单", "概括")):
            return InvestigationDepth.CONCISE
        return InvestigationDepth.STANDARD

    @staticmethod
    def _evidence_required(message: str, topics: list[InvestigationTopic]) -> bool:
        """判断最终结论是否必须补充文本证据。"""
        evidence_topics = {
            InvestigationTopic.RISKS,
            InvestigationTopic.NEWS,
            InvestigationTopic.FULL_DUE_DILIGENCE,
        }
        return any(topic in evidence_topics for topic in topics) or any(
            keyword in message for keyword in ("原因", "依据", "详情", "正文", "摘要", "证据", "解读")
        )


def effective_message(request: CompanyInvestigationRequest) -> str:
    """为报告快捷入口生成稳定的内部调查文本。"""
    if request.message:
        return request.message
    if request.shortcut == InvestigationShortcut.GENERATE_FULL_REPORT:
        return "生成完整尽调报告"
    return "企业基本信息"


def extract_company_mentions(message: str) -> list[str]:
    """从明确法律全称中提取企业提及，简称仍由候选确认处理。"""
    mentions = []
    for match in _COMPANY_PATTERN.finditer(message):
        mention = match.group(1).strip("，。；：、,.;: ")
        changed = True
        while changed:
            changed = False
            for prefix in _LEADING_MENTION_WORDS:
                if mention.startswith(prefix) and len(mention) > len(prefix):
                    mention = mention[len(prefix) :].strip()
                    changed = True
        if mention and mention not in mentions:
            mentions.append(mention)
    return mentions


def presentation_for(intent: InvestigationIntent) -> Presentation:
    """返回与意图一致的前端组件组合。"""
    if intent.presentation == PresentationType.TOPIC_REPORT:
        return Presentation(
            type=intent.presentation,
            components=["report_progress", *(topic.value for topic in intent.topics), "sources"],
        )
    components = {
        PresentationType.DIRECT_ANSWER: ["summary", "facts", "sources", "data_quality"],
        PresentationType.SUMMARY_WITH_LIST: ["summary", "findings", "fact_table", "sources"],
        PresentationType.RELATION_RESULT: ["summary", "paths", "relations", "sources"],
        PresentationType.RISK_SUMMARY: ["summary", "findings", "fact_table", "evidence", "sources"],
        PresentationType.FULL_REPORT: ["report_progress", "full_report_sections", "sources"],
        PresentationType.CLARIFICATION: ["clarification", "company_candidates"],
    }[intent.presentation]
    return Presentation(type=intent.presentation, components=components)


def _parse_years(value: str) -> int:
    """把阿拉伯数字或一到十的中文数字转换为年数。"""
    if value.isdigit():
        return max(1, min(int(value), 20))
    if value in _CHINESE_NUMBER_VALUES:
        return _CHINESE_NUMBER_VALUES[value]
    if value.startswith("十"):
        return 10 + _CHINESE_NUMBER_VALUES.get(value[1:], 0)
    if value.endswith("十"):
        return _CHINESE_NUMBER_VALUES.get(value[0], 1) * 10
    return 3


def _subtract_years(value: date, years: int) -> date:
    """按日历年回退日期，并处理闰日。"""
    try:
        return value.replace(year=value.year - years)
    except ValueError:
        return value.replace(year=value.year - years, day=28)


def _append_missing(values: list, value) -> None:
    """按原顺序追加尚未存在的白名单值。"""
    if value not in values:
        values.append(value)


def _question_type(
    structured_queries: list[StructuredQueryType],
    use_graph: bool,
    evidence_types: list[EvidenceDocumentType],
) -> QuestionType:
    """根据实际启用的工具类别确定兼容问答类型。"""
    active_categories = sum((bool(structured_queries), use_graph, bool(evidence_types)))
    if active_categories > 1:
        return QuestionType.MIXED
    if use_graph:
        return QuestionType.GRAPH_PATH
    if evidence_types:
        return QuestionType.TEXT_EVIDENCE
    if any(query_type in _LIST_QUERIES for query_type in structured_queries):
        return QuestionType.STRUCTURED_LIST
    return QuestionType.STRUCTURED_FIELD


investigation_planner = InvestigationPlanner()
