"""Tests for src/shopify/client.py — landing_site UTM/lp_slug parsing, order normalization.

Covers SHOP-02: landing_site query-string parsing is the v1 attribution source of truth.
"""
from __future__ import annotations

import inspect

import pytest


# ---------------------------------------------------------------------------
# _parse_landing_site
# ---------------------------------------------------------------------------

def test_parse_landing_site_extracts_all_params():
    from src.shopify.client import _parse_landing_site
    result = _parse_landing_site(
        "/pages/preorder?utm_source=meta&utm_campaign=nowa_launch&utm_content=ad_a&lp_slug=routine"
    )
    assert result == {
        "utm_source": "meta",
        "utm_campaign": "nowa_launch",
        "utm_content": "ad_a",
        "lp_slug": "routine",
    }


def test_parse_landing_site_none_defaults_empty_strings():
    from src.shopify.client import _parse_landing_site
    result = _parse_landing_site(None)
    assert result == {"utm_source": "", "utm_campaign": "", "utm_content": "", "lp_slug": ""}


def test_parse_landing_site_empty_string_defaults_empty_strings():
    from src.shopify.client import _parse_landing_site
    result = _parse_landing_site("")
    assert result == {"utm_source": "", "utm_campaign": "", "utm_content": "", "lp_slug": ""}


def test_parse_landing_site_no_query_string_defaults_empty_strings():
    from src.shopify.client import _parse_landing_site
    result = _parse_landing_site("/pages/preorder")
    assert result["utm_source"] == ""
    assert result["lp_slug"] == ""


def test_parse_landing_site_missing_lp_slug_defaults_empty_string():
    from src.shopify.client import _parse_landing_site
    result = _parse_landing_site("/pages/preorder?utm_source=meta&utm_campaign=nowa_launch")
    assert result["lp_slug"] == ""
    assert result["utm_source"] == "meta"


def test_parse_landing_site_lp_slug_values():
    """Segment values from CONTEXT: routine, big-feelings, screen-anxious."""
    from src.shopify.client import _parse_landing_site
    for slug in ("routine", "big-feelings", "screen-anxious"):
        result = _parse_landing_site(f"/pages/preorder?lp_slug={slug}")
        assert result["lp_slug"] == slug


def test_parse_landing_site_lp_slug_derived_from_utm_content():
    """Production shop URLs carry no lp_slug param — only utm_* forwarded by the LP
    tracking helper. The segment is the prefix of utm_content ("<slug>__<creative-id>")."""
    from src.shopify.client import _parse_landing_site
    result = _parse_landing_site(
        "/cart/47382055289003:1?utm_source=facebook&utm_campaign=nowa_preorder_2026"
        "&utm_content=routine__c1&fbclid=abc"
    )
    assert result["lp_slug"] == "routine"
    assert result["utm_content"] == "routine__c1"
    # utm_content without the "__<creative>" suffix (e.g. homepage ads) is the slug itself
    result = _parse_landing_site("/cart/47382055289003:1?utm_content=home")
    assert result["lp_slug"] == "home"
    # an explicit lp_slug param, if ever present, wins over the derived value
    result = _parse_landing_site("/cart/1?utm_content=routine__c1&lp_slug=big-feelings")
    assert result["lp_slug"] == "big-feelings"


def test_parse_landing_site_full_url_form():
    """landing_site can also be an absolute URL, not just a path — must still parse."""
    from src.shopify.client import _parse_landing_site
    result = _parse_landing_site(
        "https://shop.nowaplanet.com/pages/preorder?utm_source=meta&lp_slug=routine"
    )
    assert result["utm_source"] == "meta"
    assert result["lp_slug"] == "routine"


# ---------------------------------------------------------------------------
# _parse_order
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_order():
    return {
        "id": 5551234,
        "created_at": "2026-05-18T10:15:30-04:00",
        "total_price": "49.99",
        "financial_status": "paid",
        "landing_site": "/pages/preorder?utm_source=meta&utm_campaign=nowa_launch&utm_content=ad_a&lp_slug=big-feelings",
        "referring_site": "https://www.facebook.com/",
    }


def test_parse_order_order_id_is_string(sample_order):
    from src.shopify.client import _parse_order
    row = _parse_order(sample_order)
    assert row["order_id"] == "5551234"
    assert isinstance(row["order_id"], str)


