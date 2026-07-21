"""Tests for src/meta/client.py Phase C additions — pixel stats + best-effort EMQ.

_parse_pixel_stats_rows is pure and tested directly. fetch_pixel_event_counts /
fetch_pixel_emq are tested with the SDK / HTTP call points monkeypatched, mirroring
how test_meta_client.py keeps the parsing logic separate from the network layer.
"""
from __future__ import annotations

import pytest

from facebook_business.exceptions import FacebookRequestError

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# _parse_pixel_stats_rows — pure
# ---------------------------------------------------------------------------

def test_parse_pixel_stats_rows_sums_counts_per_event():
    from src.meta.client import _parse_pixel_stats_rows

    rows = [
        {"event": "Purchase", "count": "10"},
        {"event": "Purchase", "count": "5"},
        {"event": "Lead", "count": "3"},
    ]
    result = _parse_pixel_stats_rows(rows)
    assert result == {"Purchase": 15, "Lead": 3}


def test_parse_pixel_stats_rows_handles_event_name_key():
    from src.meta.client import _parse_pixel_stats_rows

    rows = [{"event_name": "InitiateCheckout", "count": 7}]
    assert _parse_pixel_stats_rows(rows) == {"InitiateCheckout": 7}


def test_parse_pixel_stats_rows_skips_missing_event_name():
    from src.meta.client import _parse_pixel_stats_rows

    rows = [{"count": "10"}, {"event": "Purchase", "count": "5"}]
    assert _parse_pixel_stats_rows(rows) == {"Purchase": 5}


def test_parse_pixel_stats_rows_tolerates_bad_count():
    from src.meta.client import _parse_pixel_stats_rows

    rows = [{"event": "Purchase", "count": "not-a-number"}]
    assert _parse_pixel_stats_rows(rows) == {"Purchase": 0}


def test_parse_pixel_stats_rows_empty_input():
    from src.meta.client import _parse_pixel_stats_rows

    assert _parse_pixel_stats_rows(None) == {}
    assert _parse_pixel_stats_rows([]) == {}


# ---------------------------------------------------------------------------
# fetch_pixel_event_counts — mocked SDK layer
# ---------------------------------------------------------------------------

async def test_fetch_pixel_event_counts_merges_web_and_server(monkeypatch):
    from src.meta import client as client_module

    def fake_fetch(pixel_id, date_iso, event_source):
        if event_source == "WEB_ONLY":
            return [{"event": "Purchase", "count": "100"}]
        return [{"event": "Purchase", "count": "80"}]

    monkeypatch.setattr(client_module, "_fetch_pixel_event_counts_sync", fake_fetch)

    result = await client_module.fetch_pixel_event_counts("pixel123", "2026-05-18")
    assert result == {"Purchase": {"browser_count": 100, "server_count": 80}}


async def test_fetch_pixel_event_counts_handles_event_only_on_one_side(monkeypatch):
    from src.meta import client as client_module

    def fake_fetch(pixel_id, date_iso, event_source):
        if event_source == "WEB_ONLY":
            return [{"event": "Lead", "count": "10"}]
        return []

    monkeypatch.setattr(client_module, "_fetch_pixel_event_counts_sync", fake_fetch)

    result = await client_module.fetch_pixel_event_counts("pixel123", "2026-05-18")
    assert result == {"Lead": {"browser_count": 10, "server_count": 0}}


async def test_fetch_pixel_event_counts_one_side_failing_does_not_lose_the_other(monkeypatch):
    from src.meta import client as client_module

    def fake_fetch(pixel_id, date_iso, event_source):
        if event_source == "WEB_ONLY":
            return [{"event": "Purchase", "count": "100"}]
        raise FacebookRequestError("server error", {}, 500, {}, "{}")

    monkeypatch.setattr(client_module, "_fetch_pixel_event_counts_sync", fake_fetch)

    result = await client_module.fetch_pixel_event_counts("pixel123", "2026-05-18")
    assert result == {"Purchase": {"browser_count": 100, "server_count": 0}}


# ---------------------------------------------------------------------------
# fetch_pixel_emq — best-effort, mocked HTTP layer
# ---------------------------------------------------------------------------

async def test_fetch_pixel_emq_parses_web_array(monkeypatch):
    from src.meta import client as client_module

    def fake_call(pixel_id, token):
        return {
            "web": [
                {
                    "event_name": "Purchase",
                    "event_match_quality": 6.4,
                    "deduplication_rate": 0.72,
                }
            ]
        }

    monkeypatch.setattr(client_module, "_fetch_dataset_quality_sync", fake_call)

    result = await client_module.fetch_pixel_emq("pixel123", "token")
    assert result == {"Purchase": {"emq_score": 6.4, "dedup_rate": 0.72}}


async def test_fetch_pixel_emq_returns_empty_on_any_error(monkeypatch):
    """Permission errors / 404s / network failures all degrade to {} (never raise)."""
    from src.meta import client as client_module

    def fake_call(pixel_id, token):
        raise RuntimeError("403 Forbidden — Advanced Access required")

    monkeypatch.setattr(client_module, "_fetch_dataset_quality_sync", fake_call)

    result = await client_module.fetch_pixel_emq("pixel123", "token")
    assert result == {}


async def test_fetch_pixel_emq_handles_missing_web_key(monkeypatch):
    from src.meta import client as client_module

    monkeypatch.setattr(client_module, "_fetch_dataset_quality_sync", lambda p, t: {})

    result = await client_module.fetch_pixel_emq("pixel123", "token")
    assert result == {}
