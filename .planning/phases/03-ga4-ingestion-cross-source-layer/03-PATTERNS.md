# Phase 3: GA4 Ingestion + Cross-Source Layer - Pattern Map

**Mapped:** 2026-05-19
**Files analyzed:** 13 new/modified files
**Analogs found:** 13 / 13

---

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `src/ga4/__init__.py` | package-init | — | `src/meta/__init__.py` | exact |
| `src/ga4/client.py` | service | request-response | `src/meta/client.py` | exact |
| `src/ga4/ingest.py` | service (scheduler job) | batch | `src/meta/ingest.py` | exact |
| `src/db/schema.py` | config (DDL) | CRUD | `src/db/schema.py` existing | self (extend) |
| `src/db/client.py` | service (data access) | CRUD | `src/db/client.py` existing | self (extend) |
| `src/config.py` | config | — | `src/config.py` existing | self (extend) |
| `src/reports/builder.py` | utility (HTML assembly) | transform | `src/reports/builder.py` existing | self (extend) |
| `src/reports/daily.py` | service (scheduler job) | request-response | `src/reports/daily.py` existing | self (extend) |
| `src/reports/weekly.py` | service (scheduler job) | request-response | `src/reports/weekly.py` existing | self (extend) |
| `src/main.py` | config (wiring) | — | `src/main.py` existing | self (extend) |
| `tests/test_ga4_client.py` | test | — | `tests/test_meta_client.py` | exact |
| `tests/test_ga4_ingest.py` | test | — | `tests/test_meta_ingest.py` | exact |
| `tests/test_cross_source.py` | test | — | `tests/test_meta_ingest.py` (partial) | role-match |

---

## Pattern Assignments

### `src/ga4/__init__.py` (package-init)

**Analog:** `src/meta/__init__.py`

**Full file content to copy** (line 1):
```python
"""Google Analytics 4 Data API integration package (Phase 3)."""
```

---

### `src/ga4/client.py` (service, request-response)

**Analog:** `src/meta/client.py`

**Imports pattern** (`src/meta/client.py` lines 1-31):
```python
from __future__ import annotations

import asyncio
import logging

import structlog
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)
# GA4 equivalents replace the facebook_business imports:
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    DateRange, Dimension, Filter, FilterExpression, Metric, RunReportRequest,
)

logger = structlog.get_logger(__name__)
_stdlib_log = logging.getLogger(__name__)
```

**Sync-function-wrapped-in-async pattern** (`src/meta/client.py` lines 119-156):
```python
def _fetch_insights_sync(ad_account_id: str, date_iso: str, level: str) -> list[dict]:
    """Synchronous Meta API call — called via asyncio.to_thread() from async context."""
    # ... blocking SDK work here ...
    return rows

@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=2, max=60),
    retry=retry_if_exception_type(FacebookRequestError),
    before_sleep=before_sleep_log(_stdlib_log, logging.WARNING),
    reraise=True,
)
async def fetch_campaign_insights(ad_account_id: str, date_iso: str) -> list[dict]:
    logger.info("meta_fetch_start", level="campaign", date=date_iso)
    rows = await asyncio.to_thread(_fetch_insights_sync, ad_account_id, date_iso, "campaign")
    logger.info("meta_fetch_complete", level="campaign", date=date_iso, rows=len(rows))
    return rows
```

**GA4-specific: row-parsing pattern** — parse `response.dimension_headers` + `response.metric_headers` zip:
```python
# Applied to both RunReportRequest calls
rows = []
for row in response.rows:
    dim_vals = {h.name: v.value for h, v in zip(response.dimension_headers, row.dimension_values)}
    met_vals = {h.name: v.value for h, v in zip(response.metric_headers, row.metric_values)}
    rows.append({**dim_vals, **met_vals})
return rows
```

**GA4-specific: service account init** (`str()` cast required — Pitfall 5 from RESEARCH.md):
```python
def _build_ga4_client(service_account_path) -> BetaAnalyticsDataClient:
    return BetaAnalyticsDataClient.from_service_account_file(str(service_account_path))
```

**GA4-specific: filter-out "(not set)" rows** (Pitfall 7 from RESEARCH.md — use `notExpression`):
```python
dimension_filter=FilterExpression(
    not_expression=FilterExpression(
        filter=Filter(
            field_name="sessionCampaignName",
            string_filter=Filter.StringFilter(value="(not set)"),
        )
    )
),
return_property_quota=True,   # GA4-04: always track quota
keep_empty_rows=False,
```

