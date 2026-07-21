"""Unit tests for src/dashboard/components.py (Phase D trust/UX quick wins).

Covers the pure gap-chip color logic and the scope-line string builder.
"""
from __future__ import annotations

from datetime import date

from src.dashboard.components import (
    compute_gap_pct,
    gap_chip_color,
    max_pairwise_gap_pct,
    render_scope_line,
)


# ---------------------------------------------------------------------------
# compute_gap_pct
# ---------------------------------------------------------------------------
def test_compute_gap_pct_zero_diff() -> None:
    assert compute_gap_pct(100, 100) == 0.0


def test_compute_gap_pct_both_zero_is_undefined() -> None:
    assert compute_gap_pct(0, 0) is None


def test_compute_gap_pct_basic() -> None:
    # |100-80| / max(100,80) * 100 = 20.0
    assert compute_gap_pct(100, 80) == 20.0
    assert compute_gap_pct(80, 100) == 20.0


# ---------------------------------------------------------------------------
# max_pairwise_gap_pct
# ---------------------------------------------------------------------------
def test_max_pairwise_gap_pct_picks_largest_pair() -> None:
    # meta=100, ga4=90 (10% gap), actual=50 (meta-actual = 50%, ga4-actual = 44.4%)
    gap = max_pairwise_gap_pct(100, 90, 50)
    assert gap is not None
    assert round(gap, 1) == 50.0


def test_max_pairwise_gap_pct_all_equal_is_zero() -> None:
    assert max_pairwise_gap_pct(10, 10, 10) == 0.0


def test_max_pairwise_gap_pct_all_zero_is_none() -> None:
    assert max_pairwise_gap_pct(0, 0, 0) is None


# ---------------------------------------------------------------------------
# gap_chip_color -- the pure, testable helper behind the reconciliation chip
# ---------------------------------------------------------------------------
def test_gap_chip_color_none_is_gray() -> None:
    assert gap_chip_color(None) == "gray"


def test_gap_chip_color_green_at_zero() -> None:
    assert gap_chip_color(0.0) == "green"


def test_gap_chip_color_green_up_to_boundary() -> None:
    assert gap_chip_color(20.0) == "green"


def test_gap_chip_color_amber_just_above_boundary() -> None:
    assert gap_chip_color(20.1) == "amber"


def test_gap_chip_color_amber_up_to_boundary() -> None:
    assert gap_chip_color(40.0) == "amber"


def test_gap_chip_color_red_above_boundary() -> None:
    assert gap_chip_color(40.1) == "red"


def test_gap_chip_color_red_large_gap() -> None:
    assert gap_chip_color(95.0) == "red"


# ---------------------------------------------------------------------------
# render_scope_line -- returns the exact expected string
# ---------------------------------------------------------------------------
def test_render_scope_line_default_note_and_all_campaigns() -> None:
    start = date(2026, 7, 15)
    end = date(2026, 7, 21)
    line = render_scope_line(start, end, campaign_filter="All")
    assert line == (
        "📅 15 Jul – 21 Jul 2026 (vs prior 7d) · Campaigns: All · "
        "Meta window: 7-day click / 1-day view · GA4: last-click"
    )


def test_render_scope_line_defaults_campaign_filter_to_all() -> None:
    start = date(2026, 1, 1)
    end = date(2026, 1, 1)
    line = render_scope_line(start, end)
    assert "Campaigns: All" in line
    assert "(vs prior 1d)" in line


def test_render_scope_line_named_campaign() -> None:
    start = date(2026, 7, 1)
    end = date(2026, 7, 30)
    line = render_scope_line(start, end, campaign_filter="Nowa | SALES | Brand")
    assert "Campaigns: Nowa | SALES | Brand" in line
    assert "(vs prior 30d)" in line


def test_render_scope_line_custom_attribution_note() -> None:
    start = date(2026, 1, 1)
    end = date(2026, 1, 7)
    line = render_scope_line(start, end, attribution_note="Custom note")
    assert line.endswith("Custom note")
