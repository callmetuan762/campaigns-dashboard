"""Tests for the funnel-v3 preorder-funnel queries in src/dashboard/db.py.

Covers: get_meta_funnel_summary, get_ga4_sessions_summary,
get_ga4_event_step_totals, get_orders_step, get_preorder_funnel_steps,
get_segment_mini_funnels, get_click_session_gap (+ click_session_gap_band),
get_ga4_not_set_share (+ not_set_share_band), get_quiz_funnel,
get_quiz_cost_per_lead.

Fixture style mirrors tests/test_dashboard_db.py and tests/test_mmm_persistence.py:
hand-rolled sqlite3 schema matching src/db/schema.py migrations 001/010/011/012,
built directly with sqlite3.executescript against a tmp_path DB file (no async
migration runner needed since src/dashboard/db.py is a sync sqlite3 module).

House rule under test throughout: a source table/event with ZERO rows EVER
(not just zero in the selected date range) must report `available: False`
("n/a — not measured yet"), never a bare 0 ("measured zero").
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.dashboard.db import (
    attribution_gap_band,
    capture_gap_band,
    click_session_gap_band,
    get_click_session_gap,
    get_ga4_event_step_totals,
    get_ga4_not_set_share,
    get_ga4_sessions_summary,
    get_meta_funnel_summary,
    get_orders_step,
    get_preorder_funnel_steps,
    get_quiz_cost_per_lead,
    get_quiz_funnel,
    get_segment_mini_funnels,
    get_total_sessions_daily,
    get_total_sessions_summary,
    not_set_share_band,
)

QUIZ_SLUGS = ["routine-break", "big-feelings-type", "screen-kid"]


# ---------------------------------------------------------------------------
# Fixture DB builder
# ---------------------------------------------------------------------------

_BASE_SCHEMA = """
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
    landing_page_views INTEGER,
    video_3s_views INTEGER,
    video_thruplay INTEGER,
    meta_begin_checkout INTEGER,
    meta_cost_per_begin_checkout REAL,
    meta_add_to_cart INTEGER,
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
CREATE TABLE ga4_events (
    event_name TEXT NOT NULL, date TEXT NOT NULL,
    campaign_utm TEXT NOT NULL DEFAULT '', lp_slug TEXT NOT NULL DEFAULT '',
    event_count INTEGER, fetched_at TEXT,
    PRIMARY KEY (event_name, date, campaign_utm, lp_slug)
);
CREATE TABLE shopify_orders (
    order_id TEXT PRIMARY KEY, created_at TEXT, order_date TEXT NOT NULL,
    total_price REAL, financial_status TEXT,
    utm_source TEXT NOT NULL DEFAULT '', utm_campaign TEXT NOT NULL DEFAULT '',
    utm_content TEXT NOT NULL DEFAULT '', lp_slug TEXT NOT NULL DEFAULT '',
    landing_site TEXT NOT NULL DEFAULT '', referring_site TEXT NOT NULL DEFAULT '',
    fetched_at TEXT
);
CREATE TABLE ga4_landing_pages (
    landing_page TEXT NOT NULL, date TEXT NOT NULL,
    sessions INTEGER, total_users INTEGER, ga4_purchases_lastclick INTEGER,
    screen_page_views INTEGER, avg_engagement_time REAL,
    fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (landing_page, date)
);
"""


def _make_db(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(path))
    con.executescript(_BASE_SCHEMA)
    con.commit()
    return con


@pytest.fixture()
def empty_db(tmp_path: Path) -> Path:
    """All funnel-v3 tables exist but are completely empty (fresh migration, no ingest)."""
    p = tmp_path / "metrics.db"
    con = _make_db(p)
    con.close()
    return p


@pytest.fixture()
def seeded_db(tmp_path: Path) -> Path:
    """A DB with realistic funnel-v3 data across two campaigns / two lp_slugs."""
    p = tmp_path / "metrics.db"
    con = _make_db(p)
    con.executescript("""
        INSERT INTO campaigns VALUES
            ('c1', 'meta_ads', 'Nowa | SALES | Preorder', 'ACTIVE', '2026-06-01'),
            ('c2', 'meta_ads', 'Nowa | LEADS | Quiz', 'ACTIVE', '2026-06-01');

        -- c1: full funnel columns populated (landing_page_views present)
        INSERT INTO ad_metrics
            (campaign_id, date, ad_set_id, ad_id, spend, impressions, clicks,
             roas, landing_page_views, fetched_at)
        VALUES
            ('c1', '2026-06-01', '', '', 100.0, 10000, 500, 2.0, 400, '2026-06-02T00:00:00'),
            ('c1', '2026-06-02', '', '', 150.0, 12000, 600, 2.0, 480, '2026-06-03T00:00:00');

        -- c2: LEADS campaign, spend only (used for quiz CPL)
        INSERT INTO ad_metrics
            (campaign_id, date, ad_set_id, ad_id, spend, impressions, clicks, roas, fetched_at)
        VALUES
            ('c2', '2026-06-01', '', '', 50.0, 5000, 200, 1.5, '2026-06-02T00:00:00');

        INSERT INTO ga4_metrics VALUES
            ('Nowa | SALES | Preorder', '2026-06-01', 300, 280, 200, 0.3, 40.0, 2, '2026-06-02T00:00:00'),
            ('Nowa | SALES | Preorder', '2026-06-02', 350, 320, 220, 0.28, 42.0, 3, '2026-06-03T00:00:00');

        -- GA4 events: cta_click_convert, add_to_cart, begin_checkout, purchase (no lead_submit/quiz here)
        INSERT INTO ga4_events
            (event_name, date, campaign_utm, lp_slug, event_count, fetched_at)
        VALUES
            ('cta_click_convert',      '2026-06-01', 'nowa_launch', 'routine',      150, '2026-06-02'),
            ('cta_click_convert',      '2026-06-02', 'nowa_launch', 'big-feelings', 180, '2026-06-03'),
            ('add_to_cart',    '2026-06-01', 'nowa_launch', 'routine',       80, '2026-06-02'),
            ('add_to_cart',    '2026-06-02', 'nowa_launch', 'big-feelings',  95, '2026-06-03'),
            ('begin_checkout', '2026-06-01', 'nowa_launch', 'routine',       60, '2026-06-02'),
            ('begin_checkout', '2026-06-02', '(not set)',   'big-feelings',  70, '2026-06-03'),
            ('page_view_lp',   '2026-06-01', 'nowa_launch', 'routine',      500, '2026-06-02'),
            ('page_view_lp',   '2026-06-02', 'nowa_launch', 'big-feelings', 550, '2026-06-03');

        INSERT INTO shopify_orders
            (order_id, created_at, order_date, total_price, financial_status, lp_slug, fetched_at)
        VALUES
            ('o1', '2026-06-01', '2026-06-01', 49.0, 'paid', 'routine', '2026-06-02'),
            ('o2', '2026-06-02', '2026-06-02', 49.0, 'paid', 'big-feelings', '2026-06-03'),
            ('o3', '2026-06-02', '2026-06-02', 49.0, 'refunded', 'routine', '2026-06-03');

        -- Total GA4 sessions (all campaigns incl. '(not set)') -- deliberately
        -- larger than ga4_metrics' campaign-attributed sessions (300+350=650) but
        -- smaller than Meta LPV (400+480=880), so clicks(1300) > lpv(880) >
        -- all_sessions(700) > attributed(650) forms a clean monotonic funnel for
        -- the capture-gap / attribution-gap decomposition tests.
        INSERT INTO ga4_landing_pages
            (landing_page, date, sessions, total_users, ga4_purchases_lastclick,
             screen_page_views, avg_engagement_time, fetched_at)
        VALUES
            ('/routine/', '2026-06-01', 350, 300, 1, 500, 40.0, '2026-06-02'),
            ('/big-feelings/', '2026-06-02', 350, 300, 1, 500, 42.0, '2026-06-03');
    """)
    con.commit()
    con.close()
    return p


# ---------------------------------------------------------------------------
# get_meta_funnel_summary
# ---------------------------------------------------------------------------

def test_meta_funnel_summary_sums_and_available(seeded_db: Path) -> None:
    out = get_meta_funnel_summary(seeded_db, "2026-06-01", "2026-06-02")
    assert out["impressions"] == 10000 + 12000 + 5000
    assert out["clicks"] == 500 + 600 + 200
    assert out["landing_page_views"] == 400 + 480
    assert out["available"] is True
    assert out["lpv_available"] is True


def test_meta_funnel_summary_empty_db_unavailable(empty_db: Path) -> None:
    out = get_meta_funnel_summary(empty_db, "2026-06-01", "2026-06-02")
    assert out["available"] is False
    assert out["lpv_available"] is False
    assert out["impressions"] == 0


def test_meta_funnel_summary_missing_table_returns_defaults(tmp_path: Path) -> None:
    """No table at all (pre-migration DB) -> graceful defaults, no raise."""
    db = tmp_path / "missing.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE campaigns (id TEXT)")
    con.commit()
    con.close()
    out = get_meta_funnel_summary(db, "2026-06-01", "2026-06-02")
    assert out == {
        "impressions": 0, "clicks": 0, "landing_page_views": 0,
        "available": False, "lpv_available": False,
    }


def test_meta_funnel_summary_lpv_unavailable_when_all_null(tmp_path: Path) -> None:
    """ad_metrics has rows, but landing_page_views is NULL everywhere (old ingest run)."""
    db = tmp_path / "metrics.db"
    con = _make_db(db)
    con.executescript("""
        INSERT INTO campaigns VALUES ('c1','meta_ads','Brand','ACTIVE','2026-06-01');
        INSERT INTO ad_metrics (campaign_id, date, ad_set_id, ad_id, spend, impressions, clicks, fetched_at)
        VALUES ('c1','2026-06-01','','',10.0,100,10,'2026-06-02');
    """)
    con.commit()
    con.close()
    out = get_meta_funnel_summary(db, "2026-06-01", "2026-06-01")
    assert out["available"] is True
    assert out["lpv_available"] is False
    assert out["landing_page_views"] == 0


# ---------------------------------------------------------------------------
# get_ga4_sessions_summary
# ---------------------------------------------------------------------------

def test_ga4_sessions_summary(seeded_db: Path) -> None:
    out = get_ga4_sessions_summary(seeded_db, "2026-06-01", "2026-06-02")
    assert out["sessions"] == 300 + 350
    assert out["available"] is True


def test_ga4_sessions_summary_empty(empty_db: Path) -> None:
    out = get_ga4_sessions_summary(empty_db, "2026-06-01", "2026-06-02")
    assert out == {"sessions": 0, "available": False}


# ---------------------------------------------------------------------------
# get_ga4_event_step_totals
# ---------------------------------------------------------------------------

def test_event_step_totals_counts_and_availability(seeded_db: Path) -> None:
    out = get_ga4_event_step_totals(
        seeded_db, "2026-06-01", "2026-06-02",
        ["cta_click_convert", "add_to_cart", "begin_checkout", "lead_submit"],
    )
    assert out["cta_click_convert"] == {"count": 330, "available": True}
    assert out["add_to_cart"] == {"count": 175, "available": True}
    assert out["begin_checkout"] == {"count": 130, "available": True}
    # lead_submit was never ingested anywhere -> unavailable, not a 0
    assert out["lead_submit"] == {"count": 0, "available": False}


def test_event_step_totals_empty_table(empty_db: Path) -> None:
    out = get_ga4_event_step_totals(empty_db, "2026-06-01", "2026-06-02", ["cta_click_convert"])
    assert out["cta_click_convert"] == {"count": 0, "available": False}


def test_event_step_totals_missing_table(tmp_path: Path) -> None:
    db = tmp_path / "missing.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE campaigns (id TEXT)")
    con.commit()
    con.close()
    out = get_ga4_event_step_totals(db, "2026-06-01", "2026-06-02", ["cta_click_convert", "purchase"])
    assert out["cta_click_convert"]["available"] is False
    assert out["purchase"]["available"] is False


def test_event_step_totals_empty_event_list(seeded_db: Path) -> None:
    assert get_ga4_event_step_totals(seeded_db, "2026-06-01", "2026-06-02", []) == {}


def test_event_step_totals_date_range_narrows_count(seeded_db: Path) -> None:
    """Restricting the range to just 06-01 only counts that day's event_count."""
    out = get_ga4_event_step_totals(seeded_db, "2026-06-01", "2026-06-01", ["cta_click_convert"])
    assert out["cta_click_convert"]["count"] == 150
    assert out["cta_click_convert"]["available"] is True


