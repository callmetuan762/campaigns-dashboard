"""Unit tests for src/alerts/anomaly.py — the pure tracking-breakage detector (Phase C).

Covers the three required cases from the Phase C spec:
  - drop with traffic = no alert (real dip)
  - drop without traffic = alert (broken tracking)
  - empty history = no alert (insufficient data)
plus boundary conditions and the range-scanning wrapper.
"""
from __future__ import annotations

from src.alerts.anomaly import (
    CRITICAL_EVENTS,
    detect_tracking_anomaly,
    find_anomalies_in_range,
)


# ---------------------------------------------------------------------------
# CRITICAL_EVENTS
# ---------------------------------------------------------------------------

def test_critical_events_are_the_three_funnel_steps():
    assert set(CRITICAL_EVENTS) == {"begin_checkout", "lead_submit", "purchase"}


# ---------------------------------------------------------------------------
# detect_tracking_anomaly — required cases
# ---------------------------------------------------------------------------

def test_empty_history_is_no_alert():
    """Empty trailing_counts => not enough data to establish a baseline."""
    result = detect_tracking_anomaly(
        "purchase", "2026-05-19", [], 5, [500.0] * 7, 480.0
    )
    assert result is None


def test_drop_with_comparable_traffic_drop_is_no_alert():
    """Event dropped >50% AND sessions dropped >=20% => real traffic dip, not breakage."""
    trailing_counts = [100.0] * 7
    target_count = 20.0  # 80% drop
    trailing_sessions = [1000.0] * 7
    target_sessions = 700.0  # 30% drop -- >= 20% threshold
    result = detect_tracking_anomaly(
        "purchase", "2026-05-19", trailing_counts, target_count,
        trailing_sessions, target_sessions,
    )
    assert result is None


def test_drop_without_traffic_drop_is_an_alert():
    """Event dropped >50% while sessions barely moved => tracking breakage."""
    trailing_counts = [100.0] * 7
    target_count = 10.0  # 90% drop
    trailing_sessions = [1000.0] * 7
    target_sessions = 980.0  # 2% drop -- well under the 20% traffic threshold
    result = detect_tracking_anomaly(
        "begin_checkout", "2026-05-19", trailing_counts, target_count,
        trailing_sessions, target_sessions,
    )
    assert result is not None
    assert result["event_name"] == "begin_checkout"
    assert result["date"] == "2026-05-19"
    assert result["event_count"] == 10.0
    assert result["median_trailing"] == 100.0
    assert result["count_drop_pct"] == 90.0
    assert result["sessions_drop_pct"] == 2.0


def test_no_drop_is_no_alert():
    """Event count in line with (or above) the trailing median => nothing to flag."""
    result = detect_tracking_anomaly(
        "purchase", "2026-05-19", [100.0] * 7, 105.0, [1000.0] * 7, 1000.0
    )
    assert result is None


def test_small_drop_below_threshold_is_no_alert():
    """A 30% drop is below the 50% default threshold."""
    result = detect_tracking_anomaly(
        "purchase", "2026-05-19", [100.0] * 7, 70.0, [1000.0] * 7, 1000.0
    )
    assert result is None


def test_drop_threshold_boundary_exactly_50_is_no_alert():
    """count_drop_pct == drop_threshold_pct is NOT > threshold -> no alert (strict >)."""
    result = detect_tracking_anomaly(
        "purchase", "2026-05-19", [100.0] * 7, 50.0, [1000.0] * 7, 1000.0
    )
    assert result is None


def test_traffic_threshold_boundary_exactly_20_is_no_alert():
    """sessions_drop_pct == traffic_threshold_pct IS >= threshold -> no alert (real dip)."""
    result = detect_tracking_anomaly(
        "purchase", "2026-05-19", [100.0] * 7, 10.0, [1000.0] * 7, 800.0
    )
    assert result is None  # 90% event drop, but sessions dropped exactly 20%


def test_zero_median_baseline_is_no_alert():
    """A trailing history of all zeros has no meaningful baseline to compare against."""
    result = detect_tracking_anomaly(
        "lead_submit", "2026-05-19", [0.0, 0.0, 0.0], 0.0, [500.0] * 3, 500.0
    )
    assert result is None


def test_missing_sessions_data_still_allows_event_drop_to_fire():
    """No sessions signal at all (empty list) is treated as 0% traffic drop —
    conservative default that still lets a real event-count breakage fire."""
    result = detect_tracking_anomaly(
        "purchase", "2026-05-19", [100.0] * 7, 5.0, [], None
    )
    assert result is not None


def test_custom_thresholds_are_respected():
    """A looser drop_threshold_pct flags a drop the default threshold would ignore."""
    # 45% drop: below the 50% default threshold -> no alert with defaults.
    result_default = detect_tracking_anomaly(
        "purchase", "2026-05-19", [100.0] * 7, 55.0, [1000.0] * 7, 1000.0,
    )
    assert result_default is None

    # Same data, but a looser (40%) custom threshold -> now flagged.
    result_custom = detect_tracking_anomaly(
        "purchase", "2026-05-19", [100.0] * 7, 55.0, [1000.0] * 7, 1000.0,
        drop_threshold_pct=40.0,
    )
    assert result_custom is not None


# ---------------------------------------------------------------------------
# find_anomalies_in_range
# ---------------------------------------------------------------------------

def test_find_anomalies_in_range_detects_single_day_break():
    daily_counts = {
        "2026-05-11": 100.0, "2026-05-12": 100.0, "2026-05-13": 100.0,
        "2026-05-14": 100.0, "2026-05-15": 100.0, "2026-05-16": 100.0,
        "2026-05-17": 100.0,
        "2026-05-18": 5.0,   # breakage day
    }
    daily_sessions = {d: 1000.0 for d in daily_counts}
    anomalies = find_anomalies_in_range("purchase", daily_counts, daily_sessions)
    assert len(anomalies) == 1
    assert anomalies[0]["date"] == "2026-05-18"


def test_find_anomalies_in_range_no_data_is_empty():
    assert find_anomalies_in_range("purchase", {}, {}) == []


def test_find_anomalies_in_range_sparse_dates_do_not_manufacture_drops():
    """Missing dates are skipped from the trailing window, not treated as zero."""
    daily_counts = {"2026-05-10": 100.0, "2026-05-18": 90.0}
    daily_sessions = {"2026-05-10": 1000.0, "2026-05-18": 1000.0}
    anomalies = find_anomalies_in_range("purchase", daily_counts, daily_sessions)
    # 2026-05-10 has no trailing history at all (empty) -> skipped.
    # 2026-05-18's only "trailing" candidate dates (05-11..05-17) aren't present
    # in daily_counts, so its trailing window is also empty -> skipped.
    assert anomalies == []


def test_find_anomalies_in_range_returns_sorted_by_date():
    daily_counts = {
        "2026-05-11": 100.0, "2026-05-12": 100.0, "2026-05-13": 100.0,
        "2026-05-14": 100.0, "2026-05-15": 100.0, "2026-05-16": 100.0,
        "2026-05-17": 100.0, "2026-05-18": 5.0, "2026-05-19": 5.0,
    }
    daily_sessions = {d: 1000.0 for d in daily_counts}
    anomalies = find_anomalies_in_range("purchase", daily_counts, daily_sessions)
    dates = [a["date"] for a in anomalies]
    assert dates == sorted(dates)
