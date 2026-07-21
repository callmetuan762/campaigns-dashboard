"""Funnel page — form_submit_deposit to $1 Stripe payment conversion.

Shows daily FSD counts, paid conversions, and paid_rate % by day and landing-page source.

Standalone: no src.ai.* imports, no asyncio (D-19 standalone rule).
Auth gate and palette duplicated from app.py per D-19.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import plotly.graph_objects as go
import streamlit as st

st.set_page_config(
    page_title="Funnel",
    layout="wide",
    initial_sidebar_state="expanded",
)

from src.dashboard import db                          # noqa: E402
from src.dashboard.components import render_scope_line  # noqa: E402
from src.dashboard.settings import DashboardSettings  # noqa: E402

# ---------------------------------------------------------------------------
# Dark-theme palette — duplicated from app.py per D-19 standalone rule
# ---------------------------------------------------------------------------
COLOR_BG_PAPER = "#0f1117"
COLOR_BG_PLOT = "#1a1d27"
COLOR_FONT = "#e4e7ef"
COLOR_GRID = "#2a2e3a"
COLOR_SPEND = "rgba(99, 125, 255, 0.6)"
COLOR_DEPOSITS = "#34d399"
COLOR_META = "#60a5fa"
COLOR_GA4 = "#a78bfa"
COLOR_CPD = "#f59e0b"

# Funnel-specific palette
COLOR_FSD = "rgba(99, 125, 255, 0.6)"   # blue bars — total form submits
COLOR_PAID = "#34d399"                   # green bars — paid
COLOR_RATE = "#f59e0b"                   # amber line — paid rate %
COLOR_WARN = "#f87171"                   # red dashed — warning threshold

PAID_RATE_WARNING_PCT = 25.0            # horizontal warning threshold line

settings = DashboardSettings()

# ---------------------------------------------------------------------------
# Auth gate — copied from app.py pattern (D-21)
# ---------------------------------------------------------------------------
if settings.dashboard_password:
    if not st.session_state.get("authenticated"):
        pwd = st.text_input("Password", type="password")
        if st.button("Login"):
            if pwd == settings.dashboard_password:
                st.session_state["authenticated"] = True
                st.rerun()
            else:
                st.error("Incorrect password")
        st.stop()

# ---------------------------------------------------------------------------
# Cached DB calls (D-14)
# ---------------------------------------------------------------------------

@st.cache_data(ttl=300, show_spinner=False)
def _cached_daily(db_path_str: str, start: str, end: str) -> list[dict[str, Any]]:
    from pathlib import Path
    return db.get_stripe_daily(Path(db_path_str), start, end)


@st.cache_data(ttl=300, show_spinner=False)
def _cached_by_source(db_path_str: str, start: str, end: str) -> list[dict[str, Any]]:
    from pathlib import Path
    return db.get_stripe_by_source(Path(db_path_str), start, end)


@st.cache_data(ttl=300, show_spinner=False)
def _cached_totals(db_path_str: str, start: str, end: str) -> dict[str, Any]:
    from pathlib import Path
    return db.get_stripe_period_totals(Path(db_path_str), start, end)


@st.cache_data(ttl=300, show_spinner=False)
def _cached_last_submitted(db_path_str: str) -> str | None:
    from pathlib import Path
    return db.get_stripe_last_submitted(Path(db_path_str))


@st.cache_data(ttl=300, show_spinner=False)
def _cached_campaign_funnel(db_path_str: str, start: str, end: str) -> list[dict[str, Any]]:
    from pathlib import Path
    return db.get_campaign_funnel(Path(db_path_str), start, end)


@st.cache_data(ttl=300, show_spinner=False)
def _cached_roas_freq(db_path_str: str, start: str, end: str) -> list[dict[str, Any]]:
    from pathlib import Path
    return db.get_roas_frequency_trend(Path(db_path_str), start, end)


@st.cache_data(ttl=300, show_spinner=False)
def _cached_lp_health(db_path_str: str, start: str, end: str) -> list[dict[str, Any]]:
    from pathlib import Path
    return db.get_landing_page_health(Path(db_path_str), start, end)


@st.cache_data(ttl=300, show_spinner=False)
def _cached_meta_kpi(db_path_str: str, start: str, end: str) -> dict[str, Any]:
    from pathlib import Path
    return db.get_kpi_summary(Path(db_path_str), start, end)


@st.cache_data(ttl=300, show_spinner=False)
def _cached_tracking_gap(db_path_str: str, start: str, end: str) -> list[dict[str, Any]]:
    from pathlib import Path
    return db.get_tracking_gap_days(Path(db_path_str), start, end)


@st.cache_data(ttl=300, show_spinner=False)
def _cached_source_trend(db_path_str: str, start: str, end: str) -> list[dict[str, Any]]:
    from pathlib import Path
    return db.get_stripe_source_trend(Path(db_path_str), start, end)


# ---------------------------------------------------------------------------
# Two-gate chart builder
# ---------------------------------------------------------------------------

def _make_two_gate_segment_chart(source_rows: list[dict[str, Any]]) -> go.Figure:
    """Grouped bar (FSD + Paid) per segment with Paid Rate % line.

    Gate 1: Ad → FSD  (volume bar, blue) — controlled by Meta CPR
    Gate 2: FSD → Paid (conversion bar, green) — controlled by landing page
    The gap between the two bars = opportunity to improve paid rate.
    """
    # Accept both source_rows (total_fsd key) and source_trend_rows (fsd key)
    sorted_rows = sorted(
        source_rows,
        key=lambda r: r.get("total_fsd") or r.get("fsd") or 0,
        reverse=True,
    )
    segments = [db.segment_display_name(r.get("source")) for r in sorted_rows]
    fsd_vals = [int(r.get("total_fsd") or r.get("fsd") or 0) for r in sorted_rows]
    paid_vals = [int(r.get("paid") or 0) for r in sorted_rows]
    rate_vals = [float(r.get("paid_rate") or 0) for r in sorted_rows]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=segments,
        y=fsd_vals,
        name="FSD — Gate 1 output",
        marker_color=COLOR_FSD,
        offsetgroup=0,
    ))
    fig.add_trace(go.Bar(
        x=segments,
        y=paid_vals,
        name="Paid — NSM",
        marker_color=COLOR_PAID,
        offsetgroup=1,
    ))
    fig.add_trace(go.Scatter(
        x=segments,
        y=rate_vals,
        name="Paid Rate % (Gate 2)",
        mode="lines+markers",
        line={"color": COLOR_RATE, "width": 2},
        marker={"size": 9},
        yaxis="y2",
    ))
    fig.update_layout(
        barmode="group",
        paper_bgcolor=COLOR_BG_PAPER,
        plot_bgcolor=COLOR_BG_PLOT,
        font={"color": COLOR_FONT},
        legend={"orientation": "h", "y": -0.20},
        margin={"l": 40, "r": 70, "t": 20, "b": 60},
        xaxis={"gridcolor": COLOR_GRID, "showgrid": False},
        yaxis={"title": "Count", "gridcolor": COLOR_GRID, "rangemode": "tozero"},
        yaxis2={
            "title": "Paid Rate %",
            "overlaying": "y",
            "side": "right",
            "showgrid": False,
            "ticksuffix": "%",
            "rangemode": "tozero",
        },
        height=360,
    )
    return fig


def _signal_badge(paid_rate: float | None) -> str:
    """Traffic-light badge for a segment's paid rate (Gate 2 strength)."""
    if paid_rate is None:
        return "—"
    if paid_rate >= 40:
        return "🟢 Strong (>40%)"
    if paid_rate >= 25:
        return "🟡 Medium (25–40%)"
    return "🔴 Weak (<25%)"


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
st.title("Funnel & Segments")
st.caption("NSM Two-Gate Framework: Ad → FSD (Gate 1, controlled by CPR) then FSD → Paid (Gate 2, controlled by landing page)")