# ---------------------------------------------------------------------------
# get_orders_step
# ---------------------------------------------------------------------------

def test_orders_step_uses_shopify_paid_only(seeded_db: Path) -> None:
    out = get_orders_step(seeded_db, "2026-06-01", "2026-06-02")
    # 2 paid orders (o1, o2); o3 is 'refunded' and excluded
    assert out == {"count": 2, "available": True, "source": "shopify_orders"}


def test_orders_step_falls_back_to_ga4_purchase_when_shopify_empty(tmp_path: Path) -> None:
    db = tmp_path / "metrics.db"
    con = _make_db(db)
    con.executescript("""
        INSERT INTO ga4_events (event_name, date, campaign_utm, lp_slug, event_count) VALUES
            ('purchase', '2026-06-01', 'nowa_launch', 'routine', 5);
    """)
    con.commit()
    con.close()
    out = get_orders_step(db, "2026-06-01", "2026-06-01")
    assert out == {"count": 5, "available": True, "source": "ga4_events"}


def test_orders_step_unavailable_when_both_empty(empty_db: Path) -> None:
    out = get_orders_step(empty_db, "2026-06-01", "2026-06-02")
    assert out == {"count": 0, "available": False, "source": None}


def test_orders_step_shopify_ingested_but_zero_paid_in_range_is_measured_zero(tmp_path: Path) -> None:
    """Shopify has rows (ever), but none 'paid' in this specific range -> a real 0, not n/a."""
    db = tmp_path / "metrics.db"
    con = _make_db(db)
    con.executescript("""
        INSERT INTO shopify_orders (order_id, order_date, financial_status, fetched_at)
        VALUES ('o1', '2026-06-01', 'pending', '2026-06-02');
    """)
    con.commit()
    con.close()
    out = get_orders_step(db, "2026-06-01", "2026-06-01")
    assert out == {"count": 0, "available": True, "source": "shopify_orders"}


