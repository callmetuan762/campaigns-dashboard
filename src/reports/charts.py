"""Matplotlib chart generation for Telegram photo messages.

REPORT-06: Three chart types sent as Telegram photo messages alongside text reports.
D-13: Charts as PNGs in io.BytesIO — no disk I/O, no temp files.
D-15: Minimal style, tight_layout(), static PNG only.

CRITICAL: matplotlib.use("Agg") MUST be called before any pyplot import.
CRITICAL: Always use OO API (fig, ax = plt.subplots()) and always plt.close(fig).
          Never use stateful pyplot API (plt.figure(), plt.plot()) — causes memory leaks
          in long-running scheduled jobs (RESEARCH Pitfall 3).
"""
from __future__ import annotations

import io

import matplotlib
matplotlib.use("Agg")  # Must be before any other matplotlib import
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import pandas as pd
import structlog

logger = structlog.get_logger(__name__)


def generate_spend_trend_chart(rows: list[dict]) -> bytes:
    """Generate 7-day total daily spend trend line chart. Returns PNG bytes.

    rows: list of dicts with keys 'date' (ISO str) and 'spend' (float).
    Returns b"" if rows is empty.
    """
    if not rows:
        return b""
    try:
        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"])
        daily = df.groupby("date")["spend"].sum().sort_index().tail(7)

        fig, ax = plt.subplots(figsize=(10, 4))
        ax.plot(daily.index, daily.values, marker="o", linewidth=2, color="#1f77b4")
        ax.set_title("Daily Spend (7-day)", fontsize=13)
        ax.set_ylabel("Spend ($)")
        ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"${x:,.0f}"))
        ax.grid(axis="y", alpha=0.3)
        fig.autofmt_xdate()
        plt.tight_layout()

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=100)
        plt.close(fig)  # CRITICAL: always close to prevent memory leak
        buf.seek(0)
        return buf.getvalue()
    except Exception as exc:  # noqa: BLE001
        logger.warning("chart_error", chart="spend_trend", error=str(exc))
        return b""


def generate_roas_trend_chart(rows: list[dict]) -> bytes:
    """Generate 7-day average ROAS trend line chart. Returns PNG bytes.

    rows: list of dicts with keys 'date' (ISO str) and 'roas' (float).
    Returns b"" if rows is empty.
    """
    if not rows:
        return b""
    try:
        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"])
        # Weight average ROAS by spend if available; otherwise simple mean
        if "spend" in df.columns and df["spend"].sum() > 0:
            daily = (
                df.groupby("date")
                .apply(lambda g: (g["roas"] * g["spend"]).sum() / g["spend"].sum(), include_groups=False)
                .sort_index()
                .tail(7)
            )
        else:
            daily = df.groupby("date")["roas"].mean().sort_index().tail(7)

        fig, ax = plt.subplots(figsize=(10, 4))
        ax.plot(daily.index, daily.values, marker="o", linewidth=2, color="#2ca02c")
        ax.axhline(y=1.0, color="red", linestyle="--", alpha=0.5, label="Break-even (1.0)")
        ax.set_title("ROAS Trend (7-day)", fontsize=13)
        ax.set_ylabel("ROAS")
        ax.legend(fontsize=9)
        ax.grid(axis="y", alpha=0.3)
        fig.autofmt_xdate()
        plt.tight_layout()

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=100)
        plt.close(fig)
        buf.seek(0)
        return buf.getvalue()
    except Exception as exc:  # noqa: BLE001
        logger.warning("chart_error", chart="roas_trend", error=str(exc))
        return b""