# ---------------------------------------------------------------------------
# Sidebar — date range
# ---------------------------------------------------------------------------
db_path_str = str(settings.db_path)

with st.sidebar:
    st.header("Date range")
    today = date.today()
    default_start = today - timedelta(days=13)   # last 14 days inclusive
    start_date = st.date_input("From", value=default_start, max_value=today)
    end_date = st.date_input("To", value=today, min_value=start_date, max_value=today)

    last_submitted = _cached_last_submitted(db_path_str)
    if last_submitted:
        st.caption(f"Data freshness: last row submitted_at **{last_submitted[:10]}**")
    else:
        st.caption("Data freshness: no data yet")

start_str = start_date.isoformat()
end_str = end_date.isoformat()

render_scope_line(start_date, end_date, campaign_filter="All")

# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------
daily_rows = _cached_daily(db_path_str, start_str, end_str)
source_rows = _cached_by_source(db_path_str, start_str, end_str)
totals = _cached_totals(db_path_str, start_str, end_str)
meta_kpi = _cached_meta_kpi(db_path_str, start_str, end_str)
source_trend_rows = _cached_source_trend(db_path_str, start_str, end_str)
tracking_rows = _cached_tracking_gap(db_path_str, start_str, end_str)

# ---------------------------------------------------------------------------
# Empty state
# ---------------------------------------------------------------------------
if not daily_rows and not source_rows:
    st.info(
        "No Stripe data yet. Configure GOOGLE_SHEETS_SPREADSHEET_ID + credentials "
        "then run the daily backfill."
    )
    st.code(
        "# .env / environment variables to set\n"
        "GOOGLE_SHEETS_SPREADSHEET_ID=your_sheet_id_here\n"
        "\n"
        "# Option A — service account (preferred)\n"
        "GOOGLE_SERVICE_ACCOUNT_JSON='{\"type\":\"service_account\",...}'\n"
        "\n"
        "# Option B — OAuth token file\n"
        "GOOGLE_OAUTH_TOKEN_PATH=/path/to/token.json",
        language="bash",
    )
    st.stop()

