"""Sync SQLite data access for the Streamlit dashboard.

All queries use ? positional params (sqlite3 style) and campaign-level
ad_metrics rows only (ad_set_id = '' AND ad_id = '').
Never blends Meta and GA4 conversion numbers.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator


@contextmanager
def _conn(db_path: Path) -> Generator[sqlite3.Connection, None, None]:
    """Open a sync sqlite3 connection with WAL mode + 5s busy_timeout.

    WAL pragma is persisted on the DB file (bot's DBClient already sets it), but
    setting it again is idempotent and protects the dashboard against running
    against a fresh/empty DB that has not yet been opened by the writer.
    busy_timeout=5000 mirrors src/db/client.py so dashboard reads block briefly
    instead of raising "database is locked" during bot ingest windows.
    """
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL;")
    con.execute("PRAGMA busy_timeout=5000;")
    try:
        yield con
    finally:
        con.close()


def get_kpi_summary(db_path: Path, start_date: str, end_date: str) -> dict[str, Any]:
    sql = """
        SELECT
            COALESCE(SUM(m.spend), 0)                               AS total_spend,
            CASE WHEN SUM(m.spend) > 0
                 THEN SUM(m.spend * m.roas) / SUM(m.spend)
                 ELSE 0 END                                         AS weighted_roas,
            COALESCE(SUM(m.meta_form_submit_deposit), 0)            AS total_deposits,
            CASE WHEN SUM(m.meta_form_submit_deposit) > 0
                 THEN SUM(m.spend) / SUM(m.meta_form_submit_deposit)
                 ELSE NULL END                                      AS cpd,
            COUNT(DISTINCT m.campaign_id)                           AS active_campaigns
        FROM ad_metrics m
        WHERE m.ad_set_id = '' AND m.ad_id = ''
          AND m.date BETWEEN ? AND ?
    """
    with _conn(db_path) as con:
        row = con.execute(sql, (start_date, end_date)).fetchone()
    if not row:
        return {"total_spend": 0, "weighted_roas": 0, "total_deposits": 0,
                "cpd": None, "active_campaigns": 0}
    return dict(row)


def get_ga4_kpi(db_path: Path, start_date: str, end_date: str) -> dict[str, Any]:
    sql = """
        SELECT
            COALESCE(SUM(sessions), 0)                 AS total_sessions,
            COALESCE(SUM(ga4_purchases_lastclick), 0)  AS total_purchases
        FROM ga4_metrics
        WHERE date BETWEEN ? AND ?
    """
    with _conn(db_path) as con:
        row = con.execute(sql, (start_date, end_date)).fetchone()
    if not row:
        return {"total_sessions": 0, "total_purchases": 0}
    return dict(row)


def get_daily_trend(db_path: Path, start_date: str, end_date: str) -> list[dict[str, Any]]:
    sql = """
        SELECT
            m.date,
            COALESCE(SUM(m.spend), 0)                        AS spend,
            COALESCE(SUM(m.meta_form_submit_deposit), 0)     AS deposits,
            COALESCE(g.sessions, 0)                          AS sessions
        FROM ad_metrics m
        LEFT JOIN (
            SELECT date, SUM(sessions) AS sessions
            FROM ga4_metrics
            GROUP BY date
        ) g ON g.date = m.date
        WHERE m.ad_set_id = '' AND m.ad_id = ''
          AND m.date BETWEEN ? AND ?
        GROUP BY m.date
        ORDER BY m.date
    """
    with _conn(db_path) as con:
        rows = con.execute(sql, (start_date, end_date)).fetchall()
    return [dict(r) for r in rows]


def get_campaign_table(db_path: Path, start_date: str, end_date: str) -> list[dict[str, Any]]:
    sql = """
        SELECT
            c.name                                                        AS campaign_name,
            COALESCE(SUM(m.spend), 0)                                    AS spend,
            CASE WHEN SUM(m.spend) > 0
                 THEN SUM(m.spend * m.roas) / SUM(m.spend)
                 ELSE 0 END                                              AS weighted_roas,
            COALESCE(SUM(m.impressions), 0)                              AS impressions,
            COALESCE(SUM(m.meta_form_submit_deposit), 0)                 AS deposits,
            CASE WHEN SUM(m.meta_form_submit_deposit) > 0
                 THEN SUM(m.spend) / SUM(m.meta_form_submit_deposit)
                 ELSE NULL END                                           AS cpd,
            COALESCE(SUM(g.sessions), 0)                                 AS ga4_sessions
        FROM ad_metrics m
        JOIN campaigns c ON m.campaign_id = c.id
        LEFT JOIN ga4_metrics g ON g.campaign_utm = c.name AND g.date = m.date
        WHERE m.ad_set_id = '' AND m.ad_id = ''
          AND m.date BETWEEN ? AND ?
        GROUP BY c.name
        ORDER BY deposits DESC, spend DESC
    """
    with _conn(db_path) as con:
        rows = con.execute(sql, (start_date, end_date)).fetchall()
    return [dict(r) for r in rows]


def get_attribution_comparison(db_path: Path, start_date: str, end_date: str) -> list[dict[str, Any]]:
    """Side-by-side Meta vs GA4 per campaign. Never blends the two numbers."""
    sql = """
        SELECT
            c.name                                                   AS campaign_name,
            COALESCE(SUM(m.meta_purchases_7dclick), 0)               AS meta_purchases,
            COALESCE(SUM(m.meta_form_submit_deposit), 0)             AS meta_deposits,
            COALESCE(SUM(g.ga4_purchases_lastclick), 0)              AS ga4_purchases
        FROM ad_metrics m
        JOIN campaigns c ON m.campaign_id = c.id
        LEFT JOIN ga4_metrics g ON g.campaign_utm = c.name AND g.date = m.date
        WHERE m.ad_set_id = '' AND m.ad_id = ''
          AND m.date BETWEEN ? AND ?
        GROUP BY c.name
        HAVING meta_purchases > 0 OR ga4_purchases > 0 OR meta_deposits > 0
        ORDER BY meta_deposits DESC
    """
    with _conn(db_path) as con:
        rows = con.execute(sql, (start_date, end_date)).fetchall()
    return [dict(r) for r in rows]


def get_data_freshness(db_path: Path) -> dict[str, str | None]:
    with _conn(db_path) as con:
        meta = con.execute(
            "SELECT MAX(fetched_at) AS fetched, MAX(date) AS last_date FROM ad_metrics"
        ).fetchone()
        ga4 = con.execute(
            "SELECT MAX(fetched_at) AS fetched, MAX(date) AS last_date FROM ga4_metrics"
        ).fetchone()
    return {
        "meta_fetched": meta["fetched"] if meta else None,
        "meta_last_date": meta["last_date"] if meta else None,
        "ga4_fetched": ga4["fetched"] if ga4 else None,
        "ga4_last_date": ga4["last_date"] if ga4 else None,
    }


def get_campaign_names(db_path: Path) -> list[str]:
    with _conn(db_path) as con:
        rows = con.execute("SELECT name FROM campaigns ORDER BY name").fetchall()
    return [r["name"] for r in rows]


def get_campaign_daily(
    db_path: Path,
    campaign_name: str,
    start_date: str,
    end_date: str,
) -> list[dict[str, Any]]:
    """Daily Meta + GA4 rows for one campaign within [start_date, end_date].

    - Campaign-level only (ad_set_id = '' AND ad_id = '').
    - Exact UTM join (g.campaign_utm = c.name AND g.date = m.date).
    - Never blends Meta vs GA4 conversion counts (CLAUDE.md data model rule):
      meta_purchases and ga4_purchases are returned as separate keys.
    - Campaign name is bound via positional ? param — never interpolated into SQL.
    """
    sql = '''
        SELECT
            m.date                                           AS date,
            COALESCE(SUM(m.spend), 0)                        AS spend,
            COALESCE(SUM(m.meta_form_submit_deposit), 0)     AS deposits,
            COALESCE(SUM(g.sessions), 0)                     AS sessions,
            CASE WHEN SUM(m.spend) > 0
                 THEN SUM(m.spend * m.roas) / SUM(m.spend)
                 ELSE 0 END                                  AS roas,
            COALESCE(SUM(m.meta_purchases_7dclick), 0)       AS meta_purchases,
            COALESCE(SUM(g.ga4_purchases_lastclick), 0)      AS ga4_purchases
        FROM ad_metrics m
        JOIN campaigns c ON m.campaign_id = c.id
        LEFT JOIN ga4_metrics g ON g.campaign_utm = c.name AND g.date = m.date
        WHERE m.ad_set_id = '' AND m.ad_id = ''
          AND c.name = ?
          AND m.date BETWEEN ? AND ?
        GROUP BY m.date
        ORDER BY m.date
    '''
    with _conn(db_path) as con:
        rows = con.execute(sql, (campaign_name, start_date, end_date)).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Phase 8: MMM results read helpers (DASH-12)
# ---------------------------------------------------------------------------

def get_latest_mmm_result(db_path: Path) -> dict[str, Any] | None:
    """Most-recent row of mmm_results, or None when the table is empty/missing.

    The table is created by MIGRATION_006_PHASE8. On a fresh DB that hasn't been
    opened by the bot yet (no migrations applied), the table may not exist —
    catch OperationalError and return None so the dashboard renders the
    "MMM has not run yet" empty state (D-13) instead of crashing.
    """
    sql = "SELECT * FROM mmm_results ORDER BY run_date DESC LIMIT 1"
    try:
        with _conn(db_path) as con:
            row = con.execute(sql).fetchone()
    except sqlite3.OperationalError:
        return None
    return dict(row) if row is not None else None


def get_weekly_contributions(
    db_path: Path, weeks: int = 12
) -> list[dict[str, Any]]:
    """Per-ISO-week stacked contribution data for the dashboard chart.

    Two-step:
      1. Fetch the latest MMM result for the media_pct ratio. If absent, return [].
      2. Aggregate ad_metrics by ISO week (strftime('%Y-%W', date)) at the
         campaign level (ad_set_id='' AND ad_id=''). Split total deposits per
         week into baseline vs media using the stored media_pct ratio.

    Returns list of dicts {week, avg_daily_spend, baseline_deposits, media_deposits}
    ordered ASC by week (oldest first) for stacked-bar consumption.
    """
    latest = get_latest_mmm_result(db_path)
    if latest is None:
        return []

    media_ratio = float(latest["media_pct"]) / 100.0

    sql = """
        SELECT strftime('%Y-%W', date)                        AS week,
               AVG(spend)                                     AS avg_daily_spend,
               SUM(meta_form_submit_deposit)                  AS total_deposits
        FROM ad_metrics
        WHERE ad_set_id = '' AND ad_id = ''
        GROUP BY week
        ORDER BY week DESC
        LIMIT ?
    """
    try:
        with _conn(db_path) as con:
            rows = con.execute(sql, (weeks,)).fetchall()
    except sqlite3.OperationalError:
        return []

    out: list[dict[str, Any]] = []
    for r in rows:
        total = float(r["total_deposits"] or 0)
        media_deposits = total * media_ratio
        baseline_deposits = total - media_deposits
        out.append(
            {
                "week": r["week"],
                "avg_daily_spend": float(r["avg_daily_spend"] or 0),
                "baseline_deposits": baseline_deposits,
                "media_deposits": media_deposits,
            }
        )
    # Returned DESC from SQL; reverse to ASC for stacked-bar oldest-first display.
    out.reverse()
    return out
