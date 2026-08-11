"""企业身份解析真实 MySQL 集成测试。"""

import pytest

from yuxi.company_due_diligence.entity_resolution import EntityResolutionService
from yuxi.company_due_diligence.models import CompanyReference, ResolutionStatus
from yuxi.company_due_diligence.mysql import CompanyDueDiligenceMySQL

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_real_company_resolves_consistently_by_all_identifiers():
    """同一企业应通过 eid、信用代码、股票代码和全称解析到同一身份。"""
    mysql = CompanyDueDiligenceMySQL()
    service = EntityResolutionService(mysql=mysql)
    try:
        search_result = await service.search("股份", limit=1)
        assert search_result.candidates
        candidate = search_result.candidates[0]

        results = [
            await service.resolve(CompanyReference(eid=candidate.eid)),
            await service.resolve(CompanyReference(credit_code=candidate.credit_code)),
            await service.resolve(CompanyReference(stock_code=candidate.stock_code)),
            await service.resolve(CompanyReference(name=candidate.name)),
            await service.resolve(CompanyReference(name=" ".join(candidate.name))),
        ]
    finally:
        await mysql.close()

    assert all(result.status == ResolutionStatus.RESOLVED for result in results)
    assert {result.company.eid for result in results} == {candidate.eid}
    assert all(result.sources for result in results)


async def test_real_company_search_is_limited_and_unknown_eid_is_not_found():
    """候选搜索应遵守上限，未知 eid 应返回真实未找到。"""
    mysql = CompanyDueDiligenceMySQL()
    service = EntityResolutionService(mysql=mysql)
    try:
        search_result = await service.search("股份", limit=2)
        not_found = await service.resolve(CompanyReference(eid="00000000-0000-0000-0000-000000000000"))
    finally:
        await mysql.close()

    assert 1 <= len(search_result.candidates) <= 2
    assert not_found.status == ResolutionStatus.NOT_FOUND
