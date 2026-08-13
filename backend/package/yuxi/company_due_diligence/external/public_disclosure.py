"""公开披露 Provider 占位。"""

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


class PublicDisclosureProvider(ExternalCompanyDataProvider):
    """政府、交易所、法院公开披露 Provider（首期未配置）。"""

    @property
    def name(self) -> str:
        return "public_disclosure"

    @property
    def supported_topics(self) -> list[str]:
        return ["profile", "compliance", "news", "financing"]

    async def health(self) -> ProviderHealth:
        return ProviderHealth(
            provider_name=self.name,
            status=ProviderStatus.NOT_CONFIGURED,
            supported_topics=self.supported_topics,
            message="公开披露 Provider 尚未配置数据源",
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
            error_message="公开披露 Provider 尚未配置数据源",
        )
