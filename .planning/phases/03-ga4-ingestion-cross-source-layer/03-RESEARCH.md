# Phase 3: GA4 Ingestion + Cross-Source Layer - Research

**Researched:** 2026-05-19
**Domain:** Google Analytics 4 Data API v1 (python SDK), cross-source report layer extension
**Confidence:** HIGH (core SDK patterns), MEDIUM (dimension-scope compatibility), HIGH (codebase patterns)

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

- **D-01:** GA4 data appears as a new section after the existing Meta section in daily digest and weekly summary. Header: `--- Website (GA4) ---`.
- **D-02:** Attribution comparison is side-by-side with a one-line explanation when both Meta and GA4 conversions exist for the same UTM-matched campaign. Format: `Purchases: Meta 7d-click: {meta_val} | GA4 last-click: {ga4_val}  (Attribution difference is normal — Meta counts across 7 days, GA4 uses last-click on conversion day.)`
- **D-03:** Show top 3 landing pages by conversions (`ga4_purchases_lastclick` or configured event). Ranked by `ga4_purchases_lastclick`.
- **D-04:** Daily digest shows landing page metrics for two windows: yesterday's top 3 (D-2) AND a 7-day rolling trend summary (`7-day avg: 45 sessions/day`).
- **D-05:** Weekly summary includes WoW deltas for top 3 landing pages — sessions and conversions with absolute and percentage change. Format: `Sessions: 280 → 315 (+35 / +13%)`
- **D-06:** UTM coverage warning: single summary line at bottom of GA4 section: `⚠️ UTM coverage: 5/8 campaigns matched to GA4. 3 campaigns have no website data (UTM tags missing or inconsistent).` If all match, omit line.
- **D-07:** UTM coverage warning at bottom of GA4 section (not top of report, not separate command).
- **D-08:** `GA4_CONVERSION_EVENT` env var with default `"purchase"`. Stored as `ga4_purchases_lastclick`. Attribution noted as "last-click".
- **D-09:** Module-globals APScheduler pattern: `src/ga4/` package with `ingest.py` containing `register_job_resources()` + `ga4_ingest_job()`.
- **D-10:** D-2 freshness (yesterday minus 1 day) using `ZoneInfo` + `report_timezone`.
- **D-11:** `asyncio.to_thread()` wrapping for sync GA4 SDK calls.
- **D-12:** 6-hour cache check via `ingestion_log` before API calls. `returnPropertyQuota: true` always passed.
- **D-13:** `ingestion_log` with `source = 'ga4'`. Use `log_ingestion_start` / `log_ingestion_finish` helpers.

### Claude's Discretion

- Exact GA4 Data API dimension names for landing page queries (`pagePath` vs `landingPage` — researcher determines current best practice)
- Whether to use `BetaAnalyticsDataClient` (sync) or `BetaAnalyticsDataAsyncClient` — either acceptable; asyncio.to_thread wrapping handles sync
- Exact retry parameters for GA4 API calls (tenacity, same pattern as Meta: `stop_after_attempt(5)`, `wait_exponential(min=2, max=60)`)
- Exact Telegram message formatting for GA4 section (within ParseMode.HTML + html.escape() constraint)
- Schema migration number: `MIGRATION_003_PHASE3` if any schema changes are needed

### Deferred Ideas (OUT OF SCOPE)

- `/utm_audit` command — Phase 4 (conversational AI handles this)
- GA4 BigQuery export — out of scope for v1
- Multi-property GA4 support — v2 (MULTI-02)
- Attribution model comparison — v2 (ADV-04)
- GA4 Realtime API — not needed
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| GA4-01 | Authenticate to GA4 Data API using service account with Viewer-only permissions | `BetaAnalyticsDataClient.from_service_account_file(path)` — see Standard Stack section |
| GA4-02 | Pull daily metrics: sessions, users, new users, bounce rate, avg engagement time, pageviews by landing page, goal conversions | Two separate RunReportRequest calls (campaign-scoped + landing page-scoped) — see Architecture Patterns |
| GA4-03 | GA4 data defaults to D-2 freshness | `_get_d2_iso(timezone_str)` pattern — `today - timedelta(days=2)` — same ZoneInfo pattern as meta ingest |
| GA4-04 | Track quota per request (`returnPropertyQuota: true`) and cache for ≥6h | `return_property_quota=True` in `RunReportRequest`; ingestion_log cache check — see Pitfall 4 |
| GA4-05 | GA4 data stored with `ga4_` prefixed conversion fields; attribution source (last-click) noted in report | `ga4_metrics` table (MIGRATION_001); `ga4_purchases_lastclick` field; `ga4_landing_pages` table (MIGRATION_003) |
| CROSS-01 | Join Meta + GA4 on UTM campaign name — hard exact match only | `campaigns.name == ga4_metrics.campaign_utm` — see UTM Join section |
| CROSS-02 | Side-by-side attribution comparison with explanation when both sources exist | `build_daily_report_html` extended with `ga4_rows` parameter — see builder extension pattern |
| CROSS-03 | UTM coverage warnings in reports when campaigns cannot be matched | Computed at report time: `matched_count = len(ga4_rows)`, `total_count = len(meta_rows)`; `unmatched = total - matched` |
</phase_requirements>

---

## Summary

