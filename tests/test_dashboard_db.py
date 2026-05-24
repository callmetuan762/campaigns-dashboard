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
            meta_form_submit_deposit INTEGER DEFAULT 0, fetched_at TEXT
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


# --- get_campaign_names ----------------------------------------------------

def test_campaign_names_alphabetical(db: Path) -> None:
    names = get_campaign_names(db)
    assert names == sorted(names)
    assert "Brand" in names
    assert "Convert" in names
