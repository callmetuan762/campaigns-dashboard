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

# Funnel v3 — event-level report (GA4-12)
_EVENT_NAME_DIMENSION = "eventName"
_LP_SLUG_DIMENSION = "customEvent:lp_slug"  # custom dimension; may be unregistered on the property


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


def _parse_event_row(raw: dict, date_iso: str) -> dict:
    """Map a raw GA4 event-level dimension+metric dict to a ga4_events-compatible dict.

    GA4-12: campaign_utm and lp_slug default to '' (not None) — composite PK requires
    NOT NULL DEFAULT '' to de-duplicate correctly (SQLite NULL != NULL in a PRIMARY KEY).
    lp_slug is '' whenever the customEvent:lp_slug dimension was dropped from the
    request (unregistered custom dimension — see _fetch_event_metrics_sync).
    """
    return {
        "event_name": raw.get(_EVENT_NAME_DIMENSION, "") or "",
        "date": date_iso,
        "campaign_utm": raw.get("sessionCampaignName", "") or "",
        "lp_slug": raw.get(_LP_SLUG_DIMENSION, "") or "",
        "event_count": int(raw.get("eventCount") or 0),
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

    Two-pass approach to capture ALL sessions including iOS/privacy-stripped ones:

    Pass 1 — landingPagePlusQueryString: captures Meta-attributed sessions (UTM/fbclid params
    intact). These are "Paid Other" sessions in GA4 with high engagement rates.

    Pass 2 — pagePathPlusQueryString + sessionDefaultChannelGroup='Unassigned': captures iOS
    sessions where ATT privacy strips UTM params. These show as "Unassigned" in GA4 with
    landingPagePlusQueryString = "(not set)", making them invisible to Pass 1.

    Results are merged by (landing_page_path, date), summing sessions and other metrics.
    For a single-page funnel like Nowa's, pageView on /slug/ == landing on /slug/.
    """
    from collections import defaultdict

    def _paginate(req: RunReportRequest) -> list[dict]:
        """Fetch all pages for a request, returning raw dicts."""
        raw_rows: list[dict] = []
        offset = 0
        while True:
            req.offset = offset
            resp = client.run_report(req)
            for row in resp.rows:
                dv = {h.name: v.value for h, v in zip(resp.dimension_headers, row.dimension_values)}
                mv = {h.name: v.value for h, v in zip(resp.metric_headers, row.metric_values)}
                raw_rows.append({**dv, **mv})
            total = resp.row_count if hasattr(resp, "row_count") else 0
            offset += len(resp.rows)
            if not resp.rows or offset >= total:
                break
        return raw_rows

    METRICS = [
        Metric(name="sessions"),
        Metric(name="totalUsers"),
        Metric(name=_CONVERSION_METRIC),
        Metric(name="screenPageViews"),
        Metric(name="userEngagementDuration"),
    ]
    DATE_RANGE = [DateRange(start_date=start_date, end_date=end_date)]

    # ── Pass 1: landingPagePlusQueryString (UTM-tagged, Paid Other sessions) ──────
    req1 = RunReportRequest(
        property=f"properties/{property_id}",
        dimensions=[Dimension(name=_LANDING_PAGE_DIMENSION), Dimension(name="date")],
        metrics=METRICS,
        date_ranges=DATE_RANGE,
        dimension_filter=FilterExpression(
            not_expression=FilterExpression(
                filter=Filter(
                    field_name=_LANDING_PAGE_DIMENSION,
                    string_filter=Filter.StringFilter(value="(not set)"),
                )
            )
        ),
        return_property_quota=True,
        keep_empty_rows=False,
        limit=10000,
    )
    pass1_raw = _paginate(req1)

    # ── Pass 2: pagePathPlusQueryString for Unassigned channel (iOS/privacy sessions) ─
    req2 = RunReportRequest(
        property=f"properties/{property_id}",
        dimensions=[
            Dimension(name="pagePathPlusQueryString"),
            Dimension(name="sessionDefaultChannelGroup"),
            Dimension(name="date"),
        ],
        metrics=METRICS,
        date_ranges=DATE_RANGE,
        dimension_filter=FilterExpression(
            filter=Filter(
                field_name="sessionDefaultChannelGroup",
                string_filter=Filter.StringFilter(value="Unassigned"),
            )
        ),
        return_property_quota=True,
        keep_empty_rows=False,
        limit=10000,
    )
    pass2_raw = _paginate(req2)

    # ── Merge: accumulate by (landing_page_key, date) ────────────────────────────
    # key = (landing_page_string, date_iso)
    merged: dict[tuple, dict] = {}

    def _add(page_key: str, date_key: str, raw: dict) -> None:
        """Upsert-accumulate metrics into merged dict."""
        k = (page_key, date_key)
        if k not in merged:
            merged[k] = {
                "landing_page": page_key,
                "date": date_key,
                "sessions": 0,
                "total_users": 0,
                "ga4_purchases_lastclick": 0,
                "screen_page_views": 0,
                "avg_engagement_time": 0.0,
                "_eng_sum": 0.0,
                "_eng_count": 0,
            }
        m = merged[k]
        m["sessions"]               += int(raw.get("sessions") or 0)
        m["total_users"]            += int(raw.get("totalUsers") or 0)
        m["ga4_purchases_lastclick"] += int(raw.get(_CONVERSION_METRIC) or 0)
        m["screen_page_views"]      += int(raw.get("screenPageViews") or 0)
        eng = float(raw.get("userEngagementDuration") or 0.0)
        if eng > 0:
            m["_eng_sum"]   += eng
            m["_eng_count"] += 1

    def _normalise_date(raw_date: str, fallback: str) -> str:
        d = raw_date or fallback
        if len(d) == 8:
            return f"{d[:4]}-{d[4:6]}-{d[6:]}"
        return d

    # Pass 1
    for raw in pass1_raw:
        page = raw.get(_LANDING_PAGE_DIMENSION, "") or ""
        if not page or page == "(not set)":
            continue
        _add(page, _normalise_date(raw.get("date", ""), start_date), raw)

    # Pass 2 (Unassigned)
    for raw in pass2_raw:
        page = raw.get("pagePathPlusQueryString", "") or ""
        if not page or page == "(not set)":
            continue
        _add(page, _normalise_date(raw.get("date", ""), start_date), raw)

    # Finalise avg_engagement_time
    rows = []
    for m in merged.values():
        if m["_eng_count"] > 0:
            m["avg_engagement_time"] = m["_eng_sum"] / m["_eng_count"]
        del m["_eng_sum"]
        del m["_eng_count"]
        rows.append(m)

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


def _build_event_name_filter(event_names: list[str]) -> FilterExpression:
    """FilterExpression restricting eventName to the configured funnel event list."""
    return FilterExpression(
        filter=Filter(
            field_name=_EVENT_NAME_DIMENSION,
            in_list_filter=Filter.InListFilter(values=list(event_names)),
        )
    )


def _fetch_event_metrics_sync(
    client: BetaAnalyticsDataClient,
    property_id: str,
    date_iso: str,
    event_names: list[str],
) -> list[dict]:
    """Synchronous GA4 event-level metrics fetch — called via asyncio.to_thread().

    GA4-12: dimensions [date, eventName, sessionCampaignName, customEvent:lp_slug],
    metric eventCount, restricted to the configured funnel event names (src.config
    Settings.ga4_event_list) via an eventName IN (...) filter.

    The customEvent:lp_slug dimension may be unregistered on the GA4 property (it has
    to be created as a custom dimension in the GA4 UI before the API will accept it).
    If the request errors, retry WITHOUT that dimension and fall back to lp_slug=''
    for every row (CLAUDE.md graceful degradation) rather than failing the whole fetch.
    """

    def _run(include_lp_slug: bool):
        dimensions = [
            Dimension(name="date"),
            Dimension(name=_EVENT_NAME_DIMENSION),
            Dimension(name="sessionCampaignName"),
        ]
        if include_lp_slug:
            dimensions.append(Dimension(name=_LP_SLUG_DIMENSION))
        request = RunReportRequest(
            property=f"properties/{property_id}",
            dimensions=dimensions,
            metrics=[Metric(name="eventCount")],
            date_ranges=[DateRange(start_date=date_iso, end_date=date_iso)],
            dimension_filter=_build_event_name_filter(event_names),
            return_property_quota=True,
            keep_empty_rows=False,
            limit=100000,
        )
        return client.run_report(request)

    try:
        response = _run(True)
    except GoogleAPIError as exc:
        logger.warning(
            "ga4_lp_slug_dimension_unavailable", dimension=_LP_SLUG_DIMENSION, error=str(exc)
        )
        response = _run(False)

    logger.info("ga4_quota", tokens_per_project_per_hour=str(response.property_quota))

    rows = []
    for row in response.rows:
        dim_vals = {h.name: v.value for h, v in zip(response.dimension_headers, row.dimension_values)}
        met_vals = {h.name: v.value for h, v in zip(response.metric_headers, row.metric_values)}
        raw = {**dim_vals, **met_vals}
        rows.append(_parse_event_row(raw, date_iso))
    return rows


@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=2, max=60),
    retry=retry_if_exception_type(Exception),
    before_sleep=before_sleep_log(_stdlib_log, logging.WARNING),
    reraise=True,
)
async def fetch_event_metrics(
    client: BetaAnalyticsDataClient,
    property_id: str,
    date_iso: str,
    event_names: list[str],
) -> list[dict]:
    """Async wrapper: fetch event-level GA4 metrics for a single date. GA4-12."""
    logger.info("ga4_fetch_start", type="events", date=date_iso, events=event_names)
    rows = await asyncio.to_thread(
        _fetch_event_metrics_sync, client, property_id, date_iso, event_names
    )
    logger.info("ga4_fetch_complete", type="events", date=date_iso, rows=len(rows))
    return rows
