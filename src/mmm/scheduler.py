"""MMM weekly scheduler job and Telegram message formatter.

D-08: Weekly Telegram insight (Sunday 23:00)
D-09: deposit_value_usd dual-mode ROAS rendering (deposits-per-$1000 vs dollar ROAS)
D-18/D-19: APScheduler zero-arg async job, register_job_resources module-globals pattern
D-20: Read-only on ad_metrics; only writes to mmm_results (via DBClient.upsert_mmm_result)

Pattern reference: src/ga4/ingest.py (register_job_resources + module globals).
"""
from __future__ import annotations

import asyncio
from datetime import date
from typing import TYPE_CHECKING, Optional

import numpy as np
import structlog

from src.mmm.model import MMMResult, fit_mmm

if TYPE_CHECKING:
    from aiogram import Bot

    from src.config import Settings
    from src.db.client import DBClient

logger = structlog.get_logger(__name__)


# ------------------------------------------------------------------ #
# Module globals — wired by register_job_resources() before scheduler.start()
# CRITICAL: Resources are NOT passed as APScheduler job args (PicklingError
# with SQLAlchemyJobStore — RESEARCH Pitfall 2).
# ------------------------------------------------------------------ #
_bot: Optional["Bot"] = None
_db: Optional["DBClient"] = None
_settings: Optional["Settings"] = None


# Week count SQL — D-07. Campaign-level only (ad_set_id='' AND ad_id='') per CLAUDE.md.
_WEEK_COUNT_SQL = (
    "SELECT COUNT(DISTINCT strftime('%Y-%W', date)) AS weeks "
    "FROM ad_metrics "
    "WHERE ad_set_id = '' AND ad_id = '' "
    "AND spend > 0"
)

# Data load SQL — uses meta_form_submit_deposit (NOT meta_purchases_7dclick, per RESEARCH Pitfall 7).
_DATA_LOAD_SQL = (
    "SELECT date, "
    "       SUM(spend)                    AS daily_spend, "
    "       SUM(meta_form_submit_deposit) AS daily_deposits "
    "FROM ad_metrics "
    "WHERE ad_set_id = '' AND ad_id = '' "
    "AND spend > 0 "
    "GROUP BY date "
    "ORDER BY date ASC"
)


def register_job_resources(bot: "Bot", db: "DBClient", settings: "Settings") -> None:
    """Store bot, db, settings in module globals before scheduler.start().

    Mirrors the pattern used in src/ga4/ingest.py and src/meta/ingest.py.
    """
    global _bot, _db, _settings
    _bot = bot
    _db = db
    _settings = settings
    logger.info("mmm_scheduler_resources_registered")


def build_mmm_telegram_message(
    result: MMMResult,
    week_label: str,
    deposit_value_usd: float = 0.0,
) -> str:
    """Build the D-08 weekly Telegram message.

    Pure function — testable without bot or DB.

    Rules:
    - First line: "📊 Weekly MMM Insight (week of {week_label})"
    - Always include media_pct / baseline_pct line
    - ROAS line:
        * omit entirely when result.incremental_roas_per_1k is None
        * "Meta generated N.N deposits per $1000 spend." when deposit_value_usd == 0
        * "Incremental ROAS: N.Nx (...)" when deposit_value_usd > 0
    - Always include optimal_daily_spend (rounded to integer dollars)
    - Append "⚠ Directional only — N weeks of data" when maturity_label == 'directional_only'
    - Append footnote "* Based on N weeks of data. Results strengthen at 3+ months."
      when maturity_label == 'early'
    """
    lines: list[str] = [
        f"📊 Weekly MMM Insight (week of {week_label})",
        "",
        f"Meta drove {result.media_pct:.1f}% of deposits this week "
        f"(baseline: {result.baseline_pct:.1f}%).",
    ]

    if result.incremental_roas_per_1k is not None:
        if deposit_value_usd > 0.0:
            lines.append(
                f"Incremental ROAS: {result.incremental_roas_per_1k:.1f}x "
                f"(every $1 of Meta spend generated ${result.incremental_roas_per_1k:.1f} "
                f"in deposit value)."
            )
        else:
            lines.append(
                f"Meta generated {result.incremental_roas_per_1k:.1f} deposits per $1000 spend."
            )

    lines.append(
        f"Optimal daily spend: ~${result.optimal_daily_spend:.0f} — above this, "
        f"returns diminish sharply."
    )

    if result.maturity_label == "directional_only":
        lines.append("")
        lines.append(f"⚠ Directional only — {result.weeks_of_data} weeks of data")
    elif result.maturity_label == "early":
        lines.append("")
        lines.append(
            f"* Based on {result.weeks_of_data} weeks of data. "
            f"Results strengthen at 3+ months."
        )

    return "\n".join(lines)


