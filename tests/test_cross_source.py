"""Cross-source join, attribution comparison, and UTM coverage tests.

Covers: CROSS-01 (exact UTM match only), CROSS-02 (side-by-side attribution),
        CROSS-03 (UTM coverage warning).
"""
from __future__ import annotations

import pytest


# ---- CROSS-01: Exact UTM match only ----

@pytest.mark.asyncio
async def test_ga4_metrics_campaign_utm_exact_match_required(db_client):
    """CROSS-01: UTM join uses exact string equality — different-case strings are NOT a match."""
    await db_client.upsert_ga4_metrics([{
        "campaign_utm": "spring_sale",
        "date": "2026-05-17",
        "sessions": 100,
        "users": 80,
        "new_users": 30,
        "bounce_rate": 0.4,
        "avg_engagement_time": 50.0,
        "ga4_purchases_lastclick": 5,
    }])
    # Lookup with exact match — found
    exact = await db_client.fetch_one(
        "SELECT campaign_utm FROM ga4_metrics WHERE campaign_utm = 'spring_sale'"
    )
    assert exact is not None

    # Lookup with different case — NOT found (exact match only per CLAUDE.md)
    fuzzy = await db_client.fetch_one(
        "SELECT campaign_utm FROM ga4_metrics WHERE campaign_utm = 'Spring Sale'"
    )
    assert fuzzy is None


# ---- CROSS-02: Side-by-side attribution comparison ----

def test_daily_report_includes_attribution_comparison_when_utms_match():
    """CROSS-02: Report output contains both 'Meta 7d-click' and 'GA4 last-click' for matched campaigns."""
    from src.reports.builder import build_daily_report_html
    meta_rows = [{
        "campaign_id": "c1",
        "campaign_name": "spring_sale",
        "spend": 500.0,
        "roas": 2.0,
        "meta_purchases_7dclick": 12,
    }]
    ga4_campaign_rows = [{
        "campaign_utm": "spring_sale",
        "sessions": 100,
        "ga4_purchases_lastclick": 8,
    }]
    result = build_daily_report_html(
        meta_rows, None, "2026-05-17",
        ga4_campaign_rows=ga4_campaign_rows,
        ga4_landing_rows=[],
    )
    assert "Meta 7d-click: 12" in result
    assert "GA4 last-click: 8" in result


def test_daily_report_attribution_includes_explanation():
    """CROSS-02: Attribution explanation text always present when comparison is shown."""
    from src.reports.builder import build_daily_report_html
    result = build_daily_report_html(
        [{"campaign_id": "c1", "campaign_name": "promo", "spend": 100.0, "roas": 1.5, "meta_purchases_7dclick": 5}],
        None, "2026-05-17",
        ga4_campaign_rows=[{"campaign_utm": "promo", "sessions": 50, "ga4_purchases_lastclick": 3}],
        ga4_landing_rows=[],
    )
    assert "Attribution difference is normal" in result


def test_attribution_comparison_not_blended():
    """CROSS-02: Meta and GA4 conversions must never be summed or averaged — always separate."""
    from src.reports.builder import build_daily_report_html
    result = build_daily_report_html(
        [{"campaign_id": "c1", "campaign_name": "x", "spend": 100.0, "roas": 2.0, "meta_purchases_7dclick": 10}],
        None, "2026-05-17",
        ga4_campaign_rows=[{"campaign_utm": "x", "sessions": 50, "ga4_purchases_lastclick": 6}],
        ga4_landing_rows=[],
    )
    # The blended sum (10+6=16) must NOT appear as a single purchases number
    assert "Purchases: 16" not in result
    # Both values appear separately
    assert "10" in result
    assert "6" in result


# ---- CROSS-03: UTM coverage warning ----

def test_utm_coverage_warning_shown_when_unmatched():
    """CROSS-03: Coverage warning shown when some Meta campaigns have no GA4 match."""
    from src.reports.builder import build_daily_report_html
    meta_rows = [
        {"campaign_id": "c1", "campaign_name": "spring_sale", "spend": 300.0, "roas": 2.0, "meta_purchases_7dclick": 8},
        {"campaign_id": "c2", "campaign_name": "brand_awareness", "spend": 200.0, "roas": 0.5, "meta_purchases_7dclick": 1},
    ]
    ga4_campaign_rows = [
        {"campaign_utm": "spring_sale", "sessions": 90, "ga4_purchases_lastclick": 5},
    ]
    result = build_daily_report_html(
        meta_rows, None, "2026-05-17",
        ga4_campaign_rows=ga4_campaign_rows,
        ga4_landing_rows=[],
    )
    assert "UTM coverage" in result
    assert "1/2" in result