# ---------------------------------------------------------------------------
# Section 0 — NSM Two-Gate Command Strip
# ---------------------------------------------------------------------------
st.subheader("NSM Two-Gate Framework")
st.caption(
    "**Gate 1** (Ad → FSD): controlled by creative, targeting, and bid — metric = **CPR**  ·  "
    "**Gate 2** (FSD → Paid): controlled by landing page offer and UX — metric = **Paid Rate**  ·  "
    "**CPaC** = CPR ÷ Paid Rate = your true acquisition cost"
)

# KPI strip: Paid (NSM) | FSD | CPR (Meta) | Paid Rate | CPaC
_tw_paid = int(totals.get("paid") or 0)
_tw_fsd = int(totals.get("total_fsd") or 0)
_tw_paid_rate = totals.get("paid_rate")
_tw_spend = float(meta_kpi.get("total_spend") or 0)
_tw_cpr = meta_kpi.get("cpd")  # CPR (FSD) from Meta
_tw_cpac = (_tw_spend / _tw_paid) if _tw_paid > 0 else None

tw1, tw2, tw3, tw4, tw5 = st.columns(5)
tw1.metric("Paid Conversions (NSM)", f"{_tw_paid:,}", help="Stripe paid count — your North Star Metric.")
tw2.metric("Form Submits (FSD)", f"{_tw_fsd:,}", help="Gate 1 output: total form submissions that entered the paid funnel.")
tw3.metric(
    "CPR (FSD)",
    f"${float(_tw_cpr):.2f}" if _tw_cpr else "—",
    help="Cost Per FSD from Meta. Gate 1 efficiency: lower = better creative/targeting.",
)
tw4.metric(
    "Blended Paid Rate",
    f"{float(_tw_paid_rate):.1f}%" if _tw_paid_rate is not None else "—",
    help="Gate 2 efficiency: what % of form submitters actually paid. Below 25% needs investigation.",
)
tw5.metric(
    "CPaC",
    f"${_tw_cpac:.2f}" if _tw_cpac is not None else "—",
    help="Cost Per Actual Conversion = Spend ÷ Paid. The true unit cost combining both gates.",
)

# Two-gate by segment chart + segment scorecard
if source_rows:
    st.divider()
    _chart_col, _table_col = st.columns([2, 1])

    with _chart_col:
        st.caption(
            "Gap between FSD bar and Paid bar = Gate 2 leakage. "
            "Amber line = paid rate % (right axis)."
        )
        st.plotly_chart(_make_two_gate_segment_chart(source_rows), use_container_width=True)

    with _table_col:
        st.caption("Segment Scorecard")
        # Use source_trend_rows (has prior-period delta) when available; fall back to source_rows
        _scorecard_rows = source_trend_rows if source_trend_rows else source_rows
        _seg_display = []
        for r in sorted(
            _scorecard_rows,
            key=lambda r: r.get("fsd") or r.get("total_fsd") or 0,
            reverse=True,
        ):
            pr = r.get("paid_rate")
            delta = r.get("delta_paid_rate_pp")
            _seg_display.append({
                "Segment": db.segment_display_name(r.get("source")),
                "FSD": int(r.get("fsd") or r.get("total_fsd") or 0),
                "Paid": int(r.get("paid") or 0),
                "Paid Rate": pr,
                "vs Prior": (
                    f"{delta:+.1f}pp" if delta is not None else "—"
                ),
                "Signal": _signal_badge(pr),
            })
        st.dataframe(
            _seg_display,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Segment": st.column_config.TextColumn("Segment", width="medium"),
                "FSD": st.column_config.NumberColumn("FSD", format="%d"),
                "Paid": st.column_config.NumberColumn("Paid", format="%d"),
                "Paid Rate": st.column_config.NumberColumn("Paid Rate %", format="%.1f%%"),
                "vs Prior": st.column_config.TextColumn("vs Prior", width="small",
                    help="Paid rate change vs prior equal-length period (percentage points)"),
                "Signal": st.column_config.TextColumn("Signal", width="medium"),
            },
        )
        if _tw_cpac is not None:
            st.caption(
                f"Blended CPaC: **${_tw_cpac:.2f}** · "
                "Per-segment CPaC unavailable (spend not split by segment)"
            )
        st.caption(
            "⚠️ FSDs counted per submission, not per unique person. "
            "Customers who submitted across multiple segments before paying "
            "appear once per submission — paid rate may be understated by ~3–5pp."
        )

