"""Tests for src/ga4/ingest.py — upsert idempotency, circuit breaker, 6h cache, D-2 freshness.

Covers: GA4-04 (6h cache), GA4-05 (upsert idempotency), D-09 (circuit breaker), D-10 (D-2 freshness).
"""
from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.ga4.ingest import _check_circuit_breaker, _get_d2_iso, register_job_resources

# ---- D-10: D-2 freshness ----

def test_get_d2_iso_returns_two_days_ago():
    """D-10: GA4 D-2 freshness — result must be today minus 2 days, not 1 day."""
    result = _get_d2_iso("UTC")
    expected = (date.today() - timedelta(days=2)).isoformat()
    assert result == expected


def test_get_d2_iso_not_yesterday():
    """D-10: Explicitly verify D-2 != D-1 (guards against days=1 regression)."""
    result = _get_d2_iso("UTC")
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    assert result != yesterday


# ---- GA4-05: Upsert idempotency ----

async def test_upsert_ga4_metrics_idempotent(db_client):
    """GA4-05: Re-upserting same (campaign_utm, date) does not duplicate rows."""
    row = {
        "campaign_utm": "spring_sale",
        "date": "2026-05-17",
        "sessions": 100,
        "users": 80,
        "new_users": 40,
        "bounce_rate": 0.45,
        "avg_engagement_time": 60.0,
        "ga4_purchases_lastclick": 5,
    }
    await db_client.upsert_ga4_metrics([row])
    await db_client.upsert_ga4_metrics([row])
    rows = await db_client.fetch_all(
        "SELECT * FROM ga4_metrics WHERE campaign_utm = 'spring_sale'"
    )
    assert len(rows) == 1


async def test_upsert_ga4_metrics_updates_on_conflict(db_client):
    """GA4-05: Re-upserting same key with new value updates the row."""
    row = {
        "campaign_utm": "retargeting",
        "date": "2026-05-17",
        "sessions": 50,
        "users": 40,
        "new_users": 10,
        "bounce_rate": 0.3,
        "avg_engagement_time": 45.0,
        "ga4_purchases_lastclick": 2,
    }
    await db_client.upsert_ga4_metrics([row])
    row["sessions"] = 75
    await db_client.upsert_ga4_metrics([row])
    result = await db_client.fetch_one(
        "SELECT sessions FROM ga4_metrics WHERE campaign_utm = 'retargeting'"
    )
    assert result["sessions"] == 75


async def test_upsert_ga4_landing_pages_idempotent(db_client):
    """GA4-05: Re-upserting same (landing_page, date) does not duplicate rows."""
    row = {
        "landing_page": "/products/shoes",
        "date": "2026-05-17",
        "sessions": 45,
        "total_users": 38,
        "ga4_purchases_lastclick": 3,
        "screen_page_views": 120,
        "avg_engagement_time": 55.0,
    }
    await db_client.upsert_ga4_landing_pages([row])
    await db_client.upsert_ga4_landing_pages([row])
    rows = await db_client.fetch_all(
        "SELECT * FROM ga4_landing_pages WHERE landing_page = '/products/shoes'"
    )
    assert len(rows) == 1


async def test_upsert_ga4_landing_pages_empty_list(db_client):
    """GA4-05: upsert_ga4_landing_pages([]) returns 0 without error."""
    result = await db_client.upsert_ga4_landing_pages([])
    assert result == 0


async def test_upsert_ga4_landing_pages_returns_row_count(db_client):
    """GA4-05: upsert_ga4_landing_pages returns count of rows processed."""
    rows = [
        {"landing_page": "/a", "date": "2026-05-17", "sessions": 10, "total_users": 8,
         "ga4_purchases_lastclick": 1, "screen_page_views": 30, "avg_engagement_time": 40.0},
        {"landing_page": "/b", "date": "2026-05-17", "sessions": 5, "total_users": 4,
         "ga4_purchases_lastclick": 0, "screen_page_views": 10, "avg_engagement_time": 20.0},
    ]
    result = await db_client.upsert_ga4_landing_pages(rows)
    assert result == 2


# ---- ga4_daily_totals: exact property-wide session totals (session multi-counting fix) ----

async def test_upsert_ga4_daily_totals_idempotent(db_client):
    """Re-upserting same date does not duplicate rows (PK on date)."""
    row = {"date": "2026-07-21", "sessions": 1803}
    await db_client.upsert_ga4_daily_totals([row])
    await db_client.upsert_ga4_daily_totals([row])
    rows = await db_client.fetch_all(
        "SELECT * FROM ga4_daily_totals WHERE date = '2026-07-21'"
    )
    assert len(rows) == 1


async def test_upsert_ga4_daily_totals_updates_on_conflict(db_client):
    """Re-upserting same date with a new value updates sessions in place."""
    await db_client.upsert_ga4_daily_totals([{"date": "2026-07-21", "sessions": 1000}])
    await db_client.upsert_ga4_daily_totals([{"date": "2026-07-21", "sessions": 1803}])
    result = await db_client.fetch_one(
        "SELECT sessions FROM ga4_daily_totals WHERE date = '2026-07-21'"
    )
    assert result["sessions"] == 1803


