"""Daily catch-up backfill job for APScheduler.

Runs at 03:00 every day (configurable in main.py).
Fetches:
  - Meta Ads: yesterday (D-1) — data finalises ~1-2 AM
  - GA4: D-2 — avoids incomplete-day quota issues (CLAUDE.md rule)

Uses the module-globals pattern so APScheduler's SQLAlchemyJobStore
can pickle the zero-arg job function without PicklingError.
"""
from __future__ import annotations

import structlog

logger = structlog.get_logger(__name__)

# Module-level globals — set via register_job_resources() before scheduler.start()
_db = None
_settings = None


def register_job_resources(db, settings) -> None:
    global _db, _settings
    _db = db
    _settings = settings


def _filter_by_brand_prefix(rows: list[dict], prefix: str) -> list[dict]:
    """Drop rows whose campaign_name doesn't start with the configured brand prefix.

    No-op when prefix is empty. Duplicated from src.meta.ingest's private helper
    (same pattern the dashboard pages already use for small shared constants) —
    needed here for the ad-creative/ad-insight fetches, which run outside
    src.meta.ingest._run_meta_ingest and so aren't covered by its filtering.
    """
    if not prefix:
        return rows
    return [r for r in rows if r.get("campaign_name", "").startswith(prefix)]


async def daily_backfill_job() -> None:
    """Zero-arg APScheduler entry point. Fetches Meta D-1 and GA4 D-2."""
    from datetime import date, timedelta

    from src.ga4.ingest import run_ga4_ingest_for_date
    from src.meta.ingest import run_meta_ingest_for_date

    if _db is None or _settings is None:
        logger.error("daily_backfill_not_initialised")
        return

    yesterday = (date.today() - timedelta(days=1)).isoformat()
    d2 = (date.today() - timedelta(days=2)).isoformat()

    logger.info("daily_backfill_start", meta_date=yesterday, ga4_date=d2)

    try:
        await run_meta_ingest_for_date(_db, _settings, yesterday)
        logger.info("daily_backfill_meta_done", date=yesterday)
    except Exception as exc:  # noqa: BLE001
        logger.error("daily_backfill_meta_failed", date=yesterday, error=str(exc))

    try:
        await run_ga4_ingest_for_date(_db, _settings, d2)
        logger.info("daily_backfill_ga4_done", date=d2)
    except Exception as exc:  # noqa: BLE001
        logger.error("daily_backfill_ga4_failed", date=d2, error=str(exc))

    # GA4 page-to-page session flow (migration 018). Same D-2 date as the GA4
    # ingest above, for the same incomplete-day reason. Paths are normalised to
    # the dashboard's lp_slug here rather than in the GA4 client, keeping that
    # mapping in one place (dashboard.db.lp_slug_from_url).
    try:
        from src.dashboard.db import lp_slug_from_url
        from src.ga4.client import _build_ga4_client, fetch_page_flow

        ga4_client = _build_ga4_client(_settings.ga4_service_account_json)
        flow_rows = await fetch_page_flow(
            ga4_client, _settings.ga4_property_id, d2, d2
        )
        merged: dict[tuple[str, str, str], int] = {}
        for r in flow_rows:
            f = lp_slug_from_url(r["from_path"])
            t = lp_slug_from_url(r["to_path"])
            if not f or not t:
                continue
            key = (r["date"], f, t)
            merged[key] = merged.get(key, 0) + int(r["sessions"] or 0)
        if merged:
            await _db.upsert_ga4_page_flow([
                {"date": d, "from_slug": f, "to_slug": t, "sessions": n}
                for (d, f, t), n in merged.items()
            ])
        logger.info("daily_backfill_page_flow_done", date=d2, rows=len(merged))
    except Exception as exc:  # noqa: BLE001
        logger.error("daily_backfill_page_flow_failed", date=d2, error=str(exc))

    # Fetch changelogs for the last 7 days (catches any API delivery lag)
    from datetime import timedelta
    seven_days_ago = (date.today() - timedelta(days=7)).isoformat()
    try:
        from src.meta.client import fetch_changelogs, init_meta_api
        init_meta_api(_settings)
        entries = await fetch_changelogs(_settings.meta_ad_account_id, seven_days_ago, yesterday)
        if entries:
            await _db.upsert_changelog_entries(entries)
            logger.info("daily_backfill_changelogs_done", entries=len(entries))
    except Exception as exc:  # noqa: BLE001
        logger.error("daily_backfill_changelogs_failed", error=str(exc))

    # Fetch ad creative metadata (style, format, thumbnail, URLs) — weekly refresh
    try:
        from src.meta.client import fetch_ad_creatives
        ad_creative_rows = await fetch_ad_creatives(_settings.meta_ad_account_id)
        ad_creative_rows = _filter_by_brand_prefix(ad_creative_rows, _settings.meta_campaign_name_prefix)
        if ad_creative_rows:
            await _db.upsert_ad_creatives(ad_creative_rows)
            logger.info("daily_backfill_ad_creatives_done", rows=len(ad_creative_rows))
    except Exception as exc:  # noqa: BLE001
        logger.error("daily_backfill_ad_creatives_failed", error=str(exc))

    # Fetch ad-level insights (for top/fatigue analysis)
    try:
        from src.meta.client import fetch_ad_insights
        from src.meta.client import init_meta_api as _init_meta
        _init_meta(_settings)
        ad_rows = await fetch_ad_insights(_settings.meta_ad_account_id, yesterday)
        ad_rows = _filter_by_brand_prefix(ad_rows, _settings.meta_campaign_name_prefix)
        if ad_rows:
            await _db.upsert_ad_metrics(ad_rows)
            logger.info("daily_backfill_ad_insights_done", rows=len(ad_rows))
    except Exception as exc:  # noqa: BLE001
        logger.error("daily_backfill_ad_insights_failed", error=str(exc))

    # Pull Stripe payments sheet (if configured)
    if _settings.google_sheets_spreadsheet_id:
        try:
            import asyncio

            from src.sheets.client import fetch_stripe_payments, get_sheets_credentials

            creds = get_sheets_credentials(_settings)
            rows = await asyncio.to_thread(
                fetch_stripe_payments, _settings.google_sheets_spreadsheet_id, creds
            )
            if rows:
                await _db.upsert_stripe_payments(rows)
                logger.info("daily_backfill_stripe_done", rows=len(rows))
        except Exception as exc:  # noqa: BLE001
            logger.error("daily_backfill_stripe_failed", error=str(exc))

    # Pull the email-leads sheet (if configured). Unlike the date-scoped pulls
    # above this reads the whole sheet every run: its deduped tab is one row per
    # email with no timestamp column, so there is nothing to slice by date —
    # upsert_email_leads is keyed on email and idempotent, so a full re-read is
    # the correct shape here rather than a bug.
    if _settings.google_sheets_leads_spreadsheet_id:
        try:
            import asyncio

            from src.sheets.client import get_sheets_credentials
            from src.sheets.leads_client import fetch_email_leads

            patterns = [
                p.strip()
                for p in (_settings.leads_internal_email_patterns or "").split(",")
                if p.strip()
            ]
            creds = get_sheets_credentials(_settings)
            lead_rows = await asyncio.to_thread(
                fetch_email_leads,
                _settings.google_sheets_leads_spreadsheet_id,
                creds,
                patterns,
            )
            if lead_rows:
                await _db.upsert_email_leads(lead_rows)
                logger.info("daily_backfill_email_leads_done", rows=len(lead_rows))
        except Exception as exc:  # noqa: BLE001
            logger.error("daily_backfill_email_leads_failed", error=str(exc))

    # Pull Shopify preorder orders (funnel-v3, if configured). run_shopify_ingest_for_range
    # is itself a clean no-op when SHOPIFY_STORE_DOMAIN/SHOPIFY_ADMIN_TOKEN are unset
    # (src/shopify/ingest.py), matching the Stripe/Sheets graceful-degradation pattern —
    # the outer try/except here just guards against unexpected errors during that check.
    try:
        from src.shopify.ingest import run_shopify_ingest_for_range
        await run_shopify_ingest_for_range(_db, _settings, yesterday, yesterday)
        logger.info("daily_backfill_shopify_done", date=yesterday)
    except Exception as exc:  # noqa: BLE001
        logger.error("daily_backfill_shopify_failed", error=str(exc))

    # Abandoned checkouts — a 30-day window rather than yesterday alone, because a
    # checkout created days ago can complete later and only a re-read updates its
    # completed_at. Idempotent on checkout_id, so re-reading costs nothing but the
    # request.
    try:
        from src.shopify.ingest import run_shopify_checkout_ingest_for_range
        checkout_since = (date.today() - timedelta(days=30)).isoformat()
        n_checkouts = await run_shopify_checkout_ingest_for_range(
            _db, _settings, checkout_since, yesterday
        )
        logger.info("daily_backfill_shopify_checkouts_done", rows=n_checkouts)
    except Exception as exc:  # noqa: BLE001
        logger.error("daily_backfill_shopify_checkouts_failed", error=str(exc))

    # Per-order first/last touch journeys (migration 019). Whole history each run.
    try:
        from src.shopify.ingest import run_order_journey_ingest
        n_journeys = await run_order_journey_ingest(_db, _settings)
        logger.info("daily_backfill_order_journey_done", rows=n_journeys)
    except Exception as exc:  # noqa: BLE001
        logger.error("daily_backfill_order_journey_failed", error=str(exc))

    # Pull Meta Pixel health (per-event browser/server counts + best-effort EMQ,
    # Phase C). run_pixel_health_ingest_for_date is itself a clean no-op when
    # META_PIXEL_ID is unset, and never raises on stats-endpoint errors
    # (src/meta/pixel_ingest.py) — the outer try/except here just guards
    # against unexpected errors during that check, matching the Shopify pattern above.
    try:
        from src.meta.pixel_ingest import run_pixel_health_ingest_for_date
        await run_pixel_health_ingest_for_date(_db, _settings, yesterday)
        logger.info("daily_backfill_pixel_health_done", date=yesterday)
    except Exception as exc:  # noqa: BLE001
        logger.error("daily_backfill_pixel_health_failed", error=str(exc))

    logger.info("daily_backfill_complete", meta_date=yesterday, ga4_date=d2)


# ---------------------------------------------------------------------------
# Standalone entrypoint: python -m src.daily_backfill
# Wires up real DB + settings so the job actually runs without the bot.
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import asyncio as _asyncio

    async def _standalone() -> None:
        from dotenv import load_dotenv as _load
        _load()
        from src.config import load_settings as _cfg
        from src.db.client import DBClient as _DB
        _s = _cfg()
        _d = _DB(_s.db_path)
        await _d.connect()
        register_job_resources(_d, _s)
        await daily_backfill_job()

    _asyncio.run(_standalone())