# ---------------------------------------------------------------------------
# get_preorder_funnel_steps
# ---------------------------------------------------------------------------

def test_preorder_funnel_steps_order_and_labels(seeded_db: Path) -> None:
    steps = get_preorder_funnel_steps(seeded_db, "2026-06-01", "2026-06-02")
    labels = [s["label"] for s in steps]
    assert labels == [
        "Impressions", "Clicks", "Landing-Page Views", "GA4 Sessions",
        "CTA Clicks (convert)", "Add to Cart", "Begin Checkout", "Orders",
    ]


def test_preorder_funnel_steps_first_step_no_conversion_pct(seeded_db: Path) -> None:
    steps = get_preorder_funnel_steps(seeded_db, "2026-06-01", "2026-06-02")
    assert steps[0]["conversion_pct"] is None
    assert steps[0]["value"] == 27000


def test_preorder_funnel_steps_conversion_math(seeded_db: Path) -> None:
    steps = get_preorder_funnel_steps(seeded_db, "2026-06-01", "2026-06-02")
    by_label = {s["label"]: s for s in steps}
    clicks = by_label["Clicks"]["value"]
    impressions = by_label["Impressions"]["value"]
    assert by_label["Clicks"]["conversion_pct"] == pytest.approx(
        round(clicks * 100.0 / impressions, 1)
    )
    lpv = by_label["Landing-Page Views"]["value"]
    assert by_label["Landing-Page Views"]["conversion_pct"] == pytest.approx(
        round(lpv * 100.0 / clicks, 1)
    )


