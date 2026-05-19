"""Prove MIGRATION_002_PHASE2: alert_log table created with correct structure."""
from __future__ import annotations
import pytest
from src.db.migrations import run_migrations

pytestmark = pytest.mark.asyncio


async def test_migration_002_creates_alert_log(db_client):
    """MIGRATION_002_PHASE2 must create the alert_log table."""
    rows = await db_client.fetch_all(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='alert_log'"
    )
    assert len(rows) > 0, "alert_log table must exist after MIGRATION_002_PHASE2"


async def test_alert_log_has_unique_constraint(db_client):
    """UNIQUE(alert_type, campaign_id, date) enforced at DB layer (D-18)."""
    await db_client.execute(
        "INSERT INTO alert_log (alert_type, campaign_id, date) VALUES (?, ?, ?)",
        ("SPEND_SPIKE", "c_1", "2026-05-18"),
    )
    is_new = await db_client.log_alert("SPEND_SPIKE", "c_1", "2026-05-18")
    assert is_new is False, "Duplicate alert must return False"


async def test_log_alert_new_returns_true(db_client):
    """First alert for a campaign/date returns True."""
    is_new = await db_client.log_alert("ROAS_DROP", "c_1", "2026-05-18")
    assert is_new is True


async def test_log_alert_different_date_is_new(db_client):
    """Same alert type + campaign on a different date is a NEW alert."""
    await db_client.log_alert("SPEND_SPIKE", "c_1", "2026-05-17")
    is_new = await db_client.log_alert("SPEND_SPIKE", "c_1", "2026-05-18")
    assert is_new is True, "Different date must be treated as new alert"
