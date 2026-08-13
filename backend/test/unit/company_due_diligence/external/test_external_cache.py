"""外部数据 Provider 缓存测试。"""

from __future__ import annotations


from yuxi.company_due_diligence.external.cache import ExternalProviderCache
from yuxi.company_due_diligence.external.models import ProviderResult


def test_cache_returns_result_within_ttl():
    """缓存 TTL 内命中。"""
    cache = ExternalProviderCache(default_ttl_seconds=60)
    result = ProviderResult(provider_name="akshare")
    cache.set("akshare", "e-1", ["profile"], "2023-01-01", "2026-01-01", result)
    cached = cache.get("akshare", "e-1", ["profile"], "2023-01-01", "2026-01-01")
    assert cached is result


def test_cache_expires_after_ttl():
    """缓存 TTL 过期后返回 None。"""
    cache = ExternalProviderCache(default_ttl_seconds=0.001)
    result = ProviderResult(provider_name="akshare")
    cache.set("akshare", "e-1", ["profile"], None, None, result)
    import time

    time.sleep(0.01)
    assert cache.get("akshare", "e-1", ["profile"], None, None) is None


def test_cache_invalidate_by_provider():
    """按 Provider 清除缓存。"""
    cache = ExternalProviderCache()
    cache.set("akshare", "e-1", ["profile"], None, None, ProviderResult(provider_name="akshare"))
    cache.set("financing", "e-1", ["financing"], None, None, ProviderResult(provider_name="financing"))
    cache.invalidate("akshare")
    assert cache.get("akshare", "e-1", ["profile"], None, None) is None
    assert cache.get("financing", "e-1", ["financing"], None, None) is not None
