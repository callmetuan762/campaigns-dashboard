"""Streamlit Overview page for the Ads Performance Dashboard (D-05..D-21).

Run from repo root:
    streamlit run src/dashboard/app.py

Architecture (D-01, D-02):
- Auth gate (D-21) — single shared password from DASHBOARD_PASSWORD env var.
- Sidebar (D-09) — date range picker with 7d / 30d quick buttons + data freshness.
- KPI row (D-05) — 6 st.metric cards.
- Charts (D-06, D-10) — Plotly dual-axis spend-vs-deposits + Meta vs GA4 grouped bars.
- Campaign table (D-08) — st.dataframe with ROAS emoji indicators.
- Chat bar (D-16, D-17) — st.chat_input + session_state history; calls run_chat().
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# IMPORTANT: page_config MUST be the first Streamlit call (Pitfall 4).
st.set_page_config(
    page_title="Ads Performance Dashboard",
    layout="wide",
    initial_sidebar_state="expanded",
)

from src.dashboard import chat as chat_mod  # noqa: E402  (after set_page_config)
from src.dashboard import db                  # noqa: E402
from src.dashboard.settings import DashboardSettings  # noqa: E402

# ---------------------------------------------------------------------------
# D-10 dark theme palette — single source of truth
# ---------------------------------------------------------------------------
COLOR_BG_PAPER = "#0f1117"
COLOR_BG_PLOT = "#1a1d27"
COLOR_FONT = "#e4e7ef"
COLOR_GRID = "#2a2e3a"
COLOR_SPEND = "rgba(99, 125, 255, 0.6)"
COLOR_DEPOSITS = "#34d399"
COLOR_META = "#60a5fa"
COLOR_GA4 = "#a78bfa"

# TIER tag palette (D-05) — campaign-table action labels
COLOR_TIER_SCALE = "#34d399"     # reuses COLOR_DEPOSITS green
COLOR_TIER_MAINTAIN = "#facc15"
COLOR_TIER_REDUCE = "#f87171"
COLOR_TIER_PAUSED = "#6b7280"

# ROAS thresholds (match src/reports/builder.py + D-05)
ROAS_GOOD = 2.0
ROAS_BAD = 1.0


# ---------------------------------------------------------------------------
# Cached data access (D-14) — wrappers live HERE, never inside db.py
# Cache key: db_path_str + start + end. Pass db_path as STRING.
# ---------------------------------------------------------------------------
@st.cache_data(ttl=300, show_spinner=False)
def _cached_kpi(db_path_str: str, start: str, end: str) -> dict[str, Any]:
    from pathlib import Path
    return db.get_kpi_summary(Path(db_path_str), start, end)


@st.cache_data(ttl=300, show_spinner=False)
def _cached_ga4_kpi(db_path_str: str, start: str, end: str) -> dict[str, Any]:
    from pathlib import Path
    return db.get_ga4_kpi(Path(db_path_str), start, end)


@st.cache_data(ttl=300, show_spinner=False)
def _cached_trend(db_path_str: str, start: str, end: str) -> list[dict[str, Any]]:
    from pathlib import Path
    return db.get_daily_trend(Path(db_path_str), start, end)


@st.cache_data(ttl=300, show_spinner=False)
def _cached_campaigns(db_path_str: str, start: str, end: str) -> list[dict[str, Any]]:
    from pathlib import Path
    return db.get_campaign_table(Path(db_path_str), start, end)


@st.cache_data(ttl=300, show_spinner=False)
def _cached_attribution(db_path_str: str, start: str, end: str) -> list[dict[str, Any]]:
    from pathlib import Path
    return db.get_attribution_comparison(Path(db_path_str), start, end)


def _generate_daily_insight(db_path_str: str, api_key: str) -> str:
    """Run the 3-agent system with a fixed daily-briefing prompt. Not cached here —
    caller stores the result in SQLite so it survives server restarts."""
    from datetime import date as _date, timedelta as _td
    from src.dashboard.chat import run_chat_3agent
    from src.dashboard.settings import DashboardSettings as _S
    yesterday = (_date.today() - _td(days=1)).isoformat()
    week_start = (_date.today() - _td(days=7)).isoformat()
    prompt = (
        f"Daily performance briefing as of {_date.today().isoformat()}. "
        f"Use your tools to analyze the period {week_start} to {yesterday}. "
        "Focus only on what needs attention or action today — not general summaries.\n\n"
        "**Alerts** — any campaigns with zero deposits in the last 3 days despite active spend? "
        "Any CPD that spiked more than 50% vs their prior week average? Name them specifically.\n"
        "**Top 2 this week** — campaigns with lowest CPD (name, CPD, spend, deposits)\n"
        "**Bottom 2 this week** — campaigns burning budget with worst CPD or no conversions "
        "(name, CPD or zero-deposit status, spend wasted)\n"
        "**Funnel gap** — which landing pages have the biggest drop between Meta ad sessions "
        "and GA4 conversions? Name the page, sessions, conversions, and implied conversion rate.\n"
        "**One action for today** — the single most impactful budget or creative change to make right now\n\n"
        "2 bullets max per section. Numbers only — no filler words. No preamble."
    )
    text, _ = run_chat_3agent(prompt, [], db_path_str, api_key, _S())  # type: ignore[call-arg]
    return text


@st.cache_data(ttl=60, show_spinner=False)
def _cached_freshness(db_path_str: str) -> dict[str, Any]:
    from pathlib import Path
    return db.get_data_freshness(Path(db_path_str))


@st.cache_data(ttl=300, show_spinner=False)
def _cached_stripe_kpi(db_path_str: str, start: str, end: str) -> dict[str, Any]:
    from pathlib import Path
    return db.get_stripe_period_totals(Path(db_path_str), start, end)


@st.cache_data(ttl=300, show_spinner=False)
def _cached_roas_freq(db_path_str: str, start: str, end: str) -> list[dict[str, Any]]:
    from pathlib import Path
    return db.get_roas_frequency_trend(Path(db_path_str), start, end)


# ---------------------------------------------------------------------------
# Plotly figure builders (D-06, D-10)
# ---------------------------------------------------------------------------
def _make_spend_vs_deposits_chart(rows: list[dict[str, Any]]) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=[r["date"] for r in rows],
        y=[r["spend"] for r in rows],
        name="Spend ($)",
        marker_color=COLOR_SPEND,
        yaxis="y",
    ))
    fig.add_trace(go.Scatter(
        x=[r["date"] for r in rows],
        y=[r["deposits"] for r in rows],
        name="Deposits",
        mode="lines+markers",
        line=dict(color=COLOR_DEPOSITS, width=2),
        marker=dict(size=8),
        yaxis="y2",
    ))
    fig.update_layout(
        plot_bgcolor=COLOR_BG_PLOT,
        paper_bgcolor=COLOR_BG_PAPER,
        font=dict(color=COLOR_FONT),
        xaxis=dict(title="Date", gridcolor=COLOR_GRID),
        yaxis=dict(title="Spend ($)", gridcolor=COLOR_GRID, zeroline=False),
        yaxis2=dict(title="Deposits", overlaying="y", side="right",
                    gridcolor=COLOR_GRID, zeroline=False),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(l=40, r=40, t=40, b=40),
        height=380,
    )
    return fig


def _make_attribution_chart(rows: list[dict[str, Any]], use_form_submit: bool = True) -> go.Figure:
    fig = go.Figure()
    if use_form_submit:
        meta_y = [r["meta_deposits"] for r in rows]
        meta_label = "Meta deposits (form_submit)"
    else:
        meta_y = [r["meta_purchases"] for r in rows]
        meta_label = "Meta purchases (7d-click)"
    fig.add_trace(go.Bar(
        x=[r["campaign_name"] for r in rows],
        y=meta_y,
        name=meta_label,
        marker_color=COLOR_META,
    ))
    fig.add_trace(go.Bar(
        x=[r["campaign_name"] for r in rows],
        y=[r["ga4_purchases"] for r in rows],
        name="GA4 (purchases, last-click)",
        marker_color=COLOR_GA4,
    ))
    fig.update_layout(
        barmode="group",
        plot_bgcolor=COLOR_BG_PLOT,
        paper_bgcolor=COLOR_BG_PAPER,
        font=dict(color=COLOR_FONT),
        xaxis=dict(title="Campaign", gridcolor=COLOR_GRID),
        yaxis=dict(title="Conversions", gridcolor=COLOR_GRID, zeroline=False),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(l=40, r=40, t=40, b=40),
        height=380,
    )
    return fig


# ---------------------------------------------------------------------------
# TIER action tags (D-03, D-04, DASH-06) — pure function, unit-testable
# ---------------------------------------------------------------------------
def _tier_tag(cpd: float | None, deposits: int, cpd_target: float) -> str:
    """Classify a campaign row into ★ SCALE / MAINTAIN / REDUCE / PAUSED.

    Returns empty string when cpd_target <= 0.0 (TIER column hidden — D-04).
    PAUSED takes precedence over any CPD comparison so zero-conversion
    campaigns never appear under SCALE/MAINTAIN/REDUCE.
    """
    if cpd_target <= 0.0:
        return ""
    if deposits == 0 or cpd is None:
        return "PAUSED"
    if cpd <= cpd_target:
        return "★ SCALE"
    if cpd <= cpd_target * 1.3:
        return "MAINTAIN"
    return "REDUCE"


# ---------------------------------------------------------------------------
# Campaign table helper (D-08)
# ---------------------------------------------------------------------------
def _roas_indicator(v: float | None) -> str:
    if v is None:
        return "—"
    if v >= ROAS_GOOD:
        return f"🟢 {v:.2f}"
    if v < ROAS_BAD:
        return f"🔴 {v:.2f}"
    return f"⚠️ {v:.2f}"


def _format_campaign_df(
    rows: list[dict[str, Any]],
    cpd_target: float = 0.0,
) -> pd.DataFrame:
    base_cols = ["Campaign", "Spend", "ROAS", "Impressions",
                 "Deposits", "CPD", "GA4 Sessions"]
    if not rows:
        cols = base_cols + (["TIER"] if cpd_target > 0.0 else [])
        return pd.DataFrame(columns=cols)
    df = pd.DataFrame(rows)
    df["ROAS"] = df["weighted_roas"].apply(_roas_indicator)
    df = df.rename(columns={
        "campaign_name": "Campaign",
        "spend": "Spend",
        "impressions": "Impressions",
        "deposits": "Deposits",
        "cpd": "CPD",
        "ga4_sessions": "GA4 Sessions",
    })
    if cpd_target > 0.0:
        df["TIER"] = df.apply(
            lambda r: _tier_tag(
                r["CPD"] if pd.notna(r["CPD"]) else None,
                int(r["Deposits"] or 0),
                cpd_target,
            ),
            axis=1,
        )
        return df[base_cols + ["TIER"]]
    return df[base_cols]


_CAMPAIGN_COLUMN_CONFIG = {
    "Spend": st.column_config.NumberColumn("Spend ($)", format="$%.2f"),
    "CPD": st.column_config.NumberColumn("CPD ($)", format="$%.2f"),
    "Impressions": st.column_config.NumberColumn(format="%d"),
    "Deposits": st.column_config.NumberColumn(format="%d"),
    "GA4 Sessions": st.column_config.NumberColumn(format="%d"),
}


# ---------------------------------------------------------------------------
# Auth gate (Pattern 8, D-21)
# ---------------------------------------------------------------------------
def _check_auth(password_required: str) -> bool:
    if not password_required:
        return True
    if st.session_state.get("authenticated"):
        return True
    st.title("Ads Performance Dashboard")
    st.caption("Sign in to continue.")
    with st.form("auth_form"):
        pw = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in")
        if submitted:
            if pw == password_required:
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error("Incorrect password")
    return False


# ---------------------------------------------------------------------------
# Main flow
# ---------------------------------------------------------------------------
settings = DashboardSettings()  # type: ignore[call-arg]

if not _check_auth(settings.dashboard_password):
    st.stop()

st.title("Ads Performance Dashboard")
st.caption("Meta Ads + GA4 — read-only view of metrics.db")

# DB-existence check
db_path = settings.db_path
if not db_path.exists():
    st.error(
        f"Database not found at `{db_path}`. "
        "Run the bot once to ingest data, or set DB_PATH in your .env file."
    )
    st.stop()

db_path_str = str(db_path)

# ---------------------------------------------------------------------------
# Sidebar — date picker, conversion metric selector, freshness (D-09)
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("Filters")
    today = date.today()
    default_end = today - timedelta(days=1)
    default_start = default_end - timedelta(days=6)

    if "date_range" not in st.session_state:
        st.session_state.date_range = (default_start, default_end)

    col_a, col_b = st.columns(2)
    if col_a.button("Last 7 days", use_container_width=True):
        st.session_state.date_range = (default_end - timedelta(days=6), default_end)
    if col_b.button("Last 30 days", use_container_width=True):
        st.session_state.date_range = (default_end - timedelta(days=29), default_end)

    dates = st.date_input(
        "Date range",
        value=st.session_state.date_range,
        max_value=today,
        key="date_range_picker",
    )
    if isinstance(dates, tuple) and len(dates) == 2:
        start_date, end_date = dates
        st.session_state.date_range = (start_date, end_date)
    else:
        st.warning("Pick both a start and an end date.")
        st.stop()

    if st.button("🔄 Refresh data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    st.divider()
    conv_metric = st.radio(
        "Conversion metric",
        options=["Deposits (form_submit)", "Purchases (7d-click)"],
        index=0,
        key="conv_metric",
    )
    use_form_submit: bool = conv_metric == "Deposits (form_submit)"

    st.divider()
    fresh = _cached_freshness(db_path_str)
    st.caption("**Data freshness**")
    st.caption(f"Meta last date: `{fresh.get('meta_last_date') or '—'}`")
    st.caption(f"GA4 last date:  `{fresh.get('ga4_last_date') or '—'}`")

start_iso = start_date.isoformat()
end_iso = end_date.isoformat()

# ---------------------------------------------------------------------------
# KPI row (D-05) — 7 st.metric cards (6 ad metrics + Stripe paid rate)
# ---------------------------------------------------------------------------
kpi = _cached_kpi(db_path_str, start_iso, end_iso)
ga4_kpi = _cached_ga4_kpi(db_path_str, start_iso, end_iso)

# Stripe paid-rate — current period vs prior period of equal length
_period_days = (end_date - start_date).days + 1
_prior_start = (start_date - timedelta(days=_period_days)).isoformat()
_prior_end = (start_date - timedelta(days=1)).isoformat()
stripe_kpi = _cached_stripe_kpi(db_path_str, start_iso, end_iso)
prior_stripe_kpi = _cached_stripe_kpi(db_path_str, _prior_start, _prior_end)

c1, c2, c3, c4, c5, c6, c7 = st.columns(7)

c1.metric("Total Spend", f"${float(kpi.get('total_spend') or 0):,.2f}")

roas = float(kpi.get("weighted_roas") or 0.0)
if roas >= ROAS_GOOD:
    roas_delta, roas_color = "🟢 above 2.0", "normal"
elif roas < ROAS_BAD:
    roas_delta, roas_color = "🔴 below 1.0", "inverse"
else:
    roas_delta, roas_color = "⚠️ 1.0–2.0", "off"
c2.metric("Blended ROAS", f"{roas:.2f}", delta=roas_delta, delta_color=roas_color)

deposits = int(kpi.get("total_deposits") or 0)
c3.metric("Deposits (NSM)", f"{deposits:,}")

cpd = kpi.get("cpd")
c4.metric("CPD", f"${float(cpd):.2f}" if cpd else "—")

sessions = int(ga4_kpi.get("total_sessions") or 0)
c5.metric("GA4 Sessions", f"{sessions:,}")

active = int(kpi.get("active_campaigns") or 0)
c6.metric("Active Campaigns", f"{active}")

_paid_rate = stripe_kpi.get("paid_rate")
_prior_paid_rate = prior_stripe_kpi.get("paid_rate")
if _paid_rate is not None:
    _pr_delta: str | None = None
    _pr_delta_color = "off"
    if _prior_paid_rate is not None:
        _pr_diff = _paid_rate - _prior_paid_rate
        _pr_delta = f"{_pr_diff:+.1f}% vs prior"
        _pr_delta_color = "normal" if _pr_diff >= 0 else "inverse"
    c7.metric("Stripe Paid Rate", f"{_paid_rate:.1f}%", delta=_pr_delta, delta_color=_pr_delta_color)
else:
    c7.metric("Stripe Paid Rate", "—")

# --- Secondary KPI row: efficiency metrics ---
s1, s2, s3, s4 = st.columns(4)
s1.metric("CTR %", f"{float(kpi.get('overall_ctr') or 0):.2f}%")
s2.metric("CPM", f"${float(kpi.get('avg_cpm') or 0):.2f}")
s3.metric("CPC", f"${float(kpi.get('avg_cpc') or 0):.2f}")
reach = int(kpi.get('total_reach') or 0)
s4.metric("Reach", f"{reach:,}")

# ---------------------------------------------------------------------------
# Charts row (D-06)
# ---------------------------------------------------------------------------
left, right = st.columns(2)

with left:
    st.subheader("Spend vs Deposits")
    trend_rows = _cached_trend(db_path_str, start_iso, end_iso)
    if trend_rows:
        st.plotly_chart(
            _make_spend_vs_deposits_chart(trend_rows),
            use_container_width=True,
        )
    else:
        st.info("No Meta data in this date range.")

with right:
    st.subheader("Meta vs GA4 Attribution")
    attr_rows = _cached_attribution(db_path_str, start_iso, end_iso)
    if attr_rows:
        st.plotly_chart(
            _make_attribution_chart(attr_rows, use_form_submit=use_form_submit),
            use_container_width=True,
        )
    else:
        st.info("No attribution data in this date range.")
    # D-07: never-blend caption
    st.caption(
        "Meta uses 7-day click attribution · GA4 uses last-click · "
        "Never blend these numbers."
    )

# ---------------------------------------------------------------------------
# ROAS vs Frequency Watch — compact homepage strip
# ---------------------------------------------------------------------------
roas_freq_rows = _cached_roas_freq(db_path_str, start_iso, end_iso)
if roas_freq_rows:
    _FREQ_FATIGUE = 3.0
    rf_dates = [r["date"] for r in roas_freq_rows]
    rf_roas = [r["blended_roas"] for r in roas_freq_rows]
    rf_freq = [r["avg_frequency"] for r in roas_freq_rows]

    _fatigue_days = sum(1 for f in rf_freq if f is not None and f > _FREQ_FATIGUE)
    _caption_extra = (
        f" · ⚠️ {_fatigue_days} day(s) above frequency 3.0 — check Funnel page"
        if _fatigue_days else ""
    )

    with st.expander(f"ROAS vs Frequency Watch{_caption_extra}", expanded=bool(_fatigue_days)):
        fig_rf_home = go.Figure()
        fig_rf_home.add_trace(go.Scatter(
            x=rf_dates, y=rf_roas,
            name="Blended ROAS",
            mode="lines+markers",
            line={"color": COLOR_META, "width": 2},
            marker={"size": 5},
            yaxis="y",
        ))
        fig_rf_home.add_trace(go.Scatter(
            x=rf_dates, y=rf_freq,
            name="Avg Frequency",
            mode="lines+markers",
            line={"color": "#f59e0b", "width": 2, "dash": "dash"},
            marker={"size": 5},
            yaxis="y2",
        ))
        # Fatigue threshold
        fig_rf_home.add_hline(
            y=_FREQ_FATIGUE,
            line=dict(color="#f87171", width=1, dash="dot"),
            annotation_text="Fatigue ≥3",
            annotation_font_color="#f87171",
            annotation_position="right",
            yref="y2",
        )
        fig_rf_home.update_layout(
            plot_bgcolor=COLOR_BG_PLOT,
            paper_bgcolor=COLOR_BG_PAPER,
            font=dict(color=COLOR_FONT),
            legend=dict(orientation="h", y=1.15, x=0),
            margin=dict(l=40, r=60, t=10, b=30),
            height=240,
            xaxis=dict(gridcolor=COLOR_GRID, showgrid=False),
            yaxis=dict(title="ROAS", gridcolor=COLOR_GRID, rangemode="tozero"),
            yaxis2=dict(
                title="Frequency",
                overlaying="y",
                side="right",
                showgrid=False,
                rangemode="tozero",
            ),
        )
        st.plotly_chart(fig_rf_home, use_container_width=True)
        st.caption("Full analysis → Funnel page · Section 5")

# ---------------------------------------------------------------------------
# Campaign table (D-08)
# ---------------------------------------------------------------------------
st.subheader("Campaign performance")
campaign_rows = _cached_campaigns(db_path_str, start_iso, end_iso)
if campaign_rows:
    st.dataframe(
        _format_campaign_df(campaign_rows, settings.cpd_target),
        hide_index=True,
        use_container_width=True,
        column_config=_CAMPAIGN_COLUMN_CONFIG,
    )
else:
    st.info("No campaign data in this date range.")

# ---------------------------------------------------------------------------
# AI Daily Briefing — generated once per calendar day, stored in SQLite
# ---------------------------------------------------------------------------
api_key = settings.anthropic_api_key or ""
from datetime import date as _today_date  # noqa: E402
with st.expander("AI Daily Briefing", expanded=True):
    if not api_key:
        st.info("Set `ANTHROPIC_API_KEY` in your `.env` to enable the daily AI briefing.")
    else:
        today_insight = db.get_today_insight(db_path)
        if today_insight is None:
            with st.spinner("Generating today's briefing (runs once per day)…"):
                today_insight = _generate_daily_insight(db_path_str, api_key)
                db.save_today_insight(db_path, today_insight)
        st.markdown(today_insight)
        cap_col, btn_col = st.columns([5, 1])
        cap_col.caption(
            f"Briefing for {_today_date.today().isoformat()} · "
            "Use **AI Chat** page for ad-hoc analysis"
        )
        if btn_col.button("Regenerate", use_container_width=True, key="regen_insight"):
            db.delete_today_insight(db_path)
            st.rerun()

# ---------------------------------------------------------------------------
# Drill-down navigation (D-07, DASH-07)
# ---------------------------------------------------------------------------
if campaign_rows:
    names = [r["campaign_name"] for r in campaign_rows]
    col_sel, col_btn = st.columns([4, 1])
    with col_sel:
        selected_campaign = st.selectbox(
            "Drill into a campaign",
            options=names,
            key="drill_select",
            label_visibility="visible",
        )
    with col_btn:
        st.write("")  # vertical alignment with selectbox label
        if st.button("View detail →", use_container_width=True, key="drill_btn"):
            # Pass campaign via query_params arg — st.switch_page clears params
            # before navigating so setting st.query_params first would be wiped.
            st.switch_page("pages/1_Campaign_Detail.py", query_params={"campaign": selected_campaign})

# ---------------------------------------------------------------------------
# AI chat bar (D-16, D-17)
# ---------------------------------------------------------------------------
st.divider()
st.subheader("AI assistant")

if "chat_history" not in st.session_state:
    st.session_state.chat_history = []

# Render history (skip tool_result list-content turns — those are internal)
for msg in st.session_state.chat_history:
    role = msg.get("role")
    content = msg.get("content")
    if role not in ("user", "assistant"):
        continue
    if isinstance(content, str):
        st.chat_message(role).markdown(content)
    # list-content (tool_use / tool_result) is internal; do not render

api_key = settings.anthropic_api_key or ""
if not api_key:
    st.info("AI chat unavailable — `ANTHROPIC_API_KEY` is not set in `.env`.")

if prompt := st.chat_input("Ask about campaign performance, ROAS, deposits…"):
    if not api_key:
        st.error("Cannot send: `ANTHROPIC_API_KEY` is not configured.")
    else:
        st.chat_message("user").markdown(prompt)
        with st.spinner("Thinking…"):
            final_text, new_history = chat_mod.run_chat_3agent(
                user_text=prompt,
                history=st.session_state.chat_history,
                db_path=db_path_str,
                api_key=api_key,
                settings=settings,
            )
        st.chat_message("assistant").markdown(final_text)
        st.session_state.chat_history = new_history
