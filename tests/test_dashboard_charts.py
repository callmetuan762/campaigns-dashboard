"""Unit tests for Plotly figure builders in src/dashboard/app.py (DASH-02).

Tests chart trace counts, locked D-10 color palette, dual-axis layout,
ROAS indicator thresholds, and campaign DataFrame column order.
"""
from __future__ import annotations

import os

import pytest

# Streamlit's set_page_config raises if called twice; importing app.py at module
# load triggers it. Use a flag to skip / share the import side-effect across tests.
os.environ.setdefault("DASHBOARD_PASSWORD", "")


def _import_app():
    """Import src.dashboard.app exactly once (set_page_config has run)."""
    import importlib
    import sys
    if "src.dashboard.app" in sys.modules:
        return sys.modules["src.dashboard.app"]
    return importlib.import_module("src.dashboard.app")


def test_spend_vs_deposits_chart_traces() -> None:
    app = _import_app()
    rows = [
        {"date": "2026-05-01", "spend": 100.0, "deposits": 5, "sessions": 500},
        {"date": "2026-05-02", "spend": 150.0, "deposits": 4, "sessions": 600},
    ]
    fig = app._make_spend_vs_deposits_chart(rows)
    assert len(fig.data) == 2
    # First trace: Bar (spend) with the locked rgba color
    bar = fig.data[0]
    assert bar.type == "bar"
    assert bar.marker.color == "rgba(99, 125, 255, 0.6)"
    # Second trace: Scatter (deposits) with the locked green color and yaxis2
    scatter = fig.data[1]
    assert scatter.type == "scatter"
    assert scatter.line.color == "#34d399"
    assert scatter.yaxis == "y2"


def test_spend_vs_deposits_layout_dark_theme() -> None:
    app = _import_app()
    fig = app._make_spend_vs_deposits_chart([])
    layout = fig.layout
    assert layout.plot_bgcolor == "#1a1d27"
    assert layout.paper_bgcolor == "#0f1117"
    assert layout.yaxis2.overlaying == "y"
    assert layout.yaxis2.side == "right"


def test_attribution_chart_grouped_bars_with_locked_colors() -> None:
    app = _import_app()
    rows = [
        {"campaign_name": "Brand",   "meta_deposits": 5, "ga4_purchases": 3, "meta_purchases": 7},
        {"campaign_name": "Convert", "meta_deposits": 2, "ga4_purchases": 2, "meta_purchases": 3},
    ]
    fig = app._make_attribution_chart(rows)
    assert len(fig.data) == 2
    assert fig.data[0].type == "bar"
    assert fig.data[1].type == "bar"
    assert fig.data[0].marker.color == "#60a5fa"  # Meta
    assert fig.data[1].marker.color == "#a78bfa"  # GA4
    assert fig.layout.barmode == "group"


def test_roas_indicator_thresholds() -> None:
    app = _import_app()
    assert app._roas_indicator(2.5).startswith("🟢")
    assert app._roas_indicator(2.0).startswith("🟢")
    assert app._roas_indicator(1.5).startswith("⚠️")
    assert app._roas_indicator(0.99).startswith("🔴")
    assert app._roas_indicator(None) == "—"


def test_format_campaign_df_column_order() -> None:
    app = _import_app()
    rows = [
        {"campaign_name": "Brand", "spend": 100.0, "weighted_roas": 2.5,
         "impressions": 1000, "deposits": 5, "cpd": 20.0, "ga4_sessions": 500},
    ]
    df = app._format_campaign_df(rows)
    assert list(df.columns) == ["Campaign", "Spend", "ROAS", "Impressions",
                                 "Deposits", "CPD", "GA4 Sessions"]
    assert df.iloc[0]["ROAS"].startswith("🟢")
    assert df.iloc[0]["Campaign"] == "Brand"


def test_format_campaign_df_empty() -> None:
    app = _import_app()
    df = app._format_campaign_df([])
    # Empty DataFrame still has the expected columns
    assert list(df.columns) == ["Campaign", "Spend", "ROAS", "Impressions",
                                 "Deposits", "CPD", "GA4 Sessions"]
    assert len(df) == 0