st.divider()

# ---------------------------------------------------------------------------
# Section 0b — Tracking Health Audit (P0 alert)
# ---------------------------------------------------------------------------

TRACKING_ALERT_THRESHOLD = 20.0   # GA4/click ratio % below which we alert

if tracking_rows:
    # Count trailing consecutive days with active spend but ratio ≤ threshold
    _consecutive_bad = 0
    for _tr in reversed(tracking_rows):
        _clicks = _tr.get("meta_clicks") or 0
        _ratio = _tr.get("ratio_pct")
        if _clicks == 0:
            continue  # no spend that day — skip without resetting streak
        if _ratio is not None and _ratio <= TRACKING_ALERT_THRESHOLD:
            _consecutive_bad += 1
        else:
            break  # found a healthy day — stop

    if _consecutive_bad >= 3:
        st.error(
            f"🚨 **GA4 Tracking Gap — {_consecutive_bad} consecutive days with "
            f"<{TRACKING_ALERT_THRESHOLD:.0f}% GA4 session coverage (currently 0%).** "
            "Meta is recording clicks but GA4 sessions are not being tracked. "
            "Likely cause: in-app browser (Facebook/Instagram) blocking the GA4 tag. "
            "**Action required → test GA4 DebugView, check cookie consent, verify "
            "gtag fires on in-app browser.**"
        )
    elif _consecutive_bad > 0:
        st.warning(
            f"⚠️ GA4 tracking is below {TRACKING_ALERT_THRESHOLD:.0f}% for "
            f"{_consecutive_bad} consecutive day(s). Monitor closely."
        )

    with st.expander("📡 Tracking Health Audit", expanded=(_consecutive_bad >= 3)):
        st.caption(
            "GA4 sessions / Meta clicks ratio per day · "
            "Healthy = 50–100% · "
            "Below 20% for 3+ consecutive days = P0 tracking failure · "
            "⚠️ Uses Meta *clicks* as LPV proxy (no landing_page_views in DB)"
        )

        _tr_dates   = [r["date"] for r in tracking_rows]
        _tr_clicks  = [r.get("meta_clicks") or 0 for r in tracking_rows]
        _tr_sessions = [r.get("ga4_sessions") or 0 for r in tracking_rows]
        _tr_ratio   = [r.get("ratio_pct") for r in tracking_rows]

        fig_track = go.Figure()
        fig_track.add_trace(go.Bar(
            x=_tr_dates,
            y=_tr_clicks,
            name="Meta Clicks (LPV proxy)",
            marker_color=COLOR_META,
            opacity=0.65,
            yaxis="y",
        ))
        fig_track.add_trace(go.Bar(
            x=_tr_dates,
            y=_tr_sessions,
            name="GA4 Sessions",
            marker_color=COLOR_GA4,
            opacity=0.9,
            yaxis="y",
        ))
        fig_track.add_trace(go.Scatter(
            x=_tr_dates,
            y=_tr_ratio,
            name="GA4/Click Ratio %",
            mode="lines+markers",
            line={"color": COLOR_RATE, "width": 2},
            marker={"size": 7},
            yaxis="y2",
            connectgaps=False,
        ))
        fig_track.add_hline(
            y=TRACKING_ALERT_THRESHOLD,
            line=dict(color=COLOR_WARN, width=1.5, dash="dash"),
            annotation_text="🚨 20% alert threshold",
            annotation_font_color=COLOR_WARN,
            annotation_position="right",
            yref="y2",
        )
        fig_track.update_layout(
            barmode="group",
            paper_bgcolor=COLOR_BG_PAPER,
            plot_bgcolor=COLOR_BG_PLOT,
            font={"color": COLOR_FONT},
            legend={"orientation": "h", "y": -0.20},
            margin={"l": 40, "r": 80, "t": 20, "b": 50},
            xaxis={"gridcolor": COLOR_GRID, "showgrid": False},
            yaxis={"title": "Count", "gridcolor": COLOR_GRID, "rangemode": "tozero"},
            yaxis2={
                "title": "GA4 / Click %",
                "overlaying": "y",
                "side": "right",
                "showgrid": False,
                "ticksuffix": "%",
                "rangemode": "tozero",
            },
            height=320,
        )
        st.plotly_chart(fig_track, use_container_width=True)

