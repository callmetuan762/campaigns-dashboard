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
        "actions": [
            {"action_type": "offsite_conversion.fb_pixel_purchase", "value": "10"},
            {"action_type": "landing_page_view", "value": "80"},
            {"action_type": "video_view", "value": "60"},
            {"action_type": "offsite_conversion.fb_pixel_initiate_checkout", "value": "15"},
            {"action_type": "offsite_conversion.fb_pixel_add_to_cart", "value": "25"},
            {"action_type": "offsite_conversion.fb_pixel_lead", "value": "5"},
        ],
        "cost_per_action_type": [
            {"action_type": "offsite_conversion.fb_pixel_purchase", "value": "10.0"},
            {"action_type": "offsite_conversion.fb_pixel_initiate_checkout", "value": "6.5"},
        ],
        "video_thruplay_watched_actions": [{"action_type": "video_view", "value": "40"}],
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
        "meta_form_submit_deposit",
        "reach", "frequency",
        # funnel-v3
        "landing_page_views", "video_3s_views", "video_thruplay",
        "meta_begin_checkout", "meta_cost_per_begin_checkout",
        "meta_add_to_cart", "meta_leads",
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
    # funnel-v3: actions-list-derived fields default to 0 (not None) — the list itself
    # (row.get("actions")) is simply absent, same as any other missing action_type.
    assert row["landing_page_views"] == 0
    assert row["video_3s_views"] == 0
    assert row["meta_begin_checkout"] == 0
    assert row["meta_cost_per_begin_checkout"] == 0.0
    assert row["meta_add_to_cart"] == 0
    assert row["meta_leads"] == 0
    # video_thruplay is None (not 0) when the field itself is absent from the API
    # response — distinguishes "field unavailable/degraded" from "zero views".
    assert row["video_thruplay"] is None


# ---------------------------------------------------------------------------
# funnel-v3: landing_page_view / video / InitiateCheckout / AddToCart / Lead parsing
# ---------------------------------------------------------------------------

def test_parse_row_landing_page_views(sample_campaign_row):
    from src.meta.client import _parse_insight_row
    row = _parse_insight_row(sample_campaign_row, "2026-05-18", "campaign")
    assert row["landing_page_views"] == 80


def test_parse_row_video_3s_views(sample_campaign_row):
    from src.meta.client import _parse_insight_row
    row = _parse_insight_row(sample_campaign_row, "2026-05-18", "campaign")
    assert row["video_3s_views"] == 60


def test_parse_row_video_thruplay(sample_campaign_row):
    """video_thruplay is parsed from the separate video_thruplay_watched_actions field."""
    from src.meta.client import _parse_insight_row
    row = _parse_insight_row(sample_campaign_row, "2026-05-18", "campaign")
    assert row["video_thruplay"] == 40


def test_parse_row_video_thruplay_none_when_field_absent():
    """video_thruplay is None (degraded), not 0, when the field key is entirely absent."""
    from src.meta.client import _parse_insight_row
    row = _parse_insight_row({"actions": []}, "2026-05-18", "campaign")
    assert row["video_thruplay"] is None


def test_parse_row_meta_begin_checkout(sample_campaign_row):
    """InitiateCheckout action_type -> meta_begin_checkout."""
    from src.meta.client import _parse_insight_row
    row = _parse_insight_row(sample_campaign_row, "2026-05-18", "campaign")
    assert row["meta_begin_checkout"] == 15


def test_parse_row_meta_cost_per_begin_checkout(sample_campaign_row):
    from src.meta.client import _parse_insight_row
    row = _parse_insight_row(sample_campaign_row, "2026-05-18", "campaign")
    assert row["meta_cost_per_begin_checkout"] == 6.5


def test_parse_row_meta_add_to_cart(sample_campaign_row):
    """AddToCart action_type -> meta_add_to_cart."""
    from src.meta.client import _parse_insight_row
    row = _parse_insight_row(sample_campaign_row, "2026-05-18", "campaign")
    assert row["meta_add_to_cart"] == 25


def test_parse_row_meta_leads(sample_campaign_row):
    """Lead action_type -> meta_leads."""
    from src.meta.client import _parse_insight_row
    row = _parse_insight_row(sample_campaign_row, "2026-05-18", "campaign")
    assert row["meta_leads"] == 5


# ---------------------------------------------------------------------------
# funnel-v3: _fetch_insights_sync graceful degradation on optional fields
# ---------------------------------------------------------------------------

def test_optional_fields_constant_contains_thruplay():
    from src.meta.client import _OPTIONAL_FIELDS
    assert "video_thruplay_watched_actions" in _OPTIONAL_FIELDS


def test_campaign_fields_contains_video_thruplay():
    from src.meta.client import _CAMPAIGN_FIELDS
    assert "video_thruplay_watched_actions" in _CAMPAIGN_FIELDS


def test_fetch_insights_sync_retries_without_optional_field_on_error():
    """If the API rejects video_thruplay_watched_actions, retry once without it."""
    import json as _json
    from unittest.mock import MagicMock, patch

    from facebook_business.exceptions import FacebookRequestError
    from src.meta.client import _fetch_insights_sync

    bad_error = FacebookRequestError(
        "Unknown field 'video_thruplay_watched_actions'",
        {},  # request_context — dict (constructor calls request.get(...))
        400,
        {},
        _json.dumps({"error": {"message": "Unknown field 'video_thruplay_watched_actions'"}}),
    )

    fallback_cursor = iter([{"campaign_id": "c1", "campaign_name": "C1"}])

    call_count = 0

    def fake_get_insights(fields, params):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            assert "video_thruplay_watched_actions" in fields
            raise bad_error
        assert "video_thruplay_watched_actions" not in fields
        cursor = MagicMock()
        cursor.__iter__.return_value = fallback_cursor
        cursor.load_next_page.return_value = False
        return cursor

    mock_account = MagicMock()
    mock_account.get_insights.side_effect = fake_get_insights

    with patch("src.meta.client.AdAccount", return_value=mock_account):
        rows = _fetch_insights_sync("act_123", "2026-05-18", "campaign")

    assert call_count == 2
    assert len(rows) == 1
    assert rows[0]["video_thruplay"] is None


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
