---
phase: 02-meta-ads-ingestion-scheduled-reports-alerts
plan: "05"
subsystem: ingestion
tags: [meta-ads, apscheduler, sqlite, circuit-breaker, aiogram, structlog]

# Dependency graph
requires:
  - phase: 02-02
    provides: init_meta_api, fetch_campaign_insights, fetch_adset_insights
  - phase: 02-01
    provides: DBClient with log_ingestion_start/finish, upsert_campaign, upsert_ad_metrics, fetch_all
  - phase: 02-04
    provides: evaluate_alerts(db, bot, settings, target_date)

provides:
  - meta_ingest_job(): zero-arg async APScheduler entry point
  - register_job_resources(bot, db, settings): module-globals registration
  - Circuit breaker: Telegram alert after 3 consecutive failures

affects:
  - 02-06 (scheduler registration must call register_job_resources before scheduler.start())
  - 02-07 (daily report job follows same module-globals pattern)
  - 02-08 (main.py wiring)

# Tech tracking
tech-stack:
  added: []
  patterns:
    - Module-globals pattern for APScheduler job resources (avoids PicklingError with SQLAlchemyJobStore)
    - ZoneInfo-aware yesterday computation for timezone-correct date targeting
    - html.escape() + ParseMode.HTML for all Telegram messages (security non-negotiable)

key-files:
  created:
    - src/meta/ingest.py
  modified: []

key-decisions:
  - "Module-globals pattern for APScheduler: register_job_resources() called from main.py before scheduler.start() — avoids PicklingError"
  - "Circuit breaker uses _RECENT_FAILURES_SQL with named params :source/:limit — no f-string SQL per CLAUDE.md"
  - "Credential check before init_meta_api() call logs 'failed' status and returns early — prevents misleading errors"

patterns-established:
  - "Pattern: APScheduler zero-arg job + module-globals registration — follow for all future scheduler jobs"
  - "Pattern: ingestion_log lifecycle always started at top of try block, finished in both success and except paths"
  - "Pattern: evaluate_alerts() as FINAL step after all DB writes, inside success path only"

requirements-completed:
  - META-01
  - META-02
  - META-03
  - META-04
  - META-05
  - ALERT-01
  - ALERT-02
  - ALERT-03
  - ALERT-04
  - ALERT-05

# Metrics
duration: 2min
completed: 2026-05-19
---

# Phase 02 Plan 05: Meta Ingest Job Summary

**APScheduler-compatible zero-arg Meta ingest job with ingestion_log lifecycle, circuit breaker after 3 consecutive failures, and evaluate_alerts() as final step**

## Performance

- **Duration:** 2 min
- **Started:** 2026-05-19T08:22:41Z
- **Completed:** 2026-05-19T08:24:46Z
- **Tasks:** 1
- **Files modified:** 1

## Accomplishments

- `meta_ingest_job()` created as zero-parameter async function safe for APScheduler SQLAlchemyJobStore pickling
- Full ingestion lifecycle: credentials check, init_meta_api, campaign + adset fetches, UPSERT writes, ingestion_log transitions
- Circuit breaker fires Telegram HTML alert after 3 consecutive failures (html.escape + ParseMode.HTML)
- `evaluate_alerts()` called as the final step after all successful DB writes (D-17)
- `_get_yesterday_iso()` uses ZoneInfo for timezone-aware date computation matching Meta ad account timezone

## Task Commits

1. **Task 1: Create src/meta/ingest.py** - `f703409` (feat)

**Plan metadata:** (docs commit — see below)

## Files Created/Modified

- `src/meta/ingest.py` - APScheduler job entry point, module-globals registration, ingestion lifecycle, circuit breaker

## Decisions Made

- Module-globals pattern: `register_job_resources()` must be called from `main.py` before `scheduler.start()`. APScheduler SQLAlchemyJobStore serializes job arguments via pickle — passing Bot/DBClient as scheduler args raises PicklingError. Module globals bypass this entirely.
- Credential check added before `init_meta_api()`: when `meta_access_token` or `meta_ad_account_id` is None, log to `ingestion_log` as `failed` with a descriptive error and return early. Prevents cryptic errors from the facebook-business SDK.
- `_RECENT_FAILURES_SQL` uses `:source` and `:limit` named params per CLAUDE.md no-f-string-SQL rule.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None. All 43 existing tests passed without modification.

## Verification Results

Signature verification:
```
all checks passed
```
- `meta_ingest_job()` has ZERO parameters: confirmed
- `register_job_resources` is synchronous (not async): confirmed

Test results: 43/43 passed in 2.15s

## Known Stubs

None. `meta_ingest_job()` is a complete implementation; actual API calls depend on runtime credentials and the scheduler wiring in 02-08 (main.py).

## Threat Flags

None. No new network endpoints, auth paths, file access patterns, or schema changes introduced. Telegram messages use `html.escape()` and `ParseMode.HTML` per CLAUDE.md security requirements.

## Next Phase Readiness

- `meta_ingest_job` and `register_job_resources` are ready for wiring in 02-08 (main.py)
- 02-06 (scheduler setup) must call `register_job_resources(bot, db, settings)` before `scheduler.start()`
- No blockers

## Self-Check: PASSED

- `src/meta/ingest.py` exists: FOUND
- Commit `f703409` exists: FOUND
- 43 tests pass: CONFIRMED

---
*Phase: 02-meta-ads-ingestion-scheduled-reports-alerts*
*Completed: 2026-05-19*