_METRIC_CHART_CONFIG: dict[str, dict] = {
    "spend":                    {"title": "Daily Spend (7-day)",       "ylabel": "Spend ($)",    "agg": "sum",  "color": "#1f77b4", "fmt": "${:.0f}"},
    "roas":                     {"title": "ROAS Trend (7-day)",        "ylabel": "ROAS",         "agg": "mean", "color": "#2ca02c", "fmt": "{:.2f}",  "hline": 1.0},
    "ctr":                      {"title": "CTR Trend (7-day)",         "ylabel": "CTR (%)",      "agg": "mean", "color": "#ff7f0e", "fmt": "{:.1f}%"},
    "cpc":                      {"title": "CPC Trend (7-day)",         "ylabel": "Cost/Click ($)","agg": "mean","color": "#9467bd", "fmt": "${:.2f}"},
    "cpm":                      {"title": "CPM Trend (7-day)",         "ylabel": "CPM ($)",      "agg": "mean", "color": "#8c564b", "fmt": "${:.2f}"},
    "clicks":                   {"title": "Daily Clicks (7-day)",      "ylabel": "Clicks",       "agg": "sum",  "color": "#1f77b4", "fmt": "{:.0f}"},
    "impressions":              {"title": "Daily Impressions (7-day)", "ylabel": "Impressions",  "agg": "sum",  "color": "#17becf", "fmt": "{:.0f}"},
    "meta_purchases_7dclick":   {"title": "Daily Purchases (7-day)",   "ylabel": "Purchases",    "agg": "sum",  "color": "#d62728", "fmt": "{:.0f}"},
}


def generate_metric_trend_chart(rows: list[dict], metric: str) -> bytes:
    """Generic daily trend chart for any ad_metrics column. Returns PNG bytes.

    rows: list of dicts with 'date' (ISO str) and <metric> (numeric).
    Falls back to spend if metric config is unknown.
    """
    if not rows:
        return b""
    cfg = _METRIC_CHART_CONFIG.get(metric, _METRIC_CHART_CONFIG["spend"])
    try:
        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"])
        # SQL aliases the selected metric column as 'value'; fall back to metric name if present
        value_col = "value" if "value" in df.columns else metric
        df["value"] = pd.to_numeric(df[value_col], errors="coerce").fillna(0)
        if cfg["agg"] == "sum":
            daily = df.groupby("date")["value"].sum().sort_index().tail(7)
        else:
            daily = df.groupby("date")["value"].mean().sort_index().tail(7)

        fig, ax = plt.subplots(figsize=(10, 4))
        ax.plot(daily.index, daily.values, marker="o", linewidth=2, color=cfg["color"])
        if "hline" in cfg:
            ax.axhline(y=cfg["hline"], color="red", linestyle="--", alpha=0.5,
                       label=f"Break-even ({cfg['hline']})")
            ax.legend(fontsize=9)
        ax.set_title(cfg["title"], fontsize=13)
        ax.set_ylabel(cfg["ylabel"])
        ax.grid(axis="y", alpha=0.3)
        fmt_str = cfg["fmt"]
        ax.yaxis.set_major_formatter(
            mticker.FuncFormatter(lambda x, _: fmt_str.format(x))
        )
        fig.autofmt_xdate()
        plt.tight_layout()

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=100)
        plt.close(fig)
        buf.seek(0)
        return buf.getvalue()
    except Exception as exc:  # noqa: BLE001
        logger.warning("chart_error", chart="metric_trend", metric=metric, error=str(exc))
        return b""


def generate_top_campaigns_chart(rows: list[dict]) -> bytes:
    """Generate top 10 campaigns by spend horizontal bar chart. Returns PNG bytes.

    rows: list of dicts with keys 'campaign_name' (str) and 'spend' (float).
    Returns b"" if rows is empty.
    """
    if not rows:
        return b""
    try:
        df = pd.DataFrame(rows)
        top10 = df.groupby("campaign_name")["spend"].sum().nlargest(10).sort_values()

        fig, ax = plt.subplots(figsize=(10, 6))
        bars = ax.barh(top10.index, top10.values, color="#1f77b4")
        ax.bar_label(bars, fmt="$%.0f", padding=4, fontsize=9)
        ax.set_title("Top Campaigns by Spend", fontsize=13)
        ax.set_xlabel("Spend ($)")
        ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"${x:,.0f}"))
        ax.grid(axis="x", alpha=0.3)
        plt.tight_layout()

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=100)
        plt.close(fig)
        buf.seek(0)
        return buf.getvalue()
    except Exception as exc:  # noqa: BLE001
        logger.warning("chart_error", chart="top_campaigns", error=str(exc))
        return b""
