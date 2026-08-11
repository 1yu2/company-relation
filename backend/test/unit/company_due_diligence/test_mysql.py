"""企业尽调 MySQL 只读边界单元测试。"""

import asyncio

import pytest
from sqlalchemy import create_engine, text

import yuxi.company_due_diligence.mysql as mysql_module
from yuxi.company_due_diligence.config import CompanyDueDiligenceMySQLConfig
from yuxi.company_due_diligence.mysql import (
    CompanyDueDiligenceMySQL,
    MySQLUnavailableError,
    ReadonlyConnection,
    ReadonlyViolationError,
    _install_readonly_sql_guard,
    _install_session_readonly,
    ensure_readonly_sql,
    ensure_readonly_statement,
)


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO t_enterprise VALUES (1)",
        "UPDATE t_enterprise SET name = 'x'",
        "DELETE FROM t_enterprise",
        "DROP TABLE t_enterprise",
        "SELECT * FROM t_enterprise FOR UPDATE",
        "SELECT 1; SELECT 2",
    ],
)
def test_readonly_sql_rejects_write_and_side_effect_statements(statement):
    """写入、多语句和带锁查询必须在代码边界被拒绝。"""
    with pytest.raises(ReadonlyViolationError):
        ensure_readonly_sql(statement)


def test_readonly_statement_accepts_select():
    """普通参数化查询应通过只读校验。"""
    ensure_readonly_statement(text("SELECT * FROM t_enterprise WHERE eid = :eid"))


def test_engine_guard_rejects_raw_driver_write():
    """绕过 Repository 的原始 SQL 也必须被 Engine 事件拒绝。"""
    engine = create_engine("sqlite://")
    _install_readonly_sql_guard(engine)

    with engine.connect() as connection, pytest.raises(ReadonlyViolationError):
        connection.exec_driver_sql("CREATE TABLE forbidden (id INTEGER)")

    engine.dispose()


def test_initialize_uses_bounded_async_pool(monkeypatch):
    """连接池必须启用预检、零溢出和任务约定的超时参数。"""
    captured = {}

    class FakeAsyncEngine:
        """记录初始化参数而不建立真实连接。"""

        sync_engine = object()

    def fake_create_async_engine(url, **kwargs):
        """保存 AsyncEngine 创建参数。"""
        captured["url"] = url
        captured.update(kwargs)
        return FakeAsyncEngine()

    monkeypatch.setattr(mysql_module, "create_async_engine", fake_create_async_engine)
    monkeypatch.setattr(mysql_module, "_install_session_readonly", lambda *_args: None)
    monkeypatch.setattr(mysql_module, "_install_readonly_sql_guard", lambda *_args: None)
    mysql = CompanyDueDiligenceMySQL()
    config = CompanyDueDiligenceMySQLConfig(
        url="mysql+aiomysql://readonly:password@mysql.example.com/company",
    )

    mysql.initialize(config)
    mysql.initialize(config)

    assert captured["pool_size"] == 2
    assert captured["max_overflow"] == 0
    assert captured["pool_pre_ping"] is True
    assert captured["pool_recycle"] == 300
    assert captured["pool_timeout"] == 5
    assert captured["connect_args"]["connect_timeout"] == 8


def test_session_initializer_sets_readonly_and_statement_timeout(monkeypatch):
    """每个新连接必须设置只读事务和有限语句执行时间。"""
    registered = {}
    statements = []

    class FakeCursor:
        """记录连接初始化 SQL。"""

        def execute(self, statement):
            """保存执行语句。"""
            statements.append(statement)

        def close(self):
            """模拟关闭游标。"""

    class FakeConnection:
        """提供记录型游标。"""

        def cursor(self):
            """返回记录型游标。"""
            return FakeCursor()

    def fake_listen(_engine, event_name, callback):
        """捕获 SQLAlchemy 连接事件回调。"""
        registered[event_name] = callback

    monkeypatch.setattr(mysql_module.event, "listen", fake_listen)
    _install_session_readonly(object(), 10)

    registered["connect"](FakeConnection(), object())

    assert statements == [
        "SET SESSION TRANSACTION READ ONLY",
        "SET SESSION MAX_EXECUTION_TIME = 10000",
    ]


@pytest.mark.asyncio
async def test_health_reports_unconfigured_without_creating_pool(monkeypatch):
    """未配置外部 MySQL 时健康检查应返回明确状态。"""
    monkeypatch.delenv("MYSQL_URL", raising=False)
    mysql = CompanyDueDiligenceMySQL()

    health = await mysql.health()

    assert health.configured is False
    assert health.reachable is False
    assert health.readonly is False
    assert health.pool.size == 0


@pytest.mark.asyncio
async def test_readonly_connection_invalidates_timed_out_query():
    """客户端查询超时应失效当前连接并返回显式来源错误。"""

    class SlowConnection:
        """模拟不会在服务端主动结束的慢查询连接。"""

        invalidated = False

        async def execute(self, _statement, _parameters):
            """等待直到客户端超时取消。"""
            await asyncio.sleep(1)

        async def invalidate(self, _exception):
            """记录连接已从池中失效。"""
            self.invalidated = True

    connection = SlowConnection()
    readonly = ReadonlyConnection(connection, statement_timeout_seconds=0.01)

    with pytest.raises(MySQLUnavailableError, match="查询超时"):
        await readonly.execute(text("SELECT 1"))

    assert connection.invalidated is True