def test_preorder_funnel_steps_orders_has_source_note(seeded_db: Path) -> None:
    steps = get_preorder_funnel_steps(seeded_db, "2026-06-01", "2026-06-02")
    orders_step = next(s for s in steps if s["label"] == "Orders")
    assert orders_step["available"] is True
    assert "Shopify" in orders_step["note"]


def test_preorder_funnel_steps_skips_unavailable_as_conversion_baseline(tmp_path: Path) -> None:
    """LPV unavailable (never ingested) shouldn't corrupt GA4 Sessions' conversion_pct
    baseline -- it should still be computed relative to Clicks (the last *available*
    step), not against a None/0 LPV value."""
    db = tmp_path / "metrics.db"
    con = _make_db(db)
    con.executescript("""
        INSERT INTO campaigns VALUES ('c1','meta_ads','Brand','ACTIVE','2026-06-01');
        INSERT INTO ad_metrics (campaign_id, date, ad_set_id, ad_id, spend, impressions, clicks, fetched_at)
        VALUES ('c1','2026-06-01','','',10.0,1000,100,'2026-06-02');
        INSERT INTO ga4_metrics VALUES ('Brand','2026-06-01',40,30,20,0.3,10.0,1,'2026-06-02');
    """)
    con.commit()
    con.close()

    steps = get_preorder_funnel_steps(db, "2026-06-01", "2026-06-01")
    by_label = {s["label"]: s for s in steps}
    assert by_label["Landing-Page Views"]["available"] is False
    assert by_label["Landing-Page Views"]["value"] is None
    assert by_label["GA4 Sessions"]["available"] is True
    # 40 sessions / 100 clicks (skipping the unavailable LPV step) = 40%
    assert by_label["GA4 Sessions"]["conversion_pct"] == pytest.approx(40.0)