**Retry exception type for GA4** — use `google.api_core.exceptions.GoogleAPIError` or `Exception` (tenacity `reraise=True` pattern identical to Meta client).

---

### `src/ga4/ingest.py` (service, batch/APScheduler job)

**Analog:** `src/meta/ingest.py` — exact structural clone

**Module docstring + globals pattern** (`src/meta/ingest.py` lines 1-42):
```python
"""GA4 ingest job: APScheduler-compatible zero-arg async function.

GA4-01: Authenticates via service account JSON file (BetaAnalyticsDataClient).
GA4-02: Two RunReportRequest calls — campaign-level + landing-page-level.
GA4-03: D-2 freshness (_get_d2_iso).
GA4-04: 6-hour cache check via ingestion_log. returnPropertyQuota=True.
GA4-05: Writes to ga4_metrics + ga4_landing_pages via UPSERT helpers.
D-09: Circuit breaker after 3 consecutive failures (same as Meta).

CRITICAL: ga4_ingest_job takes NO args (APScheduler PicklingError).
"""
from __future__ import annotations

import html
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import structlog
from aiogram.enums import ParseMode

logger = structlog.get_logger(__name__)

_bot = None
_db = None
_settings = None

_RECENT_FAILURES_SQL = """
    SELECT status FROM ingestion_log
    WHERE source = :source
    ORDER BY started_at DESC
    LIMIT :limit
"""
```

**register_job_resources pattern** (`src/meta/ingest.py` lines 44-55):
```python
def register_job_resources(bot, db, settings) -> None:
    global _bot, _db, _settings
    _bot = bot
    _db = db
    _settings = settings
    logger.info("ingest_resources_registered")
```

**D-2 date helper** (replaces `_get_yesterday_iso` — key difference: `timedelta(days=2)` not `days=1`):
```python
def _get_d2_iso(timezone_str: str) -> str:
    """D-10: GA4 D-2 freshness per CLAUDE.md — avoid incomplete-day quota issues."""
    tz = ZoneInfo(timezone_str)
    d2 = datetime.now(tz).date() - timedelta(days=2)
    return d2.isoformat()
```

**Circuit breaker (copy verbatim)** (`src/meta/ingest.py` lines 69-80):
```python
async def _check_circuit_breaker(db, source: str, threshold: int = 3) -> bool:
    rows = await db.fetch_all(
        _RECENT_FAILURES_SQL,
        {"source": source, "limit": threshold},
    )
    if len(rows) < threshold:
        return False
    return all(r.get("status") == "failed" for r in rows)
```

**Core ingest logic structure** (`src/meta/ingest.py` lines 83-171 — adapt for GA4):
```python
async def _run_ga4_ingest(bot, db, settings) -> None:
    date_iso = _get_d2_iso(settings.report_timezone)
    log_id: int | None = None

    try:
        # Credential guard (Pitfall 6 from RESEARCH.md)
        if not settings.ga4_property_id or not settings.ga4_service_account_json:
            logger.warning("ga4_ingest_skipped_no_credentials", date=date_iso)
            return

        # D-12: 6-hour cache check BEFORE log_ingestion_start
        recent = await db.fetch_one(
            "SELECT id FROM ingestion_log WHERE source = 'ga4' AND status = 'success' "
            "AND started_at > datetime('now', '-6 hours')"
        )
        if recent:
            logger.info("ga4_ingest_skipped_cache_hit")
            return

        logger.info("ingest_start", source="ga4", date=date_iso)
        log_id = await db.log_ingestion_start("ga4")   # D-13: source='ga4'

        # ... API calls, upserts ...

        await db.log_ingestion_finish(log_id, "success", rows_upserted=total)

    except Exception as exc:
        logger.error("ingest_failed", source="ga4", date=date_iso, error=str(exc))
        if log_id is not None:
            await db.log_ingestion_finish(log_id, "failed", error=str(exc))

        # Circuit breaker — same pattern as Meta (lines 154-170)
        try:
            if await _check_circuit_breaker(db, "ga4"):
                chat_id = next(iter(settings.telegram_allowed_chat_ids), None)
                if chat_id and bot:
                    safe_error = html.escape(str(exc)[:200])
                    await bot.send_message(
                        chat_id=chat_id,
                        text=(
                            f"🚨 <b>GA4 Ingest Circuit Breaker</b>\n"
                            f"3 consecutive failures. Last error:\n"
                            f"<code>{safe_error}</code>"
                        ),
                        parse_mode=ParseMode.HTML,
                    )
        except Exception as cb_exc:
            logger.error("circuit_breaker_alert_failed", error=str(cb_exc))
```

