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


def _apply_migration_to_fresh_db(version: str):
    """Apply ALL migrations up to and including `version` to a fresh temp SQLite DB.

    Applying the full chain (not just the target migration in isolation) matches how
    run_migrations() actually runs in production and catches ordering bugs (e.g. a
    migration that assumes a column added by an earlier one).
    """
    import os
    import sqlite3
    import tempfile

    from src.db.schema import ALL_MIGRATIONS

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    for name, sql in ALL_MIGRATIONS:
        con.executescript(sql)
        con.commit()
        if name == version:
            break
    return con, db_path


def test_migration_010_creates_ga4_events_table():
    """Funnel v3: MIGRATION_010_GA4_EVENTS creates ga4_events with the documented schema."""
    import os

    from src.db.schema import ALL_MIGRATIONS

    names = [m[0] for m in ALL_MIGRATIONS]
    assert "010_ga4_events" in names, f"Migration 010 not in ALL_MIGRATIONS: {names}"

    con, db_path = _apply_migration_to_fresh_db("010_ga4_events")
    try:
        rows = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='ga4_events'"
        ).fetchall()
        assert len(rows) == 1, "ga4_events table must exist after migration 010"

        cols = {r["name"] for r in con.execute("PRAGMA table_info(ga4_events)")}
        required = {"event_name", "date", "campaign_utm", "lp_slug", "event_count", "fetched_at"}
        missing = required - cols
        assert not missing, f"Missing columns in ga4_events: {missing}"

        idx_names = {
            r["name"]
            for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='ga4_events'"
            )
        }
        assert "idx_ga4_events_date" in idx_names
        assert "idx_ga4_events_campaign" in idx_names
        assert "idx_ga4_events_lp_slug" in idx_names
    finally:
        con.close()
        try:
            os.unlink(db_path)
        except OSError:
            pass


def test_migration_010_ga4_events_pk_dedupes_on_conflict():
    """Composite PK (event_name, date, campaign_utm, lp_slug) with '' defaults dedupes."""
    import os

    con, db_path = _apply_migration_to_fresh_db("010_ga4_events")
    try:
        con.execute(
            "INSERT INTO ga4_events (event_name, date, campaign_utm, lp_slug, event_count) "
            "VALUES ('purchase', '2026-05-18', 'nowa_launch', 'routine', 5)"
        )
        con.commit()
        con.execute(
            "INSERT INTO ga4_events (event_name, date, campaign_utm, lp_slug, event_count) "
            "VALUES ('purchase', '2026-05-18', 'nowa_launch', 'routine', 9) "
            "ON CONFLICT(event_name, date, campaign_utm, lp_slug) DO UPDATE SET event_count=excluded.event_count"
        )
        con.commit()
        rows = con.execute(
            "SELECT event_count FROM ga4_events WHERE event_name='purchase' AND campaign_utm='nowa_launch'"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["event_count"] == 9
    finally:
        con.close()
        try:
            os.unlink(db_path)
        except OSError:
            pass


def test_migration_011_adds_meta_funnel_v3_columns_to_ad_metrics():
    """Funnel v3: MIGRATION_011_META_FUNNEL_V3 adds LPV/video/checkout columns."""
    import os

    from src.db.schema import ALL_MIGRATIONS

    names = [m[0] for m in ALL_MIGRATIONS]
    assert "011_meta_funnel_v3" in names, f"Migration 011 not in ALL_MIGRATIONS: {names}"

    con, db_path = _apply_migration_to_fresh_db("011_meta_funnel_v3")
    try:
        cols = {r["name"] for r in con.execute("PRAGMA table_info(ad_metrics)")}
        required = {
            "landing_page_views",
            "video_3s_views",
            "video_thruplay",
            "meta_begin_checkout",
            "meta_cost_per_begin_checkout",
            "meta_add_to_cart",
            "meta_leads",
        }
        missing = required - cols
        assert not missing, f"Missing funnel-v3 columns in ad_metrics: {missing}"
    finally:
        con.close()
        try:
            os.unlink(db_path)
        except OSError:
            pass


def test_migration_012_creates_shopify_orders_table():
    """Funnel v3: MIGRATION_012_SHOPIFY_ORDERS creates shopify_orders with the documented schema."""
    import os

    from src.db.schema import ALL_MIGRATIONS

    names = [m[0] for m in ALL_MIGRATIONS]
    assert "012_shopify_orders" in names, f"Migration 012 not in ALL_MIGRATIONS: {names}"

    con, db_path = _apply_migration_to_fresh_db("012_shopify_orders")
    try:
        rows = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='shopify_orders'"
        ).fetchall()
        assert len(rows) == 1, "shopify_orders table must exist after migration 012"

        cols = {r["name"] for r in con.execute("PRAGMA table_info(shopify_orders)")}
        required = {
            "order_id", "created_at", "order_date", "total_price", "financial_status",
            "utm_source", "utm_campaign", "utm_content", "lp_slug",
            "landing_site", "referring_site", "fetched_at",
        }
        missing = required - cols
        assert not missing, f"Missing columns in shopify_orders: {missing}"

        idx_names = {
            r["name"]
            for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='shopify_orders'"
            )
        }
        assert "idx_shopify_orders_date" in idx_names
        assert "idx_shopify_orders_utm_campaign" in idx_names
        assert "idx_shopify_orders_lp_slug" in idx_names
    finally:
        con.close()
        try:
            os.unlink(db_path)
        except OSError:
            pass


