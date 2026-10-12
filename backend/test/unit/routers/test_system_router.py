from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.routers.system_router import readiness_check, system
from server.utils.auth_middleware import get_admin_user
from yuxi.company_due_diligence.external import external_company_data_service
from yuxi.company_due_diligence.external.models import (
    ProviderHealth,
    ProviderQueryAudit,
    ProviderStatus,
)



@pytest.mark.asyncio
async def test_readiness_requires_postgres_redis_and_worker(monkeypatch):
    class SessionContext:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def execute(self, _statement):
            return None

    class Redis:
        async def ping(self):
            return True

        async def get(self, _key):
            return "1"

    monkeypatch.setattr("server.routers.system_router.pg_manager.get_async_session_context", lambda: SessionContext())
    monkeypatch.setattr("server.routers.system_router.get_redis_client", lambda: _async_value(Redis()))

    response = await readiness_check()

    assert response.status_code == 200
    assert response.body == b'{"status":"ready","checks":{"postgres":"ok","redis":"ok","worker":"ok"}}'


@pytest.mark.asyncio
async def test_readiness_returns_503_without_worker_heartbeat(monkeypatch):
    class Redis:
        async def ping(self):
            return True

        async def get(self, _key):
            return None

    class UnavailableSession:
        async def __aenter__(self):
            raise ConnectionError("postgres unavailable")

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(
        "server.routers.system_router.pg_manager.get_async_session_context", lambda: UnavailableSession()
    )
    monkeypatch.setattr("server.routers.system_router.get_redis_client", lambda: _async_value(Redis()))

    response = await readiness_check()

    assert response.status_code == 503
    assert response.body == b'{"status":"not_ready","checks":{"postgres":"unavailable","redis":"ok","worker":"unavailable"}}'


async def _async_value(value):
    return value


def test_discovery_endpoint_is_public(monkeypatch):
    monkeypatch.setattr("server.routers.system_router.get_version", lambda: "0.7.1.dev0")

    app = FastAPI()
    app.include_router(system, prefix="/api")
    response = TestClient(app).get("/api/system/discovery")

    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "la-platform"
    assert payload["version"] == "0.7.1.dev0"
    assert payload["api_prefix"] == "/api"
    assert payload["capabilities"]["cli"]["browser_login"] is True
    assert payload["capabilities"]["cli"]["api_key_auth"] is True
    assert payload["capabilities"]["cli"]["kb_upload"] is True
    assert payload["endpoints"]["cli_auth_sessions"] == "/api/auth/cli/sessions"


def test_external_company_data_operations_are_admin_accessible(monkeypatch):
    """管理员可查看健康、缓存、脱敏审计并清理缓存。"""
    checked_at = datetime(2026, 8, 13, tzinfo=UTC)
    health = AsyncMock(
        return_value=[
            ProviderHealth(
                provider_name="akshare",
                status=ProviderStatus.HEALTHY,
                supported_topics=["profile"],
                checked_at=checked_at,
            )
        ]
    )
    cache_stats = Mock(return_value={"total": 2, "expired": 1})
    clear_cache = Mock(return_value=2)
    audit_entries = Mock(
        return_value=[
            ProviderQueryAudit(
                provider_name="akshare",
                topics=["profile"],
                status=ProviderStatus.HEALTHY,
                latency_ms=12.5,
                queried_at=checked_at,
            )
        ]
    )
    monkeypatch.setattr(external_company_data_service, "health", health)
    monkeypatch.setattr(external_company_data_service, "cache_stats", cache_stats)
    monkeypatch.setattr(external_company_data_service, "clear_cache", clear_cache)
    monkeypatch.setattr(external_company_data_service, "audit_entries", audit_entries)

    app = FastAPI()
    app.include_router(system, prefix="/api")
    app.dependency_overrides[get_admin_user] = lambda: object()
    client = TestClient(app)

    health_response = client.get("/api/system/external-company-data/health")
    cache_response = client.get("/api/system/external-company-data/cache")
    audit_response = client.get("/api/system/external-company-data/audit?limit=10")
    clear_response = client.delete("/api/system/external-company-data/cache?provider_name=akshare")

    assert health_response.status_code == 200
    assert health_response.json()["providers"][0]["provider_name"] == "akshare"
    assert cache_response.json() == {"cache": {"total": 2, "expired": 1}}
    assert audit_response.json()["entries"][0] == {
        "provider_name": "akshare",
        "topics": ["profile"],
        "status": "healthy",
        "error_code": None,
        "latency_ms": 12.5,
        "queried_at": "2026-08-13T00:00:00Z",
    }
    assert clear_response.json() == {"cleared": 2, "provider_name": "akshare"}
    audit_entries.assert_called_once_with(10)
    clear_cache.assert_called_once_with("akshare")
