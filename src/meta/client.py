"""Meta Marketing API client: initialization, insights fetch, and row parsing.

META-01: Authenticates via long-lived System User token (META_ACCESS_TOKEN env var).
META-02: Fetches campaign-level metrics: spend, impressions, clicks, CTR, CPC, CPM,
         ROAS, purchases, cost-per-purchase, reach, frequency.
META-03: Ad-set and ad-level breakdowns via level param.
META-04: All API calls wrapped in tenacity retry with exponential backoff.

CLAUDE.md: Read-only API access only. No write/bidding calls.
CLAUDE.md: Target Meta API v24.0+ (v23 deprecated June 9 2026).
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

from facebook_business.api import FacebookAdsApi
from facebook_business.adobjects.adaccount import AdAccount
from facebook_business.exceptions import FacebookRequestError

logger = structlog.get_logger(__name__)

# tenacity requires stdlib logger for before_sleep_log hook
_stdlib_log = logging.getLogger(__name__)

# Campaign-level fields requested from Meta Insights API (META-02)
#
# funnel-v3 additions (verified against the facebook-business SDK version pinned in
# pyproject.toml — AdsInsights.Field enum in this SDK release):
#   - landing_page_view / video 3-second views / InitiateCheckout / AddToCart / Lead
#     are all parsed from the existing "actions" / "cost_per_action_type" lists below
#     (no new top-level field needed — same pattern as the existing purchase parsing).
#   - video_thruplay_watched_actions IS a distinct top-level field (list<AdsActionStats>,
#     confirmed present on AdsInsights.Field in this SDK version) and must be requested
#     explicitly. If Meta rejects it for a given ad account, _fetch_insights_sync retries
#     once without it and video_thruplay degrades to NULL (CLAUDE.md graceful degradation).
_CAMPAIGN_FIELDS = [
    "campaign_id",
    "campaign_name",
    "spend",
    "impressions",
    "clicks",
    "ctr",
    "cpc",
    "cpm",
    "reach",
    "frequency",
    "purchase_roas",                   # list[{action_type, value}] — parse via _extract_action_value
    "actions",                         # list[{action_type, value}] — purchase, landing_page_view, video_view, InitiateCheckout, AddToCart, Lead
    "cost_per_action_type",            # list[{action_type, value}] — cost per purchase / InitiateCheckout
    "conversions",                     # list[{action_type, value}] — includes custom pixel events like form_submit_deposit
    "video_thruplay_watched_actions",  # list[{action_type, value}] — ThruPlay count (optional/degradable field)
]

# Optional fields that may not be available on every ad account / API version.
# _fetch_insights_sync retries without these on FacebookRequestError (graceful degradation).
_OPTIONAL_FIELDS = ["video_thruplay_watched_actions"]


def init_meta_api(settings) -> None:
    """Initialize the facebook-business SDK with System User token (D-04, D-06).

    Synchronous — called once from ingest.py at startup or before first API call.
    api_version='v24.0' per CLAUDE.md (v23 deprecated June 9 2026).
    Does NOT log the token value (CLAUDE.md security non-negotiable).
    """
    FacebookAdsApi.init(
        app_id=settings.meta_app_id,
        app_secret=settings.meta_app_secret.get_secret_value(),
        access_token=settings.meta_access_token.get_secret_value(),
        api_version="v24.0",
    )
    logger.info("meta_api_initialized", app_id=settings.meta_app_id)


def _extract_action_value(actions: list | None, action_type: str) -> float:
    """Extract numeric value for a specific action_type from a Meta actions list.

    Handles the Meta API pattern where purchase_roas, actions, and cost_per_action_type
    are all returned as list[{action_type: str, value: str}] — NOT as float scalars.
    (RESEARCH Pitfall 4: purchase_roas is a list)
    """
    if not actions:
        return 0.0
    for a in actions:
        if a.get("action_type") == action_type:
            return float(a.get("value", 0))
    return 0.0


def _parse_insight_row(row: dict, date_iso: str, level: str = "campaign") -> dict:
    """Normalize a raw Meta Insights API row into the ad_metrics schema shape.

    ad_set_id and ad_id default to '' (sentinel) for campaign-level rows (META-05).
    meta_ prefix applied to conversion fields per CLAUDE.md data model rules.

    funnel-v3: landing_page_views / video_3s_views / meta_begin_checkout /
    meta_add_to_cart / meta_leads are parsed from the "actions" list (same pattern as
    the existing purchase parsing). meta_cost_per_begin_checkout comes from
    "cost_per_action_type". video_thruplay comes from the separate
    "video_thruplay_watched_actions" field and is None (not 0) when that field was
    dropped by the graceful-degradation retry in _fetch_insights_sync — distinguishing
    "field unavailable" from "zero thruplay views".
    """
    purchases = _extract_action_value(
        row.get("actions"), "offsite_conversion.fb_pixel_purchase"
    )
    roas = _extract_action_value(
        row.get("purchase_roas") or [], "omni_purchase"
    )
    cost_per_purchase = _extract_action_value(
        row.get("cost_per_action_type"), "offsite_conversion.fb_pixel_purchase"
    )
    form_submit_deposit = _extract_action_value(
        row.get("conversions"), "offsite_conversion.fb_pixel_custom.form_submit_deposit"
    )

    # funnel-v3: landing page views + video hook/hold metrics
    landing_page_views = _extract_action_value(row.get("actions"), "landing_page_view")
    video_3s_views = _extract_action_value(row.get("actions"), "video_view")

    thruplay_raw = row.get("video_thruplay_watched_actions")
    if thruplay_raw is None:
        video_thruplay: float | None = None
    else:
        video_thruplay = _extract_action_value(thruplay_raw, "video_view")

    # funnel-v3: Shopify preorder funnel — InitiateCheckout / AddToCart / Lead
    begin_checkout = _extract_action_value(
        row.get("actions"), "offsite_conversion.fb_pixel_initiate_checkout"
    )
    cost_per_begin_checkout = _extract_action_value(
        row.get("cost_per_action_type"), "offsite_conversion.fb_pixel_initiate_checkout"
    )
    add_to_cart = _extract_action_value(
        row.get("actions"), "offsite_conversion.fb_pixel_add_to_cart"
    )
    leads = _extract_action_value(row.get("actions"), "offsite_conversion.fb_pixel_lead")

    return {
        "campaign_id": row.get("campaign_id", ""),
        "campaign_name": row.get("campaign_name", ""),
        "date": date_iso,
        "ad_set_id": row.get("adset_id", "") if level in ("adset", "ad") else "",
        "ad_id": row.get("ad_id", "") if level == "ad" else "",
        "spend": float(row.get("spend", 0) or 0),
        "impressions": int(row.get("impressions", 0) or 0),
        "clicks": int(row.get("clicks", 0) or 0),
        "ctr": float(row.get("ctr", 0) or 0),
        "cpc": float(row.get("cpc", 0) or 0),
        "cpm": float(row.get("cpm", 0) or 0),
        "roas": roas,
        "meta_purchases_7dclick": int(purchases),
        "meta_cost_per_purchase": cost_per_purchase,
        "reach": int(row.get("reach", 0) or 0),
        "frequency": float(row.get("frequency", 0) or 0),
        "meta_form_submit_deposit": int(form_submit_deposit),
        "landing_page_views": int(landing_page_views),
        "video_3s_views": int(video_3s_views),
        "video_thruplay": int(video_thruplay) if video_thruplay is not None else None,
        "meta_begin_checkout": int(begin_checkout),
        "meta_cost_per_begin_checkout": cost_per_begin_checkout,
        "meta_add_to_cart": int(add_to_cart),
        "meta_leads": int(leads),
    }


def _fetch_insights_sync(ad_account_id: str, date_iso: str, level: str) -> list[dict]:
    """Synchronous Meta API call — called via asyncio.to_thread() from async context.

    RESEARCH Pitfall 1: facebook-business SDK uses synchronous 'requests' library;
    calling this directly in an async def blocks the aiogram event loop.
    """
    account = AdAccount(f"act_{ad_account_id.removeprefix('act_')}")
    # adset_id must be explicitly requested at adset level when time_increment=1 is set —
    # without it the API omits the field and _parse_insight_row falls back to ad_set_id=''
    # (campaign-level sentinel), causing adset UPSERTs to silently overwrite campaign rows.
    extra = []
    if level == "adset":
        extra = ["adset_id"]
    elif level == "ad":
        extra = ["adset_id", "ad_id", "ad_name"]
    fields = list(_CAMPAIGN_FIELDS) + extra
    params = {
        "level": level,
        "time_range": {"since": date_iso, "until": date_iso},
        "time_increment": 1,  # required: without this, some campaigns return $0 spend incorrectly
        "limit": 500,
    }

    def _collect(field_list: list[str]) -> list[dict]:
        cursor = account.get_insights(fields=field_list, params=params)
        collected = []
        while True:
            collected.extend([_parse_insight_row(dict(r), date_iso, level) for r in cursor])
            if cursor.load_next_page() is False:
                break
        return collected

    try:
        return _collect(fields)
    except FacebookRequestError as exc:
        # Graceful degradation (CLAUDE.md): if an optional funnel-v3 field (e.g.
        # video_thruplay_watched_actions) is rejected by this ad account / API version,
        # retry once without it rather than failing the whole ingest. That metric then
        # comes back as None via _parse_insight_row's "field absent" branch.
        offending = [f for f in _OPTIONAL_FIELDS if f in fields and f in str(exc)]
        if not offending:
            raise
        logger.warning(
            "meta_optional_field_unavailable_retrying",
            fields=offending,
            level=level,
            date=date_iso,
            error=str(exc),
        )
        fallback_fields = [f for f in fields if f not in offending]
        return _collect(fallback_fields)


@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=2, max=60),
    retry=retry_if_exception_type(FacebookRequestError),
    before_sleep=before_sleep_log(_stdlib_log, logging.WARNING),
    reraise=True,
)
async def fetch_campaign_insights(ad_account_id: str, date_iso: str) -> list[dict]:
    """Fetch campaign-level insights for a single date. Async, retried on FacebookRequestError.

    Returns list of dicts shaped to match ad_metrics schema (META-02, META-05).
    D-07: Pull campaign-level metrics for yesterday's date.
    """
    logger.info("meta_fetch_start", level="campaign", date=date_iso)
    rows = await asyncio.to_thread(_fetch_insights_sync, ad_account_id, date_iso, "campaign")
    logger.info("meta_fetch_complete", level="campaign", date=date_iso, rows=len(rows))
    return rows


@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=2, max=60),
    retry=retry_if_exception_type(FacebookRequestError),
    before_sleep=before_sleep_log(_stdlib_log, logging.WARNING),
    reraise=True,
)
async def fetch_adset_insights(ad_account_id: str, date_iso: str) -> list[dict]:
    """Fetch ad-set level insights for a single date. META-03.

    ad_set_id is set from adset_id field; ad_id remains '' sentinel.
    """
    logger.info("meta_fetch_start", level="adset", date=date_iso)
    rows = await asyncio.to_thread(_fetch_insights_sync, ad_account_id, date_iso, "adset")
    logger.info("meta_fetch_complete", level="adset", date=date_iso, rows=len(rows))
    return rows


@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=2, max=60),
    retry=retry_if_exception_type(FacebookRequestError),
    before_sleep=before_sleep_log(_stdlib_log, logging.WARNING),
    reraise=True,
)
async def fetch_ad_insights(ad_account_id: str, date_iso: str) -> list[dict]:
    """Fetch ad-level insights for a single date. META-03.

    Both ad_set_id and ad_id are set from API response fields.
    """
    logger.info("meta_fetch_start", level="ad", date=date_iso)
    rows = await asyncio.to_thread(_fetch_insights_sync, ad_account_id, date_iso, "ad")
    logger.info("meta_fetch_complete", level="ad", date=date_iso, rows=len(rows))
    return rows


# Fields requested for the /activities endpoint (AdActivity object)
_ACTIVITY_FIELDS = [
    "object_id",
    "object_name",
    "object_type",
    "event_type",
    "actor_name",
    "extra_data",           # JSON string: {"current_value": {...}, "previous_value": {...}}
    "date_time_in_timezone",
    "event_time",
]

# Map AdActivity.Category values to watch for campaign/ad-set/ad changes
# Use the actual enum values (uppercase strings required by the API)
_ACTIVITY_CATEGORIES = ["CAMPAIGN", "AD_SET", "AD"]


def _normalize_change_time(d: dict) -> str:
    """Return an ISO-8601 datetime string from the API activity row.

    Prefers `event_time` (Unix timestamp int) for reliability.  Falls back to
    parsing `date_time_in_timezone` which Meta returns as "DD/MM/YYYY at HH:MM".
    """
    from datetime import datetime, timezone

    # event_time is a Unix timestamp (int or string)
    event_ts = d.get("event_time")
    if event_ts:
        try:
            return datetime.fromtimestamp(int(event_ts), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except (ValueError, TypeError):
            pass

    # Fallback: parse the "DD/MM/YYYY at HH:MM" human-readable string
    raw = str(d.get("date_time_in_timezone") or "")
    if raw:
        try:
            # Handle "23/05/2026 at 12:07"
            clean = raw.replace(" at ", " ")
            dt = datetime.strptime(clean, "%d/%m/%Y %H:%M")
            return dt.strftime("%Y-%m-%dT%H:%M:%S")
        except ValueError:
            pass

    return raw


def _parse_extra_data(extra_data_str: str | None) -> tuple[list[str], str | None, str | None]:
    """Parse extra_data JSON → (changed_fields, old_value_json, new_value_json)."""
    import json as _json
    if not extra_data_str:
        return [], None, None
    try:
        data = _json.loads(extra_data_str) if isinstance(extra_data_str, str) else extra_data_str
        old_val = data.get("previous_value") or data.get("old_value")
        new_val = data.get("current_value") or data.get("new_value")
        # Derive changed field names from the keys present in new_val
        changed = list(new_val.keys()) if isinstance(new_val, dict) else []
        return (
            changed,
            _json.dumps(old_val) if old_val else None,
            _json.dumps(new_val) if new_val else None,
        )
    except Exception:  # noqa: BLE001
        return [], None, None


# Meta's /activities edge uses legacy internal type names that differ from the
# Marketing API hierarchy users see in Ads Manager.  Normalize to display names.
_OBJECT_TYPE_MAP = {
    "CAMPAIGN_GROUP": "CAMPAIGN",   # UI "Campaign" = API CAMPAIGN_GROUP
    "CAMPAIGN": "AD_SET",           # UI "Ad Set"   = API CAMPAIGN
    "ADGROUP": "AD",                # UI "Ad"       = API ADGROUP
}


def _fetch_changelogs_sync(ad_account_id: str, start_date: str, end_date: str) -> list[dict]:
    """Synchronous Meta /activities API call — called via asyncio.to_thread().

    Uses AdAccount.get_activities() (the /activities edge) which is available in
    facebook-business SDK v22+.  The older /change_history edge was removed from
    the AdAccount object in SDK v22; this implementation replaces it.

    Object type normalization: Meta returns legacy internal names
    (CAMPAIGN_GROUP, CAMPAIGN, ADGROUP); we map these to the familiar
    display names (CAMPAIGN, AD_SET, AD) used throughout the dashboard.
    """
    import json as _json
    account = AdAccount(f"act_{ad_account_id.removeprefix('act_')}")
    entries: list[dict] = []
    for category in _ACTIVITY_CATEGORIES:
        params = {
            "since": start_date,
            "until": end_date,
            "category": category,
            "add_children": True,
            "limit": 200,
        }
        try:
            cursor = account.get_activities(fields=_ACTIVITY_FIELDS, params=params)
        except FacebookRequestError as exc:
            logger.warning("changelog_fetch_failed", category=category, error=str(exc))
            continue
        while True:
            for row in cursor:
                d = dict(row)
                changed_fields, old_val, new_val = _parse_extra_data(d.get("extra_data"))
                raw_type = str(d.get("object_type", "")).upper()
                norm_type = _OBJECT_TYPE_MAP.get(raw_type, raw_type)
                entries.append({
                    "change_time": _normalize_change_time(d),
                    "object_id": str(d.get("object_id", "")),
                    "object_name": str(d.get("object_name", "")),
                    "object_type": norm_type,
                    "event_type": str(d.get("event_type", "")),
                    "changed_fields": _json.dumps(changed_fields) if changed_fields else None,
                    "old_value": old_val,
                    "new_value": new_val,
                    "actor_name": str(d.get("actor_name", "")),
                })
            try:
                if cursor.load_next_page() is False:
                    break
            except Exception:  # noqa: BLE001
                break
    return entries


@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=2, max=60),
    retry=retry_if_exception_type(FacebookRequestError),
    before_sleep=before_sleep_log(_stdlib_log, logging.WARNING),
    reraise=True,
)
async def fetch_changelogs(ad_account_id: str, start_date: str, end_date: str) -> list[dict]:
    """Fetch Meta change history for campaigns/ad sets/ads. Async wrapper around sync SDK call."""
    logger.info("changelog_fetch_start", start=start_date, end=end_date)
    entries = await asyncio.to_thread(_fetch_changelogs_sync, ad_account_id, start_date, end_date)
    logger.info("changelog_fetch_complete", entries=len(entries))
    return entries


def _parse_ad_name_parts(name: str) -> tuple[str, str]:
    """Parse (ad_style, ad_format) from naming convention 'Nowa | CODE | style | format | version'.
    Returns ('unknown', 'unknown') if parsing fails."""
    parts = [p.strip() for p in name.split('|')]
    style = parts[2].lower().replace(' ', '_').replace('-', '_') if len(parts) > 2 else 'unknown'
    fmt_raw = parts[3].lower().replace(' ', '_') if len(parts) > 3 else 'unknown'
    if 'video' in fmt_raw:
        fmt = 'video'
    elif 'carousel' in fmt_raw:
        fmt = 'carousel'
    elif 'image' in fmt_raw:
        fmt = 'image'
    else:
        fmt = fmt_raw
    return style, fmt


def _fetch_ad_creatives_sync(ad_account_id: str) -> list[dict]:
    """Fetch all active/paused ads with creative metadata (thumbnail, URL, format, style).

    Called via asyncio.to_thread() from async context.
    Parses style and format from the ad naming convention.
    """
    account = AdAccount(f"act_{ad_account_id.removeprefix('act_')}")
    fields = [
        'id', 'name', 'status', 'effective_status', 'adset_id', 'campaign_id',
        'creative{id,object_type,thumbnail_url,effective_object_story_id,object_story_spec{link_data{link},video_data{call_to_action{value{link}}}}}',
    ]
    params = {'effective_status': ['ACTIVE', 'PAUSED'], 'limit': 200}
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        ads = account.get_ads(fields=fields, params=params)
    results = []
    for ad in ads:
        d = dict(ad)
        cr = d.get('creative') or {}
        spec = cr.get('object_story_spec') or {}
        link_data = spec.get('link_data') or {}
        video_data = spec.get('video_data') or {}
        dest_url = (
            link_data.get('link')
            or (video_data.get('call_to_action') or {}).get('value', {}).get('link')
            or ''
        )
        # Use Ads Manager deep-link — always accessible to account admins without
        # extra login steps, unlike post permalinks which require public visibility.
        ad_id_str = str(d.get('id', ''))
        act_id = ad_account_id.removeprefix('act_')
        preview_url = (
            f'https://www.facebook.com/adsmanager/manage/ads'
            f'?act={act_id}&selected_ad_ids={ad_id_str}'
        ) if ad_id_str else ''

        # Determine format from object_type as fallback
        obj_type = cr.get('object_type', '')
        name = d.get('name') or ''
        style, fmt = _parse_ad_name_parts(name)
        if obj_type == 'VIDEO' and fmt == 'unknown':
            fmt = 'video'

        results.append({
            'ad_id': str(d.get('id', '')),
            'ad_name': name,
            'adset_id': str(d.get('adset_id') or ''),
            'campaign_id': str(d.get('campaign_id') or ''),
            'effective_status': str(d.get('effective_status') or ''),
            'ad_format': fmt,
            'ad_style': style,
            'thumbnail_url': cr.get('thumbnail_url') or '',
            'destination_url': dest_url,
            'preview_url': preview_url,
        })
    logger.info('ad_creatives_fetched', count=len(results))
    return results


@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=2, max=60),
    retry=retry_if_exception_type(FacebookRequestError),
    before_sleep=before_sleep_log(_stdlib_log, logging.WARNING),
    reraise=True,
)
async def fetch_ad_creatives(ad_account_id: str) -> list[dict]:
    """Fetch all active/paused ads with creative metadata. Async wrapper."""
    logger.info('ad_creatives_fetch_start')
    return await asyncio.to_thread(_fetch_ad_creatives_sync, ad_account_id)


# ---------------------------------------------------------------------------
# Phase C: Pixel health — per-event browser/server counts + best-effort EMQ.
#
# RESEARCH FINDING (documented here for the PR, per Phase C spec):
#
#   1. Event counts (browser_count / server_count): the standard
#      /{pixel_id}/stats Graph API endpoint (AdsPixel.get_stats in the
#      facebook-business SDK) supports aggregation="event" (breaks results
#      down per standard/custom event name) plus an event_source filter of
#      "WEB_ONLY" or "SERVER_ONLY" — exactly the browser-vs-server split this
#      table needs. This data IS retrievable with the same system-user
#      token already used for Insights (ads_read). Caveat: Meta only retains
#      pixel /stats data for ~7 days from request time, so (like the Meta
#      Insights D-1 pull elsewhere in this codebase) this only ever fetches
#      recent days — there is no historical pixel-stats backfill possible.
#
#   2. Event Match Quality (EMQ): confirmed NOT present as a field on the
#      AdsPixel object in the facebook-business SDK, and NOT returned by the
#      /{pixel_id}/stats endpoint above. EMQ is exposed by a separate Graph
#      API node, GET /{version}/dataset_quality?dataset_id={pixel_id}, which
#      is documented as requiring Advanced Access to the Marketing API (an
#      app-review-gated feature tier) beyond the basic ads_read /
#      business_management scopes this project's token already holds for
#      Insights. We cannot assume that tier is granted for every ad account
#      this dashboard will ever run against, so EMQ is fetched best-effort
#      (fetch_pixel_emq below): on ANY error (permission, 404, network,
#      unexpected shape) it degrades to {} and emq_score/dedup_rate stay NULL
#      in pixel_health — exactly the "build the column anyway" fallback
#      called for in the Phase C spec, to be filled later by a manual /
#      Playwright-based process if this account never gets Advanced Access.
# ---------------------------------------------------------------------------

_PIXEL_STATS_API_VERSION = "v24.0"


def _parse_pixel_stats_rows(rows: list[dict] | None) -> dict[str, int]:
    """Map event name -> summed count from a /{pixel_id}/stats aggregation=event response.

    Row shape (Graph API docs): {"start_time": ..., "end_time": ..., "event": "Purchase",
    "count": "12", ...}. Defensive: tolerates 'count' as str/int/missing and either
    'event' or 'event_name' as the key; unparseable rows are skipped rather than raising
    (CLAUDE.md graceful degradation — a single malformed row must not lose the whole day).
    """
    counts: dict[str, int] = {}
    for row in rows or []:
        event_name = row.get("event") or row.get("event_name")
        if not event_name:
            continue
        try:
            count = int(float(row.get("count", 0) or 0))
        except (TypeError, ValueError):
            count = 0
        counts[event_name] = counts.get(event_name, 0) + count
    return counts


def _fetch_pixel_event_counts_sync(pixel_id: str, date_iso: str, event_source: str) -> list[dict]:
    """Synchronous /{pixel_id}/stats call for one event_source (WEB_ONLY or SERVER_ONLY).

    Called via asyncio.to_thread() from async context (same pattern as
    _fetch_insights_sync — the SDK's HTTP layer is synchronous 'requests').
    """
    from facebook_business.adobjects.adspixel import AdsPixel

    pixel = AdsPixel(pixel_id)
    params = {
        "aggregation": "event",
        "event_source": event_source,
        "start_time": date_iso,
        "end_time": date_iso,
    }
    cursor = pixel.get_stats(fields=[], params=params)
    return [dict(r) for r in cursor]


async def fetch_pixel_event_counts(pixel_id: str, date_iso: str) -> dict[str, dict[str, int]]:
    """Fetch per-event browser vs server counts for one day.

    Returns {event_name: {"browser_count": int, "server_count": int}}.
    Each event_source call is independent and never raises FacebookRequestError to the
    caller: a failure on one side (e.g. SERVER_ONLY rejected for this pixel) still
    returns whatever the other side produced, rather than losing the whole day
    (CLAUDE.md graceful degradation — mirrors the video_thruplay retry pattern above).
    """
    result: dict[str, dict[str, int]] = {}

    for event_source, count_key in (("WEB_ONLY", "browser_count"), ("SERVER_ONLY", "server_count")):
        try:
            rows = await asyncio.to_thread(
                _fetch_pixel_event_counts_sync, pixel_id, date_iso, event_source
            )
            counts = _parse_pixel_stats_rows(rows)
        except FacebookRequestError as exc:
            logger.warning(
                "pixel_stats_fetch_failed",
                event_source=event_source,
                date=date_iso,
                error=str(exc),
            )
            counts = {}
        for event_name, count in counts.items():
            result.setdefault(event_name, {"browser_count": 0, "server_count": 0})
            result[event_name][count_key] = count

    return result


def _fetch_dataset_quality_sync(pixel_id: str, access_token: str) -> dict:
    """Best-effort raw call to the Dataset Quality API (/dataset_quality node).

    Not exposed via a dedicated facebook-business SDK object (it is a top-level
    node, not an AdsPixel edge), so this uses a plain HTTP GET. See the module-level
    RESEARCH FINDING comment above for why this is expected to fail for accounts
    without Advanced Access, and why that's handled as a normal, logged outcome
    rather than an error.
    """
    import requests

    url = f"https://graph.facebook.com/{_PIXEL_STATS_API_VERSION}/dataset_quality"
    resp = requests.get(
        url,
        params={"dataset_id": pixel_id, "access_token": access_token},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


async def fetch_pixel_emq(pixel_id: str, access_token: str) -> dict[str, dict[str, float | None]]:
    """Best-effort per-event EMQ + dedup rate. Returns {} on ANY failure.

    Deliberately broad except clause: permission errors, 404s (feature not enabled
    for this account), network failures, and unexpected response shapes must all
    degrade the same way — emq_score/dedup_rate NULL in pixel_health, never a
    crashed ingest (CLAUDE.md graceful degradation). See RESEARCH FINDING above.
    """
    try:
        data = await asyncio.to_thread(_fetch_dataset_quality_sync, pixel_id, access_token)
    except Exception as exc:  # noqa: BLE001 — intentionally broad, see docstring
        logger.info("pixel_emq_not_available", error=str(exc))
        return {}

    result: dict[str, dict[str, float | None]] = {}
    for entry in (data.get("web") or []):
        event_name = entry.get("event_name") or entry.get("event")
        if not event_name:
            continue
        emq = entry.get("event_match_quality")
        dedup = entry.get("deduplication_rate", entry.get("dedup_rate"))
        try:
            emq_val = float(emq) if emq is not None else None
        except (TypeError, ValueError):
            emq_val = None
        try:
            dedup_val = float(dedup) if dedup is not None else None
        except (TypeError, ValueError):
            dedup_val = None
        result[event_name] = {"emq_score": emq_val, "dedup_rate": dedup_val}
    return result
