"""企业尽调外部 MySQL 真实只读连接集成测试。"""

import pytest
from sqlalchemy import text

from yuxi.company_due_diligence.config import CompanyDueDiligenceMySQLConfig
from yuxi.company_due_diligence.mysql import CompanyDueDiligenceMySQL, MySQLUnavailableError

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_real_mysql_is_readonly_and_reuses_pool():
    """真实数据源应只读、表完整，并在连续查询后归还连接。"""
    mysql = CompanyDueDiligenceMySQL()
    try:
        first = await mysql.health()
        second = await mysql.health()
    finally:
        await mysql.close()

    assert first.configured is True
    assert first.reachable is True
    assert first.readonly is True
    assert first.required_tables_ready is True
    assert second.pool.size == 2
    assert second.pool.checked_out == 0
    assert second.pool.overflow == 0


async def test_statement_timeout_returns_connection_to_pool():
    """只读查询超时后连接池应继续执行后续查询。"""
    config = CompanyDueDiligenceMySQLConfig.from_env().model_copy(update={"statement_timeout_seconds": 1})
    mysql = CompanyDueDiligenceMySQL()
    mysql.initialize(config)
    try:
        with pytest.raises(MySQLUnavailableError, match="查询超时"):
            async with mysql.connection() as connection:
                await connection.execute(text("SELECT SLEEP(2)"))

        async with mysql.connection() as connection:
            result = await connection.execute(text("SELECT 1"))
            assert result.scalar_one() == 1
        assert mysql.pool_health().checked_out == 0
    finally:
        await mysql.close()
