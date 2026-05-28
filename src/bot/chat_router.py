"""Phase 4 Telegram routing: free-text catch-all + inline-keyboard callbacks.

CHAT-01: Allowlisted users send free text -> Claude tool-use chat (any non-command).
CHAT-07: Four inline-keyboard buttons appear after every substantive answer.
D-10: Catch-all filter is F.text & ~F.text.startswith("/") so /commands route to
      the command router (which is included FIRST in setup.py — Pitfall 4).
D-11: send_chat_action(typing) before each Claude call.
D-14: 4 buttons (drill_down / compare_week / why / show_chart).
D-15: Non-chart button taps go through the same AI handler as typed messages.
D-16: 'show_chart' delegates to src/reports/charts.py — no Claude call.
      Chart metric is detected from the AI response text and stored in callback data.
AllowlistMiddleware on dp.message AND dp.callback_query is registered in setup.py
(Phase 1) — chat handlers inherit allowlist protection automatically.
"""
from __future__ import annotations

import html
from datetime import date, timedelta

import structlog
from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src.config import Settings
from src.db.client import DBClient
from src.reports.charts import generate_metric_trend_chart
from src.reports.splitter import split_html_message

logger = structlog.get_logger(__name__)

# Validated set of column names allowed in the chart SQL query (SQL-safety gate).
_ALLOWED_CHART_METRICS: frozenset[str] = frozenset({
    "spend", "roas", "ctr", "cpc", "cpm",
    "clicks", "impressions", "meta_purchases_7dclick",
})

# Human-readable button label for each metric.
_METRIC_LABELS: dict[str, str] = {
    "spend":                  "spend",
    "roas":                   "ROAS",
    "ctr":                    "CTR",
    "cpc":                    "CPC",
    "cpm":                    "CPM",
    "clicks":                 "clicks",
    "impressions":            "impressions",
    "meta_purchases_7dclick": "purchases",
}


def _detect_chart_metric(text: str) -> str:
    """Scan AI response text for the most prominent metric keyword.

    Priority: CTR > ROAS > CPC > CPM > purchases > clicks > impressions > spend.
    Returns a key from _ALLOWED_CHART_METRICS.
    """
    t = text.lower()
    if "ctr" in t or "click-through" in t or "click through" in t:
        return "ctr"
    if "roas" in t or "return on ad" in t:
        return "roas"
    if "cpc" in t or "cost per click" in t:
        return "cpc"
    if "cpm" in t or "cost per mille" in t or "cost per thousand" in t:
        return "cpm"
    if "purchase" in t or "conversion" in t:
        return "meta_purchases_7dclick"
    if "click" in t:
        return "clicks"
    if "impression" in t:
        return "impressions"
    return "spend"


def _build_chart_sql(metric: str) -> str:
    """Build the chart data SQL with a validated column name (SQL-safety gate)."""
    col = metric if metric in _ALLOWED_CHART_METRICS else "spend"
    return (
        f"SELECT c.name AS campaign_name, m.date, m.{col} AS value, m.spend "  # noqa: S608
        "FROM ad_metrics m "
        "JOIN campaigns c ON m.campaign_id = c.id "
        "WHERE m.ad_set_id = '' AND m.ad_id = '' "
        "AND m.date BETWEEN :start_date AND :end_date "
        "ORDER BY m.date ASC"
    )


class ChatAction(CallbackData, prefix="chat"):
    """Inline keyboard callback payload (D-14). 64-byte limit honored.

    action: 'drill_down' | 'compare_week' | 'why' | 'show_chart'
    metric: chart metric key (only used for show_chart; default 'spend').
    Longest possible value: 'chat:show_chart:meta_purchases_7dclick' = 38 bytes.
    """

    action: str
    metric: str = "spend"


# D-14 / D-15 canned messages injected as the user turn.
_ACTION_TO_TEXT: dict[str, str] = {
    "drill_down":    "Drill down on the previous results with more detail.",
    "compare_week":  "Compare these metrics to last week.",
    "why":           "Why is this happening? What factors might explain this?",
}


def build_followup_keyboard(metric: str = "spend") -> InlineKeyboardMarkup:
    """Render the 4-button inline keyboard with a context-aware chart label (D-14)."""
    safe_metric = metric if metric in _ALLOWED_CHART_METRICS else "spend"
    label = _METRIC_LABELS.get(safe_metric, safe_metric)
    builder = InlineKeyboardBuilder()
    builder.button(text="Drill down",
                   callback_data=ChatAction(action="drill_down", metric=safe_metric))
    builder.button(text="Compare to last week",
                   callback_data=ChatAction(action="compare_week", metric=safe_metric))
    builder.button(text="Why is this happening?",
                   callback_data=ChatAction(action="why", metric=safe_metric))
    builder.button(text=f"Show {label} chart",
                   callback_data=ChatAction(action="show_chart", metric=safe_metric))
    builder.adjust(2)
    return builder.as_markup()


