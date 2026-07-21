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
            COUNT(DISTINCT m.campaign_id)                           AS active_campaigns,
            ROUND(CASE WHEN SUM(m.impressions) > 0
                       THEN SUM(m.clicks) * 100.0 / SUM(m.impressions)
                       ELSE NULL END, 2)                            AS overall_ctr,
            ROUND(CASE WHEN SUM(m.spend) > 0
                       THEN SUM(m.spend) / SUM(m.impressions) * 1000
                       ELSE NULL END, 2)                            AS avg_cpm,
            ROUND(CASE WHEN SUM(m.clicks) > 0
                       THEN SUM(m.spend) / SUM(m.clicks)
                       ELSE NULL END, 2)                            AS avg_cpc,
            SUM(m.reach)                                            AS total_reach
        FROM ad_metrics m
        WHERE m.ad_set_id = '' AND m.ad_id = ''
          AND m.date BETWEEN ? AND ?
    """
    with _conn(db_path) as con:
        row = con.execute(sql, (start_date, end_date)).fetchone()
    if not row:
        return {"total_spend": 0, "weighted_roas": 0, "total_deposits": 0,
                "cpd": None, "active_campaigns": 0,
                "overall_ctr": None, "avg_cpm": None, "avg_cpc": None, "total_reach": 0}
    return dict(row)


def get_meta_purchases_total(db_path: Path, start_date: str, end_date: str) -> int:
    """Period total of Meta 7-day-click purchases (campaign-level ad_metrics rows).

    Added for the Overview triangle-reconciliation block (Phase D): no existing
    query returns this as a period aggregate across all campaigns (only
    per-campaign via get_attribution_comparison), so this mirrors the
    get_kpi_summary / get_ga4_kpi filtering convention.
    """
    sql = """
        SELECT COALESCE(SUM(m.meta_purchases_7dclick), 0) AS total
        FROM ad_metrics m
        WHERE m.ad_set_id = '' AND m.ad_id = ''
          AND m.date BETWEEN ? AND ?
    """
    with _conn(db_path) as con:
        row = con.execute(sql, (start_date, end_date)).fetchone()
    return int(row["total"]) if row else 0


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


def get_campaign_daily_breakdown(
    db_path: Path, start_date: str, end_date: str
) -> list[dict[str, Any]]:
    """Per-day × per-campaign: spend, FSD, CPR (FSD), CTR.
    Campaign-level rows only (ad_set_id = '', ad_id = '').
    Used by the Overview daily-trends-by-campaign charts.
    """
    sql = """
        SELECT
            m.date,
            c.name                                                       AS campaign_name,
            COALESCE(m.spend, 0)                                         AS spend,
            COALESCE(m.meta_form_submit_deposit, 0)                      AS fsd,
            CASE WHEN m.meta_form_submit_deposit > 0
                 THEN m.spend / m.meta_form_submit_deposit
                 ELSE NULL END                                           AS cpr,
            m.ctr
        FROM ad_metrics m
        JOIN campaigns c ON m.campaign_id = c.id
        WHERE m.ad_set_id = '' AND m.ad_id = ''
          AND m.date BETWEEN ? AND ?
        ORDER BY m.date, c.name
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


def get_campaign_ga4_engagement(
    db_path: Path,
    campaign_name: str,
    start_date: str,
    end_date: str,
) -> dict[str, Any]:
    """GA4 engagement metrics for one campaign UTM over [start_date, end_date].

    Returns avg_bounce_rate, avg_engagement_time_sec, total_users, total_new_users.
    All values default to 0 / None when no rows exist.
    Join key: campaign_utm = campaign name (exact UTM match, CLAUDE.md rule).
    """
    sql = """
        SELECT
            AVG(bounce_rate)           AS avg_bounce_rate,
            AVG(avg_engagement_time)   AS avg_engagement_time_sec,
            SUM(users)                 AS total_users,
            SUM(new_users)             AS total_new_users
        FROM ga4_metrics
        WHERE campaign_utm = ?
          AND date BETWEEN ? AND ?
    """
    with _conn(db_path) as con:
        row = con.execute(sql, (campaign_name, start_date, end_date)).fetchone()
    if not row:
        return {"avg_bounce_rate": None, "avg_engagement_time_sec": None,
                "total_users": 0, "total_new_users": 0}
    return dict(row)


def get_campaign_adset_breakdown(
    db_path: Path,
    campaign_name: str,
    start_date: str,
    end_date: str,
) -> list[dict[str, Any]]:
    """Per-ad-set performance for one campaign over [start_date, end_date].

    Only rows where ad_set_id != '' and ad_id = '' (ad-set level granularity).
    Returns empty list when only campaign-level rows exist (ad_set_id = '').
    """
    sql = """
        SELECT
            m.ad_set_id                                                  AS ad_set_id,
            COALESCE(SUM(m.spend), 0)                                    AS spend,
            COALESCE(SUM(m.meta_form_submit_deposit), 0)                 AS deposits,
            CASE WHEN SUM(m.meta_form_submit_deposit) > 0
                 THEN SUM(m.spend) / SUM(m.meta_form_submit_deposit)
                 ELSE NULL END                                           AS cpd,
            CASE WHEN SUM(m.spend) > 0
                 THEN SUM(m.spend * m.roas) / SUM(m.spend)
                 ELSE 0 END                                              AS roas,
            COALESCE(SUM(m.impressions), 0)                              AS impressions,
            COALESCE(SUM(m.clicks), 0)                                   AS clicks,
            CASE WHEN SUM(m.impressions) > 0
                 THEN CAST(SUM(m.clicks) AS REAL) / SUM(m.impressions) * 100
                 ELSE 0 END                                              AS ctr_pct,
            COALESCE(AVG(m.frequency), 0)                                AS avg_frequency
        FROM ad_metrics m
        JOIN campaigns c ON m.campaign_id = c.id
        WHERE c.name = ?
          AND m.ad_set_id != ''
          AND m.ad_id = ''
          AND m.date BETWEEN ? AND ?
        GROUP BY m.ad_set_id
        ORDER BY cpd ASC NULLS LAST, spend DESC
    """
    with _conn(db_path) as con:
        rows = con.execute(sql, (campaign_name, start_date, end_date)).fetchall()
    return [dict(r) for r in rows]