async def run_mmm_weekly_job() -> None:
    """APScheduler job entry point — zero args, resources from module globals.

    Steps (D-06, D-07, D-19, D-20, D-21):
      1. Validate resources are registered.
      2. Count weeks of campaign-level spend data.
      3. If weeks < 4 → log + return (no Telegram message, per D-06).
      4. Load daily spend + deposits (campaign-level, meta_form_submit_deposit).
      5. Run fit_mmm in asyncio.to_thread (CPU-bound, per Pattern 6).
      6. If fit_mmm returns None → log + return (no Telegram message).
      7. Persist via _db.upsert_mmm_result.
      8. Build Telegram message via build_mmm_telegram_message.
      9. Send to each chat_id in _settings.telegram_allowed_chat_ids; errors logged, not re-raised.
    """
    if _bot is None or _db is None or _settings is None:
        raise RuntimeError("register_job_resources() not called before run_mmm_weekly_job()")

    # Step 2: Week count (D-07)
    week_rows = await _db.fetch_all(_WEEK_COUNT_SQL)
    weeks = int(week_rows[0]["weeks"]) if week_rows and week_rows[0].get("weeks") is not None else 0

    # Step 3: Insufficient data → skip silently (D-06)
    if weeks < 4:
        logger.info("mmm_job_skipped_insufficient_weeks", weeks=weeks)
        return

    # Step 4: Load daily series (D-20 — read-only, campaign-level)
    data_rows = await _db.fetch_all(_DATA_LOAD_SQL)
    if not data_rows:
        logger.warning("mmm_job_skipped_no_data_rows", weeks=weeks)
        return

    spend = np.array([float(r["daily_spend"] or 0.0) for r in data_rows], dtype=float)
    deposits = np.array([float(r["daily_deposits"] or 0.0) for r in data_rows], dtype=float)

    # Step 5: Fit in thread (Pattern 6 — avoid blocking the event loop)
    run_date = date.today().isoformat()
    result: MMMResult | None = await asyncio.to_thread(
        fit_mmm,
        spend,
        deposits,
        _settings.deposit_value_usd,
        run_date,
        weeks,
    )

    # Step 6: Fit failure → log + skip Telegram (no result to persist)
    if result is None:
        logger.warning("mmm_fit_failed", weeks=weeks, rows=len(data_rows))
        return

    # Step 7: Persist (D-20 — only write target is mmm_results)
    await _db.upsert_mmm_result(result)

    # Step 8: Build message (D-08)
    text = build_mmm_telegram_message(result, run_date, _settings.deposit_value_usd)

    # Step 9: Send to each allowlisted chat; per-chat errors logged but not re-raised
    chat_ids = list(_settings.telegram_allowed_chat_ids or [])
    for chat_id in chat_ids:
        try:
            await _bot.send_message(chat_id=chat_id, text=text)
        except Exception as exc:  # noqa: BLE001 — Telegram errors must not break the job
            logger.warning(
                "mmm_telegram_send_failed",
                chat_id=chat_id,
                error=str(exc),
            )

    logger.info(
        "mmm_weekly_job_complete",
        weeks=weeks,
        media_pct=result.media_pct,
        optimal_daily_spend=result.optimal_daily_spend,
        maturity=result.maturity_label,
        chats_notified=len(chat_ids),
    )
