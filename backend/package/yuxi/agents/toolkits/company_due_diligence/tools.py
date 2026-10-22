"""企业外部公开数据补数工具，只作为内部事实的补充。"""

from datetime import date

from langchain_core.tools import ToolException
from pydantic import BaseModel, Field

from yuxi.agents.toolkits.registry import tool
from yuxi.company_due_diligence.external.config import (
    config_value,
    enabled_external_providers,
    load_external_data_config,
)
from yuxi.company_due_diligence.external.models import ExternalCompanyDataRequest
from yuxi.company_due_diligence.external.service import external_company_data_service


class QueryExternalCompanyDataInput(BaseModel):
    """描述主智能体显式查询外部企业数据所需参数。"""

    question: str = Field(
        min_length=1,
        max_length=2000,
        description="用户关于企业外部数据补充的原始要求，用于主题推断",
    )
    company_name: str | None = Field(
        default=None,
        max_length=256,
        description="当前已确认企业的标准全称",
    )
    confirmed_eid: str | None = Field(
        default=None,
        max_length=64,
        description="仅复用上一次工具结果返回的 company_context.eid，不得根据模型猜测或自行生成",
    )
    credit_code: str | None = Field(default=None, max_length=32, description="已确认企业的统一社会信用代码")
    stock_code: str | None = Field(default=None, max_length=32, description="已确认企业的股票代码")
    topics: list[str] = Field(
        min_length=1,
        description="要查询的外部主题，如 profile、financials、financing、shareholders、news",
    )
    providers: list[str] | None = Field(
        default=None,
        description="指定 Provider，如 akshare；为空时使用所有已启用 Provider",
    )
    start_date: date | None = Field(default=None, description="开始日期")
    end_date: date | None = Field(default=None, description="结束日期")


@tool(
    category="company_due_diligence",
    tags=["企业尽调", "外部数据"],
    display_name="企业外部数据补数",
    args_schema=QueryExternalCompanyDataInput,
)
async def query_external_company_data(
    question: str,
    company_name: str | None = None,
    confirmed_eid: str | None = None,
    credit_code: str | None = None,
    stock_code: str | None = None,
    topics: list[str] | None = None,
    providers: list[str] | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict:
    """查询外部公开企业数据，仅作为内部事实的补充。

    必须先确认企业身份（confirmed_eid）。只能补充内部数据缺失、过期或冲突的主题，
    不能用外部名称覆盖已确认 eid，也不能把“未发现”表述为“没有”。
    """
    company_context = _company_context(company_name, confirmed_eid, credit_code=credit_code, stock_code=stock_code)
    if company_context is None or not company_context.get("eid"):
        raise ToolException("必须先确认企业身份（confirmed_eid）才能查询外部数据。")
    config = await load_external_data_config()
    enabled_provider_names = enabled_external_providers(config)
    requested_providers = providers
    if requested_providers is not None:
        disabled_providers = set(requested_providers) - set(enabled_provider_names)
        if disabled_providers:
            raise ToolException(f"Provider 未启用：{', '.join(sorted(disabled_providers))}")
    response = await external_company_data_service.query(
        ExternalCompanyDataRequest(
            company_ref=company_context,
            topics=topics or [],
            providers=providers,
            start_date=start_date,
            end_date=end_date,
        ),
        enabled_providers=enabled_provider_names if providers is None else None,
        timeout_seconds=config_value(config, "default_timeout_seconds", 30),
        cache_ttl_seconds=config_value(config, "cache_ttl_seconds", 300),
        rate_limit_per_minute=config_value(config, "rate_limit_per_minute", 60),
    )
    return response.model_dump(mode="json")


def _company_context(
    name: str | None,
    eid: str | None,
    *,
    credit_code: str | None = None,
    stock_code: str | None = None,
) -> dict | None:
    """仅在存在已确认企业线索时构造外部数据查询的企业上下文。"""
    if name is None and eid is None and credit_code is None and stock_code is None:
        return None
    return {
        "name": name,
        "eid": eid,
        "credit_code": credit_code,
        "stock_code": stock_code,
    }
