"""Hand-rolled SQLite migration runner. No Alembic dependency.

INFRA-03: schema_version table tracks applied migrations; running run_migrations()
on an already-migrated DB is a no-op (idempotent).
"""
from __future__ import annotations

import aiosqlite
import structlog

from src.db.schema import ALL_MIGRATIONS

logger = structlog.get_logger(__name__)


async def applied_versions(conn: aiosqlite.Connection) -> set[str]:
    """Return the set of migration version names already applied to this DB."""
    # The schema_version table may not exist yet on a fresh DB.
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version ("
        "  version TEXT PRIMARY KEY,"
        "  applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP"
        ")"
    )
    await conn.commit()
    async with conn.execute("SELECT version FROM schema_version") as cur:
        return {row[0] async for row in cur}


async def run_migrations(conn: aiosqlite.Connection) -> list[str]:
    """Apply every migration in ALL_MIGRATIONS not yet recorded. Returns the list applied."""
    already = await applied_versions(conn)
    applied: list[str] = []
    for version, sql in ALL_MIGRATIONS:
        if version in already:
            logger.debug("migration_skip", version=version)
            continue
        logger.info("migration_apply", version=version)
        await conn.executescript(sql)
        await conn.execute(
            "INSERT OR REPLACE INTO schema_version (version) VALUES (?)",
            (version,),
        )
        await conn.commit()
        applied.append(version)
    return applied
