"""Metric Glossary page (Phase D trust/UX quick win).

A single reference table of every metric shown anywhere on the dashboard --
name, plain-English definition, data source, attribution window, and an
owner-note with gotchas. Static content -- no DB queries, no filters.

Standalone: no aiogram / no src.ai / no src.bot imports (Phase 6 D-19 rule).
Auth gate and palette duplicated from Overview.py per D-19.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

st.set_page_config(
    page_title="Metric Glossary",
    layout="wide",
    initial_sidebar_state="expanded",
)

from src.dashboard.settings import DashboardSettings  # noqa: E402


# ---------------------------------------------------------------------------
# Auth gate -- duplicated from Overview.py per D-19 (each page is its own script)
# ---------------------------------------------------------------------------
def _check_auth(password_required: str) -> bool:
    if not password_required:
        return True
    if st.session_state.get("authenticated"):
        return True
    st.title("Metric Glossary")
    st.caption("Sign in to continue.")
    with st.form("auth_form_glossary"):
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
# Glossary content -- one row per metric shown anywhere on the dashboard.
# Kept as a plain list of dicts (static content, no DB query) so it's easy
# to scan and diff in review.
# ---------------------------------------------------------------------------
_GLOSSARY_ROWS: list[dict[str, str]] = [
    {
        "Metric": "Spend",
        "Definition": "Total ad spend in USD for the selected period.",
        "Source": "Meta",
        "Attribution window": "N/A (spend, not a conversion)",
        "Owner note": "ad_metrics.spend, campaign-level rows only (ad_set_id='' AND ad_id='').",
    },
    {
        "Metric": "Impressions",
        "Definition": "Number of times an ad was shown on screen.",
        "Source": "Meta",
        "Attribution window": "N/A",
        "Owner note": "ad_metrics.impressions.",
    },
    {
        "Metric": "Clicks",
        "Definition": "Number of clicks on an ad (all click types).",
        "Source": "Meta",
        "Attribution window": "N/A",
        "Owner note": "ad_metrics.clicks.",
    },
    {
        "Metric": "CTR",
        "Definition": "Click-through rate = Clicks ÷ Impressions × 100.",
        "Source": "Meta",
        "Attribution window": "N/A",
        "Owner note": "Overview CTR is SUM(clicks)/SUM(impressions) for the period, not an "
                       "average of daily CTR values.",
    },
    {
        "Metric": "CPC",
        "Definition": "Cost per click = Spend ÷ Clicks.",
        "Source": "Meta",
        "Attribution window": "N/A",
        "Owner note": "get_kpi_summary.avg_cpc.",
    },
    {
        "Metric": "CPM",
        "Definition": "Cost per 1,000 impressions = Spend ÷ Impressions × 1000.",
        "Source": "Meta",
        "Attribution window": "N/A",
        "Owner note": "get_kpi_summary.avg_cpm.",
    },
    {
        "Metric": "Reach",
        "Definition": "Estimated number of unique people who saw the ads.",
        "Source": "Meta",
        "Attribution window": "N/A",
        "Owner note": "Summed across campaigns on Overview -- Meta reach does not de-duplicate "
                       "across campaigns, so the summed figure is directional, not a true "
                       "unique-person count.",
    },
    {
        "Metric": "Frequency",
        "Definition": "Average number of times a person saw an ad = Impressions ÷ Reach.",
        "Source": "Meta",
        "Attribution window": "N/A",
        "Owner note": "Shown in the Overview 'ROAS vs Frequency Watch' strip and the Funnel "
                       "page; fatigue threshold flagged at ≥3.0.",
    },
    {
        "Metric": "ROAS (platform)",
        "Definition": "Meta's self-reported Return on Ad Spend (attributed revenue ÷ spend), "
                      "spend-weighted across campaigns.",
        "Source": "Meta",
        "Attribution window": "7-day click / 1-day view",
        "Owner note": "Overview 'Blended ROAS' card. Platform-side only -- not reconciled "
                       "against Stripe revenue. See the Reconciliation block for the "
                       "conversion-count (not revenue) cross-check.",
    },
    {
        "Metric": "FSD",
        "Definition": "Form Submit Deposits -- count of the on-page 'form_submit' custom "
                      "conversion event. Gate 1 of the two-gate NSM framework: everyone who "
                      "submitted the deposit form, including both paid and still-pending.",
        "Source": "Meta",
        "Attribution window": "7-day click / 1-day view",
        "Owner note": "meta_form_submit_deposit. Never blend with GA4 sessions/purchases or "
                       "Stripe paid counts -- always side-by-side.",
    },
    {
        "Metric": "CPR (FSD)",
        "Definition": "Cost Per Result = Spend ÷ FSD.",
        "Source": "Meta",
        "Attribution window": "7-day click / 1-day view",
        "Owner note": "Primary campaign-tiering metric -- drives the ★ SCALE / MAINTAIN / "
                       "REDUCE / PAUSED tags on the Overview campaign table.",
    },
    {
        "Metric": "Paid Rate",
        "Definition": "Percent of FSD submissions that became a completed Stripe payment -- "
                      "the Gate 2 conversion rate (FSD → Paid).",
        "Source": "Stripe",
        "Attribution window": "N/A -- ground truth, no attribution window",
        "Owner note": "get_stripe_period_totals / get_stripe_daily .paid_rate. Practitioner "
                       "benchmark: some drop-off between FSD and paid is normal; a rate near "
                       "0% across several days usually means a payment-page or webhook issue, "
                       "not a tracking gap.",
    },
    {
        "Metric": "CPaC",
        "Definition": "Cost Per Actual Conversion = Total Spend ÷ Stripe-paid count. Combines "
                      "ad efficiency (CPR) with landing-page paid rate.",
        "Source": "Meta spend + Stripe paid",
        "Attribution window": "N/A -- Stripe side is ground truth",
        "Owner note": "Overview 'CPaC' card -- the dashboard's best estimate of true "
                       "acquisition cost.",
    },
    {
        "Metric": "MER",
        "Definition": "Marketing Efficiency Ratio = Total revenue ÷ Total ad spend, across "
                      "all channels.",
        "Source": "Stripe revenue ÷ Meta spend",
        "Attribution window": "N/A -- ground truth revenue, no attribution window",
        "Owner note": "Not currently rendered as its own KPI card in this dashboard -- listed "
                       "here for reference since it's a common team term. CPaC and Blended "
                       "ROAS are the closest cards to it today; ping the data-tracking owner "
                       "before quoting an MER figure that isn't on-screen.",
    },
    {
        "Metric": "GA4 Sessions",
        "Definition": "Number of GA4 sessions on the landing page, joined by exact UTM "
                      "campaign-name match.",
        "Source": "GA4",
        "Attribution window": "Last-click (GA4 default channel grouping)",
        "Owner note": "get_ga4_kpi.total_sessions / ga4_metrics.sessions. Includes GA4 "
                       "consent-mode gaps -- visitors who declined cookies are undercounted.",
    },
    {
        "Metric": "Engagement Rate",
        "Definition": "Share of sessions that were 'engaged' (lasted 10+ seconds, had a "
                      "conversion event, or had 2+ pageviews). GA4's complement to bounce "
                      "rate (engagement rate ≈ 1 − bounce rate).",
        "Source": "GA4",
        "Attribution window": "N/A -- session-level engagement metric, not a conversion",
        "Owner note": "The dashboard currently surfaces Avg Bounce Rate on the Campaign "
                       "Detail page (get_campaign_ga4_engagement.avg_bounce_rate), not "
                       "engagement rate directly -- treat bounce rate as the inverse signal "
                       "until a dedicated engagement-rate column is ingested.",
    },
    {
        "Metric": "ga4_purchases_lastclick",
        "Definition": "GA4-reported purchase (conversion) events, attributed using GA4's "
                      "last-click model.",
        "Source": "GA4",
        "Attribution window": "Last-click",
        "Owner note": "ga4_metrics.ga4_purchases_lastclick. Never blend with Meta purchases -- "
                       "see the Overview Reconciliation block and the 'Why don't these match?' "
                       "explainer.",
    },
    {
        "Metric": "meta_purchases_7dclick",
        "Definition": "Meta-reported purchase (conversion) events, attributed within Meta's "
                      "7-day click / 1-day view window.",
        "Source": "Meta",
        "Attribution window": "7-day click / 1-day view",
        "Owner note": "ad_metrics.meta_purchases_7dclick. Never blend with GA4 purchases -- "
                       "side-by-side only, per CLAUDE.md.",
    },
]


def _render_glossary_table() -> None:
    df = pd.DataFrame(_GLOSSARY_ROWS)
    st.dataframe(df, hide_index=True, use_container_width=True)


# ---------------------------------------------------------------------------
# Main flow
# ---------------------------------------------------------------------------
settings = DashboardSettings()  # type: ignore[call-arg]

if not _check_auth(settings.dashboard_password):
    st.stop()

st.page_link("Overview.py", label="<- Back to Overview")
st.title("Metric Glossary")
st.caption(
    "Every metric shown on this dashboard -- what it means, where it comes from, its "
    "attribution window (if any), and gotchas to know before quoting it."
)

_render_glossary_table()

st.divider()

# ---------------------------------------------------------------------------
# Changelog -- static markdown, append new entries at the top.
# ---------------------------------------------------------------------------
st.subheader("Changelog")
st.markdown(
    "- **2026-07-21** -- Scope line + triangle reconciliation added; attribution windows "
    "now labeled on every page."
)
