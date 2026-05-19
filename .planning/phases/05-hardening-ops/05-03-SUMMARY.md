---
phase: 05-hardening-ops
plan: "03"
subsystem: backfill
tags: [backfill, cli, historical-data, ingest, meta, ga4]
dependency_graph:
  requires: [05-01, 05-02]
  provides: [backfill-cli, run_meta_ingest_for_date, run_ga4_ingest_for_date]
  affects: [src/meta/ingest.py, src/ga4/ingest.py, src/backfill.py]
tech_stack:
  added: []
  patterns: [argparse-cli, suppress-alerts-flag, skip-cache-flag, async-date-loop]
key_files:
  created:
    - src/backfill.py
    - tests/test_backfill.py
  modified:
    - src/meta/ingest.py
    - src/ga4/ingest.py
decisions:
  - backfill_main uses public wrappers (run_meta_ingest_for_date / run_ga4_ingest_for_date) so backfill never calls evaluate_alerts or the 6-hour cache check
  - Credential guard in ga4/ingest.py stays unconditional before skip_cache block
  - dry_run=True returns before opening DB connection (no side effects)
  - Individual ingest errors propagate and halt the loop — operator fixes and resumes from failing date
metrics:
  duration: "2m 33s"
  completed_date: "2026-05-19"
  tasks_completed: 3
  files_changed: 4
---

# Phase 5 Plan 03: Backfill CLI Summary

**One-liner:** Argparse backfill CLI that replays Meta/GA4 ingestion over any historical date window using suppress_alerts=True and skip_cache=True flags added to both ingest modules.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Add date_override + backfill params to _run_meta_ingest and _run_ga4_ingest | 4eada5b | src/meta/ingest.py, src/ga4/ingest.py |
| 2 | Create src/backfill.py CLI with argparse, date loop, and structured logging | 9bfe3c7 | src/backfill.py |
| 3 | tests/test_backfill.py — date range, alert suppression, cache bypass, dry-run | 26986af | tests/test_backfill.py |

## What Was Built

**src/meta/ingest.py** — `_run_meta_ingest` extended with `date_override: str | None = None` and `suppress_alerts: bool = False`. The evaluate_alerts call is now guarded by `if not suppress_alerts:`. Public wrapper `run_meta_ingest_for_date(db, settings, date_iso)` added — always passes `bot=None`, `suppress_alerts=True`.

**src/ga4/ingest.py** — `_run_ga4_ingest` extended with `date_override: str | None = None` and `skip_cache: bool = False`. The 6-hour cache check block is now guarded by `if not skip_cache:`. Credential guard remains unconditional before the cache check. Public wrapper `run_ga4_ingest_for_date(db, settings, date_iso)` added — always passes `bot=None`, `skip_cache=True`.

**src/backfill.py** — Standalone argparse CLI invocable as `python -m src.backfill`. Accepts `--source {meta,ga4,all}`, `--start YYYY-MM-DD`, `--end YYYY-MM-DD`, `--dry-run`. `_date_range()` produces inclusive ISO date lists. `backfill_main()` iterates dates and calls the appropriate public wrappers. Structured log events: `backfill_date_start`, `backfill_date_current`, `backfill_complete`.

**tests/test_backfill.py** — 8 tests covering: inclusive 3-day range, single-day range, meta 3-call loop, ga4 3-call loop, all-source 2x2 calls, dry-run no-op, suppress_alerts=True in meta wrapper, skip_cache=True in GA4 wrapper.

## APScheduler Entry Points Unchanged

`meta_ingest_job()` and `ga4_ingest_job()` call `_run_meta_ingest(_bot, _db, _settings)` and `_run_ga4_ingest(_bot, _db, _settings)` with no override args. The new params have defaults (`None`/`False`) that preserve the existing scheduled-job behavior exactly.

## Verification Results

- `python -m src.backfill --help` — prints usage without error
- `_date_range(date(2026,5,1),date(2026,5,3))` returns `['2026-05-01','2026-05-02','2026-05-03']`
- `_run_meta_ingest` signature includes `suppress_alerts` param
- `_run_ga4_ingest` signature includes `skip_cache` param
- `pytest tests/test_backfill.py -x -q` — 8 tests passed
- `pytest tests/ -x -q` — 175 tests passed (no regressions; 167→175)

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

None. The backfill CLI is fully wired to the existing public wrappers. No placeholders.

## Threat Surface Scan

No new network endpoints, auth paths, or schema changes introduced. The backfill CLI is an operator-only tool that reuses existing ingest paths. Threat mitigations T-05-03-01 through T-05-03-03 are implemented as specified:
- `date.fromisoformat()` validates ISO format before any DB access
- `argparse choices=["meta","ga4","all"]` rejects invalid source values
- `date_iso` (validated ISO string) reaches SQL only via named params in DBClient

## Self-Check: PASSED

- `src/backfill.py` — FOUND
- `tests/test_backfill.py` — FOUND
- Commit 4eada5b — FOUND (feat(05-03): add date_override + backfill params)
- Commit 9bfe3c7 — FOUND (feat(05-03): create src/backfill.py)
- Commit 26986af — FOUND (test(05-03): add tests/test_backfill.py)
