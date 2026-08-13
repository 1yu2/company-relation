"""外部数据 Provider 最小必要缓存。"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from yuxi.company_due_diligence.external.models import ProviderResult


@dataclass
class _CacheEntry:
    """缓存项。"""

    result: ProviderResult
    expires_at: float


class ExternalProviderCache:
    """基于内存的 Provider 结果缓存，不缓存敏感原始响应。"""

    def __init__(self, default_ttl_seconds: float = 300.0) -> None:
        self._default_ttl = default_ttl_seconds
        self._store: dict[tuple[str, ...], _CacheEntry] = {}

    def _key(
        self,
        provider_name: str,
        eid: str,
        topics: tuple[str, ...],
        start_date: str | None,
        end_date: str | None,
    ) -> tuple[str, ...]:
        return (provider_name, eid, ":".join(topics), start_date or "", end_date or "")

    def get(
        self,
        provider_name: str,
        eid: str,
        topics: list[str],
        start_date: str | None,
        end_date: str | None,
    ) -> ProviderResult | None:
        """读取缓存；过期返回 None。"""
        key = self._key(provider_name, eid, tuple(topics), start_date, end_date)
        entry = self._store.get(key)
        if entry is None:
            return None
        if time.monotonic() > entry.expires_at:
            del self._store[key]
            return None
        return entry.result

    def set(
        self,
        provider_name: str,
        eid: str,
        topics: list[str],
        start_date: str | None,
        end_date: str | None,
        result: ProviderResult,
        ttl_seconds: float | None = None,
    ) -> None:
        """写入缓存。"""
        key = self._key(provider_name, eid, tuple(topics), start_date, end_date)
        self._store[key] = _CacheEntry(
            result=result,
            expires_at=time.monotonic() + (ttl_seconds or self._default_ttl),
        )

    def invalidate(self, provider_name: str | None = None) -> None:
        """清除缓存；provider_name 为 None 时清除全部。"""
        if provider_name is None:
            self._store.clear()
            return
        keys = [key for key in self._store if key[0] == provider_name]
        for key in keys:
            del self._store[key]

    def stats(self) -> dict[str, Any]:
        """返回缓存统计。"""
        now = time.monotonic()
        expired = sum(1 for entry in self._store.values() if entry.expires_at < now)
        return {"total": len(self._store), "expired": expired}
