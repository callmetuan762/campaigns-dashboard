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


def test_migration_006_creates_mmm_results_table():
    """DASH-12: MIGRATION_006_PHASE8 creates mmm_results with required columns.

    Runs synchronously (no db_client fixture needed) — applies the migration SQL
    directly to a fresh temp SQLite DB and verifies the table + columns.
    """
    import os
    import sqlite3
    import tempfile

    from src.db.schema import ALL_MIGRATIONS

    # 1. Migration is in the registry under the expected name.
    names = [m[0] for m in ALL_MIGRATIONS]
    assert "006_phase8" in names, f"Migration 006 not in ALL_MIGRATIONS: {names}"

    # 2. Apply the 006 migration SQL to a fresh temp DB and check the schema.
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row
        sql = next(m[1] for m in ALL_MIGRATIONS if m[0] == "006_phase8")
        con.executescript(sql)
        con.commit()

        # Table exists.
        rows = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='mmm_results'"
        ).fetchall()
        assert len(rows) == 1, "mmm_results table must exist after migration 006"

        # All required columns present.
        cols = {r["name"] for r in con.execute("PRAGMA table_info(mmm_results)")}
        required = {
            "id",
            "run_date",
            "weeks_of_data",
            "media_pct",
            "baseline_pct",
            "incremental_roas_per_1k",
            "optimal_daily_spend",
            "theta",
            "km",
            "n",
            "maturity_label",
            "created_at",
        }
        missing = required - cols
        assert not missing, f"Missing columns in mmm_results: {missing}"

        # Index exists for run_date DESC lookups.
        idx_rows = con.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND tbl_name='mmm_results'"
        ).fetchall()
        idx_names = {r["name"] for r in idx_rows}
        assert "idx_mmm_results_run_date" in idx_names, (
            f"Expected index idx_mmm_results_run_date, found: {idx_names}"
        )

        con.close()
    finally:
        try:
            os.unlink(db_path)
        except OSError:
            pass


def test_migration_006_mmm_results_accepts_insert():
    """Migration 006: schema accepts a realistic INSERT (column types correct)."""
    import os
    import sqlite3
    import tempfile

    from src.db.schema import ALL_MIGRATIONS

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        con = sqlite3.connect(db_path)
        sql = next(m[1] for m in ALL_MIGRATIONS if m[0] == "006_phase8")
        con.executescript(sql)
        con.commit()

        con.execute(
            "INSERT INTO mmm_results ("
            "run_date, weeks_of_data, media_pct, baseline_pct, "
            "incremental_roas_per_1k, optimal_daily_spend, theta, km, n, maturity_label"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "2026-05-24",
                8,
                42.3,
                57.7,
                5.2,
                350.0,
                0.5,
                100.0,
                1.5,
                "early",
            ),
        )
        con.commit()

        # incremental_roas_per_1k is nullable.
        con.execute(
            "INSERT INTO mmm_results ("
            "run_date, weeks_of_data, media_pct, baseline_pct, "
            "incremental_roas_per_1k, optimal_daily_spend, theta, km, n, maturity_label"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "2026-05-25",
                5,
                30.0,
                70.0,
                None,
                200.0,
                0.3,
                80.0,
                1.2,
                "directional_only",
            ),
        )
        con.commit()

        rows = con.execute(
            "SELECT COUNT(*) AS cnt FROM mmm_results"
        ).fetchone()
        assert rows[0] == 2, "Both inserts must succeed"
        con.close()
    finally:
        try:
            os.unlink(db_path)
        except OSError:
            pass
