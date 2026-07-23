"""Tests for the Overview.py sidebar "Conversion metric" picker (item 3,
2026-07-23 -- repo-wide FSD sweep follow-up).

Overview.py used to compute a `use_form_submit` boolean from the sidebar
radio ("FSD (form_submit)" vs "Purchases (7d-click)") that nothing ever
read -- dead code left over from the FSD -> Initiate Checkout re-point.
Re-pointed here to a Sales-vs-Leads-aware picker: "Initiate Checkout (Sales)"
vs "Lead (Leads)", wired into the Campaign performance table via
_format_campaign_df's new `show_leads_metric` param and
db.get_campaign_table's new leads/cost_per_lead fields.

Source-level only (ast.parse), matching the established page-test
convention (see tests/test_ads_page.py, tests/test_glossary_page.py) --
Overview.py runs st.set_page_config()/the auth-gated main flow at module
scope, so it is not import-safe outside a Streamlit AppTest run.
"""
from __future__ import annotations

import ast
from pathlib import Path

PAGE_PATH = Path("src/dashboard/Overview.py")


def test_page_syntax_valid() -> None:
    ast.parse(PAGE_PATH.read_text(encoding="utf-8"))


def test_no_leftover_dead_fsd_toggle() -> None:
    """The old FSD/Purchases radio and its unused use_form_submit variable
    must be gone -- both were dead code (nothing ever read use_form_submit).

    Does NOT ban "CPR (FSD)" outright: that phrase legitimately still
    appears in the "Legacy deposit funnel (FSD/Stripe era)" expander, which
    is explicitly out of scope (gated to only render when historical FSD
    data exists) -- see test_new_bc_language_present-style page tests for
    the same narrower-than-substring-ban pattern.
    """
    source = PAGE_PATH.read_text(encoding="utf-8")
    assert "FSD (form_submit)" not in source
    # The unused variable itself must be gone (a mention of its old name in
    # a docstring/comment explaining the re-point is fine and expected).
    assert "use_form_submit: bool" not in source
    assert "use_form_submit = conv_metric" not in source


def test_conversion_metric_picker_present() -> None:
    """The re-pointed picker offers the two real, currently-tracked native
    metrics -- Initiate Checkout (meta_begin_checkout, Sales) and Lead
    (meta_leads, Leads) -- and its result actually reaches the campaign
    table (not dead this time)."""
    source = PAGE_PATH.read_text(encoding="utf-8")
    assert "Initiate Checkout (Sales)" in source
    assert "Lead (Leads)" in source
    assert "show_leads_metric" in source
    assert "_format_campaign_df(" in source
    # the sidebar variable must actually be threaded into the call, not dead
    assert "campaign_rows, settings.cpd_target, campaign_objectives, show_leads_metric" in source


def test_daily_briefing_prompt_has_no_stale_fsd_language() -> None:
    """The AI daily-briefing prompt string (a Streamlit-generated, fixed
    prompt -- inert text, no functional tie to any toggle) must reference
    the live metric name, not the dead FSD/deposits/CPD vocabulary. Checked
    via phrases unique to this prompt (not the separately-scoped, deliberately
    unchanged Legacy deposit funnel expander, which still legitimately says
    "CPR (FSD)")."""
    source = PAGE_PATH.read_text(encoding="utf-8")
    assert "zero deposits in the last 3 days" not in source
    assert "CPD or zero-deposit status" not in source
    assert "Any CPR (FSD) that spiked" not in source
    assert "CPR (Initiate Checkout) that spiked" in source
