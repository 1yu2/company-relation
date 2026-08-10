from __future__ import annotations

import pytest

from yuxi.company_relation.snapshot import CompanyRelationSnapshot


@pytest.fixture
def company_relation_snapshot() -> CompanyRelationSnapshot:
    return CompanyRelationSnapshot.from_dict(
        {
            "dataset_id": "company_relation_test",
            "name": "企业关系测试快照",
            "as_of_date": "2025-12-31",
            "collected_at": "2026-08-07T00:00:00+00:00",
            "sources": [
                {
                    "source_id": "source:profile",
                    "provider": "公开披露",
                    "title": "公司资料",
                    "url": "https://example.com/profile",
                    "interface": "test_profile",
                    "data_date": "2025-12-31",
                    "collected_at": "2026-08-07T00:00:00+00:00",
                },
                {
                    "source_id": "source:holder",
                    "provider": "公开披露",
                    "title": "股东资料",
                    "url": "https://example.com/holder",
                    "interface": "test_holder",
                    "data_date": "2025-12-31",
                    "collected_at": "2026-08-07T00:00:00+00:00",
                },
            ],
            "nodes": [
                {
                    "id": "company:a",
                    "type": "Company",
                    "name": "甲公司",
                    "properties": {
                        "stock_code": "000001",
                        "industry": "软件服务",
                        "legal_representative": "张三",
                    },
                    "source_ids": ["source:profile"],
                },
                {
                    "id": "company:b",
                    "type": "Company",
                    "name": "乙公司",
                    "properties": {"stock_code": "000002", "industry": "软件服务"},
                    "source_ids": ["source:profile"],
                },
                {
                    "id": "company:holder",
                    "type": "Company",
                    "name": "共同股东",
                    "properties": {"listed": False},
                    "source_ids": ["source:holder"],
                },
                {
                    "id": "person:zhangsan",
                    "type": "Person",
                    "name": "张三",
                    "properties": {},
                    "source_ids": ["source:profile"],
                },
                {
                    "id": "industry:software",
                    "type": "Industry",
                    "name": "软件服务",
                    "properties": {},
                    "source_ids": ["source:profile"],
                },
                {
                    "id": "announcement:a1",
                    "type": "Announcement",
                    "name": "甲公司年度报告",
                    "properties": {"published_at": "2025-12-31"},
                    "source_ids": ["source:profile"],
                },
            ],
            "edges": [
                {
                    "id": "holds:holder:a",
                    "type": "HOLDS",
                    "source": "company:holder",
                    "target": "company:a",
                    "properties": {"share_ratio": 10.0},
                    "source_ids": ["source:holder"],
                },
                {
                    "id": "holds:holder:b",
                    "type": "HOLDS",
                    "source": "company:holder",
                    "target": "company:b",
                    "properties": {"share_ratio": 8.0},
                    "source_ids": ["source:holder"],
                },
                {
                    "id": "executive:zhangsan:a",
                    "type": "EXECUTIVE_OF",
                    "source": "person:zhangsan",
                    "target": "company:a",
                    "properties": {"role": "法定代表人"},
                    "source_ids": ["source:profile"],
                },
                {
                    "id": "belongs:a:software",
                    "type": "BELONGS_TO",
                    "source": "company:a",
                    "target": "industry:software",
                    "properties": {},
                    "source_ids": ["source:profile"],
                },
                {
                    "id": "released:a:a1",
                    "type": "RELEASED",
                    "source": "company:a",
                    "target": "announcement:a1",
                    "properties": {},
                    "source_ids": ["source:profile"],
                },
            ],
        }
    )
