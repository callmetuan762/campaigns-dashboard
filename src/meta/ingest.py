"""Meta Ads ingest job: APScheduler-compatible zero-arg async function.

META-01: Authenticates via System User token (init_meta_api called once).
META-02: Fetches campaign-level metrics for yesterday.
META-03: Fetches ad-set breakdowns (same run, after campaign fetch).
META-05: Writes to campaigns + ad_metrics via UPSERT helpers (idempotent).
D-03: Writes status to ingestion_log (running → success/failed).
D-08: Circuit breaker after 3 consecutive failures — sends Telegram alert.
D-17: evaluate_alerts() called as final step after successful writes.

CRITICAL (RESEARCH Pitfall 2): meta_ingest_job takes NO args.
Resources are stored in module globals via register_job_resources() called from main.py
before scheduler.start(). APScheduler SQLAlchemyJobStore uses pickle — passing Bot or
DBClient as scheduler args raises PicklingError.
"""
from __future__ import annotations

import html
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import sentry_sdk
import structlog
from aiogram.enums import ParseMode

from src.alerts.engine import evaluate_alerts
from src.meta.client import (
    fetch_adset_insights,
    fetch_campaign_insights,
    fetch_campaign_objectives,
    init_meta_api,
)

logger = structlog.get_logger(__name__)

# Module-level globals — never passed as APScheduler job args (PicklingError)
_bot = None
_db = None
_settings = None

# SQL for circuit-breaker check — named params (CLAUDE.md: no f-string SQL)
_RECENT_FAILURES_SQL = """
    SELECT status FROM ingestion_log
    WHERE source = :source
    ORDER BY started_at DESC
    LIMIT :limit
"""


def register_job_resources(bot, db, settings) -> None:
    """Store bot, db, settings in module globals before scheduler.start().

    Called from main.py after all resources are constructed.
    Pattern 1 (RESEARCH.md): module-globals pattern is the correct approach for
    APScheduler with SQLAlchemyJobStore — avoids PicklingError.
    """
    global _bot, _db, _settings
    _bot = bot
    _db = db
    _settings = settings
    logger.info("ingest_resources_registered")


def _filter_by_brand_prefix(rows: list[dict], prefix: str) -> list[dict]:
    """Drop rows whose campaign_name doesn't start with the configured brand prefix.

    No-op when prefix is empty (single-brand ad account — the common case). Needed
    for shared/agency ad accounts where multiple brands' campaigns live side by side
    (META_CAMPAIGN_NAME_PREFIX) — without this, another brand's spend/campaigns would
    silently get ingested into this brand's dashboard.
    """
    if not prefix:
        return rows
    return [r for r in rows if r.get("campaign_name", "").startswith(prefix)]


def _get_yesterday_iso(timezone_str: str) -> str:
    """Compute yesterday's date in the given timezone (RESEARCH Pitfall 5).

    Meta returns data in the ad account's timezone. Using report_timezone as a proxy
    for the account timezone (v1 single-account assumption).
    """
    tz = ZoneInfo(timezone_str)
    yesterday = datetime.now(tz).date() - timedelta(days=1)
    return yesterday.isoformat()


async def _check_circuit_breaker(db, source: str, threshold: int = 3) -> bool:
    """Return True if last `threshold` ingest runs all failed (circuit is open).

    D-08: After 3 consecutive failures, the circuit breaker triggers.
    """
    rows = await db.fetch_all(
        _RECENT_FAILURES_SQL,
        {"source": source, "limit": threshold},
    )
    if len(rows) < threshold:
        return False
    return all(r.get("status") == "failed" for r in rows)


