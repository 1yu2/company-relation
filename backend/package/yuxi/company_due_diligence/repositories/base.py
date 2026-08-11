"""企业尽调只读 Repository 基类。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from yuxi.company_due_diligence.mysql import ReadonlyConnection


class BaseReadonlyRepository:
    """为领域 Repository 提供受保护的只读查询方法。"""

    def __init__(self, connection: ReadonlyConnection):
        self._connection = connection

    async def _fetch_one(
        self,
        statement: Any,
        parameters: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any] | None:
        """执行查询并返回第一行映射。"""
        result = await self._connection.execute(statement, parameters)
        row = result.mappings().first()
        return dict(row) if row is not None else None

    async def _fetch_all(
        self,
        statement: Any,
        parameters: Mapping[str, Any] | None = None,
    ) -> list[Mapping[str, Any]]:
        """执行查询并返回全部行映射。"""
        result = await self._connection.execute(statement, parameters)
        return [dict(row) for row in result.mappings()]
