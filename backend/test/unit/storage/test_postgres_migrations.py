from __future__ import annotations

import pytest

from yuxi.storage.postgres.migrations import BASELINE_VERSION, MIGRATION_TABLE, MIGRATIONS, run_versioned_migrations


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def __iter__(self):
        return iter(self.rows)


class _Connection:
    def __init__(self):
        self.statements: list[str] = []
        self.applied: list[int] = []

    async def execute(self, statement, params=None):
        sql = str(statement)
        self.statements.append(sql)
        if sql.startswith("SELECT version"):
            return _Result([(version,) for version in self.applied])
        if sql.startswith("INSERT INTO"):
            self.applied.append(params["version"])
        return _Result([])


class _Begin:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Engine:
    def __init__(self, connection):
        self.connection = connection

    def begin(self):
        return _Begin(self.connection)


@pytest.mark.asyncio
async def test_versioned_migrations_registers_baseline_once():
    connection = _Connection()
    engine = _Engine(connection)

    await run_versioned_migrations(engine)
    await run_versioned_migrations(engine)

    assert sum(MIGRATION_TABLE in statement and "CREATE TABLE" in statement for statement in connection.statements) == 2
    # 基线和每个版本迁移都只登记、执行一次，按版本号顺序执行。
    assert connection.applied == [BASELINE_VERSION, *(version for version, _, _ in MIGRATIONS)]
    for _, _, migration in MIGRATIONS:
        assert connection.statements.count(migration) == 1


@pytest.mark.asyncio
async def test_versioned_migrations_propagates_database_failure():
    class FailingConnection:
        async def execute(self, _statement, _params=None):
            raise RuntimeError("migration probe failed")

    class FailingBegin:
        async def __aenter__(self):
            return FailingConnection()

        async def __aexit__(self, *_args):
            return False

    class FailingEngine:
        def begin(self):
            return FailingBegin()

    with pytest.raises(RuntimeError, match="migration probe failed"):
        await run_versioned_migrations(FailingEngine())