async def _send_chat_response(message: Message, response_text: str) -> None:
    """Split a long AI response, detect chart metric, and reply with keyboard."""
    metric = _detect_chart_metric(response_text)
    escaped = html.escape(response_text)
    parts = split_html_message(escaped)
    last_idx = len(parts) - 1
    for i, part in enumerate(parts):
        markup = build_followup_keyboard(metric) if i == last_idx else None
        await message.reply(part, reply_markup=markup)


def build_chat_router() -> Router:
    """Build the Phase 4 chat router.

    MUST be included AFTER build_router() in setup.py (Pitfall 4 — command router
    first, catch-all second).
    """
    router = Router(name="phase4_chat")

    @router.message(Command("ask"))
    async def handle_ask_command(
        message: Message,
        bot: Bot,
        db: DBClient,
        settings: Settings,
    ) -> None:
        """Group-friendly AI entry-point (/ask <question>) — works with privacy mode ON."""
        from src.ai.chat import handle_chat_message
        user_id = message.from_user.id if message.from_user else None
        if user_id is None:
            return
        text = message.text or ""
        parts = text.split(None, 1)
        user_text = parts[1].strip() if len(parts) > 1 else ""
        if not user_text:
            await message.reply(
                "Please add a question — e.g. <code>/ask which campaign had the best ROAS?</code>"
            )
            return
        logger.info("ask_command_received", chat_id=message.chat.id, user_id=user_id)
        await bot.send_chat_action(message.chat.id, "typing")
        response_text = await handle_chat_message(
            user_text=user_text,
            chat_id=message.chat.id,
            user_id=user_id,
            bot=bot,
            db=db,
            settings=settings,
        )
        await _send_chat_response(message, response_text)

    @router.message(F.text & ~F.text.startswith("/"))
    async def handle_text_message(
        message: Message,
        bot: Bot,
        db: DBClient,
        settings: Settings,
    ) -> None:
        from src.ai.chat import handle_chat_message
        user_id = message.from_user.id if message.from_user else None
        if user_id is None:
            logger.warning("text_message_no_user", chat_id=message.chat.id)
            return
        user_text = message.text or ""
        logger.info(
            "chat_message_received",
            chat_id=message.chat.id, user_id=user_id,
            text_len=len(user_text),
        )
        response_text = await handle_chat_message(
            user_text=user_text,
            chat_id=message.chat.id,
            user_id=user_id,
            bot=bot,
            db=db,
            settings=settings,
        )
        await _send_chat_response(message, response_text)

    @router.callback_query(ChatAction.filter())
    async def handle_chat_action(
        callback: CallbackQuery,
        callback_data: ChatAction,
        bot: Bot,
        db: DBClient,
        settings: Settings,
    ) -> None:
        await callback.answer()  # dismiss spinner first (Pitfall 3)
        from src.ai.chat import handle_chat_message

        if callback.message is None or callback.from_user is None:
            logger.warning("callback_missing_message_or_user", action=callback_data.action)
            return

        chat_id = callback.message.chat.id
        user_id = callback.from_user.id
        action = callback_data.action
        metric = callback_data.metric if callback_data.metric in _ALLOWED_CHART_METRICS else "spend"

        if action == "show_chart":
            end_date = date.today() - timedelta(days=1)
            start_date = end_date - timedelta(days=6)
            rows = await db.fetch_all(
                _build_chart_sql(metric),
                {"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
            )
            if not rows:
                await callback.message.reply(
                    "No chart data available for the recent window.",
                    reply_markup=build_followup_keyboard(metric),
                )
                return
            try:
                chart_bytes = generate_metric_trend_chart(rows, metric)
            except Exception as exc:  # noqa: BLE001
                logger.error("show_chart_failed", metric=metric, error=str(exc))
                await callback.message.reply(
                    "Chart generation failed.",
                    reply_markup=build_followup_keyboard(metric),
                )
                return
            if not chart_bytes:
                await callback.message.reply(
                    "No data to plot for this metric.",
                    reply_markup=build_followup_keyboard(metric),
                )
                return
            label = _METRIC_LABELS.get(metric, metric)
            photo = BufferedInputFile(chart_bytes, filename=f"{metric}_trend.png")
            await bot.send_photo(
                chat_id=chat_id,
                photo=photo,
                caption=f"{label.upper()} trend: {start_date} to {end_date}",
                reply_markup=build_followup_keyboard(metric),
            )
            logger.info("show_chart_sent", chat_id=chat_id, user_id=user_id, metric=metric)
            return

        # drill_down / compare_week / why → AI handler
        injected = _ACTION_TO_TEXT.get(action)
        if injected is None:
            logger.warning("unknown_chat_action", action=action)
            return

        logger.info("chat_button_tapped", chat_id=chat_id, user_id=user_id, action=action)
        response_text = await handle_chat_message(
            user_text=injected,
            chat_id=chat_id,
            user_id=user_id,
            bot=bot,
            db=db,
            settings=settings,
        )
        await _send_chat_response(callback.message, response_text)

    return router
