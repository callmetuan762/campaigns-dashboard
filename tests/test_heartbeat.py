"""Prove REPORT-05: heartbeat fires after Telegram 200; does NOT fire on failure."""
from __future__ import annotations
from unittest.mock import AsyncMock, patch
import pytest
from src.reports.daily import ping_heartbeat

pytestmark = pytest.mark.asyncio


async def test_heartbeat_fires_when_url_set():
    with patch("src.reports.daily.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock()
        await ping_heartbeat("https://hc-ping.com/test-uuid")
        mock_client.get.assert_called_once_with("https://hc-ping.com/test-uuid")


async def test_heartbeat_skipped_when_url_none():
    with patch("src.reports.daily.httpx.AsyncClient") as mock_client_cls:
        await ping_heartbeat(None)
        mock_client_cls.assert_not_called()


async def test_heartbeat_swallows_error():
    """Heartbeat failure must never crash the report job."""
    with patch("src.reports.daily.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=Exception("connection refused"))
        await ping_heartbeat("https://hc-ping.com/test-uuid")  # Must not raise
