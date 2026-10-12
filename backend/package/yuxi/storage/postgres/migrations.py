"""PostgreSQL 版本迁移入口。

首个版本只登记现有启动 DDL 已完成的基线，不修改历史业务表。后续 schema
变更必须新增有序版本，并在同一数据库 advisory lock 内执行。
"""

from __future__ import annotations

from sqlalchemy import text

MIGRATION_TABLE = "yuxi_schema_migrations"
MIGRATION_LOCK_KEY = "yuxi-schema-migrations"
BASELINE_VERSION = 1


async def run_versioned_migrations(engine) -> None:
    """在数据库锁内幂等登记并执行版本迁移。"""
    async with engine.begin() as connection:
        await connection.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS {MIGRATION_TABLE} (
                    version INTEGER PRIMARY KEY,
                    name VARCHAR(128) NOT NULL,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
        )
        await connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
            {"lock_key": MIGRATION_LOCK_KEY},
        )
        result = await connection.execute(text(f"SELECT version FROM {MIGRATION_TABLE}"))
        applied_versions = {int(row[0]) for row in result}
        if BASELINE_VERSION not in applied_versions:
            await connection.execute(
                text(f"INSERT INTO {MIGRATION_TABLE} (version, name) VALUES (:version, :name)"),
                {"version": BASELINE_VERSION, "name": "startup-ddl-baseline"},
            )
