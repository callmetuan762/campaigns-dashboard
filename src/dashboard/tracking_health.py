"""Pure, unit-testable helpers for the Tracking Health page (Phase C).

Follows the same convention as src/dashboard/components.py: pure color-banding
functions live here (no Streamlit calls), so they can be unit tested directly
and imported by both the page and its tests without a Streamlit runtime.

House rule (CLAUDE.md): Meta and GA4 conversion numbers are never blended —
the purchase-divergence chip always shows both counts side-by-side.
"""
from __future__ import annotations

_CHIP_EMOJI = {"green": "\U0001f7e2", "amber": "\U0001f7e1", "red": "\U0001f534", "gray": "⚪"}


def click_session_ratio_color(ratio_pct: float | None) -> str:
    """Band the 7-day click->session ratio %%.

    >=70% green (healthy), 50-70% amber (watch), <50% red (investigate),
    None (no data / undefined) -> gray.
    """
    if ratio_pct is None:
        return "gray"
    if ratio_pct >= 70:
        return "green"
    if ratio_pct >= 50:
        return "amber"
    return "red"


def freshness_color(hours_since_last: float | None) -> str:
    """Band hours-since-last-ingestion for a critical event.

    <30h green (fresh), 30-54h amber (stale, watch), >54h red (broken),
    None (never ingested) -> gray.
    """
    if hours_since_last is None:
        return "gray"
    if hours_since_last < 30:
        return "green"
    if hours_since_last <= 54:
        return "amber"
    return "red"


def not_set_share_color(pct: float | None) -> str:
    """Band the "(not set)" campaign-attribution share %% on checkout events.

    <=30% green (normal), 30-60% amber (watch), >60% red (utm tagging broken),
    None (no data) -> gray.
    """
    if pct is None:
        return "gray"
    if pct <= 30:
        return "green"
    if pct <= 60:
        return "amber"
    return "red"


def chip_emoji(color: str) -> str:
    """Map a banding color name to its display emoji. Unknown colors -> gray dot."""
    return _CHIP_EMOJI.get(color, _CHIP_EMOJI["gray"])