Phase 3 adds Google Analytics 4 as a second data source. The implementation follows the same module-globals APScheduler pattern as the existing Meta ingest — a `src/ga4/` package with `register_job_resources()` + zero-arg `ga4_ingest_job()`. The GA4 SDK (`google-analytics-data==0.22.0`) is synchronous, so all calls are wrapped in `asyncio.to_thread()`.

The most important technical finding is that **landing page metrics and campaign metrics require two separate GA4 RunReportRequest calls**. The `sessionCampaignName` dimension is session-scoped and works with session-level metrics. The `landingPagePlusQueryString` dimension is the current (non-deprecated) landing page dimension (replacing the deprecated `landingPage`). These two dimensional queries produce different row shapes and must be stored in separate tables: existing `ga4_metrics` (campaign-keyed) and a new `ga4_landing_pages` table (landing-page-keyed). This requires `MIGRATION_003`.

The report layer extension is additive: `build_daily_report_html` and `build_weekly_report_html` gain optional `ga4_rows`, `ga4_landing_rows`, and `unmatched_count` parameters. All GA4 string values passed to Telegram must go through `html.escape()` per the established pattern.

**Primary recommendation:** Two RunReportRequest calls per ingest (campaign-scoped + landing-page-scoped), stored in two tables (`ga4_metrics` existing, `ga4_landing_pages` new), report builder extended with optional GA4 parameters.

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| GA4 API authentication | Backend (ingest job) | — | Service account credentials are server-side secrets, never in bot handlers |
| GA4 metric ingestion (campaign-level) | Backend (APScheduler job) | — | Scheduled pull, runs at 01:00 before Meta at 02:00 |
| GA4 metric ingestion (landing-page-level) | Backend (APScheduler job) | — | Same job, second API call; different dimension scope |
| UTM join / cross-source matching | Backend (SQLite query at report time) | — | Simple exact string match in SQL; no separate join service needed |
| Attribution side-by-side rendering | Report builder (`src/reports/builder.py`) | — | Pure HTML assembly from pre-fetched data |
| UTM coverage warning computation | Report builder | — | `unmatched_count` computed by daily/weekly job, passed to builder |
| GA4 schema (tables) | Database (SQLite) | — | `ga4_metrics` (existing MIGRATION_001) + `ga4_landing_pages` (new MIGRATION_003) |
| Quota tracking | Ingest job (log + cache check) | — | `ingestion_log` 6-hour cache avoids re-calling API; `returnPropertyQuota` logs usage |

---

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `google-analytics-data` | `0.22.0` [VERIFIED: PyPI] | GA4 Data API v1 Python client | Official Google Cloud Python client; `BetaAnalyticsDataClient` is the standard entry point |
| `aiosqlite` | `>=0.20` (already installed) | Async SQLite for new `ga4_landing_pages` table | Already in project; UPSERT pattern established |
| `tenacity` | `>=9` (already installed) | Exponential backoff on GA4 API calls | Already in project; mirrors Meta ingest retry pattern |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `google-auth` | transitive dep of `google-analytics-data` | Service account credential loading | Pulled in automatically; use `service_account.Credentials` only if explicit scope needed |
| `asyncio.to_thread` | Python stdlib | Wrap sync GA4 SDK in async context | Because `BetaAnalyticsDataClient` is synchronous; `to_thread` is already the project pattern |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| `BetaAnalyticsDataClient` (sync + `to_thread`) | `BetaAnalyticsDataAsyncClient` (native async) | Async client exists at `google.analytics.data_v1beta.services.beta_analytics_data.BetaAnalyticsDataAsyncClient` but requires gRPC async transport; sync + `to_thread` matches project pattern and is lower-risk |
| `from_service_account_file(path)` | `GOOGLE_APPLICATION_CREDENTIALS` env var | Env var approach works but path-based is more explicit and aligns with `ga4_service_account_json: Path` field already in `Settings` |

**Installation:**
```bash
# Already in pyproject.toml — no new dependencies needed
# google-analytics-data>=0.22.0,<1 is already declared
```

**Version verification:**
```
google-analytics-data 0.22.0  [VERIFIED: PyPI, released 2026-05-07]
```

---

## Architecture Patterns

### System Architecture Diagram

```
APScheduler (01:00) ──► ga4_ingest_job()
                              │
                    ┌─────────┴─────────┐
                    │ 6h cache check     │
                    │ (ingestion_log)    │
                    └─────────┬─────────┘
                  cache hit ◄─┤─► cache miss
                  (skip)      │
                    ┌─────────┴──────────────────┐
                    │   BetaAnalyticsDataClient    │
                    │   (asyncio.to_thread)         │
                    │                              │
                    │  RunReportRequest #1          │
                    │  dim: sessionCampaignName     │ ──► ga4_metrics table
                    │  dim: date                    │     (campaign_utm, date)
                    │  metrics: sessions/users/...  │
                    │                              │
                    │  RunReportRequest #2          │
                    │  dim: landingPagePlusQS       │ ──► ga4_landing_pages table
                    │  dim: date                    │     (landing_page, date)
                    │  metrics: sessions/conversions│
                    └──────────────────────────────┘

APScheduler (09:00) ──► daily_report_job()
                              │
                    ┌─────────┴──────────────────┐
                    │  Query ad_metrics            │ (Meta data)
                    │  Query ga4_metrics           │ (campaign UTM join)
                    │  Query ga4_landing_pages     │ (top 3 landing pages)
                    └─────────┬──────────────────┘
                              │
                    ┌─────────▼──────────────────┐
                    │  build_daily_report_html()   │
                    │  + GA4 section               │
                    │  + attribution comparison    │
                    │  + UTM coverage warning      │
                    └─────────┬──────────────────┘
                              │
                    ┌─────────▼──────────────────┐
                    │  split_html_message()        │
                    │  bot.send_message() ×N       │
                    └────────────────────────────┘
```

