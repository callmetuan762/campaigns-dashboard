"""Tests for the Ad Creative Analysis page (src/dashboard/pages/2_Ads.py) and its
db.py query functions, re-pointed from the dead deposit-era conversion
(form_submit_deposit / FSD) to the live preorder funnel conversion (Meta
begin_checkout, 2026-07-22).

Live funnel: landing_page_views -> meta_begin_checkout (primary optimization
signal, "BC") -> meta_purchases_7dclick (secondary). form_submit_deposit is
dead (0 events) and must no longer drive any metric on this page.

Structure follows the established page-test convention (see
tests/test_attribution_page.py, tests/test_funnel_page_v3_smoke.py):
  1. Source-level checks (ast/tokenize, no Streamlit runtime) — syntax, banned
     imports, no leftover FSD/CPR(FSD) language, new BC language present.
  2. db.py function tests against the real migration-built schema (db_client
     fixture from tests/conftest.py), mirroring tests/test_overview_v2_kpi.py's
     pattern of reading back synchronously through src.dashboard.db.
  3. An AppTest boot of the page against the real data/metrics.db (read-only —
     every query the page issues is a SELECT) asserting no exception, per the
     pattern in tests/test_dashboard_app_smoke.py.
"""
from __future__ import annotations

import ast
import sqlite3
from pathlib import Path

import pytest

from src.dashboard.db import (
    get_ad_format_breakdown,
    get_ad_style_breakdown,
    get_creative_concept_breakdown,
    get_fatigue_ads,
    get_top_ads,
)

PAGE_PATH = Path("src/dashboard/pages/2_Ads.py")
REAL_DB_PATH = Path("data/metrics.db")


# ---------------------------------------------------------------------------
# Section 1: source-level checks (no Streamlit runtime)
# ---------------------------------------------------------------------------
def test_page_file_exists() -> None:
    assert PAGE_PATH.exists()


def test_page_syntax_valid() -> None:
    source = PAGE_PATH.read_text(encoding="utf-8")
    ast.parse(source)


def test_no_banned_imports() -> None:
    """D-19 standalone page rule: no bot-framework imports."""
    source = PAGE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.append(node.module)

    banned = ["aiogram", "src.bot", "src.ai", "asyncio"]
    for ban in banned:
        for mod in imported_modules:
            assert not mod.startswith(ban), f"Banned import '{mod}' found in 2_Ads.py"


def test_no_leftover_fsd_language() -> None:
    """The page must no longer surface dead-funnel FSD/CPR(FSD) language.

    A few internal identifiers legitimately still contain the substring
    (e.g. the shared COLOR_DEPOSITS palette constant, kept per the pattern
    already established in Overview.py), so this checks for the *user-facing*
    strings rather than banning the substring outright.
    """
    source = PAGE_PATH.read_text(encoding="utf-8")
    banned_phrases = [
        "form submit deposit",
        "form-submit deposit",
        "CPR (FSD)",
        '"FSD"',
        "cpr_fsd",
    ]
    for phrase in banned_phrases:
        assert phrase not in source, f"Stale FSD-era phrase {phrase!r} still present in 2_Ads.py"


def test_new_bc_language_present() -> None:
    """The re-pointed page must reference the live funnel language throughout."""
    source = PAGE_PATH.read_text(encoding="utf-8")
    required = [
        "Begin Checkout",
        "cost_per_bc",
        "Cost per BC",
        "purchases",
        "Purchases",
        # Sample-size guard caption (item 3 of the re-point spec)
        "~20+ conversions",
        "directional",
        # Confidence badge thresholds
        "≥ 3 BC",
        "1–2 BC",
        "0 BC",
    ]
    missing = [r for r in required if r not in source]
    assert not missing, f"Missing required BC-funnel elements in 2_Ads.py: {missing}"


def test_fatigue_signals_reference_bc_not_fsd() -> None:
    source = PAGE_PATH.read_text(encoding="utf-8")
    assert "cost-per-BC" in source or "Cost/BC" in source
    assert "diminishing FSD rate" not in source
    assert "rising cost-per-FSD" not in source


def test_backfill_accumulation_note_kept() -> None:
    """Top explainer must keep the note that ad-level data accumulates via
    daily backfill (only 1 day of ad-level data exists so far)."""
    source = PAGE_PATH.read_text(encoding="utf-8")
    assert "accumulate" in source.lower()
    assert "backfill" in source.lower()


