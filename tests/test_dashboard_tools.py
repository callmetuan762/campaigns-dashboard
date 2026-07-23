"""Unit tests for src/dashboard/tools.py (DASH-03, DASH-05)."""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

from src.dashboard.tools import (
    TOOLS, dispatch_tool,
    query_metrics, compare_periods, get_campaign_detail,
    list_underperformers, get_landing_page_performance,
    _ALLOWED_METRICS, _ALLOWED_SOURCES, _ALLOWED_SORT_COLS,
)


def _make_db(tmp_path: Path) -> Path:
    """Build a minimal fixture DB matching the canonical schema."""
    db = tmp_path / "fixture.db"
    con = sqlite3.connect(str(db))
    con.executescript("""
        CREATE TABLE campaigns (id TEXT PRIMARY KEY, source TEXT, name TEXT, status TEXT, created_at TEXT);
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
        CREATE TABLE ga4_landing_pages (
            landing_page TEXT NOT NULL, date TEXT NOT NULL,
            sessions INTEGER, total_users INTEGER,
            ga4_purchases_lastclick INTEGER, screen_page_views INTEGER,
            avg_engagement_time REAL, fetched_at TEXT,
            PRIMARY KEY (landing_page, date)
        );
        INSERT INTO campaigns VALUES ('c1', 'meta_ads', 'Brand', 'ACTIVE', '2026-05-01');
        INSERT INTO campaigns VALUES ('c2', 'meta_ads', 'Conv',  'ACTIVE', '2026-05-01');
        INSERT INTO ad_metrics VALUES
          ('c1','2026-05-01','','',100.0,1000,50,5.0,2.0,10.0,2.5,5,20.0,800,1.25,3,'2026-05-02T00:00:00'),
          ('c1','2026-05-02','','',150.0,1500,75,5.0,2.0,10.0,2.0,4,37.5,1200,1.25,4,'2026-05-03T00:00:00'),
          ('c2','2026-05-01','','',200.0,2000,80,4.0,2.5,10.0,1.0,2,100.0,1600,1.25,1,'2026-05-02T00:00:00');
        INSERT INTO ga4_metrics VALUES
          ('Brand','2026-05-01',500,400,300,0.30,45.0,3,'2026-05-02T00:00:00'),
          ('Brand','2026-05-02',600,500,400,0.28,50.0,5,'2026-05-03T00:00:00'),
          ('Conv', '2026-05-01',800,700,500,0.40,30.0,2,'2026-05-02T00:00:00');
        INSERT INTO ga4_landing_pages VALUES
          ('/home','2026-05-01',1000,800,5,2000,40.0,'2026-05-02T00:00:00'),
          ('/buy', '2026-05-01', 500,400,4, 800,60.0,'2026-05-02T00:00:00');
    """)
    # get_campaign_detail/list_underperformers filter on date('now', '-N days'),
    # so they need a row inside a rolling window from the real clock -- the
    # fixed 2026-05-01 rows above are for tests that pass explicit start/end
    # dates and would otherwise age out of any days_back window over time.
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    con.execute(
        "INSERT INTO ad_metrics (campaign_id, date, ad_set_id, ad_id, spend, "
        "impressions, clicks, ctr, cpc, cpm, roas, meta_purchases_7dclick, "
        "meta_cost_per_purchase, reach, frequency, meta_form_submit_deposit, "
        "fetched_at) VALUES ('c1', ?, '', '', 100.0, 1000, 50, 5.0, 2.0, 10.0, "
        "2.25, 5, 20.0, 800, 1.25, 3, ?)",
        (yesterday, f"{yesterday}T00:00:00"),
    )
    con.execute(
        "INSERT INTO ad_metrics (campaign_id, date, ad_set_id, ad_id, spend, "
        "impressions, clicks, ctr, cpc, cpm, roas, meta_purchases_7dclick, "
        "meta_cost_per_purchase, reach, frequency, meta_form_submit_deposit, "
        "fetched_at) VALUES ('c2', ?, '', '', 200.0, 2000, 80, 4.0, 2.5, 10.0, "
        "1.0, 2, 100.0, 1600, 1.25, 1, ?)",
        (yesterday, f"{yesterday}T00:00:00"),
    )
    con.execute(
        "INSERT INTO ga4_metrics (campaign_utm, date, sessions, users, "
        "new_users, bounce_rate, avg_engagement_time, ga4_purchases_lastclick, "
        "fetched_at) VALUES ('Brand', ?, 500, 400, 300, 0.30, 45.0, 3, ?)",
        (yesterday, f"{yesterday}T00:00:00"),
    )
    con.commit()
    con.close()
    return db


def test_tools_count() -> None:
    assert len(TOOLS) == 5
    names = {t["name"] for t in TOOLS}
    assert names == {
        "query_metrics", "compare_periods", "get_campaign_detail",
        "list_underperformers", "get_landing_page_performance",
    }