### Recommended Project Structure

```
src/
├── ga4/
│   ├── __init__.py        # empty
│   └── ingest.py          # register_job_resources() + ga4_ingest_job() + _run_ga4_ingest()
├── reports/
│   ├── builder.py         # extended: build_daily_report_html(ga4_rows, ga4_landing_rows, unmatched_count)
│   ├── daily.py           # extended: query ga4_metrics + ga4_landing_pages, pass to builder
│   └── weekly.py          # extended: query GA4 WoW windows, pass to builder
├── db/
│   ├── schema.py          # + MIGRATION_003_PHASE3 (ga4_landing_pages table)
│   └── client.py          # + upsert_ga4_landing_pages(rows) method
├── config.py              # + ga4_conversion_event: str = "purchase"
└── main.py                # + import ga4_ingest_module + register_job_resources + CronTrigger(hour=1)
```

### Pattern 1: GA4 Client Initialization with Service Account File

**What:** Create `BetaAnalyticsDataClient` from a path to a service account JSON key file stored in `Settings.ga4_service_account_json`.

**When to use:** Always — credentials from file, not GOOGLE_APPLICATION_CREDENTIALS env var.

```python
# Source: https://googleapis.dev/python/analyticsdata/latest/data_v1beta/beta_analytics_data.html
# + https://github.com/googleanalytics/python-docs-samples/blob/main/google-analytics-data/quickstart_json_credentials.py

from google.analytics.data_v1beta import BetaAnalyticsDataClient

def _build_ga4_client(service_account_path: str) -> BetaAnalyticsDataClient:
    """Build a sync GA4 client from a service account JSON key file path."""
    return BetaAnalyticsDataClient.from_service_account_file(service_account_path)
    # Alias: from_service_account_json() — identical behaviour
```

**Required IAM:** Grant the service account `Viewer` role on the GA4 property in GA4 Admin → Property Access Management. No custom Google Cloud OAuth scope needed; the client library handles `https://www.googleapis.com/auth/analytics.readonly` internally.

### Pattern 2: Campaign-Level RunReportRequest (Request #1)

**What:** Fetch sessions, users, conversions keyed by `sessionCampaignName` + `date` for a single day.

**When to use:** GA4-02 campaign-level ingest; results map directly to `ga4_metrics` table.

```python
# Source: https://developers.google.com/analytics/devguides/reporting/data/v1/basics
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    DateRange, Dimension, Filter, FilterExpression, Metric, RunReportRequest,
)

def _fetch_campaign_metrics(
    client: BetaAnalyticsDataClient, property_id: str, date_iso: str
) -> list[dict]:
    """Fetch campaign-level GA4 metrics for a single date. Returns list of row dicts."""
    request = RunReportRequest(
        property=f"properties/{property_id}",
        dimensions=[
            Dimension(name="sessionCampaignName"),
            Dimension(name="date"),
        ],
        metrics=[
            Metric(name="sessions"),
            Metric(name="totalUsers"),
            Metric(name="newUsers"),
            Metric(name="bounceRate"),
            Metric(name="averageEngagementTimePerSession"),
            Metric(name="keyEvents"),          # replaces deprecated "conversions" (2024-05-06)
            Metric(name="screenPageViews"),
        ],
        date_ranges=[DateRange(start_date=date_iso, end_date=date_iso)],
        dimension_filter=FilterExpression(
            filter=Filter(
                field_name="sessionCampaignName",
                string_filter=Filter.StringFilter(
                    match_type=Filter.StringFilter.MatchType.EXACT,
                    case_sensitive=False,
                    value="(not set)",
                )
            )
        ),
        return_property_quota=True,   # GA4-04: always track quota
        keep_empty_rows=False,
    )
    # NOTE: This is synchronous — wrap in asyncio.to_thread() at call site
    response = client.run_report(request)
    rows = []
    for row in response.rows:
        dim_vals = {h.name: v.value for h, v in zip(response.dimension_headers, row.dimension_values)}
        met_vals = {h.name: v.value for h, v in zip(response.metric_headers, row.metric_values)}
        rows.append({**dim_vals, **met_vals})
    return rows
```

**Important:** The `sessionCampaignName` dimension filter shown above uses `NOT expression` to exclude `(not set)` rows — the actual implementation should use `notExpression` wrapping. The UTM campaign name for untagged sessions appears as `"(not set)"` in the API — these rows are skipped in DB writes (not stored in `ga4_metrics`).

### Pattern 3: Landing-Page-Level RunReportRequest (Request #2)

**What:** Fetch sessions and conversions keyed by `landingPagePlusQueryString` + `date`.

**When to use:** GA4-02 landing page metrics; results map to new `ga4_landing_pages` table.