def test_preorder_funnel_steps_all_empty(empty_db: Path) -> None:
    steps = get_preorder_funnel_steps(empty_db, "2026-06-01", "2026-06-02")
    assert all(not s["available"] for s in steps)
    assert all(s["value"] is None for s in steps)
    assert all(s["conversion_pct"] is None for s in steps)


# ---------------------------------------------------------------------------
# get_segment_mini_funnels
# ---------------------------------------------------------------------------

def test_segment_mini_funnels_per_slug_rollup(seeded_db: Path) -> None:
    rows = get_segment_mini_funnels(seeded_db, "2026-06-01", "2026-06-02")
    by_slug = {r["lp_slug"]: r for r in rows}
    assert by_slug["routine"]["sessions"] == 500
    assert by_slug["routine"]["add_to_cart"] == 80
    assert by_slug["routine"]["begin_checkout"] == 60
    assert by_slug["routine"]["orders"] == 1  # o1 paid; o3 refunded excluded

    assert by_slug["big-feelings"]["sessions"] == 550
    assert by_slug["big-feelings"]["add_to_cart"] == 95
    assert by_slug["big-feelings"]["begin_checkout"] == 70
    assert by_slug["big-feelings"]["orders"] == 1


def test_segment_mini_funnels_empty(empty_db: Path) -> None:
    assert get_segment_mini_funnels(empty_db, "2026-06-01", "2026-06-02") == []


def test_segment_mini_funnels_sorted_by_sessions_desc(seeded_db: Path) -> None:
    rows = get_segment_mini_funnels(seeded_db, "2026-06-01", "2026-06-02")
    sessions = [r["sessions"] for r in rows]
    assert sessions == sorted(sessions, reverse=True)


# ---------------------------------------------------------------------------
# get_click_session_gap + click_session_gap_band
# ---------------------------------------------------------------------------

def test_click_session_gap_math(seeded_db: Path) -> None:
    out = get_click_session_gap(seeded_db, "2026-06-01", "2026-06-02")
    clicks = 500 + 600 + 200
    lpv = 400 + 480
    sessions = 300 + 350
    assert out["meta_clicks"] == clicks
    assert out["meta_lpv"] == lpv
    assert out["ga4_sessions"] == sessions
    assert out["gap_clicks_pct"] == pytest.approx(round((1 - sessions / clicks) * 100, 1))
    assert out["gap_lpv_pct"] == pytest.approx(round((1 - sessions / lpv) * 100, 1))


