"""Tracking-breakage anomaly detector (Phase C).

Pure, dependency-free logic shared by:
  - src.alerts.engine (the TRACKING_ANOMALY alert, evaluated after GA4 ingest)
  - src.dashboard.pages.8_Tracking_Health (the per-event volume trend chart's
    anomaly markers)

The core idea: a day-over-day drop in a critical GA4 event's count is only a
tracking-breakage signal if it happens WITHOUT a comparable drop in overall
session traffic. If sessions dropped just as much, that's a real traffic dip,
not broken tracking — the asymmetry between "event cratered" and "traffic held
steady" is what indicates the tracking pipe itself broke (consent banner,
sGTM /g/collect down, a bad deploy, etc.).
"""
from __future__ import annotations

import statistics
from typing import Any

# Critical events this detector (and the alert engine) watches — the funnel
# steps whose disappearance would silently hide real revenue/lead loss.
CRITICAL_EVENTS: tuple[str, ...] = ("begin_checkout", "lead_submit", "purchase")

# Defaults per Phase C spec: >50% event-count drop vs trailing 7-day median
# while sessions dropped <20% (that asymmetry = tracking breakage, not a
# traffic dip).
DEFAULT_DROP_THRESHOLD_PCT = 50.0
DEFAULT_TRAFFIC_THRESHOLD_PCT = 20.0


def detect_tracking_anomaly(
    event_name: str,
    target_date: str,
    trailing_counts: list[float],
    target_count: float,
    trailing_sessions: list[float] | None = None,
    target_sessions: float | None = None,
    *,
    drop_threshold_pct: float = DEFAULT_DROP_THRESHOLD_PCT,
    traffic_threshold_pct: float = DEFAULT_TRAFFIC_THRESHOLD_PCT,
) -> dict[str, Any] | None:
    """Return an anomaly dict if `target_date`'s event count looks like broken
    tracking rather than a real traffic dip. Returns None otherwise.

    Args:
        event_name: critical event name (e.g. "begin_checkout").
        target_date: ISO date string being evaluated.
        trailing_counts: event counts for the (up to) 7 days immediately
            before target_date. Empty list => "empty history = no alert"
            (not enough data to establish a baseline).
        target_count: event count on target_date.
        trailing_sessions: GA4 sessions for the same trailing window (optional
            — omitted/empty is treated as "no traffic signal available",
            which conservatively still allows the event-drop check to fire).
        target_sessions: GA4 sessions on target_date.
        drop_threshold_pct: minimum % drop (vs trailing median) to consider.
        traffic_threshold_pct: sessions must have dropped LESS than this % for
            the event drop to be flagged as tracking breakage.

    Returns:
        None when there's insufficient history, no meaningful drop, or the
        drop is accompanied by a comparable traffic drop (real dip, not
        breakage). Otherwise a dict with event_name, date, event_count,
        median_trailing, count_drop_pct, sessions_drop_pct.
    """
    if not trailing_counts:
        return None  # empty history = no alert

    median_count = statistics.median(trailing_counts)
    if median_count <= 0:
        return None  # no meaningful baseline to measure a drop against

    count_drop_pct = (median_count - target_count) / median_count * 100.0
    if count_drop_pct <= drop_threshold_pct:
        return None  # not a big enough drop

    sessions_drop_pct = 0.0
    if trailing_sessions:
        median_sessions = statistics.median(trailing_sessions)
        if median_sessions > 0:
            effective_target = target_sessions if target_sessions is not None else 0.0
            sessions_drop_pct = (median_sessions - effective_target) / median_sessions * 100.0

    if sessions_drop_pct >= traffic_threshold_pct:
        return None  # traffic itself dropped comparably -> real dip, not breakage

    return {
        "event_name": event_name,
        "date": target_date,
        "event_count": target_count,
        "median_trailing": median_count,
        "count_drop_pct": round(count_drop_pct, 1),
        "sessions_drop_pct": round(sessions_drop_pct, 1),
    }


def find_anomalies_in_range(
    event_name: str,
    daily_counts: dict[str, float],
    daily_sessions: dict[str, float] | None = None,
    *,
    lookback_days: int = 7,
    drop_threshold_pct: float = DEFAULT_DROP_THRESHOLD_PCT,
    traffic_threshold_pct: float = DEFAULT_TRAFFIC_THRESHOLD_PCT,
) -> list[dict[str, Any]]:
    """Run detect_tracking_anomaly across every date in `daily_counts`.

    `daily_counts` and `daily_sessions` are {ISO date: value} maps. For each
    date, the trailing window is the `lookback_days` calendar-adjacent entries
    that exist in the maps immediately before it (missing dates are simply
    skipped, not treated as zero — sparse data should not manufacture drops).

    Returns anomalies sorted by date ascending — used to place markers on the
    dashboard's per-event trend chart.
    """
    from datetime import date as _date, timedelta as _td

    daily_sessions = daily_sessions or {}
    sorted_dates = sorted(daily_counts.keys())
    anomalies: list[dict[str, Any]] = []

    for target_date in sorted_dates:
        try:
            target_d = _date.fromisoformat(target_date)
        except ValueError:
            continue

        trailing_counts: list[float] = []
        trailing_sessions: list[float] = []
        for offset in range(1, lookback_days + 1):
            d = (target_d - _td(days=offset)).isoformat()
            if d in daily_counts:
                trailing_counts.append(daily_counts[d])
            if d in daily_sessions:
                trailing_sessions.append(daily_sessions[d])

        result = detect_tracking_anomaly(
            event_name,
            target_date,
            trailing_counts,
            daily_counts[target_date],
            trailing_sessions,
            daily_sessions.get(target_date),
            drop_threshold_pct=drop_threshold_pct,
            traffic_threshold_pct=traffic_threshold_pct,
        )
        if result is not None:
            anomalies.append(result)

    return anomalies
