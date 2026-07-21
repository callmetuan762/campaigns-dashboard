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


# ---- D-08: event-scoped keyEvents:<event> metric (fixes ga4_purchases_lastclick
# counting ALL key events instead of just the configured conversion event) ----

def test_scoped_conversion_metric_builds_event_scoped_name():
    """D-08: the unscoped 'keyEvents' metric sums every key event on the property
    (this one has 4 — begin_checkout, purchase, lead_submit, form_submit_deposit).
    ga4_purchases_lastclick must use the event-scoped 'keyEvents:<event>' metric name
    instead so it counts only the configured conversion event."""
    from src.ga4.client import _scoped_conversion_metric
    assert _scoped_conversion_metric("purchase") == "keyEvents:purchase"


def test_scoped_conversion_metric_different_event():
    from src.ga4.client import _scoped_conversion_metric
    assert _scoped_conversion_metric("begin_checkout") == "keyEvents:begin_checkout"


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
    """GA4-02: Campaign row parser maps all expected fields.

    D-08: raw carries the event-scoped metric key ("keyEvents:purchase"), the header
    name the live GA4 API actually returns for a scoped metric request — not the bare
    "keyEvents" key. conversion_metric is passed explicitly as production code does.
    """
    from src.ga4.client import _parse_campaign_row
    raw = {
        "sessionCampaignName": "spring_sale",
        "date": "20260517",
        "sessions": "120",
        "totalUsers": "95",
        "newUsers": "40",
        "bounceRate": "0.45",
        "averageEngagementTimePerSession": "62.5",
        "keyEvents:purchase": "8",
    }
    row = _parse_campaign_row(raw, "2026-05-17", "keyEvents:purchase")
    assert row["campaign_utm"] == "spring_sale"
    assert row["date"] == "2026-05-17"
    assert row["sessions"] == 120
    assert row["users"] == 95
    assert row["new_users"] == 40
    assert abs(row["bounce_rate"] - 0.45) < 0.001
    assert row["ga4_purchases_lastclick"] == 8


def test_parse_campaign_row_ignores_other_key_events():
    """D-08 regression: a raw row carrying OTHER key events (begin_checkout,
    lead_submit) alongside the scoped purchase metric must not have them summed in —
    this is exactly the bug (unscoped keyEvents = 29 vs correct keyEvents:purchase = 5)."""
    from src.ga4.client import _parse_campaign_row
    raw = {
        "sessionCampaignName": "spring_sale",
        "sessions": "120",
        "keyEvents:purchase": "5",
        # Simulates what the OLD unscoped "keyEvents" metric would have summed —
        # must be ignored now that we look up the scoped key only.
        "keyEvents": "29",
    }
    row = _parse_campaign_row(raw, "2026-05-17", "keyEvents:purchase")
    assert row["ga4_purchases_lastclick"] == 5


def test_parse_campaign_row_default_conversion_metric_backward_compat():
    """conversion_metric defaults to the unscoped _CONVERSION_METRIC constant so
    older hand-rolled raw dicts (keyed by plain 'keyEvents') still parse."""
    from src.ga4.client import _parse_campaign_row
    row = _parse_campaign_row({"sessionCampaignName": "x", "keyEvents": "3"}, "2026-05-17")
    assert row["ga4_purchases_lastclick"] == 3


def test_parse_campaign_row_missing_fields_no_error():
    """GA4-02: Parser handles missing fields gracefully — no KeyError."""
    from src.ga4.client import _parse_campaign_row
    row = _parse_campaign_row({}, "2026-05-17", "keyEvents:purchase")
    assert row["sessions"] == 0
    assert row["ga4_purchases_lastclick"] == 0
    assert row["campaign_utm"] == ""


def test_parse_campaign_row_returns_correct_date():
    """GA4-03: date field in parsed row matches the passed date_iso."""
    from src.ga4.client import _parse_campaign_row
    raw = {"sessionCampaignName": "x", "sessions": "10"}
    row = _parse_campaign_row(raw, "2026-05-15", "keyEvents:purchase")
    assert row["date"] == "2026-05-15"


def test_parse_campaign_row_sessions_is_int():
    """GA4-02: Sessions are converted from string to int."""
    from src.ga4.client import _parse_campaign_row
    raw = {"sessionCampaignName": "spring_sale", "sessions": "42", "keyEvents:purchase": "5"}
    row = _parse_campaign_row(raw, "2026-05-17", "keyEvents:purchase")
    assert row["sessions"] == 42
    assert isinstance(row["sessions"], int)


# ---- Parser: _parse_landing_row ----

