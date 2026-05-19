# Phase 5: Hardening & Ops — Research

**Researched:** 2026-05-19
**Domain:** Observability (Sentry), operational CLI tooling (backfill), graceful degradation patterns, dead-man's-switch monitoring
**Confidence:** HIGH (all four topic areas verified against official docs or codebase inspection)

---

## Summary

Phase 5 is a hardening pass over an already-complete system. All 38 v1 requirements shipped in Phases 1-4. This phase adds three operational capabilities that the existing code partially supports but does not finish:

1. **Backfill command** — a `python -m src.backfill` CLI that re-runs Meta and/or GA4 ingestion over a user-specified date range. The UPSERTs in the existing `DBClient` are already idempotent, so backfill is primarily an orchestration layer over `_run_meta_ingest` / `_run_ga4_ingest` with alert suppression and a loop over dates.

2. **Per-source graceful degradation** — the daily/weekly report jobs currently wrap everything in a single `try/except` and silently drop errors. The fix is to query Meta and GA4 data independently in guarded blocks and inject "Data unavailable" notice strings into the report HTML when a source returns no rows for the expected date.

3. **Sentry + dead-man's-switch** — `sentry-sdk` 2.60.0 is the current release; the `AsyncioIntegration` captures all unhandled asyncio task exceptions automatically when `sentry_sdk.init()` is called inside the first `async` function. The existing `heartbeat_url` in `Settings` already targets healthchecks.io or any ping-style DMS — no code change needed for the DMS pattern, but the operator must configure a healthchecks.io check with an appropriate period/grace so missed heartbeats trigger an alert.

**Primary recommendation:** Implement all three success criteria in three focused plans. Each plan is self-contained and can be developed and tested in isolation.

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|---|---|---|---|
| Backfill CLI | CLI script (`src/backfill.py`) | `meta.ingest`, `ga4.ingest` | Re-uses existing ingest logic; CLI is the entry point only |
| Alert suppression during backfill | CLI script | `alerts.engine` | Pass a flag to `_run_meta_ingest` to skip `evaluate_alerts()` |
| Graceful degradation notices | Report builder (`reports/builder.py`) | `reports/daily.py`, `reports/weekly.py` | Builder formats the notice; report job guards the DB query |
| Source availability detection | `reports/daily.py`, `reports/weekly.py` | `db/client.py` | Report jobs query `ingestion_log` for last-success timestamp |
| Sentry error capture | `main.py` (init) | All modules (passive capture) | SDK init at app boot; `capture_exception()` at catch sites |
| Dead-man's-switch heartbeat | `reports/daily.py`, `reports/weekly.py` | External: healthchecks.io | Heartbeat ping already exists (REPORT-05); external service watches it |

---

## Standard Stack

### Core (Phase 5 additions)

| Library | Version | Purpose | Why Standard |
|---|---|---|---|
| `sentry-sdk` | `^2.60.0` | Error capture, async task monitoring | Official Python SDK; `AsyncioIntegration` handles aiogram's event loop automatically |
| `httpx` | already in project (daily.py uses it) | Heartbeat HTTP ping | Already imported for `ping_heartbeat` in `daily.py` |

### No New Supporting Libraries Needed

The backfill CLI uses only Python stdlib (`argparse`, `asyncio`, `datetime`) plus existing project modules. Graceful degradation uses existing `aiosqlite` and HTML builder. Sentry is the only new dependency.

**Version verification:**
```bash
# sentry-sdk current version confirmed via PyPI dry-run: 2.60.0 (2026-05-13)
pip install sentry-sdk --dry-run
# Result: Would install sentry-sdk-2.60.0
```
[VERIFIED: PyPI dry-run, 2026-05-19]

**Installation:**
```bash
# Add to pyproject.toml [project] dependencies:
"sentry-sdk>=2.60.0,<3",
```

---

## Architecture Patterns

### System Architecture Diagram

```
Backfill CLI
  python -m src.backfill
       |
       v
  argparse → date_range loop
       |
       ├─ _run_meta_ingest(bot=None, db, settings, suppress_alerts=True)
       |         └─ upsert_ad_metrics (idempotent UPSERT — no duplicates)
       └─ _run_ga4_ingest(bot=None, db, settings)
                 └─ upsert_ga4_metrics / upsert_ga4_landing_pages

Scheduled Report Jobs (graceful degradation)
  daily_report_job / weekly_report_job
       |
       ├─ [Meta guard block]
       |     query ad_metrics for target_date
       |     if rows empty OR ingestion_log shows failed → meta_available = False
       └─ [GA4 guard block]
             query ga4_metrics for target_date
             if rows empty OR ingestion_log shows failed → ga4_available = False
       |
       └─ build_daily_report_html(
               meta_rows, ga4_rows,
               meta_available=meta_available,
               ga4_available=ga4_available
           )
               └─ injects "⚠️ Meta data unavailable" / "⚠️ GA4 data unavailable"
                  notice into corresponding HTML section when flag is False

Sentry Init (main.py, inside async main())
  sentry_sdk.init(
      dsn=settings.sentry_dsn,
      integrations=[AsyncioIntegration()],
      environment=settings.sentry_environment,
  )
       |
       ├─ Unhandled asyncio task exceptions → auto-captured
       └─ catch sites: sentry_sdk.capture_exception(exc) at
             meta_ingest_job failure
             ga4_ingest_job failure
             daily/weekly report failure
             chat handler failure (optional — aiogram errors)

Dead-man's-switch (no code change required)
  ping_heartbeat(settings.heartbeat_url)  ← already in daily.py + weekly.py
       |
       └─ healthchecks.io check with period=24h, grace=2h
              monitors: "did ping arrive within 26 hours?"
              on miss → alerts operator via email/Telegram webhook/etc.
```

