"""Unit tests for src/dashboard/db.py (DASH-01, DASH-02).

Tests the 7 sync sqlite3 query functions: sentinel filter, weighted ROAS,
deposits-DESC sort, LEFT JOIN behavior, and data freshness.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.dashboard.db import (
    get_kpi_summary, get_ga4_kpi, get_daily_trend, get_campaign_table,
    get_attribution_comparison, get_data_freshness, get_campaign_names,
    get_campaign_daily, get_campaign_objectives, objective_display_label,
    get_campaign_ga4_engagement,
)


def _make_fixture_db(path: Path) -> None:
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
            meta_begin_checkout INTEGER,
            fetched_at TEXT,
            PRIMARY KEY (campaign_id, date, ad_set_id, ad_id)
        );
        CREATE TABLE ga4_metrics (
            campaign_utm TEXT NOT NULL, date TEXT NOT NULL,
            sessions INTEGER, users INTEGER, new_users INTEGER,
            bounce_rate REAL, avg_engagement_time REAL,
            ga4_purchases_lastclick INTEGER, fetched_at TEXT,
            PRIMARY KEY (campaign_utm, date)
        );
        INSERT INTO campaigns VALUES
            ('c1','meta_ads','Brand','ACTIVE','2026-05-01'),
            ('c2','meta_ads','Convert','ACTIVE','2026-05-01');
        -- Campaign-level rows for Brand (2 days)
        INSERT INTO ad_metrics(campaign_id, date, ad_set_id, ad_id, spend,
                               impressions, clicks, roas,
                               meta_form_submit_deposit, fetched_at)
        VALUES
            ('c1','2026-05-01','','',100.0,1000,50,2.5,5,'2026-05-02T00:00:00'),
            ('c1','2026-05-02','','',150.0,1500,75,2.0,4,'2026-05-03T00:00:00'),
            ('c2','2026-05-01','','',200.0,2000,80,1.0,2,'2026-05-02T00:00:00');
        -- Ad-set row (must be excluded by sentinel filter)
        INSERT INTO ad_metrics(campaign_id, date, ad_set_id, ad_id, spend,
                               meta_form_submit_deposit, roas, fetched_at)
        VALUES ('c1','2026-05-01','set_1','',50.0,2,3.0,'2026-05-02T00:00:00');
        INSERT INTO ga4_metrics VALUES
            ('Brand','2026-05-01',500,400,300,0.30,45.0,3,'2026-05-02T00:00:00'),
            ('Brand','2026-05-02',600,500,400,0.28,50.0,5,'2026-05-03T00:00:00'),
            ('Convert','2026-05-01',800,700,500,0.40,30.0,2,'2026-05-02T00:00:00');
    """)
    con.commit()
    con.close()


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    p = tmp_path / "metrics.db"
    _make_fixture_db(p)
    return p


# --- get_kpi_summary -------------------------------------------------------

def test_kpi_total_spend_excludes_adset_rows(db: Path) -> None:
    """Sentinel filter: ad_set_id='' AND ad_id=''. The 50.0 ad-set row is excluded."""
    out = get_kpi_summary(db, "2026-05-01", "2026-05-02")
    # Campaign-level only: 100 + 150 + 200 = 450
    assert out["total_spend"] == pytest.approx(450.0)


def test_kpi_weighted_roas(db: Path) -> None:
    """ROAS = SUM(spend*roas)/SUM(spend), NOT AVG(roas)."""
    out = get_kpi_summary(db, "2026-05-01", "2026-05-02")
    # (100*2.5 + 150*2.0 + 200*1.0) / 450 = (250+300+200)/450 = 750/450 = 1.6667
    assert out["weighted_roas"] == pytest.approx(750.0 / 450.0, rel=1e-4)


def test_kpi_total_deposits(db: Path) -> None:
    out = get_kpi_summary(db, "2026-05-01", "2026-05-02")
    # 5 + 4 + 2 = 11 (ad-set row's 2 excluded)
    assert out["total_deposits"] == 11


def test_kpi_cpd(db: Path) -> None:
    out = get_kpi_summary(db, "2026-05-01", "2026-05-02")
    # 450 / 11
    assert out["cpd"] == pytest.approx(450.0 / 11.0, rel=1e-4)


def test_kpi_active_campaigns_distinct(db: Path) -> None:
    out = get_kpi_summary(db, "2026-05-01", "2026-05-02")
    assert out["active_campaigns"] == 2


