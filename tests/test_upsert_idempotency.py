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
