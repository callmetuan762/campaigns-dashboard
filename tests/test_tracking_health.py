"""Unit tests for src/dashboard/tracking_health.py (banding helpers) and the new
src/dashboard/db.py query functions added for the Phase C Tracking Health page.

Mirrors tests/test_components.py (banding) and tests/test_dashboard_db.py
(sqlite fixture pattern) conventions.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.dashboard.tracking_health import (
    chip_emoji,
    click_session_ratio_color,
    freshness_color,
    not_set_share_color,
)


# ---------------------------------------------------------------------------
# click_session_ratio_color
# ---------------------------------------------------------------------------

def test_click_session_ratio_none_is_gray():
    assert click_session_ratio_color(None) == "gray"


def test_click_session_ratio_green_at_boundary():
    assert click_session_ratio_color(70.0) == "green"


def test_click_session_ratio_green_above_boundary():
    assert click_session_ratio_color(85.0) == "green"


def test_click_session_ratio_amber_at_lower_boundary():
    assert click_session_ratio_color(50.0) == "amber"


def test_click_session_ratio_amber_just_below_green():
    assert click_session_ratio_color(69.9) == "amber"


def test_click_session_ratio_red_below_boundary():
    assert click_session_ratio_color(49.9) == "red"


def test_click_session_ratio_red_zero():
    assert click_session_ratio_color(0.0) == "red"


# ---------------------------------------------------------------------------
# freshness_color
# ---------------------------------------------------------------------------

def test_freshness_none_is_gray():
    assert freshness_color(None) == "gray"


def test_freshness_green_below_30():
    assert freshness_color(29.9) == "green"


def test_freshness_amber_at_30():
    assert freshness_color(30.0) == "amber"


def test_freshness_amber_at_54():
    assert freshness_color(54.0) == "amber"


def test_freshness_red_above_54():
    assert freshness_color(54.1) == "red"


# ---------------------------------------------------------------------------
# not_set_share_color
# ---------------------------------------------------------------------------

def test_not_set_share_none_is_gray():
    assert not_set_share_color(None) == "gray"


def test_not_set_share_green_at_30():
    assert not_set_share_color(30.0) == "green"


def test_not_set_share_amber_just_above_30():
    assert not_set_share_color(30.1) == "amber"


def test_not_set_share_amber_at_60():
    assert not_set_share_color(60.0) == "amber"


def test_not_set_share_red_above_60():
    assert not_set_share_color(60.1) == "red"


# ---------------------------------------------------------------------------
# chip_emoji
# ---------------------------------------------------------------------------

def test_chip_emoji_known_colors():
    assert chip_emoji("green")
    assert chip_emoji("amber")
    assert chip_emoji("red")
    assert chip_emoji("gray")


def test_chip_emoji_unknown_color_falls_back_to_gray():
    assert chip_emoji("mystery") == chip_emoji("gray")


# ---------------------------------------------------------------------------
# src/dashboard/db.py — new Phase C query functions, safe-on-empty-DB
# ---------------------------------------------------------------------------

@pytest.fixture()
def empty_db(tmp_path: Path) -> Path:
    """A DB file that exists but has none of the tables these queries touch."""
    p = tmp_path / "empty.db"
    con = sqlite3.connect(str(p))
    con.execute("CREATE TABLE placeholder (id INTEGER)")
    con.commit()
    con.close()
    return p


def test_get_click_session_ratio_missing_tables_returns_none(empty_db):
    from src.dashboard.db import get_click_session_ratio

    assert get_click_session_ratio(empty_db, "2026-05-01", "2026-05-07") is None


def test_get_event_daily_counts_missing_table_returns_empty(empty_db):
    from src.dashboard.db import get_event_daily_counts

    assert get_event_daily_counts(empty_db, "purchase", "2026-05-01", "2026-05-07") == []


def test_get_sessions_daily_missing_table_returns_empty(empty_db):
    from src.dashboard.db import get_sessions_daily

    assert get_sessions_daily(empty_db, "2026-05-01", "2026-05-07") == []


def test_get_not_set_campaign_share_missing_table_returns_none(empty_db):
    from src.dashboard.db import get_not_set_campaign_share

    assert get_not_set_campaign_share(empty_db, "begin_checkout", "2026-05-01", "2026-05-07") is None


def test_get_event_freshness_hours_missing_table_returns_none_per_event(empty_db):
    from src.dashboard.db import get_event_freshness_hours

    result = get_event_freshness_hours(empty_db, ["begin_checkout", "purchase"])
    assert result == {"begin_checkout": None, "purchase": None}


def test_get_event_freshness_hours_empty_event_list(empty_db):
    from src.dashboard.db import get_event_freshness_hours

    assert get_event_freshness_hours(empty_db, []) == {}


def test_get_pixel_health_missing_table_returns_empty(empty_db):
    from src.dashboard.db import get_pixel_health

    assert get_pixel_health(empty_db, "2026-05-01", "2026-05-07") == []


# ---------------------------------------------------------------------------
# Populated-DB behavior
# ---------------------------------------------------------------------------

def _make_populated_db(path: Path) -> None:
    con = sqlite3.connect(str(path))
    con.executescript(
        """
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
        CREATE TABLE ga4_events (
            event_name TEXT NOT NULL, date TEXT NOT NULL,
            campaign_utm TEXT NOT NULL DEFAULT '', lp_slug TEXT NOT NULL DEFAULT '',
            event_count INTEGER, fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (event_name, date, campaign_utm, lp_slug)
        );
        CREATE TABLE pixel_health (
            date TEXT NOT NULL, event_name TEXT NOT NULL,
            browser_count INTEGER, server_count INTEGER,
            dedup_rate REAL, emq_score REAL, fetched_at TEXT,
            PRIMARY KEY (date, event_name)
        );
        CREATE TABLE ga4_landing_pages (
            landing_page TEXT NOT NULL, date TEXT NOT NULL,
            sessions INTEGER, total_users INTEGER, ga4_purchases_lastclick INTEGER,
            screen_page_views INTEGER, avg_engagement_time REAL,
            fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (landing_page, date)
        );
        INSERT INTO ad_metrics(campaign_id, date, ad_set_id, ad_id, spend, clicks, fetched_at)
        VALUES ('c1', '2026-05-01', '', '', 100.0, 100, '2026-05-02T00:00:00');
        INSERT INTO ga4_metrics(campaign_utm, date, sessions, fetched_at)
        VALUES ('', '2026-05-01', 60, '2026-05-02T00:00:00');
        -- Deliberately higher than ga4_metrics' 60 (campaign-attributed only) --
        -- ga4_landing_pages has no campaign filter, so it captures MORE traffic
        -- (e.g. '(not set)' sessions). get_click_session_ratio must read from
        -- here (75), not from ga4_metrics' 60 (D-11 fix).
        INSERT INTO ga4_landing_pages(landing_page, date, sessions, fetched_at)
        VALUES ('/lp/', '2026-05-01', 75, '2026-05-02T00:00:00');
        INSERT INTO ga4_events(event_name, date, campaign_utm, lp_slug, event_count)
        VALUES ('begin_checkout', '2026-05-01', '', 'routine', 40);
        INSERT INTO ga4_events(event_name, date, campaign_utm, lp_slug, event_count)
        VALUES ('begin_checkout', '2026-05-01', 'nowa_launch', 'routine', 10);
        INSERT INTO pixel_health(date, event_name, browser_count, server_count, dedup_rate, emq_score)
        VALUES ('2026-05-01', 'purchase', 100, 80, 0.7, NULL);
        """
    )
    con.commit()
    con.close()


@pytest.fixture()
def populated_db(tmp_path: Path) -> Path:
    p = tmp_path / "metrics.db"
    _make_populated_db(p)
    return p


def test_get_click_session_ratio_computes_percentage(populated_db):
    """D-11 fix: sourced from ga4_landing_pages (all sessions = 75), NOT
    ga4_metrics (campaign-attributed only = 60) — 75 / 100 clicks = 75%."""
    from src.dashboard.db import get_click_session_ratio

    ratio = get_click_session_ratio(populated_db, "2026-05-01", "2026-05-01")
    assert ratio == pytest.approx(75.0)


def test_get_click_session_ratio_zero_clicks_is_none(populated_db):
    from src.dashboard.db import get_click_session_ratio

    assert get_click_session_ratio(populated_db, "2026-01-01", "2026-01-01") is None


def test_get_event_daily_counts_sums_across_campaigns(populated_db):
    from src.dashboard.db import get_event_daily_counts

    rows = get_event_daily_counts(populated_db, "begin_checkout", "2026-05-01", "2026-05-01")
    assert len(rows) == 1
    assert rows[0]["event_count"] == 50  # 40 + 10


def test_get_not_set_campaign_share(populated_db):
    from src.dashboard.db import get_not_set_campaign_share

    # 40 (not set) out of 50 total = 80%
    share = get_not_set_campaign_share(populated_db, "begin_checkout", "2026-05-01", "2026-05-01")
    assert share == pytest.approx(80.0)


# ---------------------------------------------------------------------------
# D-07 regression: get_not_set_campaign_share must match the literal string
# '(not set)' (what GA4 actually stores), not just campaign_utm = ''. Before this
# fix, a window where GA4 stored '(not set)' rather than '' would show 0% here
# while get_ga4_not_set_share (which already matched both) showed the true share.
# ---------------------------------------------------------------------------

def test_get_not_set_campaign_share_matches_literal_not_set_string(tmp_path):
    """The exact bug: campaign_utm = '(not set)' rows must count as not-set, not
    be silently excluded (which would previously report an incorrectly low/0% share)."""
    from src.dashboard.db import get_not_set_campaign_share

    db = tmp_path / "metrics.db"
    con = sqlite3.connect(str(db))
    con.executescript(
        """
        CREATE TABLE ga4_events (
            event_name TEXT NOT NULL, date TEXT NOT NULL,
            campaign_utm TEXT NOT NULL DEFAULT '', lp_slug TEXT NOT NULL DEFAULT '',
            event_count INTEGER, fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (event_name, date, campaign_utm, lp_slug)
        );
        INSERT INTO ga4_events(event_name, date, campaign_utm, lp_slug, event_count)
        VALUES ('begin_checkout', '2026-07-15', '(not set)', '', 81);
        INSERT INTO ga4_events(event_name, date, campaign_utm, lp_slug, event_count)
        VALUES ('begin_checkout', '2026-07-15', 'nowa_launch', '', 19);
        """
    )
    con.commit()
    con.close()

    share = get_not_set_campaign_share(db, "begin_checkout", "2026-07-15", "2026-07-15")
    assert share == pytest.approx(81.0)


def test_get_not_set_campaign_share_matches_both_blank_and_literal(tmp_path):
    """Both '' and '(not set)' rows must be counted together, matching
    get_ga4_not_set_share's IN ('(not set)', '') semantics exactly."""
    from src.dashboard.db import get_not_set_campaign_share

    db = tmp_path / "metrics.db"
    con = sqlite3.connect(str(db))
    con.executescript(
        """
        CREATE TABLE ga4_events (
            event_name TEXT NOT NULL, date TEXT NOT NULL,
            campaign_utm TEXT NOT NULL DEFAULT '', lp_slug TEXT NOT NULL DEFAULT '',
            event_count INTEGER, fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (event_name, date, campaign_utm, lp_slug)
        );
        INSERT INTO ga4_events(event_name, date, campaign_utm, lp_slug, event_count)
        VALUES ('purchase', '2026-07-15', '(not set)', '', 30);
        INSERT INTO ga4_events(event_name, date, campaign_utm, lp_slug, event_count)
        VALUES ('purchase', '2026-07-15', '', '', 20);
        INSERT INTO ga4_events(event_name, date, campaign_utm, lp_slug, event_count)
        VALUES ('purchase', '2026-07-15', 'nowa_launch', '', 50);
        """
    )
    con.commit()
    con.close()

    share = get_not_set_campaign_share(db, "purchase", "2026-07-15", "2026-07-15")
    assert share == pytest.approx(50.0)  # (30 + 20) / 100


