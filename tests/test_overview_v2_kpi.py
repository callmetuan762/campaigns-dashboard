"""Tests for the Overview v2 (2026-07-22) KPI-row db.py functions:
get_shopify_paid_summary, get_shopify_paid_daily, get_meta_begin_checkout_total.

Uses the real `db_client` fixture (tests/conftest.py) so the schema is built by
the actual migration pipeline (src.db.migrations / src.db.schema), not a
hand-rolled copy -- then reads back synchronously through src.dashboard.db
(the same sync sqlite3 module the dashboard uses), exercising the exact code
path Overview.py calls.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.dashboard.db import (
    get_campaign_daily_breakdown,
    get_daily_trend,
    get_meta_begin_checkout_total,
    get_shopify_paid_daily,
    get_shopify_paid_summary,
)


def _insert_shopify_order(
    path: Path,
    order_id: str,
    order_date: str,
    total_price: float,
    financial_status: str = "paid",
) -> None:
    con = sqlite3.connect(str(path))
    con.execute(
        "INSERT INTO shopify_orders "
        "(order_id, created_at, order_date, total_price, financial_status) "
        "VALUES (?, ?, ?, ?, ?)",
        (order_id, order_date, order_date, total_price, financial_status),
    )
    con.commit()
    con.close()


def _insert_ad_metrics_row(
    path: Path,
    campaign_id: str,
    date: str,
    spend: float,
    begin_checkout: int | None,
    ad_set_id: str = "",
    ad_id: str = "",
) -> None:
    con = sqlite3.connect(str(path))
    con.execute(
        "INSERT INTO ad_metrics "
        "(campaign_id, date, ad_set_id, ad_id, spend, meta_begin_checkout) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (campaign_id, date, ad_set_id, ad_id, spend, begin_checkout),
    )
    con.commit()
    con.close()


# ---------------------------------------------------------------------------
# get_shopify_paid_summary
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_shopify_paid_summary_counts_and_sums_paid_only(db_client) -> None:
    path = db_client._path
    _insert_shopify_order(path, "o1", "2026-07-15", 116.0, "paid")
    _insert_shopify_order(path, "o2", "2026-07-16", 116.0, "paid")
    _insert_shopify_order(path, "o3", "2026-07-16", 116.0, "refunded")  # excluded

    out = get_shopify_paid_summary(path, "2026-07-15", "2026-07-21")
    assert out["count"] == 2
    assert out["revenue"] == pytest.approx(232.0)


@pytest.mark.asyncio
async def test_shopify_paid_summary_respects_orders_valid_from(db_client) -> None:
    path = db_client._path
    _insert_shopify_order(path, "test1", "2026-07-10", 116.0, "paid")  # pre-launch, excluded
    _insert_shopify_order(path, "real1", "2026-07-15", 116.0, "paid")  # kept

    out = get_shopify_paid_summary(
        path, "2026-07-01", "2026-07-21", orders_valid_from="2026-07-15"
    )
    assert out["count"] == 1
    assert out["revenue"] == pytest.approx(116.0)

    # Without the cutoff, both orders count.
    out_unfiltered = get_shopify_paid_summary(path, "2026-07-01", "2026-07-21")
    assert out_unfiltered["count"] == 2
    assert out_unfiltered["revenue"] == pytest.approx(232.0)


@pytest.mark.asyncio
async def test_shopify_paid_summary_zero_when_no_rows(db_client) -> None:
    path = db_client._path
    out = get_shopify_paid_summary(path, "2026-07-15", "2026-07-21")
    assert out == {"count": 0, "revenue": 0.0}


def test_shopify_paid_summary_missing_table_returns_zeros(tmp_path: Path) -> None:
    db = tmp_path / "missing.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE placeholder (id INTEGER)")
    con.commit()
    con.close()
    out = get_shopify_paid_summary(db, "2026-07-15", "2026-07-21")
    assert out == {"count": 0, "revenue": 0.0}


# ---------------------------------------------------------------------------
# get_meta_begin_checkout_total
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_meta_begin_checkout_total_sums_campaign_level_rows(db_client) -> None:
    path = db_client._path
    _insert_ad_metrics_row(path, "c_1", "2026-07-15", 100.0, 30)
    _insert_ad_metrics_row(path, "c_1", "2026-07-16", 100.0, 37)

    total = get_meta_begin_checkout_total(path, "2026-07-15", "2026-07-21")
    assert total == 67


@pytest.mark.asyncio
async def test_meta_begin_checkout_total_excludes_adset_rows(db_client) -> None:
    path = db_client._path
    _insert_ad_metrics_row(path, "c_1", "2026-07-15", 100.0, 30)
    _insert_ad_metrics_row(path, "c_1", "2026-07-15", 50.0, 999, ad_set_id="set_1")

    total = get_meta_begin_checkout_total(path, "2026-07-15", "2026-07-21")
    assert total == 30


@pytest.mark.asyncio
async def test_meta_begin_checkout_total_zero_when_no_rows(db_client) -> None:
    path = db_client._path
    assert get_meta_begin_checkout_total(path, "2026-07-15", "2026-07-21") == 0


def test_meta_begin_checkout_total_missing_table_returns_zero(tmp_path: Path) -> None:
    db = tmp_path / "missing.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE placeholder (id INTEGER)")
    con.commit()
    con.close()
    assert get_meta_begin_checkout_total(db, "2026-07-15", "2026-07-21") == 0


# ---------------------------------------------------------------------------
# get_shopify_paid_daily (Overview v2 "Begin Checkout vs Shopify Paid" chart)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_shopify_paid_daily_grouped_by_date(db_client) -> None:
    path = db_client._path
    _insert_shopify_order(path, "o1", "2026-07-15", 116.0, "paid")
    _insert_shopify_order(path, "o2", "2026-07-15", 116.0, "paid")
    _insert_shopify_order(path, "o3", "2026-07-16", 116.0, "paid")
    _insert_shopify_order(path, "o4", "2026-07-16", 116.0, "refunded")

    rows = get_shopify_paid_daily(path, "2026-07-15", "2026-07-21")
    by_date = {r["date"]: r["paid"] for r in rows}
    assert by_date["2026-07-15"] == 2
    assert by_date["2026-07-16"] == 1  # refunded order excluded


@pytest.mark.asyncio
async def test_shopify_paid_daily_respects_orders_valid_from(db_client) -> None:
    path = db_client._path
    _insert_shopify_order(path, "test1", "2026-07-10", 116.0, "paid")
    _insert_shopify_order(path, "real1", "2026-07-15", 116.0, "paid")

    rows = get_shopify_paid_daily(
        path, "2026-07-01", "2026-07-21", orders_valid_from="2026-07-15"
    )
    dates = {r["date"] for r in rows}
    assert dates == {"2026-07-15"}


@pytest.mark.asyncio
async def test_shopify_paid_daily_empty_when_no_rows(db_client) -> None:
    path = db_client._path
    assert get_shopify_paid_daily(path, "2026-07-15", "2026-07-21") == []


def test_shopify_paid_daily_missing_table_returns_empty(tmp_path: Path) -> None:
    db = tmp_path / "missing.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE placeholder (id INTEGER)")
    con.commit()
    con.close()
    assert get_shopify_paid_daily(db, "2026-07-15", "2026-07-21") == []


# ---------------------------------------------------------------------------
# get_daily_trend — begin_checkout column (Overview v2 "Spend vs Begin
# Checkout" chart)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_daily_trend_includes_begin_checkout_column(db_client) -> None:
    path = db_client._path
    _insert_ad_metrics_row(path, "c_1", "2026-07-15", 100.0, 30)
    _insert_ad_metrics_row(path, "c_1", "2026-07-16", 100.0, 37)

    rows = get_daily_trend(path, "2026-07-15", "2026-07-21")
    by_date = {r["date"]: r["begin_checkout"] for r in rows}
    assert by_date["2026-07-15"] == 30
    assert by_date["2026-07-16"] == 37


@pytest.mark.asyncio
async def test_daily_trend_begin_checkout_excludes_adset_rows(db_client) -> None:
    path = db_client._path
    _insert_ad_metrics_row(path, "c_1", "2026-07-15", 100.0, 30)
    _insert_ad_metrics_row(path, "c_1", "2026-07-15", 50.0, 999, ad_set_id="set_1")

    rows = get_daily_trend(path, "2026-07-15", "2026-07-21")
    assert rows[0]["begin_checkout"] == 30


# ---------------------------------------------------------------------------
# get_campaign_daily_breakdown — begin_checkout / cost_per_bc columns
# (Overview v2 "Begin Checkout by campaign" / "Cost per Begin Checkout per
# campaign" charts)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_campaign_daily_breakdown_begin_checkout_and_cost_per_bc(db_client) -> None:
    path = db_client._path
    _insert_ad_metrics_row(path, "c_1", "2026-07-15", 100.0, 20)

    rows = get_campaign_daily_breakdown(path, "2026-07-15", "2026-07-21")
    assert len(rows) == 1
    assert rows[0]["begin_checkout"] == 20
    assert rows[0]["cost_per_bc"] == pytest.approx(5.0)  # 100 / 20


@pytest.mark.asyncio
async def test_campaign_daily_breakdown_cost_per_bc_div0_guard(db_client) -> None:
    """begin_checkout == 0 (or NULL) -> cost_per_bc is None, not a ZeroDivisionError."""
    path = db_client._path
    _insert_ad_metrics_row(path, "c_1", "2026-07-15", 100.0, 0)

    rows = get_campaign_daily_breakdown(path, "2026-07-15", "2026-07-21")
    assert rows[0]["begin_checkout"] == 0
    assert rows[0]["cost_per_bc"] is None