def test_full_migration_chain_applies_cleanly_on_fresh_db():
    """All migrations (001..012) apply in order on a brand-new DB without error."""
    import os
    import sqlite3
    import tempfile

    from src.db.schema import ALL_MIGRATIONS

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        con = sqlite3.connect(db_path)
        for _name, sql in ALL_MIGRATIONS:
            con.executescript(sql)
            con.commit()
        # sanity: every funnel-v3 table/column exists at the end of the chain
        cols = {r[1] for r in con.execute("PRAGMA table_info(ad_metrics)")}
        assert "meta_begin_checkout" in cols
        tables = {
            r[0]
            for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert "ga4_events" in tables
        assert "shopify_orders" in tables
        assert "pixel_health" in tables
        assert "ga4_daily_totals" in tables
        con.close()
    finally:
        try:
            os.unlink(db_path)
        except OSError:
            pass


async def test_run_migrations_applies_all_funnel_v3_migrations(db_client):
    """run_migrations() (the real async migration runner) picks up 010/011/012 too."""
    rows = await db_client.fetch_all("SELECT version FROM schema_version")
    applied = {r["version"] for r in rows}
    assert {"010_ga4_events", "011_meta_funnel_v3", "012_shopify_orders"} <= applied


def test_migration_013_creates_pixel_health_table():
    """Phase C: MIGRATION_013_PIXEL_HEALTH creates pixel_health with the documented schema."""
    import os

    from src.db.schema import ALL_MIGRATIONS

    names = [m[0] for m in ALL_MIGRATIONS]
    assert "013_pixel_health" in names, f"Migration 013 not in ALL_MIGRATIONS: {names}"

    con, db_path = _apply_migration_to_fresh_db("013_pixel_health")
    try:
        rows = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='pixel_health'"
        ).fetchall()
        assert len(rows) == 1, "pixel_health table must exist after migration 013"

        cols = {r["name"] for r in con.execute("PRAGMA table_info(pixel_health)")}
        required = {
            "date", "event_name", "browser_count", "server_count",
            "dedup_rate", "emq_score", "fetched_at",
        }
        missing = required - cols
        assert not missing, f"Missing columns in pixel_health: {missing}"

        idx_names = {
            r["name"]
            for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='pixel_health'"
            )
        }
        assert "idx_pixel_health_date" in idx_names
    finally:
        con.close()
        try:
            os.unlink(db_path)
        except OSError:
            pass


def test_migration_013_pixel_health_pk_dedupes_on_conflict():
    """Composite PK (date, event_name) dedupes on conflict, like ga4_events."""
    import os

    con, db_path = _apply_migration_to_fresh_db("013_pixel_health")
    try:
        con.execute(
            "INSERT INTO pixel_health (date, event_name, browser_count, server_count) "
            "VALUES ('2026-05-18', 'purchase', 10, 8)"
        )
        con.commit()
        con.execute(
            "INSERT INTO pixel_health (date, event_name, browser_count, server_count) "
            "VALUES ('2026-05-18', 'purchase', 15, 9) "
            "ON CONFLICT(date, event_name) DO UPDATE SET "
            "browser_count=excluded.browser_count, server_count=excluded.server_count"
        )
        con.commit()
        rows = con.execute(
            "SELECT browser_count, server_count FROM pixel_health "
            "WHERE date='2026-05-18' AND event_name='purchase'"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["browser_count"] == 15
        assert rows[0]["server_count"] == 9
    finally:
        con.close()
        try:
            os.unlink(db_path)
        except OSError:
            pass


def test_migration_013_pixel_health_emq_and_dedup_nullable():
    """emq_score / dedup_rate must accept NULL (graceful degradation — EMQ best-effort)."""
    import os

    con, db_path = _apply_migration_to_fresh_db("013_pixel_health")
    try:
        con.execute(
            "INSERT INTO pixel_health (date, event_name, browser_count, server_count, "
            "dedup_rate, emq_score) VALUES ('2026-05-18', 'lead_submit', 5, 3, NULL, NULL)"
        )
        con.commit()
        row = con.execute(
            "SELECT dedup_rate, emq_score FROM pixel_health "
            "WHERE date='2026-05-18' AND event_name='lead_submit'"
        ).fetchone()
        assert row["dedup_rate"] is None
        assert row["emq_score"] is None
    finally:
        con.close()
        try:
            os.unlink(db_path)
        except OSError:
            pass


def test_migration_014_creates_ga4_daily_totals_table():
    """Session multi-counting fix: MIGRATION_014_GA4_DAILY_TOTALS creates
    ga4_daily_totals with the documented schema."""
    import os

    from src.db.schema import ALL_MIGRATIONS

    names = [m[0] for m in ALL_MIGRATIONS]
    assert "014_ga4_daily_totals" in names, f"Migration 014 not in ALL_MIGRATIONS: {names}"

    con, db_path = _apply_migration_to_fresh_db("014_ga4_daily_totals")
    try:
        rows = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='ga4_daily_totals'"
        ).fetchall()
        assert len(rows) == 1, "ga4_daily_totals table must exist after migration 014"

        cols = {r["name"] for r in con.execute("PRAGMA table_info(ga4_daily_totals)")}
        required = {"date", "sessions", "fetched_at"}
        missing = required - cols
        assert not missing, f"Missing columns in ga4_daily_totals: {missing}"

        pk_cols = {
            r["name"]
            for r in con.execute("PRAGMA table_info(ga4_daily_totals)")
            if r["pk"] > 0
        }
        assert pk_cols == {"date"}, f"Expected PK on date only, got: {pk_cols}"
    finally:
        con.close()
        try:
            os.unlink(db_path)
        except OSError:
            pass