st.divider()

# ---------------------------------------------------------------------------
# Section 1 — Daily FSD vs Paid Trend
# ---------------------------------------------------------------------------
st.subheader("Daily Form Submits vs. Paid Conversions")

# Compute prior-period paid_rate for delta metric
prior_start = (start_date - timedelta(days=14)).isoformat()
prior_end = (start_date - timedelta(days=1)).isoformat()
prior_totals = _cached_totals(db_path_str, prior_start, prior_end)

col_chart, col_metrics = st.columns([3, 1])

with col_chart:
    dates = [r["date"] for r in daily_rows]
    fsd_vals = [r["total_fsd"] for r in daily_rows]
    paid_vals = [r["paid"] for r in daily_rows]
    pending_vals = [r["pending"] for r in daily_rows]
    rate_vals = [r["paid_rate"] for r in daily_rows]

    fig = go.Figure()

    # Stacked bars: total_fsd (bottom = pending, top = paid)
    fig.add_trace(
        go.Bar(
            x=dates,
            y=pending_vals,
            name="Pending",
            marker_color=COLOR_FSD,
            offsetgroup=0,
        )
    )
    fig.add_trace(
        go.Bar(
            x=dates,
            y=paid_vals,
            name="Paid",
            marker_color=COLOR_PAID,
            offsetgroup=0,
            base=pending_vals,
        )
    )

    # Paid rate % line on secondary y-axis
    fig.add_trace(
        go.Scatter(
            x=dates,
            y=rate_vals,
            name="Paid Rate %",
            mode="lines+markers",
            line={"color": COLOR_RATE, "width": 2},
            yaxis="y2",
        )
    )

    # Warning threshold dashed red line at 25%
    fig.add_trace(
        go.Scatter(
            x=[dates[0], dates[-1]] if dates else [],
            y=[PAID_RATE_WARNING_PCT, PAID_RATE_WARNING_PCT],
            name=f"Warning ({PAID_RATE_WARNING_PCT:.0f}%)",
            mode="lines",
            line={"color": COLOR_WARN, "width": 1, "dash": "dash"},
            yaxis="y2",
        )
    )

    fig.update_layout(
        barmode="stack",
        paper_bgcolor=COLOR_BG_PAPER,
        plot_bgcolor=COLOR_BG_PLOT,
        font={"color": COLOR_FONT},
        legend={"orientation": "h", "y": -0.15},
        margin={"l": 40, "r": 60, "t": 30, "b": 40},
        xaxis={
            "gridcolor": COLOR_GRID,
            "showgrid": False,
        },
        yaxis={
            "title": "Form Submits",
            "gridcolor": COLOR_GRID,
            "showgrid": True,
        },
        yaxis2={
            "title": "Paid Rate %",
            "overlaying": "y",
            "side": "right",
            "showgrid": False,
            "range": [0, max(100, max((v for v in rate_vals if v is not None), default=0) + 10)],
            "ticksuffix": "%",
        },
    )

    st.plotly_chart(fig, use_container_width=True)

with col_metrics:
    total_fsd = totals.get("total_fsd") or 0
    total_paid = totals.get("paid") or 0
    current_rate = totals.get("paid_rate")
    prior_rate = prior_totals.get("paid_rate")

    st.metric("Period FSD", f"{total_fsd:,}")
    st.metric("Period Paid", f"{total_paid:,}")

    if current_rate is not None:
        delta_str: str | None = None
        if prior_rate is not None:
            delta_val = current_rate - prior_rate
            delta_str = f"{delta_val:+.1f}% vs prior 14d"
        st.metric(
            "Overall Paid Rate",
            f"{current_rate:.1f}%",
            delta=delta_str,
        )
    else:
        st.metric("Overall Paid Rate", "—")

# ---------------------------------------------------------------------------
# Section 2 — Source Breakdown Table (Gate 2 detail)
# ---------------------------------------------------------------------------
st.subheader("Source Breakdown")
st.caption(
    "Each row = one landing page segment. "
    "**Paid Rate** = Gate 2 strength. "
    "Segments below 25% need offer/UX investigation, not more ad spend."
)

