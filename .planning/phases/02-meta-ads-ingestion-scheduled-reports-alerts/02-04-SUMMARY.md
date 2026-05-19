---
phase: 02-meta-ads-ingestion-scheduled-reports-alerts
plan: "04"
subsystem: alerts
tags: [aiogram, aiosqlite, sqlite-window-functions, html-escaping, alert-dedup]

# Dependency graph
requires:
  - phase: 02-01
    provides: "alert_log table, log_alert() DBClient method, alert threshold Settings fields"
  - phase: 02-02
    provides: "ad_metrics campaign-level rows (ad_set_id='', ad_id='' sentinels)"
provides:
  - "evaluate_alerts(db, bot, settings, target_date) — evaluates 5 alert conditions post-ingest"
  - "AlertType StrEnum — SPEND_SPIKE, ROAS_DROP, ZERO_CONVERSION, BUDGET_PACING, CPC_SPIKE"
  - "HTML-formatted Telegram alert messages with emoji severity indicators"
  - "Alert deduplication: one alert per campaign per type per calendar day via alert_log"
affects:
  - "02-05 (meta ingest scheduler) — calls evaluate_alerts as final step of meta_ingest_job"
  - "Phase 4 (conversational AI) — alert history in alert_log available for query tools"

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "SQLite window function AVG() OVER ROWS BETWEEN 7 PRECEDING AND 1 PRECEDING for rolling average"
    - "db.log_alert() called before bot.send_message() — dedup check gates delivery (D-18)"
    - "html.escape() on all campaign names and date strings before f-string interpolation (T-02-11)"
    - "Exception-safe top-level try/except in evaluate_alerts — ingest never aborted by alert failure"

key-files:
  created:
    - src/alerts/__init__.py
    - src/alerts/engine.py
  modified:
    - tests/test_alert_engine.py

key-decisions:
  - "Budget pacing (ALERT-04) uses days_elapsed < 7 guard to avoid false alerts early in month"
  - "evaluate_alerts catches all exceptions at top level — alert failure must never abort ingest"
  - "chat_id sourced from settings.telegram_allowed_chat_ids[0] — operator-configured, not user input (T-02-14)"

patterns-established:
  - "Rolling average SQL: ROWS BETWEEN 7 PRECEDING AND 1 PRECEDING excludes today from avg"
  - "Alert gate: db.log_alert() BEFORE bot.send_message() — INSERT OR IGNORE enforces D-18"
  - "HTML safety: html.escape() on every dynamic value interpolated into Telegram HTML messages"

requirements-completed:
  - ALERT-01
  - ALERT-02
  - ALERT-03
  - ALERT-04
  - ALERT-05

# Metrics
duration: 3min
completed: 2026-05-19
---

# Phase 2 Plan 04: Alert Engine Summary

**5-alert-type engine using SQLite window functions for spike detection, INSERT OR IGNORE dedup via alert_log, and HTML-escaped Telegram messages with emoji severity indicators**

## Performance

- **Duration:** ~3 min
- **Started:** 2026-05-19T08:15:33Z
- **Completed:** 2026-05-19T08:18:27Z
- **Tasks:** 1 (TDD: RED + GREEN)
- **Files modified:** 3

## Accomplishments
- `evaluate_alerts()` checks all 5 conditions (spend spike, ROAS drop, zero conversion, budget pacing, CPC spike) against ingested data for a given target_date
- Rolling average SQL uses `AVG() OVER (ROWS BETWEEN 7 PRECEDING AND 1 PRECEDING)` to exclude today from the baseline — correct for spike detection
- Alert deduplication via `db.log_alert()` called before `bot.send_message()` — INSERT OR IGNORE at DB layer ensures one alert per campaign per type per calendar day (D-18)
- All campaign names and dates run through `html.escape()` before interpolation into HTML alert messages (T-02-11)
- Exception-safe: evaluate_alerts wraps everything in try/except — Telegram failures or eval errors never abort the ingest job (D-17)
- 14 new tests covering all 5 alert conditions, dedup, HTML escaping, and exception safety

## TDD Gate Compliance

- RED gate commit: `ee50a1a` — `test(02-04): add failing tests for alert engine (TDD RED)`
- GREEN gate commit: `4cf0bc5` — `feat(02-04): implement alert engine with 5 alert types (TDD GREEN)`
- REFACTOR: not required — implementation clean as written

## Task Commits

1. **Task 1 RED: Failing tests** - `ee50a1a` (test)
2. **Task 1 GREEN: Alert engine implementation** - `4cf0bc5` (feat)

## Files Created/Modified
- `src/alerts/__init__.py` — Package init for alerts module
- `src/alerts/engine.py` — evaluate_alerts() + AlertType StrEnum + 5 alert condition implementations
- `tests/test_alert_engine.py` — 14 tests covering all alert types, dedup, HTML escaping, exception safety

## Decisions Made
- Budget pacing alert uses a `days_elapsed < 7` guard to avoid spurious alerts at the start of the month when there is insufficient data to project
- Used `db.fetch_all(_CAMPAIGN_NAME_SQL, ...)` with `LIMIT 1` in SQL (consistent with note that `fetch_one` exists but using `fetch_all` with LIMIT 1 is also safe per plan guidance)
- `chat_id` sourced from `settings.telegram_allowed_chat_ids[0]` — operator-configured env var, not from user input

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed test assertion checking only last call instead of all calls**
- **Found during:** Task 1 GREEN (running tests)
- **Issue:** `test_spend_spike_fires_when_spend_above_threshold` used `bot.send_message.call_args` which returns only the LAST call — Budget Pacing alert fired after Spend Spike, causing the assertion to check the Budget Pacing message text instead of the Spend Spike message
- **Fix:** Changed assertion to join text from all `call_args_list` entries and search that combined string for "Spend Spike"
- **Files modified:** tests/test_alert_engine.py
- **Verification:** All 14 tests pass; 43 total pass
- **Committed in:** `4cf0bc5` (GREEN task commit)

---

**Total deviations:** 1 auto-fixed (Rule 1 — Bug in test assertion)
**Impact on plan:** Minor test-quality fix; engine implementation was correct on first attempt.

## Issues Encountered
- None beyond the test assertion fix above.

## User Setup Required
None — no external service configuration required for this plan.

## Next Phase Readiness
- `evaluate_alerts()` is ready to be wired as the final step of `meta_ingest_job` in Plan 02-05
- `AlertType` enum provides the alert_type strings that will be stored in alert_log for Phase 4 query tools
- All 43 tests pass; no blockers

## Known Stubs
None — all 5 alert conditions are fully implemented with real SQLite queries.

## Threat Flags
No new threat surface introduced. T-02-11 (HTML injection via campaign names) and T-02-12 (SQL injection via f-string SQL) both mitigated as required by plan's threat model.

---
*Phase: 02-meta-ads-ingestion-scheduled-reports-alerts*
*Completed: 2026-05-19*