def test_click_session_gap_none_when_unavailable(empty_db: Path) -> None:
    out = get_click_session_gap(empty_db, "2026-06-01", "2026-06-02")
    assert out["meta_clicks"] is None
    assert out["meta_lpv"] is None
    assert out["ga4_sessions"] is None
    assert out["gap_clicks_pct"] is None
    assert out["gap_lpv_pct"] is None


@pytest.mark.parametrize(
    "pct,expected",
    [(None, "gray"), (0.0, "green"), (20.0, "green"), (20.1, "amber"),
     (30.0, "amber"), (30.1, "red"), (75.0, "red")],
)
def test_click_session_gap_band_thresholds(pct: float | None, expected: str) -> None:
    assert click_session_gap_band(pct) == expected


# ---------------------------------------------------------------------------
# D-11 fix: get_total_sessions_daily / get_total_sessions_summary +
# capture_gap_pct / attribution_gap_pct decomposition on get_click_session_gap
# ---------------------------------------------------------------------------

def test_total_sessions_daily_sums_from_landing_pages(seeded_db: Path) -> None:
    """Unlike ga4_metrics (campaign-attributed only), ga4_landing_pages has no
    campaign filter -- this must return the true daily session totals."""
    rows = get_total_sessions_daily(seeded_db, "2026-06-01", "2026-06-02")
    by_date = {r["date"]: r["sessions"] for r in rows}
    assert by_date["2026-06-01"] == 350
    assert by_date["2026-06-02"] == 350


def test_total_sessions_daily_empty_db_returns_empty(empty_db: Path) -> None:
    assert get_total_sessions_daily(empty_db, "2026-06-01", "2026-06-02") == []


def test_total_sessions_daily_missing_table_returns_empty(tmp_path: Path) -> None:
    db = tmp_path / "missing.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE campaigns (id TEXT)")
    con.commit()
    con.close()
    assert get_total_sessions_daily(db, "2026-06-01", "2026-06-02") == []


def test_total_sessions_summary_computes_total_and_available(seeded_db: Path) -> None:
    out = get_total_sessions_summary(seeded_db, "2026-06-01", "2026-06-02")
    assert out == {"sessions": 700, "available": True}


def test_total_sessions_summary_unavailable_when_never_ingested(empty_db: Path) -> None:
    out = get_total_sessions_summary(empty_db, "2026-06-01", "2026-06-02")
    assert out == {"sessions": 0, "available": False}


def test_click_session_gap_decomposition_all_sessions_and_gaps(seeded_db: Path) -> None:
    """Full funnel: clicks(1300) > lpv(880) > all_sessions(700) > attributed(650).
    capture_gap = LPV vs all-sessions (consent/tracking loss); attribution_gap =
    all-sessions vs campaign-attributed (utm tagging + consent-denied traffic)."""
    out = get_click_session_gap(seeded_db, "2026-06-01", "2026-06-02")
    lpv = 400 + 480
    all_sessions = 350 + 350
    attributed = 300 + 350

    assert out["ga4_sessions_all"] == all_sessions
    assert out["ga4_sessions_attributed"] == attributed
    # Backward-compat alias must still equal the campaign-attributed value.
    assert out["ga4_sessions"] == attributed

    assert out["capture_gap_pct"] == pytest.approx(round((1 - all_sessions / lpv) * 100, 1))
    assert out["attribution_gap_pct"] == pytest.approx(
        round((1 - attributed / all_sessions) * 100, 1)
    )
    # Both gaps are independently meaningful and distinct numbers -- the whole
    # point of the fix is that they must NOT collapse into a single blended gap.
    assert out["capture_gap_pct"] != out["attribution_gap_pct"]


