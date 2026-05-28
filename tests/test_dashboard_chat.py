"""Unit tests for src/dashboard/chat.py (DASH-03)."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.dashboard.chat import (
    run_chat, build_system_prompt, BUDGET_EXHAUSTED_USER_MSG,
    _get_monthly_anthropic_cost, _log_anthropic_usage,
    _calculate_cost,
)
from src.dashboard.settings import DashboardSettings


def _make_db(tmp_path: Path) -> Path:
    db = tmp_path / "fixture.db"
    con = sqlite3.connect(str(db))
    con.executescript("""
        CREATE TABLE campaigns (id TEXT, source TEXT, name TEXT, status TEXT, created_at TEXT);
        CREATE TABLE ad_metrics (
            campaign_id TEXT, date TEXT, ad_set_id TEXT DEFAULT '',
            ad_id TEXT DEFAULT '', spend REAL, impressions INTEGER, clicks INTEGER,
            ctr REAL, cpc REAL, cpm REAL, roas REAL,
            meta_purchases_7dclick INTEGER, meta_cost_per_purchase REAL,
            reach INTEGER, frequency REAL,
            meta_form_submit_deposit INTEGER DEFAULT 0, fetched_at TEXT
        );
        CREATE TABLE ga4_metrics (
            campaign_utm TEXT, date TEXT, sessions INTEGER, users INTEGER,
            new_users INTEGER, bounce_rate REAL, avg_engagement_time REAL,
            ga4_purchases_lastclick INTEGER, fetched_at TEXT
        );
        CREATE TABLE ga4_landing_pages (
            landing_page TEXT, date TEXT, sessions INTEGER, total_users INTEGER,
            ga4_purchases_lastclick INTEGER, screen_page_views INTEGER,
            avg_engagement_time REAL, fetched_at TEXT
        );
        CREATE TABLE anthropic_usage_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_at TEXT NOT NULL DEFAULT (datetime('now')),
            model TEXT, input_tokens INTEGER, output_tokens INTEGER,
            cost_usd REAL, chat_id INTEGER, user_id INTEGER
        );
        INSERT INTO campaigns VALUES ('c1','meta_ads','Brand','ACTIVE','2026-05-01');
        INSERT INTO ad_metrics(campaign_id, date, spend, meta_form_submit_deposit)
          VALUES ('c1','2026-05-01',100.0,5);
        INSERT INTO ga4_metrics(campaign_utm, date, sessions, ga4_purchases_lastclick)
          VALUES ('Brand','2026-05-01',500,3);
    """)
    con.commit()
    con.close()
    return db


def test_budget_gate_returns_exhausted_msg(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    # Seed usage above budget
    con = sqlite3.connect(str(db))
    con.execute(
        "INSERT INTO anthropic_usage_log (model, input_tokens, output_tokens, cost_usd) "
        "VALUES ('claude-sonnet-4-6', 1000, 1000, 100.0)"
    )
    con.commit()
    con.close()
    s = DashboardSettings(anthropic_monthly_budget_usd=20.0)
    text, hist = run_chat("hi", [], str(db), "sk-fake", s)
    assert text == BUDGET_EXHAUSTED_USER_MSG


def test_missing_api_key_returns_friendly_msg(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    s = DashboardSettings(anthropic_monthly_budget_usd=20.0)
    text, hist = run_chat("hi", [], str(db), "", s)
    assert "ANTHROPIC_API_KEY" in text


def test_end_turn_path(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    s = DashboardSettings(anthropic_monthly_budget_usd=20.0)
    fake_text_block = SimpleNamespace(type="text", text="Hello world.")
    fake_response = SimpleNamespace(
        stop_reason="end_turn",
        content=[fake_text_block],
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
    )
    with patch("src.dashboard.chat.Anthropic") as mock_anthropic:
        client = MagicMock()
        client.messages.create.return_value = fake_response
        mock_anthropic.return_value = client
        text, hist = run_chat("hi", [], str(db), "sk-fake", s)
    assert text == "Hello world."
    # Final assistant turn appended
    assert hist[-1] == {"role": "assistant", "content": "Hello world."}


def test_tool_use_loop(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    s = DashboardSettings(anthropic_monthly_budget_usd=20.0)
    fake_tool_use = SimpleNamespace(
        type="tool_use", name="query_metrics", id="tu_1",
        input={"source": "meta", "start_date": "2026-05-01", "end_date": "2026-05-01"},
    )
    tool_use_resp = SimpleNamespace(
        stop_reason="tool_use", content=[fake_tool_use],
        usage=SimpleNamespace(input_tokens=20, output_tokens=10),
    )
    final_text_block = SimpleNamespace(type="text", text="Answer.")
    final_resp = SimpleNamespace(
        stop_reason="end_turn", content=[final_text_block],
        usage=SimpleNamespace(input_tokens=30, output_tokens=15),
    )
    with patch("src.dashboard.chat.Anthropic") as mock_anthropic:
        client = MagicMock()
        client.messages.create.side_effect = [tool_use_resp, final_resp]
        mock_anthropic.return_value = client
        text, hist = run_chat("show meta", [], str(db), "sk-fake", s)
    assert text == "Answer."
    # Sequence: user, assistant(tool_use), user(tool_result first), assistant(final)
    assert hist[0]["role"] == "user"
    assert hist[1]["role"] == "assistant"
    assert hist[2]["role"] == "user"
    assert isinstance(hist[2]["content"], list)
    assert hist[2]["content"][0]["type"] == "tool_result"
    assert hist[-1]["role"] == "assistant"


def test_max_iterations_limit(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    s = DashboardSettings(anthropic_monthly_budget_usd=20.0)
    fake_tu = SimpleNamespace(
        type="tool_use", name="query_metrics", id="tu_x",
        input={"source": "meta", "start_date": "2026-05-01", "end_date": "2026-05-01"},
    )
    tool_use_resp = SimpleNamespace(
        stop_reason="tool_use", content=[fake_tu],
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
    )
    with patch("src.dashboard.chat.Anthropic") as mock_anthropic:
        client = MagicMock()
        client.messages.create.return_value = tool_use_resp
        mock_anthropic.return_value = client
        text, hist = run_chat("hi", [], str(db), "sk-fake", s)
    assert "too many tool calls" in text


def test_usage_log_inserted_after_call(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    s = DashboardSettings(anthropic_monthly_budget_usd=20.0)
    fake_text_block = SimpleNamespace(type="text", text="ok")
    fake_response = SimpleNamespace(
        stop_reason="end_turn", content=[fake_text_block],
        usage=SimpleNamespace(input_tokens=100, output_tokens=50),
    )
    with patch("src.dashboard.chat.Anthropic") as mock_anthropic:
        client = MagicMock()
        client.messages.create.return_value = fake_response
        mock_anthropic.return_value = client
        run_chat("hi", [], str(db), "sk-fake", s)
    con = sqlite3.connect(str(db))
    row = con.execute("SELECT input_tokens, output_tokens, cost_usd FROM anthropic_usage_log").fetchone()
    con.close()
    assert row[0] == 100
    assert row[1] == 50
    assert row[2] > 0


def test_calculate_cost() -> None:
    # Sonnet: 1M input @ $3 + 1M output @ $15 = $18
    assert abs(_calculate_cost("claude-sonnet-4-6", 1_000_000, 1_000_000) - 18.0) < 0.001


def test_system_prompt_includes_required_phrases(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    prompt = build_system_prompt(str(db))
    assert "CPR (FSD)" in prompt
    assert "North Star" in prompt
    assert "<data>" in prompt
    assert "Never blend" in prompt
    assert "Brand" in prompt  # campaign list


def test_module_has_no_forbidden_imports() -> None:
    src = Path("src/dashboard/chat.py").read_text(encoding="utf-8")
    assert "import streamlit" not in src
    assert "from src.ai" not in src
    assert "from src.bot" not in src
    assert "import aiogram" not in src
    assert "import aiosqlite" not in src
    assert "AsyncAnthropic" not in src
    assert "asyncio" not in src
