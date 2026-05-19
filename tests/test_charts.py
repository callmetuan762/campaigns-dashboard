"""Prove REPORT-06: chart PNG bytes are generated correctly."""
from __future__ import annotations
import pytest
from src.reports.charts import (
    generate_roas_trend_chart,
    generate_spend_trend_chart,
    generate_top_campaigns_chart,
)


def test_spend_trend_empty_returns_empty_bytes():
    assert generate_spend_trend_chart([]) == b""


def test_roas_trend_empty_returns_empty_bytes():
    assert generate_roas_trend_chart([]) == b""


def test_top_campaigns_empty_returns_empty_bytes():
    assert generate_top_campaigns_chart([]) == b""


def test_spend_trend_returns_png_bytes():
    rows = [
        {"date": "2026-05-12", "campaign_id": "c1", "spend": 100.0},
        {"date": "2026-05-13", "campaign_id": "c1", "spend": 120.0},
        {"date": "2026-05-14", "campaign_id": "c1", "spend": 90.0},
    ]
    result = generate_spend_trend_chart(rows)
    assert isinstance(result, bytes)
    assert len(result) > 0
    assert result[:4] == b"\x89PNG"


def test_roas_trend_returns_png_bytes():
    rows = [
        {"date": "2026-05-12", "campaign_id": "c1", "roas": 2.5, "spend": 100.0},
        {"date": "2026-05-13", "campaign_id": "c1", "roas": 1.8, "spend": 80.0},
    ]
    result = generate_roas_trend_chart(rows)
    assert isinstance(result, bytes)
    assert len(result) > 0
    assert result[:4] == b"\x89PNG"


def test_top_campaigns_returns_png_bytes():
    rows = [
        {"campaign_name": "Campaign Alpha", "spend": 500.0},
        {"campaign_name": "Campaign Beta", "spend": 300.0},
        {"campaign_name": "Campaign Gamma", "spend": 200.0},
    ]
    result = generate_top_campaigns_chart(rows)
    assert isinstance(result, bytes)
    assert len(result) > 0
    assert result[:4] == b"\x89PNG"
