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

import re
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# IMPORTANT: page_config MUST be the first Streamlit call (Pitfall 4).
st.set_page_config(
    page_title="Overview",
    layout="wide",
    initial_sidebar_state="expanded",
)

from src.dashboard import chat as chat_mod  # noqa: E402  (after set_page_config)
from src.dashboard import db                  # noqa: E402
from src.dashboard.components import (         # noqa: E402
    render_reconciliation_block,
    render_scope_line,
    source_help,
    source_line,
)
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
# Amber = unit cost, matching the cost-per-checkout and frequency lines already
# drawn further down this page.
COLOR_COST = "#f59e0b"

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
def _cached_meta_purchases_total(db_path_str: str, start: str, end: str) -> int:
    from pathlib import Path
    return db.get_meta_purchases_total(Path(db_path_str), start, end)


@st.cache_data(ttl=300, show_spinner=False)
def _cached_trend(db_path_str: str, start: str, end: str) -> list[dict[str, Any]]:
    from pathlib import Path
    return db.get_daily_trend(Path(db_path_str), start, end)


@st.cache_data(ttl=300, show_spinner=False)
def _cached_campaigns(db_path_str: str, start: str, end: str) -> list[dict[str, Any]]:
    from pathlib import Path
    return db.get_campaign_table(Path(db_path_str), start, end)


@st.cache_data(ttl=300, show_spinner=False)
def _cached_campaign_objectives(db_path_str: str) -> dict[str, str]:
    """Campaign name -> Meta objective (e.g. OUTCOME_SALES). Account-wide
    metadata, not date-scoped -- no start/end params (item 2, 2026-07-22)."""
    from pathlib import Path
    return db.get_campaign_objectives(Path(db_path_str))


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
        "**Alerts** — any campaigns with zero Initiate Checkout in the last 3 days despite "
        "active spend? Any CPR (Initiate Checkout) that spiked more than 50% vs their prior "
        "week average? Name them specifically.\n"
        "**Top 2 this week** — campaigns with lowest CPR (name, CPR (Initiate Checkout), "
        "spend, Initiate Checkout count)\n"
        "**Bottom 2 this week** — campaigns burning budget with worst CPR (Initiate Checkout) "
        "or no conversions (name, CPR or zero-conversion status, spend wasted)\n"
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


@st.cache_data(ttl=300, show_spinner=False)
def _cached_camp_daily(db_path_str: str, start: str, end: str) -> list[dict[str, Any]]:
    from pathlib import Path
    return db.get_campaign_daily_breakdown(Path(db_path_str), start, end)


# --- Overview v2 (2026-07-22) — Shopify-anchored KPI row cached wrappers ----
@st.cache_data(ttl=300, show_spinner=False)
def _cached_shopify_summary(
    db_path_str: str, start: str, end: str, valid_from: str
) -> dict[str, Any]:
    from pathlib import Path
    return db.get_shopify_paid_summary(Path(db_path_str), start, end, valid_from)


@st.cache_data(ttl=300, show_spinner=False)
def _cached_meta_begin_checkout(db_path_str: str, start: str, end: str) -> int:
    from pathlib import Path
    return db.get_meta_begin_checkout_total(Path(db_path_str), start, end)


@st.cache_data(ttl=300, show_spinner=False)
def _cached_meta_funnel(db_path_str: str, start: str, end: str) -> dict[str, Any]:
    from pathlib import Path
    return db.get_meta_funnel_summary(Path(db_path_str), start, end)


@st.cache_data(ttl=300, show_spinner=False)
def _cached_total_sessions(db_path_str: str, start: str, end: str) -> dict[str, Any]:
    from pathlib import Path
    return db.get_total_sessions_summary(Path(db_path_str), start, end)


@st.cache_data(ttl=300, show_spinner=False)
def _cached_ga4_purchase_events(db_path_str: str, start: str, end: str) -> dict[str, Any]:
    """Property-wide GA4 'purchase' event count from ga4_events (Overview v2
    reconciliation triangle) -- NOT the campaign-attributed ga4_metrics figure,
    which undercounts because the server-side purchase event loses utm_campaign
    for most orders."""
    from pathlib import Path
    return db.get_ga4_event_step_totals(Path(db_path_str), start, end, ["purchase"])["purchase"]


@st.cache_data(ttl=300, show_spinner=False)
def _cached_shopify_paid_daily(
    db_path_str: str, start: str, end: str, valid_from: str
) -> list[dict[str, Any]]:
    from pathlib import Path
    return db.get_shopify_paid_daily(Path(db_path_str), start, end, valid_from)


@st.cache_data(ttl=300, show_spinner=False)
def _cached_email_leads(db_path_str: str, start: str, end: str) -> dict[str, Any]:
    from pathlib import Path
    return db.get_email_leads_summary(Path(db_path_str), start, end)


@st.cache_data(ttl=300, show_spinner=False)
def _cached_internal_leads(db_path_str: str, start: str, end: str) -> list[dict[str, Any]]:
    from pathlib import Path
    return db.get_internal_lead_emails(Path(db_path_str), start, end)


@st.cache_data(ttl=300, show_spinner=False)
def _cached_checkout_recon(
    db_path_str: str, start: str, end: str, valid_from: str
) -> dict[str, Any]:
    from pathlib import Path
    return db.get_checkout_reconciliation(Path(db_path_str), start, end, valid_from)


@st.cache_data(ttl=300, show_spinner=False)
def _cached_leads_daily_status(
    db_path_str: str, start: str, end: str
) -> list[dict[str, Any]]:
    from pathlib import Path
    return db.get_email_leads_daily_by_status(Path(db_path_str), start, end)


# ---------------------------------------------------------------------------
# Plotly figure builders (D-06, D-10)
#
# Overview v2 (2026-07-22): the legacy "Spend vs FSD" and "Meta FSD vs Stripe
# Paid" charts are replaced outright (not gated behind an expander) by
# "Spend vs Initiate Checkout (Meta)" and "Meta Initiate Checkout vs Shopify Paid
# Orders" below -- the $1-deposit/Stripe funnel no longer exists in the live
# product (preorders flow straight to Shopify checkout), so the old FSD/
# Stripe chart builders would only ever render empty axes on current data.
# Decision note: dropped entirely rather than duplicated behind an expander,
# to avoid maintaining two versions of a chart neither of which any live date
# range will populate; the KPI row's legacy expander already covers the
# "look back at deposit-era history" use case.
# ---------------------------------------------------------------------------
def _make_spend_vs_begin_checkout_chart(rows: list[dict[str, Any]]) -> go.Figure:
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
        y=[r["begin_checkout"] for r in rows],
        name="Initiate Checkout (Meta)",
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
        yaxis2=dict(title="Initiate Checkout", overlaying="y", side="right",
                    gridcolor=COLOR_GRID, zeroline=False),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(l=40, r=40, t=40, b=40),
        height=380,
    )
    return fig


