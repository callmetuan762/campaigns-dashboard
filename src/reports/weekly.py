"""Weekly report job: Monday 09:00 APScheduler job with WoW comparisons.

REPORT-03: Monday weekly summary with WoW comparisons for all Tier-1 metrics.
REPORT-04: HTML format with <b> headers, emoji indicators, 4096-char split.
REPORT-05: Heartbeat fired AFTER Telegram returns 200 (D-20).
REPORT-06: Chart images sent as separate send_photo calls.

D-01: Fires Monday 09:00 in report_timezone.
D-02: Reads exclusively from SQLite.
CRITICAL: weekly_report_job takes NO args (APScheduler SQLAlchemyJobStore PicklingError).
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import sentry_sdk
import structlog
from aiogram.enums import ParseMode
from aiogram.types import BufferedInputFile

from src.ai.tldr import generate_tldr
from src.reports.builder import build_weekly_report_html, get_wow_date_ranges
from src.reports.charts import (
    generate_roas_trend_chart,
    generate_spend_trend_chart,
    generate_top_campaigns_chart,
)
from src.reports.daily import ping_heartbeat  # reuse from daily module
from src.reports.splitter import split_html_message

logger = structlog.get_logger(__name__)

_bot = None
_db = None
_settings = None

# SQL for this week and last week metric windows — named params (CLAUDE.md: no f-string SQL)
_WEEK_WINDOW_SQL = """
    SELECT m.campaign_id, c.name AS campaign_name, m.date,
           m.spend, m.impressions, m.clicks, m.ctr, m.cpc, m.cpm,
           m.roas, m.meta_purchases_7dclick, m.meta_cost_per_purchase,
           m.reach, m.frequency
    FROM ad_metrics m
    JOIN campaigns c ON m.campaign_id = c.id
    WHERE m.ad_set_id = '' AND m.ad_id = ''
      AND m.date BETWEEN :start_date AND :end_date
    ORDER BY m.date ASC, m.spend DESC;
"""

# GA4 weekly queries — named params (CLAUDE.md: no f-string SQL)
_GA4_LANDING_WOW_SQL = """
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
    logger.info("weekly_report_resources_registered")


async def _run_weekly_report(bot, db, settings) -> None:
    """Core weekly report logic — queries DB for two windows, assembles WoW HTML, sends."""
    tz = ZoneInfo(settings.report_timezone)
    today = datetime.now(tz).date()
    date_ranges = get_wow_date_ranges(today)

    week_end = date_ranges["week_end"]
    logger.info("weekly_report_start", week_end=week_end)

    chat_id = next(iter(settings.telegram_allowed_chat_ids), None)
    if not chat_id:
        logger.warning("weekly_report_no_chat_id")
        return

    try:
        # D-02: Read exclusively from SQLite for both windows
        this_week_rows = await db.fetch_all(
            _WEEK_WINDOW_SQL,
            {"start_date": date_ranges["week_start"], "end_date": date_ranges["week_end"]},
        )
        last_week_rows = await db.fetch_all(
            _WEEK_WINDOW_SQL,
            {"start_date": date_ranges["prev_week_start"], "end_date": date_ranges["prev_week_end"]},
        )

        # GA4 WoW queries (D-05)
        ga4_this_week = await db.fetch_all(
            _GA4_LANDING_WOW_SQL,
            {"start_date": date_ranges["week_start"], "end_date": date_ranges["week_end"]},
        )
        ga4_last_week = await db.fetch_all(
            _GA4_LANDING_WOW_SQL,
            {"start_date": date_ranges["prev_week_start"], "end_date": date_ranges["prev_week_end"]},
        )

        # TL;DR using this week's rows for context
        tldr: str | None = None
        if settings.anthropic_api_key and this_week_rows:
            try:
                api_key = settings.anthropic_api_key.get_secret_value()
                tldr = await generate_tldr(api_key, this_week_rows, f"week ending {week_end}", db=db)
            except Exception as exc:  # noqa: BLE001
                logger.warning("weekly_report_tldr_failed", error=str(exc))
                tldr = None

        # Assemble HTML report with WoW comparisons
        report_text = build_weekly_report_html(
            this_week_rows, last_week_rows, tldr, week_end,
            ga4_this_week=ga4_this_week,
            ga4_last_week=ga4_last_week,
        )
        parts = split_html_message(report_text)

        for part in parts:
            await bot.send_message(
                chat_id=chat_id,
                text=part,
                parse_mode=ParseMode.HTML,
            )

        # Charts using this week's data (REPORT-06)
        if this_week_rows:
            spend_png = await asyncio.to_thread(generate_spend_trend_chart, this_week_rows)
            roas_png = await asyncio.to_thread(generate_roas_trend_chart, this_week_rows)
            top_png = await asyncio.to_thread(generate_top_campaigns_chart, this_week_rows)

            if spend_png:
                await bot.send_photo(
                    chat_id=chat_id,
                    photo=BufferedInputFile(file=spend_png, filename="weekly_spend.png"),
                    caption="Weekly Spend Trend",
                )
            if roas_png:
                await bot.send_photo(
                    chat_id=chat_id,
                    photo=BufferedInputFile(file=roas_png, filename="weekly_roas.png"),
                    caption="Weekly ROAS Trend",
                )
            if top_png:
                await bot.send_photo(
                    chat_id=chat_id,
                    photo=BufferedInputFile(file=top_png, filename="weekly_top_campaigns.png"),
                    caption="Top Campaigns (this week)",
                )

        # D-20: Heartbeat fires AFTER all Telegram deliveries succeed
        await ping_heartbeat(settings.heartbeat_url)

        logger.info("weekly_report_complete", week_end=week_end, parts=len(parts))

    except Exception as exc:  # noqa: BLE001
        sentry_sdk.capture_exception(exc)
        logger.error("weekly_report_failed", week_end=week_end, error=str(exc))


async def weekly_report_job() -> None:
    """APScheduler job entry point — zero args, resources from module globals.

    Called by AsyncIOScheduler at Monday 09:00 in report_timezone (D-01).
    CRITICAL: Takes NO arguments (APScheduler + SQLAlchemyJobStore pickle constraint).
    """
    if _bot is None or _db is None or _settings is None:
        logger.error("weekly_report_resources_not_registered")
        return
    await _run_weekly_report(_bot, _db, _settings)