**Zero-arg job entry point** (`src/meta/ingest.py` lines 173-186):
```python
async def ga4_ingest_job() -> None:
    """APScheduler job entry point — zero args, resources from module globals."""
    if _bot is None or _db is None or _settings is None:
        logger.error("ingest_job_resources_not_registered")
        return
    await _run_ga4_ingest(_bot, _db, _settings)
```

---

### `src/db/schema.py` (config/DDL, extend)

**Analog:** `src/db/schema.py` existing — extend `ALL_MIGRATIONS` list

**Migration constant pattern** (`src/db/schema.py` lines 110-130):
```python
# ---------------------------------------------------------------------------
# Migration 002 — Phase 2: alert deduplication log
# ---------------------------------------------------------------------------

MIGRATION_002_PHASE2: str = """
CREATE TABLE IF NOT EXISTS alert_log (
    ...
);
CREATE INDEX IF NOT EXISTS idx_alert_log_date ON alert_log(date DESC);
"""
```

**New migration to add** (append after `MIGRATION_002_PHASE2`):
```python
# ---------------------------------------------------------------------------
# Migration 003 — Phase 3: GA4 landing pages table
# ---------------------------------------------------------------------------

MIGRATION_003_PHASE3: str = """
CREATE TABLE IF NOT EXISTS ga4_landing_pages (
    landing_page              TEXT NOT NULL,
    date                      TEXT NOT NULL,
    sessions                  INTEGER,
    total_users               INTEGER,
    ga4_purchases_lastclick   INTEGER,
    screen_page_views         INTEGER,
    avg_engagement_time       REAL,
    fetched_at                TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (landing_page, date)
);
CREATE INDEX IF NOT EXISTS idx_ga4_lp_date ON ga4_landing_pages(date);
CREATE INDEX IF NOT EXISTS idx_ga4_lp_page ON ga4_landing_pages(landing_page);
"""
```

**Registry update** (`src/db/schema.py` lines 127-130):
```python
ALL_MIGRATIONS: list[tuple[str, str]] = [
    ("001_initial", MIGRATION_001_INITIAL),
    ("002_phase2", MIGRATION_002_PHASE2),
    ("003_phase3", MIGRATION_003_PHASE3),   # add this line
]
```

---

### `src/db/client.py` (service, CRUD, extend)

**Analog:** `src/db/client.py` existing — add `upsert_ga4_landing_pages` following `upsert_ga4_metrics`

**Existing upsert pattern to clone** (`src/db/client.py` lines 86-116):
```python
_UPSERT_GA4_METRICS_SQL = """
    INSERT INTO ga4_metrics (
        campaign_utm, date, sessions, users, new_users, bounce_rate,
        avg_engagement_time, ga4_purchases_lastclick
    ) VALUES (
        :campaign_utm, :date, :sessions, :users, :new_users, :bounce_rate,
        :avg_engagement_time, :ga4_purchases_lastclick
    )
    ON CONFLICT(campaign_utm, date) DO UPDATE SET
        sessions                = excluded.sessions,
        ...
        fetched_at              = datetime('now');
"""

async def upsert_ga4_metrics(self, rows: list[dict]) -> int:
    if not rows:
        return 0
    await self.conn.executemany(self._UPSERT_GA4_METRICS_SQL, rows)
    await self.conn.commit()
    return len(rows)
```

**New method to add** (immediately after `upsert_ga4_metrics`, same structure):
```python
_UPSERT_GA4_LANDING_PAGES_SQL = """
    INSERT INTO ga4_landing_pages (
        landing_page, date, sessions, total_users,
        ga4_purchases_lastclick, screen_page_views, avg_engagement_time
    ) VALUES (
        :landing_page, :date, :sessions, :total_users,
        :ga4_purchases_lastclick, :screen_page_views, :avg_engagement_time
    )
    ON CONFLICT(landing_page, date) DO UPDATE SET
        sessions                = excluded.sessions,
        total_users             = excluded.total_users,
        ga4_purchases_lastclick = excluded.ga4_purchases_lastclick,
        screen_page_views       = excluded.screen_page_views,
        avg_engagement_time     = excluded.avg_engagement_time,
        fetched_at              = datetime('now');
"""

async def upsert_ga4_landing_pages(self, rows: list[dict]) -> int:
    if not rows:
        return 0
    await self.conn.executemany(self._UPSERT_GA4_LANDING_PAGES_SQL, rows)
    await self.conn.commit()
    return len(rows)
```

