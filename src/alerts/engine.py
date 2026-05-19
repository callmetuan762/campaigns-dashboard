"""Alert engine: evaluates 5 threshold-based conditions after Meta ingest.

ALERT-01: Spend spike — daily spend > 7-day rolling average * (1 + spike_pct/100)
ALERT-02: ROAS drop — ROAS < configurable floor
ALERT-03: Zero conversion — spend > threshold with 0 purchases
ALERT-04: Budget pacing — monthly spend on pace to deviate > 20% from current run rate
ALERT-05: CPC spike — CPC > 7-day rolling average * multiplier

D-17: Called as final step of meta_ingest_job (not a separate scheduler job).
D-18: One alert per campaign per alert-type per calendar day — dedup via alert_log.
"""
from __future__ import annotations

import html
from datetime import date, timedelta
from enum import StrEnum

import sentry_sdk
import structlog
from aiogram import Bot
from aiogram.enums import ParseMode

logger = structlog.get_logger(__name__)

# Rolling window SQL for spend spike (ALERT-01) and CPC spike (ALERT-05)
# ROWS BETWEEN 7 PRECEDING AND 1 PRECEDING: excludes current row from avg
# Named params: :lookback_start, :target_date (CLAUDE.md: no f-string SQL)
_ROLLING_AVG_SQL = """
    SELECT
        campaign_id,
        date,
        spend,
        cpc,
        roas,
        meta_purchases_7dclick,
        AVG(spend) OVER (
            PARTITION BY campaign_id
            ORDER BY date
            ROWS BETWEEN 7 PRECEDING AND 1 PRECEDING
        ) AS avg_spend_7d,
        AVG(cpc) OVER (
            PARTITION BY campaign_id
            ORDER BY date
            ROWS BETWEEN 7 PRECEDING AND 1 PRECEDING
        ) AS avg_cpc_7d
    FROM ad_metrics
    WHERE ad_set_id = '' AND ad_id = ''
      AND date BETWEEN :lookback_start AND :target_date
    ORDER BY campaign_id, date;
"""

_MONTHLY_SPEND_SQL = """
    SELECT
        campaign_id,
        SUM(spend) AS monthly_spend
    FROM ad_metrics
    WHERE ad_set_id = '' AND ad_id = ''
      AND date BETWEEN :month_start AND :target_date
    GROUP BY campaign_id;
"""

_CAMPAIGN_NAME_SQL = """
    SELECT id, name FROM campaigns WHERE id = :campaign_id LIMIT 1;
"""


class AlertType(StrEnum):
    """Alert type identifiers — stored in alert_log.alert_type column."""

    SPEND_SPIKE = "SPEND_SPIKE"
    ROAS_DROP = "ROAS_DROP"
    ZERO_CONVERSION = "ZERO_CONVERSION"
    BUDGET_PACING = "BUDGET_PACING"
    CPC_SPIKE = "CPC_SPIKE"


