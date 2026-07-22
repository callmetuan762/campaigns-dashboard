"""Source-level smoke tests for the Metric Glossary page (Overview v2, 2026-07-22).

Follows the same no-Streamlit-runtime pattern as test_funnel_page_v3_smoke.py:
parses the source rather than importing it (importing would execute
st.set_page_config() and the auth-gated main flow at module scope).
"""
from __future__ import annotations

import ast
from pathlib import Path

PAGE_PATH = Path("src/dashboard/pages/7_Glossary.py")


def test_page_file_exists() -> None:
    assert PAGE_PATH.exists()


def test_page_syntax_valid() -> None:
    ast.parse(PAGE_PATH.read_text(encoding="utf-8"))


def test_overview_v2_kpi_metrics_documented() -> None:
    """Every new Overview v2 KPI-row metric must have a glossary row."""
    source = PAGE_PATH.read_text(encoding="utf-8")
    required = [
        "MER (blended)",
        "Blended CAC",
        "Pre-orders",
        "LPV → Checkout (Meta-attributed)",
        "GA4 Sessions (all)",
        "GA4 Sessions (campaign-attributed)",
        "Meta ROAS (7d-click)",
    ]
    missing = [r for r in required if r not in source]
    assert not missing, f"Missing glossary rows: {missing}"


def test_gap_decomposition_documented() -> None:
    source = PAGE_PATH.read_text(encoding="utf-8")
    assert "Capture Gap" in source
    assert "Attribution Gap" in source


def test_reach_sum_of_daily_caveat_documented() -> None:
    source = PAGE_PATH.read_text(encoding="utf-8")
    assert "Reach (sum of daily)" in source
    assert "overcounts unique" in source or "overcount" in source.lower()


def test_property_wide_ga4_purchases_documented() -> None:
    """The reconciliation triangle's property-wide GA4 purchase count (v2)
    must be distinguished from the campaign-attributed ga4_purchases_lastclick."""
    source = PAGE_PATH.read_text(encoding="utf-8")
    assert "GA4 purchases (property-wide)" in source
    assert "ga4_purchases_lastclick" in source
    assert "utm_campaign" in source


def test_legacy_deposit_metrics_note_expander() -> None:
    """FSD/CPR/Paid Rate/CPaC rows must reference the Overview legacy expander
    now that they've moved out of the primary KPI row."""
    source = PAGE_PATH.read_text(encoding="utf-8")
    assert "Legacy deposit funnel (FSD/Stripe era)" in source or "legacy expander" in source


def test_changelog_entry_present() -> None:
    source = PAGE_PATH.read_text(encoding="utf-8")
    assert "2026-07-22" in source
    assert "Overview v2" in source
    assert "MER/CAC/preorder KPI row" in source
    assert "Shopify-anchored" in source
    assert "gap decomposition" in source
    assert "legacy expander" in source


def test_changelog_preserves_prior_entry() -> None:
    source = PAGE_PATH.read_text(encoding="utf-8")
    assert "2026-07-21" in source


def test_campaign_objective_glossary_row_present() -> None:
    """Item 2 (2026-07-22): campaigns.objective must have a glossary row."""
    source = PAGE_PATH.read_text(encoding="utf-8")
    assert "Campaign Objective / Goal" in source
    assert "OUTCOME_SALES" in source
    assert "campaigns.objective" in source
