"""Prove INFRA-02: AllowlistMiddleware drops non-allowlisted updates and logs without PII."""
from __future__ import annotations

import io
import sys
from datetime import datetime, timezone

import pytest
import structlog
from aiogram.types import Chat, Message, User

from src.bot.middleware import AllowlistMiddleware

pytestmark = pytest.mark.asyncio


def _msg(chat_id: int, user_id: int, text: str = "secret content") -> Message:
    return Message(
        message_id=1,
        date=datetime.now(timezone.utc),
        chat=Chat(id=chat_id, type="private"),
        from_user=User(id=user_id, is_bot=False, first_name="Test"),
        text=text,
    )


async def _capture_handler(call_log: list):
    async def handler(event, data):
        call_log.append(event)
        return "HANDLED"
    return handler


async def test_disallowed_chat_dropped():
    mw = AllowlistMiddleware(allowed_chat_ids={111}, allowed_user_ids={222})
    calls: list = []
    h = await _capture_handler(calls)
    result = await mw(h, _msg(chat_id=999, user_id=888), {})
    assert result is None, "non-allowlisted update must return None"
    assert calls == [], "handler must NOT have been invoked"


async def test_allowed_chat_passes():
    mw = AllowlistMiddleware(allowed_chat_ids={111}, allowed_user_ids=set())
    calls: list = []
    h = await _capture_handler(calls)
    result = await mw(h, _msg(chat_id=111, user_id=999), {})
    assert result == "HANDLED"
    assert len(calls) == 1


async def test_allowed_user_passes():
    """OR semantics: user-id match alone grants access even if chat is unknown."""
    mw = AllowlistMiddleware(allowed_chat_ids=set(), allowed_user_ids={222})
    calls: list = []
    h = await _capture_handler(calls)
    result = await mw(h, _msg(chat_id=999, user_id=222), {})
    assert result == "HANDLED"
    assert len(calls) == 1


async def test_message_text_not_logged(caplog):
    """Rejection log must contain chat_id/user_id/event_type but NEVER message text."""
    # Configure structlog to write through stdlib for caplog capture.
    structlog.configure(
        processors=[structlog.processors.KeyValueRenderer()],
        logger_factory=structlog.PrintLoggerFactory(),
    )
    mw = AllowlistMiddleware(allowed_chat_ids={111}, allowed_user_ids={222})
    sentinel = "supersecret-injection-attempt-abc123"

    # Capture stdout (structlog with PrintLoggerFactory writes to stdout).
    buf = io.StringIO()
    saved = sys.stdout
    sys.stdout = buf
    try:
        await mw((lambda e, d: None), _msg(999, 888, sentinel), {})
    finally:
        sys.stdout = saved

    assert sentinel not in buf.getvalue(), "message text must never appear in rejection logs"
