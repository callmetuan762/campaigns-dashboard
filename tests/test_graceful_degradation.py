"""Prove SC-2: per-source failures degrade gracefully with unavailability notices."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.reports.builder import build_daily_report_html, build_weekly_report_html


# ---------------------------------------------------------------------------
# Builder unit tests (synchronous — no db_client needed)
# ---------------------------------------------------------------------------

def test_daily_report_meta_unavailable_notice():
    """SC-2: meta_available=False produces HTML containing Meta unavailability notice."""
    result = build_daily_report_html([], None, "2026-05-18", meta_available=False)
    assert "Meta Ads data unavailable" in result, f"Expected Meta notice, got: {result[:300]}"


def test_daily_report_ga4_unavailable_notice():
    """SC-2: ga4_available=False produces HTML containing GA4 unavailability notice."""
    result = build_daily_report_html([], None, "2026-05-18", ga4_available=False)
    assert "GA4 data unavailable" in result, f"Expected GA4 notice, got: {result[:300]}"


def test_daily_report_both_unavailable_no_crash():
    """SC-2: Both flags False produces a non-empty report without raising."""
    result = build_daily_report_html([], None, "2026-05-18", meta_available=False, ga4_available=False)
    assert isinstance(result, str)
    assert len(result) > 0


def test_weekly_report_meta_unavailable_notice():
    """SC-2: build_weekly_report_html with meta_available=False contains Meta notice."""
    result = build_weekly_report_html([], [], None, "2026-05-18", meta_available=False)
    assert "Meta Ads data unavailable" in result, f"Expected Meta notice, got: {result[:300]}"


def test_weekly_report_ga4_unavailable_notice():
    """SC-2: build_weekly_report_html with ga4_available=False contains GA4 notice."""
    result = build_weekly_report_html([], [], None, "2026-05-18", ga4_available=False)
    assert "GA4 data unavailable" in result, f"Expected GA4 notice, got: {result[:300]}"


# ---------------------------------------------------------------------------
# Report job independence tests (async — minimal mocking, no real DB/bot)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_daily_report_completes_when_meta_query_fails():
    """SC-2: _run_daily_report completes without raising when Meta DB query fails.

    The Meta guarded block catches the exception; GA4 block proceeds normally;
    build_daily_report_html is still called (report assembled with meta_available=False).
    """
    from src.reports.daily import _run_daily_report

    # Settings stub
    settings = MagicMock()
    settings.report_timezone = "UTC"
    settings.telegram_allowed_chat_ids = [-1001]
    settings.anthropic_api_key = None
    settings.heartbeat_url = None

    # DB stub: first fetch_all (Meta query) raises; subsequent calls return []
    call_count = 0

    async def fetch_all_side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise Exception("db error — meta query")
        return []

    mock_db = MagicMock()
    mock_db.fetch_all = AsyncMock(side_effect=fetch_all_side_effect)
    mock_db.fetch_one = AsyncMock(return_value=None)

    # Bot stub
    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock()
    mock_bot.send_photo = AsyncMock()

    with patch("src.reports.daily.build_daily_report_html") as mock_builder, \
         patch("sentry_sdk.capture_exception"):
        mock_builder.return_value = "<b>Report</b>"
        with patch("src.reports.daily.split_html_message", return_value=["<b>Report</b>"]):
            # Must not raise
            await _run_daily_report(mock_bot, mock_db, settings)

    # build_daily_report_html was called — report was assembled despite Meta failure
    mock_builder.assert_called_once()
    call_kwargs = mock_builder.call_args[1]
    assert call_kwargs.get("meta_available") is False, "meta_available should be False after query failure"


@pytest.mark.asyncio
async def test_daily_report_completes_when_ga4_query_fails():
    """SC-2: _run_daily_report completes without raising when GA4 DB query fails.

    Meta block succeeds; GA4 guarded block catches the exception;
    build_daily_report_html is still called (report assembled with ga4_available=False).
    """
    from src.reports.daily import _run_daily_report

    settings = MagicMock()
    settings.report_timezone = "UTC"
    settings.telegram_allowed_chat_ids = [-1001]
    settings.anthropic_api_key = None
    settings.heartbeat_url = None

    # DB stub: first two fetch_all calls (Meta queries) succeed; third (first GA4) raises
    call_count = 0

    async def fetch_all_side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            return []  # Meta queries succeed (empty = zero-spend day, not failure)
        raise Exception("db error — ga4 query")

    mock_db = MagicMock()
    mock_db.fetch_all = AsyncMock(side_effect=fetch_all_side_effect)
    # fetch_one for ingestion_log (empty rows path): return None so meta_available stays True
    mock_db.fetch_one = AsyncMock(return_value=None)

    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock()
    mock_bot.send_photo = AsyncMock()

    with patch("src.reports.daily.build_daily_report_html") as mock_builder, \
         patch("sentry_sdk.capture_exception"):
        mock_builder.return_value = "<b>Report</b>"
        with patch("src.reports.daily.split_html_message", return_value=["<b>Report</b>"]):
            await _run_daily_report(mock_bot, mock_db, settings)

    mock_builder.assert_called_once()
    call_kwargs = mock_builder.call_args[1]
    assert call_kwargs.get("ga4_available") is False, "ga4_available should be False after query failure"
    # Meta should still be available
    assert call_kwargs.get("meta_available") is True, "meta_available should remain True"
