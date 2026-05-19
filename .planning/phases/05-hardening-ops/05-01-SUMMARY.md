---
phase: "05-hardening-ops"
plan: "01"
subsystem: "observability"
tags: [sentry, error-capture, asyncio-integration, settings-extension]
dependency_graph:
  requires: []
  provides: [sentry-error-capture, sentry-settings, sentry-init]
  affects: [src/meta/ingest.py, src/ga4/ingest.py, src/reports/daily.py, src/reports/weekly.py, src/alerts/engine.py]
tech_stack:
  added: ["sentry-sdk>=2.60.0,<3"]
  patterns: [conditional-init-inside-async-main, capture-exception-at-catch-sites, secretstr-dsn-extraction]
key_files:
  created: [tests/test_sentry.py]
  modified:
    - pyproject.toml
    - src/config.py
    - src/main.py
    - src/meta/ingest.py
    - src/ga4/ingest.py
    - src/reports/daily.py
    - src/reports/weekly.py
    - src/alerts/engine.py
decisions:
  - "sentry_sdk.init() placed inside async main() after load_settings(), before configure_logging() — AsyncioIntegration requires the event loop to already exist (Pitfall 1)"
  - "Lazy import (import sentry_sdk inside if-guard) prevents sentry_sdk from loading when SENTRY_DSN is absent — no startup cost for operators who do not use Sentry"
  - "capture_exception() at all 5 outer catch-and-suppress sites; no per-call guards needed because sentry-sdk 2.x capture_exception is a no-op when uninitialized (verified in test 4)"
  - "sentry_dsn stored as SecretStr; .get_secret_value() called only at the init site to prevent DSN from appearing in logs or repr"
metrics:
  duration: "2m 32s"
  completed_date: "2026-05-19"
  tasks_completed: 3
  files_changed: 9
  tests_added: 4
  tests_total: 160
---

# Phase 5 Plan 01: Sentry Integration Summary

**One-liner:** Sentry error capture wired via conditional AsyncioIntegration init in async main() and explicit capture_exception() at 5 ingest/report catch-and-suppress sites.

## What Was Built

- **pyproject.toml:** Added `sentry-sdk>=2.60.0,<3` to `[project] dependencies`.
- **src/config.py:** Added `sentry_dsn: SecretStr | None = None` and `sentry_environment: str = "production"` fields after the Anthropic block. No validator needed — pydantic-settings reads `SENTRY_DSN` env var automatically for SecretStr fields.
- **src/main.py:** Inserted conditional `sentry_sdk.init()` block inside `async main()` between `load_settings()` and `configure_logging()`. Uses `AsyncioIntegration`, `traces_sample_rate=0.0`, `send_default_pii=False`. Lazy import prevents sentry_sdk overhead when DSN is absent.
- **5 ingest/report modules:** Added `import sentry_sdk` at module level and `sentry_sdk.capture_exception(exc)` as first line of the outer `except Exception as exc:` block in each:
  - `src/meta/ingest.py` (`_run_meta_ingest`)
  - `src/ga4/ingest.py` (`_run_ga4_ingest`)
  - `src/reports/daily.py` (`_run_daily_report`)
  - `src/reports/weekly.py` (`_run_weekly_report`)
  - `src/alerts/engine.py` (`evaluate_alerts`)
- **tests/test_sentry.py:** 4-test suite covering init/no-init branching, capture at ingest failure, and no-op when SDK uninitialized.

## Commits

| Task | Commit | Description |
|------|--------|-------------|
| 1 | 85a913a | feat(05-01): add sentry-sdk dependency, Settings fields, conditional init in main.py |
| 2 | 4ae2c38 | feat(05-01): add sentry_sdk.capture_exception() to all 5 catch-and-suppress sites |
| 3 | 3f2daa0 | test(05-01): add test_sentry.py — 4 tests covering init/no-init branching and capture paths |

## Test Results

```
160 passed in 7.19s
```

- 156 pre-existing tests: all pass (no regression)
- 4 new tests in `tests/test_sentry.py`: all pass

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

None. All Sentry integration is fully wired: DSN read from env var, init called conditionally, capture_exception active at all sites.

## Threat Flags

All threats mitigated as specified in plan threat model:

| Threat | Mitigation Applied |
|--------|--------------------|
| T-05-01-01: SENTRY_DSN disclosure | `sentry_dsn: SecretStr` — structlog redaction already masks SecretStr; `.get_secret_value()` called only at init |
| T-05-01-02: Exception PII to Sentry | `send_default_pii=False` in init; campaign data already wrapped in data-tags per CLAUDE.md |
| T-05-01-04: Sentry async blocking | `AsyncioIntegration` uses async-safe transport; `traces_sample_rate=0.0` disables perf tracing |

## Self-Check: PASSED

- All 9 files exist on disk
- All 3 task commits exist in git history (85a913a, 4ae2c38, 3f2daa0)
- 160 tests pass (no regression)