def test_click_session_gap_decomposition_none_when_landing_pages_unavailable(tmp_path: Path) -> None:
    """ga4_landing_pages never ingested (but ga4_metrics/ad_metrics have data) ->
    ga4_sessions_all / capture_gap_pct / attribution_gap_pct must be None, not 0
    or a misleading number -- the legacy fields must still compute normally."""
    db = tmp_path / "metrics.db"
    con = _make_db(db)
    con.executescript("""
        INSERT INTO campaigns VALUES ('c1','meta_ads','Brand','ACTIVE','2026-06-01');
        INSERT INTO ad_metrics (campaign_id, date, ad_set_id, ad_id, spend, impressions,
                                 clicks, landing_page_views, fetched_at)
        VALUES ('c1','2026-06-01','','',10.0,1000,100,80,'2026-06-02');
        INSERT INTO ga4_metrics VALUES ('Brand','2026-06-01',40,30,20,0.3,10.0,1,'2026-06-02');
    """)
    con.commit()
    con.close()

    out = get_click_session_gap(db, "2026-06-01", "2026-06-01")
    assert out["ga4_sessions_all"] is None
    assert out["capture_gap_pct"] is None
    assert out["attribution_gap_pct"] is None
    # Legacy fields unaffected.
    assert out["ga4_sessions"] == 40
    assert out["gap_clicks_pct"] == pytest.approx(round((1 - 40 / 100) * 100, 1))


@pytest.mark.parametrize(
    "pct,expected",
    [(None, "gray"), (0.0, "green"), (30.0, "green"), (30.1, "amber"),
     (50.0, "amber"), (50.1, "red"), (90.0, "red")],
)
def test_capture_gap_band_thresholds(pct: float | None, expected: str) -> None:
    assert capture_gap_band(pct) == expected


@pytest.mark.parametrize(
    "pct,expected",
    [(None, "gray"), (0.0, "green"), (40.0, "green"), (40.1, "amber"),
     (70.0, "amber"), (70.1, "red"), (95.0, "red")],
)
def test_attribution_gap_band_thresholds(pct: float | None, expected: str) -> None:
    assert attribution_gap_band(pct) == expected


# ---------------------------------------------------------------------------
# get_ga4_not_set_share + not_set_share_band
# ---------------------------------------------------------------------------

def test_not_set_share_weighted_by_event_count(seeded_db: Path) -> None:
    out = get_ga4_not_set_share(seeded_db, "2026-06-01", "2026-06-02")
    # add_to_cart(80+95) + begin_checkout(60+70) + purchase(0) = 305 total
    # not_set: only begin_checkout row on 06-02 has campaign_utm='(not set)' -> 70
    assert out["total_count"] == 80 + 95 + 60 + 70
    assert out["not_set_count"] == 70
    assert out["share_pct"] == pytest.approx(round(70 * 100.0 / 305, 1))
    assert out["available"] is True


def test_not_set_share_unavailable_when_no_checkout_events(empty_db: Path) -> None:
    out = get_ga4_not_set_share(empty_db, "2026-06-01", "2026-06-02")
    assert out == {"share_pct": None, "not_set_count": 0, "total_count": 0, "available": False}


def test_not_set_share_treats_blank_utm_as_not_set(tmp_path: Path) -> None:
    db = tmp_path / "metrics.db"
    con = _make_db(db)
    con.executescript("""
        INSERT INTO ga4_events (event_name, date, campaign_utm, lp_slug, event_count) VALUES
            ('add_to_cart', '2026-06-01', '', 'routine', 10),
            ('add_to_cart', '2026-06-01', 'nowa_launch', 'routine', 90);
    """)
    con.commit()
    con.close()
    out = get_ga4_not_set_share(db, "2026-06-01", "2026-06-01")
    assert out["not_set_count"] == 10
    assert out["total_count"] == 100
    assert out["share_pct"] == pytest.approx(10.0)


@pytest.mark.parametrize(
    "pct,expected",
    [(None, "gray"), (0.0, "green"), (30.0, "green"), (30.1, "amber"),
     (60.0, "amber"), (60.1, "red"), (90.0, "red")],
)
def test_not_set_share_band_thresholds(pct: float | None, expected: str) -> None:
    assert not_set_share_band(pct) == expected


# ---------------------------------------------------------------------------
# get_quiz_funnel + get_quiz_cost_per_lead
# ---------------------------------------------------------------------------

