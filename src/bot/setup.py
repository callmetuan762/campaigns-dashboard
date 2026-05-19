"""Factory: build the Bot + Dispatcher with allowlist + handlers wired in correct order.

Critical ordering (per CLAUDE.md security non-negotiable + RESEARCH Pitfall 1):
    1. Build Bot + Dispatcher
    2. Inject DBClient into dispatcher.workflow_data (so handlers receive it)
    3. Register AllowlistMiddleware on dp.message.middleware AND dp.callback_query.middleware
    4. THEN include the handler router

Caller (src/main.py — Plan 04) must NOT register additional routers before this returns.
"""
from __future__ import annotations

import structlog
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from src.bot.handlers import build_router
from src.bot.middleware import AllowlistMiddleware
from src.config import Settings
from src.db.client import DBClient

logger = structlog.get_logger(__name__)


def create_bot_and_dispatcher(
    settings: Settings,
    db_client: DBClient,
) -> tuple[Bot, Dispatcher]:
    """Return a (Bot, Dispatcher) pair with allowlist and handlers wired.

    The caller is responsible for: bot.delete_webhook(drop_pending_updates=True)
    BEFORE dp.start_polling(bot), and for graceful shutdown via bot.session.close().
    """
    bot = Bot(
        token=settings.telegram_bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()

    # Inject DBClient so handlers can declare `db: DBClient` as a parameter.
    dp["db"] = db_client

    # STEP 1: Register allowlist BEFORE any router. Order is security-critical.
    allowlist = AllowlistMiddleware(
        allowed_chat_ids=set(settings.telegram_allowed_chat_ids),
        allowed_user_ids=set(settings.telegram_allowed_user_ids),
    )
    dp.message.middleware(allowlist)
    dp.callback_query.middleware(allowlist)

    # STEP 2: Include handler router AFTER middleware registration.
    dp.include_router(build_router())

    logger.info(
        "bot_dispatcher_ready",
        allowed_chats=len(settings.telegram_allowed_chat_ids),
        allowed_users=len(settings.telegram_allowed_user_ids),
    )
    return bot, dp