def _make_begin_checkout_vs_shopify_chart(
    trend_rows: list[dict[str, Any]],
    shopify_daily_rows: list[dict[str, Any]],
) -> go.Figure:
    """Daily Meta Initiate Checkout vs Shopify Paid Orders — side by side, never
    blended (both series labeled by source; Meta = platform-attributed,
    Shopify = ground truth)."""
    shopify_by_date: dict[str, int] = {
        r["date"]: int(r["paid"] or 0) for r in shopify_daily_rows
    }

    dates = [r["date"] for r in trend_rows]
    bc_vals = [int(r.get("begin_checkout") or 0) for r in trend_rows]
    paid_vals = [shopify_by_date.get(d, 0) for d in dates]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=dates,
        y=bc_vals,
        name="Meta Initiate Checkout",
        marker_color=COLOR_META,
        opacity=0.75,
    ))
    fig.add_trace(go.Scatter(
        x=dates,
        y=paid_vals,
        name="Shopify Paid Orders",
        mode="lines+markers",
        line=dict(color=COLOR_DEPOSITS, width=2),
        marker=dict(size=7),
    ))
    fig.update_layout(
        plot_bgcolor=COLOR_BG_PLOT,
        paper_bgcolor=COLOR_BG_PAPER,
        font=dict(color=COLOR_FONT),
        xaxis=dict(title="Date", gridcolor=COLOR_GRID),
        yaxis=dict(title="Count", gridcolor=COLOR_GRID, zeroline=False),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(l=40, r=40, t=40, b=40),
        height=380,
    )
    return fig


# ---------------------------------------------------------------------------
# Daily-trends-by-campaign helpers & chart builders
# ---------------------------------------------------------------------------
_CAMP_PALETTE = [
    "#60a5fa", "#34d399", "#f59e0b", "#a78bfa",
    "#f87171", "#22d3ee", "#ec4899", "#94a3b8", "#6b7280",
]


def _shorten_campaign(name: str) -> str:
    """Strip 'Nowa | SALES/LEADS | X.X ' prefix and ' | YYYYMMDD' suffix."""
    s = re.sub(r"^Nowa \| (?:SALES|LEADS) \| \d+\.[A-Z] ", "", name)
    s = re.sub(r" \| \d{8}$", "", s)
    return s[:30] if len(s) > 30 else s


def _camp_color_map(names: list[str]) -> dict[str, str]:
    return {n: _CAMP_PALETTE[i % len(_CAMP_PALETTE)]
            for i, n in enumerate(sorted(set(names)))}


def _make_begin_checkout_by_campaign(rows: list[dict]) -> go.Figure:
    dates = sorted({r["date"] for r in rows})
    campaigns = sorted({r["campaign_name"] for r in rows})
    short = {c: _shorten_campaign(c) for c in campaigns}
    colors = _camp_color_map(campaigns)
    fig = go.Figure()
    for camp in campaigns:
        d = {r["date"]: r["begin_checkout"] for r in rows if r["campaign_name"] == camp}
        fig.add_trace(go.Bar(
            x=dates,
            y=[d.get(dt, 0) for dt in dates],
            name=short[camp],
            marker_color=colors[camp],
        ))
    fig.update_layout(
        barmode="stack",
        plot_bgcolor=COLOR_BG_PLOT, paper_bgcolor=COLOR_BG_PAPER,
        font=dict(color=COLOR_FONT, size=11),
        xaxis=dict(gridcolor=COLOR_GRID),
        yaxis=dict(title="Initiate Checkout", gridcolor=COLOR_GRID, rangemode="tozero"),
        legend=dict(orientation="h", y=-0.38, x=0.5, xanchor="center",
                    font=dict(size=10), tracegroupgap=2),
        margin=dict(l=40, r=20, t=20, b=110),
        height=320,
    )
    return fig


def _make_spend_by_campaign(rows: list[dict]) -> go.Figure:
    dates = sorted({r["date"] for r in rows})
    campaigns = sorted({r["campaign_name"] for r in rows})
    short = {c: _shorten_campaign(c) for c in campaigns}
    colors = _camp_color_map(campaigns)
    fig = go.Figure()
    for camp in campaigns:
        d = {r["date"]: r["spend"] for r in rows if r["campaign_name"] == camp}
        fig.add_trace(go.Bar(
            x=dates,
            y=[d.get(dt, 0) for dt in dates],
            name=short[camp],
            marker_color=colors[camp],
        ))
    fig.update_layout(
        barmode="stack",
        plot_bgcolor=COLOR_BG_PLOT, paper_bgcolor=COLOR_BG_PAPER,
        font=dict(color=COLOR_FONT, size=11),
        xaxis=dict(gridcolor=COLOR_GRID),
        yaxis=dict(title="Spend ($)", gridcolor=COLOR_GRID, rangemode="tozero",
                   tickprefix="$"),
        legend=dict(orientation="h", y=-0.38, x=0.5, xanchor="center",
                    font=dict(size=10), tracegroupgap=2),
        margin=dict(l=40, r=20, t=20, b=110),
        height=320,
    )
    return fig


def _make_cost_per_bc_by_campaign(rows: list[dict]) -> go.Figure:
    """Cost per Initiate Checkout per campaign = spend / meta_begin_checkout,
    div0-guarded (rows.cost_per_bc is already None when begin_checkout == 0,
    computed in get_campaign_daily_breakdown's SQL)."""
    dates = sorted({r["date"] for r in rows})
    campaigns = sorted({r["campaign_name"] for r in rows})
    short = {c: _shorten_campaign(c) for c in campaigns}
    colors = _camp_color_map(campaigns)
    fig = go.Figure()
    for camp in campaigns:
        d = {r["date"]: r["cost_per_bc"] for r in rows if r["campaign_name"] == camp}
        fig.add_trace(go.Scatter(
            x=dates,
            y=[d.get(dt) for dt in dates],
            mode="lines+markers",
            name=short[camp],
            line=dict(color=colors[camp], width=2),
            marker=dict(size=5),
            connectgaps=True,
        ))
    fig.update_layout(
        plot_bgcolor=COLOR_BG_PLOT, paper_bgcolor=COLOR_BG_PAPER,
        font=dict(color=COLOR_FONT, size=11),
        xaxis=dict(gridcolor=COLOR_GRID),
        yaxis=dict(title="Cost per Initiate Checkout $", gridcolor=COLOR_GRID, rangemode="tozero",
                   tickprefix="$"),
        legend=dict(orientation="h", y=-0.38, x=0.5, xanchor="center",
                    font=dict(size=10), tracegroupgap=2),
        margin=dict(l=40, r=20, t=20, b=110),
        height=320,
    )
    return fig


