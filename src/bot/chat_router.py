"""Phase 4 Telegram routing: free-text catch-all + inline-keyboard callbacks.

CHAT-01: Allowlisted users send free text -> Claude tool-use chat (any non-command).
CHAT-07: Four inline-keyboard buttons appear after every substantive answer.
D-10: Catch-all filter is F.text & ~F.text.startswith("/") so /commands route to
      the command router (which is included FIRST in setup.py — Pitfall 4).
D-11: send_chat_action(typing) before each Claude call.
D-14: 4 buttons (drill_down / compare_week / why / show_chart).
D-15: Non-chart button taps go through the same AI handler as typed messages.
D-16: 'show_chart' delegates to src/reports/charts.py — no Claude call.
AllowlistMiddleware on dp.message AND dp.callback_query is registered in setup.py
(Phase 1) — chat handlers inherit allowlist protection automatically.
"""
from __future__ import annotations

import html
from datetime import date, timedelta

import structlog
from aiogram import Bot, F, Router
from aiogram.filters.callback_data import CallbackData
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

# handle_chat_message is imported deferred inside handler bodies (see below)
from src.config import Settings
from src.db.client import DBClient
from src.reports.charts import generate_spend_trend_chart
from src.reports.splitter import split_html_message

logger = structlog.get_logger(__name__)


class ChatAction(CallbackData, prefix="chat"):
    """Inline keyboard callback payload (D-14). 64-byte limit honored.

    action values: 'drill_down' | 'compare_week' | 'why' | 'show_chart'.
    """

    action: str


# D-14 / D-15 canned messages — injected verbatim as the user turn so they
# appear in future conversation history with role='user'.
_ACTION_TO_TEXT: dict[str, str] = {
    "drill_down": "Drill down on the previous results with more detail.",
    "compare_week": "Compare these metrics to last week.",
    "why": "Why is this happening? What factors might explain this?",
}


# SQL for "Show chart" — 7-day spend trend across all campaigns
_SHOW_CHART_SQL = """
    SELECT m.campaign_id, c.name AS campaign_name, m.date, m.spend, m.roas
    FROM ad_metrics m
    JOIN campaigns c ON m.campaign_id = c.id
    WHERE m.ad_set_id = '' AND m.ad_id = ''
      AND m.date BETWEEN :start_date AND :end_date
    ORDER BY m.date ASC, m.spend DESC
"""


def build_followup_keyboard() -> InlineKeyboardMarkup:
    """Render the 4-button inline keyboard (D-14)."""
    builder = InlineKeyboardBuilder()
    builder.button(text="Drill down",
                   callback_data=ChatAction(action="drill_down"))
    builder.button(text="Compare to last week",
                   callback_data=ChatAction(action="compare_week"))
    builder.button(text="Why is this happening?",
                   callback_data=ChatAction(action="why"))
    builder.button(text="Show chart",
                   callback_data=ChatAction(action="show_chart"))
    builder.adjust(2)  # 2 buttons per row
    return builder.as_markup()


async def _send_chat_response(
    message: Message,
    response_text: str,
) -> None:
    """Split a long AI response and reply; the LAST part carries the keyboard.

    Existing splitter handles the 4096-char Telegram limit (REPORT-04).
    """
    # AI response is plain text — escape defensively before HTML send.
    escaped = html.escape(response_text)
    parts = split_html_message(escaped)
    last_idx = len(parts) - 1
    for i, part in enumerate(parts):
        markup = build_followup_keyboard() if i == last_idx else None
        await message.reply(part, reply_markup=markup)


def build_chat_router() -> Router:
    """Build the Phase 4 chat router.

    MUST be included AFTER build_router() in setup.py (Pitfall 4 — command router
    first, catch-all second).
    """
    router = Router(name="phase4_chat")

    @router.message(F.text & ~F.text.startswith("/"))
    async def handle_text_message(
        message: Message,
        bot: Bot,
        db: DBClient,
        settings: Settings,
    ) -> None:
        from src.ai.chat import handle_chat_message  # deferred — chat.py created in same wave (04-03)
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
        # Pitfall 3: answer() MUST be the first await — dismisses the spinner.
        await callback.answer()
        from src.ai.chat import handle_chat_message  # deferred — chat.py created in same wave (04-03)

        if callback.message is None or callback.from_user is None:
            logger.warning(
                "callback_missing_message_or_user",
                action=callback_data.action,
            )
            return

        chat_id = callback.message.chat.id
        user_id = callback.from_user.id
        action = callback_data.action

        if action == "show_chart":
            # D-16: direct chart generation, NO Claude call
            end_date = date.today() - timedelta(days=1)
            start_date = end_date - timedelta(days=6)
            rows = await db.fetch_all(
                _SHOW_CHART_SQL,
                {
                    "start_date": start_date.isoformat(),
                    "end_date": end_date.isoformat(),
                },
            )
            if not rows:
                await callback.message.reply(
                    "No chart data available for the recent window.",
                    reply_markup=build_followup_keyboard(),
                )
                return
            try:
                chart_bytes = generate_spend_trend_chart(rows)
            except Exception as exc:  # noqa: BLE001
                logger.error("show_chart_failed", error=str(exc))
                await callback.message.reply(
                    "Chart generation failed.",
                    reply_markup=build_followup_keyboard(),
                )
                return
            photo = BufferedInputFile(chart_bytes, filename="trend.png")
            await bot.send_photo(
                chat_id=chat_id,
                photo=photo,
                caption=f"Spend trend: {start_date} to {end_date}",
                reply_markup=build_followup_keyboard(),
            )
            logger.info(
                "show_chart_sent",
                chat_id=chat_id, user_id=user_id, rows=len(rows),
            )
            return

        # D-15: drill_down / compare_week / why -> route through AI handler
        injected = _ACTION_TO_TEXT.get(action)
        if injected is None:
            logger.warning("unknown_chat_action", action=action)
            return

        logger.info(
            "chat_button_tapped",
            chat_id=chat_id, user_id=user_id, action=action,
        )
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
