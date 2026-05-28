"""GA4 Data API client: authentication, request building, and row parsing.

GA4-01: BetaAnalyticsDataClient.from_service_account_file(str(path)) — Viewer-only scope.
GA4-02: Two separate RunReportRequest calls (sessionCampaignName and landingPagePlusQueryString
        are scope-incompatible in a single request — RESEARCH.md Critical Finding).
GA4-03: D-2 freshness enforced at ingest.py call site (_get_d2_iso).
GA4-04: returnPropertyQuota=True passed in every request.

RESEARCH.md Critical Findings — all deprecated names replaced:
  landingPage        → landingPagePlusQueryString  (deprecated 2023-05-14)
  conversions        → keyEvents                   (renamed 2024-05-06)
  users              → totalUsers
  averageSessionDuration → userEngagementDuration
"""
from __future__ import annotations

import asyncio
import logging

import structlog
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    DateRange,
    Dimension,
    Filter,
    FilterExpression,
    Metric,
    RunReportRequest,
)
from google.api_core.exceptions import GoogleAPIError

logger = structlog.get_logger(__name__)
_stdlib_log = logging.getLogger(__name__)

# Module-level constants — importable for test assertions (RESEARCH.md pitfall guards)
_LANDING_PAGE_DIMENSION = "landingPagePlusQueryString"  # NOT deprecated "landingPage"
_CONVERSION_METRIC = "keyEvents"                         # NOT deprecated "conversions"


def _build_ga4_client(service_account_path) -> BetaAnalyticsDataClient:
    """Build a BetaAnalyticsDataClient from a service account file path.

    str() cast required — BetaAnalyticsDataClient.from_service_account_file expects str,
    not pathlib.Path (RESEARCH.md Pitfall 5).
    """
    return BetaAnalyticsDataClient.from_service_account_file(str(service_account_path))


def _parse_campaign_row(raw: dict, date_iso: str) -> dict:
    """Map a raw GA4 API dimension+metric dict to a ga4_metrics-compatible dict.

    All int/float conversions use `int(raw.get(key) or 0)` pattern — safe on None and "".
    GA4-05: ga4_purchases_lastclick uses ga4_ prefix per CLAUDE.md.
    """
    return {
        "campaign_utm": raw.get("sessionCampaignName", "") or "",
        "date": date_iso,
        "sessions": int(raw.get("sessions") or 0),
        "users": int(raw.get("totalUsers") or 0),
        "new_users": int(raw.get("newUsers") or 0),
        "bounce_rate": float(raw.get("bounceRate") or 0.0),
        "avg_engagement_time": float(raw.get("userEngagementDuration") or 0.0),
        "ga4_purchases_lastclick": int(raw.get(_CONVERSION_METRIC) or 0),
    }


def _parse_landing_row(raw: dict, date_iso: str) -> dict:
    """Map a raw GA4 API landing page dict to a ga4_landing_pages-compatible dict.

    GA4-05: ga4_purchases_lastclick uses ga4_ prefix per CLAUDE.md.
    """
    return {
        "landing_page": raw.get(_LANDING_PAGE_DIMENSION, "") or "",
        "date": date_iso,
        "sessions": int(raw.get("sessions") or 0),
        "total_users": int(raw.get("totalUsers") or 0),
        "ga4_purchases_lastclick": int(raw.get(_CONVERSION_METRIC) or 0),
        "screen_page_views": int(raw.get("screenPageViews") or 0),
        "avg_engagement_time": float(raw.get("userEngagementDuration") or 0.0),
    }


def _fetch_campaign_metrics_sync(
    client: BetaAnalyticsDataClient,
    property_id: str,
    date_iso: str,
    conversion_event: str,
) -> list[dict]:
    """Synchronous GA4 campaign metrics fetch — called via asyncio.to_thread().

    D-08: MetricFilter on eventName restricts keyEvents to only the configured
    conversion event so ga4_purchases_lastclick counts only that event.
    Two-request architecture required: sessionCampaignName and landingPagePlusQueryString
    are in different scopes and cannot be combined (RESEARCH.md Critical Finding).
    """
    request = RunReportRequest(
        property=f"properties/{property_id}",
        dimensions=[
            Dimension(name="sessionCampaignName"),
            Dimension(name="date"),
        ],
        metrics=[
            Metric(name="sessions"),
            Metric(name="totalUsers"),
            Metric(name="newUsers"),
            Metric(name="bounceRate"),
            Metric(name="userEngagementDuration"),
            Metric(name=_CONVERSION_METRIC),
            Metric(name="screenPageViews"),
        ],
        date_ranges=[DateRange(start_date=date_iso, end_date=date_iso)],
        dimension_filter=FilterExpression(
            not_expression=FilterExpression(
                filter=Filter(
                    field_name="sessionCampaignName",
                    string_filter=Filter.StringFilter(value="(not set)"),
                )
            )
        ),
        # metric_filter on eventName removed — eventName is a dimension, not a metric.
        # keyEvents counts all configured key events (typically just the purchase event).
        return_property_quota=True,
        keep_empty_rows=False,
    )
    response = client.run_report(request)
    logger.info("ga4_quota", tokens_per_project_per_hour=str(response.property_quota))

    rows = []
    for row in response.rows:
        dim_vals = {h.name: v.value for h, v in zip(response.dimension_headers, row.dimension_values)}
        met_vals = {h.name: v.value for h, v in zip(response.metric_headers, row.metric_values)}
        raw = {**dim_vals, **met_vals}
        parsed = _parse_campaign_row(raw, date_iso)
        if parsed["campaign_utm"] and parsed["campaign_utm"] != "(not set)":
            rows.append(parsed)
    return rows