### Recommended Project Structure (additions only)

```
src/
├── backfill.py          # NEW: CLI entry point (python -m src.backfill)
└── ...existing modules unchanged
```

---

## Topic 1: Backfill Command Design

### CLI Interface

```python
# python -m src.backfill --source meta --start 2026-04-01 --end 2026-04-30
# python -m src.backfill --source ga4 --start 2026-05-01 --end 2026-05-15
# python -m src.backfill --source all --start 2026-05-01 --end 2026-05-10 --dry-run

import argparse
from datetime import date, timedelta

parser = argparse.ArgumentParser(description="Backfill Meta/GA4 historical data")
parser.add_argument("--source", choices=["meta", "ga4", "all"], required=True)
parser.add_argument("--start", required=True, help="YYYY-MM-DD inclusive")
parser.add_argument("--end", required=True, help="YYYY-MM-DD inclusive")
parser.add_argument("--dry-run", action="store_true", help="Log without writing")
```

[ASSUMED] — argparse interface design is based on project patterns and standard Python CLI conventions; no prior spec exists.

### Architecture

`src/backfill.py` is a `__main__`-compatible module with its own `asyncio.run(backfill_main())`. It:

1. Loads settings via `load_settings()` (same path as `main.py`)
2. Opens a `DBClient` connection and applies migrations
3. Loops over `[start_date, end_date]` inclusive, one day at a time
4. For each date, calls `_run_meta_ingest` / `_run_ga4_ingest` with a `suppress_alerts=True` flag

No APScheduler, no bot (heartbeat and circuit-breaker Telegram alerts are skipped — `bot=None` guard already exists in `_run_meta_ingest`).

### Idempotency

Already guaranteed by the existing `INSERT ... ON CONFLICT DO UPDATE` UPSERTs in `DBClient`. Running backfill twice over the same window is safe. [VERIFIED: src/db/client.py lines 83-103, 106-122]

### Alert Suppression

The backfill must NOT trigger `evaluate_alerts()` on historical dates. Two implementation options:

**Option A (preferred):** Add `suppress_alerts: bool = False` parameter to `_run_meta_ingest`. When `True`, skip the `evaluate_alerts(...)` call at the bottom of `_run_meta_ingest`. This requires a one-line change to `meta/ingest.py`.

**Option B:** Call `_run_meta_ingest` with `bot=None` — the circuit breaker already has a `if chat_id and bot:` guard. But `evaluate_alerts` is called before the circuit breaker guard and does not check `bot`. Option A is safer and more explicit.

### `ingestion_log` Behaviour During Backfill

The 6-hour cache check in `ga4_ingest.py` (Step 2) will block re-running the same GA4 date within 6 hours. For backfill this is undesirable. The backfill should bypass the cache check, OR call `db.fetch_one(...)` directly. Simplest: add a `skip_cache: bool = False` parameter to `_run_ga4_ingest`.

### Structured Logging

Backfill should log `backfill_date_start`, `backfill_date_end`, `backfill_date_current`, and `backfill_complete` events through structlog (same pattern as all other modules).

---

## Topic 2: Graceful Per-Source Degradation

### Current State (from code inspection)

`_run_daily_report` (daily.py) wraps the ENTIRE report in one `try/except Exception`. If the Meta DB query returns empty rows (because ingest failed) or the GA4 query throws, the whole report aborts silently. [VERIFIED: src/reports/daily.py lines 133-219]

The builder (`build_daily_report_html`) currently accepts `ga4_campaign_rows` and `ga4_landing_rows` positional args. If these are empty lists, the builder renders an empty GA4 section — but does not indicate that data is unavailable vs. genuinely zero activity. [VERIFIED: src/reports/builder.py]

### Detection Strategy

Two complementary signals indicate a source is unavailable:

**Signal 1 — Empty rows for expected date:** After querying `ad_metrics` / `ga4_metrics`, if the result is an empty list for the target date, the source likely failed to ingest.

**Signal 2 — ingestion_log query:** Query `ingestion_log` for the most recent row where `source = 'meta_ads'` (or `'ga4'`). If `status = 'failed'` or no 'success' row exists within the last 24 hours, the source is considered unavailable.

