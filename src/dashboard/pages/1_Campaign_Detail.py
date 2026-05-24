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

# --- Sidebar: date range picker (same pattern as Overview) -----------------
with st.sidebar:
    st.header("Filters")
    today = date.today()
    default_end = today - timedelta(days=1)
    default_start = default_end - timedelta(days=29)

    if "detail_date_range" not in st.session_state:
        st.session_state.detail_date_range = (default_start, default_end)

    dates = st.date_input(
        "Date range",
        value=st.session_state.detail_date_range,
        max_value=today,
        key="detail_date_range_picker",
    )
    if isinstance(dates, tuple) and len(dates) == 2:
        start_date, end_date = dates
        st.session_state.detail_date_range = (start_date, end_date)
    else:
        st.warning("Pick both a start and an end date.")
        st.stop()

db_path_str = str(settings.db_path)
rows = _cached_daily(db_path_str, campaign, start_date.isoformat(), end_date.isoformat())

if not rows:
    st.info(f"No data for `{campaign}` in {start_date.isoformat()}..{end_date.isoformat()}.")
    st.stop()

# --- Chart 1: daily spend / deposits / sessions ----------------------------
fig_trend = go.Figure()
fig_trend.add_trace(go.Bar(
    x=[r["date"] for r in rows],
    y=[r["spend"] for r in rows],
    name="Spend ($)",
    marker_color=COLOR_SPEND,
    yaxis="y",
))
fig_trend.add_trace(go.Scatter(
    x=[r["date"] for r in rows],
    y=[r["deposits"] for r in rows],
    name="Deposits",
    mode="lines+markers",
    line=dict(color=COLOR_DEPOSITS, width=2),
    marker=dict(size=8),
    yaxis="y2",
))
fig_trend.add_trace(go.Scatter(
    x=[r["date"] for r in rows],
    y=[r["sessions"] for r in rows],
    name="GA4 Sessions",
    mode="lines",
    line=dict(color=COLOR_GA4, width=2, dash="dot"),
    yaxis="y2",
))
fig_trend.update_layout(
    plot_bgcolor=COLOR_BG_PLOT,
    paper_bgcolor=COLOR_BG_PAPER,
    font=dict(color=COLOR_FONT),
    xaxis=dict(title="Date", gridcolor=COLOR_GRID),
    yaxis=dict(title="Spend ($)", gridcolor=COLOR_GRID, zeroline=False),
    yaxis2=dict(title="Deposits / Sessions", overlaying="y", side="right",
                gridcolor=COLOR_GRID, zeroline=False),
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    margin=dict(l=40, r=40, t=40, b=40),
    height=420,
)

# --- Chart 2: Meta deposits vs GA4 purchases per date (side-by-side bars) --
fig_attr = go.Figure()
fig_attr.add_trace(go.Bar(
    x=[r["date"] for r in rows],
    y=[r["meta_purchases"] for r in rows],
    name="Meta purchases (7d-click)",
    marker_color=COLOR_META,
))
fig_attr.add_trace(go.Bar(
    x=[r["date"] for r in rows],
    y=[r["ga4_purchases"] for r in rows],
    name="GA4 purchases (last-click)",
    marker_color=COLOR_GA4,
))
fig_attr.update_layout(
    barmode="group",
    plot_bgcolor=COLOR_BG_PLOT,
    paper_bgcolor=COLOR_BG_PAPER,
    font=dict(color=COLOR_FONT),
    xaxis=dict(title="Date", gridcolor=COLOR_GRID),
    yaxis=dict(title="Conversions", gridcolor=COLOR_GRID, zeroline=False),
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    margin=dict(l=40, r=40, t=40, b=40),
    height=380,
)

st.subheader("Daily trend")
st.plotly_chart(fig_trend, use_container_width=True)

st.subheader("Meta vs GA4 attribution (this campaign)")
st.plotly_chart(fig_attr, use_container_width=True)
st.caption("Never blend — Meta uses 7-day click; GA4 uses last-click.")