def test_parse_landing_row_all_fields():
    """GA4-02: Landing page row parser maps all expected fields.

    D-08: keyed by the event-scoped metric name, same as _parse_campaign_row.
    """
    from src.ga4.client import _parse_landing_row
    raw = {
        "landingPagePlusQueryString": "/products/shoes",
        "sessions": "45",
        "totalUsers": "38",
        "keyEvents:purchase": "3",
        "screenPageViews": "120",
        "averageEngagementTimePerSession": "55.2",
    }
    row = _parse_landing_row(raw, "2026-05-17", "keyEvents:purchase")
    assert row["landing_page"] == "/products/shoes"
    assert row["sessions"] == 45
    assert row["total_users"] == 38
    assert row["ga4_purchases_lastclick"] == 3
    assert row["screen_page_views"] == 120


def test_parse_landing_row_missing_fields_no_error():
    """GA4-02: Landing row parser handles missing fields gracefully."""
    from src.ga4.client import _parse_landing_row
    row = _parse_landing_row({}, "2026-05-17", "keyEvents:purchase")
    assert row["sessions"] == 0
    assert row["ga4_purchases_lastclick"] == 0
    assert row["landing_page"] == ""


# ---- D-08: sync fetch functions request the event-scoped metric ----

def _mock_response(dim_names: list[str], metric_names: list[str], dim_values: list[str], metric_values: list[str]):
    """Build a MagicMock GA4 RunReportResponse with one row."""
    from unittest.mock import MagicMock

    response = MagicMock()
    response.property_quota = "quota"
    response.dimension_headers = []
    for n in dim_names:
        h = MagicMock()
        h.name = n
        response.dimension_headers.append(h)
    response.metric_headers = []
    for n in metric_names:
        h = MagicMock()
        h.name = n
        response.metric_headers.append(h)

    row = MagicMock()
    row.dimension_values = []
    for v in dim_values:
        dv = MagicMock()
        dv.value = v
        row.dimension_values.append(dv)
    row.metric_values = []
    for v in metric_values:
        mv = MagicMock()
        mv.value = v
        row.metric_values.append(mv)
    response.rows = [row]
    response.row_count = 1
    return response


def test_fetch_campaign_metrics_sync_requests_scoped_metric_name():
    """D-08 regression: _fetch_campaign_metrics_sync must request 'keyEvents:purchase',
    not the unscoped 'keyEvents' — and must parse ga4_purchases_lastclick from that
    scoped header, not an inflated all-key-events total."""
    from unittest.mock import MagicMock

    from src.ga4.client import _fetch_campaign_metrics_sync

    captured_requests = []

    def fake_run_report(request):
        captured_requests.append(request)
        metric_names = [m.name for m in request.metrics]
        assert "keyEvents:purchase" in metric_names
        assert "keyEvents" not in metric_names
        return _mock_response(
            dim_names=["sessionCampaignName", "date"],
            metric_names=[
                "sessions", "totalUsers", "newUsers", "bounceRate",
                "userEngagementDuration", "keyEvents:purchase", "screenPageViews",
            ],
            dim_values=["nowa_launch", "20260721"],
            # Real bug: if the code mistakenly read the unscoped "keyEvents" total (29)
            # instead of the scoped "keyEvents:purchase" value (5), this test would catch it.
            metric_values=["100", "80", "40", "0.3", "50.0", "5", "150"],
        )

    mock_client = MagicMock()
    mock_client.run_report.side_effect = fake_run_report

    rows = _fetch_campaign_metrics_sync(mock_client, "534295825", "2026-07-21", "purchase")

    assert len(rows) == 1
    assert rows[0]["ga4_purchases_lastclick"] == 5
    assert rows[0]["campaign_utm"] == "nowa_launch"


def test_fetch_landing_page_metrics_sync_requests_scoped_metric_name():
    """D-08 regression: the landing-page fetch's Pass 1 / Pass 2 requests must both
    use the event-scoped metric name for the configured conversion_event."""
    from unittest.mock import MagicMock

    from src.ga4.client import _fetch_landing_page_metrics_sync

    def fake_run_report(request):
        metric_names = [m.name for m in request.metrics]
        assert "keyEvents:purchase" in metric_names
        assert "keyEvents" not in metric_names
        dim_names = [d.name for d in request.dimensions]
        if "landingPagePlusQueryString" in dim_names:
            return _mock_response(
                dim_names=dim_names,
                metric_names=[
                    "sessions", "totalUsers", "keyEvents:purchase",
                    "screenPageViews", "userEngagementDuration",
                ],
                dim_values=["/routine/", "20260721"],
                metric_values=["50", "40", "5", "60", "30.0"],
            )
        # Pass 2 (Unassigned channel) — no matching rows this test.
        response = MagicMock()
        response.property_quota = "quota"
        response.dimension_headers = []
        response.metric_headers = []
        response.rows = []
        response.row_count = 0
        return response

    mock_client = MagicMock()
    mock_client.run_report.side_effect = fake_run_report

    rows = _fetch_landing_page_metrics_sync(
        mock_client, "534295825", "2026-07-21", "2026-07-21", "purchase"
    )

    assert len(rows) == 1
    assert rows[0]["ga4_purchases_lastclick"] == 5


