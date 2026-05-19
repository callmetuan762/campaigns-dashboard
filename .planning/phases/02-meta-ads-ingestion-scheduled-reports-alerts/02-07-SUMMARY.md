---
phase: "02"
plan: "07"
subsystem: scheduler-wiring
tags: [apscheduler, scheduler, telegram, handlers, main]
dependency_graph:
  requires: [02-05, 02-06]
  provides: [scheduler-wired, report-command]
  affects: [src/main.py, src/bot/handlers.py]
tech_stack:
  added: []
  patterns: [module-globals-apscheduler, crontrigger-timezone, inline-job-trigger]
key_files:
  modified:
    - src/main.py
    - src/bot/handlers.py
decisions:
  - Register module resources before scheduler.add_job() — avoids PicklingError
  - /report handler calls _run_daily_report directly using module globals (no re-check of allowlist needed — middleware handles it)
metrics:
  duration: "5m"
  completed_date: "2026-05-19"
---

# Phase 2 Plan 07: Scheduler Wiring + /report Handler Summary

**One-liner:** Replaced Phase 1 heartbeat with 3 real CronTrigger jobs (meta_ingest 02:00, daily_report 09:00, weekly_report Mon 09:00) and added /report manual trigger handler using module globals pattern.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Modify src/main.py | 00888de | src/main.py |
| 2 | Add /report handler to handlers.py | 95a1d45 | src/bot/handlers.py |

## Changes Made

### src/main.py
- Removed `_scheduler_heartbeat` Phase 1 placeholder function entirely
- Added imports for `meta_ingest_module`, `daily_report_module`, `weekly_report_module`
- Added `register_job_resources()` calls for all 3 modules BEFORE `scheduler.add_job()`
- Replaced single Phase 1 heartbeat job with 3 real CronTrigger jobs:
  - `meta_ingest`: `hour=settings.meta_ingest_hour` (default 02:00) in `report_timezone`
  - `daily_report`: `hour=settings.daily_report_hour` (default 09:00) in `report_timezone`
  - `weekly_report`: `day_of_week="mon"`, `hour=settings.daily_report_hour` in `report_timezone`
- All 3 jobs have `misfire_grace_time=300`, `coalesce=True`, `max_instances=1`
- Updated `log.info("boot", phase=1, ...)` to `phase=2`
- Updated docstring to reflect Phase 2 lifecycle

### src/bot/handlers.py
- Added `import src.reports.daily as daily_report_module`
- Added `cmd_report` handler for `/report` command
  - Sends "Generating report..." acknowledgment with HTML formatting
  - Calls `daily_report_module._run_daily_report()` using module globals
  - AllowlistMiddleware already validated sender — no duplicate check needed
- Updated `/help` response to include `/report` command and Phase 2 status message

## Test Results

```
43 passed in 1.65s
```

All 43 existing tests pass. Syntax check and import chain verification passed.

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

None.

## Threat Flags

None — no new network endpoints, auth paths, or trust boundary changes introduced.

## Self-Check: PASSED

- src/main.py: exists, syntax ok, all acceptance criteria met
- src/bot/handlers.py: exists, syntax ok, all acceptance criteria met
- Commits 00888de and 95a1d45 confirmed in git log
- 43/43 tests pass