def test_parse_order_date_derived_from_created_at(sample_order):
    from src.shopify.client import _parse_order
    row = _parse_order(sample_order)
    assert row["order_date"] == "2026-05-18"


def test_parse_order_total_price_is_float(sample_order):
    from src.shopify.client import _parse_order
    row = _parse_order(sample_order)
    assert row["total_price"] == 49.99
    assert isinstance(row["total_price"], float)


def test_parse_order_utm_fields_from_landing_site(sample_order):
    from src.shopify.client import _parse_order
    row = _parse_order(sample_order)
    assert row["utm_source"] == "meta"
    assert row["utm_campaign"] == "nowa_launch"
    assert row["utm_content"] == "ad_a"
    assert row["lp_slug"] == "big-feelings"


def test_parse_order_preserves_raw_landing_and_referring_site(sample_order):
    from src.shopify.client import _parse_order
    row = _parse_order(sample_order)
    assert row["landing_site"] == sample_order["landing_site"]
    assert row["referring_site"] == "https://www.facebook.com/"


def test_parse_order_missing_fields_no_error():
    from src.shopify.client import _parse_order
    row = _parse_order({})
    assert row["order_id"] == ""
    assert row["order_date"] == ""
    assert row["total_price"] == 0.0
    assert row["financial_status"] == ""
    assert row["utm_source"] == ""
    assert row["lp_slug"] == ""


def test_parse_order_all_keys_present(sample_order):
    from src.shopify.client import _parse_order
    row = _parse_order(sample_order)
    expected_keys = {
        "order_id", "created_at", "order_date", "total_price", "financial_status",
        "utm_source", "utm_campaign", "utm_content", "lp_slug",
        "landing_site", "referring_site",
    }
    assert set(row.keys()) == expected_keys


# ---------------------------------------------------------------------------
# _next_page_url (Link header pagination)
# ---------------------------------------------------------------------------

def test_next_page_url_parses_rel_next():
    from src.shopify.client import _next_page_url
    link = (
        '<https://shop.nowaplanet.com/admin/api/2025-01/orders.json?page_info=abc123>; rel="next"'
    )
    assert _next_page_url(link) == "https://shop.nowaplanet.com/admin/api/2025-01/orders.json?page_info=abc123"


def test_next_page_url_none_when_no_next_rel():
    from src.shopify.client import _next_page_url
    link = '<https://shop.nowaplanet.com/admin/api/2025-01/orders.json?page_info=abc123>; rel="previous"'
    assert _next_page_url(link) is None


def test_next_page_url_none_on_empty_header():
    from src.shopify.client import _next_page_url
    assert _next_page_url(None) is None
    assert _next_page_url("") is None


# ---------------------------------------------------------------------------
# fetch_orders — async signature verification (no real HTTP call)
# ---------------------------------------------------------------------------

def test_fetch_orders_is_coroutine():
    from src.shopify.client import fetch_orders
    assert inspect.iscoroutinefunction(fetch_orders)


@pytest.mark.asyncio
async def test_fetch_orders_paginates_and_parses(monkeypatch):
    """fetch_orders follows Link-header pagination and parses each page's orders."""
    import src.shopify.client as client_module

    class _FakeResponse:
        def __init__(self, orders, next_link=None):
            self._orders = orders
            self._next_link = next_link
            self.headers = {"Link": next_link} if next_link else {}

        def raise_for_status(self):
            return None

        def json(self):
            return {"orders": self._orders}

    call_count = 0

    def fake_get(url, headers, params, timeout):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _FakeResponse(
                [{"id": 1, "created_at": "2026-05-18T00:00:00Z", "total_price": "10.0",
                  "financial_status": "paid", "landing_site": "", "referring_site": ""}],
                next_link='<https://shop.example.com/admin/api/2025-01/orders.json?page_info=xyz>; rel="next"',
            )
        return _FakeResponse(
            [{"id": 2, "created_at": "2026-05-18T01:00:00Z", "total_price": "20.0",
              "financial_status": "paid", "landing_site": "", "referring_site": ""}]
        )

    monkeypatch.setattr(client_module.requests, "get", fake_get)

    rows = await client_module.fetch_orders("shop.example.com", "fake-token", "2026-05-18", "2026-05-18")

    assert call_count == 2
    assert {r["order_id"] for r in rows} == {"1", "2"}
