"""Funnel page — form_submit_deposit to $1 Stripe payment conversion.

Shows daily FSD counts, paid conversions, and paid_rate % by day and landing-page source.

Standalone: no src.ai.* imports, no asyncio (D-19 standalone rule).
Auth gate and palette duplicated from app.py per D-19.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

st.set_page_config(
    page_title="Funnel",
    layout="wide",
    initial_sidebar_state="expanded",
)

from src.dashboard import db                          # noqa: E402
from src.dashboard.components import badge_html, render_scope_line, source_line  # noqa: E402
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

# Quiz-funnel landing pages — duplicated from src/config.py QUIZ_LP_SLUGS per
# the D-19 standalone-page rule (pages never import src.config; see the
# palette constants above, duplicated from app.py for the same reason).
QUIZ_LP_SLUGS = ["routine-break", "big-feelings-type", "screen-kid"]

# Canonical preorder-funnel landing pages — duplicated from src/config.py
# PREORDER_LP_SLUGS per the same D-19 rule. Used with QUIZ_LP_SLUGS to bucket
# junk/legacy lp_slug values (old display-name-style slugs, '(not set)',
# near-duplicates) into a single "(other)" group in the segment comparison
# below, instead of showing them as individual bars.
PREORDER_LP_SLUGS = ["home", "routine", "big-feelings", "screen-anxious", "preorder"]

_BAND_EMOJI = {"green": "🟢", "amber": "🟡", "red": "🔴", "gray": "⚪"}

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
# Cached DB calls — Preorder Funnel (v3)
# ---------------------------------------------------------------------------

@st.cache_data(ttl=300, show_spinner=False)
def _cached_funnel_steps(
    db_path_str: str, start: str, end: str, orders_valid_from: str = ""
) -> list[dict[str, Any]]:
    from pathlib import Path
    return db.get_preorder_funnel_steps(Path(db_path_str), start, end, orders_valid_from)


@st.cache_data(ttl=300, show_spinner=False)
def _cached_quiz_table(
    db_path_str: str, start: str, end: str, orders_valid_from: str = ""
) -> list[dict[str, Any]]:
    from pathlib import Path
    return db.get_quiz_funnel_table(Path(db_path_str), start, end, orders_valid_from)


@st.cache_data(ttl=300, show_spinner=False)
def _cached_lp_table(
    db_path_str: str, start: str, end: str, orders_valid_from: str = ""
) -> list[dict[str, Any]]:
    from pathlib import Path
    return db.get_landing_page_table(
        Path(db_path_str), start, end, orders_valid_from,
        canonical_slugs=QUIZ_LP_SLUGS + PREORDER_LP_SLUGS,
    )


@st.cache_data(ttl=300, show_spinner=False)
def _cached_click_gap(db_path_str: str, start: str, end: str) -> dict[str, Any]:
    from pathlib import Path
    return db.get_click_session_gap(Path(db_path_str), start, end)


@st.cache_data(ttl=300, show_spinner=False)
def _cached_not_set_share(db_path_str: str, start: str, end: str) -> dict[str, Any]:
    from pathlib import Path
    return db.get_ga4_not_set_share(Path(db_path_str), start, end)


@st.cache_data(ttl=300, show_spinner=False)
def _cached_quiz_funnel(db_path_str: str, start: str, end: str) -> dict[str, Any]:
    from pathlib import Path
    return db.get_quiz_funnel(Path(db_path_str), start, end, QUIZ_LP_SLUGS)


@st.cache_data(ttl=300, show_spinner=False)
def _cached_quiz_cpl(db_path_str: str, start: str, end: str) -> dict[str, Any]:
    from pathlib import Path
    return db.get_quiz_cost_per_lead(Path(db_path_str), start, end, QUIZ_LP_SLUGS)


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
# Preorder Funnel (v3) — new GA4-events + Meta-funnel-columns + Shopify-orders
# data layer. Rendered here, above everything else (including the legacy
# empty-state st.stop() below), so it always shows regardless of whether the
# legacy Stripe funnel has data.
# ---------------------------------------------------------------------------
st.subheader("Preorder Funnel (v3)")
source_line("M", "G", "Shop", note="every step reported straight from its own source, never combined")
st.caption(
    "Ads → Landing Page → GA4 Session → Reserve Click → Add to Cart → "
    "Begin Checkout → Order. Built on the new funnel-v3 data layer "
    "(ga4_events, ad_metrics funnel columns, shopify_orders) — may show "
    "\"n/a\" until ingestion has run."
)

_funnel_steps = _cached_funnel_steps(db_path_str, start_str, end_str, settings.orders_valid_from)

# Prior window = same length, immediately preceding the selected range, matching
# the Overview's "vs prior" convention. (The `prior_start`/`prior_end` further
# down this page are a fixed 14-day lookback for the legacy Stripe section and
# are deliberately not reused here.)
_fn_period_days = (end_date - start_date).days + 1
_fn_prior_end = start_date - timedelta(days=1)
_fn_prior_start = _fn_prior_end - timedelta(days=_fn_period_days - 1)
_funnel_steps_prior = _cached_funnel_steps(
    db_path_str, _fn_prior_start.isoformat(), _fn_prior_end.isoformat(),
    settings.orders_valid_from,
)
_click_gap = _cached_click_gap(db_path_str, start_str, end_str)
_not_set_share = _cached_not_set_share(db_path_str, start_str, end_str)
_quiz_funnel = _cached_quiz_funnel(db_path_str, start_str, end_str)
_quiz_cpl = _cached_quiz_cpl(db_path_str, start_str, end_str)

_v3_available_steps = [s for s in _funnel_steps if s["available"]]

if not _v3_available_steps:
    st.info(
        "📭 No funnel-v3 data ingested yet. This section populates once GA4 "
        "event ingestion, the Meta landing-page-view/checkout columns, and "
        "the Shopify orders ingest have run."
    )
else:
    # Rendered as a table rather than a horizontal bar chart. On a linear axis
    # Impressions (~55k) dwarfs Orders (~4), so every step below Landing-Page
    # Views collapsed into an invisible sliver and the axis auto-ranged away from
    # zero — the chart was unreadable for exactly the steps that matter. A table
    # shows each step's own number, its source, its step conversion and its
    # prior-period comparison, with the bar demoted to a proportional cue.
    _fn_prior_by_label = {
        s["label"]: s for s in _funnel_steps_prior if s.get("available")
    }
    _fn_max = max((s["value"] or 0) for s in _v3_available_steps) or 1

    def _fn_delta_cell(cur: int | None, prev: int | None) -> str:
        """Δ vs prior. More is better at every funnel step, so up is green."""
        if cur is None or prev is None or prev == 0:
            return '<span style="color:#8b8474">—</span>'
        pct = (cur - prev) / prev * 100.0
        color = "#2f6d4f" if pct >= 0 else "#a8402f"
        return f'<span style="color:{color};font-weight:600">{pct:+.0f}%</span>'

    _FN_CELL = "padding:7px 8px;border-bottom:1px solid rgba(128,128,128,.14);"
    _FN_MUTED = "color:#6e6552"
    _FN_NUM = "text-align:right;font-variant-numeric:tabular-nums"

    def _fn_td(inner: str, extra: str = "") -> str:
        return f'<td style="{_FN_CELL}{extra}">{inner}</td>'

    _fn_rows: list[str] = []
    _fn_prev_label: str | None = None
    for _s in _v3_available_steps:
        _val = _s["value"] or 0
        _conv = _s.get("conversion_pct")
        # Name the actual previous step instead of a generic "prev step" —
        # "70% of Clicks" is readable on its own, "70% of prev step" is not.
        _conv_txt = (
            f"{_conv:.1f}% of {_fn_prev_label}"
            if _conv is not None and _fn_prev_label
            else "—"
        )
        _prior_step = _fn_prior_by_label.get(_s["label"])
        _prior_val = _prior_step["value"] if _prior_step else None
        # Sub-1% bars would render as nothing; floor the width so the row still
        # reads as "a bar, just a tiny one" rather than as missing data.
        _bar_w = max(_val / _fn_max * 100.0, 0.6)
        _label_html = _s["label"] + (
            ' <span title="North Star metric">★</span>'
            if _s["label"] == "Begin Checkout" else ""
        )
        _fn_rows.append(
            "<tr>"
            + _fn_td(_label_html, "font-weight:600")
            + _fn_td(f"{_val:,}", f"{_FN_NUM};font-weight:700")
            + _fn_td(badge_html(_s["source"]), "text-align:center")
            + _fn_td(_conv_txt, f"text-align:right;{_FN_MUTED};font-size:12px")
            + _fn_td(
                f"{_prior_val:,}" if _prior_val is not None else "—",
                f"{_FN_NUM};{_FN_MUTED}",
            )
            + _fn_td(_fn_delta_cell(_val, _prior_val), "text-align:right")
            + _fn_td(
                '<span style="display:inline-block;height:14px;border-radius:3px;'
                f'background:rgba(99,125,255,.75);width:{_bar_w:.2f}%"></span>',
                "width:34%",
            )
            + "</tr>"
        )
        _fn_prev_label = _s["label"]

    _FN_TH = (
        "font-size:10.5px;text-transform:uppercase;letter-spacing:.06em;"
        "color:#6e6552;font-weight:700;padding:0 8px 6px;"
        "border-bottom:1px solid rgba(128,128,128,.28)"
    )

    def _fn_th(label: str, align: str = "right") -> str:
        return f'<th style="{_FN_TH};text-align:{align}">{label}</th>'

    st.markdown(
        '<table style="width:100%;border-collapse:collapse;font-size:13.5px">'
        "<thead><tr>"
        + _fn_th("Stage", "left")
        + _fn_th("Events")
        + _fn_th("Src", "center")
        + _fn_th("Step conv%")
        + _fn_th("Prior")
        + _fn_th("&Delta;")
        + _fn_th("Visual", "left")
        + "</tr></thead><tbody>"
        + "".join(_fn_rows)
        + "</tbody></table>",
        unsafe_allow_html=True,
    )
    st.caption(
        f"Prior period = {_fn_prior_start.isoformat()} → {_fn_prior_end.isoformat()} "
        f"({_fn_period_days}d, immediately before the selected range). Δ compares "
        "each step against itself, never against the step above it."
    )

    _v3_skipped = [s["label"] for s in _funnel_steps if not s["available"]]
    if _v3_skipped:
        st.caption("n/a — no data ingested yet: " + ", ".join(_v3_skipped))

    _orders_step = next((s for s in _funnel_steps if s["label"] == "Orders"), None)
    if _orders_step and _orders_step.get("note"):
        st.caption(f"ℹ️ {_orders_step['note']}")
    if settings.orders_valid_from:
        st.caption(
            f"ℹ️ Orders before **{settings.orders_valid_from}** (pre-launch/test "
            "orders) are excluded from Orders and per-segment order counts."
        )

    st.caption(
        "⚠️ **Directional, not blended.** Meta steps (Impressions / Clicks / "
        "Landing-Page Views) count platform-side ad delivery; GA4 and Shopify "
        "steps count on-site / order events — different scopes, shown "
        "side-by-side, never averaged (CLAUDE.md). Begin Checkout is inflated "
        "by an auto-redirect (the cart permalink lands directly on Shopify "
        "checkout), so Begin Checkout ≈ reserve-click intent, not deliberate "
        "checkout entry — expect it to sit close to Add to Cart rather than "
        "showing real funnel drop-off."
    )

    def _pct(v: float | None, dp: int = 1) -> str:
        """Rates render as text, not numbers: a row with no denominator has no rate
        at all, and Streamlit 1.60 prints NaN in a NumberColumn as the literal
        "None" whatever format is set. Defined here rather than inside either
        table's branch so the quiz table below does not depend on the
        landing-page table having rendered."""
        return f"{v:.{dp}f}%" if v is not None else "—"

    # --- By landing page ---------------------------------------------------
    # Replaces four per-segment mini bar charts. Those plotted Sessions beside
    # Orders on a shared axis, so every bar except Sessions was a stub, and a
    # reader had to hold four separate charts in their head to compare segments.
    # One table does the comparison directly and adds the Meta delivery columns
    # the charts could not show at all.
    st.markdown("**By landing page** — Meta delivery against on-site behaviour")
    source_line(
        "M", "G", "Shop",
        note="Spend/Clicks/CTR/CPM = Meta · LP views/CTA clicks = GA4 · Orders = Shopify",
    )

    _lp_rows = _cached_lp_table(db_path_str, start_str, end_str, settings.orders_valid_from)
    if not _lp_rows:
        st.caption(
            "No per-landing-page data yet — needs Meta ad-set rows plus ad "
            "creatives (for the destination URL) and GA4 lp_slug events."
        )
    else:
        _lp_df = pd.DataFrame([
            {
                "Landing page": db.segment_display_name(r["lp_slug"]),
                "Spend": r["spend"],
                "Impressions": r["impressions"],
                "Clicks": r["clicks"],
                "CTR %": _pct(r["ctr_pct"], 2),
                "Link CTR %": _pct(r["link_ctr_pct"], 2),
                "CPM": f"${r['cpm']:.2f}" if r["cpm"] is not None else "—",
                "Sessions": r["sessions"],
                "LP views": r["lp_views"],
                "CTA clicks": r["cta_clicks"],
                "% CTA": _pct(r["cta_pct"]),
                "→ /preorder": r["preorder_sessions"],
                "% → /preorder": _pct(r["preorder_pct"]),
                "Orders": r["orders"],
                "% Order": _pct(r["order_pct"], 2),
            }
            for r in _lp_rows
        ])
        st.dataframe(
            _lp_df,
            hide_index=True,
            use_container_width=True,
            column_config={
                "Landing page": st.column_config.TextColumn("Landing page", width="medium"),
                "Spend": st.column_config.NumberColumn("Spend", format="$%.2f"),
                "Impressions": st.column_config.NumberColumn("Impressions", format="%d"),
                "Clicks": st.column_config.NumberColumn("Clicks", format="%d"),
                "CTR %": st.column_config.TextColumn(
                    "CTR %", help="All clicks ÷ impressions (Meta) — includes reactions, "
                                  "comments and profile taps, not just link clicks."),
                "Link CTR %": st.column_config.TextColumn(
                    "Link CTR %", help="Link clicks ÷ impressions (Meta inline_link_clicks). "
                                       "Always lower than CTR; this is the one that reflects "
                                       "traffic actually sent to site. '—' means the date "
                                       "range predates this field being ingested."),
                "CPM": st.column_config.TextColumn("CPM"),
                "Sessions": st.column_config.NumberColumn(
                    "Sessions", format="%d",
                    help="GA4 sessions whose landing page was this one."),
                "LP views": st.column_config.NumberColumn(
                    "LP views", format="%d", help="GA4 page_view_lp events on this page."),
                "CTA clicks": st.column_config.NumberColumn(
                    "CTA clicks", format="%d",
                    help="GA4 cta_click_convert on this page — intent to advance, not a "
                         "checkout. Means different things per page; see note below."),
                "% CTA": st.column_config.TextColumn(
                    "% CTA", help="CTA clicks ÷ LP views. Above 100% means those two "
                                  "events disagree on that page — broken tracking, not a "
                                  "real rate. See note below."),
                "→ /preorder": st.column_config.NumberColumn(
                    "→ /preorder", format="%d",
                    help="GA4 sessions that started on this page and went on to view "
                         "/preorder."),
                "% → /preorder": st.column_config.TextColumn(
                    "% → /preorder",
                    help="Sessions reaching /preorder ÷ sessions on this page. Blank on "
                         "the /preorder row itself, where it would measure nothing."),
                "Orders": st.column_config.NumberColumn(
                    "Orders", format="%d", help="Shopify paid orders, last-touch."),
                "% Order": st.column_config.TextColumn(
                    "% Order", help="Orders ÷ LP views on this page."),
            },
        )
        _lp_spend_total = sum(r["spend"] for r in _lp_rows)
        st.caption(
            f"Totals — spend **${_lp_spend_total:,.2f}** · clicks "
            f"**{sum(r['clicks'] for r in _lp_rows):,}** · LP views "
            f"**{sum(r['lp_views'] for r in _lp_rows):,}** · orders "
            f"**{sum(r['orders'] for r in _lp_rows):,}**. Spend reconciles with the "
            "Total Spend KPI because it is summed from ad-set rows and attributed by "
            "each ad-set's creative destination URL."
        )
        # A rate over 100% is not a rendering bug — it is two GA4 events that do not
        # agree about which page they belong to. Surfaced by name rather than
        # capped, because the fix is a tracking fix and hiding it loses the signal.
        _cta_broken = [
            db.segment_display_name(r["lp_slug"])
            for r in _lp_rows
            if r["cta_pct"] is not None and r["cta_pct"] > 100
        ]

        st.caption(
            "**CTA clicks** = the GA4 `cta_click_convert` event fired on that page. "
            "It is an *intent-to-advance* click, not a checkout: on `/`, `/for/*` and "
            "the quiz pages it means \"clicked through toward the offer\"; only on "
            "`/preorder` does it mean \"left for Shopify\". So the column is **not "
            "comparable across rows** — a click on a segment page and a click on the "
            "offer page are different actions that happen to share an event name."
        )
        if _cta_broken:
            st.caption(
                f"⚠️ **% CTA is above 100% on: {', '.join(_cta_broken)}.** That is not a "
                "real conversion rate — on those pages `cta_click_convert` carries the "
                "`lp_slug` dimension but `page_view_lp` largely does not, so the "
                "numerator counts a page the denominator has barely heard of. Treat "
                "those rows' **% CTA** as broken tracking to fix, not as performance. "
                "The raw **CTA clicks** count is still usable."
            )
        st.caption(
            "**Sessions** vs **LP views**: sessions are GA4 session-scoped (one per "
            "visit that started on this page); LP views are `page_view_lp` events, so a "
            "visitor who reloads counts twice. **% CTA** and **% Order** divide by LP "
            "views (event ÷ event); **% → /preorder** divides by sessions (session ÷ "
            "session) — each rate keeps one scope on both sides. "
            "**Orders** are Shopify last-touch, so a visitor who arrived via a segment "
            "page but checked out from the homepage counts on the homepage row — which "
            "is why the segment rows can show 0 orders while still feeding sales."
        )
        st.caption(
            "Add-to-Cart and Begin-Checkout are deliberately absent: they fire on "
            "Shopify's domain, so most arrive with no landing-page dimension "
            "(`(not set)`) and any per-page split of them would be mostly invented. "
            "They are shown as totals in the funnel table above."
        )

    # --- Quiz funnel ------------------------------------------------------
    # Split out of the landing-page table because a quiz page's job is two hops,
    # not one: it hands off to its own segment LP and only then to the offer page.
    # In the combined table those rows looked like weak landing pages; they are
    # actually the first step of a longer path.
    st.markdown("**Quiz funnel** — quiz page → its segment LP → `/preorder`")
    source_line(
        "G", "Sheet", "Shop",
        note="Sessions = GA4 page flow · Leads = Preorder Leads sheet · Orders = Shopify",
    )

    _quiz_rows = _cached_quiz_table(
        db_path_str, start_str, end_str, settings.orders_valid_from
    )
    if not _quiz_rows or not any(r["sessions"] for r in _quiz_rows):
        st.caption(
            "No quiz-page session flow for this range — needs the GA4 page-flow "
            "ingest to have run (`ga4_page_flow`)."
        )
    else:
        _q_df = pd.DataFrame([
            {
                "Quiz page": r["quiz_slug"],
                "Segment LP": r["segment_slug"],
                "Quiz sessions": r["sessions"],
                "→ segment LP": r["to_segment"],
                "% → segment": _pct(r["to_segment_pct"]),
                "→ /preorder": r["to_preorder"],
                "% → /preorder": _pct(r["to_preorder_pct"]),
                "Email leads": r["leads"],
                "Orders": r["orders"],
            }
            for r in _quiz_rows
        ])
        st.dataframe(
            _q_df,
            hide_index=True,
            use_container_width=True,
            column_config={
                "Quiz page": st.column_config.TextColumn("Quiz page", width="medium"),
                "Segment LP": st.column_config.TextColumn("Segment LP", width="small"),
                "Quiz sessions": st.column_config.NumberColumn(
                    "Quiz sessions", format="%d",
                    help="GA4 sessions whose landing page was this quiz page."),
                "→ segment LP": st.column_config.NumberColumn(
                    "→ segment LP", format="%d",
                    help="Of those sessions, how many went on to view the segment LP."),
                "% → segment": st.column_config.TextColumn("% → segment"),
                "→ /preorder": st.column_config.NumberColumn(
                    "→ /preorder", format="%d",
                    help="Of those sessions, how many reached /preorder — by any route, "
                         "not necessarily via the segment LP."),
                "% → /preorder": st.column_config.TextColumn("% → /preorder"),
                "Email leads": st.column_config.NumberColumn(
                    "Email leads", format="%d",
                    help="Leads on the sheet whose QUIZNAME is this segment. Counted by "
                         "segment, not by entry page — see note below."),
                "Orders": st.column_config.NumberColumn(
                    "Orders", format="%d",
                    help="Shopify paid orders last-touched to the segment LP."),
            },
        )
        st.caption(
            "**Reading the two hops.** `% → segment` is how well the quiz hands off to "
            "its own segment page; `% → /preorder` is how many of the same sessions "
            "reached the offer page by any route. Both share one denominator (quiz "
            "sessions), so they are directly comparable — and `→ /preorder` is not a "
            "subset of `→ segment LP`, since a session can jump straight to the offer."
        )
        _leads_exceed = [
            r["quiz_slug"] for r in _quiz_rows if r["leads"] > r["sessions"] > 0
        ]
        if _leads_exceed:
            st.caption(
                f"ℹ️ **Email leads exceed quiz sessions on: {', '.join(_leads_exceed)}** — "
                "that is expected, not an error. The quiz is reachable from the segment "
                "LP as well as from its own landing page, and leads are counted per "
                "*segment* (the sheet's QUIZNAME) regardless of where the visitor "
                "entered. So leads here are **not** a conversion rate on the sessions "
                "column; read the two independently."
            )
        st.caption(
            "**Orders are Shopify last-touch on the segment LP**, so a quiz lead who "
            "later bought from the homepage counts on the homepage row of the table "
            "above, not here. Zero orders on a quiz row does not mean the quiz "
            "produced no revenue — it means no purchase last-touched that segment page."
        )

st.markdown("**Click → Session Gap**")
source_line("M", "G", note="split into capture gap vs attribution gap — never combined")
st.caption(
    "4-step decomposition: Meta Clicks → Meta LPV → GA4 Sessions (all traffic) → "
    "Campaign-Attributed Sessions. Two separately-labeled gaps below — never "
    "blended into one number (D-11 fix)."
)


def _band_chip_text(pct: float | None, band: str) -> str:
    emoji = _BAND_EMOJI[band]
    return f"{emoji} {pct:.0f}%" if pct is not None else f"{emoji} n/a"


_g1, _g2, _g3, _g4 = st.columns(4)
_g1.metric(
    "Meta Clicks",
    f"{_click_gap['meta_clicks']:,}" if _click_gap["meta_clicks"] is not None else "n/a",
)
_g2.metric(
    "Meta LPV",
    f"{_click_gap['meta_lpv']:,}" if _click_gap["meta_lpv"] is not None else "n/a",
)
_g3.metric(
    "GA4 Sessions (all)",
    f"{_click_gap['ga4_sessions_all']:,}" if _click_gap["ga4_sessions_all"] is not None else "n/a",
    help="All GA4 sessions from ga4_landing_pages — no campaign filter, includes "
         "'(not set)' / untagged traffic.",
)
_g4.metric(
    "Campaign-Attributed Sessions",
    f"{_click_gap['ga4_sessions_attributed']:,}"
    if _click_gap["ga4_sessions_attributed"] is not None else "n/a",
    help="GA4 sessions from ga4_metrics — excludes '(not set)' campaign rows.",
)

_h1, _h2 = st.columns(2)
_capture_band = db.capture_gap_band(_click_gap["capture_gap_pct"])
_attribution_band = db.attribution_gap_band(_click_gap["attribution_gap_pct"])
_h1.metric(
    "Capture Gap (LPV → GA4 Sessions)",
    _band_chip_text(_click_gap["capture_gap_pct"], _capture_band),
    help="Consent/tracking loss — visits Meta counted that never fired a GA4 "
         "session at all.",
)
_h2.metric(
    "Attribution Gap (Sessions → Campaign-Attributed)",
    _band_chip_text(_click_gap["attribution_gap_pct"], _attribution_band),
    help="Of the sessions GA4 did track, the share it couldn't tie to a "
         "campaign — driven by consent-denied + untagged traffic.",
)

with st.expander("Why is there a gap between Clicks / LPV and GA4 Sessions?"):
    st.markdown(
        "This gap has **two distinct causes** that used to be blended into one "
        "number — they are now reported separately:\n\n"
        "- **Capture gap** (LPV → GA4 Sessions, all traffic) — visitors who "
        "decline analytics consent never fire a GA4 session at all, and "
        "in-app browsers / ad blockers / slow tag load / server 503s can drop "
        "the GA4 hit entirely, even though Meta still counted the click/LPV. "
        "Bands: 🟢 ≤30% normal · 🟡 30–50% watch · 🔴 >50% investigate consent "
        "rate, tag latency, transport failures.\n"
        "- **Attribution gap** (GA4 Sessions, all → Campaign-Attributed) — of "
        "the sessions GA4 *did* track, some fire without a campaign-carrying "
        "hit (a consent-denied session can still register a bare pageview) "
        "or arrive genuinely untagged/organic-looking. Bands: 🟢 ≤40% normal · "
        "🟡 40–70% watch · 🔴 >70% investigate utm tagging discipline.\n\n"
        "Reporting these as one blended gap masked which failure mode was "
        "actually driving the loss — a campaign-attributed-only 'GA4 Sessions' "
        "figure will always understate real traffic by however much sits in "
        "'(not set)', even when tracking itself is perfectly healthy."
    )

st.markdown("**Checkout-events \"(not set)\" share**")
_ns_band = db.not_set_share_band(_not_set_share["share_pct"])
_ns_text = (
    f"{_not_set_share['share_pct']:.0f}%" if _not_set_share["share_pct"] is not None else "n/a"
)
st.metric(
    "(not set) share of add_to_cart + begin_checkout + purchase",
    f"{_BAND_EMOJI[_ns_band]} {_ns_text}",
    help=(
        "checkout events GA4 couldn't tie to a campaign — utm forwarding + "
        "tagging discipline"
    ),
)

st.markdown("**Quiz Funnel**")
source_line("G", note="Cost per Lead below blends in Meta spend")
st.caption(f"Landing pages: {', '.join(QUIZ_LP_SLUGS)}")
_quiz_has_data = any(v["available"] for v in _quiz_funnel.values())
if not _quiz_has_data:
    st.info("n/a — no quiz-funnel data ingested yet for these landing pages.")
else:
    _q1, _q2, _q3, _q4 = st.columns(4)
    _q1.metric(
        "Page Views",
        f"{_quiz_funnel['page_view_lp']['count']:,}"
        if _quiz_funnel["page_view_lp"]["available"] else "n/a",
    )
    _q2.metric(
        "Quiz Complete",
        f"{_quiz_funnel['quiz_complete']['count']:,}"
        if _quiz_funnel["quiz_complete"]["available"] else "n/a",
    )
    _q3.metric(
        "Lead Submit",
        f"{_quiz_funnel['lead_submit']['count']:,}"
        if _quiz_funnel["lead_submit"]["available"] else "n/a",
    )
    _q4.metric(
        "Cost per Lead",
        f"${_quiz_cpl['cpl']:.2f}" if _quiz_cpl["cpl"] is not None else "—",
    )
    st.caption(
        f"CPL = Meta spend from campaigns named 'LEADS' "
        f"(${_quiz_cpl['spend']:.2f} across {_quiz_cpl['leads_campaign_count']} "
        "campaign(s)) ÷ lead_submit count. Approximation: LEADS-named "
        "campaigns may include non-quiz lead campaigns, so treat CPL here as "
        "directional rather than an exact quiz-only figure."
    )

st.divider()
st.caption(
    "⬇️ Legacy deposit-funnel sections below track the original $1 Stripe "
    "pre-order flow (form_submit_deposit → paid) and remain fully functional "
    "independent of the funnel-v3 data above."
)

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
source_line("Sheet", "M", note="legacy $1-deposit era — Paid comes from the Google Sheet, CPR/CPaC blend in Meta spend")
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
        st.plotly_chart(_make_two_gate_segment_chart(source_rows), use_container_width=True, theme=None)

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
        source_line("M", "G")
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
        st.plotly_chart(fig_track, use_container_width=True, theme=None)

st.divider()

# ---------------------------------------------------------------------------
# Section 1 — Daily FSD vs Paid Trend
# ---------------------------------------------------------------------------
st.subheader("Daily Form Submits vs. Paid Conversions")
source_line("Sheet", note="legacy $1-deposit era, from the Google Sheet")

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

    st.plotly_chart(fig, use_container_width=True, theme=None)

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
source_line("Sheet")
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
source_line("M", "G", note="GA4 Sessions joined by campaign name — same fallback matching as Campaign Detail")
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
    st.plotly_chart(fig_f, use_container_width=True, theme=None)

    # Funnel Health table
    st.subheader("Funnel Health Table")
    source_line("M", "G")
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
source_line("G", "Sheet", note="GA4 engagement joined to the Sheet-sourced paid count")
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
    st.plotly_chart(fig_lp, use_container_width=True, theme=None)
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
source_line("M")
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
    st.plotly_chart(fig_rf, use_container_width=True, theme=None)
else:
    st.info("No Meta spend data in this date range.")

# ---------------------------------------------------------------------------
# Section 6 — Email Leads Mini-Funnel (placeholder)
# ---------------------------------------------------------------------------
import os as _os  # noqa: E402 — stdlib, safe in standalone page

st.divider()
st.subheader("Email Leads Funnel")
source_line("Sheet", note="separate, unconfigured sheet — placeholder, no backend table yet")

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
