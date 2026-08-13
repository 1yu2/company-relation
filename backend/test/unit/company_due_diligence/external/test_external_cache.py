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
    assert cache.invalidate("akshare") == 1
    assert cache.get("akshare", "e-1", ["profile"], None, None) is None
    assert cache.get("financing", "e-1", ["financing"], None, None) is not None


def test_cache_invalidate_all_returns_cleared_count():
    """清理全部缓存时返回实际删除数量。"""
    cache = ExternalProviderCache()
    cache.set("akshare", "e-1", ["profile"], None, None, ProviderResult(provider_name="akshare"))
    cache.set("financing", "e-1", ["financing"], None, None, ProviderResult(provider_name="financing"))

    assert cache.invalidate() == 2
    assert cache.stats()["total"] == 0


def test_cache_zero_ttl_disables_reuse():
    """显式 TTL 为 0 时不应回退到默认 TTL。"""
    cache = ExternalProviderCache(default_ttl_seconds=60)
    cache.set("akshare", "e-1", ["profile"], None, None, ProviderResult(provider_name="akshare"), ttl_seconds=0)

    assert cache.get("akshare", "e-1", ["profile"], None, None) is None