if source_trend_rows or source_rows:
    # Prefer source_trend_rows (has prior-period data); fall back to source_rows
    _src_table_rows = source_trend_rows if source_trend_rows else source_rows
    table_data = []
    for r in _src_table_rows:
        _pr = r.get("paid_rate")
        _delta = r.get("delta_paid_rate_pp")
        table_data.append({
            "Segment": db.segment_display_name(r.get("source")),
            "FSD": int(r.get("fsd") or r.get("total_fsd") or 0),
            "Paid": int(r.get("paid") or 0),
            "Paid Rate %": _pr,
            "vs Prior": f"{_delta:+.1f}pp" if _delta is not None else "—",
            "Signal": _signal_badge(_pr),
        })
    st.dataframe(
        table_data,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Segment": st.column_config.TextColumn("Segment", width="medium"),
            "FSD": st.column_config.NumberColumn("FSD (Gate 1 output)", format="%d"),
            "Paid": st.column_config.NumberColumn("Paid (NSM)", format="%d"),
            "Paid Rate %": st.column_config.NumberColumn("Paid Rate % (Gate 2)", format="%.1f%%"),
            "vs Prior": st.column_config.TextColumn(
                "vs Prior Period",
                width="small",
                help="Paid rate change vs prior equal-length period (percentage points). "
                     "Positive = improving Gate 2.",
            ),
            "Signal": st.column_config.TextColumn("Signal", width="medium"),
        },
    )
    st.caption(
        "FSD = form submissions counted per submission. "
        "Multi-segment journeys (customer submits on segment A, pays on segment B) "
        "credit each segment separately — entry segment gets +1 FSD with 0 paid, "
        "closing segment gets +1 FSD with 1 paid."
    )
else:
    st.info("No source data available for the selected date range.")

# ---------------------------------------------------------------------------
# Section 3 — Funnel by Campaign + Health Table
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Funnel by Campaign")
st.caption("Impressions → Clicks → GA4 Sessions → Meta FSD per active campaign")

funnel_rows = _cached_campaign_funnel(db_path_str, start_str, end_str)

if funnel_rows:
    # Truncate campaign names for readability on chart
    def _short_name(name: str, max_len: int = 28) -> str:
        return name if len(name) <= max_len else name[:max_len - 1] + "…"

    short_names = [_short_name(r["campaign_name"]) for r in funnel_rows]

    # Horizontal grouped bar — log X avoids impressions dwarfing FSD counts
    fig_f = go.Figure()
    fig_f.add_trace(go.Bar(
        y=short_names,
        x=[r["impressions"] or 0 for r in funnel_rows],
        name="Impressions",
        marker_color="rgba(148, 163, 184, 0.45)",
        orientation="h",
    ))
    fig_f.add_trace(go.Bar(
        y=short_names,
        x=[r["clicks"] or 0 for r in funnel_rows],
        name="Clicks",
        marker_color=COLOR_META,
        orientation="h",
    ))
    fig_f.add_trace(go.Bar(
        y=short_names,
        x=[r["ga4_sessions"] or 0 for r in funnel_rows],
        name="GA4 Sessions",
        marker_color=COLOR_GA4,
        orientation="h",
    ))
    fig_f.add_trace(go.Bar(
        y=short_names,
        x=[r["meta_fsd"] or 0 for r in funnel_rows],
        name="Meta FSD",
        marker_color=COLOR_FSD,
        orientation="h",
    ))
    fig_f.update_layout(
        barmode="group",
        xaxis_type="log",
        xaxis_title="Count (log scale)",
        paper_bgcolor=COLOR_BG_PAPER,
        plot_bgcolor=COLOR_BG_PLOT,
        font={"color": COLOR_FONT},
        legend={"orientation": "h", "y": -0.15},
        margin={"l": 20, "r": 40, "t": 20, "b": 40},
        xaxis={"gridcolor": COLOR_GRID},
        yaxis={"gridcolor": COLOR_GRID, "showgrid": False, "automargin": True},
        height=max(260, len(funnel_rows) * 52),
    )
    st.plotly_chart(fig_f, use_container_width=True)

    # Funnel Health table
    st.subheader("Funnel Health Table")
    health_data = []
    for r in funnel_rows:
        imp = r["impressions"] or 0
        clk = r["clicks"] or 0
        ses = r["ga4_sessions"] or 0
        fsd = r["meta_fsd"] or 0

        ctr = round(clk / imp * 100, 2) if imp else None
        click_to_session = round(ses / clk * 100, 1) if clk else None
        session_to_fsd = round(fsd / ses * 100, 1) if ses else None

        flags: list[str] = []
        if ctr is not None and ctr < 0.5:
            flags.append("⚠️ CTR<0.5%")
        if click_to_session is not None and click_to_session < 40:
            flags.append("⚠️ bounce")
        if session_to_fsd is not None and session_to_fsd < 3:
            flags.append("⚠️ low FSD")
        freq = r.get("avg_frequency")
        if freq is not None and freq > 3.0:
            flags.append("⚠️ fatigue")

        health_data.append({
            "Campaign": r["campaign_name"],
            "Spend ($)": r["spend"],
            "ROAS": r["weighted_roas"],
            "CTR %": ctr,
            "Click→Session %": click_to_session,
            "Session→FSD %": session_to_fsd,
            "Frequency": freq,
            "Health": "✅ OK" if not flags else " | ".join(flags),
        })

    st.dataframe(
        health_data,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Campaign": st.column_config.TextColumn("Campaign"),
            "Spend ($)": st.column_config.NumberColumn("Spend ($)", format="$%.2f"),
            "ROAS": st.column_config.NumberColumn("ROAS", format="%.2f"),
            "CTR %": st.column_config.NumberColumn("CTR %", format="%.2f%%"),
            "Click→Session %": st.column_config.NumberColumn("Click→Session %", format="%.1f%%"),
            "Session→FSD %": st.column_config.NumberColumn("Session→FSD %", format="%.1f%%"),
            "Frequency": st.column_config.NumberColumn("Frequency", format="%.2f"),
            "Health": st.column_config.TextColumn("Health", width="medium"),
        },
    )