Signal 2 is more reliable (it distinguishes "no campaigns ran yesterday" from "ingest failed"), but Signal 1 is simpler to implement. Recommended: use both — if rows are empty AND the ingestion_log shows a failure, flag as unavailable.

### Implementation Pattern

In `_run_daily_report`, replace the monolithic try/except with source-guarded blocks:

```python
# Pattern — not final code
meta_rows = []
meta_available = True
try:
    meta_rows = await db.fetch_all(_YESTERDAY_METRICS_SQL, {"target_date": yesterday})
    if not meta_rows:
        # Check ingestion_log for recent failure
        last = await db.fetch_one(
            "SELECT status FROM ingestion_log WHERE source='meta_ads' "
            "ORDER BY started_at DESC LIMIT 1"
        )
        if last and last["status"] == "failed":
            meta_available = False
            logger.warning("daily_report_meta_unavailable", date=yesterday)
except Exception as exc:
    meta_available = False
    logger.error("daily_report_meta_query_failed", error=str(exc))
```

Then pass `meta_available` and `ga4_available` to `build_daily_report_html`.

### Report Notice Format (HTML)

```python
# In build_daily_report_html, add to Meta section when meta_available is False:
"⚠️ <b>Meta Ads data unavailable for {date}</b> (ingestion failed — check logs)"

# In GA4 section when ga4_available is False:
"⚠️ <b>GA4 data unavailable for {date}</b> (ingestion failed — check logs)"
```

[ASSUMED] — exact notice wording; adjust to match house style.

### Weekly Report

Same pattern applies to `_run_weekly_report`. The weekly report queries two windows (this week + last week). For each window, if the query returns empty rows, emit a notice. The weekly job does not have per-source result flags in the builder today, so the builder signature needs updating.

### Builder Signature Change

```python
# Before:
def build_daily_report_html(meta_rows, tldr, date, ga4_campaign_rows, ga4_landing_rows, ga4_landing_7day_rows):

# After:
def build_daily_report_html(
    meta_rows, tldr, date,
    ga4_campaign_rows, ga4_landing_rows, ga4_landing_7day_rows,
    meta_available: bool = True,  # NEW
    ga4_available: bool = True,   # NEW
):
```

Default `True` means existing test coverage and callers without the flags still work.

---

## Topic 3: Sentry Integration

### Current Version

`sentry-sdk 2.60.0` (released 2026-05-13). [VERIFIED: PyPI dry-run, 2026-05-19]

### Async-Correct Init Pattern

Official Sentry docs state: "it's recommended to call `sentry_sdk.init()` inside an `async` function to ensure async code is instrumented properly." [CITED: docs.sentry.io/platforms/python/]

```python
# In src/main.py, inside async main(), BEFORE db.connect() and bot creation:
import sentry_sdk
from sentry_sdk.integrations.asyncio import AsyncioIntegration

async def main() -> None:
    settings = load_settings()
    configure_logging(...)

    if settings.sentry_dsn:  # Only init if DSN is configured
        sentry_sdk.init(
            dsn=settings.sentry_dsn.get_secret_value(),
            integrations=[AsyncioIntegration()],
            environment=settings.sentry_environment,
            traces_sample_rate=0.0,   # No performance tracing (not needed for this app)
            send_default_pii=False,   # CLAUDE.md: never log PII
        )

    # ... rest of main() unchanged
```

[CITED: docs.sentry.io/platforms/python/integrations/asyncio/]

### What `AsyncioIntegration` Does

Instruments the running event loop to:
- Auto-capture all **unhandled exceptions in asyncio tasks**
- Create performance spans per task (disable with `task_spans=False` if not needed) [CITED: docs.sentry.io/platforms/python/integrations/asyncio/]

### Explicit Capture at Catch Sites

The existing `except Exception as exc` blocks in `meta_ingest_job`, `ga4_ingest_job`, `daily_report_job`, `weekly_report_job`, and `evaluate_alerts` should each call `sentry_sdk.capture_exception(exc)` before logging:

```python
except Exception as exc:  # noqa: BLE001
    sentry_sdk.capture_exception(exc)     # NEW
    logger.error("ingest_failed", source="meta_ads", error=str(exc))
    ...
```

This ensures errors that are caught and handled (not unhandled tasks) still reach Sentry.

### Context Enrichment

For exceptions from Telegram handlers (chat_router.py), add chat context:

```python
import sentry_sdk

with sentry_sdk.new_scope() as scope:
    scope.set_tag("chat_id", str(message.chat.id))
    scope.set_tag("source", "telegram_handler")
    sentry_sdk.capture_exception(exc)
```

IMPORTANT: Call `scope.set_tag()` on the yielded scope object, not on `sentry_sdk` directly inside the context manager. [CITED: docs.sentry.io/platforms/python/enriching-events/scopes/]

### SENTRY_DSN as SecretStr

