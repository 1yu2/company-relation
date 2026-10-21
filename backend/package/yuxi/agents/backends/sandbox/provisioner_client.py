from __future__ import annotations

import os
from dataclasses import dataclass

import httpx


@dataclass(slots=True)
class SandboxRecord:
    sandbox_id: str
    sandbox_url: str
    status: str | None = None


# 创建沙盒时 provisioner 会等容器通过健康检查，最长 SANDBOX_HEALTH_TIMEOUT_SECONDS（默认 300 秒）。
# 客户端的等待不能比这短：并发 8 时 all-in-one 镜像同时启动、争抢 CPU，实测创建耗时 p95 约 21 秒、
# 最长 25 秒（2026-10-21），原来统一的 20 秒超时会让 2.5% 的创建在服务端仍在正常进行时被判失败。
_CREATE_TIMEOUT_MARGIN_SECONDS = 30


class ProvisionerClient:
    def __init__(self, base_url: str, *, token: str, timeout_seconds: int = 20):
        self._base_url = base_url.rstrip("/")
        self._timeout = httpx.Timeout(timeout_seconds)
        health_timeout = int(os.getenv("SANDBOX_HEALTH_TIMEOUT_SECONDS") or 300)
        self._create_timeout = httpx.Timeout(health_timeout + _CREATE_TIMEOUT_MARGIN_SECONDS)
        self._headers = {"Authorization": f"Bearer {token}"}

    def _request(self, method: str, path: str, *, timeout: httpx.Timeout | None = None, **kwargs) -> httpx.Response:
        return httpx.request(
            method=method,
            url=f"{self._base_url}{path}",
            timeout=timeout or self._timeout,
            headers=self._headers,
            **kwargs,
        )

    def health(self) -> bool:
        response = self._request("GET", "/health")
        return response.status_code == 200

    def create(
        self,
        sandbox_id: str,
        thread_id: str,
        uid: str,
        env: dict[str, str] | None = None,
        *,
        file_thread_id: str | None = None,
        skills_thread_id: str | None = None,
    ) -> SandboxRecord:
        response = self._request(
            "POST",
            "/api/sandboxes",
            timeout=self._create_timeout,
            json={
                "sandbox_id": sandbox_id,
                "thread_id": thread_id,
                "file_thread_id": file_thread_id or thread_id,
                "skills_thread_id": skills_thread_id or thread_id,
                "uid": uid,
                "env": env or {},
            },
        )
        if response.status_code >= 400:
            raise RuntimeError(f"failed to create sandbox {sandbox_id}: {response.status_code} {response.text}")
        payload = response.json()
        return SandboxRecord(
            sandbox_id=payload["sandbox_id"],
            sandbox_url=payload["sandbox_url"],
            status=payload.get("status"),
        )

    def discover(self, sandbox_id: str) -> SandboxRecord | None:
        response = self._request("GET", f"/api/sandboxes/{sandbox_id}")
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise RuntimeError(f"failed to discover sandbox {sandbox_id}: {response.status_code} {response.text}")
        payload = response.json()
        return SandboxRecord(
            sandbox_id=payload["sandbox_id"],
            sandbox_url=payload["sandbox_url"],
            status=payload.get("status"),
        )

    def touch(self, sandbox_id: str) -> bool:
        response = self._request("POST", f"/api/sandboxes/{sandbox_id}/touch")
        if response.status_code == 404:
            return False
        if response.status_code >= 400:
            raise RuntimeError(f"failed to touch sandbox {sandbox_id}: {response.status_code} {response.text}")
        return True

    def delete(self, sandbox_id: str) -> None:
        response = self._request("DELETE", f"/api/sandboxes/{sandbox_id}")
        if response.status_code in {200, 404}:
            return
        raise RuntimeError(f"failed to delete sandbox {sandbox_id}: {response.status_code} {response.text}")