else:
    st.info("No campaign data in this date range.")

# ---------------------------------------------------------------------------
# Section 4 — Landing Page Health Matrix
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Landing Page Health Matrix")
st.caption(
    "Each bubble = one landing page · "
    "X = GA4 engagement time · Y = FSD rate (form submits / sessions) · "
    "Size = sessions · Colour = Stripe paid rate"
)

lp_rows = _cached_lp_health(db_path_str, start_str, end_str)

if lp_rows:
    import math

    def _bubble_size(sessions: int) -> float:
        """Map session count to a marker pixel size (8–42)."""
        if not sessions:
            return 8.0
        return max(8.0, min(42.0, 8.0 + math.log10(max(sessions, 1)) * 12.0))

    page_labels = [r["landing_page"] for r in lp_rows]
    x_vals = [r["avg_engagement_time"] or 0 for r in lp_rows]
    y_vals = [r["fsd_rate"] or 0 for r in lp_rows]
    sizes = [_bubble_size(r["sessions"] or 0) for r in lp_rows]
    colors = [r["paid_rate"] or 0 for r in lp_rows]
    hover_texts = [
        (
            f"<b>{r['landing_page']}</b><br>"
            f"Sessions: {r['sessions']:,}<br>"
            f"Engagement: {r['avg_engagement_time'] or 0:.0f}s<br>"
            f"FSD rate: {r['fsd_rate'] or 0:.1f}%<br>"
            f"FSD: {r['total_fsd']}, Paid: {r['paid']}<br>"
            f"Paid rate: {r['paid_rate'] or 0:.1f}%"
        )
        for r in lp_rows
    ]

    fig_lp = go.Figure(go.Scatter(
        x=x_vals,
        y=y_vals,
        mode="markers+text",
        text=[lbl.lstrip("/")[:20] for lbl in page_labels],
        textposition="top center",
        textfont={"size": 10, "color": COLOR_FONT},
        hovertext=hover_texts,
        hoverinfo="text",
        marker=dict(
            size=sizes,
            color=colors,
            colorscale=[[0, "#f87171"], [0.4, "#fbbf24"], [1.0, "#34d399"]],
            colorbar=dict(
                title="Paid Rate %",
                ticksuffix="%",
                tickfont={"color": COLOR_FONT},
                title_font={"color": COLOR_FONT},
            ),
            showscale=True,
            line=dict(color=COLOR_BG_PLOT, width=1),
        ),
    ))
    fig_lp.update_layout(
        paper_bgcolor=COLOR_BG_PAPER,
        plot_bgcolor=COLOR_BG_PLOT,
        font={"color": COLOR_FONT},
        xaxis={
            "title": "GA4 Avg Engagement Time (sec)",
            "gridcolor": COLOR_GRID,
        },
        yaxis={
            "title": "FSD Rate % (Form Submits / Sessions)",
            "gridcolor": COLOR_GRID,
            "ticksuffix": "%",
        },
        margin={"l": 60, "r": 60, "t": 30, "b": 60},
        height=460,
    )
    st.plotly_chart(fig_lp, use_container_width=True)
else:
    st.info(
        "No landing page health data. "
        "GA4 landing-page data is fetched daily — ensure the GA4 ingest has run."
    )

