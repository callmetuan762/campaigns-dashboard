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
from src.bot.chat_router import build_chat_router
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
    # Phase 4: chat_router handlers declare `settings: Settings` as a parameter.
    dp["settings"] = settings

    # STEP 1: Register allowlist BEFORE any router. Order is security-critical.
    allowlist = AllowlistMiddleware(
        allowed_chat_ids=set(settings.telegram_allowed_chat_ids),
        allowed_user_ids=set(settings.telegram_allowed_user_ids),
    )
    dp.message.middleware(allowlist)
    dp.callback_query.middleware(allowlist)

    # STEP 2: Include routers in priority order — command router FIRST,
    # catch-all SECOND. If chat_router were included before build_router(),
    # the F.text catch-all would intercept /commands before the Command()
    # filters see them (RESEARCH Pitfall 4).
    dp.include_router(build_router())          # commands: /start /status /help /report /clear
    dp.include_router(build_chat_router())     # catch-all: non-command text + inline buttons (Phase 4)

    logger.info(
        "bot_dispatcher_ready",
        phase=4,
        allowed_chats=len(settings.telegram_allowed_chat_ids),
        allowed_users=len(settings.telegram_allowed_user_ids),
    )
    return bot, dp