def _make_ctr_by_campaign(rows: list[dict]) -> go.Figure:
    dates = sorted({r["date"] for r in rows})
    campaigns = sorted({r["campaign_name"] for r in rows})
    short = {c: _shorten_campaign(c) for c in campaigns}
    colors = _camp_color_map(campaigns)
    fig = go.Figure()
    for camp in campaigns:
        d = {r["date"]: r["ctr"] for r in rows if r["campaign_name"] == camp}
        fig.add_trace(go.Scatter(
            x=dates,
            y=[d.get(dt) for dt in dates],
            mode="lines+markers",
            name=short[camp],
            line=dict(color=colors[camp], width=2),
            marker=dict(size=5),
            connectgaps=True,
        ))
    fig.update_layout(
        plot_bgcolor=COLOR_BG_PLOT, paper_bgcolor=COLOR_BG_PAPER,
        font=dict(color=COLOR_FONT, size=11),
        xaxis=dict(gridcolor=COLOR_GRID),
        yaxis=dict(title="CTR %", gridcolor=COLOR_GRID, rangemode="tozero",
                   ticksuffix="%"),
        legend=dict(orientation="h", y=-0.38, x=0.5, xanchor="center",
                    font=dict(size=10), tracegroupgap=2),
        margin=dict(l=40, r=20, t=20, b=110),
        height=320,
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
    objectives: dict[str, str] | None = None,
    show_leads_metric: bool = False,
) -> pd.DataFrame:
    """objectives (item 2, 2026-07-22): {campaign_name: raw Meta objective},
    e.g. from _cached_campaign_objectives. Rendered as a "Goal" column via
    db.objective_display_label; "—" when no objective is known yet for that
    campaign (not yet backfilled, or pre-migration DB).

    "Initiate Checkout"/"CPR (Initiate Checkout)" columns source
    meta_begin_checkout-based begin_checkout/cost_per_bc fields (FSD ->
    Initiate Checkout re-point, 2026-07-22) -- meta_form_submit_deposit (FSD)
    is dead on current data. NOTE: `cpd_target` (TIER column threshold, from
    settings.cpd_target) was calibrated against cost-per-FSD dollars and is
    now compared against cost-per-Initiate-Checkout (or cost-per-Lead, see
    below) dollars instead -- these are typically very different price
    points (IC/Lead fire earlier/cheaper than a deposit did), so the
    threshold itself has NOT been re-calibrated here. It defaults to 0.0
    (TIER hidden) in this environment, so this is currently inert, but
    revisit the threshold before enabling TIER with a nonzero CPD_TARGET.

    show_leads_metric (dead use_form_submit toggle re-point, 2026-07-23):
    when True, swaps the "Initiate Checkout"/"CPR (Initiate Checkout)"
    columns for "Lead"/"CPR (Lead)", sourcing db.get_campaign_table's
    leads/cost_per_lead fields (meta_leads -- Meta's
    offsite_conversion.fb_pixel_lead). This is the Overview "Conversion
    metric" sidebar picker: Initiate Checkout is the native metric for
    OUTCOME_SALES campaigns, Lead is the native metric for OUTCOME_LEADS
    campaigns. A campaign that isn't optimizing for the selected metric
    simply reads 0 for it -- that's a true reading, not a bug. TIER (when
    enabled) is computed against whichever metric is selected."""
    metric_col = "Lead" if show_leads_metric else "Initiate Checkout"
    cpr_col = f"CPR ({metric_col})"
    base_cols = ["Campaign", "Goal", "Spend", "ROAS", "Impressions",
                 metric_col, cpr_col, "GA4 Sessions"]
    if not rows:
        cols = base_cols + (["TIER"] if cpd_target > 0.0 else [])
        return pd.DataFrame(columns=cols)
    df = pd.DataFrame(rows)
    df["ROAS"] = df["weighted_roas"].apply(_roas_indicator)
    if show_leads_metric:
        df = df.rename(columns={
            "campaign_name": "Campaign",
            "spend": "Spend",
            "impressions": "Impressions",
            "leads": metric_col,
            "cost_per_lead": cpr_col,
            "ga4_sessions": "GA4 Sessions",
        })
    else:
        df = df.rename(columns={
            "campaign_name": "Campaign",
            "spend": "Spend",
            "impressions": "Impressions",
            "begin_checkout": metric_col,
            "cost_per_bc": cpr_col,
            "ga4_sessions": "GA4 Sessions",
        })
    _objectives = objectives or {}
    df["Goal"] = df["Campaign"].apply(
        lambda name: db.objective_display_label(_objectives.get(name)) or "—"
    )
    if cpd_target > 0.0:
        df["TIER"] = df.apply(
            lambda r: _tier_tag(
                r[cpr_col] if pd.notna(r[cpr_col]) else None,
                int(r[metric_col] or 0),
                cpd_target,
            ),
            axis=1,
        )
        return df[base_cols + ["TIER"]]
    return df[base_cols]


_CAMPAIGN_COLUMN_CONFIG = {
    "Spend": st.column_config.NumberColumn("Spend ($)", format="$%.2f"),
    "CPR (Initiate Checkout)": st.column_config.NumberColumn("CPR (Initiate Checkout)", format="$%.2f"),
    "CPR (Lead)": st.column_config.NumberColumn("CPR (Lead)", format="$%.2f"),
    "Impressions": st.column_config.NumberColumn(format="%d"),
    "Initiate Checkout": st.column_config.NumberColumn("Initiate Checkout", format="%d"),
    "Lead": st.column_config.NumberColumn("Lead", format="%d"),
    "GA4 Sessions": st.column_config.NumberColumn(format="%d"),
    "Goal": st.column_config.TextColumn(
        "Goal", help="Meta campaign objective (e.g. Sales, Leads) — set at the campaign level in Ads Manager."
    ),
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
        "Conversion metric (Campaign performance table)",
        options=["Initiate Checkout (Sales)", "Lead (Leads)"],
        index=0,
        key="conv_metric",
        help="Picks which native conversion metric the campaign table below shows: "
        "Initiate Checkout (meta_begin_checkout) for OUTCOME_SALES campaigns, "
        "or Lead (meta_leads / Meta's offsite_conversion.fb_pixel_lead) for "
        "OUTCOME_LEADS campaigns. A campaign that isn't optimizing for the "
        "selected metric will simply show 0.",
    )
    show_leads_metric: bool = conv_metric == "Lead (Leads)"

    st.divider()
    fresh = _cached_freshness(db_path_str)
    st.caption("**Data freshness**")
    st.caption(f"Meta last date: `{fresh.get('meta_last_date') or '—'}`")
    st.caption(f"GA4 last date:  `{fresh.get('ga4_last_date') or '—'}`")

start_iso = start_date.isoformat()
end_iso = end_date.isoformat()

# Scope line (Phase D trust/UX) — always-visible date/campaign/attribution
# summary directly under the page title. No campaign selector on this page,
# so the scope is always "All".
render_scope_line(start_date, end_date, campaign_filter="All")

# ---------------------------------------------------------------------------
# KPI row v2 (Overview v2, 2026-07-22) — MER / Blended CAC / Pre-orders
# anchored primary row, replacing the deposit-era (FSD/CPR/Paid/CPaC) tiles
# and the mislabeled "Blended ROAS" (it was always spend-weighted Meta
# *platform* ROAS, never blended with anything). Legacy deposit tiles move
# into an expander below that only renders when there was legacy FSD activity
# in range — the $1-deposit/Stripe step no longer exists in the live funnel
# (preorders flow straight to Shopify checkout), so it is empty today.
# ---------------------------------------------------------------------------
kpi = _cached_kpi(db_path_str, start_iso, end_iso)
ga4_kpi = _cached_ga4_kpi(db_path_str, start_iso, end_iso)

