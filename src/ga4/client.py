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


def _scoped_conversion_metric(conversion_event: str) -> str:
    """Event-scoped keyEvents metric name for a single conversion event.

    D-08 fix: the unscoped ``keyEvents`` metric sums ALL key events configured on the
    GA4 property (this property has 4: begin_checkout, purchase, lead_submit,
    form_submit_deposit) — using it for ga4_purchases_lastclick massively inflates the
    count (verified live: unscoped keyEvents returned 29 for 2026-07-15..21 vs the
    correct keyEvents:purchase = 5). The event-scoped metric syntax ``keyEvents:<event>``
    restricts the count to exactly one event. The GA4 API echoes this exact string back
    as the metric header name in the response, so callers must look up the same string
    in the parsed row (see _parse_campaign_row / _parse_landing_row's conversion_metric
    parameter and the `raw.get(scoped_metric)` lookups in the two _fetch_*_sync functions).
    """
    return f"keyEvents:{conversion_event}"


def _parse_campaign_row(
    raw: dict, date_iso: str, conversion_metric: str = _CONVERSION_METRIC
) -> dict:
    """Map a raw GA4 API dimension+metric dict to a ga4_metrics-compatible dict.

    All int/float conversions use `int(raw.get(key) or 0)` pattern — safe on None and "".
    GA4-05: ga4_purchases_lastclick uses ga4_ prefix per CLAUDE.md.
    D-08: `conversion_metric` is the metric header name to read the conversion count
    from — the caller passes the event-scoped name (e.g. "keyEvents:purchase") since
    that's the header name the GA4 API returns for a scoped metric request. Defaults to
    the unscoped `_CONVERSION_METRIC` only so existing raw dicts built with the plain
    "keyEvents" key (e.g. hand-rolled test fixtures) keep working.
    """
    return {
        "campaign_utm": raw.get("sessionCampaignName", "") or "",
        "date": date_iso,
        "sessions": int(raw.get("sessions") or 0),
        "users": int(raw.get("totalUsers") or 0),
        "new_users": int(raw.get("newUsers") or 0),
        "bounce_rate": float(raw.get("bounceRate") or 0.0),
        "avg_engagement_time": float(raw.get("userEngagementDuration") or 0.0),
        "ga4_purchases_lastclick": int(raw.get(conversion_metric) or 0),
    }