---

### `src/config.py` (config, extend)

**Analog:** `src/config.py` existing — add one field under the `# ---- GA4 (Phase 3) ----` block

**Existing GA4 block** (`src/config.py` lines 41-43):
```python
# ---- GA4 (Phase 3) ----
ga4_property_id: str | None = None
ga4_service_account_json: Path | None = None
```

**New field to add** (after `ga4_service_account_json`):
```python
ga4_conversion_event: str = "purchase"   # D-08: override per deployment
```

No validator needed — plain `str` with default. Pattern matches `alert_spend_spike_pct: float = 50.0` style (lines 33-37).

---

### `src/reports/builder.py` (utility, transform, extend)

**Analog:** `src/reports/builder.py` existing

**Signature extension pattern** — add optional GA4 params with `None` defaults (no breaking change):

Current signature (`src/reports/builder.py` lines 47-51):
```python
def build_daily_report_html(
    rows: list[dict],
    tldr: str | None,
    date_str: str,
) -> str:
```

Extended signature (Phase 3):
```python
def build_daily_report_html(
    rows: list[dict],
    tldr: str | None,
    date_str: str,
    ga4_campaign_rows: list[dict] | None = None,
    ga4_landing_rows: list[dict] | None = None,
) -> str:
```

**html.escape on all dynamic strings** (`src/reports/builder.py` lines 65, 100, 103, 113, 195):
```python
safe_date = html.escape(date_str)
name = html.escape(str(r.get("campaign_name", r.get("campaign_id", ""))))
```

**parts list assembly pattern** (`src/reports/builder.py` lines 66-117):
```python
parts: list[str] = []
parts.append(f"<b>📊 Daily Meta Ads Report — {safe_date}</b>")
parts.append("")
# ... build sections ...
return "\n".join(parts)
```

**GA4 section to append at end** (after existing Meta content, before `return "\n".join(parts)`):
```python
if ga4_campaign_rows or ga4_landing_rows:
    parts.append("")
    parts.append("<b>--- Website (GA4) ---</b>")
    # sessions total
    # top 3 landing pages by ga4_purchases_lastclick
    # attribution comparison for UTM-matched campaigns
    # UTM coverage warning at bottom (D-06, D-07)
```

**Attribution comparison line format** (D-02):
```python
# For each UTM-matched campaign:
meta_val = int(meta_row.get("meta_purchases_7dclick", 0) or 0)
ga4_val = int(ga4_row.get("ga4_purchases_lastclick", 0) or 0)
safe_name = html.escape(campaign_name)
parts.append(
    f"<b>{safe_name}</b> — Purchases: Meta 7d-click: {meta_val} | "
    f"GA4 last-click: {ga4_val}  "
    f"<i>(Attribution difference is normal — Meta counts across 7 days, "
    f"GA4 uses last-click on conversion day.)</i>"
)
```

**UTM coverage warning line format** (D-06, always at bottom of GA4 section):
```python
if unmatched > 0:
    parts.append(
        f"⚠️ UTM coverage: {matched}/{total} campaigns matched to GA4. "
        f"{unmatched} campaigns have no website data "
        f"(UTM tags missing or inconsistent)."
    )
```

**WoW delta for landing pages** (D-05 for weekly, reuse existing `_fmt_delta`):
```python
# Landing page sessions WoW:
parts.append(f"Sessions: {_fmt_delta(float(curr_sessions), float(prev_sessions) if prev_sessions else None, unit='')}")
```