# Current-period Shopify / GA4-all-sessions / Meta-funnel figures
_period_days = (end_date - start_date).days + 1
_prior_start = (start_date - timedelta(days=_period_days)).isoformat()
_prior_end = (start_date - timedelta(days=1)).isoformat()

shopify_kpi = _cached_shopify_summary(db_path_str, start_iso, end_iso, settings.orders_valid_from)
ga4_sessions_all = _cached_total_sessions(db_path_str, start_iso, end_iso)
meta_bc_total = _cached_meta_begin_checkout(db_path_str, start_iso, end_iso)
meta_funnel = _cached_meta_funnel(db_path_str, start_iso, end_iso)

# Prior-period equivalents (same-length window immediately before start_date)
# — used for the period-over-period deltas on every card below.
stripe_kpi = _cached_stripe_kpi(db_path_str, start_iso, end_iso)
prior_stripe_kpi = _cached_stripe_kpi(db_path_str, _prior_start, _prior_end)
prior_kpi = _cached_kpi(db_path_str, _prior_start, _prior_end)
prior_shopify_kpi = _cached_shopify_summary(
    db_path_str, _prior_start, _prior_end, settings.orders_valid_from
)
prior_ga4_sessions_all = _cached_total_sessions(db_path_str, _prior_start, _prior_end)
prior_meta_bc_total = _cached_meta_begin_checkout(db_path_str, _prior_start, _prior_end)
prior_meta_funnel = _cached_meta_funnel(db_path_str, _prior_start, _prior_end)


def _fmt_spend(v: float) -> str:
    """Compact spend formatter: $1.2K / $1.2M so it fits in a narrow KPI card."""
    if v >= 1_000_000:
        return f"${v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"${v / 1_000:.1f}K"
    return f"${v:.2f}"


def _pct_change(cur: float | None, prior: float | None) -> float | None:
    """% change of cur vs prior. None when either side is missing/undefined."""
    if cur is None or prior is None or prior == 0:
        return None
    return (cur - prior) / abs(prior) * 100.0


def _delta_pct_text(pct: float | None) -> str | None:
    return f"{pct:+.1f}% vs prior" if pct is not None else None


# One row per conversion goal, because the two goals answer different questions
# and mixing them in one strip invites reading a Paid ratio against an
# Initiate-Checkout count. Row 1 is the paid goal (Shopify ground truth); row 2
# is the Initiate Checkout goal (Meta-attributed, the signal the ad sets actually
# optimise on). Spend sits in row 1 as the shared denominator for both.
st.markdown("**Goal: Paid** — Shopify orders, ground truth")
c1, c1b, c2, c3, c4 = st.columns(5)

st.markdown("**Goal: Initiate Checkout** — Meta-attributed, what the ad sets optimise on")
c_ic, c_cpic, c6, c7 = st.columns(4)

# Card 1 — Total Spend (Meta)
_spend = float(kpi.get("total_spend") or 0)
_prior_spend = float(prior_kpi.get("total_spend") or 0)
c1.metric(
    "Total Spend",
    _fmt_spend(_spend),
    delta=_delta_pct_text(_pct_change(_spend, _prior_spend)),
    delta_color="off",
    help=source_help("M", note="Total Meta ad spend for the period."),
)

# Card 1b — Total Revenue (Shopify paid orders) — sits next to Spend so the
# two halves of every efficiency ratio below (MER, CAC) are both visible as
# raw dollars, not only as the derived multiple.
_revenue = float(shopify_kpi.get("revenue") or 0)
_prior_revenue = float(prior_shopify_kpi.get("revenue") or 0)
c1b.metric(
    "Total Revenue",
    _fmt_spend(_revenue),
    delta=_delta_pct_text(_pct_change(_revenue, _prior_revenue)),
    delta_color="normal",
    help=source_help(
        "Shop",
        note="Sum of total_price on paid Shopify orders — money collected, so it "
             "includes shipping, not just the $99 product line. Same rows that "
             "feed the Pre-orders count and MER.",
    ),
)

# Card 2 — MER (blended) = Shopify paid revenue ÷ Meta spend
_mer = (shopify_kpi["revenue"] / _spend) if _spend > 0 else None
_prior_mer = (
    (prior_shopify_kpi["revenue"] / _prior_spend) if _prior_spend > 0 else None
)
c2.metric(
    "MER (blended)",
    f"{_mer:.2f}x" if _mer is not None else "—",
    delta=_delta_pct_text(_pct_change(_mer, _prior_mer)),
    delta_color="normal",
    help=source_help("Shop", "M", note="Shopify revenue ÷ Meta ad spend — attribution-free"),
)

# Card 3 — Blended CAC = Spend ÷ Shopify paid order count
_cac = (_spend / shopify_kpi["count"]) if shopify_kpi["count"] > 0 else None
_prior_cac = (
    (_prior_spend / prior_shopify_kpi["count"]) if prior_shopify_kpi["count"] > 0 else None
)
c3.metric(
    "Blended CAC",
    f"${_cac:.2f}" if _cac is not None else "—",
    delta=_delta_pct_text(_pct_change(_cac, _prior_cac)),
    delta_color="inverse",  # lower CAC = better = green for a negative delta
    help=source_help("M", "Shop", note="Meta spend ÷ Shopify paid order count — true cost per preorder"),
)

# Card 4 — Pre-orders (Shopify paid count in range)
_preorders = int(shopify_kpi["count"])
_prior_preorders = int(prior_shopify_kpi["count"])
c4.metric(
    "Pre-orders",
    f"{_preorders:,}",
    delta=_delta_pct_text(_pct_change(_preorders, _prior_preorders)),
    delta_color="normal",
    help=source_help("Shop", note="Paid order count (financial_status = 'paid'), respecting the "
         "orders_valid_from cutoff that excludes pre-launch/test orders."),
)

# --- Goal: Initiate Checkout row ------------------------------------------
# Meta ROAS (7d-click) was dropped from this strip: it is spend-weighted Meta
# platform ROAS, which over-reports, and MER one row up already answers the same
# question against Shopify ground truth. It is still on the Campaign performance
# table per campaign, where the over-reporting is easier to judge in context.

# Card — Initiate Checkout (Meta), the volume behind the cost figure beside it
c_ic.metric(
    "Initiate Checkout",
    f"{meta_bc_total:,}",
    delta=_delta_pct_text(_pct_change(meta_bc_total, prior_meta_bc_total)),
    delta_color="normal",
    help=source_help(
        "M",
        note="Meta's begin-checkout pixel event. Inflated by the cart-permalink "
             "auto-redirect, so read it as reserve-click intent rather than "
             "deliberate checkout entry — it will run above Shopify's own count.",
    ),
)