def _parse_landing_row(
    raw: dict, date_iso: str, conversion_metric: str = _CONVERSION_METRIC
) -> dict:
    """Map a raw GA4 API landing page dict to a ga4_landing_pages-compatible dict.

    GA4-05: ga4_purchases_lastclick uses ga4_ prefix per CLAUDE.md.
    D-08: see _parse_campaign_row's `conversion_metric` docstring — same event-scoped
    keyEvents:<event> lookup applies here.
    """
    return {
        "landing_page": raw.get(_LANDING_PAGE_DIMENSION, "") or "",
        "date": date_iso,
        "sessions": int(raw.get("sessions") or 0),
        "total_users": int(raw.get("totalUsers") or 0),
        "ga4_purchases_lastclick": int(raw.get(conversion_metric) or 0),
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

    D-08: uses the event-scoped `keyEvents:<conversion_event>` metric name (NOT the
    unscoped "keyEvents", which sums every key event configured on the property —
    this one has 4: begin_checkout, purchase, lead_submit, form_submit_deposit — and
    silently inflates ga4_purchases_lastclick). GA4 has no eventName dimension in this
    request to filter on (it isn't requested), so metric-name scoping is the only way
    to restrict the count to one event; see _scoped_conversion_metric's docstring.
    Two-request architecture required: sessionCampaignName and landingPagePlusQueryString
    are in different scopes and cannot be combined (RESEARCH.md Critical Finding).
    """
    scoped_metric = _scoped_conversion_metric(conversion_event)
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
            Metric(name=scoped_metric),
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
        parsed = _parse_campaign_row(raw, date_iso, scoped_metric)
        if parsed["campaign_utm"] and parsed["campaign_utm"] != "(not set)":
            rows.append(parsed)
    return rows


def _normalise_ga4_date(raw_date: str, fallback: str) -> str:
    """Convert an 8-digit GA4 date string ("20260721") to ISO ("2026-07-21").

    Falls back to `fallback` (the request's start_date) when raw_date is empty —
    matches the pre-existing per-function inline helper this was promoted from.
    """
    d = raw_date or fallback
    if len(d) == 8:
        return f"{d[:4]}-{d[4:6]}-{d[6:]}"
    return d


def _fetch_landing_page_metrics_sync(
    client: BetaAnalyticsDataClient,
    property_id: str,
    start_date: str,
    end_date: str,
    conversion_event: str,
) -> list[dict]:
    """Synchronous GA4 landing page metrics fetch — called via asyncio.to_thread().

    Single-pass, grouped by landingPagePlusQueryString + date ONLY. The literal
    "(not set)" landing_page value is INCLUDED (not filtered out) — see below.

    Why this used to be a two-pass fetch, and why pass 2 was removed (2026-07-22):
    the previous version ran a second request grouped by pagePathPlusQueryString
    (filtered to sessionDefaultChannelGroup='Unassigned') to recover iOS/privacy
    sessions that show landingPagePlusQueryString = "(not set)", and merged those
    rows onto pass 1's by summing sessions per (page, date). That merge silently
    multi-counted: GA4's `sessions` metric, when grouped by a per-pageview
    dimension like pagePathPlusQueryString, counts a session once for EVERY PAGE
    the session touched (session × page combinations), not once per landing page.
    A single 5-page session was returned as 5 rows and got summed in 5 times.
    Verified live against property 534295825: ga4_landing_pages (two-pass) summed
    to 2,177 sessions for 2026-07-15..20, while the property's true total for
    15..21 (one MORE day) was only 1,803 — and a direct landingPagePlusQueryString
    -only grouping for 15..21 summed to 1,871, confirming pass 1 alone tracks the
    truth and pass 2 was the source of the overcount.

    Unassigned/iOS sessions — the ones pass 2 was trying to recover — now surface
    as a literal "(not set)" landing_page row (no longer filtered out, no longer
    redistributed across pageview rows) instead of being invisible or multi-counted.
    Callers that need an exact property-wide session total independent of any
    dimension grouping (dashboard "total sessions" figures) should read
    ga4_daily_totals (see fetch_daily_session_totals) rather than summing this
    table — grouping by landing page can still diverge from the property total
    for any date where a session's landing page value is ambiguous/changes.
    """
    scoped_metric = _scoped_conversion_metric(conversion_event)

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

    request = RunReportRequest(
        property=f"properties/{property_id}",
        dimensions=[Dimension(name=_LANDING_PAGE_DIMENSION), Dimension(name="date")],
        metrics=[
            Metric(name="sessions"),
            Metric(name="totalUsers"),
            Metric(name=scoped_metric),
            Metric(name="screenPageViews"),
            Metric(name="userEngagementDuration"),
        ],
        date_ranges=[DateRange(start_date=start_date, end_date=end_date)],
        # No dimension_filter: unlike the old pass 1, "(not set)" rows are kept —
        # they are the iOS/privacy-stripped sessions the removed pass 2 used to
        # (wrongly) try to recover via pagePath redistribution.
        return_property_quota=True,
        keep_empty_rows=False,
        limit=10000,
    )
    raw_rows = _paginate(request)

    rows = []
    for raw in raw_rows:
        page = raw.get(_LANDING_PAGE_DIMENSION, "") or ""
        if not page:
            continue
        date_key = _normalise_ga4_date(raw.get("date", ""), start_date)
        rows.append(_parse_landing_row(raw, date_key, scoped_metric))
    return rows


def _fetch_daily_totals_sync(
    client: BetaAnalyticsDataClient,
    property_id: str,
    start_date: str,
    end_date: str,
) -> list[dict]:
    """Synchronous exact property-wide daily session totals — asyncio.to_thread().

    dimensions=[date] ONLY, metrics=[sessions] ONLY — no landing-page/pagePath
    grouping whatsoever, so this is the literal GA4 "Sessions" total for the whole
    property per day. This is the fix for the session multi-counting bug in the
    old two-pass _fetch_landing_page_metrics_sync (see that function's docstring):
    any dimension-grouped fetch is at risk of a session being counted once per
    distinct dimension value it touches, but a bare date-only report has nothing
    else to group by, so each session can only land in exactly one row.
    """
    request = RunReportRequest(
        property=f"properties/{property_id}",
        dimensions=[Dimension(name="date")],
        metrics=[Metric(name="sessions")],
        date_ranges=[DateRange(start_date=start_date, end_date=end_date)],
        return_property_quota=True,
        keep_empty_rows=False,
        limit=10000,
    )
    response = client.run_report(request)
    logger.info("ga4_quota", tokens_per_project_per_hour=str(response.property_quota))

    rows = []
    for row in response.rows:
        dim_vals = {h.name: v.value for h, v in zip(response.dimension_headers, row.dimension_values)}
        met_vals = {h.name: v.value for h, v in zip(response.metric_headers, row.metric_values)}
        raw = {**dim_vals, **met_vals}
        date_key = _normalise_ga4_date(raw.get("date", ""), start_date)
        rows.append({"date": date_key, "sessions": int(raw.get("sessions") or 0)})
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


@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=2, max=60),
    retry=retry_if_exception_type(Exception),
    before_sleep=before_sleep_log(_stdlib_log, logging.WARNING),
    reraise=True,
)
async def fetch_daily_session_totals(
    client: BetaAnalyticsDataClient,
    property_id: str,
    start_date: str,
    end_date: str,
) -> list[dict]:
    """Async wrapper: fetch exact property-wide daily session totals for a date range.

    Feeds ga4_daily_totals — the source of truth get_total_sessions_daily /
    get_total_sessions_summary prefer over summing ga4_landing_pages (see
    _fetch_daily_totals_sync's docstring for why grouped fetches can't guarantee
    this).
    """
    logger.info("ga4_fetch_start", type="daily_totals", start=start_date, end=end_date)
    rows = await asyncio.to_thread(
        _fetch_daily_totals_sync, client, property_id, start_date, end_date
    )
    logger.info("ga4_fetch_complete", type="daily_totals", rows=len(rows))
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
