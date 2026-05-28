"""Claude tool surface (CHAT-02).

5 SQLite-backed read-only tools that the chat orchestrator (04-03) exposes to
Claude via the Anthropic tool-use API.  Frozenset allowlists guard every
SQL-dangerous parameter (metric column, source table, sort column).
All SQL uses named parameters — no f-string SQL on untrusted values.
"""
from __future__ import annotations

import json
from typing import Any

import structlog

from src.db.client import DBClient

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Pricing lookup (D-04)
# Haiku 4.5: $1.00/$5.00 per MTok (NOT the old Haiku 3.5 rate of $0.80/$4.00)
# ---------------------------------------------------------------------------
_PRICING: dict[str, tuple[float, float]] = {
    "claude-sonnet-4-6": (3.00, 15.00),   # (input_per_mtok, output_per_mtok)
    "claude-haiku-4-5":  (1.00,  5.00),   # CORRECTED: NOT $0.80/$4.00 (Haiku 3.5 pricing)
}


def calculate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Return USD cost for an Anthropic API call.

    Falls back to Sonnet pricing for unknown models so unmapped IDs do not
    silently report $0.00 (which would let budget exhaustion slip past D-04).
    """
    inp_rate, out_rate = _PRICING.get(model, (3.00, 15.00))
    return (input_tokens * inp_rate + output_tokens * out_rate) / 1_000_000


# ---------------------------------------------------------------------------
# Frozenset allowlists (D-13)
# These are the SQL-safety gates — dynamic column names pass through here ONLY.
# ---------------------------------------------------------------------------
_ALLOWED_METRICS: frozenset[str] = frozenset({
    # Meta side (ad_metrics columns)
    "spend", "impressions", "clicks", "ctr", "cpc", "cpm", "roas",
    "meta_purchases_7dclick", "meta_cost_per_purchase", "reach", "frequency",
    "meta_form_submit_deposit",
    # GA4 side (ga4_metrics columns)
    "sessions", "users", "new_users", "bounce_rate", "avg_engagement_time",
    "ga4_purchases_lastclick",
})

_ALLOWED_SOURCES: frozenset[str] = frozenset({"meta", "ga4", "both"})

_ALLOWED_SORT_COLS: frozenset[str] = frozenset({"conversions", "sessions"})

# Which underlying SQL table each metric lives in (used by compare_periods
# and list_underperformers — they switch the FROM clause based on metric).
_META_METRICS: frozenset[str] = frozenset({
    "spend", "impressions", "clicks", "ctr", "cpc", "cpm", "roas",
    "meta_purchases_7dclick", "meta_cost_per_purchase", "reach", "frequency",
    "meta_form_submit_deposit",
})
_GA4_METRICS: frozenset[str] = frozenset({
    "sessions", "users", "new_users", "bounce_rate", "avg_engagement_time",
    "ga4_purchases_lastclick",
})


# ---------------------------------------------------------------------------
# TOOLS — Anthropic-format schemas (D-12)
# Exactly 5 entries in this order (stable — used verbatim in API requests).
# ---------------------------------------------------------------------------
TOOLS: list[dict[str, Any]] = [
    {
        "name": "query_metrics",
        "description": (
            "Query aggregated Meta Ads or GA4 metrics for a date range. "
            "Returns one row per campaign with spend, ROAS, impressions, clicks, CTR, "
            "purchases, form_submit_deposit conversions, and sessions. "
            "Always call this tool before claiming any metric "
            "is unavailable — impressions, CTR, and meta_form_submit_deposit ARE available for Meta. "
            "Cite the source and date range in any answer that uses this output."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "enum": ["meta", "ga4", "both"],
                           "description": "Data source: 'meta', 'ga4', or 'both'."},
                "start_date": {"type": "string",
                               "description": "ISO 8601 date YYYY-MM-DD (inclusive)."},
                "end_date": {"type": "string",
                             "description": "ISO 8601 date YYYY-MM-DD (inclusive)."},
                "campaign_name": {"type": "string",
                                  "description": "Optional filter to a specific campaign name (exact match)."},
            },
            "required": ["source", "start_date", "end_date"],
        },
    },
    {
        "name": "compare_periods",
        "description": (
            "Compare a single metric between two date windows. "
            "Returns the value for period A, the value for period B, the absolute "
            "delta, and the percentage change. Useful for week-over-week or "
            "month-over-month questions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "metric": {"type": "string",
                           "description": "Metric column name. Must be one of the allowed metrics."},
                "period_a_start": {"type": "string", "description": "ISO date."},
                "period_a_end":   {"type": "string", "description": "ISO date."},
                "period_b_start": {"type": "string", "description": "ISO date."},
                "period_b_end":   {"type": "string", "description": "ISO date."},
                "campaign_name":  {"type": "string", "description": "Optional exact-match filter."},
            },
            "required": ["metric", "period_a_start", "period_a_end",
                         "period_b_start", "period_b_end"],
        },
    },
    {
        "name": "get_campaign_detail",
        "description": (
            "Get daily metric rows for one campaign over the last N days, "
            "showing both Meta and GA4 data side-by-side where available. "
            "Always show source labels — never blend Meta and GA4 conversion counts."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "campaign_name": {"type": "string", "description": "Exact campaign name."},
                "days_back":     {"type": "integer", "description": "Lookback window in days. Default 7.", "default": 7},
            },
            "required": ["campaign_name"],
        },
    },
    {
        "name": "list_underperformers",
        "description": (
            "List campaigns where the chosen metric averaged over the lookback "
            "window falls below a threshold. Ordered worst-first. Returns "
            "campaign_name, avg metric value, and lookback window."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "metric":    {"type": "string", "description": "Allowed metric name."},
                "threshold": {"type": "number", "description": "Underperform if avg(metric) < threshold."},
                "days_back": {"type": "integer", "description": "Lookback window in days. Default 7.", "default": 7},
            },
            "required": ["metric", "threshold"],
        },
    },
    {
        "name": "get_landing_page_performance",
        "description": (
            "Top landing pages from GA4 by conversions or sessions. "
            "Returns landing_page, sessions, total_users, ga4_purchases_lastclick, "
            "and a GA4 source + date-range citation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "ISO date."},
                "end_date":   {"type": "string", "description": "ISO date."},
                "sort_by":    {"type": "string", "enum": ["conversions", "sessions"],
                               "description": "Sort criterion. Default 'conversions'.", "default": "conversions"},
                "limit":      {"type": "integer", "description": "Max rows. Default 10.", "default": 10},
            },
            "required": ["start_date", "end_date"],
        },
    },
]


# ---------------------------------------------------------------------------
# dispatch_tool — async router called by src/ai/chat.py (04-03)
# ---------------------------------------------------------------------------
async def dispatch_tool(name: str, tool_input: dict[str, Any], db: DBClient) -> str:
    """Route a Claude tool_use block to the correct tool function.

    Returns a plain text string for inclusion in a tool_result content block.
    Unknown tool names and tool exceptions return an error string (NOT raise) so
    the agentic loop can recover via self-correction (04-RESEARCH.md lines 493-498).
    """
    try:
        if name == "query_metrics":
            return await query_metrics(db, **tool_input)
        if name == "compare_periods":
            return await compare_periods(db, **tool_input)
        if name == "get_campaign_detail":
            return await get_campaign_detail(db, **tool_input)
        if name == "list_underperformers":
            return await list_underperformers(db, **tool_input)
        if name == "get_landing_page_performance":
            return await get_landing_page_performance(db, **tool_input)
        return (
            f"Error: unknown tool '{name}'. Allowed: query_metrics, compare_periods, "
            "get_campaign_detail, list_underperformers, get_landing_page_performance."
        )
    except TypeError as exc:
        # Missing/extra kwargs — surface to Claude for self-correction
        logger.warning("tool_bad_args", tool=name, error=str(exc))
        return f"Error calling tool '{name}': {exc}"
    except Exception as exc:  # noqa: BLE001
        logger.error("tool_error", tool=name, error=str(exc))
        return f"Error executing tool '{name}': {exc}"


# ---- Tool implementations (Task 2) ----

_QUERY_META_SQL = """
    SELECT c.name AS campaign_name,
           SUM(m.spend) AS spend,
           AVG(m.roas) AS roas,
           SUM(m.impressions) AS impressions,
           SUM(m.clicks) AS clicks,
           AVG(m.ctr) AS ctr,
           SUM(m.meta_purchases_7dclick) AS meta_purchases_7dclick,
           SUM(m.meta_form_submit_deposit) AS meta_form_submit_deposit,
           MAX(m.fetched_at) AS fetched_at
    FROM ad_metrics m
    JOIN campaigns c ON m.campaign_id = c.id
    WHERE m.ad_set_id = '' AND m.ad_id = ''
      AND m.date BETWEEN :start_date AND :end_date
      AND (:campaign_name IS NULL OR c.name = :campaign_name)
    GROUP BY c.name
    ORDER BY spend DESC
