"""企业尽调性能场景请求构造与阈值。"""

from yuxi.company_due_diligence.models import CompanyReference
from yuxi.company_due_diligence.performance.models import PerformanceScenario
from yuxi.company_due_diligence.qa.models import CompanyQuestionRequest
from yuxi.company_due_diligence.report.models import DueDiligenceReportRequest

SCENARIO_TARGET_P95_MS = {
    PerformanceScenario.ENTERPRISE_PROFILE: 500.0,
    PerformanceScenario.SHAREHOLDER_LIST: 1000.0,
    PerformanceScenario.FINANCING_AND_RISK: 1000.0,
    PerformanceScenario.GRAPH_PATH_3_HOPS: 3000.0,
    PerformanceScenario.REPORT_AGGREGATE_WITHOUT_LLM: 5000.0,
    PerformanceScenario.REPORT_WITH_LLM: 30000.0,
}

_SCENARIO_QUESTIONS = {
    PerformanceScenario.ENTERPRISE_PROFILE: "企业基本信息、注册资本、法定代表人和经营状态是什么？",
    PerformanceScenario.SHAREHOLDER_LIST: "当前股东和出资情况有哪些？",
    PerformanceScenario.FINANCING_AND_RISK: "融资、行政处罚、环保处罚和许可证情况有哪些？",
    PerformanceScenario.GRAPH_PATH_3_HOPS: "该企业有哪些最多三跳的关联路径？",
}


def build_question_request(
    scenario: PerformanceScenario,
    company_ref: CompanyReference,
    target_company_ref: CompanyReference | None,
) -> CompanyQuestionRequest:
    """为四类问答场景生成受控请求。"""
    return CompanyQuestionRequest(
        company_ref=company_ref,
        target_company_ref=target_company_ref if scenario == PerformanceScenario.GRAPH_PATH_3_HOPS else None,
        question=_SCENARIO_QUESTIONS[scenario],
        hop_limit=3,
    )


def build_report_request(company_ref: CompanyReference) -> DueDiligenceReportRequest:
    """生成不含在线 LLM 的完整十二章节报告请求。"""
    return DueDiligenceReportRequest(company_ref=company_ref)