def test_not_set_campaign_values_constant_shared_by_both_functions():
    """D-07: get_ga4_not_set_share and get_not_set_campaign_share must both bind
    the same NOT_SET_CAMPAIGN_VALUES constant so their string-matching rule
    can never drift apart again."""
    import inspect

    from src.dashboard import db

    assert db.NOT_SET_CAMPAIGN_VALUES == ("(not set)", "")
    assert "NOT_SET_CAMPAIGN_VALUES" in inspect.getsource(db.get_ga4_not_set_share)
    assert "NOT_SET_CAMPAIGN_VALUES" in inspect.getsource(db.get_not_set_campaign_share)


def test_get_pixel_health_returns_rows(populated_db):
    from src.dashboard.db import get_pixel_health

    rows = get_pixel_health(populated_db, "2026-05-01", "2026-05-01")
    assert len(rows) == 1
    assert rows[0]["event_name"] == "purchase"
    assert rows[0]["browser_count"] == 100
    assert rows[0]["server_count"] == 80
    assert rows[0]["emq_score"] is None


def test_get_purchase_divergence_never_blends(populated_db):
    from src.dashboard.db import get_purchase_divergence

    result = get_purchase_divergence(populated_db, "2026-05-01", "2026-05-01")
    assert "meta_purchases" in result
    assert "ga4_purchases" in result
