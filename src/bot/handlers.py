"""Phase 1 Telegram command handlers: /start, /status, /help.

Handlers access DBClient via dispatcher.workflow_data['db'] — wired in setup.py.
"""
from __future__ import annotations

import html
import structlog
from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from src.db.client import DBClient

logger = structlog.get_logger(__name__)


def build_router() -> Router:
    router = Router(name="phase1_commands")

    @router.message(CommandStart())
    async def cmd_start(message: Message) -> None:
        logger.info("cmd_start", chat_id=message.chat.id)
        await message.answer("Ads Reporting Agent online. Use /report for latest data.")

    @router.message(Command("status"))
    async def cmd_status(message: Message, db: DBClient) -> None:
        last = await db.get_last_sync()
        counts = await db.get_row_counts()
        lines = [
            "<b>Status</b>",
            f"Meta last sync: <code>{html.escape(str(last.get('meta_ads') or 'never'))}</code>",
            f"GA4 last sync: <code>{html.escape(str(last.get('ga4') or 'never'))}</code>",
            "",
            "<b>Row counts</b>",
            f"campaigns: <code>{counts.get('campaigns', 0)}</code>",
            f"ad_metrics: <code>{counts.get('ad_metrics', 0)}</code>",
            f"ga4_metrics: <code>{counts.get('ga4_metrics', 0)}</code>",
            f"bot_conversations: <code>{counts.get('bot_conversations', 0)}</code>",
        ]
        logger.info("cmd_status", chat_id=message.chat.id)
        await message.answer("\n".join(lines))

    @router.message(Command("help"))
    async def cmd_help(message: Message) -> None:
        logger.info("cmd_help", chat_id=message.chat.id)
        await message.answer(
            "<b>Available commands</b>\n"
            "/start — confirm bot is online\n"
            "/status — show last sync time and row counts\n"
            "/help — show this message\n"
            "<i>(more commands ship in Phase 2)</i>"
        )

    return router
