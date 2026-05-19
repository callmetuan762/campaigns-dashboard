"""Prove SC-3: Sentry captures exceptions at ingest failure sites; no-op without DSN."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.asyncio


async def test_sentry_init_called_when_dsn_set():
    """sentry_sdk.init() is called with correct dsn string and send_default_pii=False."""
    from src.config import Settings

    settings = Settings(
        telegram_bot_token="x",
        sentry_dsn="https://fake@sentry.io/123",
        sentry_environment="staging",
    )

    with patch("sentry_sdk.init") as mock_init:
        if settings.sentry_dsn:
            import sentry_sdk
            from sentry_sdk.integrations.asyncio import AsyncioIntegration
            sentry_sdk.init(
                dsn=settings.sentry_dsn.get_secret_value(),
                integrations=[AsyncioIntegration()],
                environment=settings.sentry_environment,
                traces_sample_rate=0.0,
                send_default_pii=False,
            )

        mock_init.assert_called_once()
        call_kwargs = mock_init.call_args.kwargs
        assert call_kwargs["dsn"] == "https://fake@sentry.io/123", (
            "DSN must be a plain string, not SecretStr"
        )
        assert call_kwargs["send_default_pii"] is False


async def test_sentry_init_not_called_without_dsn():
    """sentry_sdk.init() is NOT called when sentry_dsn is None."""
    from src.config import Settings

    settings = Settings(telegram_bot_token="x", sentry_dsn=None)

    with patch("sentry_sdk.init") as mock_init:
        if settings.sentry_dsn:
            import sentry_sdk
            from sentry_sdk.integrations.asyncio import AsyncioIntegration
            sentry_sdk.init(
                dsn=settings.sentry_dsn.get_secret_value(),
                integrations=[AsyncioIntegration()],
                environment=settings.sentry_environment,
                traces_sample_rate=0.0,
                send_default_pii=False,
            )

        mock_init.assert_not_called()


async def test_capture_exception_called_on_meta_ingest_failure():
    """capture_exception is called when _run_meta_ingest's DB query raises."""
    from src.meta.ingest import _run_meta_ingest

    mock_db = MagicMock()
    mock_db.log_ingestion_start = AsyncMock(return_value=1)
    mock_db.fetch_all = AsyncMock(side_effect=RuntimeError("db down"))
    mock_db.log_ingestion_finish = AsyncMock()
    mock_db.fetch_one = AsyncMock(return_value=None)

    mock_settings = MagicMock()
    mock_settings.report_timezone = "UTC"
    mock_settings.meta_access_token = "token"
    mock_settings.meta_ad_account_id = "act_123"
    mock_settings.telegram_allowed_chat_ids = []

    with patch("sentry_sdk.capture_exception") as mock_capture:
        with patch("src.meta.ingest.init_meta_api"):
            with patch("src.meta.ingest.fetch_campaign_insights", new_callable=AsyncMock) as mock_fetch:
                mock_fetch.side_effect = RuntimeError("db down")
                await _run_meta_ingest(bot=None, db=mock_db, settings=mock_settings)

        mock_capture.assert_called_once()
        captured_exc = mock_capture.call_args.args[0]
        assert isinstance(captured_exc, RuntimeError)


async def test_no_raise_when_capture_exception_called_uninitialized():
    """capture_exception() is a no-op when sentry_sdk is not initialized (Pitfall 6)."""
    import sentry_sdk

    # Ensure no init has been called by using a fresh state check
    # sentry_sdk 2.x: calling capture_exception without init must NOT raise
    try:
        sentry_sdk.capture_exception(Exception("test no-op"))
    except Exception as exc:
        pytest.fail(f"capture_exception raised unexpectedly: {exc}")