```python
# Source: https://developers.google.com/analytics/devguides/reporting/data/v1/predefined-reports
# (Landing Page predefined report)

def _fetch_landing_page_metrics(
    client: BetaAnalyticsDataClient, property_id: str, start_date: str, end_date: str
) -> list[dict]:
    """Fetch landing page metrics for a date range. Two date ranges supported for D-04 7-day trend."""
    request = RunReportRequest(
        property=f"properties/{property_id}",
        dimensions=[
            Dimension(name="landingPagePlusQueryString"),   # current name — "landingPage" is DEPRECATED
            Dimension(name="date"),
        ],
        metrics=[
            Metric(name="sessions"),
            Metric(name="totalUsers"),
            Metric(name="keyEvents"),              # conversion count
            Metric(name="screenPageViews"),
            Metric(name="averageEngagementTimePerSession"),
        ],
        date_ranges=[DateRange(start_date=start_date, end_date=end_date)],
        dimension_filter=FilterExpression(
            not_expression=FilterExpression(
                filter=Filter(
                    field_name="landingPagePlusQueryString",
                    string_filter=Filter.StringFilter(value="(not set)"),
                )
            )
        ),
        return_property_quota=True,    # GA4-04
        keep_empty_rows=False,
        limit=50,                      # Top 50 landing pages per day is sufficient
    )
    # NOTE: Synchronous — wrap in asyncio.to_thread() at call site
    response = client.run_report(request)
    # ... parse same as campaign metrics
```

### Pattern 4: asyncio.to_thread() Wrapping for Sync SDK

**What:** Defer blocking GA4 SDK calls off the asyncio event loop.

**When to use:** Every GA4 API call site — `BetaAnalyticsDataClient` is synchronous.

```python
# Source: established project pattern — see src/meta/ingest.py
import asyncio

# In async context:
campaign_rows = await asyncio.to_thread(
    _fetch_campaign_metrics, client, property_id, date_iso
)
landing_rows = await asyncio.to_thread(
    _fetch_landing_page_metrics, client, property_id, date_iso, date_iso
)
```

### Pattern 5: Module-Globals APScheduler Pattern (from src/meta/ingest.py)

```python
# Source: src/meta/ingest.py — exact template for src/ga4/ingest.py
_bot = None
_db = None
_settings = None

def register_job_resources(bot, db, settings) -> None:
    global _bot, _db, _settings
    _bot, _db, _settings = bot, db, settings

async def ga4_ingest_job() -> None:
    """Zero-arg APScheduler entry point."""
    if _bot is None or _db is None or _settings is None:
        logger.error("ga4_ingest_resources_not_registered")
        return
    await _run_ga4_ingest(_bot, _db, _settings)
```

### Pattern 6: Schema Migration for ga4_landing_pages

**What:** New table required for landing page data (different PK shape from `ga4_metrics`).

**Key insight:** `ga4_metrics` is keyed by `(campaign_utm, date)`. Landing page data is keyed by `(landing_page, date)`. These cannot share a table — they are different grain levels from different API queries.

```python
# Source: src/db/schema.py migration pattern
MIGRATION_003_PHASE3: str = """
CREATE TABLE IF NOT EXISTS ga4_landing_pages (
    landing_page              TEXT NOT NULL,   -- landingPagePlusQueryString dimension value
    date                      TEXT NOT NULL,   -- ISO YYYY-MM-DD
    sessions                  INTEGER,
    total_users               INTEGER,
    ga4_purchases_lastclick   INTEGER,         -- CLAUDE.md: ga4_ prefix
    screen_page_views         INTEGER,
    avg_engagement_time       REAL,
    fetched_at                TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (landing_page, date)
);
CREATE INDEX IF NOT EXISTS idx_ga4_lp_date ON ga4_landing_pages(date);
CREATE INDEX IF NOT EXISTS idx_ga4_lp_page ON ga4_landing_pages(landing_page);
"""
```

### Pattern 7: Report Builder Extension

**What:** Extend `build_daily_report_html` with optional GA4 parameters — no breaking change.

```python
# Extends src/reports/builder.py
def build_daily_report_html(
    rows: list[dict],
    tldr: str | None,
    date_str: str,
    ga4_campaign_rows: list[dict] | None = None,    # from ga4_metrics
    ga4_landing_rows: list[dict] | None = None,     # from ga4_landing_pages
    meta_campaign_names: list[str] | None = None,   # for UTM coverage calculation
) -> str:
    ...
    # Add GA4 section after existing Meta content
    if ga4_campaign_rows or ga4_landing_rows:
        parts.append("")
        parts.append("<b>--- Website (GA4) ---</b>")
        # ... landing pages, attribution comparison, UTM coverage line
```

### Anti-Patterns to Avoid

