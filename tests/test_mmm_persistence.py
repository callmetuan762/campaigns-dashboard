"""Tests for MIGRATION_006_PHASE8 + DBClient.upsert_mmm_result / get_mmm_results
and dashboard db.get_latest_mmm_result / get_weekly_contributions (DASH-12).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

pytestmark_async = pytest.mark.asyncio


# --------------------------------------------------------------------------
# Migration 006 — schema
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_migration_006_creates_mmm_results_table(db_client) -> None:
    """Fresh DB has mmm_results table after migrations apply."""
    rows = await db_client.fetch_all(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='mmm_results'"
    )
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_migration_006_mmm_results_has_all_columns(db_client) -> None:
    """mmm_results has all D-12 columns."""
    rows = await db_client.fetch_all("PRAGMA table_info(mmm_results)")
    cols = {r["name"] for r in rows}
    expected = {
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
    assert expected <= cols


def test_migration_006_in_all_migrations() -> None:
    """ALL_MIGRATIONS list contains ('006_phase8', MIGRATION_006_PHASE8)."""
    from src.db.schema import ALL_MIGRATIONS, MIGRATION_006_PHASE8

    by_name = dict(ALL_MIGRATIONS)
    assert "006_phase8" in by_name
    assert by_name["006_phase8"] is MIGRATION_006_PHASE8
    assert "CREATE TABLE IF NOT EXISTS mmm_results" in MIGRATION_006_PHASE8


# --------------------------------------------------------------------------
# DBClient.upsert_mmm_result + get_mmm_results
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_upsert_mmm_result_inserts_one_row(db_client) -> None:
    """DBClient.upsert_mmm_result inserts one row per call (append-only)."""
    from src.mmm.model import MMMResult

    result = MMMResult(
        run_date="2026-05-18",
        weeks_of_data=5,
        media_pct=42.3,
        baseline_pct=57.7,
        incremental_roas_per_1k=12.5,
        optimal_daily_spend=350.50,
        theta=0.4,
        km=180.123,
        n=1.45,
        maturity_label="directional_only",
    )
    await db_client.upsert_mmm_result(result)

    rows = await db_client.fetch_all("SELECT * FROM mmm_results")
    assert len(rows) == 1
    assert rows[0]["run_date"] == "2026-05-18"
    assert rows[0]["media_pct"] == pytest.approx(42.3)
    assert rows[0]["maturity_label"] == "directional_only"


@pytest.mark.asyncio
async def test_upsert_mmm_result_appends_on_duplicate_run_date(db_client) -> None:
    """No ON CONFLICT — same run_date inserted twice produces 2 rows."""
    from src.mmm.model import MMMResult

    base = MMMResult(
        run_date="2026-05-18",
        weeks_of_data=5,
        media_pct=42.3,
        baseline_pct=57.7,
        incremental_roas_per_1k=12.5,
        optimal_daily_spend=350.0,
        theta=0.4,
        km=180.0,
        n=1.45,
        maturity_label="directional_only",
    )
    await db_client.upsert_mmm_result(base)
    await db_client.upsert_mmm_result(base)
    rows = await db_client.fetch_all("SELECT * FROM mmm_results")
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_get_mmm_results_orders_by_run_date_desc(db_client) -> None:
    """get_mmm_results returns rows ordered by run_date DESC."""
    from src.mmm.model import MMMResult

    for date in ["2026-05-04", "2026-05-18", "2026-05-11"]:
        await db_client.upsert_mmm_result(
            MMMResult(
                run_date=date,
                weeks_of_data=5,
                media_pct=40.0,
                baseline_pct=60.0,
                incremental_roas_per_1k=None,
                optimal_daily_spend=300.0,
                theta=0.5,
                km=150.0,
                n=1.5,
                maturity_label="directional_only",
            )
        )
    rows = await db_client.get_mmm_results(limit=10)
    dates = [r["run_date"] for r in rows]
    assert dates == ["2026-05-18", "2026-05-11", "2026-05-04"]


@pytest.mark.asyncio
async def test_get_mmm_results_respects_limit(db_client) -> None:
    """get_mmm_results LIMIT param caps row count."""
    from src.mmm.model import MMMResult

    for i in range(5):
        await db_client.upsert_mmm_result(
            MMMResult(
                run_date=f"2026-05-{10 + i:02d}",
                weeks_of_data=5,
                media_pct=40.0,
                baseline_pct=60.0,
                incremental_roas_per_1k=None,
                optimal_daily_spend=300.0,
                theta=0.5,
                km=150.0,
                n=1.5,
                maturity_label="directional_only",
            )
        )
    rows = await db_client.get_mmm_results(limit=2)
    assert len(rows) == 2


# --------------------------------------------------------------------------
# dashboard db.get_latest_mmm_result
# --------------------------------------------------------------------------

def _make_mmm_fixture_db(path: Path, *, with_mmm_results: bool = True) -> None:
    """Create a SQLite DB with ad_metrics + (optional) mmm_results schema."""
    con = sqlite3.connect(str(path))
    con.executescript("""
        CREATE TABLE campaigns (id TEXT PRIMARY KEY, source TEXT, name TEXT,
                                status TEXT, created_at TEXT);
        CREATE TABLE ad_metrics (
            campaign_id TEXT NOT NULL, date TEXT NOT NULL,
            ad_set_id TEXT NOT NULL DEFAULT '', ad_id TEXT NOT NULL DEFAULT '',
            spend REAL, impressions INTEGER, clicks INTEGER, ctr REAL,
            cpc REAL, cpm REAL, roas REAL,
            meta_purchases_7dclick INTEGER, meta_cost_per_purchase REAL,
            reach INTEGER, frequency REAL,
            meta_form_submit_deposit INTEGER NOT NULL DEFAULT 0,
            fetched_at TEXT,
            PRIMARY KEY (campaign_id, date, ad_set_id, ad_id)
        );
        INSERT INTO campaigns VALUES ('c1','meta_ads','Brand','ACTIVE','2026-05-01');
    """)
    if with_mmm_results:
        con.executescript("""
            CREATE TABLE mmm_results (
                id                       INTEGER PRIMARY KEY AUTOINCREMENT,
                run_date                 TEXT NOT NULL,
                weeks_of_data            INTEGER NOT NULL,
                media_pct                REAL NOT NULL,
                baseline_pct             REAL NOT NULL,
                incremental_roas_per_1k  REAL,
                optimal_daily_spend      REAL NOT NULL,
                theta                    REAL NOT NULL,
                km                       REAL NOT NULL,
                n                        REAL NOT NULL,
                maturity_label           TEXT NOT NULL,
                created_at               TEXT NOT NULL DEFAULT (datetime('now'))
            );
        """)
    con.commit()
    con.close()


def test_get_latest_mmm_result_returns_none_when_empty(tmp_path: Path) -> None:
    """No rows in mmm_results → None."""
    from src.dashboard.db import get_latest_mmm_result

    db = tmp_path / "metrics.db"
    _make_mmm_fixture_db(db, with_mmm_results=True)
    out = get_latest_mmm_result(db)
    assert out is None


def test_get_latest_mmm_result_returns_none_when_table_missing(tmp_path: Path) -> None:
    """Table not present (fresh DB before migration) → None, no raise."""
    from src.dashboard.db import get_latest_mmm_result

    db = tmp_path / "metrics.db"
    _make_mmm_fixture_db(db, with_mmm_results=False)
    out = get_latest_mmm_result(db)
    assert out is None


def test_get_latest_mmm_result_returns_most_recent(tmp_path: Path) -> None:
    """Latest run_date row returned as dict."""
    from src.dashboard.db import get_latest_mmm_result

    db = tmp_path / "metrics.db"
    _make_mmm_fixture_db(db, with_mmm_results=True)
    con = sqlite3.connect(str(db))
    con.executescript("""
        INSERT INTO mmm_results
            (run_date, weeks_of_data, media_pct, baseline_pct,
             incremental_roas_per_1k, optimal_daily_spend, theta, km, n,
             maturity_label, created_at)
        VALUES
            ('2026-05-04', 4, 35.0, 65.0, 8.0, 200.0, 0.3, 150.0, 1.2,
             'directional_only', '2026-05-04T23:00:00'),
            ('2026-05-18', 6, 45.0, 55.0, 12.0, 350.0, 0.4, 200.0, 1.5,
             'directional_only', '2026-05-18T23:00:00');
    """)
    con.commit()
    con.close()

    out = get_latest_mmm_result(db)
    assert out is not None
    assert out["run_date"] == "2026-05-18"
    assert out["media_pct"] == pytest.approx(45.0)
    assert out["maturity_label"] == "directional_only"


# --------------------------------------------------------------------------
# dashboard db.get_weekly_contributions
# --------------------------------------------------------------------------

def test_get_weekly_contributions_returns_empty_when_no_mmm_result(tmp_path: Path) -> None:
    """No mmm_results → return [] (no model to split by)."""
    from src.dashboard.db import get_weekly_contributions

    db = tmp_path / "metrics.db"
    _make_mmm_fixture_db(db, with_mmm_results=True)
    out = get_weekly_contributions(db, weeks=12)
    assert out == []


def test_get_weekly_contributions_splits_total_by_media_pct(tmp_path: Path) -> None:
    """Total deposits per ISO-week split into baseline + media via media_pct."""
    from src.dashboard.db import get_weekly_contributions

    db = tmp_path / "metrics.db"
    _make_mmm_fixture_db(db, with_mmm_results=True)
    con = sqlite3.connect(str(db))
    con.executescript("""
        INSERT INTO mmm_results
            (run_date, weeks_of_data, media_pct, baseline_pct,
             incremental_roas_per_1k, optimal_daily_spend, theta, km, n,
             maturity_label, created_at)
        VALUES
            ('2026-05-18', 6, 50.0, 50.0, 12.0, 350.0, 0.4, 200.0, 1.5,
             'directional_only', '2026-05-18T23:00:00');
        -- Two days in the same ISO week with campaign-level rows.
        INSERT INTO ad_metrics(campaign_id, date, ad_set_id, ad_id, spend,
                               meta_form_submit_deposit, fetched_at)
        VALUES
            ('c1','2026-05-04','','',100.0,10,'2026-05-05T00:00:00'),
            ('c1','2026-05-05','','',200.0,20,'2026-05-06T00:00:00');
    """)
    con.commit()
    con.close()

    out = get_weekly_contributions(db, weeks=12)
    assert len(out) >= 1
    # Total deposits = 30; media_pct=50.0 → media_deposits = 15, baseline = 15
    week = out[-1]  # last (ASC order → most recent)
    assert "week" in week
    assert "baseline_deposits" in week
    assert "media_deposits" in week
    # baseline + media should equal total deposits (30)
    assert week["baseline_deposits"] + week["media_deposits"] == pytest.approx(30.0)
    assert week["media_deposits"] == pytest.approx(15.0)


def test_get_weekly_contributions_orders_ascending_by_week(tmp_path: Path) -> None:
    """Output ordered ASC by week (oldest first) for stacked-bar chart consumption."""
    from src.dashboard.db import get_weekly_contributions

    db = tmp_path / "metrics.db"
    _make_mmm_fixture_db(db, with_mmm_results=True)
    con = sqlite3.connect(str(db))
    con.executescript("""
        INSERT INTO mmm_results
            (run_date, weeks_of_data, media_pct, baseline_pct,
             incremental_roas_per_1k, optimal_daily_spend, theta, km, n,
             maturity_label, created_at)
        VALUES
            ('2026-05-18', 6, 50.0, 50.0, 12.0, 350.0, 0.4, 200.0, 1.5,
             'directional_only', '2026-05-18T23:00:00');
        -- Three weeks of spend rows
        INSERT INTO ad_metrics(campaign_id, date, ad_set_id, ad_id, spend,
                               meta_form_submit_deposit, fetched_at)
        VALUES
            ('c1','2026-05-04','','',100.0,10,'2026-05-05T00:00:00'),
            ('c1','2026-05-11','','',150.0,15,'2026-05-12T00:00:00'),
            ('c1','2026-05-18','','',200.0,20,'2026-05-19T00:00:00');
    """)
    con.commit()
    con.close()

    out = get_weekly_contributions(db, weeks=12)
    weeks = [r["week"] for r in out]
    assert weeks == sorted(weeks)