# --- get_ga4_kpi -----------------------------------------------------------

def test_ga4_kpi_sessions(db: Path) -> None:
    out = get_ga4_kpi(db, "2026-05-01", "2026-05-02")
    # 500 + 600 + 800 = 1900
    assert out["total_sessions"] == 1900


# --- get_daily_trend -------------------------------------------------------

def test_daily_trend_one_row_per_date_ordered(db: Path) -> None:
    rows = get_daily_trend(db, "2026-05-01", "2026-05-02")
    dates = [r["date"] for r in rows]
    assert dates == sorted(dates)
    assert len(rows) == 2


def test_daily_trend_excludes_adset_rows(db: Path) -> None:
    rows = get_daily_trend(db, "2026-05-01", "2026-05-01")
    # Day 1 campaign-level spend: 100 + 200 = 300 (NOT 350)
    assert rows[0]["spend"] == pytest.approx(300.0)


# --- get_campaign_table ----------------------------------------------------

def test_campaign_table_sorted_by_deposits_desc(db: Path) -> None:
    rows = get_campaign_table(db, "2026-05-01", "2026-05-02")
    # Brand: 5+4=9 deposits, Convert: 2 deposits → Brand first
    assert rows[0]["campaign_name"] == "Brand"
    assert rows[0]["deposits"] == 9
    assert rows[1]["campaign_name"] == "Convert"


def test_campaign_table_has_all_columns(db: Path) -> None:
    rows = get_campaign_table(db, "2026-05-01", "2026-05-02")
    expected = {"campaign_name", "spend", "weighted_roas", "impressions",
                "deposits", "cpd", "ga4_sessions"}
    assert expected <= set(rows[0].keys())


def test_campaign_table_leads_columns_degrade_gracefully_pre_migration(db: Path) -> None:
    """The `db` fixture's ad_metrics table predates the meta_leads column
    (item 3, 2026-07-23 -- Overview Conversion metric picker). get_campaign_table
    must not raise sqlite3.OperationalError; it falls back to leads=0/
    cost_per_lead=None for every row instead."""
    rows = get_campaign_table(db, "2026-05-01", "2026-05-02")
    assert {"leads", "cost_per_lead"} <= set(rows[0].keys())
    for r in rows:
        assert r["leads"] == 0
        assert r["cost_per_lead"] is None


