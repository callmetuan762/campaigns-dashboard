"""Tests for alert engine (Plan 02-04).

TDD RED phase: these tests are written BEFORE engine.py exists.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from src.alerts.engine import AlertType, evaluate_alerts


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_settings(
    *,
    chat_ids: list[int] | None = None,
    spend_spike_pct: float = 50.0,
    roas_floor: float = 1.0,
    zero_conv_threshold: float = 50.0,
    budget_pacing_pct: float = 20.0,
    cpc_multiplier: float = 2.0,
) -> MagicMock:
    s = MagicMock()
    s.telegram_allowed_chat_ids = chat_ids if chat_ids is not None else [99999]
    s.alert_spend_spike_pct = spend_spike_pct
    s.alert_roas_floor = roas_floor
    s.alert_zero_conv_spend_threshold = zero_conv_threshold
    s.alert_budget_pacing_pct = budget_pacing_pct
    s.alert_cpc_spike_multiplier = cpc_multiplier
    return s


def _make_row(
    *,
    campaign_id: str = "camp1",
    date: str = "2026-05-19",
    spend: float = 100.0,
    cpc: float = 1.0,
    roas: float = 2.0,
    purchases: int = 5,
    avg_spend_7d: float | None = 50.0,
    avg_cpc_7d: float | None = 0.5,
) -> dict:
    return {
        "campaign_id": campaign_id,
        "date": date,
        "spend": spend,
        "cpc": cpc,
        "roas": roas,
        "meta_purchases_7dclick": purchases,
        "avg_spend_7d": avg_spend_7d,
        "avg_cpc_7d": avg_cpc_7d,
    }


def _make_bot(*, send_ok: bool = True) -> AsyncMock:
    bot = AsyncMock()
    if send_ok:
        bot.send_message = AsyncMock(return_value=None)
    else:
        bot.send_message = AsyncMock(side_effect=Exception("Telegram error"))
    return bot


# ---------------------------------------------------------------------------
# AlertType enum
# ---------------------------------------------------------------------------

class TestAlertType:
    def test_all_five_members_defined(self):
        assert AlertType.SPEND_SPIKE == "SPEND_SPIKE"
        assert AlertType.ROAS_DROP == "ROAS_DROP"
        assert AlertType.ZERO_CONVERSION == "ZERO_CONVERSION"
        assert AlertType.BUDGET_PACING == "BUDGET_PACING"
        assert AlertType.CPC_SPIKE == "CPC_SPIKE"

    def test_str_value_matches_name(self):
        for member in AlertType:
            assert str(member) == member.value


# ---------------------------------------------------------------------------
# evaluate_alerts with real DB (integration)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_evaluate_alerts_no_data_no_error(db_client):
    """When there are no rows for target_date, evaluate_alerts returns silently."""
    bot = _make_bot()
    settings = _make_settings()
    # Call with a date that has no ad_metrics rows
    await evaluate_alerts(db_client, bot, settings, "2026-01-01")
    bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_evaluate_alerts_no_chat_id_no_error(db_client):
    """When telegram_allowed_chat_ids is empty, evaluate_alerts returns silently."""
    bot = _make_bot()
    settings = _make_settings(chat_ids=[])
    await evaluate_alerts(db_client, bot, settings, "2026-05-19")
    bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_spend_spike_fires_when_spend_above_threshold(db_client):
    """ALERT-01: fires when today spend > avg_7d * (1 + spike_pct/100)."""
    # Insert campaign-level rows (ad_set_id='', ad_id='')
    # 7 prior days at $100 each, today at $200 (100% spike vs 50% threshold)
    base_date = "2026-05-12"
    for i in range(7):
        d = f"2026-05-{12 + i:02d}"
        await db_client.execute(
            "INSERT INTO ad_metrics (campaign_id, date, ad_set_id, ad_id, spend, impressions, clicks, ctr, cpc, cpm, roas, meta_purchases_7dclick, meta_cost_per_purchase, reach, frequency) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("c_1", d, "", "", 100.0, 1000, 50, 0.05, 1.0, 10.0, 2.0, 5, 20.0, 900, 1.1),
        )
    # Today: big spike
    await db_client.execute(
        "INSERT INTO ad_metrics (campaign_id, date, ad_set_id, ad_id, spend, impressions, clicks, ctr, cpc, cpm, roas, meta_purchases_7dclick, meta_cost_per_purchase, reach, frequency) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("c_1", "2026-05-19", "", "", 200.0, 1000, 50, 0.05, 1.0, 10.0, 2.0, 5, 20.0, 900, 1.1),
    )

    bot = _make_bot()
    settings = _make_settings(spend_spike_pct=50.0)
    await evaluate_alerts(db_client, bot, settings, "2026-05-19")
    bot.send_message.assert_called()
    call_kwargs = bot.send_message.call_args
    assert "Spend Spike" in call_kwargs.kwargs.get("text", "") or "Spend Spike" in str(call_kwargs)


@pytest.mark.asyncio
async def test_spend_spike_does_not_fire_below_threshold(db_client):
    """ALERT-01: does NOT fire when spend is only slightly above average."""
    for i in range(7):
        d = f"2026-05-{12 + i:02d}"
        await db_client.execute(
            "INSERT INTO ad_metrics (campaign_id, date, ad_set_id, ad_id, spend, impressions, clicks, ctr, cpc, cpm, roas, meta_purchases_7dclick, meta_cost_per_purchase, reach, frequency) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("c_1", d, "", "", 100.0, 1000, 50, 0.05, 1.0, 10.0, 2.0, 5, 20.0, 900, 1.1),
        )
    # Today: only 10% above average (below 50% threshold)
    await db_client.execute(
        "INSERT INTO ad_metrics (campaign_id, date, ad_set_id, ad_id, spend, impressions, clicks, ctr, cpc, cpm, roas, meta_purchases_7dclick, meta_cost_per_purchase, reach, frequency) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("c_1", "2026-05-19", "", "", 110.0, 1000, 50, 0.05, 1.0, 10.0, 2.0, 5, 20.0, 900, 1.1),
    )
    bot = _make_bot()
    settings = _make_settings(spend_spike_pct=50.0)
    await evaluate_alerts(db_client, bot, settings, "2026-05-19")
    # Spend spike should NOT fire; other alerts may/may not fire but not SPEND_SPIKE
    calls = bot.send_message.call_args_list
    spend_spike_calls = [c for c in calls if "Spend Spike" in str(c)]
    assert len(spend_spike_calls) == 0


@pytest.mark.asyncio
async def test_roas_drop_fires_when_roas_below_floor(db_client):
    """ALERT-02: fires when roas < alert_roas_floor and spend > 1.0."""
    await db_client.execute(
        "INSERT INTO ad_metrics (campaign_id, date, ad_set_id, ad_id, spend, impressions, clicks, ctr, cpc, cpm, roas, meta_purchases_7dclick, meta_cost_per_purchase, reach, frequency) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("c_1", "2026-05-19", "", "", 100.0, 1000, 50, 0.05, 1.0, 10.0, 0.5, 5, 20.0, 900, 1.1),
    )
    bot = _make_bot()
    settings = _make_settings(roas_floor=1.0)
    await evaluate_alerts(db_client, bot, settings, "2026-05-19")
    calls = bot.send_message.call_args_list
    roas_calls = [c for c in calls if "ROAS Drop" in str(c)]
    assert len(roas_calls) >= 1


@pytest.mark.asyncio
async def test_roas_drop_does_not_fire_when_roas_above_floor(db_client):
    """ALERT-02: does NOT fire when roas >= alert_roas_floor."""
    await db_client.execute(
        "INSERT INTO ad_metrics (campaign_id, date, ad_set_id, ad_id, spend, impressions, clicks, ctr, cpc, cpm, roas, meta_purchases_7dclick, meta_cost_per_purchase, reach, frequency) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("c_1", "2026-05-19", "", "", 100.0, 1000, 50, 0.05, 1.0, 10.0, 2.0, 5, 20.0, 900, 1.1),
    )
    bot = _make_bot()
    settings = _make_settings(roas_floor=1.0)
    await evaluate_alerts(db_client, bot, settings, "2026-05-19")
    calls = bot.send_message.call_args_list
    roas_calls = [c for c in calls if "ROAS Drop" in str(c)]
    assert len(roas_calls) == 0


@pytest.mark.asyncio
async def test_zero_conversion_fires_when_spend_above_threshold(db_client):
    """ALERT-03: fires when spend > threshold and purchases == 0."""
    await db_client.execute(
        "INSERT INTO ad_metrics (campaign_id, date, ad_set_id, ad_id, spend, impressions, clicks, ctr, cpc, cpm, roas, meta_purchases_7dclick, meta_cost_per_purchase, reach, frequency) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("c_1", "2026-05-19", "", "", 100.0, 1000, 50, 0.05, 1.0, 10.0, 0.0, 0, 0.0, 900, 1.1),
    )
    bot = _make_bot()
    settings = _make_settings(zero_conv_threshold=50.0, roas_floor=0.0)
    await evaluate_alerts(db_client, bot, settings, "2026-05-19")
    calls = bot.send_message.call_args_list
    zero_calls = [c for c in calls if "Zero Conversions" in str(c)]
    assert len(zero_calls) >= 1


@pytest.mark.asyncio
async def test_zero_conversion_does_not_fire_below_spend_threshold(db_client):
    """ALERT-03: does NOT fire when spend <= threshold even with 0 purchases."""
    await db_client.execute(
        "INSERT INTO ad_metrics (campaign_id, date, ad_set_id, ad_id, spend, impressions, clicks, ctr, cpc, cpm, roas, meta_purchases_7dclick, meta_cost_per_purchase, reach, frequency) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("c_1", "2026-05-19", "", "", 10.0, 1000, 50, 0.05, 1.0, 10.0, 0.0, 0, 0.0, 900, 1.1),
    )
    bot = _make_bot()
    settings = _make_settings(zero_conv_threshold=50.0, roas_floor=0.0)
    await evaluate_alerts(db_client, bot, settings, "2026-05-19")
    calls = bot.send_message.call_args_list
    zero_calls = [c for c in calls if "Zero Conversions" in str(c)]
    assert len(zero_calls) == 0


@pytest.mark.asyncio
async def test_cpc_spike_fires_when_cpc_above_multiplier(db_client):
    """ALERT-05: fires when cpc > avg_cpc_7d * alert_cpc_spike_multiplier."""
    for i in range(7):
        d = f"2026-05-{12 + i:02d}"
        await db_client.execute(
            "INSERT INTO ad_metrics (campaign_id, date, ad_set_id, ad_id, spend, impressions, clicks, ctr, cpc, cpm, roas, meta_purchases_7dclick, meta_cost_per_purchase, reach, frequency) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("c_1", d, "", "", 100.0, 1000, 50, 0.05, 1.0, 10.0, 2.0, 5, 20.0, 900, 1.1),
        )
    # Today: CPC is 3x average (above 2x threshold)
    await db_client.execute(
        "INSERT INTO ad_metrics (campaign_id, date, ad_set_id, ad_id, spend, impressions, clicks, ctr, cpc, cpm, roas, meta_purchases_7dclick, meta_cost_per_purchase, reach, frequency) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("c_1", "2026-05-19", "", "", 100.0, 1000, 50, 0.05, 3.0, 10.0, 2.0, 5, 20.0, 900, 1.1),
    )
    bot = _make_bot()
    settings = _make_settings(cpc_multiplier=2.0)
    await evaluate_alerts(db_client, bot, settings, "2026-05-19")
    calls = bot.send_message.call_args_list
    cpc_calls = [c for c in calls if "CPC Spike" in str(c)]
    assert len(cpc_calls) >= 1


@pytest.mark.asyncio
async def test_alert_dedup_second_call_does_not_resend(db_client):
    """D-18: calling evaluate_alerts twice for same date does not double-send."""
    await db_client.execute(
        "INSERT INTO ad_metrics (campaign_id, date, ad_set_id, ad_id, spend, impressions, clicks, ctr, cpc, cpm, roas, meta_purchases_7dclick, meta_cost_per_purchase, reach, frequency) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("c_1", "2026-05-19", "", "", 100.0, 1000, 50, 0.05, 1.0, 10.0, 0.5, 5, 20.0, 900, 1.1),
    )
    bot = _make_bot()
    settings = _make_settings(roas_floor=1.0)
    # First call — should send ROAS drop
    await evaluate_alerts(db_client, bot, settings, "2026-05-19")
    first_call_count = bot.send_message.call_count
    assert first_call_count >= 1

    # Second call — same date, dedup must prevent re-send
    await evaluate_alerts(db_client, bot, settings, "2026-05-19")
    assert bot.send_message.call_count == first_call_count


@pytest.mark.asyncio
async def test_html_escaping_in_alert_message(db_client):
    """T-02-11: Campaign names with HTML chars must be escaped in alert messages."""
    # Insert campaign with dangerous HTML in name
    await db_client.execute(
        "INSERT INTO campaigns (id, source, name, status) VALUES (?, ?, ?, ?)",
        ("c_evil", "meta_ads", "<script>alert('xss')</script>", "ACTIVE"),
    )
    await db_client.execute(
        "INSERT INTO ad_metrics (campaign_id, date, ad_set_id, ad_id, spend, impressions, clicks, ctr, cpc, cpm, roas, meta_purchases_7dclick, meta_cost_per_purchase, reach, frequency) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("c_evil", "2026-05-19", "", "", 100.0, 1000, 50, 0.05, 1.0, 10.0, 0.5, 5, 20.0, 900, 1.1),
    )
    bot = _make_bot()
    settings = _make_settings(roas_floor=1.0)
    await evaluate_alerts(db_client, bot, settings, "2026-05-19")
    # At least one message sent
    assert bot.send_message.call_count >= 1
    # No raw unescaped script tags in any message
    for call in bot.send_message.call_args_list:
        text = call.kwargs.get("text", "") or str(call)
        assert "<script>" not in text, f"Unescaped HTML in alert message: {text}"


@pytest.mark.asyncio
async def test_telegram_error_does_not_propagate(db_client):
    """Alert send failure must not raise — evaluate_alerts must be exception-safe."""
    await db_client.execute(
        "INSERT INTO ad_metrics (campaign_id, date, ad_set_id, ad_id, spend, impressions, clicks, ctr, cpc, cpm, roas, meta_purchases_7dclick, meta_cost_per_purchase, reach, frequency) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("c_1", "2026-05-19", "", "", 100.0, 1000, 50, 0.05, 1.0, 10.0, 0.5, 5, 20.0, 900, 1.1),
    )
    bot = _make_bot(send_ok=False)
    settings = _make_settings(roas_floor=1.0)
    # Must not raise even when bot.send_message raises
    await evaluate_alerts(db_client, bot, settings, "2026-05-19")
