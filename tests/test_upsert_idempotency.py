"""Prove INFRA-03: re-inserting the same row UPSERT-updates it rather than duplicating."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def test_migration_is_idempotent(db_client):
    # db_client fixture already migrated once on connect(). Run again via the underlying runner.
    from src.db.migrations import run_migrations
    second = await run_migrations(db_client.conn)
    assert second == [], f"second run_migrations must be a no-op, got {second}"


async def test_ad_metrics_upsert_is_idempotent(db_client):
    row = {
        "campaign_id": "c_1",
        "date": "2026-05-18",
        "ad_set_id": "",   # Phase 1: campaign-level sentinel (widened PK supports Phase 2 META-03)
        "ad_id": "",       # Phase 1: campaign-level sentinel
        "spend": 100.0,
        "impressions": 1000,
        "clicks": 50,
        "ctr": 0.05,
        "cpc": 2.0,
        "cpm": 100.0,
        "roas": 3.0,
        "meta_purchases_7dclick": 5,
        "meta_cost_per_purchase": 20.0,
        "meta_form_submit_deposit": 3,
        "reach": 800,
        "frequency": 1.25,
    }
    await db_client.upsert_ad_metrics([row])
    await db_client.upsert_ad_metrics([row])
    await db_client.upsert_ad_metrics([{**row, "spend": 150.0}])  # update spend
    res = await db_client.fetch_all(
        "SELECT * FROM ad_metrics WHERE campaign_id=? AND date=? AND ad_set_id=? AND ad_id=?",
        ("c_1", "2026-05-18", "", ""),
    )
    assert len(res) == 1, f"expected 1 row, got {len(res)}"
    assert res[0]["spend"] == 150.0, "UPSERT must update spend on conflict"


async def test_ad_metrics_upsert_funnel_v3_columns_written(db_client):
    """Funnel-v3 columns (landing_page_views, video_*, meta_begin_checkout, ...) persist."""
    row = {
        "campaign_id": "c_1",
        "date": "2026-05-18",
        "ad_set_id": "",
        "ad_id": "",
        "spend": 100.0,
        "impressions": 1000,
        "clicks": 50,
        "ctr": 0.05,
        "cpc": 2.0,
        "cpm": 100.0,
        "roas": 3.0,
        "meta_purchases_7dclick": 5,
        "meta_cost_per_purchase": 20.0,
        "meta_form_submit_deposit": 3,
        "reach": 800,
        "frequency": 1.25,
        "landing_page_views": 400,
        "video_3s_views": 250,
        "video_thruplay": 90,
        "meta_begin_checkout": 30,
        "meta_cost_per_begin_checkout": 3.5,
        "meta_add_to_cart": 45,
        "meta_leads": 12,
    }
    await db_client.upsert_ad_metrics([row])
    res = await db_client.fetch_one(
        "SELECT * FROM ad_metrics WHERE campaign_id=? AND date=? AND ad_set_id=? AND ad_id=?",
        ("c_1", "2026-05-18", "", ""),
    )
    assert res["landing_page_views"] == 400
    assert res["video_3s_views"] == 250
    assert res["video_thruplay"] == 90
    assert res["meta_begin_checkout"] == 30
    assert res["meta_cost_per_begin_checkout"] == 3.5
    assert res["meta_add_to_cart"] == 45
    assert res["meta_leads"] == 12


async def test_ad_metrics_upsert_defaults_funnel_v3_columns_to_null(db_client):
    """A row built with the OLD (pre-funnel-v3) shape must not raise a binding error —
    missing funnel-v3 keys degrade to NULL rather than the UPSERT failing outright."""
    row = {
        "campaign_id": "c_1",
        "date": "2026-05-19",
        "ad_set_id": "",
        "ad_id": "",
        "spend": 50.0,
        "impressions": 500,
        "clicks": 25,
        "ctr": 0.05,
        "cpc": 2.0,
        "cpm": 100.0,
        "roas": 1.0,
        "meta_purchases_7dclick": 1,
        "meta_cost_per_purchase": 50.0,
        "meta_form_submit_deposit": 0,
        "reach": 400,
        "frequency": 1.1,
    }
    await db_client.upsert_ad_metrics([row])
    res = await db_client.fetch_one(
        "SELECT * FROM ad_metrics WHERE campaign_id=? AND date=? AND ad_set_id=? AND ad_id=?",
        ("c_1", "2026-05-19", "", ""),
    )
    assert res["landing_page_views"] is None
    assert res["video_thruplay"] is None
    assert res["meta_begin_checkout"] is None


async def test_ga4_metrics_upsert_is_idempotent(db_client):
    row = {
        "campaign_utm": "summer_sale_2026",
        "date": "2026-05-18",
        "sessions": 500,
        "users": 400,
        "new_users": 100,
        "bounce_rate": 0.45,
        "avg_engagement_time": 75.5,
        "ga4_purchases_lastclick": 12,
    }
    await db_client.upsert_ga4_metrics([row])
    await db_client.upsert_ga4_metrics([row])
    await db_client.upsert_ga4_metrics([{**row, "sessions": 600}])
    res = await db_client.fetch_all(
        "SELECT * FROM ga4_metrics WHERE campaign_utm=? AND date=?",
        ("summer_sale_2026", "2026-05-18"),
    )
    assert len(res) == 1, f"expected 1 row, got {len(res)}"
    assert res[0]["sessions"] == 600, "UPSERT must update sessions on conflict"


# ---------------------------------------------------------------------------
# funnel-v3: ga4_events UPSERT idempotency
# ---------------------------------------------------------------------------

async def test_ga4_events_upsert_is_idempotent(db_client):
    row = {
        "event_name": "begin_checkout",
        "date": "2026-05-18",
        "campaign_utm": "nowa_launch",
        "lp_slug": "routine",
        "event_count": 20,
    }
    await db_client.upsert_ga4_events([row])
    await db_client.upsert_ga4_events([row])
    await db_client.upsert_ga4_events([{**row, "event_count": 35}])
    res = await db_client.fetch_all(
        "SELECT * FROM ga4_events WHERE event_name=? AND campaign_utm=? AND lp_slug=?",
        ("begin_checkout", "nowa_launch", "routine"),
    )
    assert len(res) == 1, f"expected 1 row, got {len(res)}"
    assert res[0]["event_count"] == 35, "UPSERT must update event_count on conflict"


async def test_ga4_events_different_lp_slug_is_separate_row(db_client):
    """lp_slug is part of the composite PK — different segments must not collide."""
    base = {
        "event_name": "purchase",
        "date": "2026-05-18",
        "campaign_utm": "nowa_launch",
        "event_count": 5,
    }
    await db_client.upsert_ga4_events([{**base, "lp_slug": "routine"}])
    await db_client.upsert_ga4_events([{**base, "lp_slug": "big-feelings"}])
    res = await db_client.fetch_all(
        "SELECT lp_slug, event_count FROM ga4_events WHERE event_name='purchase' AND campaign_utm='nowa_launch'"
    )
    assert len(res) == 2
    assert {r["lp_slug"] for r in res} == {"routine", "big-feelings"}


async def test_ga4_events_upsert_empty_list_returns_zero(db_client):
    result = await db_client.upsert_ga4_events([])
    assert result == 0


# ---------------------------------------------------------------------------
# funnel-v3: shopify_orders UPSERT idempotency
# ---------------------------------------------------------------------------

async def test_shopify_orders_upsert_is_idempotent(db_client):
    row = {
        "order_id": "ord_1001",
        "created_at": "2026-05-18T10:00:00Z",
        "order_date": "2026-05-18",
        "total_price": 49.99,
        "financial_status": "pending",
        "utm_source": "meta",
        "utm_campaign": "nowa_launch",
        "utm_content": "ad_a",
        "lp_slug": "routine",
        "landing_site": "/pages/preorder?utm_source=meta&utm_campaign=nowa_launch",
        "referring_site": "https://facebook.com",
    }
    await db_client.upsert_shopify_orders([row])
    await db_client.upsert_shopify_orders([row])
    await db_client.upsert_shopify_orders([{**row, "financial_status": "paid"}])
    res = await db_client.fetch_all(
        "SELECT * FROM shopify_orders WHERE order_id=?", ("ord_1001",)
    )
    assert len(res) == 1, f"expected 1 row, got {len(res)}"
    assert res[0]["financial_status"] == "paid", "UPSERT must update financial_status on conflict"


async def test_shopify_orders_upsert_empty_list_returns_zero(db_client):
    result = await db_client.upsert_shopify_orders([])
    assert result == 0


# ---------------------------------------------------------------------------
# Phase C: pixel_health UPSERT idempotency
# ---------------------------------------------------------------------------

async def test_pixel_health_upsert_is_idempotent(db_client):
    row = {
        "date": "2026-05-18",
        "event_name": "purchase",
        "browser_count": 100,
        "server_count": 80,
        "dedup_rate": 0.65,
        "emq_score": None,
    }
    await db_client.upsert_pixel_health([row])
    await db_client.upsert_pixel_health([row])
    await db_client.upsert_pixel_health([{**row, "browser_count": 150}])
    res = await db_client.fetch_all(
        "SELECT * FROM pixel_health WHERE date=? AND event_name=?",
        ("2026-05-18", "purchase"),
    )
    assert len(res) == 1, f"expected 1 row, got {len(res)}"
    assert res[0]["browser_count"] == 150, "UPSERT must update browser_count on conflict"
    assert res[0]["server_count"] == 80
    assert res[0]["dedup_rate"] == pytest.approx(0.65)
    assert res[0]["emq_score"] is None


async def test_pixel_health_different_event_name_is_separate_row(db_client):
    base = {
        "date": "2026-05-18",
        "browser_count": 10,
        "server_count": 5,
        "dedup_rate": None,
        "emq_score": None,
    }
    await db_client.upsert_pixel_health([{**base, "event_name": "begin_checkout"}])
    await db_client.upsert_pixel_health([{**base, "event_name": "purchase"}])
    res = await db_client.fetch_all(
        "SELECT event_name FROM pixel_health WHERE date='2026-05-18'"
    )
    assert {r["event_name"] for r in res} == {"begin_checkout", "purchase"}


async def test_pixel_health_upsert_empty_list_returns_zero(db_client):
    result = await db_client.upsert_pixel_health([])
    assert result == 0
