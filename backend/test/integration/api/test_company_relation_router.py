from __future__ import annotations

import pytest


pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_admin_can_query_company_relation_with_evidence(test_client, admin_headers):
    response = await test_client.post(
        "/api/company-relations/query",
        json={"query": "平安银行和万科A有什么关联？", "strategy": "graph_rag"},
        headers=admin_headers,
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["refused"] is False
    assert len(data["paths"][0]) >= 2
    assert data["source_ids"]
    assert all(source["provider"] for source in data["sources"])


async def test_company_relation_info_exposes_checksum_and_quality(test_client, admin_headers):
    response = await test_client.get("/api/company-relations/info", headers=admin_headers)

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["validation"]["valid"] is True
    assert data["validation"]["node_count"] >= 500
    assert data["validation"]["source_coverage"] == 1.0
    assert len(data["snapshot_checksum"]) == 64
