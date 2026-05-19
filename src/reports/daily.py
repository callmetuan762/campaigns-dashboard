"""Daily report job: 09:00 APScheduler job posting daily Meta Ads digest to Telegram.

REPORT-01: Posted daily at settings.daily_report_hour (default 09:00) in report_timezone.
REPORT-02: Includes TL;DR, spend, ROAS, top/bottom campaigns, spend pacing.
REPORT-04: HTML format with <b> headers, emoji indicators, 4096-char split.
REPORT-05: Heartbeat fired AFTER Telegram returns 200 (D-20 ordering guarantee).
REPORT-06: Chart images sent as separate send_photo calls.

D-02: Reads exclusively from SQLite — no live Meta API call at report time.
CRITICAL: daily_report_job takes NO args (APScheduler + SQLAlchemyJobStore PicklingError).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import sentry_sdk
import structlog
from aiogram.enums import ParseMode
from aiogram.types import BufferedInputFile

from src.ai.tldr import generate_tldr
from src.reports.builder import build_daily_report_html
from src.reports.charts import (
    generate_roas_trend_chart,
    generate_spend_trend_chart,
    generate_top_campaigns_chart,
)
from src.reports.splitter import split_html_message

logger = structlog.get_logger(__name__)

# Module-level globals — never passed as APScheduler job args (PicklingError)
_bot = None
_db = None
_settings = None

# SQL to query yesterday's campaign metrics — named params (CLAUDE.md: no f-string SQL)
_YESTERDAY_METRICS_SQL = """
    SELECT m.campaign_id, c.name AS campaign_name, m.date,
           m.spend, m.impressions, m.clicks, m.ctr, m.cpc, m.cpm,
           m.roas, m.meta_purchases_7dclick, m.meta_cost_per_purchase,
           m.reach, m.frequency
    FROM ad_metrics m
    JOIN campaigns c ON m.campaign_id = c.id
    WHERE m.ad_set_id = '' AND m.ad_id = ''
      AND m.date = :target_date
    ORDER BY m.spend DESC;
"""

# SQL to query 7-day window for chart data
_WEEK_METRICS_SQL = """
    SELECT m.campaign_id, c.name AS campaign_name, m.date,
           m.spend, m.roas
    FROM ad_metrics m
    JOIN campaigns c ON m.campaign_id = c.id
    WHERE m.ad_set_id = '' AND m.ad_id = ''
      AND m.date BETWEEN :start_date AND :end_date
    ORDER BY m.date ASC, m.spend DESC;
"""

# GA4 queries for daily report — named params (CLAUDE.md: no f-string SQL)
_GA4_CAMPAIGN_SQL = """
    SELECT campaign_utm, sessions, users, ga4_purchases_lastclick
    FROM ga4_metrics
    WHERE date = :target_date
    ORDER BY sessions DESC;
"""

_GA4_LANDING_SQL = """
    SELECT landing_page, sessions, total_users,
           ga4_purchases_lastclick, screen_page_views
    FROM ga4_landing_pages
    WHERE date = :target_date
    ORDER BY ga4_purchases_lastclick DESC
    LIMIT 3;
"""

_GA4_LANDING_7DAY_SQL = """
    SELECT landing_page,
           SUM(sessions) AS sessions,
           SUM(ga4_purchases_lastclick) AS ga4_purchases_lastclick
    FROM ga4_landing_pages
    WHERE date BETWEEN :start_date AND :end_date
    GROUP BY landing_page
    ORDER BY ga4_purchases_lastclick DESC
    LIMIT 10;
