"""Phase 1 Telegram command handlers: /start, /status, /help.

Handlers access DBClient via dispatcher.workflow_data['db'] — wired in setup.py.
"""
from __future__ import annotations

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
            "*Status*",
            f"Meta last sync: `{last.get('meta_ads') or 'never'}`",
            f"GA4 last sync: `{last.get('ga4') or 'never'}`",
            "",
            "*Row counts*",
            f"campaigns: `{counts.get('campaigns', 0)}`",
            f"ad_metrics: `{counts.get('ad_metrics', 0)}`",
            f"ga4_metrics: `{counts.get('ga4_metrics', 0)}`",
            f"bot_conversations: `{counts.get('bot_conversations', 0)}`",
        ]
        logger.info("cmd_status", chat_id=message.chat.id)
        await message.answer("\n".join(lines))

    @router.message(Command("help"))
    async def cmd_help(message: Message) -> None:
        logger.info("cmd_help", chat_id=message.chat.id)
        await message.answer(
            "*Available commands*\n"
            "/start — confirm bot is online\n"
            "/status — show last sync time and row counts\n"
            "/help — show this message\n"
            "_(more commands ship in Phase 2)_"
        )

    return router
