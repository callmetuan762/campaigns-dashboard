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


# Live Meta preorder ads (see nowa-meta-ads-automation ad-set/ad naming, e.g.
# "Nowa | HOME-01 | broad | single_image | 20260715") set utm_content to the bare
# angle-ID prefix ("HOME-01", "ROUTINE-03", ...) with no "__<creative-id>" suffix —
# confirmed live via the Marketing API 2026-08-06, not the "<slug>__<creative-id>"
# convention this parser originally assumed. Map the prefix to the canonical
# PREORDER_LP_SLUGS value so real order traffic doesn't fall into "(other)".
_ANGLE_PREFIX_TO_LP_SLUG = {
    "HOME": "home",
    "ROUTINE": "routine",
    "SCREEN": "screen-anxious",
    "FEELINGS": "big-feelings",
}


def _lp_slug_from_utm_content(utm_content: str) -> str:
    """Derive lp_slug from utm_content, supporting both the intended "<slug>__<creative-id>"
    convention and the bare angle-ID convention ("HOME-01") actually in use on live ads."""
    if "__" in utm_content:
        return utm_content.split("__", 1)[0]
    prefix = utm_content.split("-", 1)[0].upper()
    return _ANGLE_PREFIX_TO_LP_SLUG.get(prefix, utm_content)


def _parse_landing_site(landing_site: str | None) -> dict:
    """Extract utm_source / utm_campaign / utm_content / lp_slug from a landing_site URL.

    landing_site is a path + query string as Shopify recorded it on first visit, e.g.
    "/cart/473820...:1?utm_source=facebook&utm_campaign=nowa_preorder_2026&utm_content=routine__c1".
    Missing params default to '' (never None) — matches the shopify_orders schema's
    NOT NULL DEFAULT '' columns so joins/group-bys never have to special-case NULL.

    lp_slug: the LP tracking helper forwards only utm_* + fbclid/gclid to the shop
    domain — there is no lp_slug query param on shop URLs. The segment is derived from
    utm_content via _lp_slug_from_utm_content (see its docstring for the two supported
    conventions). An explicit lp_slug param, if one ever appears, still wins.
    """
    if not landing_site:
        return {"utm_source": "", "utm_campaign": "", "utm_content": "", "lp_slug": ""}

    parsed = urlparse(landing_site)
    qs = parse_qs(parsed.query)

    def _first(key: str) -> str:
        values = qs.get(key)
        return values[0] if values else ""

    utm_content = _first("utm_content")
    lp_slug = _first("lp_slug") or (_lp_slug_from_utm_content(utm_content) if utm_content else "")

    return {
        "utm_source": _first("utm_source"),
        "utm_campaign": _first("utm_campaign"),
        "utm_content": utm_content,
        "lp_slug": lp_slug,
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


# ---------------------------------------------------------------------------
# Abandoned checkouts — the Shopify leg of the Initiate Checkout reconciliation
# ---------------------------------------------------------------------------

# /checkouts.json does not accept a `fields` filter the way /orders.json does,
# so the full payload comes back and _parse_checkout picks what it needs.
def _parse_checkout(raw: dict) -> dict:
    """Normalise one /checkouts.json row into a shopify_checkouts row."""
    created_at = str(raw.get("created_at") or "")
    utm = _parse_landing_site(raw.get("landing_site"))
    total = raw.get("total_price")
    try:
        total_price = float(total) if total not in (None, "") else None
    except (TypeError, ValueError):
        total_price = None
    return {
        "checkout_id": str(raw.get("id") or raw.get("token") or ""),
        "created_at": created_at,
        # Date bucket comes from the ISO timestamp's own date part, matching how
        # _parse_order derives order_date — no timezone shifting anywhere here.
        "checkout_date": created_at[:10],
        "completed_at": raw.get("completed_at") or None,
        "email": str(raw.get("email") or ""),
        "total_price": total_price,
        "cart_token": str(raw.get("cart_token") or ""),
        "landing_site": raw.get("landing_site") or "",
        "lp_slug": utm["lp_slug"],
    }


def _fetch_checkouts_sync(
    store_domain: str,
    admin_token: str,
    since_iso: str,
    until_iso: str,
    api_version: str = _DEFAULT_API_VERSION,
) -> list[dict]:
    """Synchronous /checkouts.json call — same Link-header pagination as orders."""
    base_url = f"https://{store_domain}/admin/api/{api_version}/checkouts.json"
    headers = {"X-Shopify-Access-Token": admin_token, "Content-Type": "application/json"}
    params: dict | None = {
        "created_at_min": f"{since_iso}T00:00:00Z",
        "created_at_max": f"{until_iso}T23:59:59Z",
        "limit": 250,
    }

    checkouts: list[dict] = []
    url: str | None = base_url
    while url:
        resp = requests.get(url, headers=headers, params=params, timeout=30)
        resp.raise_for_status()
        for raw in resp.json().get("checkouts", []):
            row = _parse_checkout(raw)
            if row["checkout_id"]:
                checkouts.append(row)
        url = _next_page_url(resp.headers.get("Link"))
        params = None

    return checkouts


@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=2, max=60),
    retry=retry_if_exception_type(requests.exceptions.RequestException),
    before_sleep=before_sleep_log(_stdlib_log, logging.WARNING),
    reraise=True,
)
async def fetch_checkouts(
    store_domain: str,
    admin_token: str,
    since_iso: str,
    until_iso: str,
    api_version: str = _DEFAULT_API_VERSION,
) -> list[dict]:
    """Fetch abandoned checkouts for a date range.

    Shopify only exposes abandoned checkouts here, and only once the shopper has
    left contact details — see the migration 017 comment for why that makes the
    Shopify checkout figure a floor rather than a like-for-like counterpart to
    Meta's pixel event or GA4's begin_checkout.
    """
    logger.info("shopify_checkouts_fetch_start", since=since_iso, until=until_iso)
    rows = await asyncio.to_thread(
        _fetch_checkouts_sync, store_domain, admin_token, since_iso, until_iso, api_version
    )
    logger.info(
        "shopify_checkouts_fetch_complete", since=since_iso, until=until_iso, rows=len(rows)
    )
    return rows