"""


def register_job_resources(bot, db, settings) -> None:
    """Store resources in module globals. Called from main.py before scheduler.start()."""
    global _bot, _db, _settings
    _bot = bot
    _db = db
    _settings = settings
    logger.info("daily_report_resources_registered")


async def ping_heartbeat(url: str | None) -> None:
    """Fire-and-forget heartbeat ping. Swallows all errors (D-19, D-20).

    CRITICAL (D-20): Must only be called AFTER Telegram API returns 200.
    A delivery failure must prevent this from being called.
    """
    if not url:
        return
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.get(url)
        logger.info("heartbeat_sent")
    except Exception:  # noqa: BLE001
        pass  # heartbeat failure must never crash the report job


async def _run_daily_report(bot, db, settings) -> None:
    """Core daily report logic — queries DB, assembles HTML, sends to Telegram."""
    tz = ZoneInfo(settings.report_timezone)
    today = datetime.now(tz).date()
    yesterday = (today - timedelta(days=1)).isoformat()

    # 7-day window for chart data
    week_start = (today - timedelta(days=7)).isoformat()

    logger.info("daily_report_start", date=yesterday)

    chat_id = next(iter(settings.telegram_allowed_chat_ids), None)
    if not chat_id:
        logger.warning("daily_report_no_chat_id")
        return

    meta_available = True
    ga4_available = True
    yesterday_rows: list[dict] = []
    week_rows: list[dict] = []
    ga4_campaign_rows: list[dict] = []
    ga4_landing_rows: list[dict] = []
    ga4_landing_7day_rows: list[dict] = []

    # Per-source guarded block: Meta (SC-2 graceful degradation)
    try:
        # D-02: Read exclusively from SQLite
        yesterday_rows = await db.fetch_all(
            _YESTERDAY_METRICS_SQL, {"target_date": yesterday}
        )
        week_rows = await db.fetch_all(
            _WEEK_METRICS_SQL, {"start_date": week_start, "end_date": yesterday}
        )
        # Pitfall 5: distinguish failed ingestion from zero-spend days via ingestion_log
        if not yesterday_rows:
            last = await db.fetch_one(
                "SELECT status FROM ingestion_log WHERE source = :source ORDER BY started_at DESC LIMIT 1",
                {"source": "meta_ads"},
            )
            if last and last["status"] == "failed":
                meta_available = False
                logger.warning("daily_report_meta_unavailable", date=yesterday)
    except Exception as exc:  # noqa: BLE001
        import sentry_sdk; sentry_sdk.capture_exception(exc)
        meta_available = False
        logger.warning("daily_report_meta_query_failed", date=yesterday, error=str(exc))

    # Per-source guarded block: GA4 (SC-2 graceful degradation)
    try:
        # GA4-03: D-2 freshness — GA4 ingest stored D-2, so query D-2 to match
        d2_date = (today - timedelta(days=2)).isoformat()
        # 7-day window for GA4 landing page trend (D-04)
        ga4_week_start = (today - timedelta(days=8)).isoformat()

        ga4_campaign_rows = await db.fetch_all(
            _GA4_CAMPAIGN_SQL, {"target_date": d2_date}
        )
        ga4_landing_rows = await db.fetch_all(
            _GA4_LANDING_SQL, {"target_date": d2_date}
        )
        # D-04: 7-day landing page trend
        ga4_landing_7day_rows = await db.fetch_all(
            _GA4_LANDING_7DAY_SQL, {"start_date": ga4_week_start, "end_date": d2_date}
        )
        # Pitfall 5: distinguish failed ingestion from zero-traffic days via ingestion_log
        if not ga4_campaign_rows:
            last = await db.fetch_one(
                "SELECT status FROM ingestion_log WHERE source = :source ORDER BY started_at DESC LIMIT 1",
                {"source": "ga4"},
            )
            if last and last["status"] == "failed":
                ga4_available = False
                logger.warning("daily_report_ga4_unavailable", date=yesterday)
    except Exception as exc:  # noqa: BLE001
        import sentry_sdk; sentry_sdk.capture_exception(exc)
        ga4_available = False
        logger.warning("daily_report_ga4_query_failed", error=str(exc))

    # Outer try/except: TL;DR generation, report assembly, Telegram delivery
    try:
        # Generate TL;DR — graceful degradation on failure (D-23)
        tldr: str | None = None
        if settings.anthropic_api_key and yesterday_rows:
            try:
                api_key = settings.anthropic_api_key.get_secret_value()
                tldr = await generate_tldr(api_key, yesterday_rows, yesterday, db=db)
            except Exception as exc:  # noqa: BLE001
                logger.warning("daily_report_tldr_failed", error=str(exc))
                tldr = None

        # Assemble HTML report
        report_text = build_daily_report_html(
            yesterday_rows, tldr, yesterday,
            ga4_campaign_rows=ga4_campaign_rows,
            ga4_landing_rows=ga4_landing_rows,
            ga4_landing_7day_rows=ga4_landing_7day_rows,
            meta_available=meta_available,
            ga4_available=ga4_available,
        )

        # Split if > 4096 chars (CLAUDE.md pitfall)
        parts = split_html_message(report_text)

        # Send text parts
        for part in parts:
            await bot.send_message(
                chat_id=chat_id,
                text=part,
                parse_mode=ParseMode.HTML,
            )

        # Generate and send charts (REPORT-06) — in asyncio.to_thread for matplotlib safety
        if week_rows:
            spend_png = await asyncio.to_thread(generate_spend_trend_chart, week_rows)
            roas_png = await asyncio.to_thread(generate_roas_trend_chart, week_rows)
            top_png = await asyncio.to_thread(generate_top_campaigns_chart, week_rows)

            if spend_png:
                await bot.send_photo(
                    chat_id=chat_id,
                    photo=BufferedInputFile(file=spend_png, filename="spend_trend.png"),
                    caption="Spend Trend (7-day)",
                )
            if roas_png:
                await bot.send_photo(
                    chat_id=chat_id,
                    photo=BufferedInputFile(file=roas_png, filename="roas_trend.png"),
                    caption="ROAS Trend (7-day)",
                )
            if top_png:
                await bot.send_photo(
                    chat_id=chat_id,
                    photo=BufferedInputFile(file=top_png, filename="top_campaigns.png"),
                    caption="Top Campaigns by Spend",
                )

        # D-20: Heartbeat fires AFTER all Telegram deliveries succeed — NOT in finally
        await ping_heartbeat(settings.heartbeat_url)

        logger.info("daily_report_complete", date=yesterday, parts=len(parts))

    except Exception as exc:  # noqa: BLE001
        sentry_sdk.capture_exception(exc)
        logger.error("daily_report_failed", date=yesterday, error=str(exc))
        # Never propagate — scheduled job must not crash the scheduler


async def daily_report_job() -> None:
    """APScheduler job entry point — zero args, resources from module globals.

    Called by AsyncIOScheduler at settings.daily_report_hour (default 09:00) in report_timezone.
    CRITICAL: Takes NO arguments (APScheduler + SQLAlchemyJobStore pickle constraint).
    """
    if _bot is None or _db is None or _settings is None:
        logger.error("daily_report_resources_not_registered")
        return
    await _run_daily_report(_bot, _db, _settings)