def test_palette_constants_declared() -> None:
    source = PAGE_PATH.read_text(encoding="utf-8")
    for const in ["COLOR_BG_PAPER", "COLOR_BG_PLOT", "COLOR_FONT", "COLOR_GRID"]:
        assert f"{const} = " in source


# ---------------------------------------------------------------------------
# Section 2: db.py function tests (real migration-built schema)
# ---------------------------------------------------------------------------
def _insert_ad_creative(
    path: Path,
    ad_id: str,
    ad_name: str,
    ad_format: str = "image",
    ad_style: str = "broad",
    campaign_id: str = "c_1",
) -> None:
    con = sqlite3.connect(str(path))
    con.execute(
        "INSERT INTO ad_creatives (ad_id, ad_name, campaign_id, ad_format, ad_style, thumbnail_url) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (ad_id, ad_name, campaign_id, ad_format, ad_style, f"https://example.com/{ad_id}.jpg"),
    )
    con.commit()
    con.close()


def _insert_ad_metrics_row(
    path: Path,
    ad_id: str,
    date: str,
    spend: float,
    begin_checkout: int = 0,
    purchases: int = 0,
    impressions: int = 1000,
    clicks: int = 10,
    ctr: float = 1.0,
    frequency: float = 1.0,
    campaign_id: str = "c_1",
) -> None:
    con = sqlite3.connect(str(path))
    con.execute(
        "INSERT INTO ad_metrics "
        "(campaign_id, date, ad_set_id, ad_id, spend, impressions, clicks, ctr, "
        " frequency, roas, meta_begin_checkout, meta_purchases_7dclick) "
        "VALUES (?, ?, '', ?, ?, ?, ?, ?, ?, 1.0, ?, ?)",
        (campaign_id, date, ad_id, spend, impressions, clicks, ctr, frequency,
         begin_checkout, purchases),
    )
    con.commit()
    con.close()


@pytest.mark.asyncio
async def test_get_top_ads_ranks_by_bc_and_exposes_cost_per_bc_and_purchases(db_client) -> None:
    path = db_client._path
    _insert_ad_creative(path, "ad_hi", "Nowa | HI-CONCEPT | broad | single_image | 20260715")
    _insert_ad_creative(path, "ad_lo", "Nowa | LO-CONCEPT | broad | single_image | 20260715")

    # ad_hi: 4 BC on $40 spend -> cost_per_bc = 10.0; 2 purchases
    _insert_ad_metrics_row(path, "ad_hi", "2026-07-21", spend=40.0, begin_checkout=4, purchases=2)
    # ad_lo: 1 BC on $20 spend -> cost_per_bc = 20.0; 0 purchases
    _insert_ad_metrics_row(path, "ad_lo", "2026-07-21", spend=20.0, begin_checkout=1, purchases=0)

    rows = get_top_ads(path, "2026-07-21", "2026-07-21", limit=10)
    assert len(rows) == 2
    # Ranked by BC desc (not spend, not FSD)
    assert rows[0]["ad_id"] == "ad_hi"
    assert rows[0]["bc"] == 4
    assert rows[0]["cost_per_bc"] == pytest.approx(10.0)
    assert rows[0]["purchases"] == 2
    assert "fsd" not in rows[0]
    assert "cpr_fsd" not in rows[0]

    assert rows[1]["ad_id"] == "ad_lo"
    assert rows[1]["bc"] == 1
    assert rows[1]["cost_per_bc"] == pytest.approx(20.0)
    assert rows[1]["purchases"] == 0


@pytest.mark.asyncio
async def test_get_top_ads_cost_per_bc_is_none_with_zero_bc(db_client) -> None:
    path = db_client._path
    _insert_ad_creative(path, "ad_zero", "Nowa | ZERO-CONCEPT | broad | single_image | 20260715")
    _insert_ad_metrics_row(path, "ad_zero", "2026-07-21", spend=5.0, begin_checkout=0, purchases=0)

    rows = get_top_ads(path, "2026-07-21", "2026-07-21", limit=10)
    assert len(rows) == 1
    assert rows[0]["bc"] == 0
    assert rows[0]["cost_per_bc"] is None


