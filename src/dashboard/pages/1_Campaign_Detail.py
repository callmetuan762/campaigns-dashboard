"""Campaign Detail drill-down page (DASH-07).

Navigation: set st.query_params["campaign"] = "<name>" on the Overview page,
then call st.switch_page("pages/1_Campaign_Detail.py"). This page reads the
query param, fetches per-day Meta + GA4 rows for that campaign, and renders
KPI cards, trend charts, GA4 engagement, and an ad-set breakdown table.

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

# Dark-theme palette -- duplicated from app.py per D-19 standalone rule
COLOR_BG_PAPER = "#0f1117"
COLOR_BG_PLOT = "#1a1d27"
COLOR_FONT = "#e4e7ef"
COLOR_GRID = "#2a2e3a"
COLOR_SPEND = "rgba(99, 125, 255, 0.6)"
COLOR_DEPOSITS = "#34d399"
COLOR_META = "#60a5fa"
COLOR_GA4 = "#a78bfa"
COLOR_CPD = "#f59e0b"


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


@st.cache_data(ttl=300, show_spinner=False)
def _cached_ga4_engagement(db_path_str: str, campaign: str, start: str, end: str) -> dict[str, Any]:
    from pathlib import Path
    return db.get_campaign_ga4_engagement(Path(db_path_str), campaign, start, end)


@st.cache_data(ttl=300, show_spinner=False)
def _cached_adset_breakdown(db_path_str: str, campaign: str, start: str, end: str) -> list[dict[str, Any]]:
    from pathlib import Path
    return db.get_campaign_adset_breakdown(Path(db_path_str), campaign, start, end)


@st.cache_data(ttl=300, show_spinner=False)
def _cached_learning_status(db_path_str: str, end: str) -> dict[str, bool]:
    """Return {ad_set_id: True} for ad sets in Meta learning phase (<50 FSDs/week)."""
    from pathlib import Path
    return db.get_adset_learning_status(Path(db_path_str), end)


settings = DashboardSettings()  # type: ignore[call-arg]

if not _check_auth(settings.dashboard_password):
    st.stop()

campaign = st.query_params.get("campaign")
if isinstance(campaign, list):
    campaign = campaign[0] if campaign else None

st.page_link("Overview.py", label="<- Back to Overview")

if not campaign:
    st.title("Campaign Detail")
    st.info("Select a campaign from the Overview page to see its drill-down.")
    st.stop()

st.header(campaign)
st.caption(
    "Daily Meta + GA4 detail. "
    "Meta uses 7-day click attribution · GA4 uses last-click · Never blend these numbers."
)

# --- Sidebar ----------------------------------------------------------------
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

    st.divider()
    conv_metric = st.radio(
        "Conversion metric",
        options=["FSD (form_submit)", "Purchases (7d-click)"],
        index=0,
        key="detail_conv_metric",
    )
    use_form_submit: bool = conv_metric == "FSD (form_submit)"

db_path_str = str(settings.db_path)
rows = _cached_daily(db_path_str, campaign, start_date.isoformat(), end_date.isoformat())

if not rows:
    st.info(f"No data for `{campaign}` in {start_date.isoformat()}..{end_date.isoformat()}.")
    st.stop()

# --- KPI summary strip ------------------------------------------------------
total_spend = sum(r["spend"] for r in rows)
total_deposits = sum(r["deposits"] for r in rows)
cpd = total_spend / total_deposits if total_deposits > 0 else None
weighted_roas_num = sum(r["spend"] * r["roas"] for r in rows)
avg_roas = weighted_roas_num / total_spend if total_spend > 0 else 0.0
total_sessions = sum(r["sessions"] for r in rows)
total_meta_purchases = sum(r["meta_purchases"] for r in rows)
total_ga4_purchases = sum(r["ga4_purchases"] for r in rows)

k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Total Spend", f"${total_spend:,.2f}")
k2.metric("FSD (form_submit)", f"{total_deposits:,}")
k3.metric("CPR (FSD)", f"${cpd:.2f}" if cpd else "--")
k4.metric("Avg ROAS", f"{avg_roas:.2f}")
k5.metric("GA4 Sessions", f"{total_sessions:,}")

st.divider()

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
    name="FSD (form_submit)",
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
    yaxis2=dict(title="FSD / Sessions", overlaying="y", side="right",
                gridcolor=COLOR_GRID, zeroline=False),
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    margin=dict(l=40, r=40, t=40, b=40),
    height=380,
)

# --- Chart 2: CPD over time ------------------------------------------------
cpd_vals = [
    r["spend"] / r["deposits"] if r["deposits"] > 0 else None
    for r in rows
]
fig_cpd = go.Figure()
fig_cpd.add_trace(go.Scatter(
    x=[r["date"] for r in rows],
    y=cpd_vals,
    name="CPR (FSD)",
    mode="lines+markers",
    line=dict(color=COLOR_CPD, width=2),
    marker=dict(size=7, symbol="circle"),
    connectgaps=False,
))
fig_cpd.update_layout(
    plot_bgcolor=COLOR_BG_PLOT,
    paper_bgcolor=COLOR_BG_PAPER,
    font=dict(color=COLOR_FONT),
    xaxis=dict(title="Date", gridcolor=COLOR_GRID),
    yaxis=dict(title="CPR (FSD) ($)", gridcolor=COLOR_GRID, zeroline=False),
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    margin=dict(l=40, r=40, t=40, b=40),
    height=300,
)

col_left, col_right = st.columns(2)
with col_left:
    st.subheader("Daily trend")
    st.plotly_chart(fig_trend, use_container_width=True)
with col_right:
    st.subheader("CPR (FSD) over time")
    st.plotly_chart(fig_cpd, use_container_width=True)
    st.caption("CPR (FSD) = spend / form_submit_deposit. Gaps = zero-FSD days.")

st.divider()

# --- Chart 3: Meta vs GA4 attribution (per date) ---------------------------
if use_form_submit:
    meta_conv_y = [r["deposits"] for r in rows]
    meta_conv_label = "Meta FSD (form_submit)"
else:
    meta_conv_y = [r["meta_purchases"] for r in rows]
    meta_conv_label = "Meta purchases (7d-click)"

fig_attr = go.Figure()
fig_attr.add_trace(go.Bar(
    x=[r["date"] for r in rows],
    y=meta_conv_y,
    name=meta_conv_label,
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
    height=320,
)

st.subheader("Meta vs GA4 attribution (daily)")
st.plotly_chart(fig_attr, use_container_width=True)
st.caption("Never blend -- Meta uses 7-day click attribution; GA4 uses last-click.")

st.divider()

# --- GA4 engagement metrics ------------------------------------------------
ga4_eng = _cached_ga4_engagement(
    db_path_str, campaign, start_date.isoformat(), end_date.isoformat()
)

st.subheader("GA4 Engagement")
bounce = ga4_eng.get("avg_bounce_rate")
eng_time = ga4_eng.get("avg_engagement_time_sec")
total_users = int(ga4_eng.get("total_users") or 0)
new_users = int(ga4_eng.get("total_new_users") or 0)
new_pct = (new_users / total_users * 100) if total_users > 0 else None

e1, e2, e3, e4 = st.columns(4)
e1.metric(
    "Avg Engagement Time",
    f"{eng_time:.0f}s" if eng_time else "--",
    help="Average seconds a user actively engaged with the landing page (GA4).",
)
e2.metric(
    "Avg Bounce Rate",
    f"{bounce*100:.1f}%" if bounce is not None else "--",
    help="Sessions that viewed only one page with no engagement event (GA4).",
)
e3.metric("Total GA4 Users", f"{total_users:,}")
e4.metric(
    "New Users %",
    f"{new_pct:.0f}%" if new_pct is not None else "--",
    help="Share of sessions from first-time visitors.",
)

if not any([bounce, eng_time, total_users]):
    st.caption("No GA4 engagement data for this campaign in the selected range. "
               "Check that UTM tagging is applied and GA4 has been ingested.")

st.divider()

# --- Ad-set breakdown -------------------------------------------------------
st.subheader("Ad-set breakdown")
adset_rows = _cached_adset_breakdown(
    db_path_str, campaign, start_date.isoformat(), end_date.isoformat()
)

if adset_rows:
    # Learning phase: <50 FSDs in last 7 days from end_date
    learning_status = _cached_learning_status(db_path_str, end_date.isoformat())

    df_adset = pd.DataFrame(adset_rows)

    # Add Phase column — learning phase badge (checked 7d window from period end)
    def _phase_label(ad_set_id: str) -> str:
        if learning_status.get(ad_set_id, False):
            return "🎓 Learning"
        return "✅ Active"

    df_adset["phase"] = df_adset["ad_set_id"].apply(_phase_label)

    df_adset = df_adset.rename(columns={
        "ad_set_id": "Ad Set ID",
        "spend": "Spend",
        "deposits": "FSD",
        "cpd": "CPR (FSD)",
        "roas": "ROAS",
        "impressions": "Impressions",
        "clicks": "Clicks",
        "ctr_pct": "CTR %",
        "avg_frequency": "Avg Freq",
        "phase": "Phase",
    })
    df_adset["Spend"] = df_adset["Spend"].apply(lambda x: f"${x:,.2f}")
    df_adset["CPR (FSD)"] = df_adset["CPR (FSD)"].apply(lambda x: f"${x:.2f}" if x else "--")
    df_adset["ROAS"] = df_adset["ROAS"].apply(lambda x: f"{x:.2f}")
    df_adset["CTR %"] = df_adset["CTR %"].apply(lambda x: f"{x:.2f}%")
    df_adset["Avg Freq"] = df_adset["Avg Freq"].apply(lambda x: f"{x:.1f}")

    # Reorder: Phase first so it's visible without scrolling
    col_order = ["Ad Set ID", "Phase", "Spend", "FSD", "CPR (FSD)",
                 "ROAS", "Impressions", "Clicks", "CTR %", "Avg Freq"]
    df_adset = df_adset[[c for c in col_order if c in df_adset.columns]]

    st.dataframe(df_adset, hide_index=True, use_container_width=True)

    _n_learning = sum(1 for row in adset_rows if learning_status.get(row["ad_set_id"], False))
    _caption = f"{len(adset_rows)} ad sets · sorted by CPR (FSD) ascending (best first)"
    if _n_learning:
        _caption += f" · 🎓 {_n_learning} in learning phase (<50 FSDs/week — don't optimise CPR yet)"
    st.caption(_caption)
else:
    st.info(
        "No ad-set level data for this campaign. "
        "Ad-set granularity is ingested when the bot runs with per-adset fetching enabled."
    )
