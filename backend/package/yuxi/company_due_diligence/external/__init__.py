"""企业尽调外部数据 Provider 适配层与补数编排。"""

from __future__ import annotations

from yuxi.company_due_diligence.external.akshare import AkShareProvider
from yuxi.company_due_diligence.external.errors import (
    ExternalProviderError,
    ProviderAuthError,
    ProviderConfigError,
    ProviderFormatError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUpstreamError,
)
from yuxi.company_due_diligence.external.financing import FinancingProvider
from yuxi.company_due_diligence.external.models import (
    ExternalCompanyDataRequest,
    ExternalCompanyDataResponse,
    ProviderErrorCode,
    ProviderResult,
    ProviderStatus,
    TopicCoverage,
)
from yuxi.company_due_diligence.external.provider import ExternalCompanyDataProvider
from yuxi.company_due_diligence.external.public_disclosure import PublicDisclosureProvider
from yuxi.company_due_diligence.external.registry import ExternalProviderRegistry, external_provider_registry
from yuxi.company_due_diligence.external.service import ExternalCompanyDataService, external_company_data_service
from yuxi.company_due_diligence.models import CoverageStatus

# 注册内置 Provider
external_company_data_service._registry.register(AkShareProvider())
external_company_data_service._registry.register(PublicDisclosureProvider())
external_company_data_service._registry.register(FinancingProvider())

__all__ = [
    "CoverageStatus",
    "ExternalCompanyDataProvider",
    "ExternalCompanyDataRequest",
    "ExternalCompanyDataResponse",
    "ExternalCompanyDataService",
    "ExternalProviderError",
    "ExternalProviderRegistry",
    "ProviderAuthError",
    "ProviderConfigError",
    "ProviderErrorCode",
    "ProviderFormatError",
    "ProviderRateLimitError",
    "ProviderResult",
    "ProviderStatus",
    "ProviderTimeoutError",
    "ProviderUpstreamError",
    "TopicCoverage",
    "external_company_data_service",
    "external_provider_registry",
]
