"""Tests for src/ga4/client.py — dimension names, row parsers, and coroutine verification.

Covers: GA4-01 (auth pattern), GA4-02 (request structure), pitfall guards from RESEARCH.md.
"""
from __future__ import annotations

import inspect

import pytest


# ---- Dimension name guards (RESEARCH.md Critical Findings) ----

def test_landing_page_dimension_is_not_deprecated():
    """RESEARCH.md Pitfall 1: landingPagePlusQueryString, not landingPage."""
    from src.ga4.client import _LANDING_PAGE_DIMENSION
    assert _LANDING_PAGE_DIMENSION == "landingPagePlusQueryString"


def test_conversion_metric_is_not_deprecated():
    """RESEARCH.md Pitfall 1: keyEvents, not conversions."""
    from src.ga4.client import _CONVERSION_METRIC
    assert _CONVERSION_METRIC == "keyEvents"


def test_deprecated_dimension_name_not_used():
    """Regression: the deprecated bare name must not be the constant value."""
    from src.ga4.client import _LANDING_PAGE_DIMENSION
    assert _LANDING_PAGE_DIMENSION != "landingPage"


def test_deprecated_metric_name_not_used():
    """Regression: 'conversions' is not the conversion metric name."""
    from src.ga4.client import _CONVERSION_METRIC
    assert _CONVERSION_METRIC != "conversions"


# ---- Parser: _parse_campaign_row ----

def test_parse_campaign_row_all_fields():
    """GA4-02: Campaign row parser maps all expected fields."""
    from src.ga4.client import _parse_campaign_row
    raw = {
        "sessionCampaignName": "spring_sale",
        "date": "20260517",
        "sessions": "120",
        "totalUsers": "95",
        "newUsers": "40",
        "bounceRate": "0.45",
        "averageEngagementTimePerSession": "62.5",
        "keyEvents": "8",
    }
    row = _parse_campaign_row(raw, "2026-05-17")
    assert row["campaign_utm"] == "spring_sale"
    assert row["date"] == "2026-05-17"
    assert row["sessions"] == 120
    assert row["users"] == 95
    assert row["new_users"] == 40
    assert abs(row["bounce_rate"] - 0.45) < 0.001
    assert row["ga4_purchases_lastclick"] == 8


def test_parse_campaign_row_missing_fields_no_error():
    """GA4-02: Parser handles missing fields gracefully — no KeyError."""
    from src.ga4.client import _parse_campaign_row
    row = _parse_campaign_row({}, "2026-05-17")
    assert row["sessions"] == 0
    assert row["ga4_purchases_lastclick"] == 0
    assert row["campaign_utm"] == ""


def test_parse_campaign_row_returns_correct_date():
    """GA4-03: date field in parsed row matches the passed date_iso."""
    from src.ga4.client import _parse_campaign_row
    raw = {"sessionCampaignName": "x", "sessions": "10"}
    row = _parse_campaign_row(raw, "2026-05-15")
    assert row["date"] == "2026-05-15"


def test_parse_campaign_row_sessions_is_int():
    """GA4-02: Sessions are converted from string to int."""
    from src.ga4.client import _parse_campaign_row
    raw = {"sessionCampaignName": "spring_sale", "sessions": "42", "keyEvents": "5"}
    row = _parse_campaign_row(raw, "2026-05-17")
    assert row["sessions"] == 42
    assert isinstance(row["sessions"], int)


# ---- Parser: _parse_landing_row ----

def test_parse_landing_row_all_fields():
    """GA4-02: Landing page row parser maps all expected fields."""
    from src.ga4.client import _parse_landing_row
    raw = {
        "landingPagePlusQueryString": "/products/shoes",
        "sessions": "45",
        "totalUsers": "38",
        "keyEvents": "3",
        "screenPageViews": "120",
        "averageEngagementTimePerSession": "55.2",
    }
    row = _parse_landing_row(raw, "2026-05-17")
    assert row["landing_page"] == "/products/shoes"
    assert row["sessions"] == 45
    assert row["total_users"] == 38
    assert row["ga4_purchases_lastclick"] == 3
    assert row["screen_page_views"] == 120


def test_parse_landing_row_missing_fields_no_error():
    """GA4-02: Landing row parser handles missing fields gracefully."""
    from src.ga4.client import _parse_landing_row
    row = _parse_landing_row({}, "2026-05-17")
    assert row["sessions"] == 0
    assert row["ga4_purchases_lastclick"] == 0
    assert row["landing_page"] == ""


# ---- Async coroutine verification ----

def test_fetch_campaign_metrics_is_coroutine():
    """GA4-11: asyncio.to_thread wrapping — function must be async."""
    from src.ga4.client import fetch_campaign_metrics
    assert inspect.iscoroutinefunction(fetch_campaign_metrics)


def test_fetch_landing_page_metrics_is_coroutine():
    """GA4-11: asyncio.to_thread wrapping — function must be async."""
    from src.ga4.client import fetch_landing_page_metrics
    assert inspect.iscoroutinefunction(fetch_landing_page_metrics)
