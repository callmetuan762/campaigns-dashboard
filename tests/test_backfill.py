"""Prove SC-1: backfill CLI iterates dates correctly and suppresses alerts/cache."""
from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.backfill import _date_range, backfill_main

pytestmark = pytest.mark.asyncio


# ---- Date range helpers ----

def test_date_range_inclusive():
    """_date_range returns all dates from start to end inclusive (3 days)."""
    result = _date_range(date(2026, 5, 1), date(2026, 5, 3))
    assert result == ["2026-05-01", "2026-05-02", "2026-05-03"]


def test_date_range_single_day():
    """_date_range with equal start/end returns a list of exactly 1 element."""
    result = _date_range(date(2026, 5, 1), date(2026, 5, 1))
    assert result == ["2026-05-01"]


# ---- backfill_main: per-source loop call counts ----

async def test_backfill_meta_calls_ingest_per_date():
    """backfill_main(source='meta', 3-day window) calls run_meta_ingest_for_date 3 times."""
    mock_settings = MagicMock()
    mock_settings.log_level = "INFO"
    mock_db_instance = AsyncMock()

    with (
        patch("src.backfill.load_settings", return_value=mock_settings),
        patch("src.backfill.configure_logging"),
        patch("src.backfill.DBClient", return_value=mock_db_instance),
        patch("src.meta.ingest.run_meta_ingest_for_date", new_callable=AsyncMock) as mock_meta,
    ):
        await backfill_main(
            source="meta",
            start=date(2026, 5, 1),
            end=date(2026, 5, 3),
        )

    assert mock_meta.call_count == 3
    call_dates = [call.args[2] for call in mock_meta.call_args_list]
    assert call_dates == ["2026-05-01", "2026-05-02", "2026-05-03"]


async def test_backfill_ga4_calls_ingest_per_date():
    """backfill_main(source='ga4', 3-day window) calls run_ga4_ingest_for_date 3 times."""
    mock_settings = MagicMock()
    mock_settings.log_level = "INFO"
    mock_db_instance = AsyncMock()

    with (
        patch("src.backfill.load_settings", return_value=mock_settings),
        patch("src.backfill.configure_logging"),
        patch("src.backfill.DBClient", return_value=mock_db_instance),
        patch("src.ga4.ingest.run_ga4_ingest_for_date", new_callable=AsyncMock) as mock_ga4,
    ):
        await backfill_main(
            source="ga4",
            start=date(2026, 5, 1),
            end=date(2026, 5, 3),
        )

    assert mock_ga4.call_count == 3
    call_dates = [call.args[2] for call in mock_ga4.call_args_list]
    assert call_dates == ["2026-05-01", "2026-05-02", "2026-05-03"]


async def test_backfill_all_calls_both_sources():
    """backfill_main(source='all', 2-day window) calls both meta and ga4 wrappers 2 times each."""
    mock_settings = MagicMock()
    mock_settings.log_level = "INFO"
    mock_db_instance = AsyncMock()

    with (
        patch("src.backfill.load_settings", return_value=mock_settings),
        patch("src.backfill.configure_logging"),
        patch("src.backfill.DBClient", return_value=mock_db_instance),
        patch("src.meta.ingest.run_meta_ingest_for_date", new_callable=AsyncMock) as mock_meta,
        patch("src.ga4.ingest.run_ga4_ingest_for_date", new_callable=AsyncMock) as mock_ga4,
    ):
        await backfill_main(
            source="all",
            start=date(2026, 5, 1),
            end=date(2026, 5, 2),
        )

    assert mock_meta.call_count == 2
    assert mock_ga4.call_count == 2


async def test_dry_run_does_not_call_ingest():
    """backfill_main(dry_run=True) logs dates and does NOT call either ingest wrapper."""
    mock_settings = MagicMock()
    mock_settings.log_level = "INFO"

    with (
        patch("src.backfill.load_settings", return_value=mock_settings),
        patch("src.backfill.configure_logging"),
        patch("src.meta.ingest.run_meta_ingest_for_date", new_callable=AsyncMock) as mock_meta,
        patch("src.ga4.ingest.run_ga4_ingest_for_date", new_callable=AsyncMock) as mock_ga4,
    ):
        await backfill_main(
            source="all",
            start=date(2026, 5, 1),
            end=date(2026, 5, 3),
            dry_run=True,
        )

    mock_meta.assert_not_called()
    mock_ga4.assert_not_called()


# ---- Wrapper flag assertions ----

async def test_suppress_alerts_true_in_meta_wrapper():
    """run_meta_ingest_for_date always calls _run_meta_ingest with suppress_alerts=True."""
    from src.meta.ingest import run_meta_ingest_for_date

    mock_db = AsyncMock()
    mock_settings = MagicMock()

    with patch("src.meta.ingest._run_meta_ingest", new_callable=AsyncMock) as mock_run:
        await run_meta_ingest_for_date(mock_db, mock_settings, "2026-05-01")

    mock_run.assert_called_once()
    _, kwargs = mock_run.call_args
    assert kwargs.get("suppress_alerts") is True
    assert kwargs.get("date_override") == "2026-05-01"
    assert kwargs.get("bot") is None


async def test_skip_cache_true_in_ga4_wrapper():
    """run_ga4_ingest_for_date always calls _run_ga4_ingest with skip_cache=True."""
    from src.ga4.ingest import run_ga4_ingest_for_date

    mock_db = AsyncMock()
    mock_settings = MagicMock()

    with patch("src.ga4.ingest._run_ga4_ingest", new_callable=AsyncMock) as mock_run:
        await run_ga4_ingest_for_date(mock_db, mock_settings, "2026-05-01")

    mock_run.assert_called_once()
    _, kwargs = mock_run.call_args
    assert kwargs.get("skip_cache") is True
    assert kwargs.get("date_override") == "2026-05-01"
    assert kwargs.get("bot") is None