The `_fmt_delta` helper (`src/reports/builder.py` lines 29-44`) is already defined and reusable for integer counts — pass `unit=''` to use the non-`$` branch.

---

### `src/reports/daily.py` (service, request-response, extend)

**Analog:** `src/reports/daily.py` existing

**SQL constants pattern** (`src/reports/daily.py` lines 40-61) — add two new SQL constants:
```python
# Named params (CLAUDE.md: no f-string SQL)
_GA4_CAMPAIGN_SQL = """
    SELECT campaign_utm, date, sessions, users, ga4_purchases_lastclick
    FROM ga4_metrics
    WHERE date = :target_date
    ORDER BY sessions DESC;
"""

_GA4_LANDING_SQL = """
    SELECT landing_page, date, sessions, total_users,
           ga4_purchases_lastclick, screen_page_views
    FROM ga4_landing_pages
    WHERE date = :target_date
    ORDER BY ga4_purchases_lastclick DESC
    LIMIT 10;
"""

# 7-day window for landing page trend (D-04)
_GA4_LANDING_7DAY_SQL = """
    SELECT landing_page,
           SUM(sessions) AS sessions_7d,
           SUM(ga4_purchases_lastclick) AS conv_7d
    FROM ga4_landing_pages
    WHERE date BETWEEN :start_date AND :end_date
    GROUP BY landing_page
    ORDER BY conv_7d DESC
    LIMIT 10;
"""
```

**DB query + pass to builder pattern** (`src/reports/daily.py` lines 106-126):
```python
# Existing:
yesterday_rows = await db.fetch_all(_YESTERDAY_METRICS_SQL, {"target_date": yesterday})

# Add after existing queries:
ga4_campaign_rows = await db.fetch_all(_GA4_CAMPAIGN_SQL, {"target_date": yesterday})
ga4_landing_rows = await db.fetch_all(_GA4_LANDING_SQL, {"target_date": yesterday})

# Pass to builder (extended signature):
report_text = build_daily_report_html(
    yesterday_rows, tldr, yesterday,
    ga4_campaign_rows=ga4_campaign_rows,
    ga4_landing_rows=ga4_landing_rows,
)
```

**D-2 date alignment** — daily report fires at 09:00, reads yesterday (D-1 in report module). GA4 ingest stored D-2. Planner note: the `target_date` for GA4 queries must be `(today - timedelta(days=2)).isoformat()` to match what ga4_ingest_job stored. Meta queries continue to use yesterday (D-1).

---

### `src/reports/weekly.py` (service, request-response, extend)

**Analog:** `src/reports/weekly.py` existing — same pattern as daily extension

**SQL constants to add** (after `_WEEK_WINDOW_SQL`):
```python
_GA4_WEEKLY_CAMPAIGN_SQL = """
    SELECT campaign_utm,
           SUM(sessions) AS sessions,
           SUM(ga4_purchases_lastclick) AS ga4_purchases_lastclick
    FROM ga4_metrics
    WHERE date BETWEEN :start_date AND :end_date
    GROUP BY campaign_utm
    ORDER BY sessions DESC;
"""

_GA4_LANDING_WOW_SQL = """
    SELECT landing_page,
           SUM(sessions) AS sessions,
           SUM(ga4_purchases_lastclick) AS ga4_purchases_lastclick
    FROM ga4_landing_pages
    WHERE date BETWEEN :start_date AND :end_date
    GROUP BY landing_page
    ORDER BY ga4_purchases_lastclick DESC
    LIMIT 10;
"""
```

**Two-window pattern** (`src/reports/weekly.py` lines 76-84`):
```python
# Existing two-window queries for Meta:
this_week_rows = await db.fetch_all(_WEEK_WINDOW_SQL, {"start_date": ..., "end_date": ...})
last_week_rows = await db.fetch_all(_WEEK_WINDOW_SQL, {"start_date": ..., "end_date": ...})

# Add GA4 two-window queries using same date_ranges dict from get_wow_date_ranges():
ga4_this_week = await db.fetch_all(
    _GA4_LANDING_WOW_SQL,
    {"start_date": date_ranges["week_start"], "end_date": date_ranges["week_end"]},
)
ga4_last_week = await db.fetch_all(
    _GA4_LANDING_WOW_SQL,
    {"start_date": date_ranges["prev_week_start"], "end_date": date_ranges["prev_week_end"]},
)
```

