"""GA4 ingest job: APScheduler-compatible zero-arg async function.

GA4-01: Authenticates via service account JSON file (BetaAnalyticsDataClient).
GA4-02: Two RunReportRequest calls — campaign-level + landing-page-level.
GA4-03: D-2 freshness (_get_d2_iso): today - timedelta(days=2).
GA4-04: 6-hour cache check via ingestion_log. returnPropertyQuota=True (in client).
GA4-05: Writes to ga4_metrics (campaign) + ga4_landing_pages (landing pages) via UPSERT.
D-09: Circuit breaker after 3 consecutive failures — sends Telegram alert.
D-13: Uses ingestion_log source='ga4'.

CRITICAL: ga4_ingest_job takes NO args (APScheduler + SQLAlchemyJobStore PicklingError).
Resources stored in module globals via register_job_resources() called from main.py.
"""
from __future__ import annotations

import html
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import sentry_sdk
import structlog
from aiogram.enums import ParseMode

logger = structlog.get_logger(__name__)

# Module-level globals — never passed as APScheduler job args (PicklingError)
_bot = None
_db = None
_settings = None

_RECENT_FAILURES_SQL = """
    SELECT status FROM ingestion_log
    WHERE source = :source
    ORDER BY started_at DESC
    LIMIT :limit
"""


def register_job_resources(bot, db, settings) -> None:
    """Store bot, db, settings in module globals before scheduler.start()."""
    global _bot, _db, _settings
    _bot = bot
    _db = db
    _settings = settings
    logger.info("ingest_resources_registered")


def _get_d2_iso(timezone_str: str) -> str:
    """Compute D-2 date in the given timezone.

    D-10: GA4 defaults to D-2 (not D-1) to avoid incomplete-day quota issues (CLAUDE.md).
    """
    tz = ZoneInfo(timezone_str)
    d2 = datetime.now(tz).date() - timedelta(days=2)
    return d2.isoformat()


async def _check_circuit_breaker(db, source: str, threshold: int = 3) -> bool:
    """Return True if last `threshold` ingest runs all failed (circuit is open)."""
    rows = await db.fetch_all(
        _RECENT_FAILURES_SQL,
        {"source": source, "limit": threshold},
    )
    if len(rows) < threshold:
        return False
    return all(r.get("status") == "failed" for r in rows)


async def _run_ga4_ingest(
    bot,
    db,
    settings,
    date_override: str | None = None,
    skip_cache: bool = False,
) -> None:
    """Core GA4 ingest logic — called by ga4_ingest_job()."""
    date_iso = date_override if date_override is not None else _get_d2_iso(settings.report_timezone)
    log_id: int | None = None

    try:
        # Step 1: Credential guard — skip without logging if not configured
        # CRITICAL: This must remain BEFORE the cache check and UNCONDITIONAL
        if not settings.ga4_property_id or not settings.ga4_service_account_json:
            logger.warning("ga4_ingest_skipped_no_credentials", date=date_iso)
            return

        # Step 2: 6-hour cache check — bypassed when skip_cache=True (backfill mode)
        if not skip_cache:
            recent = await db.fetch_one(
                "SELECT id FROM ingestion_log WHERE source = 'ga4' AND status = 'success' "
                "AND started_at > datetime('now', '-6 hours')"
            )
            if recent:
                logger.info("ga4_ingest_skipped_cache_hit")
                return

        # Step 3: Start ingestion log
        log_id = await db.log_ingestion_start("ga4")
        logger.info("ingest_start", source="ga4", date=date_iso)

        # Step 4: Build GA4 client
        from src.ga4.client import _build_ga4_client, fetch_campaign_metrics, fetch_landing_page_metrics
        client = _build_ga4_client(settings.ga4_service_account_json)

        # Step 5: Fetch campaign-level metrics (D-08: pass configured conversion event)
        campaign_rows = await fetch_campaign_metrics(
            client,
            settings.ga4_property_id,
            date_iso,
            conversion_event=settings.ga4_conversion_event,
        )

        # Step 6: Upsert campaign rows
        upserted_c = await db.upsert_ga4_metrics(campaign_rows)

        # Step 7: Fetch landing page metrics (D-08: same conversion event filter)
        lp_rows = await fetch_landing_page_metrics(
            client,
            settings.ga4_property_id,
            date_iso,
            date_iso,
            conversion_event=settings.ga4_conversion_event,
        )

        # Step 8: Upsert landing page rows
        upserted_lp = await db.upsert_ga4_landing_pages(lp_rows)

        # Step 9: Finish log
        await db.log_ingestion_finish(log_id, "success", rows_upserted=upserted_c + upserted_lp)
        logger.info(
            "ingest_complete",
            source="ga4",
            date=date_iso,
            campaign_rows=upserted_c,
            landing_rows=upserted_lp,
        )

    except Exception as exc:  # noqa: BLE001
        sentry_sdk.capture_exception(exc)
        logger.error("ingest_failed", source="ga4", date=date_iso, error=str(exc))
        if log_id is not None:
            await db.log_ingestion_finish(log_id, "failed", error=str(exc))

        # D-09: Circuit breaker — alert operator after 3 consecutive failures
        try:
            if await _check_circuit_breaker(db, "ga4"):
                chat_id = next(iter(settings.telegram_allowed_chat_ids), None)
                if chat_id and bot:
                    safe_error = html.escape(str(exc)[:200])
                    await bot.send_message(
                        chat_id=chat_id,
                        text=(
                            f"🚨 <b>GA4 Ingest Circuit Breaker</b>\n"
                            f"3 consecutive failures. Last error:\n"
                            f"<code>{safe_error}</code>"
                        ),
                        parse_mode=ParseMode.HTML,
                    )
                    logger.warning("circuit_breaker_alert_sent", source="ga4")
        except Exception as cb_exc:  # noqa: BLE001
            logger.error("circuit_breaker_alert_failed", error=str(cb_exc))


async def run_ga4_ingest_for_date(db, settings, date_iso: str) -> None:
    """Public entry point for backfill. Skips bot and bypasses 6-hour cache."""
    await _run_ga4_ingest(
        bot=None, db=db, settings=settings,
        date_override=date_iso, skip_cache=True,
    )


async def ga4_ingest_job() -> None:
    """APScheduler job entry point — zero args, resources from module globals.

    Called by AsyncIOScheduler at 01:00 in settings.report_timezone.
    CRITICAL: This function takes NO arguments. Passing Bot/DBClient as APScheduler
    job args causes PicklingError with SQLAlchemyJobStore (RESEARCH Pitfall 2).
    """
    if _bot is None or _db is None or _settings is None:
        logger.error("ingest_job_resources_not_registered")
        return
    await _run_ga4_ingest(_bot, _db, _settings)
