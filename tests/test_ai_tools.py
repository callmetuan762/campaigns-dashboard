"""Unit + integration tests for src/ai/tools.py (Phase 4 Plan 04-06).

Covers: pricing regression guards, TOOLS list shape, all 5 tool functions
(happy path + invalid-input error strings + empty-table no-data strings).
Requirement IDs: CHAT-02, CHAT-04, CHAT-08, REC-01, REC-02, REC-03, D-12, D-13
"""
from __future__ import annotations

import pytest

from src.ai.tools import (
    TOOLS,
    _PRICING,
    calculate_cost,
    compare_periods,
    dispatch_tool,
    get_campaign_detail,
    get_landing_page_performance,
    list_underperformers,
    query_metrics,
)


# ---------------------------------------------------------------------------
# Pricing regression guards (Pitfall 5 from 04-RESEARCH.md)
# ---------------------------------------------------------------------------


def test_pricing_haiku45_corrected():
    """RESEARCH Pitfall 5: Haiku 4.5 is $1.00/$5.00 per MTok (NOT $0.80/$4.00)."""
    assert _PRICING["claude-haiku-4-5"] == (1.00, 5.00)


def test_pricing_sonnet46():
    """Sonnet 4.6 pricing sanity check. D-04."""
    assert _PRICING["claude-sonnet-4-6"] == (3.00, 15.00)


def test_calculate_cost_sonnet():
    """calculate_cost: 1M input tokens at Sonnet = $3.00. D-04."""
    assert calculate_cost("claude-sonnet-4-6", 1_000_000, 0) == 3.00


def test_calculate_cost_output_sonnet():
    """calculate_cost: 1M output tokens at Sonnet = $15.00. D-04."""
    assert calculate_cost("claude-sonnet-4-6", 0, 1_000_000) == 15.00


def test_calculate_cost_unknown_model_falls_back():
    """Unknown model falls back to Sonnet rate (fails closed for budget). D-04."""
    # An unmapped model must NOT report $0 — that would silently skip the budget gate.
    assert calculate_cost("unknown-model", 1_000_000, 0) == 3.00


# ---------------------------------------------------------------------------
# TOOLS list shape (D-12)
# ---------------------------------------------------------------------------


def test_tools_list_names_in_order():
    """CHAT-02 D-12: 5 tools in exact required order with valid input_schema."""
    names = [t["name"] for t in TOOLS]
    assert names == [
        "query_metrics",
        "compare_periods",
        "get_campaign_detail",
        "list_underperformers",
        "get_landing_page_performance",
    ]
    for t in TOOLS:
        assert "input_schema" in t
        assert "required" in t["input_schema"]
        assert "properties" in t["input_schema"]
        assert isinstance(t["input_schema"]["required"], list)


def test_tools_list_length():
    """CHAT-02 D-12: exactly 5 tools — no accidental additions or removals."""
    assert len(TOOLS) == 5


# ---------------------------------------------------------------------------
# query_metrics (Tool 1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_query_metrics_invalid_source(db_client):
    """CHAT-02 D-13: invalid source returns error string, never raises."""
    result = await query_metrics(db_client, "bogus", "2026-05-01", "2026-05-02")
    assert result.startswith("Error: source"), result


@pytest.mark.asyncio
async def test_query_metrics_meta_empty(db_client):
    """CHAT-08: empty DB returns 'no data' string, not an exception."""
    result = await query_metrics(db_client, "meta", "2026-05-01", "2026-05-02")
    assert "no data" in result.lower(), result


@pytest.mark.asyncio
async def test_query_metrics_ga4_empty(db_client):
    """CHAT-08: empty GA4 table returns 'no data' string."""
    result = await query_metrics(db_client, "ga4", "2026-05-01", "2026-05-02")
    assert "no data" in result.lower(), result


@pytest.mark.asyncio
async def test_query_metrics_meta_seeded(db_client):
    """CHAT-02 / CHAT-04: returns aggregated row + (Source: ...) citation."""
    await db_client.execute(
        "INSERT INTO ad_metrics (campaign_id, date, ad_set_id, ad_id, spend, roas, "
        "meta_purchases_7dclick, clicks, impressions, fetched_at) VALUES "
        "(:cid, :d, '', '', :s, :r, :p, :c, :i, '2026-05-19 02:15')",
        {"cid": "c_1", "d": "2026-05-18", "s": 320.0, "r": 2.4,
         "p": 18, "c": 100, "i": 5000},
    )
    result = await query_metrics(db_client, "meta", "2026-05-18", "2026-05-18")
    assert "Meta Ads" in result
    assert "Test Campaign" in result
    assert "(Source: Meta ad_metrics" in result