The DSN is a secret URL (contains auth key). Use `SecretStr` in Settings:

```python
# In src/config.py:
sentry_dsn: SecretStr | None = None
sentry_environment: str = "production"
```

In Docker compose / Fly.io / Railway, set `SENTRY_DSN=https://...@sentry.io/...`.

### Sentry Does NOT Replace Structured Logging

`sentry_sdk.capture_exception()` sends to Sentry; `logger.error()` sends to structlog. Both should be called at every error site. Sentry is for alert routing and event grouping; structlog is for operational log analysis.

### Guards for Missing DSN

All `capture_exception()` calls must be guarded by `if settings.sentry_dsn:` OR check `sentry_sdk.Hub.current.client is not None`. The simplest pattern: only call `sentry_sdk.init()` when DSN is set, and `capture_exception()` is a no-op when SDK is not initialized (the SDK is safe to call even when not initialized — it does nothing). [ASSUMED — no-op behavior when uninitialized; consistent with known SDK behavior but not formally verified in this session.]

---

## Topic 4: Dead-Man's-Switch Enhancement

### What Already Exists

`ping_heartbeat(settings.heartbeat_url)` is called in `_run_daily_report` and `_run_weekly_report` AFTER Telegram delivery returns 200. [VERIFIED: src/reports/daily.py lines 212-213, src/reports/weekly.py lines 157-159]

`Settings.heartbeat_url: str | None = None` is already in `config.py`. [VERIFIED: src/config.py line 32]

### What the Success Criteria Requires

> "the dead-man's-switch alerts the operator when heartbeats stop"

This is an **external monitoring service** responsibility. The app can only ping — it cannot self-monitor for missed pings. The operator must:

1. Create a check on **healthchecks.io** (free tier allows 20 checks) or equivalent (BetterUptime, OhDear, etc.)
2. Set the check period to match the daily report schedule (e.g., 24 hours)
3. Set the grace period to allow for normal variance (e.g., 2 hours)
4. Configure healthchecks.io to send alerts (email, Telegram webhook, PagerDuty, etc.) when the check goes "Down"
5. Set `HEARTBEAT_URL=https://hc-ping.com/<uuid>` in the app environment

[CITED: healthchecks.io/docs/ — "raises an alert as soon as a ping does not arrive on time" / "grace period" concept]

### healthchecks.io Ping Protocol

| Signal | URL | When to call |
|---|---|---|
| Start | `https://hc-ping.com/<uuid>/start` | At job start (optional but useful) |
| Success | `https://hc-ping.com/<uuid>` | After Telegram delivery (existing code path) |
| Failure | `https://hc-ping.com/<uuid>/fail` | When report job catches a fatal exception |

The existing `ping_heartbeat` only sends the success ping. Adding a `/fail` ping on report job exception is a quality improvement but not required by the success criteria.

### Optional Enhancement: Fail Ping

In `_run_daily_report`'s outer `except` block:

```python
except Exception as exc:
    logger.error("daily_report_failed", date=yesterday, error=str(exc))
    # Optional: explicit fail ping so DMS transitions immediately to Down
    if settings.heartbeat_url:
        await ping_heartbeat(settings.heartbeat_url + "/fail")
```

[ASSUMED] — `ping_heartbeat` currently only accepts one URL; would need to accept an optional suffix or a separate `fail` URL parameter.

### No Code Change Required for Basic DMS

The basic dead-man's-switch requirement (operator alerted when heartbeats stop) is satisfied by:
1. The existing `ping_heartbeat` call (already implemented)
2. The operator configuring a healthchecks.io check (operator action, documented in RESEARCH)
3. Setting `HEARTBEAT_URL` in the environment (operator action)

The RESEARCH finding: **no new code is strictly required for success criterion 3's DMS component** — only operator configuration and documentation. Code changes (fail ping, start ping) are improvements but not blockers.

---

## Topic 5: Implementation Patterns from Existing Code

### Module Pattern to Reuse

`src/backfill.py` should be a top-level async script, NOT an APScheduler job. Use the same `DBClient` + `load_settings()` init pattern as `main.py`. No `register_job_resources()` needed.

```python
# src/backfill.py skeleton
async def backfill_main(source: str, start: date, end: date, dry_run: bool) -> None:
    settings = load_settings()
    configure_logging(level=settings.log_level, fmt="json")
    log = structlog.get_logger(__name__)

    db = DBClient(settings.db_path)
    await db.connect()
    try:
        current = start
        while current <= end:
            date_iso = current.isoformat()
            log.info("backfill_date", source=source, date=date_iso, dry_run=dry_run)
            if not dry_run:
                if source in ("meta", "all"):
                    await _run_meta_ingest_for_date(db, settings, date_iso)
                if source in ("ga4", "all"):
                    await _run_ga4_ingest_for_date(db, settings, date_iso)
            current += timedelta(days=1)
        log.info("backfill_complete", source=source, start=start.isoformat(), end=end.isoformat())
    finally:
        await db.close()

if __name__ == "__main__":
    args = _parse_args()
    asyncio.run(backfill_main(args.source, args.start, args.end, args.dry_run))
```