# ---------------------------------------------------------------------------
# Section 5 — ROAS vs Frequency Watch
# ---------------------------------------------------------------------------
st.divider()
st.subheader("ROAS vs Frequency Watch")
st.caption(
    "Blended ROAS (spend-weighted) vs average ad frequency · "
    "Frequency > 3 signals creative fatigue risk"
)

FREQ_FATIGUE = 3.0
ROAS_TARGET = 2.0

roas_freq_rows = _cached_roas_freq(db_path_str, start_str, end_str)

if roas_freq_rows:
    rf_dates = [r["date"] for r in roas_freq_rows]
    roas_vals = [r["blended_roas"] for r in roas_freq_rows]
    freq_vals = [r["avg_frequency"] for r in roas_freq_rows]

    fig_rf = go.Figure()

    # ROAS line (left axis, blue)
    fig_rf.add_trace(go.Scatter(
        x=rf_dates,
        y=roas_vals,
        name="Blended ROAS",
        mode="lines+markers",
        line={"color": COLOR_META, "width": 2},
        marker={"size": 6},
        yaxis="y",
    ))

    # ROAS target reference line at 2.0
    fig_rf.add_hline(
        y=ROAS_TARGET,
        line=dict(color=COLOR_DEPOSITS, width=1, dash="dot"),
        annotation_text=f"Target {ROAS_TARGET:.0f}×",
        annotation_font_color=COLOR_DEPOSITS,
        annotation_position="right",
        yref="y",
    )

    # Frequency line (right axis, amber)
    fig_rf.add_trace(go.Scatter(
        x=rf_dates,
        y=freq_vals,
        name="Avg Frequency",
        mode="lines+markers",
        line={"color": COLOR_CPD, "width": 2, "dash": "dash"},
        marker={"size": 6},
        yaxis="y2",
    ))

    # Fatigue threshold line
    fig_rf.add_hline(
        y=FREQ_FATIGUE,
        line=dict(color=COLOR_WARN, width=1, dash="dot"),
        annotation_text="⚠️ Fatigue risk",
        annotation_font_color=COLOR_WARN,
        annotation_position="right",
        yref="y2",
    )

    # Annotate individual fatigue-risk dates
    for d, f in zip(rf_dates, freq_vals):
        if f is not None and f > FREQ_FATIGUE:
            fig_rf.add_annotation(
                x=d,
                y=f,
                yref="y2",
                text="⚠️",
                showarrow=False,
                yshift=12,
                font={"size": 14},
            )

    fig_rf.update_layout(
        paper_bgcolor=COLOR_BG_PAPER,
        plot_bgcolor=COLOR_BG_PLOT,
        font={"color": COLOR_FONT},
        legend={"orientation": "h", "y": -0.18},
        margin={"l": 40, "r": 60, "t": 30, "b": 50},
        xaxis={"gridcolor": COLOR_GRID, "showgrid": False},
        yaxis={
            "title": "Blended ROAS",
            "gridcolor": COLOR_GRID,
            "showgrid": True,
            "rangemode": "tozero",
        },
        yaxis2={
            "title": "Avg Frequency",
            "overlaying": "y",
            "side": "right",
            "showgrid": False,
            "rangemode": "tozero",
        },
        height=360,
    )
    st.plotly_chart(fig_rf, use_container_width=True)
else:
    st.info("No Meta spend data in this date range.")

# ---------------------------------------------------------------------------
# Section 6 — Email Leads Mini-Funnel (placeholder)
# ---------------------------------------------------------------------------
import os as _os  # noqa: E402 — stdlib, safe in standalone page

st.divider()
st.subheader("Email Leads Funnel")

_email_sheet_id = _os.environ.get("GOOGLE_SHEETS_EMAIL_LEADS_SPREADSHEET_ID", "").strip()

if not _email_sheet_id:
    st.info(
        "📧 **Email leads funnel not configured.** "
        "To enable this section, set the environment variable below and restart the dashboard."
    )
    st.code(
        "# Add to your .env file:\n"
        "GOOGLE_SHEETS_EMAIL_LEADS_SPREADSHEET_ID=your_spreadsheet_id_here\n\n"
        "# The sheet must have columns: email, submitted_at, source, status\n"
        "# Status values: 'new' | 'qualified' | 'converted' | 'unsubscribed'",
        language="bash",
    )
    st.caption(
        "Once configured, this section will show: "
        "Leads collected → Qualified → Converted, "
        "segmented by landing page, with CPL (cost per lead) from Meta spend."
    )
else:
    # Future: call db.get_email_leads_funnel() once backend is built
    st.info(
        f"📧 Email leads sheet configured (`{_email_sheet_id[:12]}…`). "
        "Backend ingest not yet enabled — run the email leads backfill to populate."
    )