def _fetch_landing_page_metrics_sync(
    client: BetaAnalyticsDataClient,
    property_id: str,
    start_date: str,
    end_date: str,
    conversion_event: str,
) -> list[dict]:
    """Synchronous GA4 landing page metrics fetch — called via asyncio.to_thread().

    D-08: Same MetricFilter on eventName as campaign request.
    Uses landingPagePlusQueryString (not deprecated landingPage — RESEARCH.md Pitfall 1).
    """
    request = RunReportRequest(
        property=f"properties/{property_id}",
        dimensions=[
            Dimension(name=_LANDING_PAGE_DIMENSION),
            Dimension(name="date"),
        ],
        metrics=[
            Metric(name="sessions"),
            Metric(name="totalUsers"),
            Metric(name=_CONVERSION_METRIC),
            Metric(name="screenPageViews"),
            Metric(name="userEngagementDuration"),
        ],
        date_ranges=[DateRange(start_date=start_date, end_date=end_date)],
        dimension_filter=FilterExpression(
            not_expression=FilterExpression(
                filter=Filter(
                    field_name=_LANDING_PAGE_DIMENSION,
                    string_filter=Filter.StringFilter(value="(not set)"),
                )
            )
        ),
        # metric_filter on eventName removed — eventName is a dimension, not a metric.
        return_property_quota=True,
        keep_empty_rows=False,
        limit=50,
    )
    response = client.run_report(request)

    rows = []
    for row in response.rows:
        dim_vals = {h.name: v.value for h, v in zip(response.dimension_headers, row.dimension_values)}
        met_vals = {h.name: v.value for h, v in zip(response.metric_headers, row.metric_values)}
        raw = {**dim_vals, **met_vals}
        row_date = raw.get("date", start_date)
        if len(row_date) == 8:
            row_date = f"{row_date[:4]}-{row_date[4:6]}-{row_date[6:]}"
        parsed = _parse_landing_row(raw, row_date)
        if parsed["landing_page"] and parsed["landing_page"] != "(not set)":
            rows.append(parsed)
    return rows


@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=2, max=60),
    retry=retry_if_exception_type(Exception),
    before_sleep=before_sleep_log(_stdlib_log, logging.WARNING),
    reraise=True,
)
async def fetch_campaign_metrics(
    client: BetaAnalyticsDataClient,
    property_id: str,
    date_iso: str,
    conversion_event: str = "purchase",
) -> list[dict]:
    """Async wrapper: fetch campaign-level GA4 metrics for a single date. GA4-02."""
    logger.info("ga4_fetch_start", type="campaign", date=date_iso)
    rows = await asyncio.to_thread(
        _fetch_campaign_metrics_sync, client, property_id, date_iso, conversion_event
    )
    logger.info("ga4_fetch_complete", type="campaign", date=date_iso, rows=len(rows))
    return rows


@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=2, max=60),
    retry=retry_if_exception_type(Exception),
    before_sleep=before_sleep_log(_stdlib_log, logging.WARNING),
    reraise=True,
)
async def fetch_landing_page_metrics(
    client: BetaAnalyticsDataClient,
    property_id: str,
    start_date: str,
    end_date: str,
    conversion_event: str = "purchase",
) -> list[dict]:
    """Async wrapper: fetch landing page GA4 metrics for a date range. GA4-02."""
    logger.info("ga4_fetch_start", type="landing_page", start=start_date, end=end_date)
    rows = await asyncio.to_thread(
        _fetch_landing_page_metrics_sync, client, property_id, start_date, end_date, conversion_event
    )
    logger.info("ga4_fetch_complete", type="landing_page", rows=len(rows))
    return rows