@pytest.mark.asyncio
async def test_get_fatigue_ads_requires_four_days(db_client) -> None:
    path = db_client._path
    _insert_ad_creative(path, "ad1", "Nowa | SHORT-WINDOW | broad | single_image | 20260715")
    _insert_ad_metrics_row(path, "ad1", "2026-07-20", spend=10.0, begin_checkout=2, impressions=500, clicks=10)
    # Only a 2-day range -- guard must return [] regardless of signal strength
    rows = get_fatigue_ads(path, "2026-07-20", "2026-07-21")
    assert rows == []


@pytest.mark.asyncio
async def test_get_fatigue_ads_detects_rising_cost_per_bc_and_diminishing_bc_rate(db_client) -> None:
    path = db_client._path
    _insert_ad_creative(path, "ad_fatigued", "Nowa | FATIGUE-CASE | broad | single_image | 20260715")

    # Early half (strong): high CTR, high BC rate, cheap cost-per-BC
    _insert_ad_metrics_row(
        path, "ad_fatigued", "2026-07-01", spend=10.0, begin_checkout=10,
        impressions=1000, clicks=50, ctr=5.0, frequency=1.0,
    )
    # Late half (fatigued): CTR collapses, cost-per-BC rises, BC rate collapses
    _insert_ad_metrics_row(
        path, "ad_fatigued", "2026-07-08", spend=40.0, begin_checkout=1,
        impressions=1000, clicks=5, ctr=0.5, frequency=1.0,
    )

    rows = get_fatigue_ads(path, "2026-07-01", "2026-07-08")
    assert len(rows) == 1
    row = rows[0]
    assert row["ad_id"] == "ad_fatigued"
    assert "bc" in row
    assert "fsd" not in row
    assert "cpd_change_pct" not in row
    assert "cpbc_change_pct" in row
    assert row["cpbc_change_pct"] is not None and row["cpbc_change_pct"] > 0
    # New signal vocabulary — no FSD/CPR wording
    joined_signals = " ".join(row["fatigue_signals"])
    assert "FSD" not in joined_signals
    assert "CPR" not in joined_signals
    assert any("BC rate" in s or "Cost/BC" in s for s in row["fatigue_signals"])


@pytest.mark.asyncio
async def test_get_ad_format_breakdown_uses_bc(db_client) -> None:
    path = db_client._path
    _insert_ad_creative(path, "ad_img", "Nowa | IMG-CONCEPT | broad | single_image | 20260715", ad_format="image")
    _insert_ad_creative(path, "ad_vid", "Nowa | VID-CONCEPT | broad | video | 20260715", ad_format="video")
    _insert_ad_metrics_row(path, "ad_img", "2026-07-21", spend=10.0, begin_checkout=3)
    _insert_ad_metrics_row(path, "ad_vid", "2026-07-21", spend=10.0, begin_checkout=1)

    rows = get_ad_format_breakdown(path, "2026-07-21", "2026-07-21")
    by_format = {r["ad_format"]: r for r in rows}
    assert by_format["image"]["bc"] == 3
    assert by_format["video"]["bc"] == 1
    assert "fsd" not in by_format["image"]


@pytest.mark.asyncio
async def test_get_ad_style_breakdown_ranks_by_cost_per_bc_ascending(db_client) -> None:
    path = db_client._path
    _insert_ad_creative(path, "ad_cheap", "Nowa | CHEAP-CONCEPT | broad | single_image | 20260715", ad_style="broad")
    _insert_ad_creative(path, "ad_pricey", "Nowa | PRICEY-CONCEPT | testimonial | single_image | 20260715", ad_style="testimonial")
    # broad: $10 / 5 BC = $2 cost-per-BC
    _insert_ad_metrics_row(path, "ad_cheap", "2026-07-21", spend=10.0, begin_checkout=5)
    # testimonial: $10 / 1 BC = $10 cost-per-BC
    _insert_ad_metrics_row(path, "ad_pricey", "2026-07-21", spend=10.0, begin_checkout=1)

    rows = get_ad_style_breakdown(path, "2026-07-21", "2026-07-21")
    assert [r["ad_style"] for r in rows] == ["broad", "testimonial"]
    assert rows[0]["cost_per_bc"] == pytest.approx(2.0)
    assert rows[1]["cost_per_bc"] == pytest.approx(10.0)


