"""企业尽调只读 MySQL 异步连接池与健康检查。"""

from __future__ import annotations

import asyncio
import os
import re
import time
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any

from pydantic import BaseModel
from sqlalchemy import bindparam, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.sql.elements import TextClause

from yuxi.company_due_diligence.config import MYSQL_URL_ENV, REQUIRED_TABLES, CompanyDueDiligenceMySQLConfig
from yuxi.utils import logger

_LEADING_SQL_COMMENTS = re.compile(r"^(?:(?:\s+)|(?:--[^\n]*(?:\n|$))|(?:/\*.*?\*/))*", re.DOTALL)
_BLOCKED_SELECT_PATTERNS = (
    re.compile(r"\bINTO\s+(?:OUTFILE|DUMPFILE)\b", re.IGNORECASE),
    re.compile(r"\bFOR\s+UPDATE\b", re.IGNORECASE),
    re.compile(r"\bLOCK\s+IN\s+SHARE\s+MODE\b", re.IGNORECASE),
    re.compile(r"\bGET_LOCK\s*\(", re.IGNORECASE),
    re.compile(r"\bLOAD_FILE\s*\(", re.IGNORECASE),
)


class ReadonlyViolationError(PermissionError):
    """表示 SQL 违反企业尽调只读边界。"""


class MySQLUnavailableError(RuntimeError):
    """表示企业尽调 MySQL 尚未配置或无法建立连接。"""


class MySQLPoolHealth(BaseModel):
    """描述只读 MySQL 连接池的即时状态。"""

    size: int
    checked_out: int
    overflow: int
    wait_ms: float = 0
    timeouts: int = 0


class MySQLHealth(BaseModel):
    """描述企业尽调 MySQL 的脱敏健康状态。"""

    configured: bool
    reachable: bool
    readonly: bool
    required_tables_ready: bool
    latency_ms: int
    pool: MySQLPoolHealth


class ReadonlyConnection:
    """仅暴露受校验异步查询方法的数据库连接。"""

    def __init__(self, connection: AsyncConnection, statement_timeout_seconds: int):
        self._connection = connection
        self._statement_timeout_seconds = statement_timeout_seconds

    async def execute(self, statement: Any, parameters: Mapping[str, Any] | None = None):
        """在客户端超时边界内执行只读 SQLAlchemy 语句。"""
        ensure_readonly_statement(statement)
        try:
            async with asyncio.timeout(self._statement_timeout_seconds):
                return await self._connection.execute(statement, parameters or {})
        except TimeoutError as exc:
            await self._connection.invalidate(exc)
            raise MySQLUnavailableError("企业尽调 MySQL 查询超时") from exc


class CompanyDueDiligenceMySQL:
    """管理进程级企业尽调只读 MySQL AsyncEngine。"""

    def __init__(self) -> None:
        self._config: CompanyDueDiligenceMySQLConfig | None = None
        self._engine: AsyncEngine | None = None
        self._pool_timeouts = 0
        self._pool_wait_ms = 0.0

    @property
    def configured(self) -> bool:
        """返回当前进程是否配置了企业尽调 MySQL。"""
        if self._config is not None:
            return self._config.configured
        return bool(os.environ.get(MYSQL_URL_ENV, "").strip())

    def initialize(self, config: CompanyDueDiligenceMySQLConfig | None = None) -> None:
        """按需创建唯一的 SQLAlchemy 异步只读连接池。"""
        if self._engine is not None:
            return

        self._config = config or CompanyDueDiligenceMySQLConfig.from_env()
        if not self._config.configured:
            return

        engine = create_async_engine(
            self._config.url,
            pool_size=self._config.pool_size,
            max_overflow=self._config.max_overflow,
            pool_pre_ping=True,
            pool_recycle=self._config.pool_recycle_seconds,
            pool_timeout=self._config.pool_timeout_seconds,
            connect_args={
                "connect_timeout": self._config.connect_timeout_seconds,
                "charset": "utf8mb4",
                "autocommit": False,
            },
        )
        _install_session_readonly(engine.sync_engine, self._config.statement_timeout_seconds)
        _install_readonly_sql_guard(engine.sync_engine)
        self._engine = engine

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[ReadonlyConnection]:
        """签出只读连接，并保证结束后回滚和归还连接池。"""
        self.initialize()
        if self._engine is None:
            raise MySQLUnavailableError("企业尽调 MySQL 未配置")

        started_at = time.perf_counter()
        try:
            async with self._engine.connect() as connection:
                self._pool_wait_ms += (time.perf_counter() - started_at) * 1000
                try:
                    yield ReadonlyConnection(connection, self._config.statement_timeout_seconds)
                finally:
                    if not connection.invalidated:
                        await connection.rollback()
        except SQLAlchemyTimeoutError as exc:
            self._pool_timeouts += 1
            raise MySQLUnavailableError("企业尽调 MySQL 连接池等待超时") from exc

    async def health(self) -> MySQLHealth:
        """检查连接、只读会话、必要表和连接池状态。"""
        started_at = time.perf_counter()
        try:
            self.initialize()
            if self._engine is None:
                return self._health_result(started_at, reachable=False, readonly=False, tables_ready=False)

            async with self.connection() as connection:
                result = await connection.execute(text("SELECT 1"))
                result.scalar_one()
                readonly = await _read_readonly_state(connection)
                statement = text(
                    """
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = DATABASE()
                      AND table_name IN :table_names
                    """
                ).bindparams(bindparam("table_names", expanding=True))
                table_result = await connection.execute(statement, {"table_names": REQUIRED_TABLES})
                existing_tables = set(table_result.scalars())

            return self._health_result(
                started_at,
                reachable=True,
                readonly=readonly,
                tables_ready=existing_tables == set(REQUIRED_TABLES),
            )
        except Exception as exc:
            logger.warning(f"企业尽调 MySQL 健康检查失败: {type(exc).__name__}")
            return self._health_result(started_at, reachable=False, readonly=False, tables_ready=False)

    async def close(self) -> None:
        """释放进程级连接池并清空运行指标。"""
        if self._engine is not None:
            await self._engine.dispose()
        self._engine = None
        self._config = None
        self._pool_timeouts = 0
        self._pool_wait_ms = 0.0

    def pool_health(self) -> MySQLPoolHealth:
        """返回不发起查询的连接池即时状态和累计等待指标。"""
        pool_size = 0
        checked_out = 0
        overflow = 0
        if self._engine is not None:
            pool = self._engine.pool
            pool_size = pool.size()
            checked_out = pool.checkedout()
            overflow = max(0, pool.overflow())
        return MySQLPoolHealth(
            size=pool_size,
            checked_out=checked_out,
            overflow=overflow,
            wait_ms=round(self._pool_wait_ms, 3),
            timeouts=self._pool_timeouts,
        )

    def _health_result(
        self,
        started_at: float,
        *,
        reachable: bool,
        readonly: bool,
        tables_ready: bool,
    ) -> MySQLHealth:
        """装配不含连接详情的健康检查结果。"""
        return MySQLHealth(
            configured=self.configured,
            reachable=reachable,
            readonly=readonly,
            required_tables_ready=tables_ready,
            latency_ms=round((time.perf_counter() - started_at) * 1000),
            pool=self.pool_health(),
        )