async def _run_meta_ingest(
    bot,
    db,
    settings,
    date_override: str | None = None,
    suppress_alerts: bool = False,
) -> None:
    """Core ingest logic — called by meta_ingest_job()."""
    date_iso = date_override if date_override is not None else _get_yesterday_iso(settings.report_timezone)
    log_id: int | None = None

    try:
        logger.info("ingest_start", source="meta_ads", date=date_iso)
        log_id = await db.log_ingestion_start("meta_ads")

        # D-05: Validate Meta credentials are configured before attempting API call
        if not settings.meta_access_token or not settings.meta_ad_account_id:
            logger.warning("ingest_skipped_no_credentials", date=date_iso)
            await db.log_ingestion_finish(log_id, "failed", error="META_ACCESS_TOKEN or META_AD_ACCOUNT_ID not configured")
            return

        # Initialize Meta SDK (synchronous — called before any async API calls)
        init_meta_api(settings)

        # Fetch campaign-level metrics for yesterday (META-02)
        campaign_rows = await fetch_campaign_insights(
            settings.meta_ad_account_id, date_iso
        )
        campaign_rows = _filter_by_brand_prefix(campaign_rows, settings.meta_campaign_name_prefix)

        # Campaign objective (goal) — account-wide metadata, not date-scoped,
        # so this is fetched once per ingest run (never per-adset/per-ad).
        # Graceful degradation (CLAUDE.md): on failure, objectives stays {} so
        # every row below gets objective=None; upsert_campaign's
        # COALESCE(excluded.objective, campaigns.objective) then leaves any
        # existing stored objective untouched rather than nulling it out.
        try:
            objectives = await fetch_campaign_objectives(
                settings.meta_ad_account_id, settings.meta_campaign_name_prefix
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("meta_objectives_fetch_failed", error=str(exc))
            objectives = {}

        # Build campaign dimension rows for campaigns table (META-05)
        campaign_dim_rows = [
            {
                "id": r["campaign_id"],
                "source": "meta_ads",
                "name": r.get("campaign_name", r["campaign_id"]),
                "status": "ACTIVE",
                "objective": objectives.get(r["campaign_id"]),
            }
            for r in campaign_rows
            if r.get("campaign_id")
        ]
        await db.upsert_campaign(campaign_dim_rows)

        # Build ad_metrics rows (ad_set_id='', ad_id='' for campaign-level rows — META-05)
        metrics_rows = [
            {k: v for k, v in r.items() if k != "campaign_name"}
            for r in campaign_rows
            if r.get("campaign_id")
        ]
        campaign_upserted = await db.upsert_ad_metrics(metrics_rows)

        # META-03: Ad-set level breakdowns
        adset_rows = await fetch_adset_insights(settings.meta_ad_account_id, date_iso)
        adset_rows = _filter_by_brand_prefix(adset_rows, settings.meta_campaign_name_prefix)
        adset_metrics = [
            {k: v for k, v in r.items() if k != "campaign_name"}
            for r in adset_rows
        ]
        adset_upserted = await db.upsert_ad_metrics(adset_metrics)

        total_upserted = campaign_upserted + adset_upserted
        await db.log_ingestion_finish(log_id, "success", rows_upserted=total_upserted)
        logger.info(
            "ingest_complete",
            source="meta_ads",
            date=date_iso,
            campaign_rows=campaign_upserted,
            adset_rows=adset_upserted,
        )

        # D-17: Alert evaluation runs as the FINAL step after successful writes
        if not suppress_alerts:
            await evaluate_alerts(db, bot, settings, date_iso)

    except Exception as exc:  # noqa: BLE001
        sentry_sdk.capture_exception(exc)
        logger.error("ingest_failed", source="meta_ads", date=date_iso, error=str(exc))
        if log_id is not None:
            await db.log_ingestion_finish(log_id, "failed", error=str(exc))

        # D-08: Circuit breaker — alert operator after 3 consecutive failures
        try:
            if await _check_circuit_breaker(db, "meta_ads"):
                chat_id = next(iter(settings.telegram_allowed_chat_ids), None)
                if chat_id and bot:
                    safe_error = html.escape(str(exc)[:200])
                    await bot.send_message(
                        chat_id=chat_id,
                        text=(
                            f"🚨 <b>Meta Ingest Circuit Breaker</b>\n"
                            f"3 consecutive failures. Last error:\n"
                            f"<code>{safe_error}</code>"
                        ),
                        parse_mode=ParseMode.HTML,
                    )
                    logger.warning("circuit_breaker_alert_sent", source="meta_ads")
        except Exception as cb_exc:  # noqa: BLE001
            logger.error("circuit_breaker_alert_failed", error=str(cb_exc))


async def run_meta_ingest_for_date(db, settings, date_iso: str) -> None:
    """Public entry point for backfill. Skips bot, heartbeat, and alerts."""
    await _run_meta_ingest(
        bot=None, db=db, settings=settings,
        date_override=date_iso, suppress_alerts=True,
    )


async def meta_ingest_job() -> None:
    """APScheduler job entry point — zero args, resources from module globals.

    Called by AsyncIOScheduler at 02:00 in settings.report_timezone (D-01).

    CRITICAL: This function takes NO arguments. Resources (_bot, _db, _settings)
    are accessed via module globals set by register_job_resources() before scheduler.start().
    Passing Bot/DBClient as APScheduler job args causes PicklingError with SQLAlchemyJobStore
    (RESEARCH Pitfall 2).
    """
    if _bot is None or _db is None or _settings is None:
        logger.error("ingest_job_resources_not_registered")
        return
    await _run_meta_ingest(_bot, _db, _settings)
