"""Tests for src/meta/pixel_ingest.py — credential guard, upsert wiring, never-crash contract.

Mirrors tests/test_shopify_ingest.py's mocking pattern (MagicMock settings, AsyncMock db).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Credential guard — clean no-op when META_PIXEL_ID unset
# ---------------------------------------------------------------------------

async def test_pixel_health_ingest_skips_when_pixel_id_unset():
    from src.meta.pixel_ingest import run_pixel_health_ingest_for_date

    settings = MagicMock()
    settings.meta_pixel_id = None
    mock_db = AsyncMock()

    await run_pixel_health_ingest_for_date(mock_db, settings, "2026-05-18")

    mock_db.upsert_pixel_health.assert_not_called()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

async def test_pixel_health_ingest_happy_path_upserts_rows(monkeypatch):
    from src.meta import pixel_ingest as ingest_module

    settings = MagicMock()
    settings.meta_pixel_id = "pixel123"
    secret = MagicMock()
    secret.get_secret_value.return_value = "real-token"
    settings.meta_access_token = secret

    mock_db = AsyncMock()
    mock_db.upsert_pixel_health.return_value = 2

    monkeypatch.setattr(ingest_module, "init_meta_api", lambda s: None)

    async def fake_fetch_counts(pixel_id, date_iso):
        return {
            "Purchase": {"browser_count": 100, "server_count": 80},
            "Lead": {"browser_count": 10, "server_count": 5},
        }

    async def fake_fetch_emq(pixel_id, token):
        return {"Purchase": {"emq_score": 6.4, "dedup_rate": 0.72}}

    monkeypatch.setattr(ingest_module, "fetch_pixel_event_counts", fake_fetch_counts)
    monkeypatch.setattr(ingest_module, "fetch_pixel_emq", fake_fetch_emq)

    await ingest_module.run_pixel_health_ingest_for_date(mock_db, settings, "2026-05-18")

    mock_db.upsert_pixel_health.assert_called_once()
    rows = mock_db.upsert_pixel_health.call_args[0][0]
    by_event = {r["event_name"]: r for r in rows}
    assert by_event["Purchase"]["browser_count"] == 100
    assert by_event["Purchase"]["server_count"] == 80
    assert by_event["Purchase"]["emq_score"] == 6.4
    assert by_event["Purchase"]["dedup_rate"] == 0.72
    assert by_event["Lead"]["emq_score"] is None  # no EMQ data for Lead
    assert all(r["date"] == "2026-05-18" for r in rows)


async def test_pixel_health_ingest_no_events_skips_upsert(monkeypatch):
    from src.meta import pixel_ingest as ingest_module

    settings = MagicMock()
    settings.meta_pixel_id = "pixel123"
    settings.meta_access_token = None
    mock_db = AsyncMock()

    monkeypatch.setattr(ingest_module, "init_meta_api", lambda s: None)

    async def fake_fetch_counts(pixel_id, date_iso):
        return {}

    monkeypatch.setattr(ingest_module, "fetch_pixel_event_counts", fake_fetch_counts)

    await ingest_module.run_pixel_health_ingest_for_date(mock_db, settings, "2026-05-18")

    mock_db.upsert_pixel_health.assert_not_called()


async def test_pixel_health_ingest_skips_emq_when_no_token(monkeypatch):
    from src.meta import pixel_ingest as ingest_module

    settings = MagicMock()
    settings.meta_pixel_id = "pixel123"
    settings.meta_access_token = None
    mock_db = AsyncMock()
    mock_db.upsert_pixel_health.return_value = 1

    monkeypatch.setattr(ingest_module, "init_meta_api", lambda s: None)

    async def fake_fetch_counts(pixel_id, date_iso):
        return {"Purchase": {"browser_count": 100, "server_count": 80}}

    emq_called = False

    async def fake_fetch_emq(pixel_id, token):
        nonlocal emq_called
        emq_called = True
        return {}

    monkeypatch.setattr(ingest_module, "fetch_pixel_event_counts", fake_fetch_counts)
    monkeypatch.setattr(ingest_module, "fetch_pixel_emq", fake_fetch_emq)

    await ingest_module.run_pixel_health_ingest_for_date(mock_db, settings, "2026-05-18")

    assert emq_called is False, "fetch_pixel_emq must not be called without a token"
    mock_db.upsert_pixel_health.assert_called_once()


# ---------------------------------------------------------------------------
# Never-crash contract (CLAUDE.md graceful degradation)
# ---------------------------------------------------------------------------

async def test_pixel_health_ingest_never_raises_on_fetch_error(monkeypatch):
    from src.meta import pixel_ingest as ingest_module

    settings = MagicMock()
    settings.meta_pixel_id = "pixel123"
    settings.meta_access_token = None
    mock_db = AsyncMock()

    monkeypatch.setattr(ingest_module, "init_meta_api", lambda s: None)

    async def failing_fetch(pixel_id, date_iso):
        raise RuntimeError("stats endpoint down")

    monkeypatch.setattr(ingest_module, "fetch_pixel_event_counts", failing_fetch)

    # Must not raise.
    await ingest_module.run_pixel_health_ingest_for_date(mock_db, settings, "2026-05-18")

    mock_db.upsert_pixel_health.assert_not_called()


async def test_pixel_health_ingest_never_raises_on_init_error(monkeypatch):
    from src.meta import pixel_ingest as ingest_module

    settings = MagicMock()
    settings.meta_pixel_id = "pixel123"
    mock_db = AsyncMock()

    def failing_init(s):
        raise RuntimeError("bad credentials")

    monkeypatch.setattr(ingest_module, "init_meta_api", failing_init)

    await ingest_module.run_pixel_health_ingest_for_date(mock_db, settings, "2026-05-18")

    mock_db.upsert_pixel_health.assert_not_called()


# ---------------------------------------------------------------------------
# _resolve_access_token helper
# ---------------------------------------------------------------------------

def test_resolve_access_token_handles_secretstr_like_object():
    from src.meta.pixel_ingest import _resolve_access_token

    settings = MagicMock()
    secret = MagicMock()
    secret.get_secret_value.return_value = "plain-token"
    settings.meta_access_token = secret
    assert _resolve_access_token(settings) == "plain-token"


def test_resolve_access_token_handles_plain_string():
    from src.meta.pixel_ingest import _resolve_access_token

    settings = MagicMock()
    settings.meta_access_token = "plain-token"
    assert _resolve_access_token(settings) == "plain-token"


def test_resolve_access_token_handles_none():
    from src.meta.pixel_ingest import _resolve_access_token

    settings = MagicMock()
    settings.meta_access_token = None
    assert _resolve_access_token(settings) is None