# Card — Cost per Initiate Checkout = Meta spend ÷ Meta Initiate Checkout
_cpic = (_spend / meta_bc_total) if meta_bc_total > 0 else None
_prior_cpic = (
    (_prior_spend / prior_meta_bc_total) if prior_meta_bc_total > 0 else None
)
c_cpic.metric(
    "Cost per Initiate Checkout",
    f"${_cpic:.2f}" if _cpic is not None else "—",
    delta=_delta_pct_text(_pct_change(_cpic, _prior_cpic)),
    delta_color="inverse",  # cheaper is better, so a negative delta is green
    help=source_help(
        "M",
        note="Total Meta spend ÷ Meta Initiate Checkout. Both sides are Meta's own "
             "numbers, so this is single-source — but it inherits the same "
             "auto-redirect inflation, which makes it read cheaper than the true "
             "cost of a deliberate checkout.",
    ),
)

# Card 6 — GA4 Sessions (all) — from ga4_landing_pages (NOT the campaign-
# attributed ga4_metrics figure, which undercounts real GA4 traffic).
_sessions_all = int(ga4_sessions_all.get("sessions") or 0)
_prior_sessions_all = int(prior_ga4_sessions_all.get("sessions") or 0)
c6.metric(
    "GA4 Sessions (all)",
    f"{_sessions_all:,}",
    delta=_delta_pct_text(_pct_change(_sessions_all, _prior_sessions_all)),
    delta_color="normal",
    help=source_help("G", note="All GA4 sessions from ga4_landing_pages — no campaign filter, "
         "includes '(not set)' / untagged traffic. Compare with the campaign-"
         "attributed figure in the Reconciliation section below."),
)

# Card 7 — LPV → Checkout (Meta-attributed) = SUM(meta_begin_checkout) ÷ SUM(landing_page_views)
_lpv_total = int(meta_funnel.get("landing_page_views") or 0)
_lpv_cvr = (meta_bc_total / _lpv_total * 100.0) if _lpv_total > 0 else None
_prior_lpv_total = int(prior_meta_funnel.get("landing_page_views") or 0)
_prior_lpv_cvr = (
    (prior_meta_bc_total / _prior_lpv_total * 100.0) if _prior_lpv_total > 0 else None
)
c7.metric(
    "LPV → Checkout (Meta-attributed)",
    f"{_lpv_cvr:.1f}%" if _lpv_cvr is not None else "—",
    delta=_delta_pct_text(_pct_change(_lpv_cvr, _prior_lpv_cvr)),
    delta_color="normal",
    help=source_help("M", note="Meta Initiate Checkout ÷ Meta landing_page_views — platform-side only."),
)

# --- Initiate Checkout, counted three ways --------------------------------
# The card above is Meta's number alone. Shown on its own it invites the reader
# to treat it as "how many people started checkout", which it is not — so the
# same step is repeated here from each source that can see it, side by side and
# never combined (CLAUDE.md house rule), with the reason they disagree stated
# rather than left to be discovered.
_ck = _cached_checkout_recon(db_path_str, start_iso, end_iso, settings.orders_valid_from)

with st.expander(
    f"Initiate Checkout across sources — Meta {_ck['meta']:,} · GA4 {_ck['ga4']:,}"
    + (f" · Shopify {_ck['shopify']:,}" if _ck["shopify_available"] else " · Shopify n/a")
):
    source_line("M", "G", "Shop", note="three measurements of one step — never averaged")
    k1, k2, k3 = st.columns(3)

    k1.metric("Meta Initiate Checkout", f"{_ck['meta']:,}")
    k1.caption("Pixel event, 7-day click / 1-day view")

    k2.metric("GA4 begin_checkout", f"{_ck['ga4']:,}")
    k2.caption("Property-wide event count")

    if _ck["shopify_available"]:
        k3.metric("Shopify checkouts (floor)", f"{_ck['shopify']:,}")
        k3.caption(
            f"{_ck['shopify_abandoned']:,} abandoned-with-email + "
            f"{_ck['shopify_orders']:,} paid orders"
        )
    else:
        k3.metric("Shopify checkouts", "n/a")
        k3.caption("Checkout ingest has not run yet")

    st.markdown(
        "**Why the three disagree — none of them is wrong.**\n\n"
        "- **Meta** counts its own pixel firing. The cart permalink auto-redirects "
        "straight into Shopify checkout, so this fires on reserve-click intent, "
        "not on a deliberate decision to check out.\n"
        "- **GA4** counts the `begin_checkout` event property-wide, including "
        "traffic Meta never claimed — which is why it usually runs highest.\n"
        "- **Shopify** is a **floor, not a count**: it only exposes an abandoned "
        "checkout once the shopper leaves contact details, so anyone who opened "
        "checkout and left before entering an email is invisible to it. Paid "
        "orders are added back because a completed checkout disappears from that "
        "endpoint. A true Shopify checkout-page-load count needs funnel analytics "
        "this store's plan does not expose.\n\n"
        "Read the **trend in each** against itself, not the gap between them. The "
        "one to act on is Shopify — it is the only one tied to money."
    )

