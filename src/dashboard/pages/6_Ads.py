"""Ad Creative Analysis page (D-19 standalone, no asyncio, no src.ai imports).

Shows top-performing ads, creative fatigue watch, format breakdown, and style breakdown.
All data sourced from ad_metrics (ad-level rows) joined with ad_creatives.

Standalone page — no bot framework imports (D-19 rule). Palette constants
are re-declared inline, never imported from app.py.
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# st.set_page_config MUST be the first Streamlit call.
st.set_page_config(
    page_title="Ad Creative Analysis",
    layout="wide",
    initial_sidebar_state="expanded",
)

from src.dashboard import db                            # noqa: E402
from src.dashboard.settings import DashboardSettings    # noqa: E402

# --- Dark-theme palette — duplicated from app.py per D-19 standalone rule ---
COLOR_BG_PAPER = "#0f1117"
COLOR_BG_PLOT = "#1a1d27"
COLOR_FONT = "#e4e7ef"
COLOR_GRID = "#2a2e3a"
COLOR_META = "#60a5fa"
COLOR_DEPOSITS = "#34d399"
COLOR_CPD = "#f59e0b"

COLOR_FMT_IMAGE = "#60a5fa"
COLOR_FMT_VIDEO = "#34d399"
COLOR_FMT_CAROUSEL = "#f59e0b"
COLOR_FMT_DEFAULT = "#a78bfa"

# Format → color map
_FORMAT_COLORS: dict[str, str] = {
    "image": COLOR_FMT_IMAGE,
    "video": COLOR_FMT_VIDEO,
    "carousel": COLOR_FMT_CAROUSEL,
}


# ---------------------------------------------------------------------------
# Auth gate — duplicated per D-19 (each page is its own script)
# ---------------------------------------------------------------------------
def _check_auth(password_required: str) -> bool:
    if not password_required:
        return True
    if st.session_state.get("authenticated"):
        return True
    st.title("Ad Creative Analysis")
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
# Cached DB calls (ttl=300 per D-14 pattern)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=300, show_spinner=False)
def _cached_top_ads(
    db_path_str: str, start: str, end: str, limit: int = 10
) -> list[dict[str, Any]]:
    return db.get_top_ads(Path(db_path_str), start, end, limit)


@st.cache_data(ttl=300, show_spinner=False)
def _cached_fatigue_ads(
    db_path_str: str, start: str, end: str
) -> list[dict[str, Any]]:
    return db.get_fatigue_ads(Path(db_path_str), start, end)


@st.cache_data(ttl=300, show_spinner=False)
def _cached_format_breakdown(
    db_path_str: str, start: str, end: str
) -> list[dict[str, Any]]:
    return db.get_ad_format_breakdown(Path(db_path_str), start, end)


@st.cache_data(ttl=300, show_spinner=False)
def _cached_style_breakdown(
    db_path_str: str, start: str, end: str
) -> list[dict[str, Any]]:
    return db.get_ad_style_breakdown(Path(db_path_str), start, end)


# ---------------------------------------------------------------------------
# Insight helpers
# ---------------------------------------------------------------------------
def _ad_insight(fsd: int, avg_ctr: float, avg_frequency: float) -> str:
    """Rule-based insight tag for a top-ad row."""
    fsd = int(fsd or 0)
    avg_ctr = float(avg_ctr or 0)
    avg_frequency = float(avg_frequency or 0)
    if fsd >= 5 and avg_frequency > 2 and avg_ctr >= 1.0:
        return "Scale budget but monitor fatigue"
    if fsd >= 5 and avg_ctr >= 1.0:
        return "Scale — strong FSD + CTR"
    if fsd >= 3 and avg_ctr < 0.5:
        return "Test new hook — low CTR despite conversions"
    if fsd >= 5 and avg_frequency > 2:
        return "Scale budget but monitor fatigue"
    return "Monitor"


def _fatigue_label(avg_frequency: float) -> str:
    """Fatigue urgency label."""
    f = float(avg_frequency or 0)
    if f >= 3.5:
        return "Refresh creative immediately — severe fatigue"
    if f >= 3.0:
        return "Prepare new creative — approaching burnout"
    return "Watch closely — early fatigue signal"


# ---------------------------------------------------------------------------
# Chart builders
# ---------------------------------------------------------------------------
def _make_format_chart(rows: list[dict[str, Any]]) -> go.Figure:
    """Horizontal bar chart: spend by format with avg_ctr annotation."""
    formats = [r["ad_format"] for r in rows]
    spends = [float(r["spend"] or 0) for r in rows]
    ctrs = [float(r["avg_ctr"] or 0) for r in rows]
    colors = [_FORMAT_COLORS.get(str(f).lower(), COLOR_FMT_DEFAULT) for f in formats]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=spends,
        y=formats,
        orientation="h",
        marker_color=colors,
        text=[f"${s:,.0f}  CTR {c:.2f}%" for s, c in zip(spends, ctrs)],
        textposition="inside",
        insidetextanchor="start",
        name="Spend",
    ))
    fig.update_layout(
        plot_bgcolor=COLOR_BG_PLOT,
        paper_bgcolor=COLOR_BG_PAPER,
        font=dict(color=COLOR_FONT, size=12),
        xaxis=dict(title="Spend ($)", gridcolor=COLOR_GRID),
        yaxis=dict(title="", gridcolor=COLOR_GRID),
        margin=dict(l=20, r=20, t=20, b=30),
        height=220,
        showlegend=False,
    )
    return fig


def _make_style_chart(rows: list[dict[str, Any]]) -> go.Figure:
    """Horizontal bar chart: FSD by style with avg_ctr annotation."""
    styles = [r["ad_style"] for r in rows]
    fsds = [int(r["fsd"] or 0) for r in rows]
    ctrs = [float(r["avg_ctr"] or 0) for r in rows]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=fsds,
        y=styles,
        orientation="h",
        marker_color=COLOR_DEPOSITS,
        text=[f"{f} FSD  CTR {c:.2f}%" for f, c in zip(fsds, ctrs)],
        textposition="inside",
        insidetextanchor="start",
        name="FSD",
    ))
    fig.update_layout(
        plot_bgcolor=COLOR_BG_PLOT,
        paper_bgcolor=COLOR_BG_PAPER,
        font=dict(color=COLOR_FONT, size=12),
        xaxis=dict(title="FSD (form submit deposit)", gridcolor=COLOR_GRID),
        yaxis=dict(title="", gridcolor=COLOR_GRID),
        margin=dict(l=20, r=20, t=20, b=30),
        height=max(220, len(styles) * 40),
        showlegend=False,
    )
    return fig


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
settings = DashboardSettings()  # type: ignore[call-arg]

if not _check_auth(settings.dashboard_password):
    st.stop()

st.title("Ad Creative Analysis")
st.caption("Ad-level performance by format and style. Requires daily backfill with ad-level insights.")

db_path = settings.db_path
if not db_path.exists():
    st.error(
        f"Database not found at `{db_path}`. "
        "Run the bot once to ingest data, or set DB_PATH in your .env file."
    )
    st.stop()

db_path_str = str(db_path)

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("Filters")
    today = date.today()
    default_end = today - timedelta(days=1)
    default_start = default_end - timedelta(days=13)  # 14-day default

    if "ads_date_range" not in st.session_state:
        st.session_state.ads_date_range = (default_start, default_end)

    col_a, col_b = st.columns(2)
    if col_a.button("Last 14 days", use_container_width=True):
        st.session_state.ads_date_range = (default_end - timedelta(days=13), default_end)
    if col_b.button("Last 30 days", use_container_width=True):
        st.session_state.ads_date_range = (default_end - timedelta(days=29), default_end)

    dates = st.date_input(
        "Date range",
        value=st.session_state.ads_date_range,
        max_value=today,
        key="ads_date_range_picker",
    )
    if isinstance(dates, tuple) and len(dates) == 2:
        start_date, end_date = dates
        st.session_state.ads_date_range = (start_date, end_date)
    else:
        st.warning("Pick both a start and an end date.")
        st.stop()

    if st.button("Refresh data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    st.divider()

    # Format and style filters — populated after data load
    format_filter: list[str] = []
    style_filter: list[str] = []

start_iso = start_date.isoformat()
end_iso = end_date.isoformat()

# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------
top_ads = _cached_top_ads(db_path_str, start_iso, end_iso, limit=20)
fatigue_ads = _cached_fatigue_ads(db_path_str, start_iso, end_iso)
format_rows = _cached_format_breakdown(db_path_str, start_iso, end_iso)
style_rows = _cached_style_breakdown(db_path_str, start_iso, end_iso)

# Check if any ad-level data exists at all
_has_data = bool(top_ads or fatigue_ads or format_rows or style_rows)

if not _has_data:
    st.info(
        "No ad-level data has been fetched yet.\n\n"
        "To populate this page, run the backfill script:\n\n"
        "```\n"
        "python scripts/backfill_ad_creatives.py\n"
        "```\n\n"
        "Or wait for the nightly backfill job to run (03:00 daily). "
        "The job fetches ad creative metadata and ad-level insights automatically."
    )
    st.stop()

# Build sidebar filters from available data
_all_formats = sorted({r["ad_format"] for r in (top_ads + format_rows) if r.get("ad_format")})
_all_styles = sorted({r["ad_style"] for r in (top_ads + style_rows) if r.get("ad_style")})

with st.sidebar:
    if _all_formats:
        format_filter = st.multiselect(
            "Format filter",
            options=_all_formats,
            default=[],
            key="ads_format_filter",
        )
    if _all_styles:
        style_filter = st.multiselect(
            "Style filter",
            options=_all_styles,
            default=[],
            key="ads_style_filter",
        )

# Apply sidebar filters to top_ads
_filtered_top = top_ads
if format_filter:
    _filtered_top = [r for r in _filtered_top if r.get("ad_format") in format_filter]
if style_filter:
    _filtered_top = [r for r in _filtered_top if r.get("ad_style") in style_filter]

# ---------------------------------------------------------------------------
# Section 1: Top Performing Ads
# ---------------------------------------------------------------------------
st.subheader("Top Performing Ads")

if _filtered_top:
    # Build display dataframe
    _top_display = []
    for r in _filtered_top:
        fsd = int(r.get("fsd") or 0)
        avg_ctr = float(r.get("avg_ctr") or 0)
        avg_freq = float(r.get("avg_frequency") or 0)
        preview_url = r.get("preview_url") or ""
        preview_link = f"[View on Facebook]({preview_url})" if preview_url else ""
        _top_display.append({
            "thumbnail": r.get("thumbnail_url") or "",
            "ad_name": r.get("ad_name") or r.get("ad_id") or "",
            "campaign": r.get("campaign_name") or "",
            "format": r.get("ad_format") or "unknown",
            "style": r.get("ad_style") or "unknown",
            "spend": float(r.get("spend") or 0),
            "FSD": fsd,
            "CTR %": avg_ctr,
            "frequency": avg_freq,
            "preview": preview_link,
            "insight": _ad_insight(fsd, avg_ctr, avg_freq),
        })

    df_top = pd.DataFrame(_top_display)

    col_cfg: dict[str, Any] = {
        "thumbnail": st.column_config.ImageColumn("Preview", width="small"),
        "ad_name": st.column_config.TextColumn("Ad Name", width="large"),
        "campaign": st.column_config.TextColumn("Campaign"),
        "spend": st.column_config.NumberColumn("Spend ($)", format="$%.2f"),
        "FSD": st.column_config.NumberColumn("FSD", format="%d"),
        "CTR %": st.column_config.NumberColumn("CTR %", format="%.2f%%"),
        "frequency": st.column_config.NumberColumn("Avg Freq", format="%.2f"),
        "preview": st.column_config.LinkColumn("Facebook", display_text="View"),
        "insight": st.column_config.TextColumn("Insight", width="medium"),
    }
    st.dataframe(
        df_top,
        use_container_width=True,
        hide_index=True,
        column_config=col_cfg,
    )
else:
    st.info("No ad-level data for this date range (or no ads match the selected filters).")

# ---------------------------------------------------------------------------
# Section 2: Fatigue Watch
# ---------------------------------------------------------------------------
st.subheader("Fatigue Watch")
st.caption("Ads with average frequency >= 2.5 — high repeat exposure can burn out audiences.")

_fatigue_filtered = fatigue_ads
if format_filter:
    _fatigue_filtered = [r for r in _fatigue_filtered if r.get("ad_format") in format_filter]
if style_filter:
    _fatigue_filtered = [r for r in _fatigue_filtered if r.get("ad_style") in style_filter]

if _fatigue_filtered:
    _fat_display = []
    for r in _fatigue_filtered:
        avg_freq = float(r.get("avg_frequency") or 0)
        preview_url = r.get("preview_url") or ""
        preview_link = f"[View]({preview_url})" if preview_url else ""
        _fat_display.append({
            "thumbnail": r.get("thumbnail_url") or "",
            "ad_name": r.get("ad_name") or r.get("ad_id") or "",
            "campaign": r.get("campaign_name") or "",
            "format": r.get("ad_format") or "unknown",
            "style": r.get("ad_style") or "unknown",
            "spend": float(r.get("spend") or 0),
            "avg_frequency": avg_freq,
            "CTR %": float(r.get("avg_ctr") or 0),
            "FSD": int(r.get("fsd") or 0),
            "preview": preview_link,
            "recommendation": _fatigue_label(avg_freq),
        })

    df_fat = pd.DataFrame(_fat_display)
    fat_col_cfg: dict[str, Any] = {
        "thumbnail": st.column_config.ImageColumn("Preview", width="small"),
        "ad_name": st.column_config.TextColumn("Ad Name", width="large"),
        "campaign": st.column_config.TextColumn("Campaign"),
        "spend": st.column_config.NumberColumn("Spend ($)", format="$%.2f"),
        "avg_frequency": st.column_config.NumberColumn("Avg Freq", format="%.2f"),
        "CTR %": st.column_config.NumberColumn("CTR %", format="%.3f%%"),
        "FSD": st.column_config.NumberColumn("FSD", format="%d"),
        "preview": st.column_config.LinkColumn("Facebook", display_text="View"),
        "recommendation": st.column_config.TextColumn("Recommendation", width="large"),
    }
    st.dataframe(
        df_fat,
        use_container_width=True,
        hide_index=True,
        column_config=fat_col_cfg,
    )
else:
    st.success("No ads with frequency >= 2.5 in this period. Audience freshness looks healthy.")

# ---------------------------------------------------------------------------
# Section 3: Ad Format Breakdown
# ---------------------------------------------------------------------------
st.subheader("Ad Format Breakdown")
st.caption("Spend, FSD, and CTR by ad format. Helps identify which creative type drives performance.")

if format_rows:
    left_col, right_col = st.columns([3, 2])
    with left_col:
        st.plotly_chart(_make_format_chart(format_rows), use_container_width=True)
    with right_col:
        _fmt_display = []
        for r in format_rows:
            fsd = int(r.get("fsd") or 0)
            spend = float(r.get("spend") or 0)
            _fmt_display.append({
                "format": r.get("ad_format") or "unknown",
                "ads": int(r.get("ad_count") or 0),
                "spend": spend,
                "FSD": fsd,
                "CTR %": float(r.get("avg_ctr") or 0),
                "ROAS": float(r.get("weighted_roas") or 0),
            })
        df_fmt = pd.DataFrame(_fmt_display)
        fmt_col_cfg: dict[str, Any] = {
            "spend": st.column_config.NumberColumn("Spend ($)", format="$%.2f"),
            "CTR %": st.column_config.NumberColumn("CTR %", format="%.2f%%"),
            "ROAS": st.column_config.NumberColumn("ROAS", format="%.2f"),
        }
        st.dataframe(df_fmt, use_container_width=True, hide_index=True, column_config=fmt_col_cfg)
else:
    st.info("No format breakdown data available. Ad creative metadata may not yet be fetched.")

# ---------------------------------------------------------------------------
# Section 4: Ad Style Breakdown
# ---------------------------------------------------------------------------
st.subheader("Ad Style Breakdown")
st.caption("FSD and CTR by creative style. CPF = Cost Per FSD (spend / FSD).")

if style_rows:
    left_col2, right_col2 = st.columns([3, 2])
    with left_col2:
        st.plotly_chart(_make_style_chart(style_rows), use_container_width=True)
    with right_col2:
        _sty_display = []
        for r in style_rows:
            fsd = int(r.get("fsd") or 0)
            spend = float(r.get("spend") or 0)
            cpf = round(spend / fsd, 2) if fsd > 0 else None
            _sty_display.append({
                "style": r.get("ad_style") or "unknown",
                "ads": int(r.get("ad_count") or 0),
                "spend": spend,
                "FSD": fsd,
                "CPF ($)": cpf,
                "CTR %": float(r.get("avg_ctr") or 0),
                "ROAS": float(r.get("weighted_roas") or 0),
            })
        df_sty = pd.DataFrame(_sty_display)
        sty_col_cfg: dict[str, Any] = {
            "spend": st.column_config.NumberColumn("Spend ($)", format="$%.2f"),
            "CPF ($)": st.column_config.NumberColumn("CPF ($)", format="$%.2f"),
            "CTR %": st.column_config.NumberColumn("CTR %", format="%.2f%%"),
            "ROAS": st.column_config.NumberColumn("ROAS", format="%.2f"),
        }
        st.dataframe(df_sty, use_container_width=True, hide_index=True, column_config=sty_col_cfg)
else:
    st.info("No style breakdown data available. Ad creative metadata may not yet be fetched.")
