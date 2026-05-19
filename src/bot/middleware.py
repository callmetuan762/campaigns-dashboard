"""Telegram update allowlist middleware.

INFRA-02 / CLAUDE.md Security Non-Negotiable #1:
    The Telegram bot enforces a strict allowlist of permitted chat IDs AND user IDs
    BEFORE executing any command or Claude call. Non-allowlisted updates are
    silently dropped (never replied to — replying confirms the bot's existence
    to drive-by probers, per RESEARCH.md PITFALLS.md).

Semantics: OR — an update is allowed if chat_id is in the chat allowlist OR
user_id is in the user allowlist. This lets the team group be the trust boundary
(every member of the group is implicitly trusted) while still permitting solo
DMs from specifically allowlisted users.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

logger = structlog.get_logger(__name__)


class AllowlistMiddleware(BaseMiddleware):
    """Drop Telegram updates whose chat AND user are both outside the allowlist."""

    def __init__(
        self,
        allowed_chat_ids: set[int],
        allowed_user_ids: set[int],
    ) -> None:
        self._chats = set(allowed_chat_ids)
        self._users = set(allowed_user_ids)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        chat_id, user_id = self._extract_ids(event)

        if (chat_id is not None and chat_id in self._chats) or (
            user_id is not None and user_id in self._users
        ):
            return await handler(event, data)

        # Silent drop. Do NOT reply. Do NOT echo message text into the log.
        logger.info(
            "rejected_update",
            chat_id=chat_id,
            user_id=user_id,
            event_type=type(event).__name__,
        )
        return None

    @staticmethod
    def _extract_ids(event: TelegramObject) -> tuple[int | None, int | None]:
        if isinstance(event, Message):
            return (
                event.chat.id,
                event.from_user.id if event.from_user else None,
            )
        if isinstance(event, CallbackQuery):
            return (
                event.message.chat.id if event.message else None,
                event.from_user.id if event.from_user else None,
            )
        return None, None