def test_campaign_table_leads_columns_present_with_meta_leads(tmp_path: Path) -> None:
    """When meta_leads exists and has data, get_campaign_table sums it per
    campaign and computes cost_per_lead = spend / leads."""
    db_with_leads = tmp_path / "metrics_leads.db"
    con = sqlite3.connect(str(db_with_leads))
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
            meta_begin_checkout INTEGER,
            meta_leads INTEGER,
            fetched_at TEXT,
            PRIMARY KEY (campaign_id, date, ad_set_id, ad_id)
        );
        CREATE TABLE ga4_metrics (
            campaign_utm TEXT NOT NULL, date TEXT NOT NULL,
            sessions INTEGER, users INTEGER, new_users INTEGER,
            bounce_rate REAL, avg_engagement_time REAL,
            ga4_purchases_lastclick INTEGER, fetched_at TEXT,
            PRIMARY KEY (campaign_utm, date)
        );
        INSERT INTO campaigns VALUES ('c1','meta_ads','QuizLeads','ACTIVE','2026-05-01');
        INSERT INTO ad_metrics(campaign_id, date, ad_set_id, ad_id, spend,
                               roas, meta_leads, fetched_at)
        VALUES ('c1','2026-05-01','','',80.0,1.0,4,'2026-05-02T00:00:00');
    """)
    con.commit()
    con.close()
    rows = get_campaign_table(db_with_leads, "2026-05-01", "2026-05-01")
    assert rows[0]["campaign_name"] == "QuizLeads"
    assert rows[0]["leads"] == 4
    assert rows[0]["cost_per_lead"] == pytest.approx(80.0 / 4)


def test_campaign_table_keeps_campaigns_with_zero_ga4(tmp_path: Path) -> None:
    """LEFT JOIN means campaigns with no GA4 row still appear."""
    db = tmp_path / "metrics.db"
    con = sqlite3.connect(str(db))
    con.executescript("""
        CREATE TABLE campaigns (id TEXT, source TEXT, name TEXT, status TEXT, created_at TEXT);
        CREATE TABLE ad_metrics (
            campaign_id TEXT, date TEXT, ad_set_id TEXT DEFAULT '',
            ad_id TEXT DEFAULT '', spend REAL, impressions INTEGER,
            clicks INTEGER, ctr REAL, cpc REAL, cpm REAL, roas REAL,
            meta_purchases_7dclick INTEGER, meta_cost_per_purchase REAL,
            reach INTEGER, frequency REAL,
            meta_form_submit_deposit INTEGER DEFAULT 0,
            meta_begin_checkout INTEGER, fetched_at TEXT
        );
        CREATE TABLE ga4_metrics (
            campaign_utm TEXT, date TEXT, sessions INTEGER, users INTEGER,
            new_users INTEGER, bounce_rate REAL, avg_engagement_time REAL,
            ga4_purchases_lastclick INTEGER, fetched_at TEXT
        );
        INSERT INTO campaigns VALUES ('c9','meta_ads','OnlyMeta','ACTIVE','2026-05-01');
        INSERT INTO ad_metrics(campaign_id, date, spend, roas,
                               meta_form_submit_deposit, fetched_at)
          VALUES ('c9','2026-05-01',50.0,1.5,1,'2026-05-02T00:00:00');
    """)
    con.commit()
    con.close()
    rows = get_campaign_table(db, "2026-05-01", "2026-05-01")
    assert any(r["campaign_name"] == "OnlyMeta" for r in rows)
    only = next(r for r in rows if r["campaign_name"] == "OnlyMeta")
    assert only["ga4_sessions"] == 0


# --- get_attribution_comparison -------------------------------------------

def test_attribution_returns_meta_and_ga4_columns(db: Path) -> None:
    rows = get_attribution_comparison(db, "2026-05-01", "2026-05-02")
    assert rows, "expected at least one row"
    for r in rows:
        assert "meta_deposits" in r
        assert "ga4_purchases" in r
        assert "meta_purchases" in r


# --- get_data_freshness ----------------------------------------------------

def test_data_freshness_returns_max_dates(db: Path) -> None:
    out = get_data_freshness(db)
    assert out["meta_last_date"] == "2026-05-02"
    assert out["ga4_last_date"] == "2026-05-02"


def test_data_freshness_empty_tables(tmp_path: Path) -> None:
    db = tmp_path / "empty.db"
    con = sqlite3.connect(str(db))
    con.executescript("""
        CREATE TABLE ad_metrics (date TEXT, fetched_at TEXT);
        CREATE TABLE ga4_metrics (date TEXT, fetched_at TEXT);
    """)
    con.commit()
    con.close()
    out = get_data_freshness(db)
    assert out["meta_last_date"] is None
    assert out["ga4_last_date"] is None


def test_data_freshness_missing_tables_does_not_raise(tmp_path: Path) -> None:
    """D-05 regression: a DB where ad_metrics/ga4_metrics don't exist at all (not
    just empty) must degrade to None values, matching every other query function
    in this module -- not raise sqlite3.OperationalError, which would crash the
    Overview sidebar instead of showing 'Meta last date: —'."""
    db = tmp_path / "no_tables.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE placeholder (id INTEGER)")
    con.commit()
    con.close()

    out = get_data_freshness(db)
    assert out == {
        "meta_fetched": None, "meta_last_date": None,
        "ga4_fetched": None, "ga4_last_date": None,
    }


# --- get_campaign_names ----------------------------------------------------

def test_campaign_names_alphabetical(db: Path) -> None:
    names = get_campaign_names(db)
    assert names == sorted(names)
    assert "Brand" in names
    assert "Convert" in names


# --- get_campaign_objectives / objective_display_label (item 2, 2026-07-22) -

def test_get_campaign_objectives_missing_column_returns_empty(db: Path) -> None:
    """Pre-migration DB (no `objective` column yet) degrades gracefully to {}."""
    assert get_campaign_objectives(db) == {}


def test_get_campaign_objectives_missing_table_returns_empty(tmp_path: Path) -> None:
    empty_db = tmp_path / "empty.db"
    sqlite3.connect(str(empty_db)).close()
    assert get_campaign_objectives(empty_db) == {}


def test_get_campaign_objectives_returns_name_to_objective_map(tmp_path: Path) -> None:
    obj_db = tmp_path / "metrics.db"
    con = sqlite3.connect(str(obj_db))
    con.executescript("""
        CREATE TABLE campaigns (id TEXT PRIMARY KEY, source TEXT, name TEXT,
                                status TEXT, created_at TEXT, objective TEXT);
        INSERT INTO campaigns VALUES
            ('c1','meta_ads','Nowa | SALES | preorder-image | 20260715','ACTIVE','2026-07-15','OUTCOME_SALES'),
            ('c2','meta_ads','Nowa | LEADS | quiz | 20260715','ACTIVE','2026-07-15','OUTCOME_LEADS'),
            ('c3','meta_ads','No Objective Yet','ACTIVE','2026-07-15',NULL);
    """)
    con.commit()
    con.close()

    result = get_campaign_objectives(obj_db)
    assert result == {
        "Nowa | SALES | preorder-image | 20260715": "OUTCOME_SALES",
        "Nowa | LEADS | quiz | 20260715": "OUTCOME_LEADS",
    }
    assert "No Objective Yet" not in result


def test_objective_display_label_known_values() -> None:
    assert objective_display_label("OUTCOME_SALES") == "Sales"
    assert objective_display_label("OUTCOME_LEADS") == "Leads"
    assert objective_display_label("OUTCOME_ENGAGEMENT") == "Engagement"
    assert objective_display_label("OUTCOME_AWARENESS") == "Awareness"
    assert objective_display_label("OUTCOME_TRAFFIC") == "Traffic"
    assert objective_display_label("OUTCOME_APP_PROMOTION") == "App Promotion"


def test_objective_display_label_unknown_falls_back_to_title_case() -> None:
    """An objective not in the lookup table still renders sensibly."""
    assert objective_display_label("OUTCOME_SOMETHING_NEW") == "Something New"


def test_objective_display_label_none_or_empty_returns_blank() -> None:
    assert objective_display_label(None) == ""
    assert objective_display_label("") == ""


# --- get_campaign_daily (DASH-07) ------------------------------------------

def test_campaign_daily_empty_db_returns_empty(tmp_path: Path) -> None:
    """Test 1: get_campaign_daily on empty DB returns []."""
    db_empty = tmp_path / "empty.db"
    con = sqlite3.connect(str(db_empty))
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
            meta_begin_checkout INTEGER,
            fetched_at TEXT,
            PRIMARY KEY (campaign_id, date, ad_set_id, ad_id)
        );
        CREATE TABLE ga4_metrics (
            campaign_utm TEXT NOT NULL, date TEXT NOT NULL,
            sessions INTEGER, users INTEGER, new_users INTEGER,
            bounce_rate REAL, avg_engagement_time REAL,
            ga4_purchases_lastclick INTEGER, fetched_at TEXT,
            PRIMARY KEY (campaign_utm, date)
        );
    """)
    con.commit()
    con.close()
    rows = get_campaign_daily(db_empty, "Brand", "2026-05-01", "2026-05-31")
    assert rows == []