def ensure_readonly_statement(statement: Any) -> None:
    """在 Repository 边界拒绝所有非查询 SQLAlchemy 语句。"""
    if isinstance(statement, TextClause):
        ensure_readonly_sql(statement.text)
        return
    if not getattr(statement, "is_select", False):
        raise ReadonlyViolationError("企业尽调数据源仅允许执行查询语句")


def ensure_readonly_sql(statement: str) -> None:
    """在 Engine 边界校验原始 SQL 不包含写入或危险读取。"""
    normalized = _LEADING_SQL_COMMENTS.sub("", statement).strip()
    if not normalized or ";" in normalized:
        raise ReadonlyViolationError("企业尽调数据源不允许空语句或多语句 SQL")

    command = normalized.split(None, 1)[0].upper()
    if command not in {"SELECT", "SHOW", "DESCRIBE", "EXPLAIN"}:
        raise ReadonlyViolationError("企业尽调数据源仅允许执行查询语句")
    if any(pattern.search(normalized) for pattern in _BLOCKED_SELECT_PATTERNS):
        raise ReadonlyViolationError("企业尽调数据源拒绝带锁、文件或导出副作用的查询")


def _install_session_readonly(engine: Engine, statement_timeout_seconds: int) -> None:
    """为每个新 DBAPI 连接设置只读事务和执行超时。"""
    timeout_ms = statement_timeout_seconds * 1000

    def configure_connection(dbapi_connection, _connection_record) -> None:
        """初始化单个 MySQL 会话的只读属性。"""
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("SET SESSION TRANSACTION READ ONLY")
            cursor.execute(f"SET SESSION MAX_EXECUTION_TIME = {timeout_ms}")
        finally:
            cursor.close()

    event.listen(engine, "connect", configure_connection)


def _install_readonly_sql_guard(engine: Engine) -> None:
    """为 Engine 安装不可绕过的 SQL 文本只读校验。"""

    def guard_sql(_conn, _cursor, statement, _parameters, _context, _executemany) -> None:
        """在 SQL 到达 DBAPI 前阻止非只读语句。"""
        ensure_readonly_sql(statement)

    event.listen(engine, "before_cursor_execute", guard_sql)


async def _read_readonly_state(connection: ReadonlyConnection) -> bool:
    """兼容读取 MySQL 新旧版本的会话只读状态。"""
    try:
        result = await connection.execute(text("SELECT @@session.transaction_read_only"))
        value = result.scalar_one()
    except Exception:
        result = await connection.execute(text("SELECT @@session.tx_read_only"))
        value = result.scalar_one()
    return bool(value)


company_due_diligence_mysql = CompanyDueDiligenceMySQL()
