from __future__ import annotations

from types import SimpleNamespace

import pytest

from yuxi.agents.backends.sandbox.provider import sandbox_provisioner_token
from yuxi.agents.backends.sandbox.provisioner_client import ProvisionerClient


def test_provisioner_client_sends_bearer_token(monkeypatch):
    calls = []

    def fake_request(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(status_code=200, json=lambda: {"sandboxes": [], "count": 0})

    monkeypatch.setattr("yuxi.agents.backends.sandbox.provisioner_client.httpx.request", fake_request)
    client = ProvisionerClient(
        "http://sandbox-provisioner:8002",
        token="test-provisioner-token-that-is-long-enough",
    )

    client.health()

    assert calls == [
        {
            "method": "GET",
            "url": "http://sandbox-provisioner:8002/health",
            "timeout": client._timeout,
            "headers": {"Authorization": "Bearer test-provisioner-token-that-is-long-enough"},
        }
    ]


def test_sandbox_provisioner_token_reads_environment(monkeypatch):
    monkeypatch.setenv("SANDBOX_PROVISIONER_TOKEN", "test-provisioner-token-that-is-long-enough")

    assert sandbox_provisioner_token() == "test-provisioner-token-that-is-long-enough"


def test_sandbox_provisioner_token_is_required(monkeypatch):
    monkeypatch.delenv("SANDBOX_PROVISIONER_TOKEN", raising=False)

    with pytest.raises(ValueError, match="at least 32 characters"):
        sandbox_provisioner_token()


def test_create_waits_at_least_as_long_as_provisioner_health_check(monkeypatch):
    """创建请求的等待覆盖 provisioner 的健康检查上限；其他请求仍用短超时。"""
    monkeypatch.setenv("SANDBOX_HEALTH_TIMEOUT_SECONDS", "120")
    timeouts = {}

    def fake_request(**kwargs):
        timeouts[kwargs["method"]] = kwargs["timeout"]
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"sandbox_id": "s1", "sandbox_url": "http://s1:8080", "status": "running"},
        )

    monkeypatch.setattr("yuxi.agents.backends.sandbox.provisioner_client.httpx.request", fake_request)
    client = ProvisionerClient("http://sandbox-provisioner:8002", token="test-provisioner-token-that-is-long-enough")

    client.create("s1", "thread-1", "user-1")
    client.health()

    assert timeouts["POST"].read >= 120
    assert timeouts["GET"].read == 20
