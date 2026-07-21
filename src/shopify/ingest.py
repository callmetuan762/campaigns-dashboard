"""Shopify orders ingest job: APScheduler-compatible zero-arg async function.

SHOP-01: Credential guard — if SHOPIFY_STORE_DOMAIN or SHOPIFY_ADMIN_TOKEN is unset,
         ingest is a clean no-op that logs "skipped", exactly like GA4/Meta degrade
         when their credentials are missing (src/ga4/ingest.py, src/meta/ingest.py).
SHOP-02: Writes to shopify_orders via UPSERT (idempotent).
SHOP-03: D-13 pattern: ingestion_log source='shopify'.

CRITICAL (mirrors src/meta/ingest.py, src/ga4/ingest.py): shopify_ingest_job takes NO
args. Resources are stored in module globals via register_job_resources() called from
main.py before scheduler.start() — APScheduler's SQLAlchemyJobStore uses pickle, and
passing Bot/DBClient as scheduler args raises PicklingError.
"""
from __future__ import annotations

from datetime import date, timedelta

import sentry_sdk
import structlog

logger = structlog.get_logger(__name__)

# Module-level globals — never passed as APScheduler job args (PicklingError)
_bot = None
_db = None
_settings = None


def register_job_resources(bot, db, settings) -> None:
    """Store bot, db, settings in module globals before scheduler.start()."""
    global _bot, _db, _settings
    _bot = bot
    _db = db
    _settings = settings
    logger.info("ingest_resources_registered")


def _get_yesterday_iso() -> str:
    """Default backfill window: yesterday only (orders finalize same-day)."""
    return (date.today() - timedelta(days=1)).isoformat()


async def _run_shopify_ingest(
    bot,
    db,
    settings,
    since_override: str | None = None,
    until_override: str | None = None,
) -> None:
    """Core Shopify ingest logic — called by shopify_ingest_job()."""
    since_iso = since_override if since_override is not None else _get_yesterday_iso()
    until_iso = until_override if until_override is not None else since_iso
    log_id: int | None = None

    # SHOP-01: Credential guard — skip cleanly (no ingestion_log entry) when unset,
    # mirroring GA4's "ga4_ingest_skipped_no_credentials" pattern exactly. This must
    # stay BEFORE log_ingestion_start so an unconfigured Shopify integration never
    # shows up as a "failed" source in the dashboard.
    store_domain = getattr(settings, "shopify_store_domain", None)
    admin_token = getattr(settings, "shopify_admin_token", None)
    if not store_domain or not admin_token:
        logger.warning("shopify_ingest_skipped_no_credentials", since=since_iso, until=until_iso)
        return

    try:
        log_id = await db.log_ingestion_start("shopify")
        logger.info("ingest_start", source="shopify", since=since_iso, until=until_iso)

        from src.shopify.client import fetch_orders

        token_value = (
            admin_token.get_secret_value() if hasattr(admin_token, "get_secret_value") else admin_token
        )
        api_version = getattr(settings, "shopify_api_version", None) or "2025-01"

        orders = await fetch_orders(store_domain, token_value, since_iso, until_iso, api_version)
        upserted = await db.upsert_shopify_orders(orders)

        await db.log_ingestion_finish(log_id, "success", rows_upserted=upserted)
        logger.info("ingest_complete", source="shopify", since=since_iso, until=until_iso, rows=upserted)

    except Exception as exc:  # noqa: BLE001
        sentry_sdk.capture_exception(exc)
        logger.error("ingest_failed", source="shopify", since=since_iso, until=until_iso, error=str(exc))
        if log_id is not None:
            await db.log_ingestion_finish(log_id, "failed", error=str(exc))


async def run_shopify_ingest_for_range(db, settings, since_iso: str, until_iso: str) -> None:
    """Public entry point for backfill. Skips bot (no alerts on the orders funnel yet)."""
    await _run_shopify_ingest(
        bot=None, db=db, settings=settings,
        since_override=since_iso, until_override=until_iso,
    )


async def shopify_ingest_job() -> None:
    """APScheduler job entry point — zero args, resources from module globals.

    CRITICAL: This function takes NO arguments. Passing Bot/DBClient as APScheduler
    job args causes PicklingError with SQLAlchemyJobStore (RESEARCH Pitfall 2).
    """
    if _db is None or _settings is None:
        logger.error("ingest_job_resources_not_registered")
        return
    await _run_shopify_ingest(_bot, _db, _settings)
