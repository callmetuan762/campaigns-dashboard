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
    "purchase_roas",          # list[{action_type, value}] — parse via _extract_action_value
    "actions",                # list[{action_type, value}] — filter offsite_conversion.fb_pixel_purchase
    "cost_per_action_type",   # list[{action_type, value}] — filter offsite_conversion.fb_pixel_purchase
    "conversions",            # list[{action_type, value}] — includes custom pixel events like form_submit_deposit
]


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
    cursor = account.get_insights(fields=fields, params=params)
    rows = []
    while True:
        rows.extend([_parse_insight_row(dict(r), date_iso, level) for r in cursor])
        if cursor.load_next_page() is False:
            break
    return rows


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
        story_id = cr.get('effective_object_story_id') or ''
        preview_url = f'https://www.facebook.com/{story_id}' if story_id else ''

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