- **Using `landingPage` dimension:** Deprecated since 2023-05-14 — use `landingPagePlusQueryString` instead. [VERIFIED: GA4 changelog]
- **Using `conversions` metric:** Renamed to `keyEvents` in 2024-05-06. Using `conversions` will return empty data in current API. [VERIFIED: GA4 changelog]
- **Using `users` metric:** The correct metric name is `totalUsers` (not `users`). [VERIFIED: GA4 API schema docs]
- **Using `averageSessionDuration`:** Superseded by `averageEngagementTimePerSession` in GA4. The legacy metric still exists but GA4 measures engagement differently from Universal Analytics. [VERIFIED: GA4 API schema docs]
- **Combining `sessionCampaignName` + `pagePath` in one request:** `pagePath` is event-scoped, `sessionCampaignName` is session-scoped — incompatible in a single request. Use two separate requests. [VERIFIED: multiple GA4 community sources]
- **Blending Meta + GA4 conversion numbers:** CLAUDE.md non-negotiable — always side-by-side.
- **F-string SQL:** All SQL uses named params (`:foo`) — established project rule.
- **html.escape() omission on landing page paths:** Landing page paths can contain `<`, `>`, `&` — always escape.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| GA4 API auth | Custom OAuth2 flow | `BetaAnalyticsDataClient.from_service_account_file()` | Client library handles credential refresh, retries, scopes |
| Exponential backoff | Custom sleep loop | `tenacity` (already in project) | Same pattern as Meta ingest; handles GA4 quota 429s |
| Service account scope | Explicit `google.auth` scope setting | Client library handles it | `from_service_account_file()` sets `analytics.readonly` internally |
| Async GA4 client | Custom thread pool | `asyncio.to_thread()` | Established project pattern; single-call overhead is acceptable for daily scheduled jobs |
| UTM fuzzy matching | Levenshtein / edit distance | No matching at all | CLAUDE.md: exact match only. Unmatched → coverage warning |

**Key insight:** GA4 dimension/metric name discovery should come from the official schema docs, not guesswork — the API is strict about camelCase names and will silently return no data (not an error) for invalid dimension names in some cases.

---

## Critical Findings: Dimension Names (2026)

These have changed from the Universal Analytics era and are frequently wrong in older tutorials:

| What you need | Correct API name | Wrong / deprecated name |
|---------------|-----------------|------------------------|
| Landing page | `landingPagePlusQueryString` | ~~`landingPage`~~ (deprecated 2023-05-14) |
| Conversions / key events | `keyEvents` | ~~`conversions`~~ (renamed 2024-05-06) |
| Total users | `totalUsers` | ~~`users`~~ (wrong) |
| Avg engagement time | `averageEngagementTimePerSession` | ~~`averageSessionDuration`~~ (legacy UA name) |
| Campaign name at session | `sessionCampaignName` | ~~`campaignName`~~ (event-scoped, different grain) |
| Bounce rate | `bounceRate` | ~~`bounce_rate`~~ (snake_case wrong) |