async def test_upsert_ga4_daily_totals_empty_list(db_client):
    result = await db_client.upsert_ga4_daily_totals([])
    assert result == 0


async def test_upsert_ga4_daily_totals_returns_row_count(db_client):
    rows = [
        {"date": "2026-07-15", "sessions": 185},
        {"date": "2026-07-16", "sessions": 311},
    ]
    result = await db_client.upsert_ga4_daily_totals(rows)
    assert result == 2


# ---- GA4-04: 6-hour cache ----

async def test_6h_cache_check_returns_row_after_successful_ingest(db_client):
    """GA4-04: A successful ingestion_log entry within 6h is detectable by cache query."""
    log_id = await db_client.log_ingestion_start("ga4")
    await db_client.log_ingestion_finish(log_id, "success", rows_upserted=10)
    recent = await db_client.fetch_one(
        "SELECT id FROM ingestion_log WHERE source = 'ga4' AND status = 'success' "
        "AND started_at > datetime('now', '-6 hours')"
    )
    assert recent is not None


async def test_6h_cache_check_returns_none_after_failed_ingest(db_client):
    """GA4-04: A failed ingestion_log entry does NOT trigger the cache check."""
    log_id = await db_client.log_ingestion_start("ga4")
    await db_client.log_ingestion_finish(log_id, "failed", error="test")
    recent = await db_client.fetch_one(
        "SELECT id FROM ingestion_log WHERE source = 'ga4' AND status = 'success' "
        "AND started_at > datetime('now', '-6 hours')"
    )
    assert recent is None


# ---- D-09: Circuit breaker ----

async def test_circuit_breaker_not_triggered_under_threshold(db_client):
    """D-09: Circuit breaker must NOT trigger with < 3 consecutive failures."""
    log_id = await db_client.log_ingestion_start("ga4")
    await db_client.log_ingestion_finish(log_id, "failed", error="test")
    result = await _check_circuit_breaker(db_client, "ga4", threshold=3)
    assert result is False


async def test_circuit_breaker_triggered_at_threshold(db_client):
    """D-09: Circuit breaker triggers when last 3 runs all failed."""
    for _ in range(3):
        log_id = await db_client.log_ingestion_start("ga4")
        await db_client.log_ingestion_finish(log_id, "failed", error="test")
    result = await _check_circuit_breaker(db_client, "ga4", threshold=3)
    assert result is True


async def test_circuit_breaker_not_triggered_if_success_in_recent(db_client):
    """D-09: Circuit breaker stays closed if there is a success among recent runs."""
    for _ in range(2):
        log_id = await db_client.log_ingestion_start("ga4")
        await db_client.log_ingestion_finish(log_id, "failed", error="test")
    log_id = await db_client.log_ingestion_start("ga4")
    await db_client.log_ingestion_finish(log_id, "success", rows_upserted=5)
    result = await _check_circuit_breaker(db_client, "ga4", threshold=3)
    assert result is False


# ---- register_job_resources ----

# ---------------------------------------------------------------------------
# funnel-v3: event ingestion wired into _run_ga4_ingest (Step 8b)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_ga4_ingest_upserts_events_and_sums_rows(monkeypatch):
    """_run_ga4_ingest fetches event-level metrics and folds them into the same
    ingestion_log entry as campaign + landing-page rows (rows_upserted is the sum)."""
    import src.ga4.client as client_module
    from src.ga4.ingest import _run_ga4_ingest

    settings = MagicMock()
    settings.report_timezone = "UTC"
    settings.ga4_property_id = "534295825"
    settings.ga4_service_account_json = "/tmp/fake.json"
    settings.ga4_conversion_event = "purchase"
    settings.ga4_event_list = ["begin_checkout", "purchase"]

    monkeypatch.setattr(client_module, "_build_ga4_client", lambda path: MagicMock())
    monkeypatch.setattr(
        client_module, "fetch_campaign_metrics",
        AsyncMock(return_value=[{"campaign_utm": "x", "date": "2026-05-17"}]),
    )
    monkeypatch.setattr(
        client_module, "fetch_landing_page_metrics",
        AsyncMock(return_value=[{"landing_page": "/y", "date": "2026-05-17"}]),
    )
    totals_rows = [{"date": "2026-05-17", "sessions": 42}]
    monkeypatch.setattr(
        client_module, "fetch_daily_session_totals", AsyncMock(return_value=totals_rows)
    )
    event_rows = [
        {"event_name": "begin_checkout", "date": "2026-05-17", "campaign_utm": "x", "lp_slug": "", "event_count": 5},
        {"event_name": "purchase", "date": "2026-05-17", "campaign_utm": "x", "lp_slug": "", "event_count": 2},
    ]
    monkeypatch.setattr(client_module, "fetch_event_metrics", AsyncMock(return_value=event_rows))

    mock_db = AsyncMock()
    mock_db.log_ingestion_start.return_value = 99
    mock_db.upsert_ga4_metrics.return_value = 1
    mock_db.upsert_ga4_landing_pages.return_value = 1
    mock_db.upsert_ga4_daily_totals.return_value = 1
    mock_db.upsert_ga4_events.return_value = 2

    await _run_ga4_ingest(bot=None, db=mock_db, settings=settings, date_override="2026-05-17", skip_cache=True)

    mock_db.upsert_ga4_daily_totals.assert_called_once_with(totals_rows)
    mock_db.upsert_ga4_events.assert_called_once_with(event_rows)
    mock_db.log_ingestion_finish.assert_called_once_with(99, "success", rows_upserted=5)


