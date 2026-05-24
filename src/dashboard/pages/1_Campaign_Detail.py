"""Campaign Detail drill-down page (DASH-07).

Navigation: set st.query_params["campaign"] = "<name>" on the Overview page,
then call st.switch_page("pages/1_Campaign_Detail.py"). This page reads the
query param, fetches per-day Meta + GA4 rows for that campaign, and renders
two Plotly charts plus a side-by-side attribution caption.

Standalone: no aiogram / no src.ai / no src.bot imports (Phase 6 D-19 rule).
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

st.set_page_config(
    page_title="Campaign Detail",
    layout="wide",
    initial_sidebar_state="expanded",
)

from src.dashboard import db                          # noqa: E402
from src.dashboard.settings import DashboardSettings  # noqa: E402

# Dark-theme palette — duplicated from app.py per D-19 standalone rule
COLOR_BG_PAPER = "#0f1117"
COLOR_BG_PLOT = "#1a1d27"
COLOR_FONT = "#e4e7ef"
COLOR_GRID = "#2a2e3a"
COLOR_SPEND = "rgba(99, 125, 255, 0.6)"
COLOR_DEPOSITS = "#34d399"
COLOR_META = "#60a5fa"
COLOR_GA4 = "#a78bfa"

# Auth gate — duplicated from app.py per D-19 (each page is its own script)
def _check_auth(password_required: str) -> bool:
    if not password_required:
        return True
    if st.session_state.get("authenticated"):
        return True
    st.title("Campaign Detail")
    st.caption("Sign in to continue.")
    with st.form("auth_form_detail"):
        pw = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in")
        if submitted:
            if pw == password_required:
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error("Incorrect password")
    return False


@st.cache_data(ttl=300, show_spinner=False)
def _cached_daily(db_path_str: str, campaign: str, start: str, end: str) -> list[dict[str, Any]]:
    from pathlib import Path
    return db.get_campaign_daily(Path(db_path_str), campaign, start, end)


settings = DashboardSettings()  # type: ignore[call-arg]

if not _check_auth(settings.dashboard_password):
    st.stop()

# Read campaign from URL query param — UNTRUSTED INPUT, never interpolated into SQL
campaign = st.query_params.get("campaign")
if isinstance(campaign, list):  # Streamlit returns list for repeated keys
    campaign = campaign[0] if campaign else None

st.page_link("app.py", label="← Back to Overview")

if not campaign:
    st.title("Campaign Detail")
    st.info("Select a campaign from the Overview page to see its drill-down.")
    st.stop()

st.header(campaign)  # header shows the campaign name (safe — st.header escapes)
st.caption(
    "Daily Meta + GA4 detail. "
    "Meta uses 7-day click attribution · GA4 uses last-click · Never blend these numbers."
)

# (charts wired in Task 2)
