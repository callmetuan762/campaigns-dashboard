"""Unit tests for src/dashboard/settings.py (DASH-01, DASH-05).

Verifies DashboardSettings env handling:
- Defaults when env is empty
- Extra TELEGRAM_* / META_* fields silently ignored (extra="ignore")
- DASHBOARD_PASSWORD loaded correctly
- Budget cast to float
- No import of src.config (DASH-05 isolation)
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest


def _reload_settings_module():
    """Re-import DashboardSettings after env mutation."""
    import importlib

    from src.dashboard import settings as s_mod
    importlib.reload(s_mod)
    return s_mod.DashboardSettings


def test_defaults_when_env_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in ("DASHBOARD_PASSWORD", "ANTHROPIC_API_KEY", "DB_PATH",
              "ANTHROPIC_MONTHLY_BUDGET_USD"):
        monkeypatch.delenv(k, raising=False)
    # Disable .env loading so monkeypatched env is the only source
    monkeypatch.setenv("PYDANTIC_SETTINGS_NO_DOTENV", "1")
    Settings = _reload_settings_module()
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.dashboard_password == ""
    assert s.anthropic_api_key == ""
    assert s.anthropic_monthly_budget_usd == 20.0


def test_ignores_telegram_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pitfall 6: TELEGRAM_* env must NOT cause DashboardSettings to raise."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake_token")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "123,456")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "789")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "")
    Settings = _reload_settings_module()
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert not hasattr(s, "telegram_bot_token")
    assert not hasattr(s, "telegram_allowed_chat_ids")


def test_dashboard_password_loaded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_PASSWORD", "hunter2")
    Settings = _reload_settings_module()
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.dashboard_password == "hunter2"


def test_budget_cast_to_float(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_MONTHLY_BUDGET_USD", "99.5")
    Settings = _reload_settings_module()
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.anthropic_monthly_budget_usd == 99.5


def test_settings_does_not_import_src_config() -> None:
    """DASH-05: DashboardSettings must be standalone — no import of src.config."""
    tree = ast.parse(Path("src/dashboard/settings.py").read_text(encoding="utf-8"))
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module)
    assert "src.config" not in imports
    assert not any(m.startswith("src.bot") for m in imports)
    assert not any(m.startswith("src.ai") for m in imports)


# --- cpd_target field (DASH-06) -------------------------------------------

def test_cpd_target_default_is_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test 1: DashboardSettings() with no env loads cpd_target == 0.0."""
    monkeypatch.delenv("CPD_TARGET", raising=False)
    Settings = _reload_settings_module()
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.cpd_target == 0.0


def test_cpd_target_set_directly(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test 2: DashboardSettings(cpd_target=25.0) sets the value."""
    monkeypatch.delenv("CPD_TARGET", raising=False)
    Settings = _reload_settings_module()
    s = Settings(_env_file=None, cpd_target=25.0)  # type: ignore[call-arg]
    assert s.cpd_target == 25.0


def test_cpd_target_loaded_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test 3: CPD_TARGET=12.5 in monkeypatched env loads cpd_target == 12.5."""
    monkeypatch.setenv("CPD_TARGET", "12.5")
    Settings = _reload_settings_module()
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.cpd_target == pytest.approx(12.5)
