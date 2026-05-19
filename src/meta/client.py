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
    }


def _fetch_insights_sync(ad_account_id: str, date_iso: str, level: str) -> list[dict]:
    """Synchronous Meta API call — called via asyncio.to_thread() from async context.

    RESEARCH Pitfall 1: facebook-business SDK uses synchronous 'requests' library;
    calling this directly in an async def blocks the aiogram event loop.
    """
    account = AdAccount(f"act_{ad_account_id}")
    params = {
        "level": level,
        "time_range": {"since": date_iso, "until": date_iso},
        "limit": 500,
    }
    cursor = account.get_insights(fields=_CAMPAIGN_FIELDS, params=params)
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
