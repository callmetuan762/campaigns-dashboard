"""Tests for src/meta/client.py — action parsing and row normalization.

RED phase: These tests verify the behavioral contract of _extract_action_value
and _parse_insight_row. The fetch_* functions are async wrappers around
asyncio.to_thread; they are tested in Plan 02-08 with SDK mocks.
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# _extract_action_value
# ---------------------------------------------------------------------------

def test_extract_action_value_none_returns_zero():
    from src.meta.client import _extract_action_value
    assert _extract_action_value(None, "omni_purchase") == 0.0


def test_extract_action_value_empty_list_returns_zero():
    from src.meta.client import _extract_action_value
    assert _extract_action_value([], "omni_purchase") == 0.0


def test_extract_action_value_matching_type():
    from src.meta.client import _extract_action_value
    actions = [{"action_type": "omni_purchase", "value": "3.5"}]
    assert _extract_action_value(actions, "omni_purchase") == 3.5


def test_extract_action_value_no_matching_type_returns_zero():
    from src.meta.client import _extract_action_value
    actions = [{"action_type": "other_action", "value": "1.0"}]
    assert _extract_action_value(actions, "omni_purchase") == 0.0


def test_extract_action_value_multiple_entries_picks_correct():
    from src.meta.client import _extract_action_value
    actions = [
        {"action_type": "link_click", "value": "100"},
        {"action_type": "omni_purchase", "value": "5.25"},
        {"action_type": "post_engagement", "value": "200"},
    ]
    assert _extract_action_value(actions, "omni_purchase") == 5.25


def test_extract_action_value_returns_float():
    from src.meta.client import _extract_action_value
    actions = [{"action_type": "omni_purchase", "value": "10"}]
    result = _extract_action_value(actions, "omni_purchase")
    assert isinstance(result, float)
    assert result == 10.0


# ---------------------------------------------------------------------------
# _parse_insight_row — campaign level
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_campaign_row():
    return {
        "campaign_id": "c1",
        "campaign_name": "Test Campaign",
        "spend": "100.0",
        "impressions": "1000",
        "clicks": "50",
        "ctr": "5.0",
        "cpc": "2.0",
        "cpm": "5.0",
        "reach": "900",
        "frequency": "1.1",
        "purchase_roas": [{"action_type": "omni_purchase", "value": "3.0"}],
        "actions": [{"action_type": "offsite_conversion.fb_pixel_purchase", "value": "10"}],
        "cost_per_action_type": [
            {"action_type": "offsite_conversion.fb_pixel_purchase", "value": "10.0"}
        ],
    }


def test_parse_row_campaign_id(sample_campaign_row):
    from src.meta.client import _parse_insight_row
    row = _parse_insight_row(sample_campaign_row, "2026-05-18", "campaign")
    assert row["campaign_id"] == "c1"


def test_parse_row_campaign_name(sample_campaign_row):
    from src.meta.client import _parse_insight_row
    row = _parse_insight_row(sample_campaign_row, "2026-05-18", "campaign")
    assert row["campaign_name"] == "Test Campaign"


def test_parse_row_date(sample_campaign_row):
    from src.meta.client import _parse_insight_row
    row = _parse_insight_row(sample_campaign_row, "2026-05-18", "campaign")
    assert row["date"] == "2026-05-18"


def test_parse_row_roas_from_list(sample_campaign_row):
    """purchase_roas is a list — NOT a float scalar (critical pitfall)."""
    from src.meta.client import _parse_insight_row
    row = _parse_insight_row(sample_campaign_row, "2026-05-18", "campaign")
    assert row["roas"] == 3.0


def test_parse_row_meta_purchases(sample_campaign_row):
    from src.meta.client import _parse_insight_row
    row = _parse_insight_row(sample_campaign_row, "2026-05-18", "campaign")
    assert row["meta_purchases_7dclick"] == 10


def test_parse_row_meta_cost_per_purchase(sample_campaign_row):
    from src.meta.client import _parse_insight_row
    row = _parse_insight_row(sample_campaign_row, "2026-05-18", "campaign")
    assert row["meta_cost_per_purchase"] == 10.0


def test_parse_row_campaign_level_ad_set_id_empty(sample_campaign_row):
    """Campaign-level rows use '' as ad_set_id sentinel."""
    from src.meta.client import _parse_insight_row
    row = _parse_insight_row(sample_campaign_row, "2026-05-18", "campaign")
    assert row["ad_set_id"] == ""


def test_parse_row_campaign_level_ad_id_empty(sample_campaign_row):
    """Campaign-level rows use '' as ad_id sentinel."""
    from src.meta.client import _parse_insight_row
    row = _parse_insight_row(sample_campaign_row, "2026-05-18", "campaign")
    assert row["ad_id"] == ""


def test_parse_row_numeric_conversions(sample_campaign_row):
    from src.meta.client import _parse_insight_row
    row = _parse_insight_row(sample_campaign_row, "2026-05-18", "campaign")
    assert row["spend"] == 100.0
    assert isinstance(row["spend"], float)
    assert row["impressions"] == 1000
    assert isinstance(row["impressions"], int)
    assert row["clicks"] == 50
    assert isinstance(row["clicks"], int)
    assert row["reach"] == 900
    assert isinstance(row["reach"], int)
    assert row["frequency"] == pytest.approx(1.1)
    assert isinstance(row["frequency"], float)


def test_parse_row_all_keys_present(sample_campaign_row):
    from src.meta.client import _parse_insight_row
    row = _parse_insight_row(sample_campaign_row, "2026-05-18", "campaign")
    expected_keys = {
        "campaign_id", "campaign_name", "date",
        "ad_set_id", "ad_id",
        "spend", "impressions", "clicks", "ctr", "cpc", "cpm",
        "roas", "meta_purchases_7dclick", "meta_cost_per_purchase",
        "reach", "frequency",
    }
    assert set(row.keys()) == expected_keys


def test_parse_row_missing_fields_no_error():
    """Missing optional fields default to 0 without raising."""
    from src.meta.client import _parse_insight_row
    row = _parse_insight_row({}, "2026-05-18", "campaign")
    assert row["spend"] == 0.0
    assert row["impressions"] == 0
    assert row["roas"] == 0.0
    assert row["meta_purchases_7dclick"] == 0


# ---------------------------------------------------------------------------
# _parse_insight_row — adset and ad levels
# ---------------------------------------------------------------------------

def test_parse_row_adset_level_sets_ad_set_id():
    from src.meta.client import _parse_insight_row
    row = _parse_insight_row(
        {"adset_id": "as1", "campaign_id": "c1"},
        "2026-05-18",
        "adset",
    )
    assert row["ad_set_id"] == "as1"
    assert row["ad_id"] == ""


def test_parse_row_ad_level_sets_both_ids():
    from src.meta.client import _parse_insight_row
    row = _parse_insight_row(
        {"adset_id": "as1", "ad_id": "ad1", "campaign_id": "c1"},
        "2026-05-18",
        "ad",
    )
    assert row["ad_set_id"] == "as1"
    assert row["ad_id"] == "ad1"


# ---------------------------------------------------------------------------
# fetch_* functions — async signature verification (no SDK call)
# ---------------------------------------------------------------------------

def test_fetch_campaign_insights_is_coroutine():
    """fetch_campaign_insights must be an async function (coroutine)."""
    import inspect
    from src.meta.client import fetch_campaign_insights
    assert inspect.iscoroutinefunction(fetch_campaign_insights)


def test_fetch_adset_insights_is_coroutine():
    import inspect
    from src.meta.client import fetch_adset_insights
    assert inspect.iscoroutinefunction(fetch_adset_insights)


def test_fetch_ad_insights_is_coroutine():
    import inspect
    from src.meta.client import fetch_ad_insights
    assert inspect.iscoroutinefunction(fetch_ad_insights)