### Ingest Modules: Minimal Invasive Changes

Rather than copy-pasting `_run_meta_ingest` and `_run_ga4_ingest`, expose thin wrappers:

```python
# In meta/ingest.py (new public function):
async def run_meta_ingest_for_date(db, settings, date_iso: str) -> None:
    """Public entry point for backfill. Skips bot, heartbeat, and alerts."""
    await _run_meta_ingest(bot=None, db=db, settings=settings,
                           date_override=date_iso, suppress_alerts=True)
```

This requires adding `date_override: str | None = None` and `suppress_alerts: bool = False` parameters to `_run_meta_ingest`. When `date_override` is provided, use it instead of computing yesterday.

The GA4 module needs the same: `date_override` (bypasses D-2 calculation) and `skip_cache` (bypasses 6-hour cache check).

### Named-Param SQL (CLAUDE.md compliance)

The backfill module will call `db.fetch_all` directly in some places. All SQL must use `:param` named params — no f-string interpolation. [VERIFIED: CLAUDE.md + all existing modules]

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---|---|---|---|
| Error alerting / grouping | Custom error webhook | `sentry-sdk` with `AsyncioIntegration` | Deduplication, fingerprinting, replay, source maps — requires weeks of infra |
| Heartbeat monitoring | Custom "did the job run?" checker | healthchecks.io (external SaaS) | The app cannot monitor itself for missed pings; requires external observer |
| Backfill date iteration | Custom calendar logic | `datetime.date + timedelta(days=1)` in a while loop | stdlib is sufficient; no library needed |
| Idempotent re-runs | Custom duplicate detection | Existing `INSERT ... ON CONFLICT DO UPDATE` | Already implemented; do not add Python-side read-modify-write on top of it |

---

## Common Pitfalls

### Pitfall 1: `sentry_sdk.init()` Called Outside Async Context

**What goes wrong:** If `sentry_sdk.init()` is called at module import time (before the event loop exists), `AsyncioIntegration` may not correctly instrument the running loop. [CITED: docs.sentry.io/platforms/python/ — "recommended to call init inside an async function"]

**How to avoid:** Call `sentry_sdk.init()` inside `async def main()`, after `configure_logging()` and before `db.connect()`.

**Warning signs:** Sentry receives no events despite exceptions being raised.

### Pitfall 2: `capture_exception()` Inside Already-Caught Except Block

**What goes wrong:** `AsyncioIntegration` only captures **unhandled** task exceptions. Every existing `except Exception as exc` block in the ingest and report modules catches and suppresses errors before they propagate as unhandled task failures. Without explicit `sentry_sdk.capture_exception(exc)` calls at those sites, Sentry never sees them.

**How to avoid:** Add `sentry_sdk.capture_exception(exc)` as the first line of every `except` block that catches and suppresses (i.e., logs but does not re-raise).

**Warning signs:** No events in Sentry even though structlog logs show errors.

### Pitfall 3: Backfill Triggers Alert Engine on Historical Dates

**What goes wrong:** `_run_meta_ingest` calls `evaluate_alerts(db, bot, settings, date_iso)` as its final step. If backfill calls `_run_meta_ingest` without suppressing alerts, users receive historic alert spam (spend spike alerts for dates 3 months ago).

**How to avoid:** Add `suppress_alerts: bool = False` to `_run_meta_ingest`; backfill passes `suppress_alerts=True`.

**Warning signs:** Telegram receives many simultaneous old-date alerts during backfill run.

### Pitfall 4: GA4 6-Hour Cache Blocks Backfill of Same-Day Dates

**What goes wrong:** `_run_ga4_ingest` checks `ingestion_log` for a success within the last 6 hours before proceeding. When backfilling multiple dates in sequence, if two dates fall within a 6-hour window (wall-clock time of the backfill run), the second date gets skipped.

**How to avoid:** Add `skip_cache: bool = False` to `_run_ga4_ingest`; backfill passes `skip_cache=True`. [VERIFIED: src/ga4/ingest.py lines 79-86]

**Warning signs:** Backfill log shows `ga4_ingest_skipped_cache_hit` for multiple dates.

### Pitfall 5: "Data Unavailable" vs "No Campaigns Active" Ambiguity

**What goes wrong:** A Meta `ad_metrics` query returning 0 rows could mean "ingest failed" or "no campaigns ran yesterday." Surfacing "data unavailable" for the latter is misleading.

**How to avoid:** Only flag `meta_available = False` when BOTH: (a) rows are empty AND (b) `ingestion_log` shows `status = 'failed'` or no recent success. If rows are empty but `ingestion_log` shows success, render "No campaign data for {date}" instead of "data unavailable."

**Warning signs:** Report shows "Meta data unavailable" even on legitimate zero-spend days.