@pytest.mark.asyncio
async def test_get_creative_concept_breakdown_ranks_and_aggregates_purchases(db_client) -> None:
    path = db_client._path
    # Two copies of the same concept (QUIZ-01), collapsed into one row.
    _insert_ad_creative(path, "ad_c1", "Nowa | QUIZ-01-pt1-c1 | quiz | single_image | 20260715")
    _insert_ad_creative(path, "ad_c2", "Nowa | QUIZ-01-pt2-c1 | quiz | single_image | 20260715")
    _insert_ad_creative(path, "ad_other", "Nowa | OTHER-CONCEPT | broad | single_image | 20260715")

    _insert_ad_metrics_row(path, "ad_c1", "2026-07-21", spend=10.0, begin_checkout=2, purchases=1)
    _insert_ad_metrics_row(path, "ad_c2", "2026-07-21", spend=10.0, begin_checkout=1, purchases=0)
    # "other" concept: cheaper cost-per-BC, should rank first
    _insert_ad_metrics_row(path, "ad_other", "2026-07-21", spend=5.0, begin_checkout=5, purchases=2)

    rows = get_creative_concept_breakdown(path, "2026-07-21", "2026-07-21")
    by_concept = {r["concept"]: r for r in rows}

    assert by_concept["QUIZ-01"]["ad_copies"] == 2
    assert by_concept["QUIZ-01"]["bc"] == 3
    assert by_concept["QUIZ-01"]["purchases"] == 1
    assert by_concept["QUIZ-01"]["cost_per_bc"] == pytest.approx(20.0 / 3, rel=1e-3)

    assert by_concept["OTHER-CONCEPT"]["bc"] == 5
    assert by_concept["OTHER-CONCEPT"]["purchases"] == 2
    assert by_concept["OTHER-CONCEPT"]["cost_per_bc"] == pytest.approx(1.0)

    # Ranked by cost-per-BC ascending -> OTHER-CONCEPT ($1.00) before QUIZ-01 (~$6.67)
    assert [r["concept"] for r in rows] == ["OTHER-CONCEPT", "QUIZ-01"]
    assert "fsd" not in rows[0]
    assert "cpr_fsd" not in rows[0]


@pytest.mark.asyncio
async def test_get_creative_concept_breakdown_zero_bc_sorts_last(db_client) -> None:
    path = db_client._path
    _insert_ad_creative(path, "ad_signal", "Nowa | SIGNAL-CONCEPT | broad | single_image | 20260715")
    _insert_ad_creative(path, "ad_nosignal", "Nowa | NOSIGNAL-CONCEPT | broad | single_image | 20260715")
    _insert_ad_metrics_row(path, "ad_signal", "2026-07-21", spend=10.0, begin_checkout=1)
    _insert_ad_metrics_row(path, "ad_nosignal", "2026-07-21", spend=10.0, begin_checkout=0)

    rows = get_creative_concept_breakdown(path, "2026-07-21", "2026-07-21")
    assert rows[-1]["concept"] == "NOSIGNAL-CONCEPT"
    assert rows[-1]["cost_per_bc"] is None


# ---------------------------------------------------------------------------
# Section 3: AppTest boot against the real data/metrics.db (read-only)
# ---------------------------------------------------------------------------
def test_ads_page_boots_with_apptest_against_real_db(monkeypatch: pytest.MonkeyPatch) -> None:
    """Boot the real page against the real, read-only data/metrics.db and
    assert no exception. Every db.py call this page makes is a SELECT, so this
    does not mutate the production database."""
    try:
        from streamlit.testing.v1 import AppTest
    except ImportError:
        pytest.skip("streamlit.testing.v1.AppTest unavailable")

    if not REAL_DB_PATH.exists():
        pytest.skip(f"Real DB not found at {REAL_DB_PATH} — nothing to boot against")

    monkeypatch.setenv("DB_PATH", str(REAL_DB_PATH.resolve()))
    monkeypatch.setenv("DASHBOARD_PASSWORD", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")

    at = AppTest.from_file(str(PAGE_PATH), default_timeout=30)
    at.run()
    assert not at.exception, f"2_Ads.py raised: {[str(e) for e in at.exception]}"

    titles = [t.value for t in at.title]
    assert any("Ad Creative Analysis" in t for t in titles)