def test_query_metrics_meta(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    out = query_metrics(str(db), "meta", "2026-05-01", "2026-05-02")
    assert "Meta Ads" in out
    assert "Brand" in out
    assert "Conv" in out
    assert "Spend: $250.00" in out or "Spend: $250.0" in out  # Brand total
    assert "Form Submit Deposit" in out


def test_query_metrics_ga4(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    out = query_metrics(str(db), "ga4", "2026-05-01", "2026-05-02")
    assert "GA4" in out
    assert "last-click" in out
    assert "Brand" in out


def test_query_metrics_unknown_source(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    out = query_metrics(str(db), "twitter", "2026-05-01", "2026-05-02")
    assert "Error" in out


def test_compare_periods_rejects_bad_metric(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    out = compare_periods(str(db), "DROP TABLE", "2026-05-01", "2026-05-01",
                          "2026-05-02", "2026-05-02")
    assert "Error" in out
    assert "DROP TABLE" not in out or "not recognised" in out


def test_get_campaign_detail(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    out = get_campaign_detail(str(db), "Brand", 30)
    assert "Brand" in out
    assert "Never blend" in out


def test_list_underperformers(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    out = list_underperformers(str(db), "roas", 2.0, 30)
    assert "Conv" in out  # ROAS 1.0 < 2.0
    # Brand averages 2.25 >= 2.0 — must NOT be listed
    assert "Brand" not in out


def test_list_underperformers_rejects_injection(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    out = list_underperformers(str(db), "roas; DROP TABLE", 1.0)
    assert "Error" in out


def test_get_landing_page_performance(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    out = get_landing_page_performance(str(db), "2026-05-01", "2026-05-01")
    assert "/home" in out
    assert "/buy" in out


def test_get_landing_page_rejects_bad_sort(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    out = get_landing_page_performance(str(db), "2026-05-01", "2026-05-01",
                                       sort_by="rowid")
    assert "Error" in out


def test_dispatch_tool_routes(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    out = dispatch_tool("query_metrics",
                        {"source": "meta", "start_date": "2026-05-01",
                         "end_date": "2026-05-02"}, str(db))
    assert "Meta Ads" in out


def test_dispatch_tool_unknown(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    out = dispatch_tool("evil_tool", {}, str(db))
    assert out.startswith("Error: unknown tool")


def test_dispatch_tool_bad_args(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    out = dispatch_tool("query_metrics", {"wrong_kwarg": 1}, str(db))
    assert "Error" in out


def test_query_metrics_weighted_roas(tmp_path: Path) -> None:
    """ROAS in tools.py must be SUM(spend*roas)/SUM(spend), matching db.py.
    Brand: (100*2.5 + 150*2.0) / (100+150) = 550/250 = 2.2  (NOT AVG=2.25)."""
    db = _make_db(tmp_path)
    out = query_metrics(str(db), "meta", "2026-05-01", "2026-05-02",
                        campaign_name="Brand")
    assert "ROAS: 2.20" in out


def test_module_has_no_forbidden_imports() -> None:
    src = Path("src/dashboard/tools.py").read_text(encoding="utf-8")
    assert "import streamlit" not in src
    assert "from src.ai" not in src
    assert "from src.bot" not in src
    assert "import aiogram" not in src
    assert "import aiosqlite" not in src
    assert "import asyncio" not in src


# ---------------------------------------------------------------------------
# Task 1 — 3-agent architecture additions: GA4_TOOLS + ga4_query_metrics
# ---------------------------------------------------------------------------

def test_ga4_tools_list_length_and_names() -> None:
    """GA4_TOOLS must contain exactly 2 schemas: get_landing_page_performance + ga4_query_metrics."""
    from src.dashboard.tools import GA4_TOOLS
    assert len(GA4_TOOLS) == 2
    names = {t["name"] for t in GA4_TOOLS}
    assert names == {"get_landing_page_performance", "ga4_query_metrics"}


def test_dispatch_ga4_query_metrics_forces_ga4_source(tmp_path: Path) -> None:
    """dispatch_tool('ga4_query_metrics', ...) must return a string containing 'GA4'."""
    db = _make_db(tmp_path)
    out = dispatch_tool(
        "ga4_query_metrics",
        {"start_date": "2026-05-01", "end_date": "2026-05-02"},
        str(db),
    )
    assert isinstance(out, str)
    assert "GA4" in out


def test_ga4_query_metrics_schema_has_no_source_property() -> None:
    """ga4_query_metrics schema must NOT expose a 'source' property (D-22)."""
    from src.dashboard.tools import GA4_TOOLS
    schema = next(t for t in GA4_TOOLS if t["name"] == "ga4_query_metrics")
    properties = schema["input_schema"]["properties"]
    assert "source" not in properties


def test_tools_list_still_length_5() -> None:
    """Original TOOLS list must remain exactly 5 schemas — ga4_query_metrics is NOT added to it."""
    assert len(TOOLS) == 5
    names = {t["name"] for t in TOOLS}
    assert names == {
        "query_metrics", "compare_periods", "get_campaign_detail",
        "list_underperformers", "get_landing_page_performance",
    }