**Extended builder call** (`src/reports/weekly.py` lines 96-99`):
```python
# Existing:
report_text = build_weekly_report_html(this_week_rows, last_week_rows, tldr, week_end)

# Extended:
report_text = build_weekly_report_html(
    this_week_rows, last_week_rows, tldr, week_end,
    ga4_this_week=ga4_this_week,
    ga4_last_week=ga4_last_week,
)
```

---

### `src/main.py` (config/wiring, extend)

**Analog:** `src/main.py` existing

**Import pattern** (`src/main.py` lines 23-25):
```python
import src.meta.ingest as meta_ingest_module
import src.reports.daily as daily_report_module
import src.reports.weekly as weekly_report_module
```

**Add after line 25:**
```python
import src.ga4.ingest as ga4_ingest_module
```

**register_job_resources pattern** (`src/main.py` lines 56-58):
```python
meta_ingest_module.register_job_resources(bot, db, settings)
daily_report_module.register_job_resources(bot, db, settings)
weekly_report_module.register_job_resources(bot, db, settings)
```

**Add after line 58:**
```python
ga4_ingest_module.register_job_resources(bot, db, settings)
```

**scheduler.add_job pattern** (`src/main.py` lines 66-73):
```python
scheduler.add_job(
    meta_ingest_module.meta_ingest_job,
    trigger=CronTrigger(hour=settings.meta_ingest_hour, minute=0, timezone=settings.report_timezone),
    id="meta_ingest",
    replace_existing=True,
    misfire_grace_time=300,
    coalesce=True,
    max_instances=1,
)
```

**Add before the meta_ingest job** (GA4 runs at 01:00, before Meta at 02:00 per D-09/specifics):
```python
scheduler.add_job(
    ga4_ingest_module.ga4_ingest_job,
    trigger=CronTrigger(hour=1, minute=0, timezone=settings.report_timezone),
    id="ga4_ingest",
    replace_existing=True,
    misfire_grace_time=300,
    coalesce=True,
    max_instances=1,
)
```

No `settings.ga4_ingest_hour` config field is needed — the time is fixed at 01:00 per the phase decisions. (Contrast with `meta_ingest_hour` which is configurable.)

---

### `tests/test_ga4_client.py` (test)

**Analog:** `tests/test_meta_client.py`

**Test file structure to copy** (`tests/test_meta_client.py` lines 1-10):
```python
"""Tests for src/ga4/client.py — row parsing and response normalization.

RED phase: These tests verify _parse_campaign_row and _parse_landing_row.
fetch_* functions tested separately with SDK mocks.
"""
from __future__ import annotations

import pytest
```

**Test pattern for parser functions** (`tests/test_meta_client.py` lines 56-158):
```python
@pytest.fixture
def sample_campaign_row():
    return { ... }  # raw API response dict

def test_parse_row_campaign_id(sample_campaign_row):
    from src.ga4.client import _parse_campaign_row
    row = _parse_campaign_row(sample_campaign_row, "2026-05-17")
    assert row["campaign_utm"] == "spring_sale"

def test_parse_row_missing_fields_no_error():
    from src.ga4.client import _parse_campaign_row
    row = _parse_campaign_row({}, "2026-05-17")
    assert row["sessions"] == 0
    assert row["ga4_purchases_lastclick"] == 0
```

**Test pattern for coroutine verification** (`tests/test_meta_client.py` lines 200-217):
```python
def test_fetch_campaign_metrics_is_coroutine():
    import inspect
    from src.ga4.client import fetch_campaign_metrics
    assert inspect.iscoroutinefunction(fetch_campaign_metrics)

def test_fetch_landing_page_metrics_is_coroutine():
    import inspect
    from src.ga4.client import fetch_landing_page_metrics
    assert inspect.iscoroutinefunction(fetch_landing_page_metrics)
```

**Key GA4-specific tests to add** (no Meta analog — address RESEARCH.md pitfalls):
```python
def test_parse_campaign_row_filters_not_set():
    """Pitfall 7: (not set) rows must not reach DB — verify parser or ingest filters them."""

def test_landing_page_uses_correct_dimension_name():
    """Pitfall 1: dimension name must be landingPagePlusQueryString, not landingPage."""
    from src.ga4.client import _LANDING_PAGE_DIMENSION
    assert _LANDING_PAGE_DIMENSION == "landingPagePlusQueryString"

def test_keyevents_metric_name():
    """Pitfall 1: metric must be keyEvents, not conversions."""
    from src.ga4.client import _CONVERSION_METRIC
    assert _CONVERSION_METRIC == "keyEvents"
```

---

### `tests/test_ga4_ingest.py` (test)

**Analog:** `tests/test_meta_ingest.py`

**Full test file structure to copy** (`tests/test_meta_ingest.py` lines 1-53):
```python
"""Prove GA4 ingest: upsert idempotency and circuit breaker."""
from __future__ import annotations
from unittest.mock import MagicMock
import pytest
from src.ga4.ingest import _check_circuit_breaker, register_job_resources

pytestmark = pytest.mark.asyncio


async def test_upsert_ga4_metrics_idempotent(db_client):
    """GA4-05: Re-upserting same (campaign_utm, date) does not duplicate rows."""
    row = {
        "campaign_utm": "spring_sale", "date": "2026-05-17",
        "sessions": 100, "users": 80, "new_users": 40,
        "bounce_rate": 0.45, "avg_engagement_time": 60.0,
        "ga4_purchases_lastclick": 5,
    }
    await db_client.upsert_ga4_metrics([row])
    await db_client.upsert_ga4_metrics([row])
    rows = await db_client.fetch_all(
        "SELECT * FROM ga4_metrics WHERE campaign_utm = 'spring_sale'"
    )
    assert len(rows) == 1


async def test_upsert_ga4_landing_pages_idempotent(db_client):
    """GA4-05: Re-upserting same (landing_page, date) does not duplicate rows."""
    # ... same pattern ...


async def test_circuit_breaker_not_triggered_under_threshold(db_client):
    """D-08: Circuit breaker must NOT trigger with < 3 consecutive failures."""
    log_id = await db_client.log_ingestion_start("ga4")
    await db_client.log_ingestion_finish(log_id, "failed", error="test error")
    result = await _check_circuit_breaker(db_client, "ga4", threshold=3)
    assert result is False


async def test_register_job_resources_sets_globals():
    import src.ga4.ingest as ingest_module
    mock_bot = MagicMock()
    mock_db = MagicMock()
    mock_settings = MagicMock()
    register_job_resources(mock_bot, mock_db, mock_settings)
    assert ingest_module._bot is mock_bot
    # Clean up
    ingest_module._bot = None
    ingest_module._db = None
    ingest_module._settings = None
```

**GA4-specific test to add** (cache check logic):
```python
async def test_6h_cache_check_skips_ingest(db_client):
    """D-12: If successful GA4 run within 6h, ingest is skipped."""
    log_id = await db_client.log_ingestion_start("ga4")
    await db_client.log_ingestion_finish(log_id, "success", rows_upserted=10)
    recent = await db_client.fetch_one(
        "SELECT id FROM ingestion_log WHERE source='ga4' AND status='success' "
        "AND started_at > datetime('now', '-6 hours')"
    )
    assert recent is not None
```

---

### `tests/test_cross_source.py` (test)

**Analog:** `tests/test_meta_ingest.py` (partial — uses `db_client` fixture, same `pytestmark`)

**Test file header:**
```python
"""Cross-source join, UTM coverage, and attribution comparison tests.

CROSS-01: Exact UTM match only.
CROSS-02: Side-by-side attribution comparison.
CROSS-03: UTM coverage warning when campaigns cannot be matched.
"""
from __future__ import annotations
import pytest

pytestmark = pytest.mark.asyncio
```

**conftest.py `db_client` fixture** (`tests/conftest.py` lines 11-24) — reuse as-is; it applies all migrations including MIGRATION_003 once that is registered.

**Key tests to write:**
```python
async def test_utm_exact_match_only(db_client):
    """CROSS-01: Fuzzy or case-insensitive matches must NOT produce a join."""
    # Insert ga4_metrics with campaign_utm='spring_sale'
    # Insert campaigns with name='Spring Sale' (different case)
    # Verify UTM join returns 0 rows

async def test_utm_coverage_zero_unmatched(db_client):
    """CROSS-03: All campaigns matched → coverage line omitted."""
    # Insert matching campaign + ga4_metrics
    # Compute: unmatched = 0 → no warning line

async def test_utm_coverage_partial_match(db_client):
    """CROSS-03: 1 of 2 campaigns unmatched → warning line shows correct counts."""

async def test_attribution_comparison_format():
    """CROSS-02: Attribution line contains both meta_val and ga4_val side-by-side."""
    from src.reports.builder import build_daily_report_html
    # Pass rows with both meta and ga4 data for same campaign
    # Assert output contains "Meta 7d-click" and "GA4 last-click"
    # Assert output contains attribution explanation text

async def test_ga4_section_omitted_when_no_data():
    """GA4 section not shown when ga4_campaign_rows is None or empty."""
    from src.reports.builder import build_daily_report_html
    html = build_daily_report_html([], None, "2026-05-17")
    assert "Website (GA4)" not in html
```

---

## Shared Patterns

### Module-Globals APScheduler Pattern
**Source:** `src/meta/ingest.py` lines 31-55
**Apply to:** `src/ga4/ingest.py` (exact clone), `src/reports/daily.py`, `src/reports/weekly.py` (already use it)
```python
_bot = None
_db = None
_settings = None

def register_job_resources(bot, db, settings) -> None:
    global _bot, _db, _settings
    _bot = bot
    _db = db
    _settings = settings
    logger.info("ingest_resources_registered")
```

### asyncio.to_thread() for Sync SDKs
**Source:** `src/meta/client.py` lines 154, 162, 170
**Apply to:** `src/ga4/client.py` for both `_fetch_campaign_metrics_sync` and `_fetch_landing_page_metrics_sync`
```python
rows = await asyncio.to_thread(_fetch_insights_sync, ad_account_id, date_iso, "campaign")
```

### ingestion_log Lifecycle
**Source:** `src/db/client.py` lines 136-175, `src/meta/ingest.py` lines 88-136
**Apply to:** `src/ga4/ingest.py` — use `source='ga4'`, same `log_ingestion_start` / `log_ingestion_finish`
```python
log_id = await db.log_ingestion_start("ga4")
# ... work ...
await db.log_ingestion_finish(log_id, "success", rows_upserted=total)
```

### Named-Parameter SQL (No F-String SQL)
**Source:** `src/db/client.py` lines 63-102, `src/reports/daily.py` lines 40-61
**Apply to:** All new SQL constants in `src/reports/daily.py`, `src/reports/weekly.py`, `src/ga4/ingest.py`
```python
# Always:
await db.fetch_all(_GA4_CAMPAIGN_SQL, {"target_date": date_iso})
# Never:
f"SELECT * FROM ga4_metrics WHERE date = '{date_iso}'"
```

### html.escape() on All Dynamic Strings
**Source:** `src/reports/builder.py` lines 65, 73, 100, 110, 195
**Apply to:** All landing page paths, campaign names, UTM values in `src/reports/builder.py` GA4 section
```python
safe_page = html.escape(lp.get("landing_page", ""))
safe_name = html.escape(str(campaign_name))
```

### Credential Guard + Early Return
**Source:** `src/meta/ingest.py` lines 93-96
**Apply to:** `src/ga4/ingest.py` `_run_ga4_ingest()` — check `ga4_property_id` and `ga4_service_account_json`
```python
if not settings.ga4_property_id or not settings.ga4_service_account_json:
    logger.warning("ga4_ingest_skipped_no_credentials", date=date_iso)
    return
```

### tenacity Retry Decorator
**Source:** `src/meta/client.py` lines 140-146
**Apply to:** All `async def fetch_*` functions in `src/ga4/client.py`
```python
@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=2, max=60),
    retry=retry_if_exception_type(GoogleAPIError),   # or Exception with reraise=True
    before_sleep=before_sleep_log(_stdlib_log, logging.WARNING),
    reraise=True,
)
```

### UPSERT Helper Pattern
**Source:** `src/db/client.py` lines 104-116
**Apply to:** New `upsert_ga4_landing_pages` method in `src/db/client.py`
```python
async def upsert_X(self, rows: list[dict]) -> int:
    if not rows:
        return 0
    await self.conn.executemany(self._UPSERT_X_SQL, rows)
    await self.conn.commit()
    return len(rows)