# ---- Async coroutine verification ----

def test_fetch_campaign_metrics_is_coroutine():
    """GA4-11: asyncio.to_thread wrapping — function must be async."""
    from src.ga4.client import fetch_campaign_metrics
    assert inspect.iscoroutinefunction(fetch_campaign_metrics)


def test_fetch_landing_page_metrics_is_coroutine():
    """GA4-11: asyncio.to_thread wrapping — function must be async."""
    from src.ga4.client import fetch_landing_page_metrics
    assert inspect.iscoroutinefunction(fetch_landing_page_metrics)


# ---------------------------------------------------------------------------
# funnel-v3: event-level report (GA4-12)
# ---------------------------------------------------------------------------

def test_event_name_dimension_constant():
    from src.ga4.client import _EVENT_NAME_DIMENSION
    assert _EVENT_NAME_DIMENSION == "eventName"


def test_lp_slug_dimension_constant():
    from src.ga4.client import _LP_SLUG_DIMENSION
    assert _LP_SLUG_DIMENSION == "customEvent:lp_slug"


def test_parse_event_row_all_fields():
    from src.ga4.client import _parse_event_row
    raw = {
        "eventName": "begin_checkout",
        "sessionCampaignName": "nowa_launch",
        "customEvent:lp_slug": "routine",
        "eventCount": "42",
    }
    row = _parse_event_row(raw, "2026-05-17")
    assert row["event_name"] == "begin_checkout"
    assert row["date"] == "2026-05-17"
    assert row["campaign_utm"] == "nowa_launch"
    assert row["lp_slug"] == "routine"
    assert row["event_count"] == 42
    assert isinstance(row["event_count"], int)


def test_parse_event_row_missing_lp_slug_defaults_empty_string():
    """When customEvent:lp_slug was dropped from the request, lp_slug defaults to ''."""
    from src.ga4.client import _parse_event_row
    raw = {"eventName": "purchase", "sessionCampaignName": "nowa_launch", "eventCount": "3"}
    row = _parse_event_row(raw, "2026-05-17")
    assert row["lp_slug"] == ""


def test_parse_event_row_missing_fields_no_error():
    from src.ga4.client import _parse_event_row
    row = _parse_event_row({}, "2026-05-17")
    assert row["event_name"] == ""
    assert row["campaign_utm"] == ""
    assert row["lp_slug"] == ""
    assert row["event_count"] == 0


def test_fetch_event_metrics_is_coroutine():
    from src.ga4.client import fetch_event_metrics
    assert inspect.iscoroutinefunction(fetch_event_metrics)


def test_fetch_event_metrics_sync_retries_without_lp_slug_on_error():
    """GA4-12: if customEvent:lp_slug is unregistered, retry without it — lp_slug=''."""
    from unittest.mock import MagicMock

    from google.api_core.exceptions import GoogleAPIError

    from src.ga4.client import _fetch_event_metrics_sync

    call_count = 0

    def fake_run_report(request):
        nonlocal call_count
        call_count += 1
        dim_names = [d.name for d in request.dimensions]
        if call_count == 1:
            assert "customEvent:lp_slug" in dim_names
            raise GoogleAPIError("customEvent:lp_slug is not a valid dimension")
        assert "customEvent:lp_slug" not in dim_names
        response = MagicMock()
        response.property_quota = "quota"
        header_names = [d.name for d in request.dimensions] + ["eventCount"]
        response.dimension_headers = [MagicMock(name=n) for n in dim_names]
        for h, n in zip(response.dimension_headers, dim_names):
            h.name = n
        response.metric_headers = [MagicMock()]
        response.metric_headers[0].name = "eventCount"

        row = MagicMock()
        dim_values = []
        for n in dim_names:
            v = MagicMock()
            if n == "date":
                v.value = "2026-05-17"
            elif n == "eventName":
                v.value = "purchase"
            elif n == "sessionCampaignName":
                v.value = "nowa_launch"
            else:
                v.value = ""
            dim_values.append(v)
        row.dimension_values = dim_values
        met_value = MagicMock()
        met_value.value = "7"
        row.metric_values = [met_value]
        response.rows = [row]
        return response

    mock_client = MagicMock()
    mock_client.run_report.side_effect = fake_run_report

    rows = _fetch_event_metrics_sync(mock_client, "534295825", "2026-05-17", ["purchase"])

    assert call_count == 2
    assert len(rows) == 1
    assert rows[0]["lp_slug"] == ""
    assert rows[0]["event_count"] == 7
    assert rows[0]["event_name"] == "purchase"
    assert rows[0]["campaign_utm"] == "nowa_launch"