# ---------------------------------------------------------------------------
# TIER tag tests (07-02, D-03, D-04, DASH-06)
# ---------------------------------------------------------------------------

def test_tier_tag_paused_no_deposits_with_cpd() -> None:
    """cpd is set but deposits==0 → PAUSED (zero-conversion guard)."""
    app = _import_app()
    assert app._tier_tag(50.0, 0, 25.0) == "PAUSED"


def test_tier_tag_paused_none_cpd() -> None:
    """cpd is None and deposits==0 → PAUSED."""
    app = _import_app()
    assert app._tier_tag(None, 0, 25.0) == "PAUSED"


def test_tier_tag_scale_below_target() -> None:
    """cpd < cpd_target → ★ SCALE."""
    app = _import_app()
    assert app._tier_tag(20.0, 5, 25.0) == "★ SCALE"


def test_tier_tag_scale_at_target_boundary() -> None:
    """cpd == cpd_target (inclusive) → ★ SCALE."""
    app = _import_app()
    assert app._tier_tag(25.0, 5, 25.0) == "★ SCALE"


def test_tier_tag_maintain_above_target() -> None:
    """cpd <= cpd_target * 1.3 → MAINTAIN."""
    app = _import_app()
    assert app._tier_tag(30.0, 5, 25.0) == "MAINTAIN"


def test_tier_tag_maintain_at_upper_boundary() -> None:
    """cpd == cpd_target * 1.3 (32.5) → MAINTAIN (inclusive upper boundary)."""
    app = _import_app()
    assert app._tier_tag(32.5, 5, 25.0) == "MAINTAIN"


def test_tier_tag_reduce_over_threshold() -> None:
    """cpd > cpd_target * 1.3 → REDUCE."""
    app = _import_app()
    assert app._tier_tag(33.0, 5, 25.0) == "REDUCE"


def test_tier_tag_disabled_returns_empty_string() -> None:
    """cpd_target == 0.0 (TIER disabled) → '' regardless of cpd."""
    app = _import_app()
    assert app._tier_tag(50.0, 5, 0.0) == ""


# ---------------------------------------------------------------------------
# _format_campaign_df with cpd_target (07-02, D-04)
# ---------------------------------------------------------------------------

def test_format_campaign_df_no_tier_when_cpd_target_zero() -> None:
    """cpd_target == 0.0 → 7-column Phase-6-parity DataFrame, no TIER column."""
    app = _import_app()
    df = app._format_campaign_df([], cpd_target=0.0)
    assert list(df.columns) == ["Campaign", "Spend", "ROAS", "Impressions",
                                 "Deposits", "CPD", "GA4 Sessions"]


def test_format_campaign_df_has_tier_when_cpd_target_set() -> None:
    """cpd_target > 0.0 → 8-column DataFrame with TIER as last column."""
    app = _import_app()
    df = app._format_campaign_df([], cpd_target=25.0)
    assert list(df.columns)[-1] == "TIER"
    assert len(df.columns) == 8


def test_format_campaign_df_tier_scale_value() -> None:
    """Row with cpd=20, deposits=5, cpd_target=25 → TIER == '★ SCALE'."""
    app = _import_app()
    rows = [{"campaign_name": "x", "spend": 100.0, "weighted_roas": 2.0,
             "impressions": 1000, "deposits": 5, "cpd": 20.0, "ga4_sessions": 50}]
    df = app._format_campaign_df(rows, cpd_target=25.0)
    assert df.iloc[0]["TIER"] == "★ SCALE"


def test_format_campaign_df_tier_paused_no_deposits() -> None:
    """Row with deposits=0, cpd=None → TIER == 'PAUSED'."""
    app = _import_app()
    rows = [{"campaign_name": "y", "spend": 50.0, "weighted_roas": 0.0,
             "impressions": 500, "deposits": 0, "cpd": None, "ga4_sessions": 10}]
    df = app._format_campaign_df(rows, cpd_target=25.0)
    assert df.iloc[0]["TIER"] == "PAUSED"