"""

_QUERY_GA4_SQL = """
    SELECT g.campaign_utm AS campaign_name,
           SUM(g.sessions) AS sessions,
           SUM(g.users) AS users,
           AVG(g.bounce_rate) AS bounce_rate,
           SUM(g.ga4_purchases_lastclick) AS ga4_purchases_lastclick,
           MAX(g.fetched_at) AS fetched_at
    FROM ga4_metrics g
    WHERE g.date BETWEEN :start_date AND :end_date
      AND (:campaign_name IS NULL OR g.campaign_utm = :campaign_name)
    GROUP BY g.campaign_utm
    ORDER BY sessions DESC
"""


async def query_metrics(
    db: DBClient,
    source: str,
    start_date: str,
    end_date: str,
    campaign_name: str | None = None,
) -> str:
    """Tool 1 — D-12. CHAT-02, CHAT-04, CHAT-08."""
    if source not in _ALLOWED_SOURCES:
        return f"Error: source '{source}' is not recognised. Valid: meta, ga4, both."

    out_blocks: list[str] = []

    if source in ("meta", "both"):
        rows = await db.fetch_all(
            _QUERY_META_SQL,
            {"start_date": start_date, "end_date": end_date,
             "campaign_name": campaign_name},
        )
        if not rows:
            out_blocks.append(
                f"Meta Ads — {start_date} to {end_date}: no data."
            )
        else:
            lines = [f"Meta Ads — {start_date} to {end_date}"]
            fetched_max = None
            for r in rows:
                lines.append(
                    f"Campaign: {r['campaign_name']} | "
                    f"Spend: ${float(r.get('spend') or 0):.2f} | "
                    f"ROAS: {float(r.get('roas') or 0):.2f} | "
                    f"Impressions: {int(r.get('impressions') or 0)} | "
                    f"Clicks: {int(r.get('clicks') or 0)} | "
                    f"CTR: {float(r.get('ctr') or 0):.2f}% | "
                    f"Purchases (7d-click): {int(r.get('meta_purchases_7dclick') or 0)} | "
                    f"Form Submit Deposit: {int(r.get('meta_form_submit_deposit') or 0)}"
                )
                if r.get("fetched_at"):
                    fetched_max = r["fetched_at"]
            lines.append(f"(Source: Meta ad_metrics; as of ingest {fetched_max})")
            out_blocks.append("\n".join(lines))

    if source in ("ga4", "both"):
        rows = await db.fetch_all(
            _QUERY_GA4_SQL,
            {"start_date": start_date, "end_date": end_date,
             "campaign_name": campaign_name},
        )
        if not rows:
            out_blocks.append(
                f"GA4 — {start_date} to {end_date}: no data."
            )
        else:
            lines = [f"GA4 — {start_date} to {end_date} (attribution: last-click)"]
            fetched_max = None
            for r in rows:
                lines.append(
                    f"Campaign UTM: {r['campaign_name']} | "
                    f"Sessions: {int(r.get('sessions') or 0)} | "
                    f"Users: {int(r.get('users') or 0)} | "
                    f"Bounce: {float(r.get('bounce_rate') or 0):.2%} | "
                    f"Purchases: {int(r.get('ga4_purchases_lastclick') or 0)}"
                )
                if r.get("fetched_at"):
                    fetched_max = r["fetched_at"]
            lines.append(f"(Source: GA4 ga4_metrics; as of ingest {fetched_max})")
            out_blocks.append("\n".join(lines))

    return "\n\n".join(out_blocks)


async def compare_periods(
    db: DBClient,
    metric: str,
    period_a_start: str,
    period_a_end: str,
    period_b_start: str,
    period_b_end: str,
    campaign_name: str | None = None,
) -> str:
    """Tool 2 — D-12. Validates metric against frozenset BEFORE building SQL."""
    if metric not in _ALLOWED_METRICS:
        return (f"Error: metric '{metric}' is not recognised. "
                f"Valid: {', '.join(sorted(_ALLOWED_METRICS))}.")

    # Pick the right table — frozenset membership is the SQL-safety gate
    if metric in _META_METRICS:
        sql = (
            f"SELECT AVG({metric}) AS v, MAX(fetched_at) AS fetched_at "  # noqa: S608
            "FROM ad_metrics m "
            "JOIN campaigns c ON m.campaign_id = c.id "
            "WHERE m.ad_set_id = '' AND m.ad_id = '' "
            "AND m.date BETWEEN :start AND :end "
            "AND (:campaign_name IS NULL OR c.name = :campaign_name)"
        )
        source_label = "Meta ad_metrics"
    else:  # GA4
        sql = (
            f"SELECT AVG({metric}) AS v, MAX(fetched_at) AS fetched_at "  # noqa: S608
            "FROM ga4_metrics "
            "WHERE date BETWEEN :start AND :end "
            "AND (:campaign_name IS NULL OR campaign_utm = :campaign_name)"
        )
        source_label = "GA4 ga4_metrics"

    a = await db.fetch_one(sql, {"start": period_a_start, "end": period_a_end,
                                 "campaign_name": campaign_name})
    b = await db.fetch_one(sql, {"start": period_b_start, "end": period_b_end,
                                 "campaign_name": campaign_name})
    av = float(a["v"]) if a and a.get("v") is not None else 0.0
    bv = float(b["v"]) if b and b.get("v") is not None else 0.0
    delta = bv - av
    pct = (delta / av * 100) if av != 0 else float("inf") if delta != 0 else 0.0
    fetched = (b or {}).get("fetched_at") or (a or {}).get("fetched_at")

    return (
        f"Comparison of {metric}\n"
        f"  Period A {period_a_start}..{period_a_end}: {av:.4f}\n"
        f"  Period B {period_b_start}..{period_b_end}: {bv:.4f}\n"
        f"  Delta: {delta:+.4f}  ({pct:+.1f}%)\n"
        f"(Source: {source_label}; as of ingest {fetched})"
    )


_CAMPAIGN_DETAIL_SQL = """
    SELECT c.name AS campaign_name, m.date,
           m.spend, m.roas, m.cpc, m.ctr,
           m.meta_purchases_7dclick, m.meta_form_submit_deposit,
           g.sessions, g.bounce_rate, g.ga4_purchases_lastclick,
           m.fetched_at AS meta_fetched_at, g.fetched_at AS ga4_fetched_at
    FROM ad_metrics m
    JOIN campaigns c ON m.campaign_id = c.id
    LEFT JOIN ga4_metrics g ON g.campaign_utm = c.name AND g.date = m.date
    WHERE m.ad_set_id = '' AND m.ad_id = ''
      AND c.name = :campaign_name
      AND m.date >= date('now', :days_back_str)
    ORDER BY m.date DESC