@pytest.mark.asyncio
async def test_run_ga4_ingest_event_fetch_failure_does_not_fail_whole_run(monkeypatch):
    """A GA4 events-report failure must not prevent campaign/LP ingestion from succeeding
    (CLAUDE.md graceful degradation) — the run still finishes with status='success'."""
    import src.ga4.client as client_module
    from src.ga4.ingest import _run_ga4_ingest

    settings = MagicMock()
    settings.report_timezone = "UTC"
    settings.ga4_property_id = "534295825"
    settings.ga4_service_account_json = "/tmp/fake.json"
    settings.ga4_conversion_event = "purchase"
    settings.ga4_event_list = ["begin_checkout"]

    monkeypatch.setattr(client_module, "_build_ga4_client", lambda path: MagicMock())
    monkeypatch.setattr(client_module, "fetch_campaign_metrics", AsyncMock(return_value=[]))
    monkeypatch.setattr(client_module, "fetch_landing_page_metrics", AsyncMock(return_value=[]))
    monkeypatch.setattr(client_module, "fetch_daily_session_totals", AsyncMock(return_value=[]))
    monkeypatch.setattr(client_module, "fetch_event_metrics", AsyncMock(side_effect=Exception("ga4 events down")))

    mock_db = AsyncMock()
    mock_db.log_ingestion_start.return_value = 1
    mock_db.upsert_ga4_metrics.return_value = 0
    mock_db.upsert_ga4_landing_pages.return_value = 0
    mock_db.upsert_ga4_daily_totals.return_value = 0

    with patch("sentry_sdk.capture_exception"):
        await _run_ga4_ingest(bot=None, db=mock_db, settings=settings, date_override="2026-05-17", skip_cache=True)

    mock_db.upsert_ga4_events.assert_not_called()
    mock_db.log_ingestion_finish.assert_called_once_with(1, "success", rows_upserted=0)


@pytest.mark.asyncio
async def test_run_ga4_ingest_skips_events_when_event_list_empty(monkeypatch):
    """settings.ga4_event_list=[] must not call fetch_event_metrics at all."""
    import src.ga4.client as client_module
    from src.ga4.ingest import _run_ga4_ingest

    settings = MagicMock()
    settings.report_timezone = "UTC"
    settings.ga4_property_id = "534295825"
    settings.ga4_service_account_json = "/tmp/fake.json"
    settings.ga4_conversion_event = "purchase"
    settings.ga4_event_list = []

    monkeypatch.setattr(client_module, "_build_ga4_client", lambda path: MagicMock())
    monkeypatch.setattr(client_module, "fetch_campaign_metrics", AsyncMock(return_value=[]))
    monkeypatch.setattr(client_module, "fetch_landing_page_metrics", AsyncMock(return_value=[]))
    monkeypatch.setattr(client_module, "fetch_daily_session_totals", AsyncMock(return_value=[]))
    mock_fetch_events = AsyncMock()
    monkeypatch.setattr(client_module, "fetch_event_metrics", mock_fetch_events)

    mock_db = AsyncMock()
    mock_db.log_ingestion_start.return_value = 1
    mock_db.upsert_ga4_metrics.return_value = 0
    mock_db.upsert_ga4_landing_pages.return_value = 0
    mock_db.upsert_ga4_daily_totals.return_value = 0

    await _run_ga4_ingest(bot=None, db=mock_db, settings=settings, date_override="2026-05-17", skip_cache=True)

    mock_fetch_events.assert_not_called()
    mock_db.upsert_ga4_events.assert_not_called()


def test_register_job_resources_sets_globals():
    """D-09: register_job_resources sets module globals for APScheduler."""
    import src.ga4.ingest as ingest_module
    mock_bot = MagicMock()
    mock_db = MagicMock()
    mock_settings = MagicMock()
    register_job_resources(mock_bot, mock_db, mock_settings)
    assert ingest_module._bot is mock_bot
    assert ingest_module._db is mock_db
    assert ingest_module._settings is mock_settings
    # Clean up module globals
    ingest_module._bot = None
    ingest_module._db = None
    ingest_module._settings = None