# --- Initiate Checkout + Cost per Initiate Checkout, by day ----------------
# The two cards above are period totals, which hide whether a move happened on
# one bad day or drifted all week. Volume and unit cost share one chart on two
# axes because they are read together: rising cost is only alarming if volume
# is not rising with it.
_ic_daily = _cached_trend(db_path_str, start_iso, end_iso)
if _ic_daily:
    fig_ic = go.Figure()
    fig_ic.add_trace(go.Bar(
        x=[r["date"] for r in _ic_daily],
        y=[int(r["begin_checkout"] or 0) for r in _ic_daily],
        name="Initiate Checkout",
        marker_color=COLOR_META,
        opacity=0.75,
        yaxis="y",
    ))
    fig_ic.add_trace(go.Scatter(
        x=[r["date"] for r in _ic_daily],
        # None rather than 0 on zero-checkout days: a gap in the line is honest,
        # a $0 point would read as "checkouts were free that day".
        y=[
            (float(r["spend"]) / int(r["begin_checkout"]))
            if int(r["begin_checkout"] or 0) > 0 else None
            for r in _ic_daily
        ],
        name="Cost per Initiate Checkout",
        mode="lines+markers",
        connectgaps=False,
        line=dict(color=COLOR_COST, width=2),
        marker=dict(size=7),
        yaxis="y2",
    ))
    fig_ic.update_layout(
        plot_bgcolor=COLOR_BG_PLOT,
        paper_bgcolor=COLOR_BG_PAPER,
        font=dict(color=COLOR_FONT),
        xaxis=dict(title="Date", gridcolor=COLOR_GRID),
        yaxis=dict(title="Initiate Checkout", gridcolor=COLOR_GRID,
                   zeroline=False, rangemode="tozero"),
        yaxis2=dict(title="Cost per IC ($)", overlaying="y", side="right",
                    gridcolor=COLOR_GRID, zeroline=False, rangemode="tozero"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(l=40, r=50, t=30, b=40),
        height=300,
    )
    st.plotly_chart(fig_ic, use_container_width=True, theme=None)
    st.caption(
        "Both series are Meta's own numbers. Gaps in the cost line are days with "
        "zero Initiate Checkout, where a cost per checkout does not exist."
    )

# --- Legacy deposit funnel (FSD/Stripe era) — only when there was legacy FSD
# activity in range. The $1-deposit/Stripe step no longer exists in the live
# funnel, so this collapses to nothing on current data; kept so historical
# date ranges (pre-migration to Shopify preorders) still render correctly.
_legacy_fsd_total = int(kpi.get("total_deposits") or 0)
if _legacy_fsd_total > 0:
    with st.expander("Legacy deposit funnel (FSD/Stripe era)"):
        source_line("M", "Sheet", note="Deposit-era metrics: FSD/CPR from Meta, Paid from the legacy Stripe Google Sheet")
        l1, l2, l3, l4 = st.columns(4)

        l1.metric(
            "FSD (Gate 1)",
            f"{_legacy_fsd_total:,}",
            help=source_help("M", note="Form Submit Deposits — Gate 1 output: everyone who submitted the form "
                 "(includes both paid and still-pending). CPR = Spend ÷ FSD."),
        )

        cpd = kpi.get("cpd")
        l2.metric("CPR (FSD)", f"${float(cpd):.2f}" if cpd else "—", help=source_help("M"))

        _paid_count = int(stripe_kpi.get("paid") or 0)
        _paid_rate = stripe_kpi.get("paid_rate")
        _prior_paid_rate = prior_stripe_kpi.get("paid_rate")
        _pr_delta: str | None = None
        _pr_delta_color = "off"
        if _paid_rate is not None and _prior_paid_rate is not None:
            _pr_diff = _paid_rate - _prior_paid_rate
            _pr_delta = f"{_pr_diff:+.1f}% paid rate vs prior"
            _pr_delta_color = "normal" if _pr_diff >= 0 else "inverse"
        elif _paid_rate is not None:
            _pr_delta = f"{_paid_rate:.1f}% paid rate"
            _pr_delta_color = "off"
        l3.metric(
            "Paid (NSM)",
            f"{_paid_count:,}" if _paid_count else "—",
            delta=_pr_delta,
            delta_color=_pr_delta_color,
            help=source_help("Sheet", note="Stripe paid conversions (from the legacy Google Sheet) — "
                 "your North Star Metric (Gate 2 output). Delta = paid rate change vs prior period."),
        )

        # CPaC = Cost Per Actual Conversion = total spend / paid
        _total_paid_cpac = int(stripe_kpi.get("paid") or 0)
        _total_spend_cpac = float(kpi.get("total_spend") or 0)
        _cpac = (_total_spend_cpac / _total_paid_cpac) if _total_paid_cpac > 0 else None

        _prior_paid_cpac = int(prior_stripe_kpi.get("paid") or 0)
        _prior_spend_cpac = float(prior_kpi.get("total_spend") or 0)
        _prior_cpac = (
            (_prior_spend_cpac / _prior_paid_cpac) if _prior_paid_cpac > 0 else None
        )

        _cpac_delta: str | None = None
        if _cpac is not None and _prior_cpac is not None:
            _cpac_diff = _cpac - _prior_cpac
            _cpac_delta = f"{_cpac_diff:+.2f} vs prior"

        l4.metric(
            "CPaC",
            f"${_cpac:.2f}" if _cpac is not None else "—",
            delta=_cpac_delta,
            delta_color="inverse",
            help=source_help("M", "Sheet", note="Cost Per Actual Conversion = Meta spend ÷ Sheet-sourced Paid. "
                 "Combines ad efficiency (CPR) + landing page paid rate. Deposit/Stripe-era metric."),
        )

# --- Secondary KPI row: efficiency metrics ---
source_line("M", note="Secondary efficiency metrics")
s1, s2, s3, s4 = st.columns(4)
s1.metric("CTR %", f"{float(kpi.get('overall_ctr') or 0):.2f}%", help=source_help("M"))
s2.metric("CPM", f"${float(kpi.get('avg_cpm') or 0):.2f}", help=source_help("M"))
s3.metric("CPC", f"${float(kpi.get('avg_cpc') or 0):.2f}", help=source_help("M"))
reach = int(kpi.get('total_reach') or 0)
s4.metric(
    "Reach (sum of daily)",
    f"{reach:,}",
    help=source_help("M", note="daily reach summed — overcounts unique people across days"),
)

# ---------------------------------------------------------------------------
# Email leads — from the Preorder Leads Dashboard sheet (email_leads table).
#
# Sits directly under the two NSM rows because leads are the other half of what
# the same ad spend bought: the rows above price a preorder, this one prices an
# email address. Counts are unique addresses, not sheet rows, and are scoped the
# same way the sheet's own summary tab scopes them (internal/test excluded,
# deduped-tab population) so the totals reconcile with what the team already
# reads there. See db.get_email_leads_summary for why statuses are not
# normalised and why the channel counts do not sum to the total.
# ---------------------------------------------------------------------------
_leads = _cached_email_leads(db_path_str, start_iso, end_iso)
_prior_leads = _cached_email_leads(db_path_str, _prior_start, _prior_end)

if _leads["total"] or _prior_leads["total"]:
    st.subheader("Email leads")
    source_line(
        "Sheet", "M",
        note="lead counts from the Preorder Leads sheet; cost per lead divides Meta spend by them",
    )

    _n_leads = _leads["total"]
    _n_prior_leads = _prior_leads["total"]
    _cpl = (_spend / _n_leads) if _n_leads > 0 else None
    _prior_cpl = (_prior_spend / _n_prior_leads) if _n_prior_leads > 0 else None

    e1, e2, e3, e4, e5, e6 = st.columns(6)
    e1.metric(
        "Total leads",
        f"{_n_leads:,}",
        delta=_delta_pct_text(_pct_change(_n_leads, _n_prior_leads)),
        delta_color="normal",
        help=source_help(
            "Sheet",
            note="Unique email addresses first seen in this period, internal/test "
                 "addresses excluded. Counts addresses, not sheet rows.",
        ),
    )
    e2.metric(
        "Cost per lead",
        f"${_cpl:.2f}" if _cpl is not None else "—",
        delta=_delta_pct_text(_pct_change(_cpl, _prior_cpl)),
        delta_color="inverse",  # cheaper is better, so a negative delta is green
        help=source_help(
            "M", "Sheet",
            note="All Meta spend in the period ÷ total leads — blended, so it charges "
                 "preorder and quiz spend alike against every lead. Not a quiz-only CPL.",
        ),
    )
    e3.metric(
        "Quiz leads",
        f"{_leads['from_quiz']:,}",
        delta=_delta_pct_text(_pct_change(_leads["from_quiz"], _prior_leads["from_quiz"])),
        delta_color="normal",
        help=source_help("Sheet", note="Seen on the quiz tab."),
    )
    e4.metric(
        "Preorder Started",
        f"{_leads['from_preorder_started']:,}",
        delta=_delta_pct_text(
            _pct_change(_leads["from_preorder_started"], _prior_leads["from_preorder_started"])
        ),
        delta_color="normal",
        help=source_help("Sheet", note="Seen on the preorder-started tab (the checkout email gate)."),
    )
    e5.metric(
        "Exit Intent leads",
        f"{_leads['from_exit_intent']:,}",
        delta=_delta_pct_text(
            _pct_change(_leads["from_exit_intent"], _prior_leads["from_exit_intent"])
        ),
        delta_color="normal",
        help=source_help("Sheet", note="Seen on the exit-intent tab."),
    )
    # Abandoned checkout — the recoverable audience, so it earns a card of its own
    # rather than only living in the status bar below. delta_color is "off": more
    # abandoned checkouts is neither plainly good (more people reached checkout)
    # nor plainly bad (more people dropped), so colouring it would assert a
    # judgement the number does not support.
    _abandoned = _leads["by_status"].get("abandoned_checkout", 0)
    _prior_abandoned = _prior_leads["by_status"].get("abandoned_checkout", 0)
    e6.metric(
        "Abandoned checkout",
        f"{_abandoned:,}",
        delta=_delta_pct_text(_pct_change(_abandoned, _prior_abandoned)),
        delta_color="off",
        help=source_help(
            "Sheet",
            note="Leads whose latest deposit status is abandoned_checkout — they "
                 "reached checkout and did not pay, so this is the recovery "
                 "audience. Counted on latest status, not on the day they "
                 "abandoned.",
        ),
    )

    st.caption(
        "The three channel counts overlap and will not add up to Total leads — one "
        "address can take a quiz and later hit the exit-intent popup, and is counted "
        "in both. These are sheet (ActiveCampaign-synced) figures, so they will not "
        "match GA4 `lead_submit` / popup event counts."
        + (
            f" · {_leads['channel_only']} more address(es) appear in the channel tabs "
            "but not on the deduped Leads tab, so they are excluded from Total leads."
            if _leads["channel_only"] else ""
        )
        + (
            f" · {_leads['internal_excluded']} internal/test address(es) excluded."
            if _leads["internal_excluded"] else ""
        )
    )

    # The excluded list is shown rather than just counted: the failure mode here
    # is a NEW staff or test address the pattern list has not caught yet, and the
    # only way to spot that is to see what the filter did catch and notice what
    # is missing from it.
    if _leads["internal_excluded"]:
        with st.expander(
            f"Excluded internal / test addresses ({_leads['internal_excluded']})"
        ):
            _internal_rows = _cached_internal_leads(db_path_str, start_iso, end_iso)
            st.dataframe(
                pd.DataFrame(_internal_rows).rename(columns={
                    "email": "Email",
                    "lead_date": "First seen",
                    "deposit_status": "Deposit status",
                }),
                hide_index=True,
                use_container_width=True,
            )
            st.caption(
                "Matched by `LEADS_INTERNAL_EMAIL_PATTERNS`. Rows are flagged, not "
                "deleted — editing that list re-classifies them on the next sheet "
                "pull with no backfill. Spot a real lead in here, or a test address "
                "missing from it, and that env var is the one thing to change."
            )

    # --- Deposit-status split -------------------------------------------------
    # Ordered by funnel progression rather than by size, so the shape of the bar
    # reads left-to-right as coldest -> paid. Anything the sheet reports that is
    # not in this list still renders, appended after the known stages.
    _STATUS_ORDER = [
        "exit_intent", "pending", "preorder_started",
        "abandoned_checkout", "payment-pending", "paid",
    ]
    _STATUS_COLORS = {
        "exit_intent": "#64748b",
        "pending": "#f59e0b",
        "preorder_started": COLOR_META,
        "abandoned_checkout": "#f87171",
        "payment-pending": "#a78bfa",
        "paid": COLOR_DEPOSITS,
        "(not set)": "#3f3f46",
    }
    _by_status = _leads["by_status"]
    if _by_status:
        _known = [s for s in _STATUS_ORDER if s in _by_status]
        _rest = sorted(s for s in _by_status if s not in _STATUS_ORDER)
        _ordered = _known + _rest
        _total_status = sum(_by_status.values()) or 1

        fig_status = go.Figure()
        for status in _ordered:
            n = _by_status[status]
            pct = n / _total_status * 100
            fig_status.add_trace(go.Bar(
                x=[n],
                y=["Leads"],
                name=f"{status} ({n} · {pct:.0f}%)",
                orientation="h",
                marker_color=_STATUS_COLORS.get(status, "#94a3b8"),
                # Hide the label on slivers, where it would overlap its neighbours.
                text=[f"{status}<br>{n} · {pct:.0f}%" if pct >= 7 else ""],
                textposition="inside",
                insidetextanchor="middle",
                hovertemplate=f"{status}: {n} leads ({pct:.1f}%)<extra></extra>",
            ))
        fig_status.update_layout(
            barmode="stack",
            plot_bgcolor=COLOR_BG_PLOT,
            paper_bgcolor=COLOR_BG_PAPER,
            font=dict(color=COLOR_FONT, size=11),
            xaxis=dict(title="", showgrid=False, zeroline=False, showticklabels=False),
            yaxis=dict(title="", showgrid=False, showticklabels=False),
            legend=dict(orientation="h", y=-0.45, x=0, font=dict(size=10)),
            margin=dict(l=10, r=10, t=6, b=6),
            height=170,
            showlegend=True,
        )
        st.plotly_chart(fig_status, use_container_width=True, theme=None)
        st.caption(
            "Deposit status is the address's latest funnel stage on the sheet, coldest "
            "on the left. Statuses are shown exactly as the sheet writes them — "
            "`pending` and `payment-pending` are separate bars because they are "
            "separate values in the source, not a rendering bug."
        )

        # --- Same split, by day ------------------------------------------------
        # Read this as cohorts, not transitions: the sheet keeps one row per
        # address with its first-seen date and its LATEST status, with no history.
        # So a point on the 'paid' line is "leads first seen that day who are paid
        # today", not "leads that paid that day". Stated in the caption because the
        # natural reading of a status time-series is the wrong one here.
        _leads_daily = _cached_leads_daily_status(db_path_str, start_iso, end_iso)
        if _leads_daily:
            _all_dates = sorted({r["lead_date"] for r in _leads_daily})
            _by_status_date: dict[str, dict[str, int]] = {}
            for r in _leads_daily:
                _by_status_date.setdefault(r["deposit_status"], {})[r["lead_date"]] = (
                    int(r["count"] or 0)
                )
            _daily_order = [s for s in _ordered if s in _by_status_date] + [
                s for s in sorted(_by_status_date) if s not in _ordered
            ]

            fig_leads_ts = go.Figure()
            for status in _daily_order:
                per_date = _by_status_date[status]
                fig_leads_ts.add_trace(go.Scatter(
                    x=_all_dates,
                    # 0 rather than None here: unlike a unit cost, "no leads at
                    # this stage that day" is a real, meaningful zero.
                    y=[per_date.get(d, 0) for d in _all_dates],
                    name=status,
                    mode="lines+markers",
                    line=dict(color=_STATUS_COLORS.get(status, "#94a3b8"), width=2),
                    marker=dict(size=6),
                ))
            fig_leads_ts.update_layout(
                plot_bgcolor=COLOR_BG_PLOT,
                paper_bgcolor=COLOR_BG_PAPER,
                font=dict(color=COLOR_FONT),
                xaxis=dict(title="Lead first-seen date", gridcolor=COLOR_GRID),
                yaxis=dict(title="Leads", gridcolor=COLOR_GRID,
                           zeroline=False, rangemode="tozero"),
                legend=dict(orientation="h", yanchor="bottom", y=1.02,
                            xanchor="left", x=0, font=dict(size=10)),
                margin=dict(l=40, r=20, t=30, b=40),
                height=300,
                hovermode="x unified",
            )
            st.plotly_chart(fig_leads_ts, use_container_width=True, theme=None)
            st.caption(
                "**Cohorts, not transitions.** The x-axis is the day a lead was "
                "first seen; the line it sits on is that lead's status *today*. A "
                "rise in `paid` on 24 Jul means leads acquired on 24 Jul who have "
                "since paid — not payments received on 24 Jul. The sheet stores "
                "only each address's latest status, so a true status-change-per-day "
                "view is not derivable from it. Daily totals here sum to Total "
                f"leads ({_n_leads:,})."
            )

# ---------------------------------------------------------------------------
# Triangle reconciliation v2 (Overview v2, 2026-07-22) — Meta vs GA4
# (property-wide) vs Shopify (ground truth). Never blended (CLAUDE.md house
# rule): three independent counts side by side, plus a gap chip, a
# campaign-attributed GA4 caption so the utm-loss is visible, and a
# plain-English "why don't these match" note.
# ---------------------------------------------------------------------------
st.subheader("Reconciliation: Meta vs GA4 vs Shopify")
source_line("M", "G", "Shop", note="three independent counts, shown side by side on purpose — never summed")
_meta_purchases_total = _cached_meta_purchases_total(db_path_str, start_iso, end_iso)
_ga4_purchase_events = _cached_ga4_purchase_events(db_path_str, start_iso, end_iso)
_ga4_purchases_total = int(_ga4_purchase_events.get("count") or 0)
_ga4_purchases_attributed = int(ga4_kpi.get("total_purchases") or 0)
_shopify_paid_total = int(shopify_kpi.get("count") or 0)
render_reconciliation_block(
    meta_purchases=_meta_purchases_total,
    ga4_purchases=_ga4_purchases_total,
    shopify_paid_orders=_shopify_paid_total,
    ga4_purchases_attributed=_ga4_purchases_attributed,
)

# ---------------------------------------------------------------------------
# Charts row (D-06)
# ---------------------------------------------------------------------------
left, right = st.columns(2)

with left:
    st.subheader("Spend vs Initiate Checkout (Meta)")
    source_line("M", note="both series are Meta's own numbers")
    trend_rows = _cached_trend(db_path_str, start_iso, end_iso)
    if trend_rows:
        st.plotly_chart(
            _make_spend_vs_begin_checkout_chart(trend_rows),
            use_container_width=True,
            theme=None,
        )
    else:
        st.info("No Meta data in this date range.")

with right:
    st.subheader("Meta Initiate Checkout vs Shopify Paid Orders")
    source_line("M", "Shop", note="never blended — different attribution scopes")
    shopify_daily_rows = _cached_shopify_paid_daily(
        db_path_str, start_iso, end_iso, settings.orders_valid_from
    )
    if trend_rows:
        st.plotly_chart(
            _make_begin_checkout_vs_shopify_chart(trend_rows, shopify_daily_rows),
            use_container_width=True,
            theme=None,
        )
    else:
        st.info("No data in this date range.")
    st.caption(
        "Bars = Meta-attributed Initiate Checkout (≈ reserve-click intent; inflated by the "
        "cart-permalink auto-redirect) · Line = Shopify paid orders (ground truth) · "
        "Side-by-side, never blended — different attribution scopes."
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
        source_line("M")
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
        st.plotly_chart(fig_rf_home, use_container_width=True, theme=None)
        st.caption("Full analysis → Funnel page · Section 5")

# ---------------------------------------------------------------------------
# Daily trends by campaign (stacked Initiate Checkout/Spend + Cost-per-IC/CTR)
# ---------------------------------------------------------------------------
camp_daily = _cached_camp_daily(db_path_str, start_iso, end_iso)
if camp_daily:
    st.subheader("Daily trends by campaign")
    source_line("M", note="all 4 charts below are Meta only")
    _dt_l, _dt_r = st.columns(2)
    with _dt_l:
        st.caption("Initiate Checkout by campaign")
        st.plotly_chart(_make_begin_checkout_by_campaign(camp_daily), use_container_width=True, theme=None)
    with _dt_r:
        st.caption("Spend by campaign ($)")
        st.plotly_chart(_make_spend_by_campaign(camp_daily), use_container_width=True, theme=None)
    _dt_l2, _dt_r2 = st.columns(2)
    with _dt_l2:
        st.caption("Cost per Initiate Checkout per campaign — lower is better")
        st.plotly_chart(_make_cost_per_bc_by_campaign(camp_daily), use_container_width=True, theme=None)
    with _dt_r2:
        st.caption("CTR % per campaign")
        st.plotly_chart(_make_ctr_by_campaign(camp_daily), use_container_width=True, theme=None)

# ---------------------------------------------------------------------------
# Campaign table (D-08)
# ---------------------------------------------------------------------------
st.subheader("Campaign performance")
source_line("M", "G", note="GA4 badge covers the Sessions column only, joined by exact campaign name")
st.caption(
    "Showing **Lead** (Leads-objective campaigns)" if show_leads_metric
    else "Showing **Initiate Checkout** (Sales-objective campaigns) — "
    "switch to Lead in the sidebar to see Leads-objective campaigns' native metric."
)
campaign_rows = _cached_campaigns(db_path_str, start_iso, end_iso)
campaign_objectives = _cached_campaign_objectives(db_path_str)
if campaign_rows:
    st.dataframe(
        _format_campaign_df(
            campaign_rows, settings.cpd_target, campaign_objectives, show_leads_metric,
        ),
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
    source_line("M", "G", "Shop", note="Claude reads across all three via tool calls")
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

    def _drill_option_label(name: str) -> str:
        """Append the campaign's Meta objective as a short "(Goal: X)" suffix,
        e.g. "Nowa | SALES | preorder-image | 20260715  (Goal: Sales)" (item 2,
        2026-07-22). format_func only affects the displayed text -- the
        selectbox's return value (used for st.switch_page's query param and
        the exact campaign-name match on the Detail page) stays the raw name.
        """
        goal = db.objective_display_label(campaign_objectives.get(name))
        return f"{name}  (Goal: {goal})" if goal else name

    col_sel, col_btn = st.columns([4, 1])
    with col_sel:
        selected_campaign = st.selectbox(
            "Drill into a campaign",
            options=names,
            format_func=_drill_option_label,
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
