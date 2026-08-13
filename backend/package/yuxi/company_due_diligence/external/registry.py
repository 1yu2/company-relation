"""Provider 注册表。"""

from __future__ import annotations

from yuxi.company_due_diligence.external.errors import ProviderConfigError
from yuxi.company_due_diligence.external.models import ProviderHealth
from yuxi.company_due_diligence.external.provider import ExternalCompanyDataProvider


class ExternalProviderRegistry:
    """按名称管理 Provider 实例，支持动态注册和按配置启停。"""

    def __init__(self) -> None:
        self._providers: dict[str, ExternalCompanyDataProvider] = {}

    def register(self, provider: ExternalCompanyDataProvider) -> None:
        """注册 Provider；同名覆盖。"""
        self._providers[provider.name] = provider

    def get(self, name: str) -> ExternalCompanyDataProvider:
        """按名称获取 Provider，不存在时抛出标准错误。"""
        provider = self._providers.get(name)
        if provider is None:
            raise ProviderConfigError(f"未找到 Provider: {name}")
        return provider

    def list_providers(self) -> list[ExternalCompanyDataProvider]:
        """返回所有已注册 Provider。"""
        return list(self._providers.values())

    async def health(self) -> list[ProviderHealth]:
        """返回所有 Provider 健康状态。"""
        results = []
        for provider in self._providers.values():
            try:
                results.append(await provider.health())
            except Exception as exc:
                results.append(
                    ProviderHealth(
                        provider_name=provider.name,
                        status="error",
                        supported_topics=provider.supported_topics,
                        message=str(exc),
                    )
                )
        return results


external_provider_registry = ExternalProviderRegistry()