### Pitfall 6: `sentry_sdk.capture_exception` Raising When SDK Not Initialized

**What goes wrong:** If `SENTRY_DSN` is not set, `sentry_sdk.init()` is not called, but code still calls `sentry_sdk.capture_exception()`. In sentry-sdk 2.x, calling SDK functions before init is a no-op (does not raise). However, relying on undocumented no-op behavior is fragile.

**How to avoid:** Guard the `sentry_sdk.init()` call with `if settings.sentry_dsn:`. Alternatively, initialize sentry to a non-reporting state: `sentry_sdk.init(dsn="")` returns a disabled client. [ASSUMED — no-op when DSN is empty string; verify in integration tests.]

### Pitfall 7: Bot=None in Backfill Triggers Circuit Breaker Telegram Send

**What goes wrong:** `_run_meta_ingest` catches exceptions and then checks `if chat_id and bot:` before sending Telegram. If `bot=None` is passed, this guard already prevents the send. But `_check_circuit_breaker` still queries the DB. With a fresh `ingestion_log`, multiple backfill failures in a row could set the circuit breaker state, blocking the normal scheduled job later.

**How to avoid:** Backfill should write to `ingestion_log` with a `source` suffix (e.g., `'meta_ads_backfill'`) to keep backfill failure counts separate from scheduled job failure counts. Or: pass a flag to disable circuit breaker DB writes during backfill. [ASSUMED — one of these two approaches; planner should pick the simpler one.]

---

## Code Examples

### Sentry Init (async-correct)
```python
# Source: docs.sentry.io/platforms/python/ + docs.sentry.io/platforms/python/integrations/asyncio/
import sentry_sdk
from sentry_sdk.integrations.asyncio import AsyncioIntegration

async def main() -> None:
    settings = load_settings()
    configure_logging(...)

    if settings.sentry_dsn:
        sentry_sdk.init(
            dsn=settings.sentry_dsn.get_secret_value(),
            integrations=[AsyncioIntegration()],
            environment=settings.sentry_environment,
            traces_sample_rate=0.0,
            send_default_pii=False,
        )
```

### Explicit capture_exception at catch sites
```python
# Source: docs.sentry.io/platforms/python/usage/
import sentry_sdk

except Exception as exc:  # noqa: BLE001
    sentry_sdk.capture_exception(exc)
    logger.error("ingest_failed", source="meta_ads", error=str(exc))
```

### Backfill date loop
```python
# Source: Python stdlib — datetime.date + timedelta
from datetime import date, timedelta

current = start_date
while current <= end_date:
    await run_meta_ingest_for_date(db, settings, current.isoformat())
    current += timedelta(days=1)
```

