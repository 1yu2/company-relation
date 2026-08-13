"""融资信息 Provider 占位。"""

from __future__ import annotations

from yuxi.company_due_diligence.external.models import (
    CoverageStatus,
    ExternalCompanyDataRequest,
    ProviderErrorCode,
    ProviderHealth,
    ProviderResult,
    ProviderStatus,
)
from yuxi.company_due_diligence.external.provider import ExternalCompanyDataProvider


class FinancingProvider(ExternalCompanyDataProvider):
    """融资历史外部 Provider（首期未配置，为商业 API 预留）。"""

    @property
    def name(self) -> str:
        return "financing"

    @property
    def supported_topics(self) -> list[str]:
        return ["financing"]

    async def health(self) -> ProviderHealth:
        return ProviderHealth(
            provider_name=self.name,
            status=ProviderStatus.NOT_CONFIGURED,
            supported_topics=self.supported_topics,
            message="融资 Provider 尚未配置数据源",
        )

    async def query(self, request: ExternalCompanyDataRequest) -> ProviderResult:
        return ProviderResult(
            provider_name=self.name,
            facts=[],
            sources=[],
            evidence=[],
            coverage={topic: CoverageStatus.UNSUPPORTED for topic in request.topics},
            missing_topics=list(request.topics),
            status=ProviderStatus.NOT_CONFIGURED,
            error_code=ProviderErrorCode.CONFIG_MISSING,
            error_message="融资 Provider 尚未配置数据源；后续可接入企业公告、商业数据 API 或授权来源",
        )
