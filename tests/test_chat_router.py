"""Tests for Phase 4 Telegram inline-keyboard + router (src/bot/chat_router.py).

Covers: keyboard shape, callback data 64-byte limit, action-to-text map,
router name and observer registration.

Requirement IDs: CHAT-01, CHAT-07, D-14, D-15
"""
from __future__ import annotations

import pytest

from src.bot.chat_router import (
    ChatAction,
    _ACTION_TO_TEXT,
    build_chat_router,
    build_followup_keyboard,
)


def test_build_followup_keyboard_shape():
    """CHAT-07 D-14: 4 buttons, 2 rows, exact labels in required order."""
    kb = build_followup_keyboard()
    rows = kb.inline_keyboard
    assert len(rows) == 2, f"Expected 2 rows, got {len(rows)}"
    assert len(rows[0]) == 2, f"Expected 2 buttons in row 0, got {len(rows[0])}"
    assert len(rows[1]) == 2, f"Expected 2 buttons in row 1, got {len(rows[1])}"
    texts = [b.text for row in rows for b in row]
    assert texts == [
        "Drill down",
        "Compare to last week",
        "Why is this happening?",
        "Show spend chart",
    ], f"Button labels mismatch: {texts}"


def test_chat_action_callback_data_under_64_bytes():
    """CHAT-07: Telegram 64-byte callback_data limit honored for all 4 actions."""
    for action in ("drill_down", "compare_week", "why", "show_chart"):
        packed = ChatAction(action=action).pack()
        byte_len = len(packed.encode("utf-8"))
        assert byte_len <= 64, (
            f"callback_data for '{action}' is {byte_len} bytes (limit 64)"
        )
        # Round-trip verify
        unpacked = ChatAction.unpack(packed)
        assert unpacked.action == action, (
            f"Unpack mismatch: expected '{action}', got '{unpacked.action}'"
        )


def test_action_to_text_map():
    """CHAT-07 D-15: 3 non-chart actions have canned injected text."""
    assert set(_ACTION_TO_TEXT.keys()) == {"drill_down", "compare_week", "why"}, (
        f"Keys mismatch: {set(_ACTION_TO_TEXT.keys())}"
    )
    for action, text in _ACTION_TO_TEXT.items():
        assert isinstance(text, str) and len(text) > 0, (
            f"Action '{action}' has empty/non-string text: {text!r}"
        )


def test_build_chat_router_has_handlers():
    """CHAT-01 / CHAT-07: Router named 'phase4_chat' with message + callback_query observers."""
    r = build_chat_router()
    assert r.name == "phase4_chat", f"Router name should be 'phase4_chat', got '{r.name}'"
    obs = r.observers
    assert "message" in obs, f"Router missing 'message' observer. Keys: {list(obs.keys())}"
    assert "callback_query" in obs, (
        f"Router missing 'callback_query' observer. Keys: {list(obs.keys())}"
    )