def test_campaign_daily_returns_3_rows_ordered(db: Path) -> None:
    """Test 2: 3 campaign-level rows for Brand across 3 dates, ordered ascending."""
    # Brand in fixture has 2 dates; add a third
    con = sqlite3.connect(str(db))
    con.execute(
        "INSERT INTO ad_metrics(campaign_id, date, ad_set_id, ad_id, spend, roas, "
        "meta_form_submit_deposit, meta_purchases_7dclick, fetched_at) "
        "VALUES ('c1','2026-05-03','','',120.0,3.0,6,2,'2026-05-04T00:00:00')"
    )
    con.commit()
    con.close()

    rows = get_campaign_daily(db, "Brand", "2026-05-01", "2026-05-03")
    assert len(rows) == 3
    dates = [r["date"] for r in rows]
    assert dates == sorted(dates)
    expected_keys = {"date", "spend", "deposits", "sessions", "roas", "meta_purchases", "ga4_purchases"}
    assert expected_keys <= set(rows[0].keys())


def test_campaign_daily_ga4_join_populated_and_unmatched_zero(db: Path) -> None:
    """Test 3: GA4-matched dates populate sessions/ga4_purchases; unmatched dates show 0."""
    # Brand has ga4_metrics for 2026-05-01 and 2026-05-02, but we add a date with no GA4
    con = sqlite3.connect(str(db))
    con.execute(
        "INSERT INTO ad_metrics(campaign_id, date, ad_set_id, ad_id, spend, roas, "
        "meta_form_submit_deposit, meta_purchases_7dclick, fetched_at) "
        "VALUES ('c1','2026-05-03','','',90.0,2.5,3,1,'2026-05-04T00:00:00')"
    )
    con.commit()
    con.close()

    rows = get_campaign_daily(db, "Brand", "2026-05-01", "2026-05-03")
    assert len(rows) == 3

    # 2026-05-01 and 2026-05-02 have GA4 rows — sessions should be populated
    row_01 = next(r for r in rows if r["date"] == "2026-05-01")
    row_03 = next(r for r in rows if r["date"] == "2026-05-03")

    assert row_01["sessions"] == 500  # from fixture ga4_metrics
    assert row_01["ga4_purchases"] == 3  # from fixture ga4_metrics

    assert row_03["sessions"] == 0   # no GA4 row for 2026-05-03 — LEFT JOIN → 0
    assert row_03["ga4_purchases"] == 0


