"""Sync SQLite data access for the Streamlit dashboard.

All queries use ? positional params (sqlite3 style) and campaign-level
ad_metrics rows only (ad_set_id = '' AND ad_id = '').
Never blends Meta and GA4 conversion numbers.
"""
from __future__ import annotations

import re
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


# ---------------------------------------------------------------------------
# Shopify orders — MER / Blended CAC / Pre-orders KPIs (Overview v2, 2026-07-22)
#
# shopify_orders is ground truth for orders/revenue (financial_status = 'paid').
# orders_valid_from excludes pre-launch/test orders via order_date >= cutoff —
# same query-time-only filter convention as get_orders_step / get_shopify_paid
# summary's siblings elsewhere in this module. Never blended with Meta/GA4
# conversion counts (CLAUDE.md) — MER and Blended CAC divide Shopify revenue/
# count by Meta spend, which is an attribution-free ratio, not a blend of two
# conversion-count sources.
# ---------------------------------------------------------------------------
def get_shopify_paid_summary(
    db_path: Path, start_date: str, end_date: str, orders_valid_from: str = ""
) -> dict[str, Any]:
    """Shopify paid order count + revenue for the Overview KPI row.

    Returns {"count": int, "revenue": float}. Defaults to zeros on a missing
    table (pre-migration DB) or no rows in range -- matches the graceful-
    degradation convention used throughout this module.
    """
    valid_from_clause = " AND order_date >= ?" if orders_valid_from else ""
    params: list[str] = [start_date, end_date]
    if orders_valid_from:
        params.append(orders_valid_from)
    sql = (
        "SELECT COUNT(*) AS n, COALESCE(SUM(total_price), 0) AS revenue "
        "FROM shopify_orders WHERE financial_status = 'paid' "
        "AND order_date BETWEEN ? AND ?" + valid_from_clause
    )
    try:
        with _conn(db_path) as con:
            row = con.execute(sql, params).fetchone()
        return {
            "count": int(row["n"]) if row else 0,
            "revenue": float(row["revenue"]) if row else 0.0,
        }
    except sqlite3.OperationalError:
        return {"count": 0, "revenue": 0.0}


def get_shopify_paid_daily(
    db_path: Path, start_date: str, end_date: str, orders_valid_from: str = ""
) -> list[dict[str, Any]]:
    """Daily Shopify paid order count -- used by the "Meta Initiate Checkout vs
    Shopify Paid Orders" Overview chart (Overview v2, 2026-07-22).

    Returns [] on a missing table / no rows (graceful degradation).
    """
    valid_from_clause = " AND order_date >= ?" if orders_valid_from else ""
    params: list[str] = [start_date, end_date]
    if orders_valid_from:
        params.append(orders_valid_from)
    sql = (
        "SELECT order_date AS date, COUNT(*) AS paid FROM shopify_orders "
        "WHERE financial_status = 'paid' AND order_date BETWEEN ? AND ?"
        + valid_from_clause
        + " GROUP BY order_date ORDER BY order_date"
    )
    try:
        with _conn(db_path) as con:
            rows = con.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []


def get_meta_begin_checkout_total(db_path: Path, start_date: str, end_date: str) -> int:
    """Period total of Meta meta_begin_checkout (campaign-level ad_metrics rows).

    Used by the Overview LPV->Checkout CVR KPI and the "Spend vs Initiate Checkout"
    chart (Overview v2, 2026-07-22) -- mirrors get_meta_purchases_total's shape.
    """
    sql = """
        SELECT COALESCE(SUM(m.meta_begin_checkout), 0) AS total
        FROM ad_metrics m
        WHERE m.ad_set_id = '' AND m.ad_id = ''
          AND m.date BETWEEN ? AND ?
    """
    try:
        with _conn(db_path) as con:
            row = con.execute(sql, (start_date, end_date)).fetchone()
        return int(row["total"]) if row and row["total"] is not None else 0
    except sqlite3.OperationalError:
        return 0


