"""Shared pytest fixtures."""
from __future__ import annotations

from pathlib import Path

import pytest_asyncio

from src.db.client import DBClient


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
