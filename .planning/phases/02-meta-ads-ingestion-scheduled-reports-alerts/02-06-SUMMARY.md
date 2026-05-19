---
phase: "02"
plan: "06"
subsystem: reports
tags: [apscheduler, telegram, reports, heartbeat, charts, tldr]
dependency_graph:
  requires: [02-03-report-builders, 02-04-alert-engine, src/ai/tldr.py, src/db/client.py]
  provides: [daily_report_job, weekly_report_job, ping_heartbeat]
  affects: [main.py scheduler registration]
tech_stack:
  added: [httpx (heartbeat HTTP client)]
  patterns: [module-globals APScheduler pattern, asyncio.to_thread for matplotlib, D-20 heartbeat ordering]
key_files:
  created:
    - src/reports/daily.py
    - src/reports/weekly.py
  modified: []
decisions:
  - Module-level globals (_bot, _db, _settings) used instead of job args to satisfy APScheduler SQLAlchemyJobStore pickle constraint
  - ping_heartbeat() imported and reused in weekly.py from daily.py to avoid duplication
  - asyncio.to_thread wraps all three chart generators to isolate matplotlib's non-async-safe Agg backend
  - ping_heartbeat placed inside try block, after all send_photo calls, so any Telegram delivery failure prevents heartbeat (D-20 guarantee)
metrics:
  duration: "~2 minutes"
  completed: "2026-05-19"
  tasks: 2
  files_created: 2
  files_modified: 0
---

# Phase 02 Plan 06: Daily and Weekly APScheduler Report Jobs Summary

Daily and weekly zero-arg APScheduler jobs that query SQLite exclusively, assemble HTML reports with WoW comparisons, send charts as Telegram photo messages, generate TL;DR via Anthropic, and ping heartbeat only after all Telegram deliveries succeed.

## Tasks Completed

| Task | Description | Commit | Files |
|------|-------------|--------|-------|
| 1 | Create src/reports/daily.py | e5bc800 | src/reports/daily.py |
| 2 | Create src/reports/weekly.py | e5bc800 | src/reports/weekly.py |

## What Was Built

### src/reports/daily.py
- `daily_report_job()` — zero-arg async APScheduler entry point
- `register_job_resources(bot, db, settings)` — synchronous registration of module globals before scheduler start
- `ping_heartbeat(url)` — async fire-and-forget; swallows all errors; must be called AFTER Telegram returns 200
- `_run_daily_report()` — queries yesterday's campaign metrics + 7-day window, generates TL;DR, builds HTML, splits at 4096 chars, sends text parts + 3 chart images, then pings heartbeat

### src/reports/weekly.py
- `weekly_report_job()` — zero-arg async APScheduler entry point (Monday 09:00)
- `register_job_resources(bot, db, settings)` — synchronous module-globals setup
- `_run_weekly_report()` — queries two week windows for WoW comparison, generates TL;DR, builds weekly HTML, sends text + 3 charts, pings heartbeat
- Imports `ping_heartbeat` from `src.reports.daily` (DRY, single implementation)

## Verification Results

```
all checks passed
```

Checks verified:
- `len(inspect.signature(daily_report_job).parameters) == 0` — True
- `len(inspect.signature(weekly_report_job).parameters) == 0` — True
- `inspect.iscoroutinefunction(register_job_resources)` — False (sync for both)
- `inspect.iscoroutinefunction(ping_heartbeat)` — True

Full test suite: **43/43 passed** (2.14s)

## Key Design Decisions

1. **Module-globals pattern** — APScheduler's SQLAlchemyJobStore uses pickle to serialize jobs. Passing bot/db/settings as arguments would cause PicklingError. Module-level globals (`_bot`, `_db`, `_settings`) registered via synchronous `register_job_resources()` before `scheduler.start()` solve this cleanly.

2. **D-20 heartbeat ordering** — `ping_heartbeat()` placed at the end of the `try` block, after all `send_message`/`send_photo` calls. Any Telegram delivery failure raises an exception, which is caught by the outer `except`, never reaching the heartbeat. A `finally` block would violate this constraint.

3. **asyncio.to_thread for charts** — matplotlib's Agg backend is not coroutine-safe. Wrapping all three chart generators (`generate_spend_trend_chart`, `generate_roas_trend_chart`, `generate_top_campaigns_chart`) in `asyncio.to_thread()` keeps them off the event loop thread.

4. **ping_heartbeat reuse** — weekly.py imports `ping_heartbeat` from daily.py rather than duplicating. Single implementation, single point of change.

## Requirements Addressed

- REPORT-01: Daily job at settings.daily_report_hour in report_timezone
- REPORT-02: TL;DR, spend, ROAS, top/bottom campaigns — via build_daily_report_html
- REPORT-03: Monday weekly summary with WoW comparisons — via build_weekly_report_html + get_wow_date_ranges
- REPORT-04: HTML format, 4096-char split via split_html_message
- REPORT-05: Heartbeat after Telegram 200 (D-20)
- REPORT-06: Charts sent as separate send_photo calls

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

None. Both jobs are fully wired: SQLite queries use real schema columns, all report builders and chart generators are from completed Wave 2 plans, TL;DR calls live Anthropic API.

## Threat Flags

None. No new network endpoints introduced. Heartbeat URL is outbound-only (no inbound surface). All Telegram sends go through existing bot instance. No new auth paths or schema changes.

## Self-Check: PASSED

- src/reports/daily.py: FOUND
- src/reports/weekly.py: FOUND
- Commit e5bc800: FOUND (git log confirmed)
- 43/43 tests passing
