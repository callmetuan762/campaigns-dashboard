"""Prove REPORT-02: TL;DR generated when API available; None returned on failure."""
from __future__ import annotations
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from src.ai.tldr import generate_tldr

pytestmark = pytest.mark.asyncio

_SAMPLE_ROWS = [
    {"campaign_name": "Test Campaign", "spend": 100.0, "roas": 2.5, "meta_purchases_7dclick": 10},
]


async def test_tldr_returns_string_on_success():
    """REPORT-02: TL;DR returned as string when Anthropic API succeeds."""
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="• Signal 1\n• Signal 2\n• Signal 3")]
    with patch("src.ai.tldr.AsyncAnthropic") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value = mock_client
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        result = await generate_tldr("fake-key", _SAMPLE_ROWS, "2026-05-18")
        assert isinstance(result, str)
        assert len(result) > 0


async def test_tldr_returns_none_on_api_status_error():
    """D-23: Graceful degradation — returns None on APIStatusError."""
    from anthropic import APIStatusError
    with patch("src.ai.tldr.AsyncAnthropic") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value = mock_client
        mock_client.messages.create = AsyncMock(
            side_effect=APIStatusError("rate limit", response=MagicMock(status_code=429), body={})
        )
        result = await generate_tldr("fake-key", _SAMPLE_ROWS, "2026-05-18")
        assert result is None


async def test_tldr_returns_none_on_connection_error():
    """D-23: Returns None on APIConnectionError."""
    from anthropic import APIConnectionError
    with patch("src.ai.tldr.AsyncAnthropic") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value = mock_client
        mock_client.messages.create = AsyncMock(
            side_effect=APIConnectionError(request=MagicMock())
        )
        result = await generate_tldr("fake-key", _SAMPLE_ROWS, "2026-05-18")
        assert result is None


async def test_tldr_empty_rows_returns_none():
    result = await generate_tldr("fake-key", [], "2026-05-18")
    assert result is None


async def test_tldr_prompt_contains_data_tags():
    """D-23 / CLAUDE.md: Campaign data must be wrapped in <data>...</data> tags."""
    captured_prompt = {}

    async def capture_create(**kwargs):
        captured_prompt["messages"] = kwargs.get("messages", [])
        mock_resp = MagicMock()
        mock_resp.content = [MagicMock(text="summary")]
        return mock_resp

    with patch("src.ai.tldr.AsyncAnthropic") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value = mock_client
        mock_client.messages.create = capture_create
        await generate_tldr("fake-key", _SAMPLE_ROWS, "2026-05-18")
        content = captured_prompt["messages"][0]["content"]
        assert "<data>" in content, "Campaign data must be inside <data> tags"
        assert "</data>" in content
