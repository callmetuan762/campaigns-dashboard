---
phase: "05-hardening-ops"
plan: "02"
subsystem: "reports"
tags: [graceful-degradation, reports, observability, sc-2]

dependency_graph:
  requires:
    - "05-01: Sentry SDK integrated (sentry_sdk.capture_exception available)"
  provides:
    - "SC-2: per-source failures degrade gracefully with unavailability notices"
    - "meta_available / ga4_available kwargs in both builder functions"
    - "Per-source guarded fetch blocks in daily and weekly report jobs"
    - "ingestion_log checked to distinguish failed ingestion from zero-spend days"
  affects:
    - "src/reports/builder.py: backward-compatible signature extension"
    - "src/reports/daily.py: _run_daily_report refactored"
    - "src/reports/weekly.py: _run_weekly_report refactored"

tech_stack:
  added: []
  patterns:
    - "Per-source guarded try/except blocks with availability flags"
    - "ingestion_log status check to distinguish failure from zero-spend (Pitfall 5)"
    - "Static HTML notice strings injected into builder when source unavailable"
    - "Inline sentry_sdk.capture_exception in per-source except blocks"

key_files:
  created:
    - tests/test_graceful_degradation.py
  modified:
    - src/reports/builder.py
    - src/reports/daily.py
    - src/reports/weekly.py

decisions:
  - "Used <b> tags for unavailability notices (visually distinct from existing <i> empty-rows notice)"
  - "GA4 notice injected using elif pattern so it does not duplicate when ga4_available=False with existing ga4_campaign_rows guard"
  - "Inline import sentry_sdk in per-source except blocks per plan spec; outer except reuses module-level import"
  - "pytestmark = pytest.mark.asyncio omitted from module level; @pytest.mark.asyncio applied per-test to avoid PytestWarning on sync tests"

metrics:
  duration: "~12 minutes"
  completed_date: "2026-05-19"
  tasks_completed: 3
  files_modified: 4
---

# Phase 5 Plan 02: Graceful Degradation Summary

**One-liner:** Per-source guarded fetch blocks in daily and weekly report jobs with HTML unavailability notices when Meta or GA4 ingestion fails, satisfying SC-2.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Extend report builder with meta_available / ga4_available flags | 21b0858 | src/reports/builder.py |
| 2 | Refactor daily.py and weekly.py to per-source guarded fetch blocks | fbdd70e | src/reports/daily.py, src/reports/weekly.py |
| 3 | tests/test_graceful_degradation.py — builder unit tests and report-job independence tests | f9cb7ae | tests/test_graceful_degradation.py |

## What Was Built

### Task 1 — Builder availability flags (21b0858)

`build_daily_report_html` and `build_weekly_report_html` each gained two trailing keyword parameters:
- `meta_available: bool = True`
- `ga4_available: bool = True`

When `meta_available=False`, a `<b>⚠️ Meta Ads data unavailable for this date</b> (ingestion failed — check logs)` notice is injected before the existing empty-rows check. When `ga4_available=False`, a matching GA4 notice replaces the GA4 section. Both populated-data and empty-data code paths handle the flags. All existing callers remain backward-compatible (defaults=True).

### Task 2 — Per-source guarded fetch blocks (fbdd70e)

The monolithic `try/except` in `_run_daily_report` and `_run_weekly_report` was split into three sections:

1. **Meta guarded block** — queries `_YESTERDAY_METRICS_SQL`, `_WEEK_METRICS_SQL`, and `ingestion_log` (to distinguish failure from zero-spend). Sets `meta_available=False` on exception or confirmed ingestion failure.
2. **GA4 guarded block** — queries GA4 SQL constants and `ingestion_log` (same failure-vs-zero-traffic distinction). Sets `ga4_available=False` on exception or confirmed ingestion failure.
3. **Outer try/except** — TL;DR generation, `build_*_report_html(..., meta_available=meta_available, ga4_available=ga4_available)`, Telegram send, `ping_heartbeat` (D-20 ordering preserved). The existing `sentry_sdk.capture_exception` call is retained here.

All `ingestion_log` queries use named params (`:source`) — no f-strings. `sentry_sdk.capture_exception(exc)` called in each per-source except block.

### Task 3 — Test suite (f9cb7ae)

`tests/test_graceful_degradation.py` — 7 tests:

**Builder unit tests (sync):**
- `test_daily_report_meta_unavailable_notice` — HTML contains "Meta Ads data unavailable"
- `test_daily_report_ga4_unavailable_notice` — HTML contains "GA4 data unavailable"
- `test_daily_report_both_unavailable_no_crash` — non-empty HTML, no exception
- `test_weekly_report_meta_unavailable_notice` — weekly HTML contains Meta notice
- `test_weekly_report_ga4_unavailable_notice` — weekly HTML contains GA4 notice

**Job independence tests (async):**
- `test_daily_report_completes_when_meta_query_fails` — first `fetch_all` raises; `_run_daily_report` completes; `build_daily_report_html` called with `meta_available=False`
- `test_daily_report_completes_when_ga4_query_fails` — first two `fetch_all` calls succeed; third raises; `_run_daily_report` completes; builder called with `ga4_available=False`, `meta_available=True`

## Verification Results

```
python -c "from src.reports.builder import build_daily_report_html; ..."  PASS (meta notice)
python -c "from src.reports.builder import build_daily_report_html; ..."  PASS (backward compat)
pytest tests/test_graceful_degradation.py -x -q                          7 passed
pytest tests/ -x -q                                                       167 passed (160 + 7 new)
AST parse daily.py / weekly.py                                            OK syntax
```

## Deviations from Plan

**1. [Rule 2 - Style] Removed module-level pytestmark; applied @pytest.mark.asyncio per-test**

- **Found during:** Task 3 test run
- **Issue:** `pytestmark = pytest.mark.asyncio` on a file with synchronous test functions triggers `PytestWarning: test is marked with '@pytest.mark.asyncio' but it is not an async function` for all 5 sync builder tests
- **Fix:** Removed module-level `pytestmark`; added `@pytest.mark.asyncio` decorator to the 2 async test functions only
- **Files modified:** tests/test_graceful_degradation.py
- **Impact:** Zero — all 7 tests still pass, warnings eliminated

No other deviations — plan executed as written.

## Known Stubs

None.

## Threat Flags

No new network endpoints, auth paths, file access patterns, or schema changes introduced. All mitigations in the plan's threat register (T-05-02-01 through T-05-02-04) applied:
- T-05-02-02: ingestion_log SQL uses named params `:source` with hardcoded string literals "meta_ads"/"ga4" as param values — no user input in query path.
- T-05-02-01: `error=str(exc)` logged to structlog only — not included in Telegram HTML.

## Self-Check: PASSED

- `src/reports/builder.py` — exists, contains `meta_available: bool = True`
- `src/reports/daily.py` — exists, contains `meta_available`, `daily_report_meta_query_failed`
- `src/reports/weekly.py` — exists, contains `meta_available`, `ga4_available`
- `tests/test_graceful_degradation.py` — exists, 7 tests pass
- Commits 21b0858, fbdd70e, f9cb7ae — all present in git log
