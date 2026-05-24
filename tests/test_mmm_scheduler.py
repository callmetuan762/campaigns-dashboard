"""Tests for src/mmm/scheduler.py — Phase 8 Plan 02.

Covers:
- register_job_resources sets module globals
- build_mmm_telegram_message (pure function) formats per D-08
- run_mmm_weekly_job skips silently when weeks < 4 (D-06)
- run_mmm_weekly_job calls fit_mmm via asyncio.to_thread (D-19)
- run_mmm_weekly_job persists result via _db.upsert_mmm_result
- run_mmm_weekly_job sends Telegram message to every allowed chat
- run_mmm_weekly_job skips Telegram when fit_mmm returns None
- ROAS line is omitted when incremental_roas_per_1k is None
- Directional-only warning appears when maturity_label == 'directional_only'
- Early-data footnote appears when maturity_label == 'early'
- When deposit_value_usd == 0: ROAS line uses "deposits per $1000 spend" phrasing
- When deposit_value_usd > 0 and ROAS not None: ROAS line says "Incremental ROAS: N.Nx"
- Data load SQL uses meta_form_submit_deposit (NOT meta_purchases_7dclick)
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.mmm.model import MMMResult


# -------------------------------------------------------------------- #
# build_mmm_telegram_message — pure function                           #
# -------------------------------------------------------------------- #


def _make_result(
    *,
    media_pct: float = 42.3,
    baseline_pct: float = 57.7,
    incremental_roas_per_1k: float | None = 5.2,
    optimal_daily_spend: float = 350.4,
    weeks_of_data: int = 12,
    maturity_label: str = "reliable",
) -> MMMResult:
    return MMMResult(
        run_date="2026-05-24",
        weeks_of_data=weeks_of_data,
        media_pct=media_pct,
        baseline_pct=baseline_pct,
        incremental_roas_per_1k=incremental_roas_per_1k,
        optimal_daily_spend=optimal_daily_spend,
        theta=0.3,
        km=200.0,
        n=1.5,
        maturity_label=maturity_label,
    )


def test_telegram_message_starts_with_emoji_and_week_label() -> None:
    from src.mmm.scheduler import build_mmm_telegram_message

    text = build_mmm_telegram_message(_make_result(), "2026-05-24")
    assert text.startswith("📊 Weekly MMM Insight (week of 2026-05-24)")


def test_telegram_message_contains_media_and_baseline_pct() -> None:
    from src.mmm.scheduler import build_mmm_telegram_message

    text = build_mmm_telegram_message(_make_result(media_pct=42.3, baseline_pct=57.7), "2026-05-24")
    assert "42.3%" in text
    assert "57.7%" in text


def test_telegram_message_contains_optimal_daily_spend_as_integer() -> None:
    from src.mmm.scheduler import build_mmm_telegram_message

    text = build_mmm_telegram_message(_make_result(optimal_daily_spend=350.4), "2026-05-24")
    assert "~$350" in text


def test_telegram_message_directional_warning_present_when_maturity_directional_only() -> None:
    from src.mmm.scheduler import build_mmm_telegram_message

    result = _make_result(weeks_of_data=5, maturity_label="directional_only")
    text = build_mmm_telegram_message(result, "2026-05-24")
    assert "Directional only" in text
    assert "5 weeks" in text


def test_telegram_message_directional_warning_absent_when_reliable() -> None:
    from src.mmm.scheduler import build_mmm_telegram_message

    result = _make_result(weeks_of_data=20, maturity_label="reliable")
    text = build_mmm_telegram_message(result, "2026-05-24")
    assert "Directional only" not in text


def test_telegram_message_early_footnote_present_when_maturity_early() -> None:
    from src.mmm.scheduler import build_mmm_telegram_message

    result = _make_result(weeks_of_data=9, maturity_label="early")
    text = build_mmm_telegram_message(result, "2026-05-24")
    assert "Based on 9 weeks of data" in text
    assert "strengthen at 3+ months" in text


def test_telegram_message_early_footnote_absent_when_reliable() -> None:
    from src.mmm.scheduler import build_mmm_telegram_message

    result = _make_result(weeks_of_data=20, maturity_label="reliable")
    text = build_mmm_telegram_message(result, "2026-05-24")
    assert "strengthen at 3+ months" not in text


def test_telegram_message_omits_roas_line_when_roas_is_none() -> None:
    from src.mmm.scheduler import build_mmm_telegram_message

    result = _make_result(incremental_roas_per_1k=None)
    text = build_mmm_telegram_message(result, "2026-05-24")
    assert "ROAS" not in text
    assert "deposits per $1000" not in text


def test_telegram_message_deposit_value_zero_uses_deposits_per_1000_phrasing() -> None:
    from src.mmm.scheduler import build_mmm_telegram_message

    result = _make_result(incremental_roas_per_1k=5.2)
    text = build_mmm_telegram_message(result, "2026-05-24", deposit_value_usd=0.0)
    assert "5.2 deposits per $1000 spend" in text


def test_telegram_message_deposit_value_positive_uses_dollar_roas_phrasing() -> None:
    from src.mmm.scheduler import build_mmm_telegram_message

    result = _make_result(incremental_roas_per_1k=3.4)
    text = build_mmm_telegram_message(result, "2026-05-24", deposit_value_usd=50.0)
    assert "Incremental ROAS: 3.4x" in text


# -------------------------------------------------------------------- #
# register_job_resources + run_mmm_weekly_job                          #
# -------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_register_job_resources_sets_module_globals() -> None:
    import src.mmm.scheduler as sched

    bot = MagicMock()
    db = MagicMock()
    settings = MagicMock()

    sched.register_job_resources(bot, db, settings)
    assert sched._bot is bot
    assert sched._db is db
    assert sched._settings is settings


@pytest.mark.asyncio
async def test_run_mmm_weekly_job_raises_when_resources_not_registered() -> None:
    import src.mmm.scheduler as sched

    sched._bot = None
    sched._db = None
    sched._settings = None
    with pytest.raises((RuntimeError, AssertionError)):
        await sched.run_mmm_weekly_job()


@pytest.mark.asyncio
async def test_run_mmm_weekly_job_skips_silently_when_weeks_lt_4() -> None:
    import src.mmm.scheduler as sched

    bot = MagicMock()
    bot.send_message = AsyncMock()
    db = MagicMock()
    db.fetch_all = AsyncMock(return_value=[{"weeks": 3}])
    db.upsert_mmm_result = AsyncMock()
    settings = MagicMock()
    settings.telegram_allowed_chat_ids = [111]
    settings.deposit_value_usd = 0.0

    sched.register_job_resources(bot, db, settings)
    await sched.run_mmm_weekly_job()

    # No Telegram message sent, no upsert called
    bot.send_message.assert_not_called()
    db.upsert_mmm_result.assert_not_called()


@pytest.mark.asyncio
async def test_run_mmm_weekly_job_runs_when_weeks_ge_4_and_sends_telegram() -> None:
    import src.mmm.scheduler as sched

    bot = MagicMock()
    bot.send_message = AsyncMock()
    db = MagicMock()
    db.fetch_all = AsyncMock(side_effect=[
        # week count query
        [{"weeks": 5}],
        # data load query
        [
            {"date": "2026-05-01", "daily_spend": 100.0, "daily_deposits": 5.0},
            {"date": "2026-05-02", "daily_spend": 120.0, "daily_deposits": 6.0},
        ],
    ])
    db.upsert_mmm_result = AsyncMock()
    settings = MagicMock()
    settings.telegram_allowed_chat_ids = [111, 222]
    settings.deposit_value_usd = 0.0

    fake_result = _make_result(weeks_of_data=5, maturity_label="directional_only")

    sched.register_job_resources(bot, db, settings)
    with patch("src.mmm.scheduler.fit_mmm", return_value=fake_result) as mock_fit:
        await sched.run_mmm_weekly_job()

    # fit_mmm called once
    mock_fit.assert_called_once()
    # Result persisted
    db.upsert_mmm_result.assert_awaited_once_with(fake_result)
    # Telegram message sent to each chat id
    assert bot.send_message.await_count == 2
    sent_kwargs = [call.kwargs for call in bot.send_message.await_args_list]
    chat_ids_sent = {kw["chat_id"] for kw in sent_kwargs}
    assert chat_ids_sent == {111, 222}
    # Message contains the directional warning
    for kw in sent_kwargs:
        assert "📊 Weekly MMM Insight" in kw["text"]
        assert "Directional only" in kw["text"]


@pytest.mark.asyncio
async def test_run_mmm_weekly_job_uses_asyncio_to_thread_for_fit_mmm() -> None:
    import src.mmm.scheduler as sched

    bot = MagicMock()
    bot.send_message = AsyncMock()
    db = MagicMock()
    db.fetch_all = AsyncMock(side_effect=[
        [{"weeks": 8}],
        [
            {"date": "2026-05-01", "daily_spend": 100.0, "daily_deposits": 5.0},
        ],
    ])
    db.upsert_mmm_result = AsyncMock()
    settings = MagicMock()
    settings.telegram_allowed_chat_ids = [111]
    settings.deposit_value_usd = 0.0

    fake_result = _make_result(weeks_of_data=8, maturity_label="early")

    sched.register_job_resources(bot, db, settings)

    async def fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    with patch("src.mmm.scheduler.fit_mmm", return_value=fake_result):
        with patch("src.mmm.scheduler.asyncio.to_thread", side_effect=fake_to_thread) as mock_tt:
            await sched.run_mmm_weekly_job()

    # asyncio.to_thread was used at least once
    assert mock_tt.call_count >= 1


@pytest.mark.asyncio
async def test_run_mmm_weekly_job_skips_telegram_when_fit_returns_none() -> None:
    import src.mmm.scheduler as sched

    bot = MagicMock()
    bot.send_message = AsyncMock()
    db = MagicMock()
    db.fetch_all = AsyncMock(side_effect=[
        [{"weeks": 6}],
        [
            {"date": "2026-05-01", "daily_spend": 100.0, "daily_deposits": 5.0},
        ],
    ])
    db.upsert_mmm_result = AsyncMock()
    settings = MagicMock()
    settings.telegram_allowed_chat_ids = [111]
    settings.deposit_value_usd = 0.0

    sched.register_job_resources(bot, db, settings)
    with patch("src.mmm.scheduler.fit_mmm", return_value=None):
        await sched.run_mmm_weekly_job()

    db.upsert_mmm_result.assert_not_called()
    bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_run_mmm_weekly_job_data_load_sql_uses_meta_form_submit_deposit() -> None:
    """Data load SQL must reference meta_form_submit_deposit, not meta_purchases_7dclick."""
    import src.mmm.scheduler as sched

    bot = MagicMock()
    bot.send_message = AsyncMock()
    db = MagicMock()
    db.fetch_all = AsyncMock(side_effect=[
        [{"weeks": 8}],
        [{"date": "2026-05-01", "daily_spend": 100.0, "daily_deposits": 5.0}],
    ])
    db.upsert_mmm_result = AsyncMock()
    settings = MagicMock()
    settings.telegram_allowed_chat_ids = [111]
    settings.deposit_value_usd = 0.0

    sched.register_job_resources(bot, db, settings)

    fake_result = _make_result(weeks_of_data=8, maturity_label="early")
    with patch("src.mmm.scheduler.fit_mmm", return_value=fake_result):
        await sched.run_mmm_weekly_job()

    # Inspect SQL passed to db.fetch_all on the second call (data load)
    second_call_sql = db.fetch_all.await_args_list[1].args[0]
    assert "meta_form_submit_deposit" in second_call_sql
    assert "meta_purchases_7dclick" not in second_call_sql
    # Campaign-level filter present
    assert "ad_set_id" in second_call_sql
    assert "ad_id" in second_call_sql


@pytest.mark.asyncio
async def test_run_mmm_weekly_job_send_message_errors_do_not_raise() -> None:
    """Telegram send_message failures must be logged but not re-raised."""
    import src.mmm.scheduler as sched

    bot = MagicMock()
    bot.send_message = AsyncMock(side_effect=RuntimeError("telegram down"))
    db = MagicMock()
    db.fetch_all = AsyncMock(side_effect=[
        [{"weeks": 8}],
        [{"date": "2026-05-01", "daily_spend": 100.0, "daily_deposits": 5.0}],
    ])
    db.upsert_mmm_result = AsyncMock()
    settings = MagicMock()
    settings.telegram_allowed_chat_ids = [111]
    settings.deposit_value_usd = 0.0

    sched.register_job_resources(bot, db, settings)

    fake_result = _make_result(weeks_of_data=8, maturity_label="early")
    with patch("src.mmm.scheduler.fit_mmm", return_value=fake_result):
        # Should NOT raise
        await sched.run_mmm_weekly_job()

    # Upsert still called (occurs before send_message)
    db.upsert_mmm_result.assert_awaited_once()