def test_campaign_daily_excludes_adset_level_rows(db: Path) -> None:
    """Test 4: ad_set_id != '' rows are excluded; only campaign-level rows count."""
    # The fixture already has an ad-set-level row for c1 on 2026-05-01 with spend=50
    # Campaign-level spend for Brand on 2026-05-01 is 100.0 only
    rows = get_campaign_daily(db, "Brand", "2026-05-01", "2026-05-01")
    assert len(rows) == 1
    assert rows[0]["spend"] == pytest.approx(100.0)  # NOT 150.0 (would include ad-set row)


def test_campaign_daily_date_bounds_inclusive(db: Path) -> None:
    """Test 5: start_date and end_date bounds are inclusive; rows outside excluded."""
    rows = get_campaign_daily(db, "Brand", "2026-05-02", "2026-05-02")
    assert len(rows) == 1
    assert rows[0]["date"] == "2026-05-02"
    assert rows[0]["spend"] == pytest.approx(150.0)

    # Completely outside range
    rows_outside = get_campaign_daily(db, "Brand", "2026-04-01", "2026-04-30")
    assert rows_outside == []


def test_campaign_daily_no_cross_campaign_leakage(db: Path) -> None:
    """Test 6: Different campaign_name returns 0 rows — no cross-campaign leakage."""
    rows = get_campaign_daily(db, "NonexistentCampaign", "2026-05-01", "2026-05-02")
    assert rows == []

    # Convert is real but results should only contain Convert rows, not Brand
    convert_rows = get_campaign_daily(db, "Convert", "2026-05-01", "2026-05-02")
    for r in convert_rows:
        # Convert only has 1 campaign-level row (2026-05-01), spend=200.0
        assert r["spend"] == pytest.approx(200.0)


# --- get_campaign_ga4_engagement (GA4 Engagement zero-read fix, 2026-07-22) -
#
# The fixture's ga4_metrics.campaign_utm happens to equal the campaigns.name
# ('Brand'/'Convert') exactly, so this exercises the exact-match filter
# itself, not the utm-slug-vs-full-campaign-name mismatch that zeroes this
# out for real campaigns (that mismatch is a Campaign Detail page-level
# concern, covered in tests/test_campaign_detail.py).

def test_ga4_engagement_returns_metrics_for_matching_utm(db: Path) -> None:
    result = get_campaign_ga4_engagement(db, "Brand", "2026-05-01", "2026-05-02")
    assert result["avg_bounce_rate"] == pytest.approx((0.30 + 0.28) / 2)
    assert result["avg_engagement_time_sec"] == pytest.approx((45.0 + 50.0) / 2)
    assert result["total_users"] == 400 + 500
    assert result["total_new_users"] == 300 + 400


def test_ga4_engagement_unmatched_campaign_returns_none_not_zero(db: Path) -> None:
    """No matching campaign_utm rows -> AVG/SUM all come back NULL (None),
    not 0 -- callers must check for None, not falsiness, to detect "no
    data" (this is what the Campaign Detail page's utm-fallback trigger
    relies on)."""
    result = get_campaign_ga4_engagement(db, "NonexistentCampaign", "2026-05-01", "2026-05-02")
    assert result["avg_bounce_rate"] is None
    assert result["avg_engagement_time_sec"] is None
    assert result["total_users"] is None
    assert result["total_new_users"] is None
