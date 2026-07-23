"""Shared pytest fixtures."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest_asyncio

from src.db.client import DBClient
from src.db.schema import ALL_MIGRATIONS


def build_migrated_db(path: Path) -> None:
    """Apply every migration in ALL_MIGRATIONS to a fresh sqlite3 file.

    Sync counterpart to src.db.migrations.run_migrations, for dashboard-layer
    tests that read/write via plain sqlite3 (src/dashboard/db.py) rather than
    the async DBClient. Keeps hand-rolled test fixtures from drifting out of
    sync with the real schema as migrations are added.
    """
    con = sqlite3.connect(str(path))
    try:
        for _version, sql in ALL_MIGRATIONS:
            con.executescript(sql)
        con.commit()
    finally:
        con.close()


@pytest_asyncio.fixture
async def db_client(tmp_path: Path):
    """Fresh DBClient backed by a temp SQLite file. Migrations applied on connect()."""
    client = DBClient(tmp_path / "test.db")
    await client.connect()
    # Seed a parent campaign so FK-constrained ad_metrics inserts can succeed.
    await client.execute(
        "INSERT INTO campaigns (id, source, name, status) VALUES (?, ?, ?, ?)",
        ("c_1", "meta_ads", "Test Campaign", "ACTIVE"),
    )
    try:
        yield client
    finally:
        await client.close()