def test_quiz_funnel_counts_restricted_to_slugs(tmp_path: Path) -> None:
    db = tmp_path / "metrics.db"
    con = _make_db(db)
    con.executescript("""
        INSERT INTO ga4_events (event_name, date, campaign_utm, lp_slug, event_count) VALUES
            ('page_view_lp',  '2026-06-01', 'q', 'routine-break', 200),
            ('quiz_complete', '2026-06-01', 'q', 'routine-break', 50),
            ('lead_submit',   '2026-06-01', 'q', 'routine-break', 20),
            ('page_view_lp',  '2026-06-01', 'q', 'unrelated-slug', 999);
    """)
    con.commit()
    con.close()

    out = get_quiz_funnel(db, "2026-06-01", "2026-06-01", QUIZ_SLUGS)
    assert out["page_view_lp"] == {"count": 200, "available": True}
    assert out["quiz_complete"] == {"count": 50, "available": True}
    assert out["lead_submit"] == {"count": 20, "available": True}


def test_quiz_funnel_unavailable_when_no_data(empty_db: Path) -> None:
    out = get_quiz_funnel(empty_db, "2026-06-01", "2026-06-02", QUIZ_SLUGS)
    assert all(v == {"count": 0, "available": False} for v in out.values())


def test_quiz_funnel_empty_slug_list(seeded_db: Path) -> None:
    out = get_quiz_funnel(seeded_db, "2026-06-01", "2026-06-02", [])
    assert all(v == {"count": 0, "available": False} for v in out.values())


def test_quiz_cost_per_lead_math(tmp_path: Path) -> None:
    db = tmp_path / "metrics.db"
    con = _make_db(db)
    con.executescript("""
        INSERT INTO campaigns VALUES
            ('c1', 'meta_ads', 'Nowa | LEADS | 3.A Quiz | 20260601', 'ACTIVE', '2026-06-01'),
            ('c2', 'meta_ads', 'Nowa | SALES | 1.A Preorder | 20260601', 'ACTIVE', '2026-06-01');
        INSERT INTO ad_metrics (campaign_id, date, ad_set_id, ad_id, spend, fetched_at) VALUES
            ('c1', '2026-06-01', '', '', 40.0, '2026-06-02'),
            ('c2', '2026-06-01', '', '', 999.0, '2026-06-02');
        INSERT INTO ga4_events (event_name, date, campaign_utm, lp_slug, event_count) VALUES
            ('lead_submit', '2026-06-01', 'q', 'routine-break', 20);
    """)
    con.commit()
    con.close()

    out = get_quiz_cost_per_lead(db, "2026-06-01", "2026-06-01", QUIZ_SLUGS)
    # Only the LEADS-named campaign's spend counts (40.0), not the SALES campaign's 999.0
    assert out["spend"] == pytest.approx(40.0)
    assert out["lead_submit"] == 20
    assert out["cpl"] == pytest.approx(2.0)
    assert out["leads_campaign_count"] == 1


def test_quiz_cost_per_lead_none_when_no_leads(empty_db: Path) -> None:
    out = get_quiz_cost_per_lead(empty_db, "2026-06-01", "2026-06-02", QUIZ_SLUGS)
    assert out["cpl"] is None
    assert out["spend"] == 0.0
    assert out["lead_submit"] == 0


def test_quiz_cost_per_lead_none_when_spend_but_no_leads(tmp_path: Path) -> None:
    db = tmp_path / "metrics.db"
    con = _make_db(db)
    con.executescript("""
        INSERT INTO campaigns VALUES ('c1', 'meta_ads', 'Nowa | LEADS | X', 'ACTIVE', '2026-06-01');
        INSERT INTO ad_metrics (campaign_id, date, ad_set_id, ad_id, spend, fetched_at) VALUES
            ('c1', '2026-06-01', '', '', 40.0, '2026-06-02');
    """)
    con.commit()
    con.close()
    out = get_quiz_cost_per_lead(db, "2026-06-01", "2026-06-01", QUIZ_SLUGS)
    assert out["cpl"] is None
    assert out["spend"] == pytest.approx(40.0)
    assert out["lead_submit"] == 0
