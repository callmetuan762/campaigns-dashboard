"""Shopify Admin REST API client: order fetch + landing_site UTM/lp_slug parsing.

SHOP-01: Authenticates via a custom-app Admin API access token
         (X-Shopify-Access-Token header — SHOPIFY_ADMIN_TOKEN env var).
SHOP-02: Fetches orders (status=any) via the REST /orders.json endpoint. v1 attribution
         is landing_site query-string parsing only — no GraphQL customer_journey lookup
         yet (that would give true multi-touch attribution; tracked as a future upgrade).
SHOP-03: All API calls wrapped in tenacity retry with exponential backoff, mirroring
         src/meta/client.py and src/ga4/client.py.

CLAUDE.md: Meta <-> GA4 join key is exact UTM campaign name match only — the same rule
applies here: utm_campaign parsed from landing_site must exact-match campaigns.name /
ga4_metrics.campaign_utm for cross-source joins. No fuzzy matching.
"""
from __future__ import annotations

import asyncio
import logging
from urllib.parse import parse_qs, urlparse

import requests
import structlog
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = structlog.get_logger(__name__)
_stdlib_log = logging.getLogger(__name__)

# Default Shopify Admin API version. Override via Settings.shopify_api_version if the
# store requires a different (still-supported) release.
_DEFAULT_API_VERSION = "2025-01"

# Order fields requested from the REST API (SHOP-02). Kept minimal — this is a v1
# landing_site-only attribution pass, not a full order export.
_ORDER_FIELDS = [
    "id",
    "created_at",
    "total_price",
    "financial_status",
    "landing_site",
    "referring_site",
]


def _parse_landing_site(landing_site: str | None) -> dict:
    """Extract utm_source / utm_campaign / utm_content / lp_slug from a landing_site URL.

    landing_site is a path + query string as Shopify recorded it on first visit, e.g.
    "/pages/preorder?utm_source=meta&utm_campaign=nowa_launch&lp_slug=routine".
    Missing params default to '' (never None) — matches the shopify_orders schema's
    NOT NULL DEFAULT '' columns so joins/group-bys never have to special-case NULL.
    """
    if not landing_site:
        return {"utm_source": "", "utm_campaign": "", "utm_content": "", "lp_slug": ""}

    parsed = urlparse(landing_site)
    qs = parse_qs(parsed.query)

    def _first(key: str) -> str:
        values = qs.get(key)
        return values[0] if values else ""

    return {
        "utm_source": _first("utm_source"),
        "utm_campaign": _first("utm_campaign"),
        "utm_content": _first("utm_content"),
        "lp_slug": _first("lp_slug"),
    }


def _parse_order(raw: dict) -> dict:
    """Normalize a raw Shopify order dict into the shopify_orders schema shape."""
    created_at = str(raw.get("created_at") or "")
    order_date = created_at[:10] if len(created_at) >= 10 else ""
    utm = _parse_landing_site(raw.get("landing_site"))

    return {
        "order_id": str(raw.get("id", "")),
        "created_at": created_at,
        "order_date": order_date,
        "total_price": float(raw.get("total_price") or 0.0),
        "financial_status": raw.get("financial_status") or "",
        "utm_source": utm["utm_source"],
        "utm_campaign": utm["utm_campaign"],
        "utm_content": utm["utm_content"],
        "lp_slug": utm["lp_slug"],
        "landing_site": raw.get("landing_site") or "",
        "referring_site": raw.get("referring_site") or "",
    }


def _next_page_url(link_header: str | None) -> str | None:
    """Parse the RFC 5988 Link header Shopify uses for cursor-based pagination.

    Returns the rel="next" URL, or None on the last page / missing header.
    """
    if not link_header:
        return None
    for part in link_header.split(","):
        if 'rel="next"' in part:
            return part.split(";")[0].strip().strip("<>")
    return None


def _fetch_orders_sync(
    store_domain: str,
    admin_token: str,
    since_iso: str,
    until_iso: str,
    api_version: str = _DEFAULT_API_VERSION,
) -> list[dict]:
    """Synchronous Shopify Admin REST call — called via asyncio.to_thread().

    Paginates via the Link header cursor (Shopify's REST API does not support
    page/offset pagination for orders.json beyond the first request).
    """
    base_url = f"https://{store_domain}/admin/api/{api_version}/orders.json"
    headers = {"X-Shopify-Access-Token": admin_token, "Content-Type": "application/json"}
    params: dict | None = {
        "status": "any",
        "created_at_min": f"{since_iso}T00:00:00Z",
        "created_at_max": f"{until_iso}T23:59:59Z",
        "fields": ",".join(_ORDER_FIELDS),
        "limit": 250,
    }

    orders: list[dict] = []
    url: str | None = base_url
    while url:
        resp = requests.get(url, headers=headers, params=params, timeout=30)
        resp.raise_for_status()
        payload = resp.json()
        for raw in payload.get("orders", []):
            orders.append(_parse_order(raw))

        url = _next_page_url(resp.headers.get("Link"))
        params = None  # the next_url returned by Shopify already encodes all query params

    return orders


@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=2, max=60),
    retry=retry_if_exception_type(requests.exceptions.RequestException),
    before_sleep=before_sleep_log(_stdlib_log, logging.WARNING),
    reraise=True,
)
async def fetch_orders(
    store_domain: str,
    admin_token: str,
    since_iso: str,
    until_iso: str,
    api_version: str = _DEFAULT_API_VERSION,
) -> list[dict]:
    """Async wrapper: fetch Shopify orders for a date range. SHOP-02.

    RESEARCH pattern (matches src/meta/client.py, src/ga4/client.py): the underlying
    HTTP call is synchronous (requests), so it runs via asyncio.to_thread() to avoid
    blocking the aiogram event loop.
    """
    logger.info("shopify_fetch_start", since=since_iso, until=until_iso)
    rows = await asyncio.to_thread(
        _fetch_orders_sync, store_domain, admin_token, since_iso, until_iso, api_version
    )
    logger.info("shopify_fetch_complete", since=since_iso, until=until_iso, rows=len(rows))
    return rows
