"""外部数据 Provider 抽象接口。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

from yuxi.company_due_diligence.external.models import (
    ExternalCompanyDataRequest,
    ProviderHealth,
    ProviderResult,
)


class ExternalCompanyDataProvider(ABC):
    """外部企业数据 Provider 抽象基类。

    所有原始字段映射、错误分类和来源标准化必须在子类内部完成，
    不得将上游原始结构泄漏到业务编排层。
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider 唯一标识名。"""

    @property
    @abstractmethod
    def supported_topics(self) -> list[str]:
        """声明支持的主题列表，如 ['profile', 'financials', 'shareholders', 'news']。"""

    @abstractmethod
    async def health(self) -> ProviderHealth:
        """返回 Provider 健康状态，不触发实际业务查询。"""

    @abstractmethod
    async def query(self, request: ExternalCompanyDataRequest) -> ProviderResult:
        """执行外部查询并返回标准 ProviderResult。"""

    def _business_date_in_range(self, value: date | None, start: date | None, end: date | None) -> bool:
        """判断业务日期是否落在请求时间窗口内。"""
        if value is None:
            return True
        if start is not None and value < start:
            return False
        if end is not None and value > end:
            return False
        return True
