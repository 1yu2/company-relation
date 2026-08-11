"""企业尽调问题分类和白名单计划单元测试。"""

import pytest
from pydantic import ValidationError

from yuxi.company_due_diligence.evidence.models import EvidenceDocumentType
from yuxi.company_due_diligence.graph.schema import GraphRelationType
from yuxi.company_due_diligence.models import CompanyReference, StructuredQueryType
from yuxi.company_due_diligence.qa.models import CompanyQuestionRequest, QuestionType
from yuxi.company_due_diligence.qa.planner import QuestionPlanner


@pytest.mark.parametrize(
    ("question", "expected_type", "expected_queries"),
    [
        (
            "注册资本和法人是谁？",
            QuestionType.STRUCTURED_FIELD,
            {StructuredQueryType.REGISTERED_CAPITAL, StructuredQueryType.LEGAL_REPRESENTATIVE},
        ),
        (
            "当前股东及认缴实缴情况",
            QuestionType.STRUCTURED_LIST,
            {StructuredQueryType.CURRENT_SHAREHOLDERS, StructuredQueryType.SHAREHOLDER_CONTRIBUTIONS},
        ),
    ],
)
def test_planner_routes_structured_questions_without_graph_or_evidence(question, expected_type, expected_queries):
    """简单结构化问题只生成 MySQL 白名单查询。"""
    plan = QuestionPlanner().plan(question)

    assert plan.question_type == expected_type
    assert set(plan.structured_queries) == expected_queries
    assert plan.use_graph is False
    assert plan.evidence_types == []


def test_planner_routes_common_shareholder_to_high_confidence_graph():
    """共同股东问题只使用 HOLDS 图关系。"""
    plan = QuestionPlanner().plan("两家公司是否存在共同股东关联路径？")

    assert plan.question_type == QuestionType.GRAPH_PATH
    assert plan.use_graph is True
    assert plan.graph_relation_types == [GraphRelationType.HOLDS]


def test_planner_routes_punishment_reason_to_mixed_query():
    """处罚原因同时需要结构化记录和处罚文本证据。"""
    plan = QuestionPlanner().plan("这家公司的行政处罚原因和依据是什么？")

    assert plan.question_type == QuestionType.MIXED
    assert plan.structured_queries == [StructuredQueryType.ADMINISTRATIVE_PENALTIES]
    assert plan.evidence_types == [EvidenceDocumentType.ADMINISTRATIVE_PUNISHMENT]


@pytest.mark.parametrize(
    "payload",
    [
        {"company_ref": {"eid": "eid-1"}, "question": "问题", "hop_limit": 4},
        {"company_ref": {"eid": "eid-1"}, "question": "问题", "top_k": 51},
    ],
)
def test_question_request_rejects_unbounded_parameters(payload):
    """最多跳数和召回数量不得突破冻结上限。"""
    with pytest.raises(ValidationError):
        CompanyQuestionRequest.model_validate(payload)


def test_question_request_normalizes_text():
    """问题两端空白应在规划前去除。"""
    request = CompanyQuestionRequest(company_ref=CompanyReference(eid="eid-1"), question="  注册资本？  ")

    assert request.question == "注册资本？"