@pytest.mark.asyncio
async def test_query_metrics_ga4_seeded(db_client):
    """CHAT-02: GA4 source returns sessions and citation."""
    await db_client.execute(
        "INSERT INTO ga4_metrics (campaign_utm, date, sessions, users, new_users, "
        "bounce_rate, avg_engagement_time, ga4_purchases_lastclick, fetched_at) VALUES "
        "(:utm, :d, :s, :u, :nu, :br, :ae, :p, '2026-05-19 03:00')",
        {"utm": "spring_sale", "d": "2026-05-18", "s": 500, "u": 400,
         "nu": 200, "br": 0.35, "ae": 120.0, "p": 25},
    )
    result = await query_metrics(db_client, "ga4", "2026-05-18", "2026-05-18")
    assert "GA4" in result
    assert "spring_sale" in result
    assert "(Source: GA4 ga4_metrics" in result


# ---------------------------------------------------------------------------
# compare_periods (Tool 2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compare_periods_invalid_metric(db_client):
    """CHAT-02 D-13: invalid metric returns error string, never raises."""
    result = await compare_periods(
        db_client, "no_such_metric",
        "2026-05-01", "2026-05-07", "2026-05-08", "2026-05-14",
    )
    assert result.startswith("Error: metric"), result


@pytest.mark.asyncio
async def test_compare_periods_meta_seeded(db_client):
    """CHAT-02: compare Meta spend across two periods returns delta and citation."""
    # Period A
    await db_client.execute(
        "INSERT INTO ad_metrics (campaign_id, date, ad_set_id, ad_id, spend, roas, "
        "meta_purchases_7dclick, clicks, impressions, fetched_at) VALUES "
        "(:cid, :d, '', '', :s, :r, :p, :c, :i, '2026-05-19')",
        {"cid": "c_1", "d": "2026-05-01", "s": 100.0, "r": 2.0,
         "p": 5, "c": 50, "i": 1000},
    )
    # Period B
    await db_client.execute(
        "INSERT INTO ad_metrics (campaign_id, date, ad_set_id, ad_id, spend, roas, "
        "meta_purchases_7dclick, clicks, impressions, fetched_at) VALUES "
        "(:cid, :d, '', '', :s, :r, :p, :c, :i, '2026-05-19')",
        {"cid": "c_1", "d": "2026-05-08", "s": 200.0, "r": 3.0,
         "p": 10, "c": 80, "i": 2000},
    )
    result = await compare_periods(
        db_client, "spend",
        "2026-05-01", "2026-05-01", "2026-05-08", "2026-05-08",
    )
    assert "Comparison of spend" in result
    assert "(Source: Meta ad_metrics" in result


@pytest.mark.asyncio
async def test_compare_periods_ga4_seeded(db_client):
    """CHAT-02: compare GA4 sessions across two periods returns delta and citation."""
    await db_client.execute(
        "INSERT INTO ga4_metrics (campaign_utm, date, sessions, users, new_users, "
        "bounce_rate, avg_engagement_time, ga4_purchases_lastclick, fetched_at) VALUES "
        "(:utm, :d, :s, :u, :nu, :br, :ae, :p, '2026-05-19')",
        {"utm": "promo_a", "d": "2026-05-01", "s": 200, "u": 150,
         "nu": 80, "br": 0.40, "ae": 90.0, "p": 10},
    )
    await db_client.execute(
        "INSERT INTO ga4_metrics (campaign_utm, date, sessions, users, new_users, "
        "bounce_rate, avg_engagement_time, ga4_purchases_lastclick, fetched_at) VALUES "
        "(:utm, :d, :s, :u, :nu, :br, :ae, :p, '2026-05-19')",
        {"utm": "promo_a", "d": "2026-05-08", "s": 400, "u": 300,
         "nu": 150, "br": 0.30, "ae": 110.0, "p": 20},
    )
    result = await compare_periods(
        db_client, "sessions",
        "2026-05-01", "2026-05-01", "2026-05-08", "2026-05-08",
    )
    assert "Comparison of sessions" in result
    assert "(Source: GA4 ga4_metrics" in result


# ---------------------------------------------------------------------------
# get_campaign_detail (Tool 3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_campaign_detail_no_data(db_client):
    """CHAT-08 REC-01: no data returns helpful message with campaign name."""
    result = await get_campaign_detail(db_client, "NonExistentCampaign", 7)
    assert "No data for campaign" in result


@pytest.mark.asyncio
async def test_get_campaign_detail_with_data(db_client):
    """REC-01: seeded Meta data returns detail rows with source labels."""
    await db_client.execute(
        "INSERT INTO ad_metrics (campaign_id, date, ad_set_id, ad_id, spend, roas, "
        "meta_purchases_7dclick, clicks, impressions, cpc, ctr, fetched_at) VALUES "
        "(:cid, date('now', '-1 day'), '', '', :s, :r, :p, :c, :i, :cpc, :ctr, '2026-05-19')",
        {"cid": "c_1", "s": 150.0, "r": 2.5, "p": 8,
         "c": 60, "i": 3000, "cpc": 2.50, "ctr": 0.02},
    )
    result = await get_campaign_detail(db_client, "Test Campaign", 7)
    assert "Test Campaign" in result
    assert "Meta" in result