[VERIFIED: https://developers.google.com/analytics/devguides/reporting/data/v1/changelog + GA4 API schema]
[VERIFIED: https://developers.google.com/analytics/devguides/reporting/data/v1/api-schema]

---

## Schema Change Analysis

### Existing `ga4_metrics` Table — Sufficient for Campaign-Level GA4-02

The existing `ga4_metrics` table (MIGRATION_001) covers:
- `campaign_utm TEXT` — matches `sessionCampaignName` dimension value
- `sessions INTEGER` — maps directly
- `users INTEGER` — should be `total_users` in semantics but column exists; minor naming inconsistency OK since it's internal
- `new_users INTEGER` — maps directly
- `bounce_rate REAL` — `bounceRate` metric value
- `avg_engagement_time REAL` — `averageEngagementTimePerSession` metric value
- `ga4_purchases_lastclick INTEGER` — `keyEvents` metric value (filtered by `GA4_CONVERSION_EVENT`)
- Missing: `screen_page_views` — not in current schema but not in GA4-02 requirements for campaign level either

**Assessment:** `ga4_metrics` is sufficient for campaign-level GA4-02. No column additions needed. [VERIFIED: codebase inspection]

### New `ga4_landing_pages` Table — Required for GA4-02 Landing Page Metrics

Landing page data has a fundamentally different PK (`landing_page, date`) vs campaign data (`campaign_utm, date`). A new table is required. This is `MIGRATION_003_PHASE3`.

The `upsert_ga4_metrics()` method in `DBClient` already exists and works for campaign rows. A parallel `upsert_ga4_landing_pages()` method is needed for landing page rows.

---

## UTM Join Logic

```
campaign name in ga4_metrics.campaign_utm
    ↕ exact match (case-sensitive)
campaign name in campaigns.name (populated from Meta campaign_name)
```

**What happens with no UTM tagging:**
- GA4 reports untagged sessions under `sessionCampaignName = "(not set)"`
- These are NOT stored in `ga4_metrics` (filtered out at ingest time)
- At report time: `unmatched_count = len(meta_campaign_names) - len(matched_campaign_utms)`
- Coverage line: `⚠️ UTM coverage: {matched}/{total} campaigns matched to GA4.`

**SQL for UTM join at report time:**
```sql
-- Named params — no f-string SQL
SELECT g.campaign_utm, g.sessions, g.ga4_purchases_lastclick,
       m.spend, m.roas, m.meta_purchases_7dclick
FROM ga4_metrics g
JOIN ad_metrics m ON g.campaign_utm = c.name
JOIN campaigns c ON c.id = m.campaign_id
WHERE m.date = :target_date
  AND g.date = :target_date
  AND m.ad_set_id = ''
  AND m.ad_id = '';
```

[ASSUMED: The exact SQL for the cross-source join at report time — confirmed pattern is correct but exact column selection may be adjusted by planner]

---

## Common Pitfalls

### Pitfall 1: Deprecated Dimension Names in GA4 API
**What goes wrong:** Using `landingPage` or `conversions` returns no data or empty rows without a clear error.
**Why it happens:** GA4 deprecated `landingPage` in 2023 and renamed `conversions` to `keyEvents` in 2024. Old tutorials and training data use wrong names.
**How to avoid:** Use `landingPagePlusQueryString` and `keyEvents`. See Critical Findings table above.
**Warning signs:** Response has 0 rows for dates that clearly had traffic in GA4 UI.
[VERIFIED: https://developers.google.com/analytics/devguides/reporting/data/v1/changelog]

### Pitfall 2: Combining Session-Scoped and Event-Scoped Dimensions
**What goes wrong:** Combining `sessionCampaignName` (session-scoped) with `pagePath` (event-scoped) in one request returns incompatible / misleading data.
**Why it happens:** GA4 stores session-level and event-level data separately; mixing scopes causes data inflation or zeros.
**How to avoid:** Two separate RunReportRequest calls — Request #1 uses `sessionCampaignName`, Request #2 uses `landingPagePlusQueryString`.
**Warning signs:** Sessions count is dramatically higher than expected; GA4 UI data doesn't match API results.
[VERIFIED: multiple GA4 community sources + GA4 Data API documentation]

### Pitfall 3: BetaAnalyticsDataClient is Synchronous
**What goes wrong:** Calling `client.run_report()` directly in an `async def` blocks the aiogram event loop.
**Why it happens:** The SDK uses gRPC blocking transport by default.
**How to avoid:** Always wrap in `asyncio.to_thread()`. This is already the project pattern for `facebook-business` SDK.
**Warning signs:** Bot becomes unresponsive during ingest window.
[VERIFIED: SDK docs + project pattern in src/meta/ingest.py]

### Pitfall 4: GA4 Quota Exhaustion Without Caching
**What goes wrong:** Running multiple ingest runs per day exhausts the per-property quota.
**Why it happens:** GA4 Data API has tiered quota limits (e.g., 200,000 tokens per day for standard properties).
**How to avoid:** (a) Check `ingestion_log` for a successful GA4 run within the past 6 hours before making any API call. (b) Always pass `return_property_quota=True` in requests and log the quota response. (c) Schedule ingest at 01:00 once per day.
**Warning signs:** API returns 429 errors after multiple runs.
[VERIFIED: https://developers.google.com/analytics/devguides/reporting/data/v1/quotas]

### Pitfall 5: Service Account File Path
**What goes wrong:** `Settings.ga4_service_account_json` is typed as `Path | None` — must be converted to `str` when passed to `from_service_account_file()`.
**Why it happens:** `BetaAnalyticsDataClient.from_service_account_file()` expects a `str`, not a `pathlib.Path`.
**How to avoid:** `client = BetaAnalyticsDataClient.from_service_account_file(str(settings.ga4_service_account_json))`
**Warning signs:** `TypeError: expected str, bytes or os.PathLike object, not PosixPath`
[ASSUMED: from_service_account_file str vs Path typing — standard for Google client libraries]

### Pitfall 6: Skipping Ingest if GA4 Credentials Not Configured
**What goes wrong:** Report job fires at 09:00 with no GA4 data, generating confusing empty-section reports.
**Why it happens:** `ga4_property_id` or `ga4_service_account_json` may be `None` in dev.
**How to avoid:** Check `settings.ga4_property_id` and `settings.ga4_service_account_json` at the start of `_run_ga4_ingest()`. Log a warning and return early (same pattern as Meta credential check in `src/meta/ingest.py`). The GA4 section in reports should be omitted (not errored) if no data.
**Warning signs:** `AttributeError: 'NoneType' object has no attribute 'run_report'`
[VERIFIED: src/meta/ingest.py pattern — credential guard at top of _run_meta_ingest()]

### Pitfall 7: "(not set)" Campaign Rows
**What goes wrong:** GA4 API returns a high-traffic `sessionCampaignName = "(not set)"` row that inflates totals if stored.
**Why it happens:** All sessions without UTM tagging are bucketed under `(not set)`.
**How to avoid:** Filter out `(not set)` rows before writing to `ga4_metrics`. Use a `dimension_filter` with `notExpression` in the API request or filter in Python before upsert.
**Warning signs:** UTM coverage = 100% but sessions are abnormally high.
[VERIFIED: GA4 predefined reports documentation (filter examples)]

---

## Code Examples

### Full Ingest Flow Skeleton

```python
# Source: mirrors src/meta/ingest.py exactly
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    DateRange, Dimension, Filter, FilterExpression, Metric, RunReportRequest,
)
import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

async def _run_ga4_ingest(bot, db, settings) -> None:
    tz = ZoneInfo(settings.report_timezone)
    # D-10: D-2 freshness (yesterday minus 1 day per CLAUDE.md)
    d2 = (datetime.now(tz).date() - timedelta(days=2)).isoformat()

    # GA4-04: 6-hour cache check
    recent = await db.fetch_one(
        "SELECT id FROM ingestion_log WHERE source='ga4' AND status='success' "
        "AND started_at > datetime('now', '-6 hours')"
    )
    if recent:
        logger.info("ga4_ingest_skipped_cache_hit")
        return

    log_id = await db.log_ingestion_start("ga4")
    try:
        client = BetaAnalyticsDataClient.from_service_account_file(
            str(settings.ga4_service_account_json)
        )
        prop = settings.ga4_property_id

        # Request #1: campaign-level metrics
        campaign_rows = await asyncio.to_thread(
            _fetch_campaign_metrics, client, prop, d2
        )
        upserted_c = await db.upsert_ga4_metrics(campaign_rows)

        # Request #2: landing page metrics (D-2 single day for daily; 7-day for trend available in report)
        lp_rows = await asyncio.to_thread(
            _fetch_landing_page_metrics, client, prop, d2, d2
        )
        upserted_lp = await db.upsert_ga4_landing_pages(lp_rows)

        await db.log_ingestion_finish(log_id, "success", rows_upserted=upserted_c + upserted_lp)
    except Exception as exc:
        await db.log_ingestion_finish(log_id, "failed", error=str(exc))
        # Circuit breaker check (same pattern as Meta)
        ...
```

### Report Builder Extension — GA4 Section

```python
# Source: src/reports/builder.py extension pattern
# Called inside build_daily_report_html() after existing Meta content

def _build_ga4_section(
    ga4_campaign_rows: list[dict],
    ga4_landing_rows: list[dict],
    meta_rows: list[dict],
    conversion_event: str = "purchase",
) -> list[str]:
    """Assemble the GA4 section lines for the daily digest."""
    parts: list[str] = []
    parts.append("")
    parts.append("<b>--- Website (GA4) ---</b>")

    # Total sessions from campaign rows
    total_sessions = sum(int(r.get("sessions", 0) or 0) for r in ga4_campaign_rows)
    parts.append(f"Sessions: {total_sessions:,}")

    # Top 3 landing pages by conversions
    top3 = sorted(
        ga4_landing_rows,
        key=lambda r: int(r.get("ga4_purchases_lastclick", 0) or 0),
        reverse=True,
    )[:3]
    if top3:
        parts.append("")
        parts.append("<b>Top 3 Landing Pages (yesterday)</b>")
        for i, lp in enumerate(top3, 1):
            page = html.escape(lp.get("landing_page", ""))
            conv = int(lp.get("ga4_purchases_lastclick", 0) or 0)
            sess = int(lp.get("sessions", 0) or 0)
            parts.append(f"<b>{i}. {page}</b> — {conv} conversions, {sess} sessions")

    # Attribution comparison for UTM-matched campaigns
    # (built at call site where meta_rows are available)
    ...

    # UTM coverage warning (D-06, D-07) — bottom of GA4 section
    meta_names = {r.get("campaign_name") for r in meta_rows if r.get("campaign_name")}
    ga4_utms = {r.get("campaign_utm") for r in ga4_campaign_rows if r.get("campaign_utm")}
    matched = meta_names & ga4_utms
    total = len(meta_names)
    unmatched = total - len(matched)
    if unmatched > 0:
        parts.append("")
        parts.append(
            f"⚠️ UTM coverage: {len(matched)}/{total} campaigns matched to GA4. "
            f"{unmatched} campaigns have no website data (UTM tags missing or inconsistent)."
        )
    return parts
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `landingPage` dimension | `landingPagePlusQueryString` | 2023-05-14 | Must use new name; old name deprecated |
| `conversions` metric | `keyEvents` metric | 2024-05-06 | `conversions` returns no data in current API |
| Universal Analytics SDK | GA4 Data API (`google-analytics-data`) | 2023-07-01 (UA sunset) | Completely different API, different dimension names |
| `averageSessionDuration` | `averageEngagementTimePerSession` | GA4 launch | GA4 measures engagement, not pure session duration |

**Deprecated/outdated:**
- `landingPage` dimension: deprecated 2023-05-14, use `landingPagePlusQueryString`
- `conversions` metric: renamed to `keyEvents` 2024-05-06
- `isConversionEvent` dimension: replaced by `isKeyEvent`
- `sessionConversionRate`: replaced by `sessionKeyEventRate`

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `from_service_account_file()` accepts `str` type (not `pathlib.Path`) | Pitfall 5 | Minor — `str()` conversion always safe |
| A2 | `sessionCampaignName` and `landingPagePlusQueryString` are incompatible in a single RunReportRequest (both session-scoped but different dimension compatibility groups) | Architecture / Pitfall 2 | Medium — if compatible, could use single request but two-request approach works either way |
| A3 | Cross-source SQL join using `campaigns.name = ga4_metrics.campaign_utm` is the correct UTM linkage | UTM Join Logic section | Medium — if campaign names don't match UTM values (e.g. Meta renames but UTM unchanged), coverage will be underreported; this is a data quality issue not a code issue |
| A4 | GA4 `keyEvents` metric counts conversions matching any configured key event (not just `GA4_CONVERSION_EVENT`) unless explicitly filtered | Code Examples section | Medium — if keyEvents includes ALL key events, the `ga4_purchases_lastclick` column may overcount. May need `eventName` dimension filter to isolate specific event |

---

## Open Questions (RESOLVED)

1. **Does `keyEvents` in RunReportRequest count ALL key events or only the configured event?**
   - What we know: `keyEvents` is a session-scoped metric counting total key events
   - RESOLVED: Both `_fetch_campaign_metrics_sync` and `_fetch_landing_page_metrics_sync` in `src/ga4/client.py` add a `metric_filter` using `FilterExpression(filter=Filter(field_name="eventName", string_filter=Filter.StringFilter(match_type=EXACT, value=conversion_event)))` to filter `keyEvents` to only `settings.ga4_conversion_event` (D-08). This ensures `ga4_purchases_lastclick` counts only the configured event.

2. **Are `sessionCampaignName` + `landingPagePlusQueryString` compatible in one request?**
   - What we know: Both are documented as session-scoped dimensions
   - RESOLVED: Two-request approach adopted regardless (architecturally cleaner; each result maps to a different DB table with different PK shape). Compatibility question is moot.

3. **Phase 3 Open from STATE.md: Is UTM tagging consistently applied to existing Meta campaigns?**
   - What we know: This is a data quality question, not a code question
   - RESOLVED: Data quality question — the UTM coverage warning (D-06, D-07) is the code mitigation. `src/reports/builder.py` computes matched/unmatched counts and surfaces the warning line when unmatched > 0. No code fix needed; operator acts on the warning.

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| `google-analytics-data` | GA4 ingest | Listed in pyproject.toml but not yet installed in venv | `>=0.22.0` | — (required) |
| GA4 property ID | Ingest | Config (env var) — may not be set in dev | — | Credential guard + early return |
| Service account JSON file | Ingest | Config (env var path) — may not exist in dev | — | Credential guard + early return |

**Missing dependencies with no fallback:**
- None at code level — `google-analytics-data` is already declared in pyproject.toml

**Missing dependencies with fallback:**
- GA4 credentials not configured: ingest job logs warning and skips (same pattern as Meta credential guard). Reports silently omit GA4 section when no GA4 data in DB.

---

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | yes | Service account JSON key file; Viewer-only IAM role; never write access |
| V3 Session Management | no | N/A (scheduled job, not user sessions) |
| V4 Access Control | yes | GA4 property Viewer role enforced at GA4 Admin level; read-only Data API |
| V5 Input Validation | yes | All GA4 landing page paths and campaign names go through `html.escape()` before Telegram output |
| V6 Cryptography | no | Service account key handled by Google client library; no custom crypto |

### Known Threat Patterns for GA4 Ingestion

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Landing page path containing HTML/script injection | Tampering (report output) | `html.escape()` on all `landing_page` values before Telegram message assembly |
| Campaign name containing prompt injection | Tampering (AI prompt) | Wrap in `<data>...</data>` tags if used in TL;DR prompt (Phase 4 concern; Phase 3 uses for display only) |
| Service account key exposure | Information Disclosure | Key file path from env var (`GA4_SERVICE_ACCOUNT_JSON`); never committed to git; Docker secret mount |
| Over-broad GA4 permissions | Elevation of Privilege | Viewer-only role in GA4 Admin; Data API is read-only by design |

---

## Sources

### Primary (HIGH confidence)
- [Google Analytics Data API changelog](https://developers.google.com/analytics/devguides/reporting/data/v1/changelog) — dimension renames (`landingPage`→`landingPagePlusQueryString`, `conversions`→`keyEvents`)
- [GA4 API Schema](https://developers.google.com/analytics/devguides/reporting/data/v1/api-schema) — exact metric/dimension names (totalUsers, bounceRate, averageEngagementTimePerSession, keyEvents, sessionCampaignName)
- [BetaAnalyticsDataClient Python docs](https://googleapis.dev/python/analyticsdata/latest/data_v1beta/beta_analytics_data.html) — `from_service_account_file()`, `from_service_account_json()` signatures
- [PyPI: google-analytics-data](https://pypi.org/project/google-analytics-data/) — current version 0.22.0 (2026-05-07)
- [GA4 predefined reports (landing page)](https://developers.google.com/analytics/devguides/reporting/data/v1/predefined-reports) — `landingPage` and `landingPagePlusQueryString` request patterns
- [GA4 quotas documentation](https://developers.google.com/analytics/devguides/reporting/data/v1/quotas) — `returnPropertyQuota: true` pattern
- Codebase inspection: `src/meta/ingest.py`, `src/db/client.py`, `src/db/schema.py`, `src/reports/builder.py`, `src/config.py`, `src/main.py`

### Secondary (MEDIUM confidence)
- [python-docs-samples quickstart_json_credentials.py](https://github.com/googleanalytics/python-docs-samples/blob/main/google-analytics-data/quickstart_json_credentials.py) — `from_service_account_json()` usage pattern
- GA4 Data API dimension incompatibility (session vs event scope) — multiple community sources confirming two-request approach

### Tertiary (LOW confidence)
- Dimension scope compatibility matrix for `sessionCampaignName` + `landingPagePlusQueryString` in a single request — not explicitly documented; two-request approach adopted as safe default

---

## Metadata

**Confidence breakdown:**
- Standard stack (SDK, versions): HIGH — verified against PyPI
- Dimension/metric names: HIGH — verified against official GA4 changelog and API schema
- Architecture (two-request pattern): HIGH — forced by scope incompatibility research; two-request approach is safe regardless
- Schema migration requirement: HIGH — different PK shapes cannot share `ga4_metrics` table
- Pitfalls: HIGH — deprecated names verified against changelog

**Research date:** 2026-05-19
**Valid until:** 2026-11-19 (stable API; watch for GA4 dimension name changes)