```

### ParseMode.HTML + split_html_message
**Source:** `src/reports/daily.py` lines 127-136
**Apply to:** Any new Telegram sends in report jobs; GA4 section is appended before the `split_html_message` call so no new send calls are needed
```python
parts = split_html_message(report_text)
for part in parts:
    await bot.send_message(chat_id=chat_id, text=part, parse_mode=ParseMode.HTML)
```

---

## No Analog Found

All files have close analogs. No files require research-only patterns.

---

## Critical Anti-Patterns (from RESEARCH.md — enforce in code review)

| Anti-pattern | Correct pattern | Source |
|---|---|---|
| `landingPage` dimension | `landingPagePlusQueryString` | RESEARCH.md Pitfall 1 |
| `conversions` metric | `keyEvents` | RESEARCH.md Pitfall 1 |
| `users` metric | `totalUsers` | RESEARCH.md critical findings |
| `averageSessionDuration` | `averageEngagementTimePerSession` | RESEARCH.md critical findings |
| Combine `sessionCampaignName` + `pagePath` in one request | Two separate RunReportRequest calls | RESEARCH.md Pitfall 2 |
| Pass `Path` directly to `from_service_account_file()` | `str(settings.ga4_service_account_json)` | RESEARCH.md Pitfall 5 |
| Store `(not set)` campaign rows | Filter in API request with `notExpression` | RESEARCH.md Pitfall 7 |
| Blend Meta + GA4 conversion numbers | Always side-by-side, never averaged | CLAUDE.md |
| Fuzzy UTM matching | Exact string match only | CLAUDE.md |

---

## Metadata

**Analog search scope:** `src/meta/`, `src/reports/`, `src/db/`, `src/`, `tests/`
**Files scanned:** 11 source files, 3 test files, 1 conftest
**Pattern extraction date:** 2026-05-19
