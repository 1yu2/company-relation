"""外部数据 Provider 标准错误分类。"""

from __future__ import annotations

from yuxi.company_due_diligence.external.models import ProviderErrorCode


class ExternalProviderError(RuntimeError):
    """Provider 执行过程中的标准错误。"""

    def __init__(self, message: str, *, code: ProviderErrorCode = ProviderErrorCode.UPSTREAM_ERROR) -> None:
        super().__init__(message)
        self.code = code


class ProviderTimeoutError(ExternalProviderError):
    """上游接口超时。"""

    def __init__(self, message: str = "上游接口超时") -> None:
        super().__init__(message, code=ProviderErrorCode.TIMEOUT)


class ProviderRateLimitError(ExternalProviderError):
    """触发上游限流。"""

    def __init__(self, message: str = "触发上游限流") -> None:
        super().__init__(message, code=ProviderErrorCode.RATE_LIMIT)


class ProviderAuthError(ExternalProviderError):
    """认证或授权失败。"""

    def __init__(self, message: str = "认证失败") -> None:
        super().__init__(message, code=ProviderErrorCode.AUTH_FAILED)


class ProviderFormatError(ExternalProviderError):
    """上游返回格式无法解析或缺少关键字段。"""

    def __init__(self, message: str = "上游返回格式异常") -> None:
        super().__init__(message, code=ProviderErrorCode.FORMAT_ERROR)


class ProviderUpstreamError(ExternalProviderError):
    """上游服务返回非 2xx 或其他不可恢复错误。"""

    def __init__(self, message: str = "上游服务错误") -> None:
        super().__init__(message, code=ProviderErrorCode.UPSTREAM_ERROR)


class ProviderConfigError(ExternalProviderError):
    """Provider 未配置或配置不完整。"""

    def __init__(self, message: str = "Provider 未配置") -> None:
        super().__init__(message, code=ProviderErrorCode.CONFIG_MISSING)
