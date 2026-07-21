"""Shared UI components for the Ads Performance Dashboard (Phase D trust/UX).

Follows the same shared-module convention as db.py / settings.py: imported by
Overview.py and by the standalone pages (D-19 rule — pages don't import from
each other, only from these shared modules).

Contains:
- The always-visible scope line rendered under every page title, so a viewer
  never has to guess what date range / campaign filter / attribution window
  produced the numbers on screen.
- Pure, unit-testable helpers for the Meta/GA4/Stripe reconciliation ("triangle")
  block, plus the block's renderer.

House rule (CLAUDE.md): Meta conversion fields use the meta_ prefix, GA4 use
the ga4_ prefix. Never blend or average Meta and GA4 conversion numbers --
always show side-by-side with an attribution explanation.
"""
from __future__ import annotations

from datetime import date
from typing import Any

import streamlit as st

# ---------------------------------------------------------------------------
# Attribution window labels -- ONE place to update these strings.
# ---------------------------------------------------------------------------
META_WINDOW_LABEL = "Meta window: 7-day click / 1-day view"
GA4_WINDOW_LABEL = "GA4: last-click"

# Shorter forms used inline in the triangle block captions.
META_WINDOW_CAPTION = "7-day click / 1-day view"
GA4_WINDOW_CAPTION = "Last-click"
STRIPE_CAPTION = "Stripe payment records — ground truth"

# Gap-chip thresholds (practitioner benchmark: 15-30% platform-vs-actual
# variance is normal; >40% signals a tracking problem).
_GAP_GREEN_MAX = 20.0
_GAP_AMBER_MAX = 40.0

_GAP_CHIP_EMOJI = {"green": "🟢", "amber": "🟡", "red": "🔴", "gray": "⚪"}


def _fmt_scope_date(d: date) -> str:
    """'15 Jul' style short date -- day first, no leading zero, no year."""
    return f"{d.day} {d.strftime('%b')}"


def render_scope_line(
    start: date,
    end: date,
    campaign_filter: str | None = None,
    attribution_note: str | None = None,
) -> str:
    """Render the always-visible scope caption directly under a page title.

    Example output:
        "📅 15 Jul – 21 Jul 2026 (vs prior 7d) · Campaigns: All · "
        "Meta window: 7-day click / 1-day view · GA4: last-click"

    Args:
        start: period start date (inclusive).
        end: period end date (inclusive).
        campaign_filter: human-readable campaign scope, e.g. "All" or a single
            campaign name (Campaign Detail page). Defaults to "All".
        attribution_note: override for the trailing attribution-window note.
            Defaults to the standard Meta + GA4 window labels above.

    Returns the rendered string (also written via st.caption) so callers/tests
    can assert on the exact text without re-parsing the DOM.
    """
    period_days = (end - start).days + 1
    date_part = f"{_fmt_scope_date(start)} – {_fmt_scope_date(end)} {end.year}"
    prior_part = f"(vs prior {period_days}d)"
    campaigns_part = f"Campaigns: {campaign_filter or 'All'}"
    note = attribution_note if attribution_note is not None else (
        f"{META_WINDOW_LABEL} · {GA4_WINDOW_LABEL}"
    )

    line = f"📅 {date_part} {prior_part} · {campaigns_part} · {note}"
    st.caption(line)
    return line


# ---------------------------------------------------------------------------
# Triangle reconciliation -- pure helpers (unit-testable, no Streamlit calls)
# ---------------------------------------------------------------------------
def compute_gap_pct(a: float, b: float) -> float | None:
    """Percentage gap between two counts, relative to the larger value.

    Returns None when both values are zero (gap is undefined, not zero).
    """
    a = float(a or 0)
    b = float(b or 0)
    if a == 0 and b == 0:
        return None
    denom = max(a, b)
    return abs(a - b) / denom * 100.0


def max_pairwise_gap_pct(meta: float, ga4: float, actual: float) -> float | None:
    """Largest pairwise % gap among Meta / GA4 / actual-paid counts.

    Pairs where both values are zero are ignored (undefined, not zero-gap).
    Returns None if every pair is undefined (i.e. all three counts are zero).
    """
    pairs = [(meta, ga4), (meta, actual), (ga4, actual)]
    gaps = [g for g in (compute_gap_pct(x, y) for x, y in pairs) if g is not None]
    return max(gaps) if gaps else None


def gap_chip_color(gap_pct: float | None) -> str:
    """Classify the max pairwise gap % into a trust-signal color.

    Practitioner benchmark: 15-30% platform-vs-actual variance is normal
    for attribution-window differences; >40% signals a tracking problem.
        gap <= 20%        -> "green"  (normal)
        20% < gap <= 40%   -> "amber"  (watch)
        gap > 40%          -> "red"    (investigate)
        gap is None        -> "gray"   (no data)
    """
    if gap_pct is None:
        return "gray"
    if gap_pct <= _GAP_GREEN_MAX:
        return "green"
    if gap_pct <= _GAP_AMBER_MAX:
        return "amber"
    return "red"


def render_reconciliation_block(
    meta_purchases: int,
    ga4_purchases: int,
    actual_paid: int,
) -> None:
    """Render the Meta / GA4 / actual-paid ("triangle") reconciliation block.

    Three side-by-side counts (never blended -- CLAUDE.md house rule), each
    with its attribution-window caption, plus a fourth gap chip showing the
    max pairwise variance -- and an expander explaining why the numbers
    normally disagree.
    """
    col1, col2, col3, col4 = st.columns(4)

    col1.metric("Meta-reported purchases (7d-click/1d-view)", f"{int(meta_purchases):,}")
    col1.caption(META_WINDOW_CAPTION)

    col2.metric("GA4 purchases (last-click)", f"{int(ga4_purchases):,}")
    col2.caption(GA4_WINDOW_CAPTION)

    col3.metric("Actual paid (Stripe)", f"{int(actual_paid):,}")
    col3.caption(STRIPE_CAPTION)

    gap = max_pairwise_gap_pct(meta_purchases, ga4_purchases, actual_paid)
    color = gap_chip_color(gap)
    emoji = _GAP_CHIP_EMOJI[color]
    gap_text = f"{gap:.0f}%" if gap is not None else "—"
    col4.metric("Max gap", f"{emoji} {gap_text}")
    col4.caption("Largest pairwise variance")

    with st.expander("Why don't these match?"):
        st.markdown(
            "Meta credits a purchase to an ad within its 7-day-click / "
            "1-day-view attribution window, so a sale can count even if the "
            "click (or view) happened up to a week earlier or was influenced "
            "by other channels. GA4 instead uses last-click within its own "
            "session model, and consent-mode opt-outs mean some conversions "
            "are never recorded there at all. Both ad platforms also tend to "
            "over-attribute conversions that would have happened anyway, "
            "which inflates their self-reported numbers relative to reality. "
            "Stripe (via Shopify checkout) reflects only real, completed "
            "payments, so treat it as ground truth and the platform numbers "
            "as directional signals rather than exact counts."
        )
