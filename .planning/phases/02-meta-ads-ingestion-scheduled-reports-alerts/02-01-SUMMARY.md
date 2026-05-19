---
phase: "02"
plan: "01"
subsystem: foundation-extension
tags: [config, schema, db, bot, settings, migrations, alerts]
dependency_graph:
  requires: [01-scaffold, 01-database, 01-telegram-bot]
  provides: [alert-thresholds-config, scheduling-config, migration-002, upsert-campaign, ingestion-log-helpers, alert-log-dedup, html-parse-mode]
  affects: [src/config.py, src/db/schema.py, src/db/client.py, src/bot/setup.py, src/bot/handlers.py]
tech_stack:
  added: []
  patterns: [named-param-sql, insert-or-ignore-dedup, html-escape-output]
key_files:
  modified:
    - src/config.py
    - src/db/schema.py
    - src/db/client.py
    - src/bot/setup.py
    - src/bot/handlers.py
decisions:
  - SQL constants defined as class attributes outside methods, matching existing _UPSERT_AD_METRICS_SQL pattern
  - log_alert() returns bool (True=newly fired, False=duplicate) using INSERT OR IGNORE + rowcount check
  - html.escape() applied only to dynamic values in /status; static text does not need escaping
metrics:
  duration: "1m 44s"
  completed: "2026-05-19"
  tasks_completed: 2
  files_modified: 5
---

# Phase 02 Plan 01: Foundation Extension Summary

**One-liner:** Extended config, schema, DBClient, and bot parse mode so all Phase 2 subsystems have the contracts they need — alert thresholds, scheduling fields, MIGRATION_002_PHASE2 (alert_log), campaign/log/alert DB helpers, and HTML parse mode.

## Status: COMPLETE

## Tasks Completed

### Task 1: Extend Settings and add MIGRATION_002_PHASE2

**Files:** `src/config.py`, `src/db/schema.py`
**Commit:** 3824ff6

- Added 3 scheduling fields to Settings: `meta_ingest_hour`, `daily_report_hour`, `heartbeat_url`
- Added 5 alert threshold fields: `alert_spend_spike_pct`, `alert_roas_floor`, `alert_zero_conv_spend_threshold`, `alert_budget_pacing_pct`, `alert_cpc_spike_multiplier`
- Added `MIGRATION_002_PHASE2` constant creating `alert_log` table with `UNIQUE(alert_type, campaign_id, date)` and date index
- Registered `002_phase2` in `ALL_MIGRATIONS` (list now length 2)

### Task 2: Add DBClient Phase 2 helpers and switch bot to ParseMode.HTML

**Files:** `src/db/client.py`, `src/bot/setup.py`, `src/bot/handlers.py`
**Commit:** 04f7964

- Added `upsert_campaign()` — idempotent campaign dimension upsert using `INSERT ... ON CONFLICT DO UPDATE`
- Added `log_ingestion_start()` — inserts `running` ingestion_log row, returns `lastrowid`
- Added `log_ingestion_finish()` — updates ingestion_log row to `success`/`failed`/`partial`
- Added `log_alert()` — `INSERT OR IGNORE` with UNIQUE constraint; returns `True` if newly fired, `False` if duplicate
- Switched `DefaultBotProperties` from `ParseMode.MARKDOWN` to `ParseMode.HTML`
- Converted `/status` handler: Markdown bold/backtick replaced with `<b>`, `<code>` tags; dynamic values wrapped in `html.escape()`
- Converted `/help` handler: Markdown bold/italic replaced with `<b>`, `<i>` tags

## Test Results

All 7 existing tests passed — zero regressions:

```
tests/test_allowlist.py::test_disallowed_chat_dropped PASSED
tests/test_allowlist.py::test_allowed_chat_passes PASSED
tests/test_allowlist.py::test_allowed_user_passes PASSED
tests/test_allowlist.py::test_message_text_not_logged PASSED
tests/test_upsert_idempotency.py::test_migration_is_idempotent PASSED
tests/test_upsert_idempotency.py::test_ad_metrics_upsert_is_idempotent PASSED
tests/test_upsert_idempotency.py::test_ga4_metrics_upsert_is_idempotent PASSED

7 passed in 1.39s
```

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

None — this plan extends infrastructure contracts; no UI rendering or data sources involved.

## Threat Flags

None — no new network endpoints, auth paths, file access patterns, or schema changes at trust boundaries beyond what was planned. The `alert_log` table is internal-only, written by the ingestion pipeline.

## Self-Check: PASSED

- `src/config.py` modified — alert_spend_spike_pct, alert_roas_floor, heartbeat_url, meta_ingest_hour present
- `src/db/schema.py` modified — MIGRATION_002_PHASE2 and 002_phase2 in ALL_MIGRATIONS present
- `src/db/client.py` modified — upsert_campaign, log_ingestion_start, log_alert, INSERT OR IGNORE present
- `src/bot/setup.py` modified — ParseMode.HTML present, ParseMode.MARKDOWN absent
- `src/bot/handlers.py` modified — html.escape appears 2+ times, `<b>` tags appear 2+ times
- Commit 3824ff6 exists (Task 1)
- Commit 04f7964 exists (Task 2)
