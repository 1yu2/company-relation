"""把企业尽调领域智能体暴露为系统主智能体工具。"""

from pydantic import BaseModel, Field

from yuxi.agents.toolkits.registry import tool
from yuxi.company_due_diligence.agent.models import CompanyInvestigationRequest
from yuxi.company_due_diligence.agent.service import company_due_diligence_agent
from yuxi.services.task_service import tasker


class CompanyInvestigationToolInput(BaseModel):
    """描述主智能体调用企业尽调领域服务所需的最小参数。"""

    message: str = Field(
        min_length=1,
        max_length=2000,
        description="用户关于企业股东、关系、风险、经营、融资、知识产权、新闻或尽调报告的原始要求",
    )
    company_name: str | None = Field(
        default=None,
        max_length=256,
        description="当前已确认企业的标准全称；若用户问题已包含完整企业名称可以省略",
    )
    confirmed_eid: str | None = Field(
        default=None,
        max_length=64,
        description="仅复用上一次工具结果返回的 company_context.eid，不得根据模型猜测或自行生成",
    )
    target_company_name: str | None = Field(
        default=None,
        max_length=256,
        description="企业关系问题中已确认的目标企业标准全称",
    )
    target_confirmed_eid: str | None = Field(
        default=None,
        max_length=64,
        description="仅复用上一次工具结果返回的 target_company_context.eid，不得根据模型猜测或自行生成",
    )


@tool(
    category="company_due_diligence",
    tags=["企业尽调", "知识图谱"],
    display_name="企业尽调智能体",
    args_schema=CompanyInvestigationToolInput,
)
async def investigate_company(
    message: str,
    company_name: str | None = None,
    confirmed_eid: str | None = None,
    target_company_name: str | None = None,
    target_confirmed_eid: str | None = None,
) -> dict:
    """调查企业事实、关系和风险，或按自然语言要求创建专题/完整尽调报告。

    对企业问题必须优先调用本工具，不要直接推测企业身份、股东、关系或风险。
    首次调用可传企业标准全称；后续调用应原样复用工具返回的 company_context，
    其中 eid 只能来自工具结果。若返回 clarification，应向用户展示候选或请求补充标准全称。
    """
    company_context = _company_context(company_name, confirmed_eid)
    target_company_context = _company_context(target_company_name, target_confirmed_eid)
    response = await company_due_diligence_agent.investigate(
        CompanyInvestigationRequest(
            company_context=company_context,
            target_company_context=target_company_context,
            message=message,
        ),
        tasker=tasker,
    )
    return response.model_dump(mode="json")


def _company_context(name: str | None, eid: str | None) -> dict | None:
    """仅在存在已确认企业线索时构造领域智能体上下文。"""
    if name is None and eid is None:
        return None
    return {"name": name, "eid": eid}
