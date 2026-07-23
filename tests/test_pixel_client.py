"""Tests for src/meta/client.py Phase C additions — pixel stats + best-effort EMQ.

_parse_pixel_stats_rows is pure and tested directly. fetch_pixel_event_counts /
fetch_pixel_emq are tested with the SDK / HTTP call points monkeypatched, mirroring
how test_meta_client.py keeps the parsing logic separate from the network layer.

Fixture shape below is the REAL /{pixel_id}/stats aggregation=event response, confirmed
live against the Graph API: a list of hourly buckets, each with a nested 'data' list of
{"value": <event name>, "count": <int>} items — NOT the flat {"event": ..., "count": ...}
shape previously (incorrectly) assumed here.
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
        {"start_time": "2026-07-17T00:00:00+0000", "data": [{"value": "Purchase", "count": 10}]},
        {"start_time": "2026-07-17T01:00:00+0000", "data": [{"value": "Purchase", "count": 5}]},
        {"start_time": "2026-07-17T02:00:00+0000", "data": [{"value": "Lead", "count": 3}]},
    ]
    result = _parse_pixel_stats_rows(rows)
    assert result == {"Purchase": 15, "Lead": 3}


def test_parse_pixel_stats_rows_sums_across_multiple_buckets_same_event():
    """Two hourly buckets each reporting InitiateCheckout must sum, not overwrite."""
    from src.meta.client import _parse_pixel_stats_rows

    rows = [
        {"start_time": "2026-07-17T00:00:00+0000", "data": [{"value": "InitiateCheckout", "count": 2}]},
        {"start_time": "2026-07-17T03:00:00+0000", "data": [{"value": "InitiateCheckout", "count": 2}]},
    ]
    assert _parse_pixel_stats_rows(rows) == {"InitiateCheckout": 4}


def test_parse_pixel_stats_rows_handles_multiple_events_per_bucket():
    from src.meta.client import _parse_pixel_stats_rows

    rows = [
        {
            "start_time": "2026-07-17T00:00:00+0000",
            "data": [
                {"value": "AddToCart", "count": 3},
                {"value": "AddPaymentInfo", "count": 1},
                {"value": "InitiateCheckout", "count": 1},
                {"value": "Purchase", "count": 1},
            ],
        }
    ]
    assert _parse_pixel_stats_rows(rows) == {
        "AddToCart": 3,
        "AddPaymentInfo": 1,
        "InitiateCheckout": 1,
        "Purchase": 1,
    }


def test_parse_pixel_stats_rows_tolerates_string_counts():
    from src.meta.client import _parse_pixel_stats_rows

    rows = [{"start_time": "2026-07-17T00:00:00+0000", "data": [{"value": "Purchase", "count": "7"}]}]
    assert _parse_pixel_stats_rows(rows) == {"Purchase": 7}


def test_parse_pixel_stats_rows_skips_missing_event_value():
    from src.meta.client import _parse_pixel_stats_rows

    rows = [
        {"start_time": "2026-07-17T00:00:00+0000", "data": [{"count": 10}, {"value": "Purchase", "count": 5}]}
    ]
    assert _parse_pixel_stats_rows(rows) == {"Purchase": 5}


def test_parse_pixel_stats_rows_tolerates_bad_count():
    from src.meta.client import _parse_pixel_stats_rows

    rows = [{"start_time": "2026-07-17T00:00:00+0000", "data": [{"value": "Purchase", "count": "not-a-number"}]}]
    assert _parse_pixel_stats_rows(rows) == {"Purchase": 0}


def test_parse_pixel_stats_rows_skips_bucket_with_missing_data_list():
    """A bucket with no/malformed 'data' key is skipped, not fatal to the whole day."""
    from src.meta.client import _parse_pixel_stats_rows

    rows = [
        {"start_time": "2026-07-17T00:00:00+0000"},  # no 'data' key at all
        {"start_time": "2026-07-17T01:00:00+0000", "data": "not-a-list"},
        {"start_time": "2026-07-17T02:00:00+0000", "data": [{"value": "Lead", "count": 1}]},
    ]
    assert _parse_pixel_stats_rows(rows) == {"Lead": 1}


def test_parse_pixel_stats_rows_empty_input():
    from src.meta.client import _parse_pixel_stats_rows

    assert _parse_pixel_stats_rows(None) == {}
    assert _parse_pixel_stats_rows([]) == {}


# ---------------------------------------------------------------------------
# _day_bounds_unix — pure
# ---------------------------------------------------------------------------

def test_day_bounds_unix_is_midnight_to_next_midnight_utc():
    from src.meta.client import _day_bounds_unix

    start_ts, end_ts = _day_bounds_unix("2026-07-17")
    assert end_ts - start_ts == 86400
    # 2026-07-17T00:00:00Z
    assert start_ts == 1784246400
    # 2026-07-18T00:00:00Z
    assert end_ts == 1784332800


# ---------------------------------------------------------------------------
# _fetch_pixel_event_counts_sync — passes Unix timestamps, not ISO date strings
# ---------------------------------------------------------------------------

def test_fetch_pixel_event_counts_sync_passes_unix_timestamps(monkeypatch):
    """Regression test: the /{pixel_id}/stats endpoint silently returns zero rows for
    ISO date-string start_time/end_time — it requires Unix timestamps."""
    from facebook_business.adobjects import adspixel as adspixel_module

    captured_params = {}

    class FakeAdsPixel:
        def __init__(self, pixel_id):
            self.pixel_id = pixel_id

        def get_stats(self, fields, params):
            captured_params.update(params)
            return []

    monkeypatch.setattr(adspixel_module, "AdsPixel", FakeAdsPixel)

    from src.meta.client import _fetch_pixel_event_counts_sync

    _fetch_pixel_event_counts_sync("pixel123", "2026-07-17", "WEB_ONLY")

    assert isinstance(captured_params["start_time"], int)
    assert isinstance(captured_params["end_time"], int)
    assert captured_params["end_time"] - captured_params["start_time"] == 86400
    assert captured_params["start_time"] == 1784246400  # 2026-07-17T00:00:00Z


# ---------------------------------------------------------------------------
# fetch_pixel_event_counts — mocked SDK layer
# ---------------------------------------------------------------------------

async def test_fetch_pixel_event_counts_merges_web_and_server(monkeypatch):
    from src.meta import client as client_module

    def fake_fetch(pixel_id, date_iso, event_source):
        if event_source == "WEB_ONLY":
            return [{"start_time": "2026-05-18T00:00:00+0000", "data": [{"value": "Purchase", "count": 100}]}]
        return [{"start_time": "2026-05-18T00:00:00+0000", "data": [{"value": "Purchase", "count": 80}]}]

    monkeypatch.setattr(client_module, "_fetch_pixel_event_counts_sync", fake_fetch)

    result = await client_module.fetch_pixel_event_counts("pixel123", "2026-05-18")
    assert result == {"Purchase": {"browser_count": 100, "server_count": 80}}


async def test_fetch_pixel_event_counts_handles_event_only_on_one_side(monkeypatch):
    from src.meta import client as client_module

    def fake_fetch(pixel_id, date_iso, event_source):
        if event_source == "WEB_ONLY":
            return [{"start_time": "2026-05-18T00:00:00+0000", "data": [{"value": "Lead", "count": 10}]}]
        return []

    monkeypatch.setattr(client_module, "_fetch_pixel_event_counts_sync", fake_fetch)

    result = await client_module.fetch_pixel_event_counts("pixel123", "2026-05-18")
    assert result == {"Lead": {"browser_count": 10, "server_count": 0}}


async def test_fetch_pixel_event_counts_one_side_failing_does_not_lose_the_other(monkeypatch):
    from src.meta import client as client_module

    def fake_fetch(pixel_id, date_iso, event_source):
        if event_source == "WEB_ONLY":
            return [{"start_time": "2026-05-18T00:00:00+0000", "data": [{"value": "Purchase", "count": 100}]}]
        raise FacebookRequestError("server error", {}, 500, {}, "{}")

    monkeypatch.setattr(client_module, "_fetch_pixel_event_counts_sync", fake_fetch)

    result = await client_module.fetch_pixel_event_counts("pixel123", "2026-05-18")
    assert result == {"Purchase": {"browser_count": 100, "server_count": 0}}


async def test_fetch_pixel_event_counts_sums_multiple_hourly_buckets(monkeypatch):
    """A full day's response is multiple hourly buckets; counts must sum across them."""
    from src.meta import client as client_module

    def fake_fetch(pixel_id, date_iso, event_source):
        if event_source == "WEB_ONLY":
            return [
                {"start_time": "2026-05-18T00:00:00+0000", "data": [{"value": "InitiateCheckout", "count": 2}]},
                {"start_time": "2026-05-18T03:00:00+0000", "data": [{"value": "InitiateCheckout", "count": 2}]},
            ]
        return []

    monkeypatch.setattr(client_module, "_fetch_pixel_event_counts_sync", fake_fetch)

    result = await client_module.fetch_pixel_event_counts("pixel123", "2026-05-18")
    assert result == {"InitiateCheckout": {"browser_count": 4, "server_count": 0}}


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
