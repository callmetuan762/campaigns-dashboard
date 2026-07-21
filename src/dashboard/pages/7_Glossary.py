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
        "Metric": "Reach (sum of daily)",
        "Definition": "Estimated number of unique people who saw the ads, summed across the "
                      "days in the selected period.",
        "Source": "Meta",
        "Attribution window": "N/A",
        "Owner note": "Overview secondary KPI row. Daily reach summed -- overcounts unique "
                       "people across days (the same person seen on multiple days is counted "
                       "once per day), so treat this as directional volume, not a true "
                       "unique-person count for the period.",
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
        "Metric": "Meta ROAS (7d-click)",
        "Definition": "Meta's self-reported Return on Ad Spend (attributed revenue ÷ spend), "
                      "spend-weighted across campaigns.",
        "Source": "Meta",
        "Attribution window": "7-day click / 1-day view",
        "Owner note": "Overview KPI row (v2, 2026-07-22) -- honestly relabeled from the old "
                       "'Blended ROAS' card, which was never actually blended: this is, and "
                       "always was, spend-weighted Meta *platform* ROAS only. Platform-side, "
                       "expect over-reporting -- compare against MER (blended), which is "
                       "Shopify-anchored ground truth. Not reconciled against Shopify revenue "
                       "directly; see the Reconciliation block for the conversion-count "
                       "(not revenue) cross-check.",
    },
    {
        "Metric": "MER (blended)",
        "Definition": "Marketing Efficiency Ratio = Shopify paid revenue ÷ Meta ad spend -- "
                      "attribution-free (no click/view window on either side).",
        "Source": "Shopify revenue ÷ Meta spend",
        "Attribution window": "N/A -- attribution-free",
        "Owner note": "Overview KPI row (v2, 2026-07-22): get_shopify_paid_summary.revenue ÷ "
                       "get_kpi_summary.total_spend, respecting orders_valid_from. Compare "
                       "against Meta ROAS (7d-click) -- MER is the trustworthy one when the "
                       "two disagree, since it isn't subject to platform attribution-window "
                       "over-reporting.",
    },
    {
        "Metric": "Blended CAC",
        "Definition": "Blended Customer Acquisition Cost = Meta spend ÷ Shopify paid order "
                      "count -- the true cost per preorder across all Meta spend.",
        "Source": "Meta spend ÷ Shopify paid count",
        "Attribution window": "N/A -- Shopify side is ground truth",
        "Owner note": "Overview KPI row (v2, 2026-07-22). Replaces the deposit-era CPaC card "
                       "for the current (Shopify-checkout, no Stripe/$1-deposit step) funnel. "
                       "Lower is better -- the card's delta color is inverted accordingly.",
    },
    {
        "Metric": "Pre-orders",
        "Definition": "Count of Shopify orders with financial_status = 'paid' in the selected "
                      "period.",
        "Source": "Shopify",
        "Attribution window": "N/A -- ground truth, no attribution window",
        "Owner note": "Overview KPI row (v2, 2026-07-22). get_shopify_paid_summary.count, "
                       "respecting the orders_valid_from cutoff that excludes pre-launch/test "
                       "orders (query-time filter only, no rows deleted).",
    },
    {
        "Metric": "LPV → Checkout (Meta-attributed)",
        "Definition": "Meta begin_checkout ÷ Meta landing_page_views, expressed as a %.",
        "Source": "Meta",
        "Attribution window": "7-day click / 1-day view",
        "Owner note": "Overview KPI row (v2, 2026-07-22). Platform-side only -- both the "
                       "numerator and denominator are Meta-reported figures, so this measures "
                       "on-site checkout-intent conversion as Meta sees it, not a "
                       "Shopify-verified rate.",
    },
    {
        "Metric": "FSD",
        "Definition": "Form Submit Deposits -- count of the on-page 'form_submit' custom "
                      "conversion event. Gate 1 of the two-gate NSM framework: everyone who "
                      "submitted the deposit form, including both paid and still-pending.",
        "Source": "Meta",
        "Attribution window": "7-day click / 1-day view",
        "Owner note": "meta_form_submit_deposit. Deposit/Stripe-era metric -- Overview (v2, "
                       "2026-07-22) moved this behind the 'Legacy deposit funnel (FSD/Stripe "
                       "era)' expander, which only renders when there was FSD activity in the "
                       "selected range (the $1-deposit/Stripe step no longer exists in the "
                       "live funnel). Never blend with GA4 sessions/purchases or Shopify order "
                       "counts -- always side-by-side.",
    },
    {
        "Metric": "CPR (FSD)",
        "Definition": "Cost Per Result = Spend ÷ FSD.",
        "Source": "Meta",
        "Attribution window": "7-day click / 1-day view",
        "Owner note": "Deposit/Stripe-era metric, now inside the Overview legacy expander "
                       "(see FSD above). Primary campaign-tiering metric -- still drives the "
                       "★ SCALE / MAINTAIN / REDUCE / PAUSED tags on the Overview campaign "
                       "table.",
    },
    {
        "Metric": "Paid Rate",
        "Definition": "Percent of FSD submissions that became a completed Stripe payment -- "
                      "the Gate 2 conversion rate (FSD → Paid).",
        "Source": "Stripe",
        "Attribution window": "N/A -- ground truth, no attribution window",
        "Owner note": "get_stripe_period_totals / get_stripe_daily .paid_rate. Deposit/"
                       "Stripe-era metric, now inside the Overview legacy expander. "
                       "Practitioner benchmark: some drop-off between FSD and paid is normal; "
                       "a rate near 0% across several days usually means a payment-page or "
                       "webhook issue, not a tracking gap.",
    },
    {
        "Metric": "CPaC",
        "Definition": "Cost Per Actual Conversion = Total Spend ÷ Stripe-paid count. Combines "
                      "ad efficiency (CPR) with landing-page paid rate.",
        "Source": "Meta spend + Stripe paid",
        "Attribution window": "N/A -- Stripe side is ground truth",
        "Owner note": "Deposit/Stripe-era metric, now inside the Overview 'Legacy deposit "
                       "funnel (FSD/Stripe era)' expander. Superseded by Blended CAC (Shopify- "
                       "anchored) for the current funnel.",
    },
    {
        "Metric": "GA4 Sessions (all)",
        "Definition": "Total GA4 sessions across ALL traffic, including untagged / "
                      "'(not set)' campaign_utm rows -- no campaign filter.",
        "Source": "GA4",
        "Attribution window": "N/A -- raw session count, not a conversion",
        "Owner note": "Overview KPI row (v2, 2026-07-22). get_total_sessions_summary, sourced "
                       "from ga4_landing_pages (which carries no campaign filter) -- NOT "
                       "ga4_metrics, which excludes '(not set)' rows and therefore undercounts "
                       "true GA4 traffic. Compare against 'GA4 Sessions (campaign-attributed)' "
                       "below to see how much traffic GA4 couldn't tie to a campaign.",
    },
    {
        "Metric": "GA4 Sessions (campaign-attributed)",
        "Definition": "Number of GA4 sessions joined to a specific campaign by exact UTM "
                      "campaign-name match.",
        "Source": "GA4",
        "Attribution window": "Last-click (GA4 default channel grouping)",
        "Owner note": "get_ga4_kpi.total_sessions / ga4_metrics.sessions (this was previously "
                       "just labeled 'GA4 Sessions' on Overview; see 'GA4 Sessions (all)' "
                       "above for the property-wide figure it undercounts against). Includes "
                       "GA4 consent-mode gaps -- visitors who declined cookies are "
                       "undercounted -- plus every session GA4 couldn't tie to a campaign "
                       "(the attribution gap, below).",
    },
    {
        "Metric": "Capture Gap",
        "Definition": "1 − (GA4 Sessions, all traffic) ÷ Meta Landing-Page Views -- the share "
                      "of Meta-counted visits GA4 never tracked at all.",
        "Source": "Meta LPV vs GA4 (all sessions)",
        "Attribution window": "N/A -- a coverage/tracking metric, not a conversion",
        "Owner note": "Funnel page 'Click → Session Gap' section (get_click_session_gap."
                       "capture_gap_pct). Driven by consent-denied visitors (no GA4 hit fires "
                       "at all), in-app browsers/ad blockers, slow tag load, or server 503s -- "
                       "a real tracking/capture-coverage problem, distinct from the "
                       "Attribution Gap below. Bands: 🟢 ≤30% normal · 🟡 30–50% watch · "
                       "🔴 >50% investigate consent rate / tag latency / transport failures.",
    },
    {
        "Metric": "Attribution Gap",
        "Definition": "1 − (campaign-attributed GA4 sessions) ÷ (GA4 sessions, all traffic) "
                      "-- of the sessions GA4 DID track, the share it couldn't tie back to a "
                      "campaign.",
        "Source": "GA4 (attributed vs all)",
        "Attribution window": "N/A -- a UTM-tagging coverage metric, not a conversion",
        "Owner note": "Funnel page 'Click → Session Gap' section (get_click_session_gap."
                       "attribution_gap_pct). Driven by consent-denied sessions that still "
                       "fire a bare pageview without a campaign-carrying hit, plus genuinely "
                       "untagged/organic-looking traffic -- a UTM-tagging discipline issue, "
                       "NOT the same failure mode as the Capture Gap above (which is about "
                       "whether GA4 tracked the visit at all). Bands: 🟢 ≤40% normal · "
                       "🟡 40–70% watch · 🔴 >70% investigate utm tagging discipline.",
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
        "Owner note": "ga4_metrics.ga4_purchases_lastclick -- campaign-attributed only. The "
                       "Overview Reconciliation block's GA4 leg (v2, 2026-07-22) uses the "
                       "property-wide ga4_events 'purchase' event count instead (see below), "
                       "with this campaign-attributed figure shown as a smaller caption "
                       "underneath to make the utm-loss visible. Never blend with Meta "
                       "purchases -- see the 'Why don't these match?' explainer.",
    },
    {
        "Metric": "GA4 purchases (property-wide)",
        "Definition": "SUM of ga4_events where event_name = 'purchase', across the whole GA4 "
                      "property -- not restricted to sessions GA4 could tie to a campaign.",
        "Source": "GA4",
        "Attribution window": "Last-click, property-wide",
        "Owner note": "Overview Reconciliation block (v2, 2026-07-22). Much larger than "
                       "ga4_purchases_lastclick / ga4_metrics because the server-side purchase "
                       "event loses utm_campaign for most orders -- most real purchases have "
                       "no campaign attribution at all, not because GA4 missed them, but "
                       "because the utm never made it onto that specific event. Treat this as "
                       "the true GA4 purchase-event count and the campaign-attributed figure "
                       "as a narrower, utm-loss-affected subset of it.",
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
    "- **2026-07-22** -- Overview v2 — MER/CAC/preorder KPI row, Shopify-anchored "
    "reconciliation, gap decomposition; deposit-era tiles moved behind legacy expander.\n"
    "- **2026-07-21** -- Scope line + triangle reconciliation added; attribution windows "
    "now labeled on every page."
)
