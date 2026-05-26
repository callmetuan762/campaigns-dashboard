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