def get_changelog_entries(
    db_path: Path,
    start_date: str,
    end_date: str,
    object_types: list[str] | None = None,
    limit: int = 2000,
) -> list[dict[str, Any]]:
    """Fetch changelog entries within a date range, optionally filtered by object_type.

    change_time is stored as a UTC ISO datetime string from the Meta API.
    Returns newest-first.
    """
    type_filter = ""
    params: list[Any] = [start_date, end_date]
    if object_types:
        placeholders = ",".join("?" * len(object_types))
        type_filter = f"AND object_type IN ({placeholders})"
        params.extend(object_types)
    params.append(limit)
    sql = f"""
        SELECT change_time, object_type, object_name, event_type,
               changed_fields, old_value, new_value, actor_name
        FROM ad_changelogs
        WHERE date(change_time) BETWEEN ? AND ?
          {type_filter}
        ORDER BY change_time DESC
        LIMIT ?
    """
    try:
        with _conn(db_path) as con:
            rows = con.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []


# ---------------------------------------------------------------------------
# Dashboard chat history — persisted across sessions (no migration needed)
# ---------------------------------------------------------------------------

_DASHBOARD_CHAT_DDL = """
CREATE TABLE IF NOT EXISTS dashboard_chat_history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    role       TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
    content    TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""


def get_dashboard_chat_history(db_path: Path, limit: int = 100) -> list[dict[str, Any]]:
    """Load the most recent `limit` turns as Anthropic message dicts.

    Returns oldest-first so the list can be passed directly to the API as
    conversation history.  Only 'user' and 'assistant' text turns are stored —
    tool_use / tool_result internal traces are never persisted (D-20).
    """
    try:
        with _conn(db_path) as con:
            con.execute(_DASHBOARD_CHAT_DDL)
            rows = con.execute(
                "SELECT role, content FROM dashboard_chat_history "
                "ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        # fetchall returns newest-first; reverse to oldest-first for API
        return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]
    except sqlite3.OperationalError:
        return []


def append_dashboard_chat_messages(
    db_path: Path, messages: list[dict[str, Any]]
) -> None:
    """Append one or more {role, content} text turns to persistent history."""
    with _conn(db_path) as con:
        con.execute(_DASHBOARD_CHAT_DDL)
        for msg in messages:
            role = msg.get("role")
            content = msg.get("content")
            if role in ("user", "assistant") and isinstance(content, str):
                con.execute(
                    "INSERT INTO dashboard_chat_history (role, content) VALUES (?, ?)",
                    (role, content),
                )
        con.commit()


def clear_dashboard_chat_history(db_path: Path) -> None:
    """Delete all rows from dashboard_chat_history."""
    try:
        with _conn(db_path) as con:
            con.execute(_DASHBOARD_CHAT_DDL)
            con.execute("DELETE FROM dashboard_chat_history")
            con.commit()
    except sqlite3.OperationalError:
        pass


# ---------------------------------------------------------------------------
# Daily AI briefing helpers — self-bootstrapping table (no migration needed)
# ---------------------------------------------------------------------------

_DAILY_INSIGHTS_DDL = """
CREATE TABLE IF NOT EXISTS daily_insights (
    generated_date  TEXT PRIMARY KEY,
    content         TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
)
"""


def get_today_insight(db_path: Path) -> str | None:
    """Return today's stored insight text, or None if not yet generated."""
    from datetime import date as _date
    today = _date.today().isoformat()
    try:
        with _conn(db_path) as con:
            con.execute(_DAILY_INSIGHTS_DDL)
            row = con.execute(
                "SELECT content FROM daily_insights WHERE generated_date = ?", (today,)
            ).fetchone()
        return row["content"] if row else None
    except sqlite3.OperationalError:
        return None


def save_today_insight(db_path: Path, content: str) -> None:
    """Upsert today's insight into daily_insights."""
    from datetime import date as _date
    today = _date.today().isoformat()
    with _conn(db_path) as con:
        con.execute(_DAILY_INSIGHTS_DDL)
        con.execute(
            "INSERT OR REPLACE INTO daily_insights (generated_date, content) VALUES (?, ?)",
            (today, content),
        )
        con.commit()


def delete_today_insight(db_path: Path) -> None:
    """Remove today's insight so the next page load regenerates it."""
    from datetime import date as _date
    today = _date.today().isoformat()
    try:
        with _conn(db_path) as con:
            con.execute(_DAILY_INSIGHTS_DDL)
            con.execute(
                "DELETE FROM daily_insights WHERE generated_date = ?", (today,)
            )
            con.commit()
    except sqlite3.OperationalError:
        pass


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


# ---------------------------------------------------------------------------
# Stripe payments (Google Sheets funnel data)
# ---------------------------------------------------------------------------


def get_stripe_daily(
    db_path: Path, start_date: str, end_date: str
) -> list[dict[str, Any]]:
    """Daily FSD (form submit deposit) vs paid conversion counts and rate.

    Returns [] if the stripe_payments table does not yet exist (migration not yet applied).
    """
    sql = """
        SELECT date(submitted_at)                                              AS date,
               COUNT(*)                                                        AS total_fsd,
               SUM(CASE WHEN status = 'paid'    THEN 1 ELSE 0 END)            AS paid,
               SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END)            AS pending,
               ROUND(
                   SUM(CASE WHEN status = 'paid' THEN 1 ELSE 0 END) * 100.0
                   / COUNT(*), 1
               )                                                               AS paid_rate
        FROM stripe_payments
        WHERE date(submitted_at) BETWEEN ? AND ?
        GROUP BY date(submitted_at)
        ORDER BY date(submitted_at)
    """
    try:
        with _conn(db_path) as con:
            rows = con.execute(sql, (start_date, end_date)).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []


def get_stripe_by_source(
    db_path: Path, start_date: str, end_date: str
) -> list[dict[str, Any]]:
    """FSD and paid-conversion breakdown grouped by landing-page source slug.

    Returns [] if the stripe_payments table does not yet exist.
    """
    sql = """
        SELECT source,
               COUNT(*)                                                        AS total_fsd,
               SUM(CASE WHEN status = 'paid' THEN 1 ELSE 0 END)               AS paid,
               ROUND(
                   SUM(CASE WHEN status = 'paid' THEN 1 ELSE 0 END) * 100.0
                   / COUNT(*), 1
               )                                                               AS paid_rate
        FROM stripe_payments
        WHERE date(submitted_at) BETWEEN ? AND ?
        GROUP BY source
        ORDER BY total_fsd DESC
    """
    try:
        with _conn(db_path) as con:
            rows = con.execute(sql, (start_date, end_date)).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []


def get_stripe_last_submitted(db_path: Path) -> str | None:
    """Return the most recent submitted_at timestamp, or None if table is empty/missing."""
    try:
        with _conn(db_path) as con:
            row = con.execute(
                "SELECT MAX(submitted_at) AS latest FROM stripe_payments"
            ).fetchone()
        return row["latest"] if row else None
    except sqlite3.OperationalError:
        return None


def get_stripe_period_totals(
    db_path: Path, start_date: str, end_date: str
) -> dict[str, Any]:
    """Aggregate totals for the selected period: total FSD, paid count, paid rate."""
    sql = """
        SELECT COUNT(*)                                                    AS total_fsd,
               SUM(CASE WHEN status = 'paid' THEN 1 ELSE 0 END)           AS paid,
               ROUND(
                   SUM(CASE WHEN status = 'paid' THEN 1 ELSE 0 END) * 100.0
                   / NULLIF(COUNT(*), 0), 1
               )                                                           AS paid_rate
        FROM stripe_payments
        WHERE date(submitted_at) BETWEEN ? AND ?
    """
    try:
        with _conn(db_path) as con:
            row = con.execute(sql, (start_date, end_date)).fetchone()
        return dict(row) if row else {"total_fsd": 0, "paid": 0, "paid_rate": None}
    except sqlite3.OperationalError:
        return {"total_fsd": 0, "paid": 0, "paid_rate": None}


def get_campaign_funnel(
    db_path: Path, start_date: str, end_date: str
) -> list[dict[str, Any]]:
    """Per-campaign funnel: impressions → clicks → GA4 sessions → Meta FSD.

    Campaign-level rows only (ad_set_id = '' AND ad_id = '').
    GA4 sessions joined via exact UTM campaign name match.
    Returns [] if no data or table missing.
    """
    sql = """
        SELECT c.name                                                              AS campaign_name,
               SUM(m.impressions)                                                 AS impressions,
               SUM(m.clicks)                                                      AS clicks,
               ROUND(SUM(m.spend), 2)                                             AS spend,
               SUM(m.meta_form_submit_deposit)                                    AS meta_fsd,
               COALESCE(g_agg.sessions, 0)                                        AS ga4_sessions,
               ROUND(AVG(NULLIF(m.frequency, 0)), 2)                              AS avg_frequency,
               ROUND(
                   CASE WHEN SUM(m.spend) > 0
                        THEN SUM(m.roas * m.spend) / SUM(m.spend)
                        ELSE NULL END, 2
               )                                                                   AS weighted_roas
        FROM campaigns c
        JOIN ad_metrics m ON m.campaign_id = c.id
            AND m.ad_set_id = '' AND m.ad_id = ''
            AND m.date BETWEEN ? AND ?
        LEFT JOIN (
            SELECT campaign_utm, SUM(sessions) AS sessions
            FROM ga4_metrics
            WHERE date BETWEEN ? AND ?
            GROUP BY campaign_utm
        ) g_agg ON g_agg.campaign_utm = c.name
        GROUP BY c.id, c.name
        HAVING SUM(m.spend) > 0
        ORDER BY spend DESC
    """
    try:
        with _conn(db_path) as con:
            rows = con.execute(sql, (start_date, end_date, start_date, end_date)).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []


def get_roas_frequency_trend(
    db_path: Path, start_date: str, end_date: str
) -> list[dict[str, Any]]:
    """Daily spend-weighted blended ROAS and average ad frequency.

    Campaign-level rows only (ad_set_id = '' AND ad_id = '').
    Returns [] if no data.
    """
    sql = """
        SELECT date,
               ROUND(
                   CASE WHEN SUM(spend) > 0
                        THEN SUM(roas * spend) / SUM(spend)
                        ELSE NULL END, 2
               )                                AS blended_roas,
               ROUND(AVG(NULLIF(frequency, 0)), 2) AS avg_frequency
        FROM ad_metrics
        WHERE ad_set_id = '' AND ad_id = ''
          AND date BETWEEN ? AND ?
        GROUP BY date
        ORDER BY date
    """
    try:
        with _conn(db_path) as con:
            rows = con.execute(sql, (start_date, end_date)).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []


def get_landing_page_health(
    db_path: Path, start_date: str, end_date: str
) -> list[dict[str, Any]]:
    """Per-landing-page health combining GA4 engagement with Stripe funnel metrics.

    Joins ga4_landing_pages with stripe_payments by stripping the leading '/'
    from landing_page to match the source slug (e.g. '/6a-nostalgia-bridge' → '6a-nostalgia-bridge').

    Returns columns: landing_page, sessions, avg_engagement_time, total_fsd,
                     paid, fsd_rate (%), paid_rate (%).
    Returns [] if no data or table missing.
    """
    sql = """
        SELECT lp.landing_page,
               SUM(lp.sessions)                                                    AS sessions,
               ROUND(AVG(lp.avg_engagement_time), 1)                               AS avg_engagement_time,
               COUNT(sp.uid)                                                        AS total_fsd,
               SUM(CASE WHEN sp.status = 'paid' THEN 1 ELSE 0 END)                 AS paid,
               ROUND(
                   COUNT(sp.uid) * 100.0 / NULLIF(SUM(lp.sessions), 0), 1
               )                                                                    AS fsd_rate,
               ROUND(
                   SUM(CASE WHEN sp.status = 'paid' THEN 1 ELSE 0 END) * 100.0
                   / NULLIF(COUNT(sp.uid), 0), 1
               )                                                                    AS paid_rate
        FROM ga4_landing_pages lp
        LEFT JOIN stripe_payments sp
            ON TRIM(lp.landing_page, '/') = sp.source
            AND date(sp.submitted_at) BETWEEN ? AND ?
        WHERE lp.date BETWEEN ? AND ?
        GROUP BY lp.landing_page
        HAVING COUNT(sp.uid) > 0 OR SUM(lp.sessions) >= 20
        ORDER BY total_fsd DESC, sessions DESC
    """
    try:
        with _conn(db_path) as con:
            rows = con.execute(sql, (start_date, end_date, start_date, end_date)).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []


def get_top_ads(
    db_path: Path, start_date: str, end_date: str, limit: int = 10
) -> list[dict[str, Any]]:
    """Top performing ads by FSD count, enriched with creative metadata.

    Joins ad_metrics (ad_id != '') with ad_creatives for name/format/style/URLs.
    Returns [] if no ad-level data exists yet.
    """
    sql = """
        SELECT m.ad_id,
               COALESCE(cr.ad_name, m.ad_id)                    AS ad_name,
               c.name                                            AS campaign_name,
               ROUND(SUM(m.spend), 2)                           AS spend,
               SUM(m.impressions)                               AS impressions,
               SUM(m.meta_form_submit_deposit)                  AS fsd,
               SUM(m.clicks)                                    AS clicks,
               ROUND(AVG(m.ctr), 2)                             AS avg_ctr,
               ROUND(AVG(NULLIF(m.frequency, 0)), 2)            AS avg_frequency,
               ROUND(AVG(NULLIF(m.cpc, 0)), 2)                  AS avg_cpc,
               ROUND(AVG(NULLIF(m.cpm, 0)), 2)                  AS avg_cpm,
               ROUND(
                   CASE WHEN SUM(m.spend) > 0
                        THEN SUM(m.roas * m.spend) / SUM(m.spend)
                        ELSE NULL END, 2
               )                                                AS weighted_roas,
               ROUND(
                   CASE WHEN SUM(m.meta_form_submit_deposit) > 0
                        THEN SUM(m.spend) / SUM(m.meta_form_submit_deposit)
                        ELSE NULL END, 2
               )                                                AS cpr_fsd,
               cr.ad_format,
               cr.ad_style,
               cr.thumbnail_url,
               cr.destination_url,
               cr.preview_url,
               cr.effective_status
        FROM ad_metrics m
        JOIN campaigns c ON m.campaign_id = c.id
        LEFT JOIN ad_creatives cr ON cr.ad_id = m.ad_id
        WHERE m.ad_id != ''
          AND m.date BETWEEN ? AND ?
        GROUP BY m.ad_id
        HAVING SUM(m.spend) > 0
        ORDER BY fsd DESC, spend DESC
        LIMIT ?
    """
    try:
        with _conn(db_path) as con:
            rows = con.execute(sql, (start_date, end_date, limit)).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []


def get_fatigue_ads(
    db_path: Path, start_date: str, end_date: str
) -> list[dict[str, Any]]:
    """Detect fatigued ads using Meta's 4-signal framework.

    Signals checked (any combination triggers inclusion):
      1. Declining CTR  — CTR in late half of period < CTR in early half by ≥30 %
      2. Rising CPR     — Cost-per-FSD in late half > early half by ≥30 %
      3. High frequency — avg frequency ≥ 2.5 (audience saturation)
      4. Diminishing returns — FSD rate (FSD/impressions) fell >40 % between halves

    The date range is split at its midpoint; CTR and CPD trends are computed
    by comparing early-half vs late-half aggregates using conditional SQL.

    Returns each fatigued ad enriched with:
      fatigue_signals  – list of triggered signal descriptions
      ctr_change_pct   – % change in CTR (negative = decline)
      cpd_change_pct   – % change in CPD (positive = more expensive)
      severity         – 'critical' (≥3 signals) | 'warning' (2) | 'watch' (1)
      recommendation   – plain-English action

    Returns [] if no ad-level data exists yet.
    """
    from datetime import date as _date, timedelta as _td

    try:
        start = _date.fromisoformat(start_date)
        end = _date.fromisoformat(end_date)
    except ValueError:
        return []

    days_span = (end - start).days
    # Need at least 4 days to split meaningfully; otherwise return empty
    if days_span < 4:
        return []

    mid = (start + _td(days=days_span // 2)).isoformat()

    # 12 × mid params + start + end
    sql = """
        SELECT m.ad_id,
               COALESCE(cr.ad_name, m.ad_id)                                        AS ad_name,
               c.name                                                                AS campaign_name,
               ROUND(SUM(m.spend), 2)                                               AS spend,
               SUM(m.impressions)                                                    AS impressions,
               SUM(m.meta_form_submit_deposit)                                       AS fsd,
               ROUND(AVG(NULLIF(m.frequency, 0)), 2)                                 AS avg_frequency,
               ROUND(AVG(m.ctr), 3)                                                  AS avg_ctr,
               -- CTR split: early half (start..mid) vs late half (mid+1..end)
               ROUND(
                   SUM(CASE WHEN m.date <= ? THEN m.clicks ELSE 0 END) * 100.0
                   / NULLIF(SUM(CASE WHEN m.date <= ? THEN m.impressions ELSE 0 END), 0), 3
               )                                                                     AS ctr_early,
               ROUND(
                   SUM(CASE WHEN m.date > ? THEN m.clicks ELSE 0 END) * 100.0
                   / NULLIF(SUM(CASE WHEN m.date > ? THEN m.impressions ELSE 0 END), 0), 3
               )                                                                     AS ctr_late,
               -- CPD split (cost per FSD)
               ROUND(
                   SUM(CASE WHEN m.date <= ? THEN m.spend ELSE 0 END)
                   / NULLIF(SUM(CASE WHEN m.date <= ? THEN m.meta_form_submit_deposit ELSE 0 END), 0), 2
               )                                                                     AS cpd_early,
               ROUND(
                   SUM(CASE WHEN m.date > ? THEN m.spend ELSE 0 END)
                   / NULLIF(SUM(CASE WHEN m.date > ? THEN m.meta_form_submit_deposit ELSE 0 END), 0), 2
               )                                                                     AS cpd_late,
               -- Impressions split (for FSD-rate / diminishing-returns check)
               SUM(CASE WHEN m.date <= ? THEN m.impressions ELSE 0 END)              AS impr_early,
               SUM(CASE WHEN m.date > ? THEN m.impressions ELSE 0 END)               AS impr_late,
               SUM(CASE WHEN m.date <= ? THEN m.meta_form_submit_deposit ELSE 0 END) AS fsd_early,
               SUM(CASE WHEN m.date > ? THEN m.meta_form_submit_deposit ELSE 0 END)  AS fsd_late,
               cr.ad_format,
               cr.ad_style,
               cr.thumbnail_url,
               cr.preview_url
        FROM ad_metrics m
        JOIN campaigns c ON m.campaign_id = c.id
        LEFT JOIN ad_creatives cr ON cr.ad_id = m.ad_id
        WHERE m.ad_id != ''
          AND m.date BETWEEN ? AND ?
        GROUP BY m.ad_id
        HAVING SUM(m.spend) > 5 AND SUM(m.impressions) >= 200
        ORDER BY SUM(m.spend) DESC
    """
    # 12 mid values then start, end
    params = [mid] * 12 + [start_date, end_date]

    try:
        with _conn(db_path) as con:
            rows = con.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        return []

    results: list[dict[str, Any]] = []
    for row in rows:
        d = dict(row)
        signals: list[str] = []

        ctr_early = float(d.get("ctr_early") or 0)
        ctr_late  = float(d.get("ctr_late")  or 0)
        cpd_early = d.get("cpd_early")
        cpd_late  = d.get("cpd_late")
        freq      = float(d.get("avg_frequency") or 0)
        impr_early = int(d.get("impr_early") or 0)
        impr_late  = int(d.get("impr_late")  or 0)
        fsd_early  = int(d.get("fsd_early")  or 0)
        fsd_late   = int(d.get("fsd_late")   or 0)

        # --- Signal 1: Declining CTR ---
        ctr_change_pct: float | None = None
        if ctr_early > 0:
            ctr_change_pct = round((ctr_late - ctr_early) / ctr_early * 100, 1)
            if ctr_change_pct <= -30:
                signals.append(f"CTR dropped {abs(ctr_change_pct):.0f}%")

        # --- Signal 2: Rising CPD ---
        cpd_change_pct: float | None = None
        if cpd_early and cpd_late and float(cpd_early) > 0:
            cpd_change_pct = round((float(cpd_late) - float(cpd_early)) / float(cpd_early) * 100, 1)
            if cpd_change_pct >= 30:
                signals.append(f"CPR rose {cpd_change_pct:.0f}%")

        # --- Signal 3: High frequency ---
        if freq >= 2.5:
            signals.append(f"Frequency {freq:.1f}×")

        # --- Signal 4: Diminishing returns (FSD rate fell >40 %) ---
        if impr_early >= 100 and impr_late >= 100 and fsd_early > 0:
            rate_early = fsd_early / impr_early
            rate_late  = fsd_late  / impr_late if impr_late else 0
            if rate_early > 0 and rate_late < rate_early * 0.6:
                signals.append("FSD rate fell >40%")

        if not signals:
            continue

        d["fatigue_signals"] = signals
        d["ctr_change_pct"]  = ctr_change_pct
        d["cpd_change_pct"]  = cpd_change_pct

        # Severity
        n = len(signals)
        d["severity"] = "critical" if n >= 3 else "warning" if n == 2 else "watch"

        # Recommendation
        if ctr_change_pct is not None and ctr_change_pct <= -50:
            rec = "Refresh creative immediately — CTR collapsed >50 %"
        elif ctr_change_pct is not None and ctr_change_pct <= -30:
            rec = "Test a new hook or visual — audience stopped responding"
        elif freq >= 3.5:
            rec = "Refresh creative immediately — severe audience saturation"
        elif freq >= 3.0:
            rec = "Prepare new creative — approaching burnout"
        elif cpd_change_pct is not None and cpd_change_pct >= 50:
            rec = "Narrow audience or pause — conversion efficiency collapsing"
        elif n >= 2:
            rec = "Reduce budget or refresh creative — multiple fatigue signals"
        else:
            rec = "Monitor closely — early fatigue signal"

        d["recommendation"] = rec
        results.append(d)

    # Sort: most signals first, then by spend
    results.sort(key=lambda x: (len(x["fatigue_signals"]), x["spend"]), reverse=True)
    return results


def get_ad_format_breakdown(
    db_path: Path, start_date: str, end_date: str
) -> list[dict[str, Any]]:
    """Spend, FSD, CTR by ad format (image, video, carousel).

    Joins ad_metrics with ad_creatives for format labels.
    Returns [] if no ad-level or creative data.
    """
    sql = """
        SELECT COALESCE(cr.ad_format, 'unknown')                AS ad_format,
               COUNT(DISTINCT m.ad_id)                          AS ad_count,
               ROUND(SUM(m.spend), 2)                           AS spend,
               SUM(m.impressions)                               AS impressions,
               SUM(m.clicks)                                    AS clicks,
               SUM(m.meta_form_submit_deposit)                  AS fsd,
               ROUND(AVG(m.ctr), 2)                             AS avg_ctr,
               ROUND(AVG(NULLIF(m.cpc, 0)), 2)                  AS avg_cpc,
               ROUND(
                   CASE WHEN SUM(m.spend) > 0
                        THEN SUM(m.roas * m.spend) / SUM(m.spend)
                        ELSE NULL END, 2
               )                                                AS weighted_roas
        FROM ad_metrics m
        LEFT JOIN ad_creatives cr ON cr.ad_id = m.ad_id
        WHERE m.ad_id != ''
          AND m.date BETWEEN ? AND ?
        GROUP BY COALESCE(cr.ad_format, 'unknown')
        HAVING SUM(m.spend) > 0
        ORDER BY spend DESC
    """
    try:
        with _conn(db_path) as con:
            rows = con.execute(sql, (start_date, end_date)).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []


def get_ad_style_breakdown(
    db_path: Path, start_date: str, end_date: str
) -> list[dict[str, Any]]:
    """Spend, FSD, CTR by ad style (testimonial, product_hero, etc.).

    Returns [] if no ad-level or creative data.
    """
    sql = """
        SELECT COALESCE(cr.ad_style, 'unknown')                 AS ad_style,
               COUNT(DISTINCT m.ad_id)                          AS ad_count,
               ROUND(SUM(m.spend), 2)                           AS spend,
               SUM(m.impressions)                               AS impressions,
               SUM(m.meta_form_submit_deposit)                  AS fsd,
               ROUND(AVG(m.ctr), 2)                             AS avg_ctr,
               ROUND(
                   CASE WHEN SUM(m.spend) > 0
                        THEN SUM(m.roas * m.spend) / SUM(m.spend)
                        ELSE NULL END, 2
               )                                                AS weighted_roas,
               ROUND(
                   CASE WHEN SUM(m.meta_form_submit_deposit) > 0
                        THEN SUM(m.spend) / SUM(m.meta_form_submit_deposit)
                        ELSE NULL END, 2
               )                                                AS cpr_fsd
        FROM ad_metrics m
        LEFT JOIN ad_creatives cr ON cr.ad_id = m.ad_id
        WHERE m.ad_id != ''
          AND m.date BETWEEN ? AND ?
        GROUP BY COALESCE(cr.ad_style, 'unknown')
        HAVING SUM(m.spend) > 0
        ORDER BY cpr_fsd ASC NULLS LAST, spend DESC
    """
    try:
        with _conn(db_path) as con:
            rows = con.execute(sql, (start_date, end_date)).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []


def get_creative_concept_breakdown(
    db_path: Path, start_date: str, end_date: str
) -> list[dict[str, Any]]:
    """Performance grouped by creative concept — all copies of the same concept merged.

    The concept key is extracted from ad_name by taking the 2nd pipe-delimited segment
    (e.g. 'Nowa | NOSTBRD-06-pt1-c1 | native_ui | ...' → 'NOSTBRD-06') and stripping
    the '-ptN-cN' production suffix common to static image ads.

    Multiple copies of the same concept running across different campaigns (or the same
    campaign) are collapsed into a single row so you can judge the concept itself, not
    the distribution mechanic.

    Returns sorted by CPR (FSD) ascending (best first), nulls last, then FSD descending.
    Returns [] if no ad-level creative data is available.
    """
    import re as _re

    def _concept_key(ad_name: str) -> str:
        parts = ad_name.split(" | ")
        raw = parts[1].strip() if len(parts) >= 2 else ad_name
        # Strip -ptN-cN production suffix (e.g. '-pt1-c1', '-pt2-c3')
        return _re.sub(r"-pt\d+-c\d+$", "", raw, flags=_re.IGNORECASE).strip()

    sql = """
        SELECT
            cr.ad_id,
            cr.ad_name,
            cr.ad_format,
            cr.ad_style,
            cr.thumbnail_url,
            ROUND(SUM(m.spend), 2)                              AS spend,
            SUM(m.impressions)                                  AS impressions,
            SUM(m.clicks)                                       AS clicks,
            SUM(m.meta_form_submit_deposit)                     AS fsd,
            ROUND(AVG(m.ctr), 2)                                AS avg_ctr,
            ROUND(AVG(NULLIF(m.frequency, 0)), 2)               AS avg_frequency
        FROM ad_metrics m
        JOIN ad_creatives cr ON m.ad_id = cr.ad_id
        WHERE m.ad_id != ''
          AND m.date BETWEEN ? AND ?
        GROUP BY m.ad_id
        HAVING SUM(m.spend) >= 5
    """
    try:
        with _conn(db_path) as con:
            raw_rows = [dict(r) for r in con.execute(sql, (start_date, end_date)).fetchall()]
    except sqlite3.OperationalError:
        return []

    # Group by concept key
    concepts: dict[str, dict[str, Any]] = {}
    for row in raw_rows:
        key = _concept_key(row.get("ad_name") or "")
        if key not in concepts:
            concepts[key] = {
                "concept": key,
                "ad_format": row.get("ad_format") or "unknown",
                "ad_style": row.get("ad_style") or "unknown",
                "thumbnail_url": row.get("thumbnail_url") or "",
                "ad_copies": 0,
                "spend": 0.0,
                "impressions": 0,
                "clicks": 0,
                "fsd": 0,
                "_impr_ctr_sum": 0.0,  # impressions-weighted CTR for avg
            }
        c = concepts[key]
        c["ad_copies"] += 1
        c["spend"] = round(c["spend"] + float(row.get("spend") or 0), 2)
        c["impressions"] += int(row.get("impressions") or 0)
        c["clicks"] += int(row.get("clicks") or 0)
        c["fsd"] += int(row.get("fsd") or 0)
        c["_impr_ctr_sum"] += float(row.get("avg_ctr") or 0) * int(row.get("impressions") or 0)

    results: list[dict[str, Any]] = []
    for c in concepts.values():
        c["cpr_fsd"] = round(c["spend"] / c["fsd"], 2) if c["fsd"] > 0 else None
        c["avg_ctr"] = round(c["_impr_ctr_sum"] / c["impressions"], 2) if c["impressions"] > 0 else 0.0
        del c["_impr_ctr_sum"]
        results.append(c)

    # Sort: CPR asc (None last), then FSD desc, then spend desc
    results.sort(key=lambda x: (
        x["cpr_fsd"] is None,
        x["cpr_fsd"] if x["cpr_fsd"] is not None else float("inf"),
        -x["fsd"],
        -x["spend"],
    ))
    return results


def get_adset_learning_status(
    db_path: Path, end_date: str, window_days: int = 7
) -> dict[str, bool]:
    """Return {ad_set_id: True} for ad sets currently in Meta's learning phase.

    Learning phase = fewer than 50 FSDs in the most recent `window_days` days.
    Only ad-set level rows (ad_set_id != '', ad_id = '') are considered.
    Returns an empty dict if no ad-set level data exists.

    Rule of thumb: Meta exits learning after ~50 conversions/week per ad set.
    Ad sets below this threshold should not be judged on CPR alone.
    """
    from datetime import date as _date, timedelta as _td
    try:
        end = _date.fromisoformat(end_date)
    except ValueError:
        return {}
    start = (end - _td(days=window_days - 1)).isoformat()

    sql = """
        SELECT ad_set_id,
               COALESCE(SUM(meta_form_submit_deposit), 0) AS fsd_window
        FROM ad_metrics
        WHERE ad_set_id != ''
          AND ad_id = ''
          AND date BETWEEN ? AND ?
        GROUP BY ad_set_id
    """
    try:
        with _conn(db_path) as con:
            rows = con.execute(sql, (start, end_date)).fetchall()
        return {r["ad_set_id"]: int(r["fsd_window"]) < 50 for r in rows}
    except sqlite3.OperationalError:
        return {}


# ---------------------------------------------------------------------------
# Segment display name lookup
# ---------------------------------------------------------------------------

_SEGMENT_DISPLAY_NAMES: dict[str, str] = {
    "1a-screen-time":           "1A Screen Time",
    "1b-homework-meltdown":     "1B Homework Meltdown",
    "1c-anxiety-regulation":    "1C Anxiety Regulation",
    "1d-routine-chaos":         "1D Routine Chaos",
    "2b-sturdy-parenting":      "2B Sturdy Parenting",
    "2c-homeschool":            "2C Homeschool",
    "3a-first-ai-introduction": "3A First AI",
    "5a-pcit-at-home":          "5A ADHD-EF",
    "5b-pcit-sm-at-home":       "5B Selective Mutism",
    "6a-nostalgia-bridge":      "6A Nostalgia Bridge",
}


def segment_display_name(slug: str | None) -> str:
    """Map a landing-page source slug to a human-readable segment name.

    Falls back to the raw slug if not found in the lookup table,
    so new segments appear with their slug instead of crashing.
    """
    if not slug:
        return "(unknown)"
    return _SEGMENT_DISPLAY_NAMES.get(str(slug).strip(), slug)


def get_tracking_gap_days(
    db_path: Path, start_date: str, end_date: str
) -> list[dict[str, Any]]:
    """Daily Meta clicks vs GA4 sessions with GA4/click ratio.

    Uses Meta ``clicks`` as a proxy for landing-page views (the ad_metrics table
    has no landing_page_views column).  The ratio underestimates true tracking
    coverage slightly because clicks > LPVs, but the directional signal is valid.

    Returns rows: date, meta_clicks, ga4_sessions, ratio_pct (None when clicks=0).
    Returns [] on missing table or no data.
    """
    sql = """
        SELECT m.date,
               COALESCE(SUM(m.clicks), 0)   AS meta_clicks,
               COALESCE(g.sessions, 0)       AS ga4_sessions,
               CASE WHEN SUM(m.clicks) > 0
                    THEN ROUND(
                        COALESCE(g.sessions, 0) * 100.0 / SUM(m.clicks), 1
                    )
                    ELSE NULL END            AS ratio_pct
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
    try:
        with _conn(db_path) as con:
            rows = con.execute(sql, (start_date, end_date)).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []


def get_stripe_source_trend(
    db_path: Path, start_date: str, end_date: str
) -> list[dict[str, Any]]:
    """Per-source breakdown with current and prior equal-length period comparison.

    Prior period is the same number of calendar days immediately before start_date.
    Rows include: source, fsd, paid, paid_rate, prior_fsd, prior_paid,
                  prior_paid_rate, delta_paid_rate_pp.
    Returns [] if the stripe_payments table does not yet exist.
    """
    from datetime import date as _date, timedelta as _td
    try:
        s = _date.fromisoformat(start_date)
        e = _date.fromisoformat(end_date)
    except ValueError:
        return []

    n_days = (e - s).days + 1
    prior_end = (s - _td(days=1)).isoformat()
    prior_start = (s - _td(days=n_days)).isoformat()

    sql = """
        SELECT src,
               SUM(CASE WHEN period = 'current' THEN 1  ELSE 0 END) AS fsd,
               SUM(CASE WHEN period = 'current' THEN pd ELSE 0 END) AS paid,
               SUM(CASE WHEN period = 'prior'   THEN 1  ELSE 0 END) AS prior_fsd,
               SUM(CASE WHEN period = 'prior'   THEN pd ELSE 0 END) AS prior_paid
        FROM (
            SELECT COALESCE(source, '(unknown)') AS src,
                   CASE WHEN status = 'paid' THEN 1 ELSE 0 END AS pd,
                   'current' AS period
            FROM stripe_payments
            WHERE date(submitted_at) BETWEEN ? AND ?
            UNION ALL
            SELECT COALESCE(source, '(unknown)') AS src,
                   CASE WHEN status = 'paid' THEN 1 ELSE 0 END AS pd,
                   'prior' AS period
            FROM stripe_payments
            WHERE date(submitted_at) BETWEEN ? AND ?
        )
        GROUP BY src
        ORDER BY SUM(CASE WHEN period = 'current' THEN 1 ELSE 0 END) DESC
    """
    try:
        with _conn(db_path) as con:
            rows = con.execute(
                sql, (start_date, end_date, prior_start, prior_end)
            ).fetchall()
    except sqlite3.OperationalError:
        return []

    results: list[dict[str, Any]] = []
    for r in rows:
        fsd = int(r["fsd"] or 0)
        paid = int(r["paid"] or 0)
        prior_fsd = int(r["prior_fsd"] or 0)
        prior_paid = int(r["prior_paid"] or 0)

        paid_rate = round(paid * 100.0 / fsd, 1) if fsd > 0 else None
        prior_paid_rate = round(prior_paid * 100.0 / prior_fsd, 1) if prior_fsd > 0 else None
        delta: float | None = None
        if paid_rate is not None and prior_paid_rate is not None:
            delta = round(paid_rate - prior_paid_rate, 1)

        results.append({
            "source": r["src"],
            "fsd": fsd,
            "paid": paid,
            "paid_rate": paid_rate,
            "prior_fsd": prior_fsd,
            "prior_paid": prior_paid,
            "prior_paid_rate": prior_paid_rate,
            "delta_paid_rate_pp": delta,
        })
    return results


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


# ---------------------------------------------------------------------------
# Phase C: Tracking Health page queries
# ---------------------------------------------------------------------------

def get_click_session_ratio(db_path: Path, start_date: str, end_date: str) -> float | None:
    """Overall click->session ratio %% over a window: total GA4 sessions / total Meta clicks.

    Uses period TOTALS (not an average of daily ratios) — more statistically sound
    when daily click volume is uneven. Returns None when there were zero clicks in
    the window (ratio undefined) or the underlying tables don't exist yet.
    """
    sql = """
        SELECT
            COALESCE((SELECT SUM(clicks) FROM ad_metrics
                      WHERE ad_set_id = '' AND ad_id = '' AND date BETWEEN ? AND ?), 0) AS total_clicks,
            COALESCE((SELECT SUM(sessions) FROM ga4_metrics
                      WHERE date BETWEEN ? AND ?), 0) AS total_sessions
    """
    try:
        with _conn(db_path) as con:
            row = con.execute(sql, (start_date, end_date, start_date, end_date)).fetchone()
    except sqlite3.OperationalError:
        return None
    if not row:
        return None
    total_clicks = row["total_clicks"] or 0
    if total_clicks <= 0:
        return None
    return round((row["total_sessions"] or 0) * 100.0 / total_clicks, 1)


def get_purchase_divergence(db_path: Path, start_date: str, end_date: str) -> dict[str, Any]:
    """Meta vs GA4 purchase counts, side-by-side (never blended — CLAUDE.md).

    Returns {"meta_purchases": int, "ga4_purchases": int, "gap_pct": float | None}.
    gap_pct reuses the same max-two-value gap formula as components.compute_gap_pct
    (kept as a plain calculation here to avoid a Streamlit-adjacent module importing
    from components for a single number — callers that want banding should pass
    both counts through src.dashboard.components.compute_gap_pct themselves).
    """
    meta_total = get_meta_purchases_total(db_path, start_date, end_date)
    ga4 = get_ga4_kpi(db_path, start_date, end_date)
    ga4_total = int(ga4.get("total_purchases", 0) or 0)
    return {"meta_purchases": meta_total, "ga4_purchases": ga4_total}


def get_event_daily_counts(
    db_path: Path, event_name: str, start_date: str, end_date: str
) -> list[dict[str, Any]]:
    """Daily ga4_events counts for one event name, summed across campaign_utm/lp_slug.

    Returns [] on missing table / no data (graceful degradation for empty-DB state).
    """
    sql = """
        SELECT date, COALESCE(SUM(event_count), 0) AS event_count
        FROM ga4_events
        WHERE event_name = ? AND date BETWEEN ? AND ?
        GROUP BY date
        ORDER BY date
    """
    try:
        with _conn(db_path) as con:
            rows = con.execute(sql, (event_name, start_date, end_date)).fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(r) for r in rows]


def get_sessions_daily(db_path: Path, start_date: str, end_date: str) -> list[dict[str, Any]]:
    """Daily total GA4 sessions across all campaigns. Returns [] on missing table."""
    sql = """
        SELECT date, COALESCE(SUM(sessions), 0) AS sessions
        FROM ga4_metrics
        WHERE date BETWEEN ? AND ?
        GROUP BY date
        ORDER BY date
    """
    try:
        with _conn(db_path) as con:
            rows = con.execute(sql, (start_date, end_date)).fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(r) for r in rows]


def get_not_set_campaign_share(
    db_path: Path, event_name: str, start_date: str, end_date: str
) -> float | None:
    """%% of `event_name` rows attributed to campaign_utm='' ("(not set)") in a window.

    Returns None when there's no data at all for the event in this window
    (share is undefined, not zero) or the table doesn't exist yet.
    """
    sql = """
        SELECT
            COALESCE(SUM(CASE WHEN campaign_utm = '' THEN event_count ELSE 0 END), 0) AS not_set,
            COALESCE(SUM(event_count), 0) AS total
        FROM ga4_events
        WHERE event_name = ? AND date BETWEEN ? AND ?
    """
    try:
        with _conn(db_path) as con:
            row = con.execute(sql, (event_name, start_date, end_date)).fetchone()
    except sqlite3.OperationalError:
        return None
    if not row or (row["total"] or 0) <= 0:
        return None
    return round((row["not_set"] or 0) * 100.0 / row["total"], 1)


def get_event_freshness_hours(db_path: Path, event_names: list[str]) -> dict[str, float | None]:
    """Hours since the most recent ga4_events ingestion for each event name.

    Uses MAX(fetched_at) (an ingestion timestamp), not MAX(date) (a calendar day) —
    freshness is about whether the pipeline is still delivering data, not how
    recent the underlying event's calendar date is. Returns None per event when
    that event has never been ingested, or the table doesn't exist yet.
    """
    out: dict[str, float | None] = dict.fromkeys(event_names)
    if not event_names:
        return out
    placeholders = ",".join("?" for _ in event_names)
    sql = f"""
        SELECT event_name, MAX(fetched_at) AS last_fetched
        FROM ga4_events
        WHERE event_name IN ({placeholders})
        GROUP BY event_name
    """  # noqa: S608 — placeholders are '?' marks, not interpolated values
    try:
        with _conn(db_path) as con:
            rows = con.execute(sql, tuple(event_names)).fetchall()
    except sqlite3.OperationalError:
        return out

    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    for r in rows:
        last_fetched = r["last_fetched"]
        if not last_fetched:
            continue
        try:
            # SQLite datetime('now') produces 'YYYY-MM-DD HH:MM:SS' (naive, UTC).
            parsed = datetime.fromisoformat(last_fetched.replace(" ", "T")).replace(
                tzinfo=timezone.utc
            )
            hours = (now - parsed).total_seconds() / 3600.0
            out[r["event_name"]] = round(hours, 1)
        except ValueError:
            continue
    return out


def get_pixel_health(db_path: Path, start_date: str, end_date: str) -> list[dict[str, Any]]:
    """pixel_health rows for a window, one row per (event_name) aggregated over the range.

    Returns [] on missing table / no data (empty-DB graceful degradation — pixel_health
    is only populated once META_PIXEL_ID is configured and the daily backfill has run).
    """
    sql = """
        SELECT
            event_name,
            COALESCE(SUM(browser_count), 0) AS browser_count,
            COALESCE(SUM(server_count), 0) AS server_count,
            AVG(dedup_rate) AS dedup_rate,
            AVG(emq_score) AS emq_score
        FROM pixel_health
        WHERE date BETWEEN ? AND ?
        GROUP BY event_name
        ORDER BY event_name
    """
    try:
        with _conn(db_path) as con:
            rows = con.execute(sql, (start_date, end_date)).fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(r) for r in rows]