async def _send_alert(
    bot: Bot,
    chat_id: int,
    db,
    alert_type: AlertType,
    campaign_id: str,
    date_str: str,
    message_html: str,
) -> bool:
    """Send alert if not already fired today. Returns True if message was sent.

    D-18: db.log_alert() returns False if duplicate — no message sent in that case.
    D-09: message_html must already have html.escape() applied to all dynamic strings.
    """
    newly_fired = await db.log_alert(str(alert_type), campaign_id, date_str)
    if not newly_fired:
        logger.info(
            "alert_dedup_skipped",
            alert_type=str(alert_type),
            campaign_id=campaign_id,
            date=date_str,
        )
        return False

    try:
        await bot.send_message(chat_id=chat_id, text=message_html, parse_mode=ParseMode.HTML)
        logger.info(
            "alert_sent",
            alert_type=str(alert_type),
            campaign_id=campaign_id,
            date=date_str,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("alert_send_failed", alert_type=str(alert_type), error=str(exc))
    return True


async def _get_campaign_name(db, campaign_id: str) -> str:
    """Look up campaign name from campaigns table. Falls back to campaign_id on miss."""
    rows = await db.fetch_all(_CAMPAIGN_NAME_SQL, {"campaign_id": campaign_id})
    if rows:
        return rows[0].get("name") or campaign_id
    return campaign_id


async def evaluate_alerts(db, bot: Bot, settings, target_date: str) -> None:
    """Evaluate all 5 alert conditions against data for target_date.

    Called as the final step of meta_ingest_job after all UPSERT writes complete.
    target_date: ISO YYYY-MM-DD string for the day just ingested.

    D-17: This function is the final step of meta_ingest_job.
    D-18: alert_log UNIQUE constraint prevents re-alerting.
    """
    try:
        chat_id = next(iter(settings.telegram_allowed_chat_ids), None)
        if not chat_id:
            logger.warning("evaluate_alerts_no_chat_id")
            return

        lookback_start = (
            date.fromisoformat(target_date) - timedelta(days=8)
        ).isoformat()

        rows = await db.fetch_all(
            _ROLLING_AVG_SQL,
            {"lookback_start": lookback_start, "target_date": target_date},
        )

        # Filter to only the target date rows (window function returns all dates for context)
        today_rows = [r for r in rows if r.get("date") == target_date]

        if not today_rows:
            logger.info("evaluate_alerts_no_data", date=target_date)
            return

        sent_count = 0

        for row in today_rows:
            campaign_id = row["campaign_id"]
            campaign_name = await _get_campaign_name(db, campaign_id)
            safe_name = html.escape(str(campaign_name))

            spend = float(row.get("spend") or 0)
            roas = float(row.get("roas") or 0)
            cpc = float(row.get("cpc") or 0)
            purchases = int(row.get("meta_purchases_7dclick") or 0)
            avg_spend_7d = row.get("avg_spend_7d")
            avg_cpc_7d = row.get("avg_cpc_7d")

            # ALERT-01: Spend spike
            if (
                avg_spend_7d is not None
                and avg_spend_7d > 0
                and spend > avg_spend_7d * (1 + settings.alert_spend_spike_pct / 100)
            ):
                msg = (
                    f"\U0001f6a8 <b>Spend Spike</b>\n"
                    f"Campaign: {safe_name}\n"
                    f"Today: <b>${spend:,.2f}</b> vs 7-day avg ${avg_spend_7d:,.2f} "
                    f"(+{((spend / avg_spend_7d - 1) * 100):.0f}%)\n"
                    f"Date: {html.escape(target_date)}"
                )
                if await _send_alert(
                    bot, chat_id, db, AlertType.SPEND_SPIKE, campaign_id, target_date, msg
                ):
                    sent_count += 1

            # ALERT-02: ROAS drop
            if spend > 1.0 and roas < settings.alert_roas_floor:
                msg = (
                    f"\U0001f6a8 <b>ROAS Drop</b>\n"
                    f"Campaign: {safe_name}\n"
                    f"ROAS: <b>{roas:.2f}</b> (floor: {settings.alert_roas_floor})\n"
                    f"Spend: ${spend:,.2f} | Date: {html.escape(target_date)}"
                )
                if await _send_alert(
                    bot, chat_id, db, AlertType.ROAS_DROP, campaign_id, target_date, msg
                ):
                    sent_count += 1

            # ALERT-03: Zero conversion
            if spend > settings.alert_zero_conv_spend_threshold and purchases == 0:
                msg = (
                    f"\U0001f507 <b>Zero Conversions</b>\n"
                    f"Campaign: {safe_name}\n"
                    f"Spend: <b>${spend:,.2f}</b> with 0 purchases\n"
                    f"Date: {html.escape(target_date)}"
                )
                if await _send_alert(
                    bot, chat_id, db, AlertType.ZERO_CONVERSION, campaign_id, target_date, msg
                ):
                    sent_count += 1

            # ALERT-05: CPC spike
            if (
                avg_cpc_7d is not None
                and avg_cpc_7d > 0
                and cpc > avg_cpc_7d * settings.alert_cpc_spike_multiplier
            ):
                msg = (
                    f"⚠️ <b>CPC Spike</b>\n"
                    f"Campaign: {safe_name}\n"
                    f"CPC: <b>${cpc:.2f}</b> vs 7-day avg ${avg_cpc_7d:.2f} "
                    f"(×{cpc / avg_cpc_7d:.1f})\n"
                    f"Date: {html.escape(target_date)}"
                )
                if await _send_alert(
                    bot, chat_id, db, AlertType.CPC_SPIKE, campaign_id, target_date, msg
                ):
                    sent_count += 1

        # ALERT-04: Budget pacing (per-campaign monthly projection)
        month_start = date.fromisoformat(target_date).replace(day=1).isoformat()
        monthly_rows = await db.fetch_all(
            _MONTHLY_SPEND_SQL,
            {"month_start": month_start, "target_date": target_date},
        )
        days_elapsed = date.fromisoformat(target_date).day
        days_in_month = 30  # approximation; sufficient for pacing alert

        for mrow in monthly_rows:
            campaign_id = mrow["campaign_id"]
            monthly_spend = float(mrow.get("monthly_spend") or 0)
            # Need minimum 7 days to establish a meaningful baseline
            if days_elapsed < 7:
                continue
            if monthly_spend <= 0:
                continue
            daily_rate = monthly_spend / days_elapsed
            projected_monthly = daily_rate * days_in_month
            deviation_pct = ((projected_monthly - monthly_spend) / monthly_spend) * 100
            if abs(deviation_pct) > settings.alert_budget_pacing_pct:
                pacing_campaign_name = await _get_campaign_name(db, campaign_id)
                safe_pacing_name = html.escape(str(pacing_campaign_name))
                direction = "over" if deviation_pct > 0 else "under"
                msg = (
                    f"⚠️ <b>Budget Pacing Alert</b>\n"
                    f"Campaign: {safe_pacing_name}\n"
                    f"MTD spend: <b>${monthly_spend:,.2f}</b> "
                    f"({days_elapsed} days)\n"
                    f"On pace to {direction}spend by {abs(deviation_pct):.0f}%\n"
                    f"Date: {html.escape(target_date)}"
                )
                if await _send_alert(
                    bot, chat_id, db, AlertType.BUDGET_PACING, campaign_id, target_date, msg
                ):
                    sent_count += 1

        logger.info("evaluate_alerts_complete", date=target_date, sent=sent_count)

    except Exception as exc:  # noqa: BLE001
        sentry_sdk.capture_exception(exc)
        logger.error("evaluate_alerts_error", error=str(exc), date=target_date)
