"""Unit tests for pure dashboard helpers in src/dashboard/Overview.py (DASH-02, DASH-06).

Covers ROAS indicator thresholds, TIER tag classification, and the
_format_campaign_df conditional column set (Goal, Initiate Checkout/Lead
metric swap, TIER).

Note: the old _make_spend_vs_deposits_chart / _make_attribution_chart figure
builders this file used to test were removed from the dashboard entirely
(no longer present anywhere in src/) -- there is nothing left to test.
"""
from __future__ import annotations

import os

import pytest

# Streamlit's set_page_config raises if called twice; importing Overview.py at
# module load triggers it. Use a flag to skip / share the import side-effect
# across tests.
os.environ.setdefault("DASHBOARD_PASSWORD", "")


def _import_app():
    """Import src.dashboard.Overview exactly once (set_page_config has run)."""
    import importlib
    import sys
    if "src.dashboard.Overview" in sys.modules:
        return sys.modules["src.dashboard.Overview"]
    return importlib.import_module("src.dashboard.Overview")


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
         "impressions": 1000, "begin_checkout": 5, "cost_per_bc": 20.0, "ga4_sessions": 500},
    ]
    df = app._format_campaign_df(rows)
    assert list(df.columns) == ["Campaign", "Goal", "Spend", "ROAS", "Impressions",
                                 "Initiate Checkout", "CPR (Initiate Checkout)", "GA4 Sessions"]
    assert df.iloc[0]["ROAS"].startswith("🟢")
    assert df.iloc[0]["Campaign"] == "Brand"


def test_format_campaign_df_empty() -> None:
    app = _import_app()
    df = app._format_campaign_df([])
    # Empty DataFrame still has the expected columns
    assert list(df.columns) == ["Campaign", "Goal", "Spend", "ROAS", "Impressions",
                                 "Initiate Checkout", "CPR (Initiate Checkout)", "GA4 Sessions"]
    assert len(df) == 0


def test_format_campaign_df_leads_metric_swap() -> None:
    """show_leads_metric=True swaps Initiate Checkout for Lead (D-19 toggle)."""
    app = _import_app()
    rows = [
        {"campaign_name": "Brand", "spend": 100.0, "weighted_roas": 2.5,
         "impressions": 1000, "leads": 7, "cost_per_lead": 12.0, "ga4_sessions": 500},
    ]
    df = app._format_campaign_df(rows, show_leads_metric=True)
    assert list(df.columns) == ["Campaign", "Goal", "Spend", "ROAS", "Impressions",
                                 "Lead", "CPR (Lead)", "GA4 Sessions"]
    assert df.iloc[0]["Lead"] == 7
    assert df.iloc[0]["CPR (Lead)"] == 12.0


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
    """cpd_target == 0.0 → 8-column Goal-aware DataFrame, no TIER column."""
    app = _import_app()
    df = app._format_campaign_df([], cpd_target=0.0)
    assert list(df.columns) == ["Campaign", "Goal", "Spend", "ROAS", "Impressions",
                                 "Initiate Checkout", "CPR (Initiate Checkout)", "GA4 Sessions"]


def test_format_campaign_df_has_tier_when_cpd_target_set() -> None:
    """cpd_target > 0.0 → 9-column DataFrame with TIER as last column."""
    app = _import_app()
    df = app._format_campaign_df([], cpd_target=25.0)
    assert list(df.columns)[-1] == "TIER"
    assert len(df.columns) == 9


def test_format_campaign_df_tier_scale_value() -> None:
    """Row with cost_per_bc=20, begin_checkout=5, cpd_target=25 → TIER == '★ SCALE'."""
    app = _import_app()
    rows = [{"campaign_name": "x", "spend": 100.0, "weighted_roas": 2.0,
             "impressions": 1000, "begin_checkout": 5, "cost_per_bc": 20.0, "ga4_sessions": 50}]
    df = app._format_campaign_df(rows, cpd_target=25.0)
    assert df.iloc[0]["TIER"] == "★ SCALE"


def test_format_campaign_df_tier_paused_no_deposits() -> None:
    """Row with begin_checkout=0, cost_per_bc=None → TIER == 'PAUSED'."""
    app = _import_app()
    rows = [{"campaign_name": "y", "spend": 50.0, "weighted_roas": 0.0,
             "impressions": 500, "begin_checkout": 0, "cost_per_bc": None, "ga4_sessions": 10}]
    df = app._format_campaign_df(rows, cpd_target=25.0)
    assert df.iloc[0]["TIER"] == "PAUSED"