def test_migration_014_ga4_daily_totals_pk_dedupes_on_conflict():
    """Single-column PK (date) dedupes on UPSERT, like the other funnel-v3 tables."""
    import os

    con, db_path = _apply_migration_to_fresh_db("014_ga4_daily_totals")
    try:
        con.execute(
            "INSERT INTO ga4_daily_totals (date, sessions) VALUES ('2026-07-21', 1000)"
        )
        con.commit()
        con.execute(
            "INSERT INTO ga4_daily_totals (date, sessions) VALUES ('2026-07-21', 1803) "
            "ON CONFLICT(date) DO UPDATE SET sessions=excluded.sessions"
        )
        con.commit()
        rows = con.execute(
            "SELECT sessions FROM ga4_daily_totals WHERE date='2026-07-21'"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["sessions"] == 1803
    finally:
        con.close()
        try:
            os.unlink(db_path)
        except OSError:
            pass


async def test_run_migrations_applies_migration_014(db_client):
    """run_migrations() (the real async migration runner) picks up 014 too."""
    rows = await db_client.fetch_all("SELECT version FROM schema_version")
    applied = {r["version"] for r in rows}
    assert "014_ga4_daily_totals" in applied


def test_migration_015_adds_objective_column_to_campaigns():
    """Meta campaign objective: MIGRATION_015_CAMPAIGN_OBJECTIVE adds a nullable
    `objective` TEXT column to campaigns."""
    import os

    from src.db.schema import ALL_MIGRATIONS

    names = [m[0] for m in ALL_MIGRATIONS]
    assert "015_campaign_objective" in names, f"Migration 015 not in ALL_MIGRATIONS: {names}"

    con, db_path = _apply_migration_to_fresh_db("015_campaign_objective")
    try:
        cols = {r["name"] for r in con.execute("PRAGMA table_info(campaigns)")}
        assert "objective" in cols, "campaigns.objective column must exist after migration 015"

        # Nullable, no default required.
        con.execute(
            "INSERT INTO campaigns (id, source, name, status) VALUES (?, ?, ?, ?)",
            ("c_pre_migration", "meta_ads", "Pre-existing Campaign", "ACTIVE"),
        )
        con.commit()
        row = con.execute(
            "SELECT objective FROM campaigns WHERE id = 'c_pre_migration'"
        ).fetchone()
        assert row["objective"] is None

        # A row with an objective set works too.
        con.execute(
            "INSERT INTO campaigns (id, source, name, status, objective) VALUES (?, ?, ?, ?, ?)",
            ("c_with_objective", "meta_ads", "Sales Campaign", "ACTIVE", "OUTCOME_SALES"),
        )
        con.commit()
        row2 = con.execute(
            "SELECT objective FROM campaigns WHERE id = 'c_with_objective'"
        ).fetchone()
        assert row2["objective"] == "OUTCOME_SALES"
    finally:
        con.close()
        try:
            os.unlink(db_path)
        except OSError:
            pass


async def test_run_migrations_applies_migration_015(db_client):
    """run_migrations() (the real async migration runner) picks up 015 too."""
    rows = await db_client.fetch_all("SELECT version FROM schema_version")
    applied = {r["version"] for r in rows}
    assert "015_campaign_objective" in applied


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
