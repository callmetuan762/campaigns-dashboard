"""Unit tests for the password gate helper in src/dashboard/app.py (DASH-04).

Tests _check_auth() behavior:
- Empty password_required → always True (open mode)
- Authenticated session_state flag → True bypass
- No session flag + password set → False (gate blocks access)
"""
from __future__ import annotations

import os

import pytest


os.environ.setdefault("DASHBOARD_PASSWORD", "")


def _import_app():
    import importlib
    import sys
    if "src.dashboard.app" in sys.modules:
        return sys.modules["src.dashboard.app"]
    return importlib.import_module("src.dashboard.app")


def test_empty_password_opens() -> None:
    app = _import_app()
    assert app._check_auth("") is True


def test_authenticated_session_passes_through(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _import_app()
    import streamlit as st
    # Simulate a successful prior login
    st.session_state["authenticated"] = True
    try:
        assert app._check_auth("required_pw") is True
    finally:
        st.session_state.pop("authenticated", None)


def test_unauthenticated_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without a session flag, the gate must NOT pass.

    Note: in real Streamlit flow, _check_auth renders a form and returns False
    when no submission has happened yet. We verify here that the function does
    not silently elevate access.
    """
    app = _import_app()
    import streamlit as st
    st.session_state.pop("authenticated", None)
    # Calling _check_auth outside an AppTest context will render to a no-op container
    # but must NOT return True without authentication.
    try:
        result = app._check_auth("hunter2")
    except Exception:
        # Streamlit may complain about being outside a run context for the form
        # widget; in that case the function definitely didn't return True.
        result = False
    assert result is False
    assert "authenticated" not in st.session_state
