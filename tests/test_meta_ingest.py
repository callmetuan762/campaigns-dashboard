"""Prove META-03, META-05: campaign upsert idempotency and circuit breaker."""
from __future__ import annotations
from unittest.mock import MagicMock
import pytest
from src.meta.ingest import _check_circuit_breaker, register_job_resources

pytestmark = pytest.mark.asyncio


async def test_upsert_campaign_idempotent(db_client):
    """META-05: Re-upserting same campaign_id does not duplicate rows."""
    row = {"id": "c_test", "source": "meta_ads", "name": "Test Campaign", "status": "ACTIVE"}
    await db_client.upsert_campaign([row])
    await db_client.upsert_campaign([row])
    rows = await db_client.fetch_all("SELECT * FROM campaigns WHERE id = 'c_test'")
    assert len(rows) == 1


async def test_upsert_campaign_updates_name(db_client):
    """META-05: Upserting same campaign_id with new name updates the row."""
    await db_client.upsert_campaign([
        {"id": "c_rename", "source": "meta_ads", "name": "Old Name", "status": "ACTIVE"}
    ])
    await db_client.upsert_campaign([
        {"id": "c_rename", "source": "meta_ads", "name": "New Name", "status": "ACTIVE"}
    ])
    row = await db_client.fetch_one("SELECT name FROM campaigns WHERE id = 'c_rename'")
    assert row["name"] == "New Name"


async def test_circuit_breaker_not_triggered_under_threshold(db_client):
    """D-08: Circuit breaker must NOT trigger with < 3 consecutive failures."""
    log_id = await db_client.log_ingestion_start("meta_ads")
    await db_client.log_ingestion_finish(log_id, "failed", error="test error")
    result = await _check_circuit_breaker(db_client, "meta_ads", threshold=3)
    assert result is False


async def test_register_job_resources_sets_globals():
    """Pattern 1: register_job_resources sets module globals for APScheduler."""
    import src.meta.ingest as ingest_module
    mock_bot = MagicMock()
    mock_db = MagicMock()
    mock_settings = MagicMock()
    register_job_resources(mock_bot, mock_db, mock_settings)
    assert ingest_module._bot is mock_bot
    assert ingest_module._db is mock_db
    assert ingest_module._settings is mock_settings
    # Clean up
    ingest_module._bot = None
    ingest_module._db = None
    ingest_module._settings = None
