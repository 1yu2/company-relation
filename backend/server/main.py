import asyncio
import ipaddress
import os
import sys

# ==============================================================================
# 解决 Windows 下 psycopg 异步模式不支持 ProactorEventLoop 的问题
# 注意：这段代码必须放在应用的极早期，最好在导入 FastAPI 或初始化数据库之前
# ==============================================================================
if sys.platform == "win32":
    # 把当前文件 (main.py) 的上一级的上一级 (即根目录 Yuxi) 加入到 sys.path
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import uvicorn
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from server.routers import router
from server.utils.lifespan import lifespan
from server.utils.common_utils import setup_logging
from server.utils.access_log_middleware import AccessLogMiddleware
from yuxi.storage.redis import get_async_redis_client

# 设置日志配置
setup_logging()

RATE_LIMIT_MAX_ATTEMPTS = 10
RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_ENDPOINTS = {("/api/auth/token", "POST")}
DEFAULT_DEVELOPMENT_CORS_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")
EXPLICIT_CORS_METHODS = ("DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT")
EXPLICIT_CORS_HEADERS = ("Accept", "Authorization", "Content-Type", "Last-Event-ID", "X-Requested-With")

# Redis key contains only the client address and expires with the rate window.
LOGIN_RATE_LIMIT_KEY_PREFIX = "security:login-rate:"


def _trusted_proxy_networks() -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    configured = os.getenv("YUXI_TRUSTED_PROXY_NETWORKS", "")
    networks = []
    for value in configured.split(","):
        value = value.strip()
        if not value:
            continue
        try:
            networks.append(ipaddress.ip_network(value, strict=False))
        except ValueError as exc:
            raise RuntimeError(f"Invalid YUXI_TRUSTED_PROXY_NETWORKS entry: {value}") from exc
    return tuple(networks)


def _extract_client_ip(request: Request) -> str:
    peer = request.client.host if request.client else "unknown"
    try:
        peer_address = ipaddress.ip_address(peer)
    except ValueError:
        return peer

    if any(peer_address in network for network in _trusted_proxy_networks()):
        forwarded_for = request.headers.get("x-forwarded-for")
        if forwarded_for:
            for address in reversed([item.strip() for item in forwarded_for.split(",") if item.strip()]):
                try:
                    ipaddress.ip_address(address)
                except ValueError:
                    continue
                return address

    return peer


async def _increment_login_attempt(client_ip: str) -> int:
    redis = await get_async_redis_client()
    key = f"{LOGIN_RATE_LIMIT_KEY_PREFIX}{client_ip}"
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, RATE_LIMIT_WINDOW_SECONDS)
    return int(count)


async def _login_retry_after(client_ip: str) -> int:
    redis = await get_async_redis_client()
    ttl = await redis.ttl(f"{LOGIN_RATE_LIMIT_KEY_PREFIX}{client_ip}")
    return int(max(1, ttl if ttl > 0 else RATE_LIMIT_WINDOW_SECONDS))


async def _clear_login_attempts(client_ip: str) -> None:
    redis = await get_async_redis_client()
    await redis.delete(f"{LOGIN_RATE_LIMIT_KEY_PREFIX}{client_ip}")


def _parse_cors_origins() -> list[str]:
    value = os.getenv("YUXI_CORS_ORIGINS")
    origins = [origin.strip() for origin in (value or "").split(",") if origin.strip()]
    if origins:
        return origins

    environment = (os.getenv("YUXI_ENV") or "development").strip().lower()
    if environment in {"production", "prod"}:
        return []

    return list(DEFAULT_DEVELOPMENT_CORS_ORIGINS)


def _build_cors_options(origins: list[str] | None = None) -> dict[str, object]:
    allow_origins = _parse_cors_origins() if origins is None else origins
    if "*" in allow_origins:
        return {
            "allow_origins": ["*"],
            "allow_credentials": False,
            "allow_methods": ["*"],
            "allow_headers": ["*"],
        }

    return {
        "allow_origins": allow_origins,
        "allow_credentials": True,
        "allow_methods": list(EXPLICIT_CORS_METHODS),
        "allow_headers": list(EXPLICIT_CORS_HEADERS),
        "expose_headers": ["Content-Disposition", "X-Lock-Remaining"],
    }


app = FastAPI(lifespan=lifespan)
# 所有业务接口统一挂载到 /api，具体分组在 server.routers 中集中注册。
app.include_router(router, prefix="/api")

# CORS 设置
app.add_middleware(
    CORSMiddleware,
    **_build_cors_options(),
)


class LoginRateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        normalized_path = request.url.path.rstrip("/") or "/"
        request_signature = (normalized_path, request.method.upper())
        if request_signature not in RATE_LIMIT_ENDPOINTS:
            return await call_next(request)

        client_ip = _extract_client_ip(request)
        try:
            attempt_count = await _increment_login_attempt(client_ip)
        except Exception:
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={"detail": "登录服务暂时不可用，请稍后重试"},
            )

        if attempt_count > RATE_LIMIT_MAX_ATTEMPTS:
            try:
                retry_after = await _login_retry_after(client_ip)
            except Exception:
                retry_after = RATE_LIMIT_WINDOW_SECONDS
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={"detail": "登录尝试过于频繁，请稍后再试"},
                headers={"Retry-After": str(retry_after)},
            )

        response = await call_next(request)
        if response.status_code < 400:
            try:
                await _clear_login_attempts(client_ip)
            except Exception:
                pass
        return response


# 添加访问日志中间件（记录请求处理时间）
app.add_middleware(AccessLogMiddleware)

# 添加登录限流中间件
app.add_middleware(LoginRateLimitMiddleware)

if __name__ == "__main__":
    # uvicorn.run(app, host="0.0.0.0", port=5050, threads=10, workers=10, reload=True)

    uvicorn.run(
        "server.main:app",
        host="0.0.0.0",
        port=5050,
        reload=True,
        # 与 docker-compose 开发环境保持一致，避免 package 下代码变更不触发热重载。
        reload_dirs=["server", "package"],
    )
