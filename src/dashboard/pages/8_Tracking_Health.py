"""Tracking Health page (Phase C) — pixel stats, GA4 event anomalies, freshness.

Monitors whether Nowa's Meta + GA4 + Shopify tracking pipeline itself is
healthy, distinct from whether campaigns are performing well. Every query
degrades to a friendly "no data yet" state when the underlying tables are
empty or missing (production tables may be empty until ingestion runs).

Standalone: no aiogram / no src.bot / no src.ai imports (Phase 6 D-19 rule).
Auth gate and palette duplicated from app.py per D-19.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import plotly.graph_objects as go
import streamlit as st

st.set_page_config(
    page_title="Tracking Health",
    layout="wide",
    initial_sidebar_state="expanded",
)

from src.alerts.anomaly import CRITICAL_EVENTS, find_anomalies_in_range  # noqa: E402
from src.dashboard import db                                             # noqa: E402
from src.dashboard.components import (                                   # noqa: E402
    compute_gap_pct,
    gap_chip_color,
    render_scope_line,
)
from src.dashboard.settings import DashboardSettings                     # noqa: E402
from src.dashboard.tracking_health import (                              # noqa: E402
    chip_emoji,
    click_session_ratio_color,
    freshness_color,
    not_set_share_color,
)

# ---------------------------------------------------------------------------
# Dark-theme palette — duplicated from app.py per D-19 standalone rule
# ---------------------------------------------------------------------------
COLOR_BG_PAPER = "#0f1117"
COLOR_BG_PLOT = "#1a1d27"
COLOR_FONT = "#e4e7ef"
COLOR_GRID = "#2a2e3a"

# One line color per critical event on the trend chart.
_EVENT_LINE_COLOR = {
    "begin_checkout": "#60a5fa",
    "lead_submit": "#a78bfa",
    "purchase": "#34d399",
}
_ANOMALY_MARKER_COLOR = "#f87171"

_PRIMARY_CHECKOUT_EVENT = "begin_checkout"  # used for the "(not set)" chip

settings = DashboardSettings()

# ---------------------------------------------------------------------------
# Auth gate — copied from 3_Funnel.py pattern (D-21)
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

st.title("Tracking Health")
st.caption("Is the tracking pipeline itself healthy? Campaign performance lives on other pages.")

# ---------------------------------------------------------------------------
# Cached DB calls (D-14 pattern)
# ---------------------------------------------------------------------------

@st.cache_data(ttl=300, show_spinner=False)
def _cached_click_session_ratio(db_path_str: str, start: str, end: str) -> float | None:
    from pathlib import Path
    return db.get_click_session_ratio(Path(db_path_str), start, end)


@st.cache_data(ttl=300, show_spinner=False)
def _cached_purchase_divergence(db_path_str: str, start: str, end: str) -> dict[str, Any]:
    from pathlib import Path
    return db.get_purchase_divergence(Path(db_path_str), start, end)


@st.cache_data(ttl=300, show_spinner=False)
def _cached_not_set_share(db_path_str: str, event_name: str, start: str, end: str) -> float | None:
    from pathlib import Path
    return db.get_not_set_campaign_share(Path(db_path_str), event_name, start, end)


@st.cache_data(ttl=300, show_spinner=False)
def _cached_event_freshness(db_path_str: str, event_names: tuple[str, ...]) -> dict[str, float | None]:
    from pathlib import Path
    return db.get_event_freshness_hours(Path(db_path_str), list(event_names))


@st.cache_data(ttl=300, show_spinner=False)
def _cached_event_daily(db_path_str: str, event_name: str, start: str, end: str) -> list[dict[str, Any]]:
    from pathlib import Path
    return db.get_event_daily_counts(Path(db_path_str), event_name, start, end)


@st.cache_data(ttl=300, show_spinner=False)
def _cached_sessions_daily(db_path_str: str, start: str, end: str) -> list[dict[str, Any]]:
    from pathlib import Path
    return db.get_sessions_daily(Path(db_path_str), start, end)


@st.cache_data(ttl=300, show_spinner=False)
def _cached_pixel_health(db_path_str: str, start: str, end: str) -> list[dict[str, Any]]:
    from pathlib import Path
    return db.get_pixel_health(Path(db_path_str), start, end)


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("Filters")
    today = date.today()
    default_end = today - timedelta(days=1)
    default_start = default_end - timedelta(days=29)  # 30-day trend window

    dates = st.date_input(
        "Trend chart date range",
        value=(default_start, default_end),
        max_value=today,
        key="tracking_health_date_range",
    )
    if isinstance(dates, tuple) and len(dates) == 2:
        start_date, end_date = dates
    else:
        st.warning("Pick both a start and an end date.")
        st.stop()

    st.divider()
    if st.button("Refresh", use_container_width=True):
        st.cache_data.clear()

db_path_str = str(settings.db_path)
start_str = start_date.isoformat()
end_str = end_date.isoformat()

# Status-strip chips always look at a fixed trailing 7 days, independent of the
# trend-chart range picker above — "is tracking healthy right now" should not
# move when someone widens the chart to look at history.
chip_end = today - timedelta(days=1)
chip_start = chip_end - timedelta(days=6)
chip_start_str, chip_end_str = chip_start.isoformat(), chip_end.isoformat()

render_scope_line(start_date, end_date, campaign_filter="All")

# ---------------------------------------------------------------------------
# Status strip
# ---------------------------------------------------------------------------
st.subheader("Status")

ratio_pct = _cached_click_session_ratio(db_path_str, chip_start_str, chip_end_str)
divergence = _cached_purchase_divergence(db_path_str, chip_start_str, chip_end_str)
not_set_pct = _cached_not_set_share(
    db_path_str, _PRIMARY_CHECKOUT_EVENT, chip_start_str, chip_end_str
)
freshness = _cached_event_freshness(db_path_str, tuple(CRITICAL_EVENTS))

chip_cols = st.columns(4 + len(CRITICAL_EVENTS))

with chip_cols[0]:
    color = click_session_ratio_color(ratio_pct)
    ratio_text = f"{ratio_pct:.0f}%" if ratio_pct is not None else "—"
    st.metric("Click → Session (7d, all traffic)", f"{chip_emoji(color)} {ratio_text}")
    st.caption(
        "Meta clicks vs ALL GA4 sessions (ga4_landing_pages, incl. '(not set)') — "
        "capture rate, not campaign attribution"
    )

with chip_cols[1]:
    not_set_color = not_set_share_color(not_set_pct)
    not_set_text = f"{not_set_pct:.0f}%" if not_set_pct is not None else "—"
    st.metric("(not set) on checkout", f"{chip_emoji(not_set_color)} {not_set_text}")
    st.caption(f"{_PRIMARY_CHECKOUT_EVENT} campaign_utm missing")

with chip_cols[2]:
    meta_p = divergence.get("meta_purchases", 0)
    ga4_p = divergence.get("ga4_purchases", 0)
    gap = compute_gap_pct(meta_p, ga4_p)
    gap_color = gap_chip_color(gap)
    gap_text = f"{gap:.0f}%" if gap is not None else "—"
    st.metric("Meta vs GA4 purchases", f"{chip_emoji(gap_color)} {gap_text}")
    st.caption(f"Meta {meta_p:,} · GA4 {ga4_p:,} (never blended)")

for i, event_name in enumerate(CRITICAL_EVENTS):
    with chip_cols[3 + i]:
        hours = freshness.get(event_name)
        f_color = freshness_color(hours)
        hours_text = f"{hours:.0f}h" if hours is not None else "—"
        st.metric(f"{event_name} freshness", f"{chip_emoji(f_color)} {hours_text}")
        st.caption("since last ingest")

st.divider()

# ---------------------------------------------------------------------------
# Per-event volume trend chart with anomaly markers
# ---------------------------------------------------------------------------
st.subheader("Event volume trend")

sessions_rows = _cached_sessions_daily(db_path_str, start_str, end_str)
sessions_by_date = {r["date"]: float(r["sessions"] or 0) for r in sessions_rows}

any_event_data = False
fig = go.Figure()

for event_name in CRITICAL_EVENTS:
    event_rows = _cached_event_daily(db_path_str, event_name, start_str, end_str)
    if not event_rows:
        continue
    any_event_data = True
    counts_by_date = {r["date"]: float(r["event_count"] or 0) for r in event_rows}
    dates_sorted = sorted(counts_by_date.keys())
    values = [counts_by_date[d] for d in dates_sorted]

    fig.add_trace(
        go.Scatter(
            x=dates_sorted,
            y=values,
            mode="lines+markers",
            name=event_name,
            line=dict(color=_EVENT_LINE_COLOR.get(event_name, COLOR_FONT)),
        )
    )

    anomalies = find_anomalies_in_range(event_name, counts_by_date, sessions_by_date)
    if anomalies:
        fig.add_trace(
            go.Scatter(
                x=[a["date"] for a in anomalies],
                y=[a["event_count"] for a in anomalies],
                mode="markers",
                name=f"{event_name} anomaly",
                marker=dict(
                    color=_ANOMALY_MARKER_COLOR, size=14, symbol="x", line=dict(width=2)
                ),
                text=[
                    f"-{a['count_drop_pct']:.0f}% vs median, sessions {a['sessions_drop_pct']:.0f}%"
                    for a in anomalies
                ],
                hoverinfo="text+x",
            )
        )

if any_event_data:
    fig.update_layout(
        paper_bgcolor=COLOR_BG_PAPER,
        plot_bgcolor=COLOR_BG_PLOT,
        font=dict(color=COLOR_FONT),
        xaxis=dict(gridcolor=COLOR_GRID),
        yaxis=dict(gridcolor=COLOR_GRID, title="Daily event count"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        margin=dict(l=10, r=10, t=40, b=10),
        height=420,
    )
    st.plotly_chart(fig, use_container_width=True)
    st.caption(
        "X markers = anomaly: event count dropped >50% vs its trailing 7-day median "
        "while sessions dropped <20% over the same window — that asymmetry points at "
        "broken tracking, not a real traffic dip."
    )
else:
    st.info(
        "No GA4 event data yet for begin_checkout / lead_submit / purchase in this range. "
        "The daily backfill ingests ga4_events once GA4_PROPERTY_ID is configured. "
        "You can also run: `python -m src.daily_backfill` to populate."
    )

st.divider()

# ---------------------------------------------------------------------------
# Pixel health table
# ---------------------------------------------------------------------------
st.subheader("Pixel health")

pixel_rows = _cached_pixel_health(db_path_str, start_str, end_str)
if not pixel_rows:
    st.info(
        "No pixel_health data yet. Set META_PIXEL_ID to enable per-event browser/server "
        "counts from Meta's /stats endpoint (7-day retention on Meta's side, so only "
        "recent days will ever be available). Run: `python -m src.daily_backfill` "
        "once configured."
    )
else:
    table_rows = []
    emq_available = any(r.get("emq_score") is not None for r in pixel_rows)
    for r in pixel_rows:
        table_rows.append(
            {
                "Event": r["event_name"],
                "Browser": int(r.get("browser_count") or 0),
                "Server": int(r.get("server_count") or 0),
                "Dedup rate": (
                    f"{r['dedup_rate']:.0%}" if r.get("dedup_rate") is not None else "—"
                ),
                "EMQ score": (
                    f"{r['emq_score']:.1f}" if r.get("emq_score") is not None else "n/a"
                ),
            }
        )
    st.dataframe(table_rows, use_container_width=True, hide_index=True)
    if not emq_available:
        st.caption(
            "EMQ score: n/a — not exposed via the standard Meta API access this "
            "dashboard uses (see src/meta/client.py fetch_pixel_emq for the research "
            "finding). A manual / Playwright-based filler can populate "
            "pixel_health.emq_score directly once available."
        )

st.divider()

# ---------------------------------------------------------------------------
# Runbook
# ---------------------------------------------------------------------------
with st.expander("Runbook — what to do per red chip"):
    st.markdown(
        """
- **🔴 Click → Session ratio** — check the consent banner isn't blocking GA4
  before consent; confirm server-side GTM's `/g/collect` endpoint is returning
  200s; verify ad destination URLs still carry the expected UTM tags.
- **🔴 (not set) share on checkout** — audit `utm_content` tagging discipline on
  ad creatives; check whether the server-side purchase event (Shopify webhook →
  CAPI) is dropping the UTM params that the browser-side event still has.
- **🔴 GA4 vs Meta purchase divergence** — confirm this is attribution-window
  drift (normal, see the Overview page's "Why don't these match?" note) and not
  a broken Conversions API or a stalled GA4 property; check both ingestion logs.
- **🔴 Event freshness (begin_checkout / lead_submit / purchase)** — check the
  GTM container version hasn't been rolled back; confirm the site's latest
  deploy didn't strip the dataLayer push; check GA4 DebugView for live events.
- **X anomaly markers on the trend chart** — same checklist as freshness above,
  but for a single day rather than an ongoing gap — look for a deploy or GTM
  publish around that date.
        """
    )
