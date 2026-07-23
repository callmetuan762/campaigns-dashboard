"""DASH-01: streamlit app boots without import error.

Uses streamlit.testing.v1.AppTest when available; otherwise falls back to a
pure-import smoke check (importing Overview.py would call st.set_page_config which
errors outside an AppTest context, so the fallback uses ast.parse only).
"""
from __future__ import annotations

import ast
import os
import sqlite3
from pathlib import Path

import pytest

from tests.conftest import build_migrated_db


def _seed_db(path: Path) -> None:
    build_migrated_db(path)
    con = sqlite3.connect(str(path))
    con.executescript("""
        INSERT INTO campaigns VALUES ('c1','meta_ads','Brand','ACTIVE','2026-05-01',NULL);
        INSERT INTO ad_metrics(campaign_id, date, spend, impressions, clicks, roas,
                               meta_form_submit_deposit, fetched_at)
          VALUES ('c1','2026-05-01',100.0,1000,50,2.5,5,'2026-05-02T00:00:00');
        INSERT INTO ga4_metrics(campaign_utm, date, sessions, users,
                                ga4_purchases_lastclick, fetched_at)
          VALUES ('Brand','2026-05-01',500,400,3,'2026-05-02T00:00:00');
    """)
    con.commit()
    con.close()


def test_app_file_parses() -> None:
    src = Path("src/dashboard/Overview.py").read_text(encoding="utf-8")
    ast.parse(src)  # raises SyntaxError on failure


def test_app_first_streamlit_call_is_set_page_config() -> None:
    src = Path("src/dashboard/Overview.py").read_text(encoding="utf-8")
    # The first `st.` call in executable code (not docstrings/comments) must be
    # `st.set_page_config`. We use tokenize to skip string literals and comments.
    import tokenize
    import io
    first_st_call: str | None = None
    tokens = list(tokenize.generate_tokens(io.StringIO(src).readline))
    for i, tok in enumerate(tokens):
        if tok.type == tokenize.NAME and tok.string == "st":
            # Check next token is OP "."
            if i + 1 < len(tokens) and tokens[i + 1].string == ".":
                # Check token after "." is a NAME
                if i + 2 < len(tokens) and tokens[i + 2].type == tokenize.NAME:
                    first_st_call = f"st.{tokens[i + 2].string}"
                    break
    assert first_st_call == "st.set_page_config", (
        f"First st.* call in executable code must be st.set_page_config, got: {first_st_call!r}"
    )


def test_app_boots_with_apptest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """If streamlit.testing.v1.AppTest is available, boot the app and assert no exception."""
    try:
        from streamlit.testing.v1 import AppTest
    except ImportError:
        pytest.skip("streamlit.testing.v1.AppTest unavailable")

    db = tmp_path / "metrics.db"
    _seed_db(db)
    monkeypatch.setenv("DB_PATH", str(db))
    monkeypatch.setenv("DASHBOARD_PASSWORD", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")

    at = AppTest.from_file("src/dashboard/Overview.py", default_timeout=30)
    at.run()
    assert not at.exception, f"app raised: {[str(e) for e in at.exception]}"
    # At least the title should render
    titles = [t.value for t in at.title]
    assert any("Ads Performance" in t for t in titles)


def test_settings_ignores_telegram(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pitfall 6 regression: DashboardSettings must not blow up on TELEGRAM_* env."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "123,456")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "789")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "")
    from src.dashboard.settings import DashboardSettings
    # Reading should succeed; telegram fields are dropped via extra="ignore"
    s = DashboardSettings()
    assert not hasattr(s, "telegram_bot_token")
    assert not hasattr(s, "telegram_allowed_chat_ids")