# ---------------------------------------------------------------------------
# Order customer journey (migration 019) — GraphQL, not REST
# ---------------------------------------------------------------------------
# REST /orders.json exposes a single landing_site with no notion of separate
# visits. customerJourneySummary is GraphQL-only and is what makes a two-touch
# path ("segment LP introduced, homepage closed") visible.
_ORDER_JOURNEY_QUERY = """
query($cursor: String) {
  orders(first: 100, after: $cursor, reverse: true) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id
      name
      createdAt
      displayFinancialStatus
      totalPriceSet { shopMoney { amount } }
      customerJourneySummary {
        momentsCount { count }
        firstVisit { landingPage source sourceType
                     utmParameters { source medium campaign content term } }
        lastVisit  { landingPage source sourceType
                     utmParameters { source medium campaign content term } }
      }
    }
  }
}
"""


def _visit_fields(visit: dict | None, prefix: str) -> dict:
    """Flatten one firstVisit/lastVisit node into prefixed columns."""
    v = visit or {}
    utm = v.get("utmParameters") or {}
    return {
        f"{prefix}_landing_page": v.get("landingPage") or "",
        f"{prefix}_source": v.get("source") or "",
        f"{prefix}_source_type": v.get("sourceType") or "",
        f"{prefix}_utm_source": utm.get("source") or "",
        f"{prefix}_utm_medium": utm.get("medium") or "",
        f"{prefix}_utm_campaign": utm.get("campaign") or "",
        f"{prefix}_utm_content": utm.get("content") or "",
        f"{prefix}_utm_term": utm.get("term") or "",
    }


def _parse_order_journey(node: dict) -> dict:
    """Normalise one GraphQL order node into a shopify_order_journey row."""
    created = str(node.get("createdAt") or "")
    money = ((node.get("totalPriceSet") or {}).get("shopMoney") or {}).get("amount")
    try:
        total = float(money) if money not in (None, "") else None
    except (TypeError, ValueError):
        total = None
    cj = node.get("customerJourneySummary") or {}
    # Shopify returns a gid:// URI; keep only the trailing numeric id so this
    # joins to shopify_orders.order_id, which REST reports as a bare number.
    gid = str(node.get("id") or "")
    return {
        "order_id": gid.rsplit("/", 1)[-1] if gid else "",
        "order_name": node.get("name") or "",
        "created_at": created,
        "order_date": created[:10],
        "financial_status": (node.get("displayFinancialStatus") or "").lower(),
        "total_price": total,
        "moments_count": (cj.get("momentsCount") or {}).get("count"),
        **_visit_fields(cj.get("firstVisit"), "first"),
        **_visit_fields(cj.get("lastVisit"), "last"),
    }


def _fetch_order_journeys_sync(
    store_domain: str,
    admin_token: str,
    api_version: str = _DEFAULT_API_VERSION,
    max_orders: int = 500,
) -> list[dict]:
    """Page the GraphQL orders connection newest-first, collecting journeys.

    Not date-filtered: the store's whole order history is small (tens of orders),
    and a journey can gain a moment after the order date, so re-reading all of
    them is both cheap and more correct than a windowed pull. max_orders is a
    guard for when that stops being true.
    """
    url = f"https://{store_domain}/admin/api/{api_version}/graphql.json"
    headers = {"X-Shopify-Access-Token": admin_token, "Content-Type": "application/json"}

    rows: list[dict] = []
    cursor: str | None = None
    while True:
        resp = requests.post(
            url, headers=headers,
            json={"query": _ORDER_JOURNEY_QUERY, "variables": {"cursor": cursor}},
            timeout=30,
        )
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("errors"):
            # A GraphQL 200-with-errors is still a failure; surface it rather than
            # returning a silently short list.
            raise requests.exceptions.RequestException(str(payload["errors"])[:500])
        conn = payload["data"]["orders"]
        for node in conn.get("nodes", []):
            row = _parse_order_journey(node)
            if row["order_id"]:
                rows.append(row)
        info = conn.get("pageInfo") or {}
        if not info.get("hasNextPage") or len(rows) >= max_orders:
            break
        cursor = info.get("endCursor")

    return rows


@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=2, max=60),
    retry=retry_if_exception_type(requests.exceptions.RequestException),
    before_sleep=before_sleep_log(_stdlib_log, logging.WARNING),
    reraise=True,
)
async def fetch_order_journeys(
    store_domain: str,
    admin_token: str,
    api_version: str = _DEFAULT_API_VERSION,
) -> list[dict]:
    """Fetch first/last-touch journeys for every order."""
    logger.info("shopify_journey_fetch_start")
    rows = await asyncio.to_thread(
        _fetch_order_journeys_sync, store_domain, admin_token, api_version
    )
    logger.info("shopify_journey_fetch_complete", rows=len(rows))
    return rows