### healthchecks.io signal URLs
```python
# Source: healthchecks.io/docs/
# Success (existing ping_heartbeat already sends this)
GET https://hc-ping.com/<uuid>

# Optional start signal (call at job start)
GET https://hc-ping.com/<uuid>/start

# Optional fail signal (call in except block)
GET https://hc-ping.com/<uuid>/fail
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|---|---|---|---|
| `sentry_sdk.Hub` (deprecated) | `sentry_sdk.new_scope()` context manager | sentry-sdk 2.0 (2023) | `Hub.current` and `push_scope` are removed in SDK 2.x |
| `with sentry_sdk.push_scope() as scope` | `with sentry_sdk.new_scope() as scope` | sentry-sdk 2.0 | Old pattern causes AttributeError in 2.x |

**Deprecated/outdated:**
- `sentry_sdk.Hub`: Removed in sentry-sdk 2.x. Do not use. Use `sentry_sdk.new_scope()` for scoped context enrichment.
- `sentry_sdk.push_scope()`: Alias removed in 2.x. Use `new_scope()`.

---

## Recommended Plan Structure

Phase 5 maps cleanly to **3 plans** (no cross-plan dependencies; can be parallelized):

### Plan 05-01: Sentry Integration + Settings extension

**Wave 1:**
- Add `sentry_dsn: SecretStr | None` and `sentry_environment: str` to `Settings` in `config.py`
- Add `sentry-sdk>=2.60.0,<3` to `pyproject.toml`
- Add `sentry_sdk.init()` call in `main.py` inside `async main()`
- Add `sentry_sdk.capture_exception(exc)` at every existing `except Exception` catch-and-suppress site:
  - `meta/ingest.py` (_run_meta_ingest outer except)
  - `ga4/ingest.py` (_run_ga4_ingest outer except)
  - `reports/daily.py` (_run_daily_report outer except)
  - `reports/weekly.py` (_run_weekly_report outer except)
  - `alerts/engine.py` (evaluate_alerts outer except)

**Wave 2:**
- Tests: `test_sentry.py` — mock `sentry_sdk.capture_exception`, verify it is called when ingest raises; verify it is NOT called when `SENTRY_DSN` is not set.

### Plan 05-02: Graceful Per-Source Degradation

**Wave 1:**
- Add `meta_available: bool = True` and `ga4_available: bool = True` parameters to `build_daily_report_html` and `build_weekly_report_html` in `reports/builder.py`
- Inject "data unavailable" HTML notice strings into Meta and GA4 sections when the respective flag is False

**Wave 2:**
- Refactor `_run_daily_report` in `daily.py`: split Meta and GA4 queries into separate guarded blocks; set `meta_available` / `ga4_available` based on empty rows + `ingestion_log` status check
- Refactor `_run_weekly_report` in `weekly.py`: same pattern

**Wave 3:**
- Tests: `test_graceful_degradation.py` — verify report HTML contains "data unavailable" notice when DB returns empty rows with failed ingestion_log; verify report still completes when only one source fails.

### Plan 05-03: Backfill CLI

**Wave 1:**
- Add `date_override: str | None = None` and `suppress_alerts: bool = False` parameters to `_run_meta_ingest` in `meta/ingest.py`
- Add `date_override: str | None = None` and `skip_cache: bool = False` parameters to `_run_ga4_ingest` in `ga4/ingest.py`
- Add public wrapper functions `run_meta_ingest_for_date(db, settings, date_iso)` and `run_ga4_ingest_for_date(db, settings, date_iso)` to each ingest module

**Wave 2:**
- Create `src/backfill.py` with `argparse` CLI, date loop, and calls to the public wrappers
- Add `__main__.py` guard for `python -m src.backfill`

**Wave 3:**
- Tests: `test_backfill.py` — verify date range loop calls ingest for each date, verify `suppress_alerts=True` prevents `evaluate_alerts` call, verify `skip_cache=True` bypasses 6-hour check, verify idempotency (calling twice produces same row count).

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|---|---|---|
| A1 | `sentry_sdk.capture_exception()` is a no-op when SDK is not initialized | Pitfall 6 | Code would raise AttributeError at runtime in uninitialized state; fix: add `if settings.sentry_dsn:` guard before each call |
| A2 | Adding `sentry_dsn: SecretStr | None = None` to Settings is sufficient; `SENTRY_SDK` auto-reads `SENTRY_DSN` env var even without explicit `dsn=` in init | Topic 3 (Sentry Init) | If SDK ignores explicit `dsn=` set to `None`, init silently does nothing; fix: always pass `dsn=settings.sentry_dsn.get_secret_value()` explicitly |
| A3 | Backfill should use `source='meta_ads_backfill'` tag in ingestion_log to avoid polluting circuit breaker counters | Pitfall 7 | Circuit breaker could trip during large backfill runs; planner should pick a source tagging strategy |
| A4 | "Data unavailable" notice wording in report HTML | Topic 2 (report notice format) | User may want different phrasing; low risk — trivially changed |

---

## Open Questions

1. **Backfill — alert_log deduplication on historical dates**
   - What we know: `alert_log` has a UNIQUE constraint on `(alert_type, campaign_id, date)`. If an alert was already fired for a historical date (e.g., 2026-04-15), running backfill for that date with alerts enabled would hit the dedup guard and silently skip.
   - What's unclear: Does the operator want backfill to ever re-evaluate alerts? (Probably no — backfill is for data recovery, not alert replay.)
   - Recommendation: Confirm that `suppress_alerts=True` is always the correct default for backfill; document in operator guide.

2. **DMS heartbeat configuration — operator action needed**
   - What we know: Code already pings `heartbeat_url` after each report. healthchecks.io is the recommended external service.
   - What's unclear: Has the operator set up a healthchecks.io check yet? The Phase 5 plan should include a documentation task for the operator setup steps.
   - Recommendation: Add an operator guide (or README section) documenting how to create the check, set period to 24h, grace to 2h, and configure alert channel.

3. **sentry_sdk no-op when uninitialized**
   - What we know: sentry-sdk 2.x is generally safe to call when uninitialized.
   - What's unclear: Not formally verified in this session.
   - Recommendation: The integration test for Plan 05-01 should explicitly verify calling `capture_exception()` without `init()` does not raise. [ASSUMED]

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|---|---|---|---|---|
| `sentry-sdk` | Plan 05-01 (Sentry) | ✗ (not installed) | 2.60.0 available on PyPI | None — add to pyproject.toml |
| `httpx` | Plan 05-02 (fail ping) | Yes (already in daily.py) | Existing install | — |
| `healthchecks.io` account | Success criterion 3 | Unknown (operator SaaS) | — | BetterUptime, OhDear, or any ping-webhook receiver |
| Python 3.12 | All | Yes | Confirmed in pyproject.toml | — |

**Missing dependencies with no fallback:**
- `sentry-sdk` must be added to `pyproject.toml`; it is not currently a declared dependency.

**Missing dependencies with fallback:**
- `healthchecks.io` account — operator action required; app code already supports any ping URL. Operator can use any similar service.

---

## Validation Architecture

### Test Framework

| Property | Value |
|---|---|
| Framework | pytest 8 + pytest-asyncio |
| Config file | `pyproject.toml` `[tool.pytest.ini_options]` (asyncio_mode = "auto") |
| Quick run command | `pytest tests/ -x -q` |
| Full suite command | `pytest tests/ -v` |

### Phase Requirements → Test Map

Phase 5 has no formal REQ-IDs (all v1 requirements shipped in Phases 1-4). Tests map to success criteria instead:

| Success Criterion | Behavior | Test Type | Automated Command | File Exists? |
|---|---|---|---|---|
| SC-1 (backfill) | CLI produces correct date range iteration | unit | `pytest tests/test_backfill.py -x` | No — Wave 1 gap |
| SC-1 (backfill) | Backfill is idempotent (no duplicate rows) | unit | `pytest tests/test_backfill.py::test_idempotent -x` | No — Wave 1 gap |
| SC-1 (backfill) | Alerts suppressed during backfill | unit | `pytest tests/test_backfill.py::test_alert_suppression -x` | No — Wave 1 gap |
| SC-2 (degradation) | Report includes "unavailable" notice when Meta ingest failed | unit | `pytest tests/test_graceful_degradation.py -x` | No — Wave 2 gap |
| SC-2 (degradation) | GA4 failure does not block Meta report section | unit | `pytest tests/test_graceful_degradation.py::test_meta_survives_ga4_failure -x` | No — Wave 2 gap |
| SC-3 (Sentry) | capture_exception called on ingest failure | unit | `pytest tests/test_sentry.py -x` | No — Wave 1 gap |

### Wave 0 Gaps

- [ ] `tests/test_backfill.py` — covers SC-1 (date loop, idempotency, alert suppression)
- [ ] `tests/test_graceful_degradation.py` — covers SC-2 (per-source independence, report notices)
- [ ] `tests/test_sentry.py` — covers SC-3 (capture_exception called, no-op without DSN)

---

## Security Domain

| ASVS Category | Applies | Standard Control |
|---|---|---|
| V2 Authentication | No | — |
| V3 Session Management | No | — |
| V4 Access Control | No | — |
| V5 Input Validation | Yes (backfill date args) | Validate `start <= end`, valid ISO dates, `source` in allowed enum via argparse `choices=` |
| V6 Cryptography | No | — |

### Known Threat Patterns

| Pattern | STRIDE | Standard Mitigation |
|---|---|---|
| Backfill date injection via CLI arg | Tampering | `argparse` with `type=date.fromisoformat` validates format; no f-string SQL — named params only |
| SENTRY_DSN in logs | Information Disclosure | Use `SecretStr` in Settings; `structlog` redaction pipeline already masks `SecretStr` fields (INFRA-05) |
| Sentry sending PII | Information Disclosure | `send_default_pii=False` in `sentry_sdk.init()` |

---

## Sources

### Primary (HIGH confidence)

- `src/meta/ingest.py`, `src/ga4/ingest.py`, `src/reports/daily.py`, `src/reports/weekly.py`, `src/db/client.py`, `src/config.py` — direct codebase inspection
- [docs.sentry.io/platforms/python/integrations/asyncio/](https://docs.sentry.io/platforms/python/integrations/asyncio/) — AsyncioIntegration init pattern, task span behavior
- [docs.sentry.io/platforms/python/](https://docs.sentry.io/platforms/python/) — async init recommendation, basic SDK usage
- [docs.sentry.io/platforms/python/enriching-events/scopes/](https://docs.sentry.io/platforms/python/enriching-events/scopes/) — new_scope context manager, set_tag on yielded scope
- [healthchecks.io/docs/](https://healthchecks.io/docs/) — grace period, check states, alert triggering
- PyPI dry-run — sentry-sdk 2.60.0 confirmed as current version

### Secondary (MEDIUM confidence)

- [pypi.org/project/sentry-sdk/](https://pypi.org/project/sentry-sdk/) — version 2.60.0 confirmed, async framework list
- [healthchecks.io/docs/python/](https://healthchecks.io/docs/python/) — ping URL format, /start /fail suffixes

### Tertiary (LOW confidence / ASSUMED)

- `sentry_sdk.capture_exception()` no-op behavior when uninitialized — consistent with known SDK behavior but not formally verified in this session (A1)
- Backfill `ingestion_log` source-tagging strategy to avoid circuit breaker pollution (A3)

---

## Metadata

**Confidence breakdown:**
- Sentry integration: HIGH — official docs consulted, current version confirmed
- Graceful degradation: HIGH — based on direct codebase inspection
- Backfill design: MEDIUM/HIGH — architecture is clear; minor details (source tagging, GA4 skip_cache flag) are ASSUMED
- Dead-man's-switch: HIGH — existing heartbeat confirmed; healthchecks.io docs confirmed external service pattern

**Research date:** 2026-05-19
**Valid until:** 2026-06-19 (sentry-sdk versions change rapidly; re-verify if >30 days pass before execution)