def get_daily_trend(db_path: Path, start_date: str, end_date: str) -> list[dict[str, Any]]:
    sql = """
        SELECT
            m.date,
            COALESCE(SUM(m.spend), 0)                        AS spend,
            COALESCE(SUM(m.meta_form_submit_deposit), 0)     AS deposits,
            COALESCE(SUM(m.meta_begin_checkout), 0)          AS begin_checkout,
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
    """Per-day × per-campaign: spend, FSD, CPR (FSD), begin_checkout,
    cost-per-begin-checkout, CTR. Campaign-level rows only
    (ad_set_id = '', ad_id = ''). Used by the Overview daily-trends-by-
    campaign charts. `fsd`/`cpr` are kept for backward compatibility
    (legacy deposit-era charts); `begin_checkout`/`cost_per_bc` power the
    Overview v2 "Initiate Checkout by campaign" / "Cost per Initiate Checkout per
    campaign" charts.
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
            COALESCE(m.meta_begin_checkout, 0)                           AS begin_checkout,
            CASE WHEN m.meta_begin_checkout > 0
                 THEN m.spend / m.meta_begin_checkout
                 ELSE NULL END                                           AS cost_per_bc,
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
    """Per-campaign summary for the Overview "Campaign performance" table.

    `deposits`/`cpd` (meta_form_submit_deposit-based, FSD) are kept for
    backward compatibility but are dead on current data -- no current
    campaign fires this event. `begin_checkout`/`cost_per_bc`
    (meta_begin_checkout-based, Initiate Checkout) are the live metric and
    back the table's "Initiate Checkout"/"CPR (Initiate Checkout)" columns
    (FSD -> Initiate Checkout re-point, 2026-07-22). `leads`/`cost_per_lead`
    (meta_leads-based -- Meta's offsite_conversion.fb_pixel_lead) are the
    equivalent live metric for OUTCOME_LEADS-objective campaigns, added so
    the Overview "Conversion metric" picker can show each campaign's own
    native metric instead of always defaulting to Initiate Checkout (dead
    use_form_submit toggle re-point, 2026-07-23). Falls back to an
    unconditional `leads=0`/`cost_per_lead=None` on sqlite3.OperationalError
    (pre-migration DB predating the meta_leads column) -- graceful
    degradation, matching every other query in this module. Sort order
    intentionally left as `deposits DESC` (pinned by
    test_campaign_table_sorted_by_deposits_desc, and the on-screen dataframe
    is user-sortable by clicking any column header anyway).
    """
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
            COALESCE(SUM(m.meta_begin_checkout), 0)                      AS begin_checkout,
            CASE WHEN SUM(m.meta_begin_checkout) > 0
                 THEN SUM(m.spend) / SUM(m.meta_begin_checkout)
                 ELSE NULL END                                           AS cost_per_bc,
            COALESCE(SUM(m.meta_leads), 0)                               AS leads,
            CASE WHEN SUM(m.meta_leads) > 0
                 THEN SUM(m.spend) / SUM(m.meta_leads)
                 ELSE NULL END                                           AS cost_per_lead,
            COALESCE(SUM(g.sessions), 0)                                 AS ga4_sessions
        FROM ad_metrics m
        JOIN campaigns c ON m.campaign_id = c.id
        LEFT JOIN ga4_metrics g ON g.campaign_utm = c.name AND g.date = m.date
        WHERE m.ad_set_id = '' AND m.ad_id = ''
          AND m.date BETWEEN ? AND ?
        GROUP BY c.name
        ORDER BY deposits DESC, spend DESC
    """
    fallback_sql_no_leads = """
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
            COALESCE(SUM(m.meta_begin_checkout), 0)                      AS begin_checkout,
            CASE WHEN SUM(m.meta_begin_checkout) > 0
                 THEN SUM(m.spend) / SUM(m.meta_begin_checkout)
                 ELSE NULL END                                           AS cost_per_bc,
            COALESCE(SUM(g.sessions), 0)                                 AS ga4_sessions
        FROM ad_metrics m
        JOIN campaigns c ON m.campaign_id = c.id
        LEFT JOIN ga4_metrics g ON g.campaign_utm = c.name AND g.date = m.date
        WHERE m.ad_set_id = '' AND m.ad_id = ''
          AND m.date BETWEEN ? AND ?
        GROUP BY c.name
        ORDER BY deposits DESC, spend DESC
    """
    try:
        with _conn(db_path) as con:
            rows = con.execute(sql, (start_date, end_date)).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        with _conn(db_path) as con:
            rows = con.execute(fallback_sql_no_leads, (start_date, end_date)).fetchall()
        out = [dict(r) for r in rows]
        for r in out:
            r["leads"] = 0
            r["cost_per_lead"] = None
        return out


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
    """Meta/GA4 freshness for the Overview sidebar ("Meta last date: ...").

    D-05 fix: reflects MAX(date) / MAX(fetched_at) straight from ad_metrics /
    ga4_metrics -- the same tables the backfill (src.meta.ingest / src.ga4.ingest)
    upserts into, so this always matches what was actually written, whenever it
    was written. Previously this was the one aggregate-query function in this
    module without the try/except OperationalError graceful-degradation pattern
    every other query here follows -- on a brand-new/pre-migration DB (tables
    missing entirely, not just empty) it would raise instead of showing "—",
    which is the most likely way the sidebar could end up showing nothing.
    """
    try:
        with _conn(db_path) as con:
            meta = con.execute(
                "SELECT MAX(fetched_at) AS fetched, MAX(date) AS last_date FROM ad_metrics"
            ).fetchone()
            ga4 = con.execute(
                "SELECT MAX(fetched_at) AS fetched, MAX(date) AS last_date FROM ga4_metrics"
            ).fetchone()
    except sqlite3.OperationalError:
        return {
            "meta_fetched": None, "meta_last_date": None,
            "ga4_fetched": None, "ga4_last_date": None,
        }
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


def get_campaign_objectives(db_path: Path) -> dict[str, str]:
    """Map campaign name -> raw Meta objective (e.g. 'OUTCOME_SALES').

    Backs the Overview drill-down selectbox and the Campaign Detail objective
    badge (item 2, 2026-07-22) -- both look up a campaign by name, not id, so
    this returns {name: objective} rather than {id: objective}. Use
    objective_display_label() to render the raw value human-readably.

    Returns {} on a missing table/column (pre-migration DB, before migration
    015_campaign_objective has run) -- graceful degradation, matching every
    other query in this module. Campaigns with a NULL objective (not yet
    backfilled by an ingest run) are simply omitted from the dict.
    """
    try:
        with _conn(db_path) as con:
            rows = con.execute(
                "SELECT name, objective FROM campaigns WHERE objective IS NOT NULL"
            ).fetchall()
        return {r["name"]: r["objective"] for r in rows}
    except sqlite3.OperationalError:
        return {}


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

    `deposits` (meta_form_submit_deposit-based, FSD) is kept for backward
    compatibility but is dead on current data. `begin_checkout`/`cost_per_bc`
    (meta_begin_checkout-based, Initiate Checkout) are the live metric and
    back Campaign Detail's KPI strip + charts (FSD -> Initiate Checkout
    re-point, 2026-07-22).
    """
    sql = '''
        SELECT
            m.date                                           AS date,
            COALESCE(SUM(m.spend), 0)                        AS spend,
            COALESCE(SUM(m.meta_form_submit_deposit), 0)     AS deposits,
            COALESCE(SUM(m.meta_begin_checkout), 0)          AS begin_checkout,
            CASE WHEN SUM(m.meta_begin_checkout) > 0
                 THEN SUM(m.spend) / SUM(m.meta_begin_checkout)
                 ELSE NULL END                                AS cost_per_bc,
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


def match_campaign_to_utm(
    campaign_name: str, utm_campaign_map: dict[str, str]
) -> str | None:
    """Resolve the ga4_metrics/ga4_events `campaign_utm` value to filter or
    join on for a given Meta campaign name (utm mapping fix, 2026-07-22).

    Extracted from src/dashboard/pages/1_Campaign_Detail.py's
    `_reverse_utm_match`, which was duplicated across three independently
    discovered instances of the same bug: GA4 sessions and GA4 purchases in
    `get_campaign_daily` (one exact-name join backs both columns) and GA4
    engagement in `get_campaign_ga4_engagement`. All three fail the same way:
    they join/filter `ga4_metrics.campaign_utm` against the full Meta
    campaign name (e.g. 'Nowa | SALES | preorder-image | 20260715') using an
    exact match, but GA4 actually stores a short slug there (`nowa_preorder`,
    `nowa_quiz` -- see `src.config.utm_campaign_map`), so the exact match
    silently returns zero/NULL rows forever.

    Resolution, in order:
      1. Exact match: `campaign_name` is itself a valid utm slug (already a
         key in `utm_campaign_map`) -- return it unchanged. Cheap, and
         correct for any case where the caller already has the right value
         (e.g. a future campaign generation whose Meta names and GA4
         campaign_utm slugs happen to align, or a caller that already
         resolved the utm and calls this again defensively).
      2. Reverse substring match: `campaign_name` contains the Meta
         campaign-name substring mapped to a utm slug in `utm_campaign_map`
         (current campaign generation: 'SALES' -> 'nowa_preorder', 'LEADS'
         -> 'nowa_quiz').
      3. Neither matches -> None. Callers must treat this as "no GA4 data
         resolvable for this campaign" and say so, never guess/blend.

    `utm_campaign_map` is passed in explicitly (not imported from
    src.config) because dashboard modules stay decoupled from the
    TELEGRAM_BOT_TOKEN-requiring Settings import chain (see
    DashboardSettings' "standalone" docstring) -- callers pass their own
    local copy of the map (e.g. pages/1_Campaign_Detail.py's
    UTM_CAMPAIGN_MAP, duplicated from src.config.utm_campaign_map per the
    D-19 standalone-page rule).
    """
    if campaign_name in utm_campaign_map:
        return campaign_name
    for utm, substring in utm_campaign_map.items():
        if substring in campaign_name:
            return utm
    return None


def get_ga4_daily_by_utm(
    db_path: Path, utm_campaign: str, start_date: str, end_date: str
) -> list[dict[str, Any]]:
    """Daily GA4 sessions + purchases for one utm_campaign value (exact match).

    Campaign Detail fallback (2026-07-22): GA4's utm_campaign values for the
    current campaign generation ('nowa_preorder' / 'nowa_quiz') never
    exact-match a Meta campaign *name* ('Nowa | SALES | ... '), so
    get_campaign_daily's join always returns zero GA4 rows for these
    campaigns. This function fetches GA4 data straight by utm value -- the
    caller resolves that utm value via `match_campaign_to_utm` first -- so
    the page can show real GA4 numbers with a caption explaining the utm
    covers the whole campaign generation, not just the one campaign drilled
    into.

    Returns [] on a missing table / no rows (graceful degradation).
    """
    sql = """
        SELECT date,
               COALESCE(sessions, 0)                 AS sessions,
               COALESCE(ga4_purchases_lastclick, 0)  AS ga4_purchases
        FROM ga4_metrics
        WHERE campaign_utm = ? AND date BETWEEN ? AND ?
        ORDER BY date
    """
    try:
        with _conn(db_path) as con:
            rows = con.execute(sql, (utm_campaign, start_date, end_date)).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []


def get_campaign_ga4_engagement(
    db_path: Path,
    campaign_name: str,
    start_date: str,
    end_date: str,
) -> dict[str, Any]:
    """GA4 engagement metrics for one campaign UTM over [start_date, end_date].

    Returns avg_bounce_rate, avg_engagement_time_sec, total_users, total_new_users.
    Filter key: campaign_utm = campaign_name (exact match, CLAUDE.md rule) --
    despite the parameter name, this is a plain WHERE campaign_utm = ? filter
    with no join, so it works equally well if the caller passes GA4's raw
    utm_campaign slug ('nowa_preorder'/'nowa_quiz') instead of a full Meta
    campaign name.

    Campaign Detail's exact-name calls always come back empty for the
    current campaign generation, same root cause as get_campaign_daily's
    broken sessions/purchases join: GA4's campaign_utm is a short slug, not
    the full Meta campaign name (utm mapping fix, 2026-07-22). Because this
    function has no join to work around (unlike get_campaign_daily), the
    page-level fallback (1_Campaign_Detail.py) simply re-calls this same
    function with the utm slug resolved by `match_campaign_to_utm` instead
    of using a separate by-utm helper.

    When no rows match, AVG()/SUM() return NULL for every column (not 0) --
    the caller's "is this empty" check must test for None, not falsy/0.
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

    `deposits`/`cpd` (meta_form_submit_deposit-based, FSD) are kept for
    backward compatibility but are dead on current data. `begin_checkout`/
    `cost_per_bc` (meta_begin_checkout-based, Initiate Checkout) are the live
    metric and back the Campaign Detail ad-set table's "Initiate Checkout"/
    "CPR (Initiate Checkout)" columns (FSD -> Initiate Checkout re-point,
    2026-07-22) -- sort order below now follows cost_per_bc ascending to
    match the page's "sorted by CPR (Initiate Checkout) ascending" caption.
    """
    sql = """
        SELECT
            m.ad_set_id                                                  AS ad_set_id,
            COALESCE(SUM(m.spend), 0)                                    AS spend,
            COALESCE(SUM(m.meta_form_submit_deposit), 0)                 AS deposits,
            CASE WHEN SUM(m.meta_form_submit_deposit) > 0
                 THEN SUM(m.spend) / SUM(m.meta_form_submit_deposit)
                 ELSE NULL END                                           AS cpd,
            COALESCE(SUM(m.meta_begin_checkout), 0)                      AS begin_checkout,
            CASE WHEN SUM(m.meta_begin_checkout) > 0
                 THEN SUM(m.spend) / SUM(m.meta_begin_checkout)
                 ELSE NULL END                                           AS cost_per_bc,
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
        ORDER BY cost_per_bc ASC NULLS LAST, spend DESC
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
    """Top performing ads by Meta Initiate Checkout (meta_begin_checkout), enriched
    with creative metadata.

    meta_begin_checkout (Meta 7-day-click) is the primary optimization signal for
    the live preorder funnel (landing_page_views -> meta_begin_checkout ->
    meta_purchases_7dclick); form_submit_deposit is dead (deposit-era funnel, 0
    events). meta_purchases_7dclick is surfaced as a secondary column — never
    blended with GA4 conversion counts (CLAUDE.md).

    Joins ad_metrics (ad_id != '') with ad_creatives for name/format/style/URLs.
    Returns [] if no ad-level data exists yet.
    """
    sql = """
        SELECT m.ad_id,
               COALESCE(cr.ad_name, m.ad_id)                    AS ad_name,
               c.name                                            AS campaign_name,
               ROUND(SUM(m.spend), 2)                           AS spend,
               SUM(m.impressions)                               AS impressions,
               SUM(m.meta_begin_checkout)                       AS bc,
               SUM(m.meta_purchases_7dclick)                    AS purchases,
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
                   CASE WHEN SUM(m.meta_begin_checkout) > 0
                        THEN SUM(m.spend) / SUM(m.meta_begin_checkout)
                        ELSE NULL END, 2
               )                                                AS cost_per_bc,
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
        ORDER BY bc DESC, spend DESC
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
      2. Rising cost-per-IC — cost per meta_begin_checkout in late half > early
         half by ≥30 % (IC = Initiate Checkout, Meta 7d-click — the live preorder
         funnel's primary optimization signal; form_submit_deposit is dead)
      3. High frequency — avg frequency ≥ 2.5 (audience saturation)
      4. Diminishing returns — IC rate (meta_begin_checkout/impressions) fell
         >40 % between halves

    The date range is split at its midpoint; CTR and cost-per-IC trends are
    computed by comparing early-half vs late-half aggregates using conditional
    SQL. Requires ≥4 days of data in range to split meaningfully.

    Returns each fatigued ad enriched with:
      fatigue_signals  – list of triggered signal descriptions
      ctr_change_pct   – % change in CTR (negative = decline)
      cpbc_change_pct  – % change in cost-per-IC (positive = more expensive)
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
               SUM(m.meta_begin_checkout)                                            AS bc,
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
               -- Cost-per-IC split (cost per meta_begin_checkout)
               ROUND(
                   SUM(CASE WHEN m.date <= ? THEN m.spend ELSE 0 END)
                   / NULLIF(SUM(CASE WHEN m.date <= ? THEN m.meta_begin_checkout ELSE 0 END), 0), 2
               )                                                                     AS cpbc_early,
               ROUND(
                   SUM(CASE WHEN m.date > ? THEN m.spend ELSE 0 END)
                   / NULLIF(SUM(CASE WHEN m.date > ? THEN m.meta_begin_checkout ELSE 0 END), 0), 2
               )                                                                     AS cpbc_late,
               -- Impressions split (for IC-rate / diminishing-returns check)
               SUM(CASE WHEN m.date <= ? THEN m.impressions ELSE 0 END)              AS impr_early,
               SUM(CASE WHEN m.date > ? THEN m.impressions ELSE 0 END)               AS impr_late,
               SUM(CASE WHEN m.date <= ? THEN m.meta_begin_checkout ELSE 0 END)      AS bc_early,
               SUM(CASE WHEN m.date > ? THEN m.meta_begin_checkout ELSE 0 END)       AS bc_late,
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
        cpbc_early = d.get("cpbc_early")
        cpbc_late  = d.get("cpbc_late")
        freq      = float(d.get("avg_frequency") or 0)
        impr_early = int(d.get("impr_early") or 0)
        impr_late  = int(d.get("impr_late")  or 0)
        bc_early   = int(d.get("bc_early")   or 0)
        bc_late    = int(d.get("bc_late")    or 0)

        # --- Signal 1: Declining CTR ---
        ctr_change_pct: float | None = None
        if ctr_early > 0:
            ctr_change_pct = round((ctr_late - ctr_early) / ctr_early * 100, 1)
            if ctr_change_pct <= -30:
                signals.append(f"CTR dropped {abs(ctr_change_pct):.0f}%")

        # --- Signal 2: Rising cost-per-IC ---
        cpbc_change_pct: float | None = None
        if cpbc_early and cpbc_late and float(cpbc_early) > 0:
            cpbc_change_pct = round((float(cpbc_late) - float(cpbc_early)) / float(cpbc_early) * 100, 1)
            if cpbc_change_pct >= 30:
                signals.append(f"Cost/IC rose {cpbc_change_pct:.0f}%")

        # --- Signal 3: High frequency ---
        if freq >= 2.5:
            signals.append(f"Frequency {freq:.1f}×")

        # --- Signal 4: Diminishing returns (IC rate fell >40 %) ---
        if impr_early >= 100 and impr_late >= 100 and bc_early > 0:
            rate_early = bc_early / impr_early
            rate_late  = bc_late  / impr_late if impr_late else 0
            if rate_early > 0 and rate_late < rate_early * 0.6:
                signals.append("IC rate fell >40%")

        if not signals:
            continue

        d["fatigue_signals"] = signals
        d["ctr_change_pct"]  = ctr_change_pct
        d["cpbc_change_pct"] = cpbc_change_pct

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
        elif cpbc_change_pct is not None and cpbc_change_pct >= 50:
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
    """Spend, Initiate Checkout (meta_begin_checkout), CTR by ad format (image,
    video, carousel).

    Joins ad_metrics with ad_creatives for format labels.
    Returns [] if no ad-level or creative data.
    """
    sql = """
        SELECT COALESCE(cr.ad_format, 'unknown')                AS ad_format,
               COUNT(DISTINCT m.ad_id)                          AS ad_count,
               ROUND(SUM(m.spend), 2)                           AS spend,
               SUM(m.impressions)                               AS impressions,
               SUM(m.clicks)                                    AS clicks,
               SUM(m.meta_begin_checkout)                       AS bc,
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
    """Spend, Initiate Checkout (meta_begin_checkout), CTR by ad style
    (testimonial, product_hero, etc.).

    Returns [] if no ad-level or creative data.
    """
    sql = """
        SELECT COALESCE(cr.ad_style, 'unknown')                 AS ad_style,
               COUNT(DISTINCT m.ad_id)                          AS ad_count,
               ROUND(SUM(m.spend), 2)                           AS spend,
               SUM(m.impressions)                               AS impressions,
               SUM(m.meta_begin_checkout)                       AS bc,
               ROUND(AVG(m.ctr), 2)                             AS avg_ctr,
               ROUND(
                   CASE WHEN SUM(m.spend) > 0
                        THEN SUM(m.roas * m.spend) / SUM(m.spend)
                        ELSE NULL END, 2
               )                                                AS weighted_roas,
               ROUND(
                   CASE WHEN SUM(m.meta_begin_checkout) > 0
                        THEN SUM(m.spend) / SUM(m.meta_begin_checkout)
                        ELSE NULL END, 2
               )                                                AS cost_per_bc
        FROM ad_metrics m
        LEFT JOIN ad_creatives cr ON cr.ad_id = m.ad_id
        WHERE m.ad_id != ''
          AND m.date BETWEEN ? AND ?
        GROUP BY COALESCE(cr.ad_style, 'unknown')
        HAVING SUM(m.spend) > 0
        ORDER BY cost_per_bc ASC NULLS LAST, spend DESC
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

    Ranked by cost-per-IC (meta_begin_checkout) ascending (best first), nulls last,
    then IC descending. meta_purchases_7dclick is aggregated as a secondary
    `purchases` column — never blended with GA4 conversion counts (CLAUDE.md).
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
            SUM(m.meta_begin_checkout)                          AS bc,
            SUM(m.meta_purchases_7dclick)                       AS purchases,
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
                "bc": 0,
                "purchases": 0,
                "_impr_ctr_sum": 0.0,  # impressions-weighted CTR for avg
            }
        c = concepts[key]
        c["ad_copies"] += 1
        c["spend"] = round(c["spend"] + float(row.get("spend") or 0), 2)
        c["impressions"] += int(row.get("impressions") or 0)
        c["clicks"] += int(row.get("clicks") or 0)
        c["bc"] += int(row.get("bc") or 0)
        c["purchases"] += int(row.get("purchases") or 0)
        c["_impr_ctr_sum"] += float(row.get("avg_ctr") or 0) * int(row.get("impressions") or 0)

    results: list[dict[str, Any]] = []
    for c in concepts.values():
        c["cost_per_bc"] = round(c["spend"] / c["bc"], 2) if c["bc"] > 0 else None
        c["avg_ctr"] = round(c["_impr_ctr_sum"] / c["impressions"], 2) if c["impressions"] > 0 else 0.0
        del c["_impr_ctr_sum"]
        results.append(c)

    # Sort: cost-per-IC asc (None last), then IC desc, then spend desc
    results.sort(key=lambda x: (
        x["cost_per_bc"] is None,
        x["cost_per_bc"] if x["cost_per_bc"] is not None else float("inf"),
        -x["bc"],
        -x["spend"],
    ))
    return results


def get_adset_learning_status(
    db_path: Path, end_date: str, window_days: int = 7
) -> dict[str, bool]:
    """Return {ad_set_id: True} for ad sets currently in Meta's learning phase.

    Learning phase = fewer than 50 Initiate Checkout events (meta_begin_checkout)
    in the most recent `window_days` days. Only ad-set level rows (ad_set_id != '',
    ad_id = '') are considered. Returns an empty dict if no ad-set level data exists.

    Rule of thumb: Meta exits learning after ~50 conversions/week per ad set.
    Ad sets below this threshold should not be judged on CPR alone.

    NOTE (FSD -> Initiate Checkout re-point, 2026-07-22): this function used to
    key off meta_form_submit_deposit (FSD), which is dead on current data. It now
    keys off meta_begin_checkout (Initiate Checkout) instead, but the 50/week
    threshold itself was carried over unchanged and has NOT been independently
    re-validated against Initiate Checkout's typical firing rate -- IC fires much
    earlier/more often in the funnel than a deposit did, so 50/week may be too
    low a bar in practice. Revisit if ad sets exit "Learning" suspiciously fast.
    """
    from datetime import date as _date, timedelta as _td
    try:
        end = _date.fromisoformat(end_date)
    except ValueError:
        return {}
    start = (end - _td(days=window_days - 1)).isoformat()

    sql = """
        SELECT ad_set_id,
               COALESCE(SUM(meta_begin_checkout), 0) AS ic_window
        FROM ad_metrics
        WHERE ad_set_id != ''
          AND ad_id = ''
          AND date BETWEEN ? AND ?
        GROUP BY ad_set_id
    """
    try:
        with _conn(db_path) as con:
            rows = con.execute(sql, (start, end_date)).fetchall()
        return {r["ad_set_id"]: int(r["ic_window"]) < 50 for r in rows}
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


# ---------------------------------------------------------------------------
# Campaign objective (goal) display label — item 2, 2026-07-22
# ---------------------------------------------------------------------------

_OBJECTIVE_DISPLAY_NAMES: dict[str, str] = {
    "OUTCOME_SALES": "Sales",
    "OUTCOME_LEADS": "Leads",
    "OUTCOME_ENGAGEMENT": "Engagement",
    "OUTCOME_AWARENESS": "Awareness",
    "OUTCOME_TRAFFIC": "Traffic",
    "OUTCOME_APP_PROMOTION": "App Promotion",
}


def objective_display_label(objective: str | None) -> str:
    """Map a raw Meta campaign objective (e.g. 'OUTCOME_SALES') to a short
    human label ('Sales') for display next to a campaign name.

    Falls back to a title-cased, 'OUTCOME_'-prefix-stripped version of the
    raw value for objectives not in the lookup table (e.g. a newer Meta
    objective this dashboard hasn't been updated for), so new/unknown
    objectives still render sensibly instead of crashing or showing raw
    'OUTCOME_WHATEVER'. Returns "" for None/empty (no objective known yet —
    caller should omit the badge/suffix in that case).
    """
    if not objective:
        return ""
    known = _OBJECTIVE_DISPLAY_NAMES.get(objective)
    if known is not None:
        return known
    fallback = str(objective).removeprefix("OUTCOME_").replace("_", " ").strip()
    return fallback.title() if fallback else str(objective)


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
# Funnel v3 — preorder funnel, click->session gap, (not set) share, quiz strip
#
# Data-honesty rules (CLAUDE.md + funnel-v3 data layer):
#   - Never blend or average Meta and GA4 conversion numbers -- every step here
#     is reported straight from its own source table (ad_metrics = Meta,
#     ga4_metrics/ga4_events = GA4, shopify_orders = Shopify), never combined.
#   - A step whose source has NEVER been ingested (zero rows ever, not just
#     zero in the selected date range) is reported as *unavailable* ("n/a"),
#     not a measured zero. A dashboard viewer seeing "0" assumes something ran
#     and produced zero conversions; "n/a" correctly signals "not measured
#     yet". All queries below therefore return an `available` flag alongside
#     the value, computed from whether that specific source/event has *ever*
#     had a row (any date), independent of the selected range.
#   - All queries follow the get_tracking_gap_days try/except OperationalError
#     pattern so a fresh/partially-migrated DB degrades to a friendly
#     "no data yet" empty state instead of crashing the page.
# ---------------------------------------------------------------------------

# GA4 stores the literal string '(not set)' in campaign_utm for sessions/events it
# could not tie back to a campaign; a blank '' also occurs (rows from sources/paths
# that never populate the dimension at all). Both mean the same thing -- "no campaign
# attribution" -- and every query that computes a "(not set) share" must match BOTH
# values identically. A prior drift here (get_not_set_campaign_share matching only
# campaign_utm = '' while get_ga4_not_set_share correctly matched IN ('(not set)', ''))
# caused Tracking Health to report 0% "(not set)" share for the exact same underlying
# data the Funnel page reported as ~81% -- two numbers for one fact. Any future
# "(not set)" query MUST bind this tuple via `campaign_utm IN (?, ?)`, never hand-roll
# the match, so the two can't drift apart again.
NOT_SET_CAMPAIGN_VALUES: tuple[str, str] = ("(not set)", "")


def get_meta_funnel_summary(db_path: Path, start_date: str, end_date: str) -> dict[str, Any]:
    """Impressions / clicks / landing_page_views from ad_metrics (Meta side).

    Campaign-level rows only (ad_set_id = '' AND ad_id = '').

    `available` reflects whether ad_metrics has ever had campaign-level rows
    at all (table missing/empty => False). `lpv_available` is a separate,
    stricter flag: landing_page_views is a new nullable column (funnel-v3
    migration 011), so most historical rows predate it -- a row existing does
    not mean this particular metric was ever populated.
    """
    try:
        with _conn(db_path) as con:
            row = con.execute(
                """
                SELECT COALESCE(SUM(impressions), 0)          AS impressions,
                       COALESCE(SUM(clicks), 0)                AS clicks,
                       COALESCE(SUM(landing_page_views), 0)    AS landing_page_views
                FROM ad_metrics
                WHERE ad_set_id = '' AND ad_id = ''
                  AND date BETWEEN ? AND ?
                """,
                (start_date, end_date),
            ).fetchone()
            available = con.execute(
                "SELECT 1 FROM ad_metrics WHERE ad_set_id = '' AND ad_id = '' LIMIT 1"
            ).fetchone() is not None
            lpv_available = con.execute(
                "SELECT 1 FROM ad_metrics WHERE landing_page_views IS NOT NULL LIMIT 1"
            ).fetchone() is not None
        return {
            "impressions": int(row["impressions"]) if row else 0,
            "clicks": int(row["clicks"]) if row else 0,
            "landing_page_views": int(row["landing_page_views"]) if row else 0,
            "available": available,
            "lpv_available": lpv_available,
        }
    except sqlite3.OperationalError:
        return {
            "impressions": 0, "clicks": 0, "landing_page_views": 0,
            "available": False, "lpv_available": False,
        }


def get_ga4_sessions_summary(db_path: Path, start_date: str, end_date: str) -> dict[str, Any]:
    """GA4 sessions total for the range, plus whether ga4_metrics has ever had rows."""
    try:
        with _conn(db_path) as con:
            row = con.execute(
                "SELECT COALESCE(SUM(sessions), 0) AS sessions FROM ga4_metrics "
                "WHERE date BETWEEN ? AND ?",
                (start_date, end_date),
            ).fetchone()
            available = con.execute("SELECT 1 FROM ga4_metrics LIMIT 1").fetchone() is not None
        return {"sessions": int(row["sessions"]) if row else 0, "available": available}
    except sqlite3.OperationalError:
        return {"sessions": 0, "available": False}


def get_ga4_event_step_totals(
    db_path: Path, start_date: str, end_date: str, event_names: list[str]
) -> dict[str, dict[str, Any]]:
    """Per-event totals from ga4_events, each with its own 'ever ingested' flag.

    Returns {event_name: {"count": int, "available": bool}} for every name in
    `event_names`. Distinct GA4 events can be enabled/backfilled at different
    times, so availability is checked per event_name (not per-table) -- e.g.
    'cta_click_convert' having zero rows ever must not be masked by 'begin_checkout'
    already having data.
    """
    result: dict[str, dict[str, Any]] = {
        name: {"count": 0, "available": False} for name in event_names
    }
    if not event_names:
        return result
    try:
        with _conn(db_path) as con:
            placeholders = ",".join("?" * len(event_names))
            rows = con.execute(
                f"""
                SELECT event_name, COALESCE(SUM(event_count), 0) AS total
                FROM ga4_events
                WHERE event_name IN ({placeholders})
                  AND date BETWEEN ? AND ?
                GROUP BY event_name
                """,
                (*event_names, start_date, end_date),
            ).fetchall()
            counts = {r["event_name"]: int(r["total"]) for r in rows}
            ever_rows = con.execute(
                f"SELECT DISTINCT event_name FROM ga4_events WHERE event_name IN ({placeholders})",
                tuple(event_names),
            ).fetchall()
            ever = {r["event_name"] for r in ever_rows}
        for name in event_names:
            result[name] = {"count": counts.get(name, 0), "available": name in ever}
        return result
    except sqlite3.OperationalError:
        return result


def get_orders_step(
    db_path: Path, start_date: str, end_date: str, orders_valid_from: str = ""
) -> dict[str, Any]:
    """Orders count for the preorder funnel: Shopify paid orders, falling back
    to the GA4 'purchase' event count when Shopify hasn't been ingested yet.

    `orders_valid_from` (D-06 fix): internal test/pre-launch orders placed before
    a campaign's real launch date pollute the funnel. When non-empty, adds an
    `order_date >= orders_valid_from` filter -- a query-time exclusion only, no
    rows are deleted. Empty string (default) = no filtering, backward compatible.

    Returns {"count": int, "available": bool,
             "source": "shopify_orders" | "ga4_events" | None}.
    `source` tells the caller which caption to render ("falling back to GA4
    purchase event count" per the funnel-v3 spec).
    """
    valid_from_clause = " AND order_date >= ?" if orders_valid_from else ""
    try:
        with _conn(db_path) as con:
            shopify_ingested = con.execute(
                "SELECT 1 FROM shopify_orders LIMIT 1"
            ).fetchone() is not None
            if shopify_ingested:
                params: list[str] = [start_date, end_date]
                if orders_valid_from:
                    params.append(orders_valid_from)
                row = con.execute(
                    "SELECT COUNT(*) AS n FROM shopify_orders "
                    "WHERE financial_status = 'paid' AND order_date BETWEEN ? AND ?"
                    + valid_from_clause,
                    params,
                ).fetchone()
                return {
                    "count": int(row["n"]) if row else 0,
                    "available": True,
                    "source": "shopify_orders",
                }
    except sqlite3.OperationalError:
        pass

    ga4_purchase = get_ga4_event_step_totals(db_path, start_date, end_date, ["purchase"])["purchase"]
    if ga4_purchase["available"]:
        return {"count": ga4_purchase["count"], "available": True, "source": "ga4_events"}
    return {"count": 0, "available": False, "source": None}


def get_preorder_funnel_steps(
    db_path: Path, start_date: str, end_date: str, orders_valid_from: str = ""
) -> list[dict[str, Any]]:
    """Assemble the full preorder funnel with step-conversion %.

    Order: Impressions -> Clicks -> Landing-Page Views -> GA4 Sessions ->
    CTA Clicks -> Add to Cart -> Begin Checkout -> Orders.

    NOTE (Initiate Checkout rename, 2026-07-22): this "Begin Checkout" step is
    the GA4-native `begin_checkout` event (ga4_events, via
    get_ga4_event_step_totals) -- a genuinely different data source from the
    Meta `meta_begin_checkout` field that was renamed to "Initiate Checkout"
    display-wide. Deliberately NOT renamed here: GA4's own event is literally
    named "begin_checkout", so labeling it "Initiate Checkout" (Meta's
    terminology) would misattribute the data source.

    Each step: {"label", "value" (int, None when unavailable), "available",
    "conversion_pct" (value / previous *available* step's value * 100, or
    None for the first available step or when the previous value was 0), "note"}.
    Unavailable steps are skipped when locating "previous" so a later
    available step still gets a meaningful conversion rate.

    `orders_valid_from` (D-06): forwarded to get_orders_step to exclude
    pre-launch/test Shopify orders from the "Orders" step. See its docstring.
    """
    meta = get_meta_funnel_summary(db_path, start_date, end_date)
    ga4_sessions = get_ga4_sessions_summary(db_path, start_date, end_date)
    events = get_ga4_event_step_totals(
        db_path, start_date, end_date, ["cta_click_convert", "add_to_cart", "begin_checkout"]
    )
    orders = get_orders_step(db_path, start_date, end_date, orders_valid_from)

    orders_note: str | None = None
    if orders["source"] == "shopify_orders":
        orders_note = "Source: Shopify orders (financial_status = 'paid')"
    elif orders["source"] == "ga4_events":
        orders_note = "Source: GA4 purchase events (Shopify orders not yet ingested)"

    # `source` is the badge code the UI shows per row (see components.SOURCE_BADGES).
    # It is derived here, next to the query that actually produces each number, so
    # the badge cannot drift from the table the value came from. Note Add to Cart
    # and Begin Checkout are GA4 events (ga4_events), NOT Shopify-side counts --
    # labelling them "Shopify" would misattribute the source.
    orders_source = "Shop" if orders["source"] == "shopify_orders" else "G"

    steps: list[dict[str, Any]] = [
        {"label": "Impressions", "value": meta["impressions"], "source": "M",
         "available": meta["available"], "note": None},
        {"label": "Clicks", "value": meta["clicks"], "source": "M",
         "available": meta["available"], "note": None},
        {"label": "Landing-Page Views", "value": meta["landing_page_views"], "source": "M",
         "available": meta["lpv_available"], "note": None},
        {"label": "GA4 Sessions", "value": ga4_sessions["sessions"], "source": "G",
         "available": ga4_sessions["available"], "note": None},
        {"label": "CTA Clicks (convert)", "value": events["cta_click_convert"]["count"],
         "source": "G",
         "available": events["cta_click_convert"]["available"], "note": None},
        {"label": "Add to Cart", "value": events["add_to_cart"]["count"], "source": "G",
         "available": events["add_to_cart"]["available"], "note": None},
        {"label": "Begin Checkout", "value": events["begin_checkout"]["count"], "source": "G",
         "available": events["begin_checkout"]["available"], "note": None},
        {"label": "Orders", "value": orders["count"], "source": orders_source,
         "available": orders["available"], "note": orders_note},
    ]

    prev_value: int | None = None
    for step in steps:
        if not step["available"]:
            step["value"] = None
            step["conversion_pct"] = None
            continue
        if prev_value is not None and prev_value > 0:
            step["conversion_pct"] = round(step["value"] * 100.0 / prev_value, 1)
        else:
            step["conversion_pct"] = None
        prev_value = step["value"]

    return steps


def get_segment_mini_funnels(
    db_path: Path,
    start_date: str,
    end_date: str,
    orders_valid_from: str = "",
    canonical_slugs: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Per-lp_slug mini funnel: page views -> add-to-cart -> begin-checkout -> orders.

    'sessions' here is GA4 page_view_lp *event* volume from ga4_events, not a
    true GA4 session count -- ga4_metrics carries no lp_slug dimension, so
    page_view_lp is the closest per-landing-page top-of-funnel proxy available
    (callers should label the axis accordingly, not as raw "GA4 Sessions").
    Orders are Shopify paid orders joined on shopify_orders.lp_slug.
    Returns [] when neither source has any lp_slug-tagged rows in range.

    `orders_valid_from` (D-06): when non-empty, excludes shopify_orders rows with
    order_date before this cutoff (internal test/pre-launch orders) from the
    per-segment "orders" count -- query-time filter only, no rows deleted.

    `canonical_slugs` (segment slug cleanup, 2026-07-22): raw lp_slug values
    include a long tail of junk from legacy traffic -- old display-name-style
    slugs ('6A Nostalgia Bridge'), '(not set)', and near-duplicates of the
    current slugs (plain 'big-feelings' predates canonical
    'big-feelings-type'). When given (typically QUIZ_LP_SLUGS +
    PREORDER_LP_SLUGS from src.config), any lp_slug NOT in this list is
    aggregated into a single trailing "(other)" row instead of appearing
    individually. Canonical rows are still sorted by sessions desc among
    themselves; "(other)" is always last regardless of its own session count.
    When `canonical_slugs` is None (default), behavior is unchanged from
    before this bucketing was added -- every lp_slug appears individually,
    sorted by sessions desc.
    """
    events_by_slug: dict[str, dict[str, int]] = {}
    try:
        with _conn(db_path) as con:
            rows = con.execute(
                """
                SELECT lp_slug,
                       SUM(CASE WHEN event_name = 'page_view_lp' THEN event_count ELSE 0 END)
                           AS sessions,
                       SUM(CASE WHEN event_name = 'add_to_cart' THEN event_count ELSE 0 END)
                           AS add_to_cart,
                       SUM(CASE WHEN event_name = 'begin_checkout' THEN event_count ELSE 0 END)
                           AS begin_checkout
                FROM ga4_events
                WHERE lp_slug != '' AND date BETWEEN ? AND ?
                GROUP BY lp_slug
                """,
                (start_date, end_date),
            ).fetchall()
        for r in rows:
            events_by_slug[r["lp_slug"]] = {
                "sessions": int(r["sessions"] or 0),
                "add_to_cart": int(r["add_to_cart"] or 0),
                "begin_checkout": int(r["begin_checkout"] or 0),
            }
    except sqlite3.OperationalError:
        pass

    orders_by_slug: dict[str, int] = {}
    valid_from_clause = " AND order_date >= ?" if orders_valid_from else ""
    try:
        with _conn(db_path) as con:
            params: list[str] = [start_date, end_date]
            if orders_valid_from:
                params.append(orders_valid_from)
            rows = con.execute(
                """
                SELECT lp_slug, COUNT(*) AS n
                FROM shopify_orders
                WHERE lp_slug != '' AND financial_status = 'paid'
                  AND order_date BETWEEN ? AND ?
                """
                + valid_from_clause
                + """
                GROUP BY lp_slug
                """,
                params,
            ).fetchall()
        orders_by_slug = {r["lp_slug"]: int(r["n"]) for r in rows}
    except sqlite3.OperationalError:
        pass

    slugs = sorted(set(events_by_slug) | set(orders_by_slug))
    results: list[dict[str, Any]] = []
    for slug in slugs:
        e = events_by_slug.get(slug, {"sessions": 0, "add_to_cart": 0, "begin_checkout": 0})
        results.append({
            "lp_slug": slug,
            "sessions": e["sessions"],
            "add_to_cart": e["add_to_cart"],
            "begin_checkout": e["begin_checkout"],
            "orders": orders_by_slug.get(slug, 0),
        })

    if canonical_slugs is not None:
        canonical_set = set(canonical_slugs)
        canonical_rows = [r for r in results if r["lp_slug"] in canonical_set]
        other_rows = [r for r in results if r["lp_slug"] not in canonical_set]
        canonical_rows.sort(key=lambda r: r["sessions"], reverse=True)
        if not other_rows:
            return canonical_rows
        other_bucket = {
            "lp_slug": "(other)",
            "sessions": sum(r["sessions"] for r in other_rows),
            "add_to_cart": sum(r["add_to_cart"] for r in other_rows),
            "begin_checkout": sum(r["begin_checkout"] for r in other_rows),
            "orders": sum(r["orders"] for r in other_rows),
        }
        return canonical_rows + [other_bucket]

    results.sort(key=lambda r: r["sessions"], reverse=True)
    return results


def get_total_sessions_daily(db_path: Path, start_date: str, end_date: str) -> list[dict[str, Any]]:
    """Daily TOTAL GA4 sessions -- prefers ga4_daily_totals, falls back to summing
    ga4_landing_pages -- NOT ga4_metrics.

    D-11 fix: _fetch_campaign_metrics_sync's dimension_filter EXCLUDES
    campaign_utm = '(not set)' rows entirely (see that function's not_expression
    filter), so SUM(sessions) over ga4_metrics only ever reflects
    campaign-*attributed* sessions -- every "GA4 sessions" figure derived from it
    (get_ga4_sessions_summary, get_click_session_gap's old `ga4_sessions` field,
    get_click_session_ratio, get_tracking_gap_days) silently undercounts real GA4
    traffic by however much sits in '(not set)' (verified live: ~650 attributed vs
    ~1,800 real sessions for 2026-07-15..21, with 939 sessions in '(not set)').

    Session multi-counting fix (2026-07-22): ga4_landing_pages used to be built by
    a two-pass fetch whose second pass grouped sessions by pagePathPlusQueryString
    and multi-counted them (once per page viewed per session) -- see
    src/ga4/client.py _fetch_landing_page_metrics_sync's docstring. ga4_daily_totals
    is fed by a dimensions=[date]-only report with no landing-page grouping at all,
    so it cannot exhibit that failure mode and is the preferred source. Falls back
    to summing ga4_landing_pages when ga4_daily_totals is empty or missing (older
    DB not yet migrated / re-backfilled) so callers keep working during rollout.

    Returns [] on missing table / no data (graceful degradation, matching
    get_sessions_daily's convention).
    """
    try:
        with _conn(db_path) as con:
            any_totals_row = con.execute("SELECT 1 FROM ga4_daily_totals LIMIT 1").fetchone()
            if any_totals_row is not None:
                rows = con.execute(
                    "SELECT date, sessions FROM ga4_daily_totals "
                    "WHERE date BETWEEN ? AND ? ORDER BY date",
                    (start_date, end_date),
                ).fetchall()
                return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        pass  # ga4_daily_totals missing (pre-migration DB) -- fall through below

    sql = """
        SELECT date, COALESCE(SUM(sessions), 0) AS sessions
        FROM ga4_landing_pages
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


def get_total_sessions_summary(db_path: Path, start_date: str, end_date: str) -> dict[str, Any]:
    """Period-total GA4 sessions (all campaigns incl. '(not set)') + availability flag.

    Thin wrapper around get_total_sessions_daily for callers (get_click_session_gap,
    Tracking Health's click->session chip) that need one number + an "ever ingested"
    flag rather than a daily series. Prefers ga4_daily_totals (see
    get_total_sessions_daily's docstring for the session multi-counting fix this
    resolves), falling back to summing ga4_landing_pages when ga4_daily_totals is
    empty/missing. `available` reflects whether the source table actually used
    has ever had ANY row (independent of this specific date range), matching the
    convention used by get_ga4_sessions_summary / get_meta_funnel_summary.
    """
    try:
        with _conn(db_path) as con:
            any_totals_row = con.execute("SELECT 1 FROM ga4_daily_totals LIMIT 1").fetchone()
            if any_totals_row is not None:
                row = con.execute(
                    "SELECT COALESCE(SUM(sessions), 0) AS sessions FROM ga4_daily_totals "
                    "WHERE date BETWEEN ? AND ?",
                    (start_date, end_date),
                ).fetchone()
                return {"sessions": int(row["sessions"]) if row else 0, "available": True}
    except sqlite3.OperationalError:
        pass  # ga4_daily_totals missing (pre-migration DB) -- fall through below

    try:
        with _conn(db_path) as con:
            row = con.execute(
                "SELECT COALESCE(SUM(sessions), 0) AS sessions FROM ga4_landing_pages "
                "WHERE date BETWEEN ? AND ?",
                (start_date, end_date),
            ).fetchone()
            available = con.execute(
                "SELECT 1 FROM ga4_landing_pages LIMIT 1"
            ).fetchone() is not None
        return {"sessions": int(row["sessions"]) if row else 0, "available": available}
    except sqlite3.OperationalError:
        return {"sessions": 0, "available": False}


def get_click_session_gap(db_path: Path, start_date: str, end_date: str) -> dict[str, Any]:
    """4-step decomposition: Meta Clicks -> Meta LPV -> GA4 sessions (all) ->
    campaign-attributed GA4 sessions.

    D-11 fix: the click/LPV -> GA4-session gap used to conflate two very different
    failure modes into one number computed against campaign-*attributed* sessions
    only (ga4_metrics excludes '(not set)' rows -- see get_total_sessions_daily's
    docstring). This function now separates them:

      - capture_gap_pct      = 1 - ga4_sessions_all / meta_lpv
        "Did the visit get tracked by GA4 at all?" -- consent-denied visitors and
        tag/transport failures never fire a GA4 session, but Meta still counted
        the LPV. Band: <=30% green / 30-50% amber / >50% red.

      - attribution_gap_pct  = 1 - ga4_sessions_attributed / ga4_sessions_all
        "Of the sessions GA4 DID track, how many could it tie to a campaign?"
        Driven by consent-denied sessions that still fire a bare pageview without
        a campaign-carrying analytics hit, plus genuinely untagged/organic-looking
        traffic. Band: <=40% green / 40-70% amber / >70% red.

    Legacy fields (`gap_clicks_pct`, `gap_lpv_pct`, `ga4_sessions`) are kept,
    unchanged, for backward compatibility with existing callers/tests -- they are
    computed against campaign-attributed sessions only, exactly as before. New
    callers should prefer `ga4_sessions_all` / `ga4_sessions_attributed` /
    `capture_gap_pct` / `attribution_gap_pct`, which correctly separate capture
    loss from attribution loss instead of blending them into one gap.
    """
    meta = get_meta_funnel_summary(db_path, start_date, end_date)
    ga4 = get_ga4_sessions_summary(db_path, start_date, end_date)
    ga4_all = get_total_sessions_summary(db_path, start_date, end_date)

    sessions = ga4["sessions"] if ga4["available"] else None
    all_sessions = ga4_all["sessions"] if ga4_all["available"] else None
    clicks = meta["clicks"] if meta["available"] else None
    lpv = meta["landing_page_views"] if meta["lpv_available"] else None

    gap_clicks_pct: float | None = None
    if clicks and sessions is not None and clicks > 0:
        gap_clicks_pct = round((1 - sessions / clicks) * 100, 1)

    gap_lpv_pct: float | None = None
    if lpv and sessions is not None and lpv > 0:
        gap_lpv_pct = round((1 - sessions / lpv) * 100, 1)

    capture_gap_pct: float | None = None
    if lpv and all_sessions is not None and lpv > 0:
        capture_gap_pct = round((1 - all_sessions / lpv) * 100, 1)

    attribution_gap_pct: float | None = None
    if all_sessions and sessions is not None and all_sessions > 0:
        attribution_gap_pct = round((1 - sessions / all_sessions) * 100, 1)

    return {
        "meta_clicks": clicks,
        "meta_lpv": lpv,
        "ga4_sessions": sessions,
        "ga4_sessions_attributed": sessions,
        "ga4_sessions_all": all_sessions,
        "gap_clicks_pct": gap_clicks_pct,
        "gap_lpv_pct": gap_lpv_pct,
        "capture_gap_pct": capture_gap_pct,
        "attribution_gap_pct": attribution_gap_pct,
    }


_GAP_BAND_GREEN_MAX = 20.0
_GAP_BAND_AMBER_MAX = 30.0


def click_session_gap_band(gap_pct: float | None) -> str:
    """Classify a click/LPV -> GA4-session gap % into a trust-signal band.

    Kept for backward compatibility with the legacy gap_clicks_pct/gap_lpv_pct
    fields -- new code computing the capture/attribution decomposition should use
    capture_gap_band / attribution_gap_band instead, which have their own,
    intentionally different thresholds (see get_click_session_gap's docstring).

    gap <= 20%        -> "green"  (normal -- consent + platform counting)
    20% < gap <= 30%   -> "amber"  (watch)
    gap > 30%          -> "red"    (investigate: consent rate, tag latency, server 503s)
    gap is None        -> "gray"   (no data)
    """
    if gap_pct is None:
        return "gray"
    if gap_pct <= _GAP_BAND_GREEN_MAX:
        return "green"
    if gap_pct <= _GAP_BAND_AMBER_MAX:
        return "amber"
    return "red"


_CAPTURE_GAP_GREEN_MAX = 30.0
_CAPTURE_GAP_AMBER_MAX = 50.0


def capture_gap_band(gap_pct: float | None) -> str:
    """Classify the LPV -> all-GA4-sessions "capture gap" % (consent/tracking loss).

    gap <= 30%         -> "green"  (normal)
    30% < gap <= 50%    -> "amber"  (watch)
    gap > 50%           -> "red"    (investigate consent rate, tag load, transport failures)
    gap is None         -> "gray"   (no data)
    """
    if gap_pct is None:
        return "gray"
    if gap_pct <= _CAPTURE_GAP_GREEN_MAX:
        return "green"
    if gap_pct <= _CAPTURE_GAP_AMBER_MAX:
        return "amber"
    return "red"


_ATTRIBUTION_GAP_GREEN_MAX = 40.0
_ATTRIBUTION_GAP_AMBER_MAX = 70.0


def attribution_gap_band(gap_pct: float | None) -> str:
    """Classify the all-sessions -> campaign-attributed-sessions "attribution gap" %.

    Driven by consent-denied sessions (no campaign-carrying hit fires) plus
    genuinely untagged/organic-looking traffic -- NOT the same failure mode as the
    capture gap, hence a separate, wider band (some attribution loss is normal).

    gap <= 40%          -> "green"  (normal)
    40% < gap <= 70%     -> "amber"  (watch)
    gap > 70%            -> "red"    (investigate utm tagging discipline, consent rate)
    gap is None          -> "gray"   (no data)
    """
    if gap_pct is None:
        return "gray"
    if gap_pct <= _ATTRIBUTION_GAP_GREEN_MAX:
        return "green"
    if gap_pct <= _ATTRIBUTION_GAP_AMBER_MAX:
        return "amber"
    return "red"


def get_ga4_not_set_share(db_path: Path, start_date: str, end_date: str) -> dict[str, Any]:
    """% of checkout-adjacent GA4 event volume (add_to_cart + begin_checkout +
    purchase) whose campaign_utm is '(not set)' or '' -- events GA4 could not
    tie back to a campaign (utm forwarding / tagging gaps).

    Weighted by event_count (volume), not by number of grouped rows.
    Returns {"share_pct", "not_set_count", "total_count", "available"}.
    available is False (share_pct None) when there is no add_to_cart /
    begin_checkout / purchase volume at all in range.
    """
    events = ("add_to_cart", "begin_checkout", "purchase")
    try:
        with _conn(db_path) as con:
            row = con.execute(
                """
                SELECT
                    COALESCE(SUM(event_count), 0) AS total,
                    COALESCE(SUM(CASE WHEN campaign_utm IN (?, ?)
                                       THEN event_count ELSE 0 END), 0) AS not_set
                FROM ga4_events
                WHERE event_name IN (?, ?, ?)
                  AND date BETWEEN ? AND ?
                """,
                (*NOT_SET_CAMPAIGN_VALUES, *events, start_date, end_date),
            ).fetchone()
    except sqlite3.OperationalError:
        return {"share_pct": None, "not_set_count": 0, "total_count": 0, "available": False}

    total = int(row["total"]) if row else 0
    not_set = int(row["not_set"]) if row else 0
    if total <= 0:
        return {"share_pct": None, "not_set_count": 0, "total_count": 0, "available": False}
    share = round(not_set * 100.0 / total, 1)
    return {"share_pct": share, "not_set_count": not_set, "total_count": total, "available": True}


_NOT_SET_BAND_GREEN_MAX = 30.0
_NOT_SET_BAND_AMBER_MAX = 60.0


def not_set_share_band(share_pct: float | None) -> str:
    """green <= 30% · amber 30-60% · red > 60% · gray when no data."""
    if share_pct is None:
        return "gray"
    if share_pct <= _NOT_SET_BAND_GREEN_MAX:
        return "green"
    if share_pct <= _NOT_SET_BAND_AMBER_MAX:
        return "amber"
    return "red"


def get_quiz_funnel(
    db_path: Path, start_date: str, end_date: str, lp_slugs: list[str]
) -> dict[str, dict[str, Any]]:
    """page_view_lp -> quiz_complete -> lead_submit, restricted to `lp_slugs`.

    Returns {"page_view_lp": {"count","available"}, "quiz_complete": {...},
    "lead_submit": {...}}. Availability reflects whether that event_name has
    ever been ingested for ANY of the given slugs.
    """
    events = ("page_view_lp", "quiz_complete", "lead_submit")
    result: dict[str, dict[str, Any]] = {name: {"count": 0, "available": False} for name in events}
    if not lp_slugs:
        return result
    try:
        with _conn(db_path) as con:
            ev_ph = ",".join("?" * len(events))
            slug_ph = ",".join("?" * len(lp_slugs))
            rows = con.execute(
                f"""
                SELECT event_name, COALESCE(SUM(event_count), 0) AS total
                FROM ga4_events
                WHERE event_name IN ({ev_ph})
                  AND lp_slug IN ({slug_ph})
                  AND date BETWEEN ? AND ?
                GROUP BY event_name
                """,
                (*events, *lp_slugs, start_date, end_date),
            ).fetchall()
            counts = {r["event_name"]: int(r["total"]) for r in rows}
            ever_rows = con.execute(
                f"SELECT DISTINCT event_name FROM ga4_events "
                f"WHERE event_name IN ({ev_ph}) AND lp_slug IN ({slug_ph})",
                (*events, *lp_slugs),
            ).fetchall()
            ever = {r["event_name"] for r in ever_rows}
        for name in events:
            result[name] = {"count": counts.get(name, 0), "available": name in ever}
        return result
    except sqlite3.OperationalError:
        return result


def get_quiz_cost_per_lead(
    db_path: Path, start_date: str, end_date: str, lp_slugs: list[str]
) -> dict[str, Any]:
    """Cost-per-lead for the quiz funnel: quiz-campaign Meta spend / lead_submit count.

    Campaign scope: campaign name LIKE '%LEADS%' -- the naming convention this
    codebase already uses to tag lead-gen campaigns (see
    src/dashboard/Overview.py `_shorten_campaign`, which strips a
    'Nowa | SALES/LEADS | X.X ' prefix). This is a caption-worthy
    approximation: LEADS-named campaigns may include non-quiz lead campaigns,
    so CPL here is directional, not an exact quiz-only figure.

    Returns {"cpl": float | None, "spend": float, "lead_submit": int,
    "leads_campaign_count": int}. cpl is None when spend or lead_submit is 0.
    """
    try:
        with _conn(db_path) as con:
            row = con.execute(
                """
                SELECT COALESCE(SUM(m.spend), 0)     AS spend,
                       COUNT(DISTINCT m.campaign_id)  AS n_campaigns
                FROM ad_metrics m
                JOIN campaigns c ON c.id = m.campaign_id
                WHERE m.ad_set_id = '' AND m.ad_id = ''
                  AND c.name LIKE '%LEADS%'
                  AND m.date BETWEEN ? AND ?
                """,
                (start_date, end_date),
            ).fetchone()
        spend = float(row["spend"]) if row else 0.0
        n_campaigns = int(row["n_campaigns"]) if row else 0
    except sqlite3.OperationalError:
        spend, n_campaigns = 0.0, 0

    quiz = get_quiz_funnel(db_path, start_date, end_date, lp_slugs)
    lead_submit = quiz["lead_submit"]["count"] if quiz["lead_submit"]["available"] else 0

    cpl = round(spend / lead_submit, 2) if spend > 0 and lead_submit > 0 else None
    return {
        "cpl": cpl,
        "spend": spend,
        "lead_submit": lead_submit,
        "leads_campaign_count": n_campaigns,
    }
# Phase C: Tracking Health page queries
# ---------------------------------------------------------------------------

def get_click_session_ratio(db_path: Path, start_date: str, end_date: str) -> float | None:
    """Overall click->session ratio %% over a window: total GA4 sessions (ALL
    traffic) / total Meta clicks -- the Tracking Health "capture rate" chip.

    D-11 fix: previously sourced sessions from ga4_metrics, whose fetch EXCLUDES
    campaign_utm = '(not set)' rows entirely (see _fetch_campaign_metrics_sync's
    dimension_filter) -- undercounting true GA4 session volume and making this
    chip conflate genuine tracking-capture failures with campaign-attribution
    gaps (utm tagging). ga4_landing_pages has no such campaign filter, so this
    now measures the thing the chip's label claims: did the visit get captured
    by GA4 at all, regardless of whether it could be tied back to a campaign.

    Uses period TOTALS (not an average of daily ratios) — more statistically sound
    when daily click volume is uneven. Returns None when there were zero clicks in
    the window (ratio undefined) or the underlying tables don't exist yet.
    """
    sql = """
        SELECT
            COALESCE((SELECT SUM(clicks) FROM ad_metrics
                      WHERE ad_set_id = '' AND ad_id = '' AND date BETWEEN ? AND ?), 0) AS total_clicks,
            COALESCE((SELECT SUM(sessions) FROM ga4_landing_pages
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
    # Property-wide GA4 purchase event count — the honest comparator, and the
    # same source the Overview reconciliation triangle uses. The campaign-
    # attributed ga4_metrics figure undercounts (the server-side purchase event
    # loses utm_campaign for most orders), which made this chip disagree with
    # the triangle for the same window (GA4 1 here vs 5 there).
    ga4_total = int(
        get_ga4_event_step_totals(db_path, start_date, end_date, ["purchase"])
        ["purchase"].get("count") or 0
    )
    ga4_attributed = int(
        get_ga4_kpi(db_path, start_date, end_date).get("total_purchases", 0) or 0
    )
    return {
        "meta_purchases": meta_total,
        "ga4_purchases": ga4_total,
        "ga4_purchases_attributed": ga4_attributed,
    }


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
    """%% of `event_name` rows with no campaign attribution in a window.

    D-07 fix: GA4 stores the literal string '(not set)' in campaign_utm for
    unattributed sessions/events -- matching only campaign_utm = '' (as this
    function used to) missed nearly all of that traffic, so Tracking Health showed
    0% "(not set)" share for the same data the Funnel page's get_ga4_not_set_share
    (which already matched both values) correctly reported as ~81%. Uses the shared
    NOT_SET_CAMPAIGN_VALUES constant so the two functions can't drift apart again --
    this function still scopes to a single event_name (per-event chip), while
    get_ga4_not_set_share aggregates 3 checkout events together; only the
    "(not set)" string-matching rule is now guaranteed identical between them.

    Returns None when there's no data at all for the event in this window
    (share is undefined, not zero) or the table doesn't exist yet.
    """
    sql = """
        SELECT
            COALESCE(SUM(CASE WHEN campaign_utm IN (?, ?) THEN event_count ELSE 0 END), 0) AS not_set,
            COALESCE(SUM(event_count), 0) AS total
        FROM ga4_events
        WHERE event_name = ? AND date BETWEEN ? AND ?
    """
    try:
        with _conn(db_path) as con:
            row = con.execute(
                sql, (*NOT_SET_CAMPAIGN_VALUES, event_name, start_date, end_date)
            ).fetchone()
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


# ---------------------------------------------------------------------------
# Email leads (Preorder Leads Dashboard sheet) — Overview NSM strip
# ---------------------------------------------------------------------------
def get_email_leads_summary(
    db_path: Path, start_date: str, end_date: str
) -> dict[str, Any]:
    """Deduped email-lead counts for a window, sliced by deposit status + channel.

    Counts unique EMAILS whose first-seen date falls in the window, not sheet rows:
    the underlying table is one row per address (see migration 016).

    Scope matches the sheet's own summary tab -- internal/test addresses excluded
    (is_internal = 0) and restricted to the deduped Leads tab (in_dedup_tab = 1),
    which is what reproduces the 156 that tab reports. Addresses seen only in the
    channel tabs are returned separately as ``channel_only`` so the difference is
    visible instead of silently inflating or deflating the headline.

    Returns zeros / empty on a missing table (pre-migration DB, or the sheet not
    configured) -- same graceful-degradation contract as the Shopify helpers.

    ``by_status`` keys are the sheet's raw values, deliberately not normalised:
    the live sheet contains both ``pending`` and ``payment-pending`` and
    collapsing them here would hide a real data-entry inconsistency.

    ``from_*`` counts are NOT mutually exclusive and do not sum to ``total`` --
    one address can arrive via a quiz and later via the exit-intent popup.
    """
    base = "FROM email_leads WHERE is_internal = 0 AND lead_date BETWEEN ? AND ?"
    params = (start_date, end_date)
    empty = {
        "total": 0,
        "by_status": {},
        "from_quiz": 0,
        "from_preorder_started": 0,
        "from_exit_intent": 0,
        "channel_only": 0,
        "internal_excluded": 0,
    }
    try:
        with _conn(db_path) as con:
            row = con.execute(
                "SELECT "
                "  COUNT(*) AS total, "
                "  COALESCE(SUM(from_quiz), 0) AS from_quiz, "
                "  COALESCE(SUM(from_preorder_started), 0) AS from_preorder_started, "
                "  COALESCE(SUM(from_exit_intent), 0) AS from_exit_intent "
                + base + " AND in_dedup_tab = 1",
                params,
            ).fetchone()
            status_rows = con.execute(
                "SELECT COALESCE(NULLIF(TRIM(deposit_status), ''), '(not set)') AS status, "
                "COUNT(*) AS n "
                + base + " AND in_dedup_tab = 1 GROUP BY status ORDER BY n DESC",
                params,
            ).fetchall()
            extra = con.execute(
                "SELECT COUNT(*) AS n " + base + " AND in_dedup_tab = 0", params
            ).fetchone()
            internal = con.execute(
                "SELECT COUNT(*) AS n FROM email_leads "
                "WHERE is_internal = 1 AND lead_date BETWEEN ? AND ?",
                params,
            ).fetchone()
    except sqlite3.OperationalError:
        return empty

    if row is None:
        return empty
    return {
        "total": int(row["total"] or 0),
        "by_status": {r["status"]: int(r["n"]) for r in status_rows},
        "from_quiz": int(row["from_quiz"] or 0),
        "from_preorder_started": int(row["from_preorder_started"] or 0),
        "from_exit_intent": int(row["from_exit_intent"] or 0),
        "channel_only": int(extra["n"] or 0) if extra else 0,
        "internal_excluded": int(internal["n"] or 0) if internal else 0,
    }


def get_internal_lead_emails(
    db_path: Path, start_date: str, end_date: str
) -> list[dict[str, Any]]:
    """The internal/test addresses excluded from the reported lead numbers.

    Exists so the exclusion is auditable from the dashboard instead of having to
    be re-derived by hand: if a new staff or test address starts landing in the
    sheet and LEADS_INTERNAL_EMAIL_PATTERNS has not caught it yet, the way to
    notice is to see what the filter *did* catch and spot what is missing.

    Returns [] on a missing table, same graceful-degradation contract as the rest
    of this module.
    """
    sql = (
        "SELECT email, lead_date, deposit_status "
        "FROM email_leads WHERE is_internal = 1 AND lead_date BETWEEN ? AND ? "
        "ORDER BY lead_date, email"
    )
    try:
        with _conn(db_path) as con:
            rows = con.execute(sql, (start_date, end_date)).fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Landing-page table (Funnel page) — Meta delivery joined to on-site behaviour
# ---------------------------------------------------------------------------
# The offer page's own slug. Rates that describe "traffic sent onward to the offer
# page" are undefined on this row, since it IS the destination.
PREORDER_OFFER_SLUG = "preorder"


def lp_slug_from_url(url: str) -> str:
    """Normalise an ad's destination URL to the lp_slug GA4 reports.

    ``https://nowaplanet.com/for/routine/?utm_source=meta...``  -> ``routine``
    ``https://nowaplanet.com/``                                 -> ``home``
    ``https://nowaplanet.com/resources/quiz/screen-kid``         -> ``screen-kid``

    Returns "" for anything unrecognised so the caller can bucket it as unmapped
    rather than inventing an attribution.
    """
    raw = str(url or "").strip()
    if not raw:
        return ""
    path = raw.split("?", 1)[0].split("#", 1)[0]
    # Drop scheme + host without needing urlparse's full machinery.
    if "//" in path:
        path = path.split("//", 1)[1]
        path = path[path.find("/"):] if "/" in path else "/"
    segments = [s for s in path.split("/") if s]
    if not segments:
        return "home"
    # /for/<slug> and /resources/quiz/<slug> both key on their last segment, which
    # is exactly what GA4's lp_slug dimension carries.
    return segments[-1]


def get_landing_page_table(
    db_path: Path,
    start_date: str,
    end_date: str,
    orders_valid_from: str = "",
    canonical_slugs: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Per-landing-page rows joining Meta ad delivery to GA4 on-site behaviour.

    The Meta side is attributed per landing page through ad_creatives.destination_url
    -- the only link in the schema between an ad and the page it points at. There is
    no ad-set-to-landing-page mapping table, and campaign/ad-set NAMES are not a
    reliable substitute (they are renamed freely, and the current generation dropped
    the format tokens the old parser relied on).

    Spend/clicks/impressions are read from AD-SET-level rows, not ad-level ones,
    and mapped to a landing page via each ad-set's creatives. Ad-level rows look
    like the more natural grain but only exist for the days the ad-level backfill
    has run -- on live data that was 1 day of a 7-day window, 12.8% of spend --
    which would have silently shown a single day's spend beside a full week of GA4
    events. Ad-set rows carry 100% of spend, and every ad-set's creatives resolve
    to exactly one landing page (verified: 0 of 64 ad-sets ambiguous), so nothing
    is lost by aggregating one level up. Ad-sets whose creatives disagree on the
    destination are skipped rather than split on a guess.

    Deliberately excluded: add_to_cart and begin_checkout. Those fire on Shopify's
    domain, so ~90% of them arrive with lp_slug = '(not set)' or empty (verified on
    live data: 159 of 207 add_to_cart, 61 of 78 begin_checkout). A per-landing-page
    split of them would be mostly invented. They stay in the funnel table above,
    where they are only ever shown as a total.

    Each row: {"lp_slug", "spend", "clicks", "impressions", "ctr_pct", "cpm",
    "lp_views", "cta_clicks", "orders", "has_meta"}. Sorted by spend DESC, then
    landing pages with on-site activity but no ad spend of their own (e.g. the
    /preorder offer page, which every other page feeds).

    `canonical_slugs` (segment slug cleanup, 2026-07-22): slugs outside this list
    are folded into a trailing "(other)" row, matching get_segment_mini_funnels.
    Without it, retired slugs from earlier campaign generations (e.g.
    `1a-screen-time`) each get their own row on the strength of a handful of
    stray page-views. None (default) leaves every slug on its own row.

    Returns [] on a missing table -- same graceful-degradation contract as the rest
    of this module.
    """
    rows: dict[str, dict[str, Any]] = {}

    def _slot(slug: str) -> dict[str, Any]:
        if slug not in rows:
            rows[slug] = {
                "lp_slug": slug, "spend": 0.0, "clicks": 0, "impressions": 0,
                "link_clicks": 0, "link_clicks_available": False,
                "lp_views": 0, "cta_clicks": 0, "sessions": 0,
                "preorder_sessions": 0, "orders": 0, "has_meta": False,
            }
        return rows[slug]

    try:
        with _conn(db_path) as con:
            # --- ad-set -> landing page, from the ad-set's own creatives ---
            adset_slugs: dict[str, set[str]] = {}
            for r in con.execute(
                "SELECT adset_id, destination_url FROM ad_creatives "
                "WHERE COALESCE(adset_id, '') != '' "
                "AND COALESCE(destination_url, '') != ''"
            ):
                slug = lp_slug_from_url(r["destination_url"])
                if slug:
                    adset_slugs.setdefault(str(r["adset_id"]), set()).add(slug)
            adset_to_lp = {a: next(iter(s)) for a, s in adset_slugs.items() if len(s) == 1}

            # --- Meta delivery at ad-set grain (full spend coverage) ---
            meta_sql = """
                SELECT ad_set_id,
                       COALESCE(SUM(spend), 0)       AS spend,
                       COALESCE(SUM(clicks), 0)      AS clicks,
                       COALESCE(SUM(impressions), 0) AS impressions,
                       SUM(inline_link_clicks)       AS link_clicks
                FROM ad_metrics
                WHERE date BETWEEN ? AND ? AND ad_set_id != '' AND ad_id = ''
                GROUP BY ad_set_id
            """
            for r in con.execute(meta_sql, (start_date, end_date)):
                slug = adset_to_lp.get(str(r["ad_set_id"]))
                if not slug:
                    continue
                slot = _slot(slug)
                slot["spend"] += float(r["spend"] or 0)
                slot["clicks"] += int(r["clicks"] or 0)
                slot["impressions"] += int(r["impressions"] or 0)
                slot["has_meta"] = True
                # SUM() over an all-NULL column is NULL, which is how a window
                # predating migration 018 is told apart from genuine zero link
                # clicks — the UI shows "—" for the former, "0" for the latter.
                if r["link_clicks"] is not None:
                    slot["link_clicks"] += int(r["link_clicks"])
                    slot["link_clicks_available"] = True

            # --- GA4 sessions per landing page (denominator for the flow rate) ---
            # ga4_landing_pages keys on the full landing path incl. query string,
            # so it is normalised to a slug here the same way ad destinations are.
            try:
                lp_sessions_sql = """
                    SELECT landing_page, COALESCE(SUM(sessions), 0) AS sessions
                    FROM ga4_landing_pages
                    WHERE date BETWEEN ? AND ?
                    GROUP BY landing_page
                """
                for r in con.execute(lp_sessions_sql, (start_date, end_date)):
                    slug = lp_slug_from_url(str(r["landing_page"] or ""))
                    if not slug or slug in NOT_SET_CAMPAIGN_VALUES:
                        continue
                    _slot(slug)["sessions"] += int(r["sessions"] or 0)
            except sqlite3.OperationalError:
                pass

            # --- sessions that went on to view /preorder (migration 018) ---
            try:
                for r in con.execute(
                    "SELECT from_slug, COALESCE(SUM(sessions), 0) AS sessions "
                    "FROM ga4_page_flow WHERE date BETWEEN ? AND ? AND to_slug = ? "
                    "AND from_slug != to_slug GROUP BY from_slug",
                    (start_date, end_date, PREORDER_OFFER_SLUG),
                ):
                    slug = str(r["from_slug"] or "").strip()
                    if not slug or slug in NOT_SET_CAMPAIGN_VALUES:
                        continue
                    _slot(slug)["preorder_sessions"] += int(r["sessions"] or 0)
            except sqlite3.OperationalError:
                pass

            # --- GA4 on-site behaviour per landing page ---
            ga4_sql = """
                SELECT lp_slug, event_name, COALESCE(SUM(event_count), 0) AS n
                FROM ga4_events
                WHERE date BETWEEN ? AND ?
                  AND event_name IN ('page_view_lp', 'cta_click_convert')
                  AND TRIM(COALESCE(lp_slug, '')) != ''
                GROUP BY lp_slug, event_name
            """
            for r in con.execute(ga4_sql, (start_date, end_date)):
                slug = str(r["lp_slug"]).strip()
                if slug in NOT_SET_CAMPAIGN_VALUES:
                    continue
                slot = _slot(slug)
                if r["event_name"] == "page_view_lp":
                    slot["lp_views"] += int(r["n"] or 0)
                else:
                    slot["cta_clicks"] += int(r["n"] or 0)

            # --- Shopify paid orders per landing page ---
            valid_clause = " AND order_date >= ?" if orders_valid_from else ""
            params: list[str] = [start_date, end_date]
            if orders_valid_from:
                params.append(orders_valid_from)
            orders_sql = (
                "SELECT lp_slug, COUNT(*) AS n FROM shopify_orders "
                "WHERE financial_status = 'paid' AND order_date BETWEEN ? AND ?"
                + valid_clause + " GROUP BY lp_slug"
            )
            for r in con.execute(orders_sql, params):
                slug = str(r["lp_slug"] or "").strip()
                if not slug or slug in NOT_SET_CAMPAIGN_VALUES:
                    continue
                _slot(slug)["orders"] += int(r["n"] or 0)
    except sqlite3.OperationalError:
        return []

    out = list(rows.values())

    if canonical_slugs is not None:
        canonical_set = set(canonical_slugs)
        keep = [r for r in out if r["lp_slug"] in canonical_set]
        other = [r for r in out if r["lp_slug"] not in canonical_set]
        if other:
            keep.append({
                "lp_slug": "(other)",
                "spend": sum(r["spend"] for r in other),
                "clicks": sum(r["clicks"] for r in other),
                "impressions": sum(r["impressions"] for r in other),
                "link_clicks": sum(r["link_clicks"] for r in other),
                "link_clicks_available": any(r["link_clicks_available"] for r in other),
                "lp_views": sum(r["lp_views"] for r in other),
                "cta_clicks": sum(r["cta_clicks"] for r in other),
                "sessions": sum(r["sessions"] for r in other),
                "preorder_sessions": sum(r["preorder_sessions"] for r in other),
                "orders": sum(r["orders"] for r in other),
                "has_meta": any(r["has_meta"] for r in other),
            })
        out = keep

    # Rates are derived after bucketing so the "(other)" row gets its own blended
    # rate rather than an average of averages. Each is None when its denominator
    # is zero — a missing rate, not a 0% one.
    for row in out:
        imp = row["impressions"]
        row["ctr_pct"] = round(row["clicks"] * 100.0 / imp, 2) if imp else None
        row["cpm"] = round(row["spend"] * 1000.0 / imp, 2) if imp else None
        row["link_ctr_pct"] = (
            round(row["link_clicks"] * 100.0 / imp, 2)
            if imp and row["link_clicks_available"] else None
        )
        # % CTA click is against LP views (page-level events, both GA4) rather
        # than sessions, so numerator and denominator share one scope.
        row["cta_pct"] = (
            round(row["cta_clicks"] * 100.0 / row["lp_views"], 1)
            if row["lp_views"] else None
        )
        # % onward to /preorder is session-scoped on both sides, and is undefined
        # for the offer page itself: "sessions that landed on /preorder and viewed
        # /preorder" is the whole row, so a rate there measures nothing (it also
        # exceeded 100% on live data, the two GA4 reports counting that page's
        # sessions slightly differently).
        row["preorder_pct"] = (
            round(row["preorder_sessions"] * 100.0 / row["sessions"], 1)
            if row["sessions"] and row["lp_slug"] != PREORDER_OFFER_SLUG else None
        )
        row["order_pct"] = (
            round(row["orders"] * 100.0 / row["lp_views"], 2)
            if row["lp_views"] else None
        )

    # Ad-funded pages first by spend; then pages that only receive internal
    # traffic; "(other)" always last since it is a remainder, not a page.
    out.sort(key=lambda r: (
        r["lp_slug"] == "(other)", not r["has_meta"], -r["spend"], r["lp_slug"],
    ))
    return out


# ---------------------------------------------------------------------------
# Initiate Checkout reconciliation — Meta vs GA4 vs Shopify
# ---------------------------------------------------------------------------
def get_checkout_reconciliation(
    db_path: Path, start_date: str, end_date: str, orders_valid_from: str = ""
) -> dict[str, Any]:
    """The same checkout step counted three ways, never combined.

    - ``meta``: ad_metrics.meta_begin_checkout. Platform pixel, inflated by the
      cart-permalink auto-redirect, so it reads closer to reserve-click intent.
    - ``ga4``: the ga4_events `begin_checkout` event, property-wide.
    - ``shopify``: abandoned checkouts created in the period PLUS paid orders
      created in the period. A FLOOR, not a like-for-like counterpart: Shopify
      only exposes an abandoned checkout once the shopper leaves contact details,
      so anyone who opened checkout and left earlier is invisible to it. Adding
      paid orders back in stops completed checkouts from vanishing (they leave
      the abandoned-checkout endpoint once they convert).

    Returns {"meta", "ga4", "shopify", "shopify_abandoned", "shopify_orders",
    "shopify_available"}. shopify_available is False when the checkouts table is
    missing or empty for the window, so the UI can say "not ingested" instead of
    showing a zero that looks like a real measurement.
    """
    out: dict[str, Any] = {
        "meta": 0, "ga4": 0, "shopify": 0,
        "shopify_abandoned": 0, "shopify_orders": 0, "shopify_available": False,
    }

    out["meta"] = int(get_meta_begin_checkout_total(db_path, start_date, end_date) or 0)
    ga4_steps = get_ga4_event_step_totals(db_path, start_date, end_date, ["begin_checkout"])
    out["ga4"] = int(ga4_steps["begin_checkout"]["count"] or 0)

    paid = get_shopify_paid_summary(db_path, start_date, end_date, orders_valid_from)
    out["shopify_orders"] = int(paid["count"] or 0)

    try:
        with _conn(db_path) as con:
            row = con.execute(
                "SELECT COUNT(*) AS n FROM shopify_checkouts "
                "WHERE checkout_date BETWEEN ? AND ?",
                (start_date, end_date),
            ).fetchone()
            total_rows = con.execute(
                "SELECT COUNT(*) AS n FROM shopify_checkouts"
            ).fetchone()
    except sqlite3.OperationalError:
        return out

    out["shopify_abandoned"] = int(row["n"] or 0) if row else 0
    out["shopify"] = out["shopify_abandoned"] + out["shopify_orders"]
    # "Available" keys off the table having ANY rows, not rows in this window:
    # a genuinely quiet week should read as zero checkouts, not as no data.
    out["shopify_available"] = bool(total_rows and int(total_rows["n"] or 0) > 0)
    return out


def get_email_leads_daily_by_status(
    db_path: Path, start_date: str, end_date: str
) -> list[dict[str, Any]]:
    """Leads per day, split by deposit status, for the Email-leads time series.

    IMPORTANT semantics: email_leads stores one row per address carrying its
    FIRST-SEEN date and its LATEST status — no status history. So a point on the
    'paid' line means "leads first seen that day whose status is paid TODAY", not
    "leads that became paid that day". It is a cohort view, and it is the only
    view this data can support; reading it as status transitions would be wrong.

    Scoped like get_email_leads_summary: internal/test excluded, restricted to the
    deduped Leads tab, so the daily numbers add up to the headline Total leads.

    Returns [{"lead_date", "deposit_status", "count"}], sorted by date then status.
    [] on a missing table.
    """
    sql = (
        "SELECT lead_date, "
        "COALESCE(NULLIF(TRIM(deposit_status), ''), '(not set)') AS deposit_status, "
        "COUNT(*) AS count "
        "FROM email_leads "
        "WHERE is_internal = 0 AND in_dedup_tab = 1 AND lead_date BETWEEN ? AND ? "
        "GROUP BY lead_date, deposit_status "
        "ORDER BY lead_date, deposit_status"
    )
    try:
        with _conn(db_path) as con:
            rows = con.execute(sql, (start_date, end_date)).fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Quiz funnel — quiz page -> its segment LP -> /preorder
# ---------------------------------------------------------------------------
# Each quiz page belongs to one segment, and the mapping is a product fact that
# no table encodes: the quiz page slug and the segment slug simply differ
# ("screen-kid" is the quiz for the "screen-anxious" segment). Verified against
# live GA4 flow data — each quiz page's onward traffic goes to exactly this
# /for/* page — and against the leads sheet, whose QUIZNAME column stores the
# SEGMENT slug, not the quiz page slug.
QUIZ_TO_SEGMENT: list[tuple[str, str]] = [
    ("routine-break", "routine"),
    ("big-feelings-type", "big-feelings"),
    ("screen-kid", "screen-anxious"),
]


def get_quiz_funnel_table(
    db_path: Path,
    start_date: str,
    end_date: str,
    orders_valid_from: str = "",
    quiz_map: list[tuple[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """One row per quiz page, tracing the full path a quiz lead can take.

    quiz page sessions -> sessions that went on to the segment LP -> sessions that
    reached /preorder -> email leads captured for that segment -> paid orders.

    Sessions come from ga4_page_flow, where a (from == to) row is the page's own
    session count. The "-> segment" and "-> /preorder" figures are session counts
    for that same landing page, so all three share one denominator and the
    percentages are honest.

    Leads come from email_leads.quiz_name, which stores the SEGMENT slug — a quiz
    lead is identified by the segment it answered for, not by the quiz page URL.
    Internal/test addresses are excluded, matching every other lead figure.

    Orders are Shopify paid orders whose lp_slug is the SEGMENT — last-touch, so
    a quiz lead who eventually bought from the homepage will not appear here.
    Stated in the UI rather than papered over.

    Returns [] on missing tables (graceful degradation, as elsewhere here).
    """
    pairs = quiz_map if quiz_map is not None else QUIZ_TO_SEGMENT
    out: list[dict[str, Any]] = []

    try:
        with _conn(db_path) as con:
            def _flow(from_slug: str, to_slug: str) -> int:
                row = con.execute(
                    "SELECT COALESCE(SUM(sessions), 0) AS n FROM ga4_page_flow "
                    "WHERE date BETWEEN ? AND ? AND from_slug = ? AND to_slug = ?",
                    (start_date, end_date, from_slug, to_slug),
                ).fetchone()
                return int(row["n"] or 0) if row else 0

            def _leads(segment: str) -> int:
                try:
                    row = con.execute(
                        "SELECT COUNT(*) AS n FROM email_leads "
                        "WHERE is_internal = 0 AND quiz_name = ? "
                        "AND lead_date BETWEEN ? AND ?",
                        (segment, start_date, end_date),
                    ).fetchone()
                except sqlite3.OperationalError:
                    return 0
                return int(row["n"] or 0) if row else 0

            def _orders(segment: str) -> int:
                clause = " AND order_date >= ?" if orders_valid_from else ""
                params: list[str] = [segment, start_date, end_date]
                if orders_valid_from:
                    params.append(orders_valid_from)
                try:
                    row = con.execute(
                        "SELECT COUNT(*) AS n FROM shopify_orders "
                        "WHERE financial_status = 'paid' AND lp_slug = ? "
                        "AND order_date BETWEEN ? AND ?" + clause,
                        params,
                    ).fetchone()
                except sqlite3.OperationalError:
                    return 0
                return int(row["n"] or 0) if row else 0

            for quiz_slug, segment_slug in pairs:
                sessions = _flow(quiz_slug, quiz_slug)
                to_segment = _flow(quiz_slug, segment_slug)
                to_preorder = _flow(quiz_slug, PREORDER_OFFER_SLUG)
                out.append({
                    "quiz_slug": quiz_slug,
                    "segment_slug": segment_slug,
                    "sessions": sessions,
                    "to_segment": to_segment,
                    "to_segment_pct": (
                        round(to_segment * 100.0 / sessions, 1) if sessions else None
                    ),
                    "to_preorder": to_preorder,
                    "to_preorder_pct": (
                        round(to_preorder * 100.0 / sessions, 1) if sessions else None
                    ),
                    "leads": _leads(segment_slug),
                    "orders": _orders(segment_slug),
                })
    except sqlite3.OperationalError:
        return []
    return out


# ---------------------------------------------------------------------------
# Orders page — per-order journey with Meta ad traceback
# ---------------------------------------------------------------------------
def _looks_like_meta_id(value: str) -> bool:
    """Meta ad/ad-set ids are long all-digit strings.

    The length floor keeps a stray "1" from being read as an id. See
    ORDER_UTM_SCHEMES for why utm_content needs this test at all.
    """
    v = str(value or "").strip()
    return v.isdigit() and len(v) >= 10


# Three tagging generations reach Shopify order records, and utm_content means
# something different in each. Verified against the live data rather than assumed:
#
#   1. Numeric ad id   -- utm_content=120247134827400025, utm_term=<adset id>,
#      utm_campaign=nowa-pre-order-img. These ids appear on NO ad destination URL
#      in ad_creatives, so they are not hardcoded in the link: they come from
#      Meta's own ad-level "URL parameters" field using the {{ad.id}} /
#      {{adset.id}} macros, which Meta appends at click time. Only this generation
#      identifies one specific ad.
#   2. Creative code   -- utm_content=HOME-08 / ROUTINE-09 / QUIZ-03, with
#      utm_term=homepage|preorder-lp. This is what every current destination URL
#      in ad_creatives actually carries. The code appears inside ad_name, so it
#      resolves to a CREATIVE, not to one ad: "HOME-04" matches 3 ads (different
#      ad-sets/formats reuse the code).
#   3. Page slug       -- utm_content=home / screen-anxious, no code and no id.
#      The oldest generation; identifies the landing page only.
#
# A single order can mix generations: #1023 carries a page slug in utm_content
# (gen 3) and a numeric ad-set id in utm_term (gen 1), which is exactly why it
# traces to an ad-set but not to an ad.
_CREATIVE_CODE_RE = re.compile(r"^[A-Z]+(?:-[A-Z]+)*-(?:CAR-)?\d{1,3}$", re.IGNORECASE)


def _looks_like_creative_code(value: str) -> bool:
    """True for utm_content values like HOME-08, ROUTINE-09, HOME-CAR-01."""
    return bool(_CREATIVE_CODE_RE.match(str(value or "").strip()))


def _order_number(order_name: str) -> int | None:
    """'#1023' -> 1023. None when the name is not a plain #<number>."""
    digits = "".join(ch for ch in str(order_name or "") if ch.isdigit())
    return int(digits) if digits else None


def get_order_journeys(
    db_path: Path,
    start_date: str = "",
    end_date: str = "",
    min_order_number: int = 0,
) -> list[dict[str, Any]]:
    """Per-order first/last touch, with the Meta ad resolved where traceable.

    Date args are optional: the Orders page shows the whole (small) order history
    by default, because "where did our handful of orders come from" is a
    history question, not a this-week question. Pass both to window it.

    Each row adds, on top of the stored journey columns:
      - ``ad_id`` / ``adset_id``: from utm_content / utm_term when those hold a
        numeric Meta id (see _looks_like_meta_id) — else "".
      - ``ad_name``: resolved from ad_creatives when the ad id is known.
      - ``lp_hint``: utm_content when it is a slug rather than an id, which is how
        pre-24-Jul orders still reveal which landing page tagged them.
      - ``traced``: "ad" when a single ad is identified, "adset" when only the
        ad-set is, "" otherwise. This is what the page counts, rather than
        re-deriving the rule in the UI.

    Returns [] on a missing table (graceful degradation, as elsewhere here).
    """
    where, params = "", []
    if start_date and end_date:
        where = " WHERE order_date BETWEEN ? AND ?"
        params = [start_date, end_date]

    try:
        with _conn(db_path) as con:
            rows = [
                dict(r) for r in con.execute(
                    "SELECT * FROM shopify_order_journey" + where
                    + " ORDER BY order_date DESC, order_name DESC",
                    params,
                )
            ]
            ad_names: dict[str, str] = {}
            try:
                for r in con.execute("SELECT ad_id, ad_name FROM ad_creatives"):
                    ad_names[str(r["ad_id"])] = str(r["ad_name"] or "")
            except sqlite3.OperationalError:
                pass
    except sqlite3.OperationalError:
        return []

    out: list[dict[str, Any]] = []
    for row in rows:
        num = _order_number(row.get("order_name", ""))
        if min_order_number and num is not None and num < min_order_number:
            continue

        # Prefer the LAST touch for attribution: that is the click that actually
        # delivered the buyer. First-touch utm stays in its own columns for the
        # introduction story.
        content = row.get("last_utm_content") or row.get("first_utm_content") or ""
        term = row.get("last_utm_term") or row.get("first_utm_term") or ""

        row["order_number"] = num
        row["ad_id"] = content if _looks_like_meta_id(content) else ""
        row["adset_id"] = term if _looks_like_meta_id(term) else ""
        row["creative_code"] = (
            content if _looks_like_creative_code(content) else ""
        )
        row["lp_hint"] = (
            "" if (row["ad_id"] or row["creative_code"]) else str(content or "")
        )
        row["ad_name"] = ad_names.get(row["ad_id"], "") if row["ad_id"] else ""

        # A creative code names a creative reused across ad-sets/formats, so it is
        # only an ad-level trace when exactly one ad carries it. Counting it as
        # "traced to one ad" otherwise would overstate attribution.
        code_matches: list[str] = []
        if row["creative_code"]:
            code = row["creative_code"].upper()
            code_matches = sorted(
                n for n in ad_names.values() if code in n.upper()
            )
        row["creative_ad_names"] = code_matches

        if row["ad_id"]:
            row["traced"] = "ad"
        elif len(code_matches) == 1:
            row["traced"] = "ad"
            row["ad_name"] = row["ad_name"] or code_matches[0]
        elif code_matches:
            row["traced"] = "creative"
        elif row["adset_id"]:
            row["traced"] = "adset"
        else:
            row["traced"] = ""
        out.append(row)
    return out
