from __future__ import annotations

from types import SimpleNamespace

import pytest
from starlette.requests import Request
from starlette.responses import Response

import server.main as main


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, int] = {}
        self.expirations: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.values[key] = self.values.get(key, 0) + 1
        return self.values[key]

    async def expire(self, key: str, seconds: int) -> bool:
        self.expirations[key] = seconds
        return True

    async def ttl(self, key: str) -> int:
        return self.expirations.get(key, -1)

    async def delete(self, key: str) -> int:
        return int(self.values.pop(key, None) is not None)


def _request(client_host: str, headers: list[tuple[bytes, bytes]] = []) -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/auth/token",
        "raw_path": b"/api/auth/token",
        "query_string": b"",
        "headers": headers,
        "client": (client_host, 1234),
        "server": ("testserver", 80),
        "scheme": "http",
    }
    return Request(scope)


def test_extract_client_ip_ignores_forwarded_header_from_untrusted_peer(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("YUXI_TRUSTED_PROXY_NETWORKS", "10.0.0.0/8")

    request = _request("192.0.2.10", [(b"x-forwarded-for", b"198.51.100.20")])

    assert main._extract_client_ip(request) == "192.0.2.10"


def test_extract_client_ip_uses_rightmost_valid_address_from_trusted_proxy(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("YUXI_TRUSTED_PROXY_NETWORKS", "10.0.0.0/8")

    request = _request(
        "10.1.2.3",
        [(b"x-forwarded-for", b"198.51.100.20, 203.0.113.7, forged")],
    )

    assert main._extract_client_ip(request) == "203.0.113.7"


@pytest.mark.asyncio
async def test_login_rate_limit_uses_shared_redis_window(monkeypatch: pytest.MonkeyPatch):
    redis = FakeRedis()
    monkeypatch.setattr(main, "get_async_redis_client", lambda: _async_value(redis))

    for _ in range(main.RATE_LIMIT_MAX_ATTEMPTS):
        assert await main._increment_login_attempt("198.51.100.20") <= main.RATE_LIMIT_MAX_ATTEMPTS

    assert await main._increment_login_attempt("198.51.100.20") == main.RATE_LIMIT_MAX_ATTEMPTS + 1
    assert redis.expirations[f"{main.LOGIN_RATE_LIMIT_KEY_PREFIX}198.51.100.20"] == main.RATE_LIMIT_WINDOW_SECONDS
    assert await main._login_retry_after("198.51.100.20") == main.RATE_LIMIT_WINDOW_SECONDS


@pytest.mark.asyncio
async def test_rate_limit_middleware_returns_429_and_clears_after_success(monkeypatch: pytest.MonkeyPatch):
    redis = FakeRedis()
    monkeypatch.setattr(main, "get_async_redis_client", lambda: _async_value(redis))
    middleware = main.LoginRateLimitMiddleware(app=SimpleNamespace())
    request = _request("198.51.100.20")

    async def call_next(_request: Request) -> Response:
        return Response(status_code=401)

    for _ in range(main.RATE_LIMIT_MAX_ATTEMPTS):
        assert (await middleware.dispatch(request, call_next)).status_code == 401

    limited = await middleware.dispatch(request, call_next)
    assert limited.status_code == 429
    assert limited.headers["Retry-After"] == str(main.RATE_LIMIT_WINDOW_SECONDS)

    async def successful_call(_request: Request) -> Response:
        return Response(status_code=200)

    await main._clear_login_attempts("198.51.100.20")
    assert (await middleware.dispatch(request, successful_call)).status_code == 200
    assert redis.values == {}


@pytest.mark.asyncio
async def test_login_rate_limit_rejects_when_redis_is_unavailable(monkeypatch: pytest.MonkeyPatch):
    async def unavailable():
        raise ConnectionError("redis unavailable")

    monkeypatch.setattr(main, "get_async_redis_client", unavailable)
    middleware = main.LoginRateLimitMiddleware(app=SimpleNamespace())

    async def call_next(_request: Request) -> Response:
        raise AssertionError("login handler must not run")

    response = await middleware.dispatch(_request("198.51.100.20"), call_next)

    assert response.status_code == 503
    assert response.body.decode() == '{"detail":"登录服务暂时不可用，请稍后重试"}'


async def _async_value(value):
    return value