"""


async def get_campaign_detail(
    db: DBClient,
    campaign_name: str,
    days_back: int = 7,
) -> str:
    """Tool 3 — D-12. Joins Meta + GA4 by exact UTM match (CROSS-01 rule).

    Never blends Meta vs GA4 conversions — shows both with source labels (CROSS-02).
    """
    # Build the days_back SQL fragment safely (integer, not user-supplied col name)
    days_back_str = f"-{int(days_back)} days"
    rows = await db.fetch_all(
        _CAMPAIGN_DETAIL_SQL,
        {"campaign_name": campaign_name, "days_back_str": days_back_str},
    )
    if not rows:
        return (f"No data for campaign '{campaign_name}' in last {days_back} days. "
                "Check the campaign name spelling.")
    lines = [f"Campaign detail: {campaign_name} (last {days_back} days)"]
    meta_fetched = None
    ga4_fetched = None
    for r in rows:
        meta_fetched = r.get("meta_fetched_at") or meta_fetched
        ga4_fetched = r.get("ga4_fetched_at") or ga4_fetched
        lines.append(
            f"  {r['date']} | Meta: spend ${float(r.get('spend') or 0):.2f} "
            f"ROAS {float(r.get('roas') or 0):.2f} "
            f"purchases {int(r.get('meta_purchases_7dclick') or 0)} "
            f"form_submit_deposit {int(r.get('meta_form_submit_deposit') or 0)} | "
            f"GA4: sessions {int(r.get('sessions') or 0)} "
            f"purchases {int(r.get('ga4_purchases_lastclick') or 0)}"
        )
    lines.append(
        f"(Sources: Meta ad_metrics as of {meta_fetched}; "
        f"GA4 ga4_metrics as of {ga4_fetched}. "
        "Meta vs GA4 numbers may differ — Meta is 7-day click attribution; "
        "GA4 is last-click. Never blend the two.)"
    )
    return "\n".join(lines)


async def list_underperformers(
    db: DBClient,
    metric: str,
    threshold: float,
    days_back: int = 7,
) -> str:
    """Tool 4 — D-12. Validates metric against frozenset BEFORE building SQL."""
    if metric not in _ALLOWED_METRICS:
        return (f"Error: metric '{metric}' is not recognised. "
                f"Valid: {', '.join(sorted(_ALLOWED_METRICS))}.")

    days_back_str = f"-{int(days_back)} days"

    if metric in _META_METRICS:
        sql = (
            f"SELECT c.name AS campaign_name, AVG({metric}) AS avg_val, "  # noqa: S608
            "MAX(m.fetched_at) AS fetched_at "
            "FROM ad_metrics m "
            "JOIN campaigns c ON m.campaign_id = c.id "
            "WHERE m.ad_set_id = '' AND m.ad_id = '' "
            "AND m.date >= date('now', :days_back_str) "
            "GROUP BY c.name "
            "HAVING avg_val < :threshold "
            "ORDER BY avg_val ASC"
        )
        source_label = "Meta ad_metrics"
    else:
        sql = (
            f"SELECT campaign_utm AS campaign_name, AVG({metric}) AS avg_val, "  # noqa: S608
            "MAX(fetched_at) AS fetched_at "
            "FROM ga4_metrics "
            "WHERE date >= date('now', :days_back_str) "
            "GROUP BY campaign_utm "
            "HAVING avg_val < :threshold "
            "ORDER BY avg_val ASC"
        )
        source_label = "GA4 ga4_metrics"

    rows = await db.fetch_all(
        sql, {"days_back_str": days_back_str, "threshold": float(threshold)}
    )
    if not rows:
        return (f"No campaigns underperform on {metric} < {threshold} "
                f"over last {days_back} days. (Source: {source_label})")
    lines = [f"Underperformers on {metric} < {threshold} (last {days_back} days)"]
    fetched = None
    for r in rows:
        fetched = r.get("fetched_at") or fetched
        lines.append(
            f"  {r['campaign_name']}: avg {metric} = {float(r['avg_val']):.4f}"
        )
    lines.append(f"(Source: {source_label}; as of ingest {fetched})")
    return "\n".join(lines)


async def get_landing_page_performance(
    db: DBClient,
    start_date: str,
    end_date: str,
    sort_by: str = "conversions",
    limit: int = 10,
) -> str:
    """Tool 5 — D-12."""
    if sort_by not in _ALLOWED_SORT_COLS:
        return (f"Error: sort_by '{sort_by}' is not recognised. "
                "Valid: conversions, sessions.")

    # Map the validated public sort key to the validated SQL column
    order_col = "ga4_purchases_lastclick" if sort_by == "conversions" else "sessions"

    sql = (
        "SELECT landing_page, SUM(sessions) AS sessions, "
        "SUM(total_users) AS total_users, "
        "SUM(ga4_purchases_lastclick) AS ga4_purchases_lastclick, "
        "MAX(fetched_at) AS fetched_at "
        "FROM ga4_landing_pages "
        "WHERE date BETWEEN :start_date AND :end_date "
        "GROUP BY landing_page "
        f"ORDER BY {order_col} DESC "  # noqa: S608 — column validated against frozenset above
        "LIMIT :limit"
    )

    rows = await db.fetch_all(
        sql,
        {"start_date": start_date, "end_date": end_date, "limit": int(limit)},
    )
    if not rows:
        return (f"GA4 landing pages — {start_date} to {end_date}: no data.")
    lines = [f"Top landing pages by {sort_by} ({start_date} to {end_date})"]
    fetched = None
    for r in rows:
        fetched = r.get("fetched_at") or fetched
        lines.append(
            f"  {r['landing_page']}: sessions {int(r.get('sessions') or 0)}, "
            f"purchases {int(r.get('ga4_purchases_lastclick') or 0)}"
        )
    lines.append(f"(Source: GA4 ga4_landing_pages; as of ingest {fetched})")
    return "\n".join(lines)
