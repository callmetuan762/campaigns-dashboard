"""Unit tests for run_chat_3agent in src/dashboard/chat.py (DASH-09, D-18, D-20)."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.dashboard.chat import BUDGET_EXHAUSTED_USER_MSG
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


# ---------------------------------------------------------------------------
# Test 1: run_chat_3agent returns (str, list[dict]) tuple
# ---------------------------------------------------------------------------
def test_run_chat_3agent_return_shape(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    from src.dashboard.chat import run_chat_3agent
    settings = DashboardSettings(anthropic_monthly_budget_usd=20.0)

    with patch("src.dashboard.agents.Orchestrator.run", return_value=("Answer.", 0.01)):
        text, history = run_chat_3agent("hello", [], str(db), "sk-fake", settings)

    assert isinstance(text, str)
    assert isinstance(history, list)
    assert all(isinstance(m, dict) for m in history)


# ---------------------------------------------------------------------------
# Test 2: budget exhausted returns BUDGET_EXHAUSTED_USER_MSG with correct history shape
# ---------------------------------------------------------------------------
def test_run_chat_3agent_budget_exhausted(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    from src.dashboard.chat import run_chat_3agent
    from src.dashboard.agents import BudgetExhaustedError
    settings = DashboardSettings(anthropic_monthly_budget_usd=20.0)

    with patch("src.dashboard.agents.Orchestrator.run", side_effect=BudgetExhaustedError(BUDGET_EXHAUSTED_USER_MSG)):
        text, history = run_chat_3agent("hello", [], str(db), "sk-fake", settings)

    assert text == BUDGET_EXHAUSTED_USER_MSG
    assert len(history) == 2
    assert history[0] == {"role": "user", "content": "hello"}
    assert history[1] == {"role": "assistant", "content": BUDGET_EXHAUSTED_USER_MSG}


# ---------------------------------------------------------------------------
# Test 3: empty api_key returns not-configured message
# ---------------------------------------------------------------------------
def test_run_chat_3agent_missing_api_key(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    from src.dashboard.chat import run_chat_3agent
    settings = DashboardSettings(anthropic_monthly_budget_usd=20.0)

    text, history = run_chat_3agent("hello", [], str(db), "", settings)

    assert "ANTHROPIC_API_KEY" in text
    assert len(history) == 2
    assert history[0]["role"] == "user"
    assert history[1]["role"] == "assistant"


# ---------------------------------------------------------------------------
# Test 4: returned history contains only user + final assistant turn (D-20)
# ---------------------------------------------------------------------------
def test_run_chat_3agent_history_is_clean(tmp_path: Path) -> None:
    """D-20: agent-internal tool traces must NOT be persisted into history."""
    db = _make_db(tmp_path)
    from src.dashboard.chat import run_chat_3agent
    settings = DashboardSettings(anthropic_monthly_budget_usd=20.0)

    prior_history = [
        {"role": "user", "content": "prior question"},
        {"role": "assistant", "content": "prior answer"},
    ]

    with patch("src.dashboard.agents.Orchestrator.run", return_value=("New answer.", 0.02)):
        text, history = run_chat_3agent("new question", prior_history, str(db), "sk-fake", settings)

    # Should be prior_history + user + final assistant only (no tool_result internals)
    assert len(history) == len(prior_history) + 2
    assert history[-2] == {"role": "user", "content": "new question"}
    assert history[-1] == {"role": "assistant", "content": "New answer."}
    # All history items must be plain string content (no list-of-blocks)
    for msg in history:
        assert isinstance(msg["content"], str), f"Non-string content in history: {msg}"


# ---------------------------------------------------------------------------
# Test 5: run_chat() is still importable and has its original signature
# ---------------------------------------------------------------------------
def test_run_chat_unchanged_signature() -> None:
    """run_chat must still exist with its original 5 parameters."""
    import inspect
    from src.dashboard.chat import run_chat, run_chat_3agent

    sig_rc = inspect.signature(run_chat)
    sig_r3a = inspect.signature(run_chat_3agent)

    params_rc = list(sig_rc.parameters.keys())
    params_r3a = list(sig_r3a.parameters.keys())

    assert params_rc == ["user_text", "history", "db_path", "api_key", "settings"]
    assert params_r3a == params_rc, f"Signature mismatch: {params_rc} vs {params_r3a}"
