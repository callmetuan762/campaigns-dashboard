"""Prove REPORT-01, REPORT-04: daily report HTML escaping and structure."""
from __future__ import annotations
import pytest
from src.reports.builder import build_daily_report_html


def test_daily_report_html_escape():
    """REPORT-04: Campaign names with HTML chars must be escaped."""
    rows = [
        {
            "campaign_id": "c1",
            "campaign_name": '<script>alert("xss")</script>',
            "spend": 100.0,
            "roas": 2.0,
            "meta_purchases_7dclick": 5,
            "clicks": 50,
            "impressions": 1000,
        }
    ]
    result = build_daily_report_html(rows, None, "2026-05-18")
    assert "<script>" not in result, "Raw HTML injection must not appear in output"


def test_daily_report_tldr_at_top():
    """REPORT-02: TL;DR section must appear before the Overall metrics section."""
    rows = [
        {"campaign_id": "c1", "campaign_name": "Camp A", "spend": 100.0,
         "roas": 2.0, "meta_purchases_7dclick": 5, "clicks": 50, "impressions": 1000}
    ]
    result = build_daily_report_html(rows, "Key insight here", "2026-05-18")
    tldr_pos = result.index("TL;DR")
    overall_pos = result.index("Overall")
    assert tldr_pos < overall_pos, "TL;DR must appear before Overall section"


def test_daily_report_unavailable_notice_when_no_tldr():
    """D-23: When TL;DR is None, include unavailable notice."""
    result = build_daily_report_html([], None, "2026-05-18")
    assert "unavailable" in result.lower()


def test_daily_report_no_data_notice():
    """D-02: Report with empty rows includes data unavailable notice."""
    result = build_daily_report_html([], None, "2026-05-18")
    assert "No data available" in result or "unavailable" in result.lower()


def test_daily_report_uses_html_bold():
    """REPORT-04: Bold headers use <b> tags, not Markdown asterisks."""
    result = build_daily_report_html([], None, "2026-05-18")
    assert "<b>" in result
    assert "**" not in result
