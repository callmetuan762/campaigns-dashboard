"""Tests for src/shopify/ingest.py — credential guard (no-op), UPSERT wiring, ingestion_log.

Covers SHOP-01 (graceful no-op when unset — mirrors GA4/Meta credential guards) and
SHOP-03 (ingestion_log source='shopify').
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# SHOP-01: credential guard — clean no-op when unset
# ---------------------------------------------------------------------------

async def test_shopify_ingest_skips_when_store_domain_unset():
    from src.shopify.ingest import _run_shopify_ingest

    settings = MagicMock()
    settings.shopify_store_domain = None
    settings.shopify_admin_token = "some-token"

    mock_db = AsyncMock()

    await _run_shopify_ingest(bot=None, db=mock_db, settings=settings)

    mock_db.log_ingestion_start.assert_not_called()


async def test_shopify_ingest_skips_when_admin_token_unset():
    from src.shopify.ingest import _run_shopify_ingest

    settings = MagicMock()
    settings.shopify_store_domain = "shop.nowaplanet.com"
    settings.shopify_admin_token = None

    mock_db = AsyncMock()

    await _run_shopify_ingest(bot=None, db=mock_db, settings=settings)

    mock_db.log_ingestion_start.assert_not_called()


async def test_shopify_ingest_skips_when_both_unset():
    from src.shopify.ingest import _run_shopify_ingest

    settings = MagicMock()
    settings.shopify_store_domain = None
    settings.shopify_admin_token = None

    mock_db = AsyncMock()

    await _run_shopify_ingest(bot=None, db=mock_db, settings=settings)

    mock_db.log_ingestion_start.assert_not_called()
    mock_db.upsert_shopify_orders.assert_not_called()


# ---------------------------------------------------------------------------
# Happy path: credentials configured -> fetch + upsert + ingestion_log
# ---------------------------------------------------------------------------

async def test_shopify_ingest_happy_path_writes_and_logs_success(monkeypatch):
    from src.shopify import ingest as ingest_module

    settings = MagicMock()
    settings.shopify_store_domain = "shop.nowaplanet.com"
    secret = MagicMock()
    secret.get_secret_value.return_value = "real-token"
    settings.shopify_admin_token = secret
    settings.shopify_api_version = "2025-01"

    mock_db = AsyncMock()
    mock_db.log_ingestion_start.return_value = 42
    mock_db.upsert_shopify_orders.return_value = 3

    fake_orders = [
        {"order_id": "1", "created_at": "", "order_date": "2026-05-18", "total_price": 10.0,
         "financial_status": "paid", "utm_source": "", "utm_campaign": "", "utm_content": "",
         "lp_slug": "", "landing_site": "", "referring_site": ""},
    ] * 3

    mock_fetch = AsyncMock(return_value=fake_orders)
    monkeypatch.setattr("src.shopify.client.fetch_orders", mock_fetch)

    await ingest_module._run_shopify_ingest(
        bot=None, db=mock_db, settings=settings,
        since_override="2026-05-18", until_override="2026-05-18",
    )

    mock_db.log_ingestion_start.assert_called_once_with("shopify")
    mock_fetch.assert_called_once_with("shop.nowaplanet.com", "real-token", "2026-05-18", "2026-05-18", "2025-01")
    mock_db.upsert_shopify_orders.assert_called_once_with(fake_orders)
    mock_db.log_ingestion_finish.assert_called_once_with(42, "success", rows_upserted=3)


async def test_shopify_ingest_plain_string_token_used_directly(monkeypatch):
    """shopify_admin_token can be a plain str (no get_secret_value) — must not crash."""
    from src.shopify import ingest as ingest_module

    settings = MagicMock()
    settings.shopify_store_domain = "shop.nowaplanet.com"
    settings.shopify_admin_token = "plain-token"
    settings.shopify_api_version = "2025-01"

    mock_db = AsyncMock()
    mock_db.log_ingestion_start.return_value = 1
    mock_db.upsert_shopify_orders.return_value = 0

    mock_fetch = AsyncMock(return_value=[])
    monkeypatch.setattr("src.shopify.client.fetch_orders", mock_fetch)

    await ingest_module._run_shopify_ingest(
        bot=None, db=mock_db, settings=settings,
        since_override="2026-05-18", until_override="2026-05-18",
    )

    mock_fetch.assert_called_once_with("shop.nowaplanet.com", "plain-token", "2026-05-18", "2026-05-18", "2025-01")


async def test_shopify_ingest_failure_logs_failed_status(monkeypatch):
    from src.shopify import ingest as ingest_module

    settings = MagicMock()
    settings.shopify_store_domain = "shop.nowaplanet.com"
    settings.shopify_admin_token = "token"
    settings.shopify_api_version = "2025-01"

    mock_db = AsyncMock()
    mock_db.log_ingestion_start.return_value = 7

    mock_fetch = AsyncMock(side_effect=Exception("boom"))
    monkeypatch.setattr("src.shopify.client.fetch_orders", mock_fetch)

    with __import__("unittest.mock", fromlist=["patch"]).patch("sentry_sdk.capture_exception"):
        await ingest_module._run_shopify_ingest(
            bot=None, db=mock_db, settings=settings,
            since_override="2026-05-18", until_override="2026-05-18",
        )

    mock_db.log_ingestion_finish.assert_called_once()
    args, kwargs = mock_db.log_ingestion_finish.call_args
    assert args[0] == 7
    assert args[1] == "failed"


# ---------------------------------------------------------------------------
# run_shopify_ingest_for_range / register_job_resources / shopify_ingest_job
# ---------------------------------------------------------------------------

async def test_run_shopify_ingest_for_range_passes_bot_none():
    from src.shopify.ingest import run_shopify_ingest_for_range

    mock_db = AsyncMock()
    mock_settings = MagicMock()

    from unittest.mock import patch
    with patch("src.shopify.ingest._run_shopify_ingest", new_callable=AsyncMock) as mock_run:
        await run_shopify_ingest_for_range(mock_db, mock_settings, "2026-05-01", "2026-05-02")

    mock_run.assert_called_once()
    _, kwargs = mock_run.call_args
    assert kwargs.get("bot") is None
    assert kwargs.get("since_override") == "2026-05-01"
    assert kwargs.get("until_override") == "2026-05-02"


def test_register_job_resources_sets_globals():
    import src.shopify.ingest as ingest_module

    mock_bot = MagicMock()
    mock_db = MagicMock()
    mock_settings = MagicMock()
    ingest_module.register_job_resources(mock_bot, mock_db, mock_settings)
    assert ingest_module._bot is mock_bot
    assert ingest_module._db is mock_db
    assert ingest_module._settings is mock_settings
    # Clean up module globals
    ingest_module._bot = None
    ingest_module._db = None
    ingest_module._settings = None


async def test_shopify_ingest_job_noop_when_resources_not_registered():
    import src.shopify.ingest as ingest_module

    ingest_module._db = None
    ingest_module._settings = None
    # Must not raise even though nothing is registered.
    await ingest_module.shopify_ingest_job()