def test_utm_coverage_warning_omitted_when_all_match():
    """CROSS-03 + D-07: Coverage warning is omitted entirely when all campaigns match GA4."""
    from src.reports.builder import build_daily_report_html
    meta_rows = [
        {"campaign_id": "c1", "campaign_name": "spring_sale", "spend": 300.0, "roas": 2.0, "meta_purchases_7dclick": 8},
    ]
    ga4_campaign_rows = [
        {"campaign_utm": "spring_sale", "sessions": 90, "ga4_purchases_lastclick": 5},
    ]
    result = build_daily_report_html(
        meta_rows, None, "2026-05-17",
        ga4_campaign_rows=ga4_campaign_rows,
        ga4_landing_rows=[],
    )
    assert "UTM coverage" not in result


def test_utm_coverage_warning_at_bottom_of_ga4_section():
    """D-07: Coverage warning appears after the GA4 section content, not at the top."""
    from src.reports.builder import build_daily_report_html
    meta_rows = [
        {"campaign_id": "c1", "campaign_name": "a", "spend": 100.0, "roas": 1.0, "meta_purchases_7dclick": 2},
        {"campaign_id": "c2", "campaign_name": "b", "spend": 100.0, "roas": 1.0, "meta_purchases_7dclick": 2},
    ]
    ga4_rows = [{"campaign_utm": "a", "sessions": 50, "ga4_purchases_lastclick": 1}]
    result = build_daily_report_html(
        meta_rows, None, "2026-05-17",
        ga4_campaign_rows=ga4_rows,
        ga4_landing_rows=[],
    )
    ga4_section_pos = result.find("Website (GA4)")
    utmcoverage_pos = result.find("UTM coverage")
    assert ga4_section_pos < utmcoverage_pos


# ---- GA4 section omission ----

def test_ga4_section_absent_when_no_data():
    """GA4-05: GA4 section not rendered when ga4_campaign_rows is None."""
    from src.reports.builder import build_daily_report_html
    result = build_daily_report_html([], None, "2026-05-17")
    assert "Website (GA4)" not in result


def test_ga4_section_absent_when_empty_lists():
    """GA4-05: GA4 section not rendered when both lists are empty."""
    from src.reports.builder import build_daily_report_html
    result = build_daily_report_html(
        [], None, "2026-05-17",
        ga4_campaign_rows=[],
        ga4_landing_rows=[],
    )
    assert "Website (GA4)" not in result


# ---- HTML injection guard ----

def test_landing_page_html_escaped_in_output():
    """CLAUDE.md: html.escape() applied to landing page paths before Telegram HTML output."""
    from src.reports.builder import build_daily_report_html
    malicious_path = "</b><script>alert(1)</script>"
    result = build_daily_report_html(
        [], None, "2026-05-17",
        ga4_campaign_rows=[{"campaign_utm": "x", "sessions": 5, "ga4_purchases_lastclick": 0}],
        ga4_landing_rows=[{"landing_page": malicious_path, "sessions": 5, "ga4_purchases_lastclick": 1}],
    )
    assert malicious_path not in result
    assert "<script>" not in result
    assert "&lt;/b&gt;" in result


# ---- 7-day trend (D-04) ----

def test_daily_report_7day_trend_shown_when_provided():
    """D-04: 7-day trend summary line present when ga4_landing_7day_rows provided."""
    from src.reports.builder import build_daily_report_html
    result = build_daily_report_html(
        [], None, "2026-05-17",
        ga4_campaign_rows=[{"campaign_utm": "x", "sessions": 10, "ga4_purchases_lastclick": 1}],
        ga4_landing_rows=[],
        ga4_landing_7day_rows=[
            {"landing_page": "/a", "sessions": 42, "ga4_purchases_lastclick": 3},
            {"landing_page": "/b", "sessions": 28, "ga4_purchases_lastclick": 1},
        ],
    )
    assert "7-day avg" in result


def test_daily_report_7day_trend_absent_when_not_provided():
    """D-04: 7-day trend summary line absent when ga4_landing_7day_rows is None."""
    from src.reports.builder import build_daily_report_html
    result = build_daily_report_html(
        [], None, "2026-05-17",
        ga4_campaign_rows=[{"campaign_utm": "x", "sessions": 10, "ga4_purchases_lastclick": 1}],
        ga4_landing_rows=[],
        ga4_landing_7day_rows=None,
    )
    assert "7-day avg" not in result


# ---- Weekly WoW GA4 section (D-05) ----

def test_weekly_report_ga4_wow_section_present():
    """D-05: Weekly report includes GA4 WoW section when ga4_this_week provided."""
    from src.reports.builder import build_weekly_report_html
    result = build_weekly_report_html(
        [], [], None, "2026-05-17",
        ga4_this_week=[{"landing_page": "/", "sessions": 280, "ga4_purchases_lastclick": 10}],
        ga4_last_week=[{"landing_page": "/", "sessions": 240, "ga4_purchases_lastclick": 8}],
    )
    assert "Website (GA4) Week-over-Week" in result
    assert "Sessions" in result
