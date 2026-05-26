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
    """Ads showing creative fatigue: high frequency (>= 2.5) with low CTR or high CPC.

    Returns [] if no ad-level data exists yet.
    """
    sql = """
        SELECT m.ad_id,
               COALESCE(cr.ad_name, m.ad_id)                    AS ad_name,
               c.name                                            AS campaign_name,
               ROUND(SUM(m.spend), 2)                           AS spend,
               ROUND(AVG(NULLIF(m.frequency, 0)), 2)            AS avg_frequency,
               ROUND(AVG(m.ctr), 3)                             AS avg_ctr,
               ROUND(AVG(NULLIF(m.cpc, 0)), 2)                  AS avg_cpc,
               SUM(m.meta_form_submit_deposit)                  AS fsd,
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
        HAVING avg_frequency >= 2.5 AND SUM(m.spend) > 0
        ORDER BY avg_frequency DESC
    """
    try:
        with _conn(db_path) as con:
            rows = con.execute(sql, (start_date, end_date)).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []


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
               )                                                AS weighted_roas
        FROM ad_metrics m
        LEFT JOIN ad_creatives cr ON cr.ad_id = m.ad_id
        WHERE m.ad_id != ''
          AND m.date BETWEEN ? AND ?
        GROUP BY COALESCE(cr.ad_style, 'unknown')
        HAVING SUM(m.spend) > 0
        ORDER BY spend DESC
    """
    try:
        with _conn(db_path) as con:
            rows = con.execute(sql, (start_date, end_date)).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []


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
