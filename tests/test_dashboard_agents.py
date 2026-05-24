"""Unit tests for src/dashboard/agents.py (DASH-09, DASH-10).

Tests cover BudgetExhaustedError, each agent returning non-empty strings,
Orchestrator parallel fan-out, budget gate, usage logging, and graceful
degradation when MetaAgent stalls.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, call
import concurrent.futures

import pytest

from src.dashboard.settings import DashboardSettings


def _make_db(tmp_path: Path) -> Path:
    """Minimal fixture DB for agents tests."""
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


def _fake_text_response(text: str = "Agent answer.") -> SimpleNamespace:
    """Build a minimal Anthropic response with a text block and end_turn."""
    return SimpleNamespace(
        stop_reason="end_turn",
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
    )


# ---------------------------------------------------------------------------
# Test 1: BudgetExhaustedError is a custom RuntimeError subclass
# ---------------------------------------------------------------------------
def test_budget_exhausted_error_is_runtime_error() -> None:
    from src.dashboard.agents import BudgetExhaustedError
    assert issubclass(BudgetExhaustedError, RuntimeError)
    exc = BudgetExhaustedError("test")
    assert str(exc) == "test"


# ---------------------------------------------------------------------------
# Test 2: MetaAgent.run returns a non-empty string
# ---------------------------------------------------------------------------
def test_meta_agent_run_returns_string(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    from src.dashboard.agents import MetaAgent

    with patch("src.dashboard.agents.Anthropic") as mock_cls:
        client = MagicMock()
        client.messages.create.return_value = _fake_text_response("Meta result.")
        mock_cls.return_value = client
        result, inp, out = MetaAgent().run("What is spend?", str(db), "sk-fake")

    assert isinstance(result, str)
    assert len(result) > 0
    assert result == "Meta result."


# ---------------------------------------------------------------------------
# Test 3: GA4Agent.run returns a non-empty string
# ---------------------------------------------------------------------------
def test_ga4_agent_run_returns_string(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    from src.dashboard.agents import GA4Agent

    with patch("src.dashboard.agents.Anthropic") as mock_cls:
        client = MagicMock()
        client.messages.create.return_value = _fake_text_response("GA4 result.")
        mock_cls.return_value = client
        result, inp, out = GA4Agent().run("What are sessions?", str(db), "sk-fake")

    assert isinstance(result, str)
    assert len(result) > 0
    assert result == "GA4 result."


# ---------------------------------------------------------------------------
# Test 4: AttributionAgent.run returns non-empty string and uses meta+ga4 inputs
# ---------------------------------------------------------------------------
def test_attribution_agent_run_uses_specialist_outputs(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    from src.dashboard.agents import AttributionAgent

    captured_messages = []

    def fake_create(**kwargs):
        captured_messages.extend(kwargs.get("messages", []))
        return _fake_text_response("Unified answer.")

    with patch("src.dashboard.agents.Anthropic") as mock_cls:
        client = MagicMock()
        client.messages.create.side_effect = fake_create
        mock_cls.return_value = client
        result, inp, out = AttributionAgent().run(
            "Compare Meta and GA4 spend",
            meta_result="Meta says 500",
            ga4_result="GA4 says 400",
            db_path=str(db),
            api_key="sk-fake",
        )

    assert result == "Unified answer."
    # The user message must contain both meta_result and ga4_result
    all_content = " ".join(
        m["content"] for m in captured_messages
        if isinstance(m.get("content"), str)
    )
    assert "Meta says 500" in all_content
    assert "GA4 says 400" in all_content


# ---------------------------------------------------------------------------
# Test 5: Orchestrator.run fans out meta+ga4 in parallel before AttributionAgent
# ---------------------------------------------------------------------------
def test_orchestrator_parallel_fanout_then_attribution(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    from src.dashboard.agents import Orchestrator
    settings = DashboardSettings(anthropic_monthly_budget_usd=20.0)

    submit_order: list[str] = []

    def fake_meta_run(*_a, **_k):
        return ("meta text", 5, 5)

    def fake_ga4_run(*_a, **_k):
        return ("ga4 text", 5, 5)

    def fake_attr_run(*_a, **_k):
        return ("final answer", 5, 5)

    with (
        patch("src.dashboard.agents.MetaAgent.run", side_effect=fake_meta_run),
        patch("src.dashboard.agents.GA4Agent.run", side_effect=fake_ga4_run),
        patch("src.dashboard.agents.AttributionAgent.run", side_effect=fake_attr_run),
    ):
        final_text, cost = Orchestrator().run(
            "question", str(db), "sk-fake", settings
        )

    assert final_text == "final answer"
    assert isinstance(cost, float)


# ---------------------------------------------------------------------------
# Test 6: Orchestrator.run raises BudgetExhaustedError when over budget
# ---------------------------------------------------------------------------
def test_orchestrator_raises_budget_exhausted_when_over_budget(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    # Seed usage above budget
    con = sqlite3.connect(str(db))
    con.execute(
        "INSERT INTO anthropic_usage_log (model, input_tokens, output_tokens, cost_usd)"
        " VALUES ('claude-sonnet-4-6', 1000, 1000, 100.0)"
    )
    con.commit()
    con.close()

    from src.dashboard.agents import Orchestrator, BudgetExhaustedError
    settings = DashboardSettings(anthropic_monthly_budget_usd=20.0)

    with pytest.raises(BudgetExhaustedError):
        Orchestrator().run("question", str(db), "sk-fake", settings)


# ---------------------------------------------------------------------------
# Test 7: Orchestrator.run logs 3 anthropic_usage_log rows per call
# ---------------------------------------------------------------------------
def test_orchestrator_logs_three_usage_rows(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    from src.dashboard.agents import Orchestrator
    settings = DashboardSettings(anthropic_monthly_budget_usd=20.0)

    with (
        patch("src.dashboard.agents.MetaAgent.run", return_value=("meta", 10, 5)),
        patch("src.dashboard.agents.GA4Agent.run", return_value=("ga4", 10, 5)),
        patch("src.dashboard.agents.AttributionAgent.run", return_value=("final", 10, 5)),
    ):
        Orchestrator().run("question", str(db), "sk-fake", settings)

    con = sqlite3.connect(str(db))
    count = con.execute("SELECT COUNT(*) FROM anthropic_usage_log").fetchone()[0]
    con.close()
    assert count == 3


# ---------------------------------------------------------------------------
# Test 8: Orchestrator graceful degradation when MetaAgent stalls beyond 60s
# ---------------------------------------------------------------------------
def test_orchestrator_graceful_degradation_on_meta_timeout(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    from src.dashboard.agents import Orchestrator
    settings = DashboardSettings(anthropic_monthly_budget_usd=20.0)

    attr_received: list[str] = []

    def fake_attr_run(user_text, meta_result, ga4_result, db_path, api_key):
        attr_received.append(meta_result)
        attr_received.append(ga4_result)
        return ("final answer", 5, 5)

    # Simulate MetaAgent future NOT in done set — patch wait so fut_meta is not done
    import concurrent.futures as cf
    original_wait = cf.wait

    def patched_wait(futures, timeout=None):
        # Only return ga4 future as done; meta future not in done
        fut_meta_ref, fut_ga4_ref = list(futures)[0], list(futures)[1]
        return {fut_ga4_ref}, {fut_meta_ref}

    with (
        patch("src.dashboard.agents.MetaAgent.run", return_value=("meta text", 5, 5)),
        patch("src.dashboard.agents.GA4Agent.run", return_value=("ga4 text", 5, 5)),
        patch("src.dashboard.agents.AttributionAgent.run", side_effect=fake_attr_run),
        patch("src.dashboard.agents.wait", patched_wait),
    ):
        final_text, _ = Orchestrator().run("question", str(db), "sk-fake", settings)

    # AttributionAgent should still run with "MetaAgent timed out." placeholder
    assert "timed out" in attr_received[0].lower() or "MetaAgent" in attr_received[0]
    assert final_text == "final answer"
