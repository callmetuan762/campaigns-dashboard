"""Tests for Phase 4 bot handlers: /clear scoping + /help text (Phase 4 Plan 04-06).

Covers: per-user conversation scoping, Phase 4 help text.
Requirement IDs: CHAT-03, D-06, D-09
"""
from __future__ import annotations

import inspect

import pytest

from src.bot.handlers import build_router


def test_help_text_contains_clear():
    """CHAT-03 D-09: /help lists /clear and reflects Phase 4 conversational AI."""
    src = inspect.getsource(build_router)
    assert "/clear" in src
    assert "Phase 4" in src


@pytest.mark.asyncio
async def test_clear_scoped(db_client):
    """CHAT-03 D-06: /clear scopes to (chat_id, user_id) — other users unaffected."""
    # Alice saves two turns
    await db_client.save_conversation_turn(100, 1, "user", "alice msg 1")
    await db_client.save_conversation_turn(100, 1, "assistant", "alice ans")
    # Bob saves one turn in the same chat
    await db_client.save_conversation_turn(100, 2, "user", "bob msg")

    # Clear only Alice's history
    await db_client.clear_conversation(100, 1)

    alice = await db_client.get_conversation_history(100, 1)
    bob = await db_client.get_conversation_history(100, 2)

    assert alice == [], f"Alice's history should be empty, got: {alice}"
    assert len(bob) == 1, f"Bob should have 1 turn, got: {len(bob)}"
    assert bob[0]["content"] == "bob msg"
