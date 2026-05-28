"""Attribution Intelligence page (DASH-11, DASH-12, DASH-13).

Renders the latest Marketing Mix Model (MMM) result from the `mmm_results`
SQLite table: KPI cards, saturation curve, weekly contribution stacked bar,
and Meta vs GA4 attribution table.

Standalone page -- no bot framework imports (D-19 rule). Palette constants
are re-declared inline, never imported from app.py.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import numpy as np
import plotly.graph_objects as go
import streamlit as st

# st.set_page_config MUST be the first Streamlit call.
st.set_page_config(
    page_title="Attribution Intelligence",
    layout="wide",
    initial_sidebar_state="expanded",
)

from src.dashboard import db                            # noqa: E402
from src.dashboard.settings import DashboardSettings    # noqa: E402

# --- Dark-theme palette -- duplicated from app.py per D-19 standalone rule ---
COLOR_BG_PAPER = "#0f1117"
COLOR_BG_PLOT = "#1a1d27"
COLOR_FONT = "#e4e7ef"
COLOR_GRID = "#2a2e3a"
COLOR_SPEND = "rgba(99, 125, 255, 0.6)"
COLOR_DEPOSITS = "#34d399"
COLOR_META = "#60a5fa"
COLOR_GA4 = "#a78bfa"
COLOR_BASELINE = "#6366f1"
COLOR_MEDIA = "#34d399"
COLOR_OPT_ZONE = "#34d399"
COLOR_AVG_LINE = "#f59e0b"


# --- Auth gate -- duplicated from app.py per D-19 (each page is its own script)
def _check_auth(password_required: str) -> bool:
    if not password_required:
        return True
    if st.session_state.get("authenticated"):
        return True
    st.title("Attribution Intelligence")
    st.caption("Sign in to continue.")
    with st.form("auth_form_attribution"):
        pw = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in")
        if submitted:
            if pw == password_required:
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error("Incorrect password")
    return False


# --- Cached DB read wrappers (str db_path for cache-key stability) ----------
@st.cache_data(ttl=300, show_spinner=False)
def _cached_mmm_result(db_path_str: str) -> dict[str, Any] | None:
    from pathlib import Path
    return db.get_latest_mmm_result(Path(db_path_str))


@st.cache_data(ttl=300, show_spinner=False)
def _cached_weekly_contributions(
    db_path_str: str, weeks: int = 12
) -> list[dict[str, Any]]:
    from pathlib import Path
    return db.get_weekly_contributions(Path(db_path_str), weeks)


@st.cache_data(ttl=300, show_spinner=False)
def _cached_attribution(
    db_path_str: str, start: str, end: str
) -> list[dict[str, Any]]:
    from pathlib import Path
    return db.get_attribution_comparison(Path(db_path_str), start, end)


# --- Helpers ----------------------------------------------------------------
def _format_roas(roas: float | None, deposit_value_usd: float) -> str:
    """Format incremental ROAS for the KPI card.

    - None  -> "N/A"
    - deposit_value_usd == 0 -> "X.X dep/$1k" (deposits per $1000 spend)
    - else  -> "X.Xx" (true dollar ROAS multiple)
    """
    if roas is None:
        return "N/A"
    if deposit_value_usd <= 0:
        return f"{roas:.1f} dep/$1k"
    return f"{roas:.1f}x"


def _build_saturation_chart(
    km: float, n: float, avg_spend: float, opt_spend: float
) -> go.Figure:
    """Saturation curve with current-avg vertical line and optimal-zone shading.

    Implements RESEARCH Pattern 8. X-axis from 0 to opt_spend * 2 (200 points).
    """
    # Defensive: avoid degenerate x range.
    x_max = max(opt_spend * 2.0, max(avg_spend * 2.0, 1.0))
    x = np.linspace(0.0, x_max, 200)
    # Hill saturation: y = x^n / (km^n + x^n). Guard against km <= 0.
    safe_km = km if km > 0 else 1e-6
    y = (x ** n) / (safe_km ** n + x ** n)

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=x,
        y=y,
        mode="lines",
        line=dict(color=COLOR_META, width=2),
        name="Saturation curve",
    ))
    fig.add_vline(
        x=avg_spend,
        line_dash="dash",
        line_color=COLOR_AVG_LINE,
        annotation_text="Current avg",
        annotation_position="top left",
    )
    fig.add_vrect(
        x0=opt_spend * 0.85,
        x1=opt_spend * 1.15,
        fillcolor=COLOR_OPT_ZONE,
        opacity=0.15,
        line_width=0,
        annotation_text="Optimal zone",
        annotation_position="top right",
    )
    fig.update_layout(
        plot_bgcolor=COLOR_BG_PLOT,
        paper_bgcolor=COLOR_BG_PAPER,
        font=dict(color=COLOR_FONT),
        xaxis=dict(title="Daily Spend ($)", gridcolor=COLOR_GRID, zeroline=False),
        yaxis=dict(
            title="Saturation (0-1)",
            gridcolor=COLOR_GRID,
            zeroline=False,
            range=[0, 1.05],
        ),
        showlegend=False,
        margin=dict(l=40, r=40, t=40, b=40),
        height=380,
    )
    return fig


def _build_contribution_bar(contribs: list[dict[str, Any]]) -> go.Figure:
    """Stacked baseline + media bar across the last 12 ISO weeks.

    Implements RESEARCH Pattern 9 (barmode='stack'). Returns an empty figure
    with a centered "No data" annotation when there are no rows.
    """
    fig = go.Figure()
    if not contribs:
        fig.update_layout(
            plot_bgcolor=COLOR_BG_PLOT,
            paper_bgcolor=COLOR_BG_PAPER,
            font=dict(color=COLOR_FONT),
            xaxis=dict(visible=False),
            yaxis=dict(visible=False),
            annotations=[dict(
                text="No weekly contribution data available yet.",
                xref="paper", yref="paper", x=0.5, y=0.5,
                showarrow=False, font=dict(color=COLOR_FONT, size=14),
            )],
            margin=dict(l=40, r=40, t=40, b=40),
            height=380,
        )
        return fig

    weeks = [c["week"] for c in contribs]
    baseline_y = [c["baseline_deposits"] for c in contribs]
    media_y = [c["media_deposits"] for c in contribs]

    fig.add_trace(go.Bar(
        x=weeks,
        y=baseline_y,
        name="Baseline (organic)",
        marker_color=COLOR_BASELINE,
    ))
    fig.add_trace(go.Bar(
        x=weeks,
        y=media_y,
        name="Meta media",
        marker_color=COLOR_MEDIA,
    ))
    fig.update_layout(
        barmode="stack",
        plot_bgcolor=COLOR_BG_PLOT,
        paper_bgcolor=COLOR_BG_PAPER,
        font=dict(color=COLOR_FONT),
        xaxis=dict(title="ISO Week", gridcolor=COLOR_GRID),
        yaxis=dict(title="FSD", gridcolor=COLOR_GRID, zeroline=False),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(l=40, r=40, t=40, b=40),
        height=380,
    )
    return fig


def _run_mmm_now(settings: DashboardSettings) -> None:
    """Inline manual MMM run from the empty-state button (D-13).

    Sync -- no asyncio, no Telegram push. Loads campaign-level daily spend +
    deposits via sqlite3, calls fit_mmm(), inserts the resulting row directly
    via sync sqlite3.connect (NOT aiosqlite), clears caches, then st.rerun().
    """
    import sqlite3
    from pathlib import Path as _Path

    # Local imports to keep top-level imports clean of the heavy MMM stack
    # (statsmodels/scipy) until the user actually clicks the button.
    from src.mmm.model import fit_mmm  # noqa: E402

    db_path = _Path(str(settings.db_path))

    # Load campaign-level daily series (ad_set_id='' AND ad_id='' is the
    # campaign aggregation marker per CLAUDE.md data model rule).
    daily_sql = (
        "SELECT date, "
        "       SUM(spend) AS daily_spend, "
        "       SUM(meta_form_submit_deposit) AS daily_deposits "
        "FROM ad_metrics "
        "WHERE ad_set_id='' AND ad_id='' AND spend > 0 "
        "GROUP BY date "
        "ORDER BY date ASC"
    )
    weeks_sql = (
        "SELECT COUNT(DISTINCT strftime('%Y-%W', date)) AS weeks "
        "FROM ad_metrics "
        "WHERE ad_set_id='' AND ad_id='' AND spend > 0"
    )

    try:
        with sqlite3.connect(str(db_path)) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(daily_sql).fetchall()
            weeks_row = con.execute(weeks_sql).fetchone()
    except sqlite3.OperationalError as exc:
        st.error(f"Database not initialized: {exc}")
        return

    if not rows:
        st.error("No campaign-level spend data available -- cannot run MMM.")
        return

    spend_arr = np.array([float(r["daily_spend"] or 0) for r in rows], dtype=float)
    deposits_arr = np.array(
        [float(r["daily_deposits"] or 0) for r in rows], dtype=float
    )
    weeks_of_data = int(weeks_row["weeks"]) if weeks_row else 0

    deposit_value_usd = float(getattr(settings, "deposit_value_usd", 0.0) or 0.0)

    with st.spinner("Fitting MMM (geometric adstock + Hill + OLS)..."):
        result = fit_mmm(
            spend_arr,
            deposits_arr,
            deposit_value_usd=deposit_value_usd,
            run_date=date.today().isoformat(),
            weeks_of_data=weeks_of_data,
        )

    if result is None:
        st.error(
            "MMM fit failed -- typically caused by too few weeks of data, "
            "sparse deposits, or Hill saturation not converging. "
            "Check bot logs for the specific reason."
        )
        return

    # Persist via sync sqlite3 directly (matches D-12 schema column order).
    insert_sql = (
        "INSERT INTO mmm_results ("
        "run_date, weeks_of_data, media_pct, baseline_pct, "
        "incremental_roas_per_1k, optimal_daily_spend, "
        "theta, km, n, maturity_label"
        ") VALUES ("
        ":run_date, :weeks_of_data, :media_pct, :baseline_pct, "
        ":incremental_roas_per_1k, :optimal_daily_spend, "
        ":theta, :km, :n, :maturity_label"
        ")"
    )
    try:
        with sqlite3.connect(str(db_path)) as con:
            con.execute(insert_sql, result.to_dict())
            con.commit()
    except sqlite3.OperationalError as exc:
        st.error(
            f"Could not save MMM result -- mmm_results table missing? {exc}"
        )
        return

    # Invalidate cached reads so the next render shows the fresh row.
    st.cache_data.clear()
    st.success("MMM run complete -- refreshing...")
    st.rerun()


# --- Main page body ---------------------------------------------------------
settings = DashboardSettings()  # type: ignore[call-arg]

if not _check_auth(settings.dashboard_password):
    st.stop()

st.page_link("Overview.py", label="<- Back to Overview")
st.title("Attribution Intelligence")
st.caption(
    "Marketing Mix Model (MMM) -- geometric adstock + Hill saturation + "
    "OLS decomposition. Estimates Meta media's incremental contribution to "
    "deposits, separate from baseline (organic) demand."
)

db_path_str = str(settings.db_path)
mmm = _cached_mmm_result(db_path_str)

# --- Empty state (D-13) -----------------------------------------------------
if mmm is None:
    st.info(
        "MMM has not run yet. The weekly job runs Sunday at 23:00. "
        "Click below to run an ad-hoc fit on the data available right now."
    )
    if st.button("Run MMM now", type="primary"):
        _run_mmm_now(settings)
    st.stop()

# --- Row 1: KPI cards (D-11) -----------------------------------------------
deposit_value_usd = float(getattr(settings, "deposit_value_usd", 0.0) or 0.0)

c1, c2, c3, c4 = st.columns(4)
c1.metric(
    "Media Contribution",
    f"{mmm['media_pct']:.1f}%",
    help="Share of deposits attributed to Meta media spend (MMM estimate). "
         "Remainder is baseline / organic.",
)
c2.metric(
    "Incremental ROAS",
    _format_roas(mmm.get("incremental_roas_per_1k"), deposit_value_usd),
    help="Deposits per $1000 spend when DEPOSIT_VALUE_USD is 0; "
         "true dollar ROAS multiple when set.",
)
c3.metric(
    "Optimal Daily Spend",
    f"~${mmm['optimal_daily_spend']:.0f}",
    help="Spend level at 80% of Hill saturation -- above this, returns "
         "diminish sharply.",
)
maturity_display = str(mmm.get("maturity_label", "")).replace("_", " ").title()
c4.metric(
    "Data Maturity",
    maturity_display or "--",
    help=f"Based on {mmm.get('weeks_of_data', 0)} weeks of data. "
         f">=12 weeks = reliable; 8-11 = early; <8 = directional only.",
)

# Footnote when maturity warrants it.
maturity = str(mmm.get("maturity_label", ""))
if maturity == "directional_only":
    st.warning(
        f"[!] Directional only -- {mmm.get('weeks_of_data', 0)} weeks of data. "
        "Results improve significantly at >=8 weeks; treat as orientation, "
        "not allocation truth."
    )
elif maturity == "early":
    st.caption(
        f"* Based on {mmm.get('weeks_of_data', 0)} weeks of data. "
        "Results strengthen at 3+ months."
    )

# --- Row 2: Saturation curve + 12-week contribution bar (D-11) -------------
col_left, col_right = st.columns(2)

# Pull the weekly contributions once -- used by both columns (avg_spend on left,
# bar values on right).
contribs = _cached_weekly_contributions(db_path_str)

with col_left:
    st.subheader("Saturation Curve")
    km_val = float(mmm.get("km") or 1.0)
    n_val = float(mmm.get("n") or 1.0)
    opt_val = float(mmm.get("optimal_daily_spend") or 0.0)
    if contribs:
        avg_spend = float(
            np.mean([c["avg_daily_spend"] for c in contribs if c["avg_daily_spend"]])
        ) if any(c["avg_daily_spend"] for c in contribs) else opt_val * 0.5
    else:
        avg_spend = opt_val * 0.5

    fig_sat = _build_saturation_chart(km_val, n_val, avg_spend, opt_val)
    st.plotly_chart(fig_sat, use_container_width=True)
    st.caption(
        f"Hill parameters: Km=${km_val:.0f}, n={n_val:.2f}, theta={float(mmm.get('theta') or 0):.2f}. "
        "Optimal zone is +/-15% of optimal daily spend."
    )

with col_right:
    st.subheader("12-Week Contribution Breakdown")
    fig_bar = _build_contribution_bar(contribs)
    st.plotly_chart(fig_bar, use_container_width=True)
    st.caption(
        "Total weekly deposits split into baseline (organic / seasonal) vs "
        "Meta media using the MMM's media_pct ratio."
    )

# --- Row 3: Meta vs GA4 attribution table (D-11) ----------------------------
st.subheader("Meta vs GA4 Attribution (last 30 days)")
today = date.today()
end_yesterday = (today - timedelta(days=1)).isoformat()
start_30 = (today - timedelta(days=30)).isoformat()
attr_rows = _cached_attribution(db_path_str, start_30, end_yesterday)

if attr_rows:
    import pandas as pd  # local import -- Streamlit auto-renders DataFrames

    df = pd.DataFrame(attr_rows)
    # Reorder + rename columns for display clarity (never blend numbers).
    display_cols = {
        "campaign_name": "Campaign",
        "meta_purchases": "Meta purchases (7d-click)",
        "meta_deposits": "Meta FSD (form_submit)",
        "ga4_purchases": "GA4 purchases (last-click)",
    }
    present = [c for c in display_cols if c in df.columns]
    df_display = df[present].rename(columns=display_cols)
    st.dataframe(df_display, use_container_width=True, hide_index=True)
    st.caption(
        "Never blend -- Meta uses 7-day click attribution; GA4 uses last-click. "
        "Discrepancies between these numbers are expected and normal."
    )
else:
    st.info("No attribution data available for the last 30 days.")