# ---------------------------------------------------------------------------
# list_underperformers (Tool 4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_underperformers_invalid_metric(db_client):
    """CHAT-02 D-13: invalid metric returns error string, never raises."""
    result = await list_underperformers(db_client, "not_a_metric", 1.0, 7)
    assert result.startswith("Error: metric"), result


@pytest.mark.asyncio
async def test_list_underperformers_meta_no_underperformers(db_client):
    """REC-01: returns 'no campaigns underperform' string when threshold not met."""
    # Seed a campaign with roas=3.0, threshold=1.0 — should NOT be listed
    await db_client.execute(
        "INSERT INTO ad_metrics (campaign_id, date, ad_set_id, ad_id, spend, roas, "
        "meta_purchases_7dclick, clicks, impressions, fetched_at) VALUES "
        "(:cid, date('now', '-1 day'), '', '', :s, :r, :p, :c, :i, '2026-05-19')",
        {"cid": "c_1", "s": 100.0, "r": 3.0, "p": 5, "c": 50, "i": 1000},
    )
    result = await list_underperformers(db_client, "roas", 1.0, 7)
    assert "No campaigns underperform" in result or "no campaigns" in result.lower()


@pytest.mark.asyncio
async def test_list_underperformers_meta_seeded(db_client):
    """REC-01: campaign with roas below threshold appears in results."""
    await db_client.execute(
        "INSERT INTO ad_metrics (campaign_id, date, ad_set_id, ad_id, spend, roas, "
        "meta_purchases_7dclick, clicks, impressions, fetched_at) VALUES "
        "(:cid, date('now', '-1 day'), '', '', :s, :r, :p, :c, :i, '2026-05-19')",
        {"cid": "c_1", "s": 100.0, "r": 0.5, "p": 2, "c": 40, "i": 1000},
    )
    result = await list_underperformers(db_client, "roas", 1.0, 7)
    assert "Test Campaign" in result
    assert "(Source: Meta ad_metrics" in result


# ---------------------------------------------------------------------------
# get_landing_page_performance (Tool 5)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_landing_page_performance_invalid_sort(db_client):
    """CHAT-02 D-13: invalid sort_by returns error string, never raises."""
    result = await get_landing_page_performance(
        db_client, "2026-05-01", "2026-05-18", sort_by="nope"
    )
    assert result.startswith("Error: sort_by"), result


@pytest.mark.asyncio
async def test_get_landing_page_performance_empty(db_client):
    """CHAT-08: empty table returns 'no data' string."""
    result = await get_landing_page_performance(
        db_client, "2026-05-01", "2026-05-18"
    )
    assert "no data" in result.lower(), result


@pytest.mark.asyncio
async def test_get_landing_page_performance_seeded(db_client):
    """REC-02: landing pages returned with sessions and purchases citation."""
    await db_client.execute(
        "INSERT INTO ga4_landing_pages (landing_page, date, sessions, total_users, "
        "ga4_purchases_lastclick, screen_page_views, avg_engagement_time, fetched_at) "
        "VALUES (:lp, :d, :s, :tu, :p, :spv, :ae, '2026-05-19')",
        {"lp": "/home", "d": "2026-05-18", "s": 100, "tu": 80,
         "p": 10, "spv": 200, "ae": 90.0},
    )
    result = await get_landing_page_performance(
        db_client, "2026-05-18", "2026-05-18"
    )
    assert "/home" in result
    assert "(Source: GA4 ga4_landing_pages" in result


@pytest.mark.asyncio
async def test_get_landing_page_performance_sort_by_sessions(db_client):
    """REC-02: sort_by='sessions' accepted and returns results."""
    await db_client.execute(
        "INSERT INTO ga4_landing_pages (landing_page, date, sessions, total_users, "
        "ga4_purchases_lastclick, screen_page_views, avg_engagement_time, fetched_at) "
        "VALUES (:lp, :d, :s, :tu, :p, :spv, :ae, '2026-05-19')",
        {"lp": "/shop", "d": "2026-05-18", "s": 200, "tu": 150,
         "p": 5, "spv": 400, "ae": 60.0},
    )
    result = await get_landing_page_performance(
        db_client, "2026-05-18", "2026-05-18", sort_by="sessions"
    )
    assert "/shop" in result
    assert "(Source: GA4 ga4_landing_pages" in result


# ---------------------------------------------------------------------------
# dispatch_tool router
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_tool_unknown(db_client):
    """CHAT-02: unknown tool name returns error string (never raises)."""
    result = await dispatch_tool("nonexistent", {}, db_client)
    assert result.startswith("Error: unknown tool"), result


@pytest.mark.asyncio
async def test_dispatch_tool_routes_query_metrics(db_client):
    """CHAT-02 REC-03: dispatch_tool correctly routes to query_metrics."""
    result = await dispatch_tool(
        "query_metrics",
        {"source": "meta", "start_date": "2026-05-01", "end_date": "2026-05-02"},
        db_client,
    )
    # Empty DB returns a no-data string — not an 'Error: source' string
    assert "Error: source" not in result
