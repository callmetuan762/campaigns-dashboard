---
phase: 05-hardening-ops
verified: 2026-05-19T00:00:00Z
status: passed
score: 3/3 must-haves verified
overrides_applied: 0
---

# Phase 5: Hardening & Ops Verification Report

**Phase Goal:** Post-v1 reliability, observability, and operability — Sentry error capture, per-source graceful degradation, and a backfill CLI for historical data replay.
**Verified:** 2026-05-19
**Status:** PASSED
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | SC-1: Operator has a documented backfill command to replay historical Meta/GA4 windows into the canonical store | VERIFIED | `src/backfill.py` exists; `python -m src.backfill --help` produces correct usage; argparse declares `--source {meta,ga4,all}`, `--start`, `--end`, `--dry-run`; `backfill_main` iterates an inclusive date range and calls `run_meta_ingest_for_date` / `run_ga4_ingest_for_date` |
| 2 | SC-2: Per-source ingestion failures degrade gracefully — Meta failure does not block GA4 reports and vice versa, with explicit "data unavailable" notices in the digest | VERIFIED | `_run_daily_report` and `_run_weekly_report` each have independent Meta and GA4 guarded fetch blocks; builder flags `meta_available=False`/`ga4_available=False` inject `<b>⚠️ Meta Ads data unavailable …</b>` and `<b>⚠️ GA4 data unavailable …</b>` notices; `ingestion_log` queried to distinguish failed ingestion from zero-spend days |
| 3 | SC-3: Errors are forwarded to Sentry and the dead-man's-switch alerts the operator when heartbeats stop | VERIFIED | `sentry-sdk>=2.60.0,<3` in `pyproject.toml`; `sentry_dsn: SecretStr \| None` and `sentry_environment` in `Settings`; conditional `sentry_sdk.init()` with `AsyncioIntegration` inside `async main()` after `load_settings()`, before `configure_logging()`; `capture_exception(exc)` at all 5 catch-and-suppress sites in ingest and report modules; `ping_heartbeat` wired inside outer try (after Telegram 200) in both `daily.py` and `weekly.py` |

**Score:** 3/3 truths verified

---

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `pyproject.toml` | `sentry-sdk>=2.60.0,<3` dependency | VERIFIED | Line 21: `"sentry-sdk>=2.60.0,<3"` present in `[project] dependencies` |
| `src/config.py` | `sentry_dsn` and `sentry_environment` Settings fields | VERIFIED | Lines 50-51: `sentry_dsn: SecretStr \| None = None` and `sentry_environment: str = "production"` after Anthropic block |
| `src/main.py` | Conditional `sentry_sdk.init()` inside `async main()` | VERIFIED | Lines 38-47: lazy import guard, `AsyncioIntegration`, `traces_sample_rate=0.0`, `send_default_pii=False`; placement is after `load_settings()`, before `configure_logging()` |
| `src/meta/ingest.py` | `capture_exception` at outer except; `date_override` + `suppress_alerts` params; `run_meta_ingest_for_date` wrapper | VERIFIED | Line 157: `sentry_sdk.capture_exception(exc)`; lines 88-89: new optional params with correct defaults; lines 182-187: public wrapper always passes `suppress_alerts=True` |
| `src/ga4/ingest.py` | `capture_exception` at outer except; `date_override` + `skip_cache` params; `run_ga4_ingest_for_date` wrapper | VERIFIED | Line 139: `sentry_sdk.capture_exception(exc)`; lines 73-74: new optional params; lines 164-169: public wrapper always passes `skip_cache=True`; credential guard remains unconditional before cache check |
| `src/reports/daily.py` | Per-source guarded fetch blocks; `capture_exception` at both per-source excepts; flags passed to builder | VERIFIED | Lines 143-163 (Meta block), 166-194 (GA4 block), 209-215 (builder call with both flags); `capture_exception` via inline import at lines 161, 192; outer `sentry_sdk.capture_exception` at line 260 |
| `src/reports/weekly.py` | Same per-source pattern as daily.py | VERIFIED | Lines 96-118 (Meta block), 121-143 (GA4 block), 158-163 (builder call with both flags); `ping_heartbeat` at line 200 inside outer try |
| `src/reports/builder.py` | `meta_available: bool = True` and `ga4_available: bool = True` in both builder functions | VERIFIED | `build_daily_report_html` lines 194-195; `build_weekly_report_html` lines 299-300; notices injected at correct positions in both Meta and GA4 sections |
| `src/alerts/engine.py` | `capture_exception` at `evaluate_alerts` outer except | VERIFIED | Line 269: `sentry_sdk.capture_exception(exc)` as first line of outer except block |
| `src/backfill.py` | argparse CLI with `--source/--start/--end/--dry-run`; `backfill_main` iterates dates | VERIFIED | Lines 27-53: `_parse_args`; lines 56-63: `_date_range` (inclusive, ISO output); lines 66-118: `backfill_main` with `dry_run` short-circuit before DB open; structured log events `backfill_date_start`, `backfill_date_current`, `backfill_complete` |
| `tests/test_sentry.py` | 4 tests covering init/no-init branching and capture paths | VERIFIED | 4 tests present: `test_sentry_init_called_when_dsn_set`, `test_sentry_init_not_called_without_dsn`, `test_capture_exception_called_on_meta_ingest_failure`, `test_no_raise_when_capture_exception_called_uninitialized` |
| `tests/test_graceful_degradation.py` | 7 tests covering builder notices and job independence | VERIFIED | 5 sync builder tests + 2 async independence tests; `meta_available=False` / `ga4_available=False` assertion checks present |
| `tests/test_backfill.py` | 8 tests covering date range, alert suppression, cache bypass, dry-run | VERIFIED | Tests: `test_date_range_inclusive`, `test_date_range_single_day`, `test_backfill_meta_calls_ingest_per_date`, `test_backfill_ga4_calls_ingest_per_date`, `test_backfill_all_calls_both_sources`, `test_dry_run_does_not_call_ingest`, `test_suppress_alerts_true_in_meta_wrapper`, `test_skip_cache_true_in_ga4_wrapper` |

---

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `src/config.py` | `src/main.py` | `settings.sentry_dsn` checked before `sentry_sdk.init()` | WIRED | Line 38: `if settings.sentry_dsn:` guard confirmed present |
| `src/main.py` | `sentry_sdk` | `sentry_sdk.init(dsn=settings.sentry_dsn.get_secret_value(), ...)` | WIRED | Lines 41-47: correct lazy import inside guard; `.get_secret_value()` used to extract DSN string |
| `src/meta/ingest.py` | `sentry_sdk` | `sentry_sdk.capture_exception(exc)` at outer except | WIRED | Line 157: first line of outer except block |
| `src/ga4/ingest.py` | `sentry_sdk` | `sentry_sdk.capture_exception(exc)` at outer except | WIRED | Line 139: first line of outer except block |
| `src/alerts/engine.py` | `sentry_sdk` | `sentry_sdk.capture_exception(exc)` at outer except | WIRED | Line 269: first line of outer except block |
| `src/reports/daily.py` | `src/reports/builder.py` | `build_daily_report_html(..., meta_available=meta_available, ga4_available=ga4_available)` | WIRED | Lines 209-215: flags passed through correctly |
| `src/reports/daily.py` | `ingestion_log` | Named-param query to distinguish failure from zero-spend | WIRED | Lines 153-159 (Meta) and 184-190 (GA4): `SELECT status FROM ingestion_log WHERE source = :source ...` |
| `src/reports/weekly.py` | `src/reports/builder.py` | `build_weekly_report_html(..., meta_available=meta_available, ga4_available=ga4_available)` | WIRED | Lines 158-163: flags passed through correctly |
| `src/backfill.py` | `src/meta/ingest.py` | `run_meta_ingest_for_date(db, settings, d)` which passes `suppress_alerts=True` | WIRED | Lines 98-101: lazy import + loop; `run_meta_ingest_for_date` wrapper confirmed to always pass `suppress_alerts=True` |
| `src/backfill.py` | `src/ga4/ingest.py` | `run_ga4_ingest_for_date(db, settings, d)` which passes `skip_cache=True` | WIRED | Lines 103-107: lazy import + loop; `run_ga4_ingest_for_date` wrapper confirmed to always pass `skip_cache=True` |
| `src/meta/ingest.py` | `src/alerts/engine.py` | `if not suppress_alerts: await evaluate_alerts(...)` | WIRED | Lines 153-154: guard present; backfill path never calls `evaluate_alerts` |

---

### Data-Flow Trace (Level 4)

Not applicable for this phase — all artifacts are operational plumbing (CLI, error-capture hooks, graceful-degradation guards), not data-rendering components. No new data queries were added that surface to user-visible output; existing query flows were refactored but not replaced.

---

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| `_date_range` returns inclusive ISO date list | `python -c "from src.backfill import _date_range; from datetime import date; r=_date_range(date(2026,5,1),date(2026,5,3)); assert r==['2026-05-01','2026-05-02','2026-05-03']"` | `OK 3-day range` | PASS |
| backfill CLI `--help` produces usage without error | `python -m src.backfill --help` | Shows `--source {meta,ga4,all}`, `--start`, `--end`, `--dry-run` | PASS |
| Meta ingest has `suppress_alerts` param | `python -c "import inspect; from src.meta.ingest import _run_meta_ingest, run_meta_ingest_for_date; sig=inspect.signature(_run_meta_ingest); assert 'suppress_alerts' in sig.parameters"` | `OK meta` | PASS |
| GA4 ingest has `skip_cache` param | `python -c "import inspect; from src.ga4.ingest import _run_ga4_ingest, run_ga4_ingest_for_date; sig=inspect.signature(_run_ga4_ingest); assert 'skip_cache' in sig.parameters"` | `OK ga4` | PASS |
| Sentry Settings defaults: `sentry_dsn=None`, `sentry_environment='production'` | `python -c "from src.config import Settings; s=Settings(telegram_bot_token='x'); assert s.sentry_dsn is None; assert s.sentry_environment == 'production'"` | `OK defaults` | PASS |
| Builder meta notice when `meta_available=False` | `python -c "from src.reports.builder import build_daily_report_html; r=build_daily_report_html([], None, '2026-05-18', meta_available=False); assert 'Meta Ads data unavailable' in r"` | `OK meta notice` | PASS |
| Builder GA4 notice when `ga4_available=False` | `python -c "from src.reports.builder import build_daily_report_html; r=build_daily_report_html([], None, '2026-05-18', ga4_available=False); assert 'GA4 data unavailable' in r"` | `OK ga4 notice` | PASS |

---

### Requirements Coverage

Phase 5 does not have v1 REQUIREMENTS.md IDs assigned (all 38 v1 requirements were mapped to Phases 1-4 and marked as shipped before Phase 5 work began). Phase 5 success criteria (SC-1, SC-2, SC-3) are operational hardening deliverables above the v1 requirement baseline. All three success criteria verified above.

---

### Anti-Patterns Found

No blockers or warnings. Full scan of all Phase 5 modified files:

| File | Pattern Checked | Finding |
|------|-----------------|---------|
| `src/backfill.py` | TODO/FIXME/placeholder comments | None found |
| `src/backfill.py` | Empty implementations, return `{}` / `[]` | `dry_run` returns early by design — this is correct, not a stub |
| `src/main.py` | `sentry_sdk.init` at module level | Not at module level — correctly inside `async main()` |
| `src/config.py` | `sentry_dsn` missing `SecretStr` type | Correctly typed as `SecretStr \| None` |
| `src/reports/daily.py` | `capture_exception` missing in per-source excepts | Present at both per-source excepts (inline import pattern) |
| `src/reports/weekly.py` | `ping_heartbeat` in `finally` block | Correctly inside outer `try` after Telegram send, not in `finally` |
| `src/meta/ingest.py` | `evaluate_alerts` called unconditionally | Correctly guarded by `if not suppress_alerts:` |
| `src/ga4/ingest.py` | Credential guard inside `if not skip_cache:` | Correctly remains unconditional before cache check |

One notable style point (not a blocker): in `daily.py` and `weekly.py`, the per-source `capture_exception` calls use an inline `import sentry_sdk;` on the same line as the call rather than a top-level module import. This is consistent with the plan's intent and works correctly because `sentry_sdk` is imported at module level in the outer try via the top-level `import sentry_sdk` at line 19 of `daily.py` — the inline imports in the per-source excepts are redundant but harmless.

---

### Human Verification Required

None. All success criteria are verifiable programmatically. The dead-man's-switch (external monitoring service like healthchecks.io) requires operator-side configuration of the external service period and grace window, but the code-side heartbeat ping is fully wired and verified.

---

## Gaps Summary

No gaps. All three success criteria are fully implemented and wired:

- **SC-1 (Backfill CLI):** `src/backfill.py` is a complete, invocable CLI. Date range iteration, alert suppression, cache bypass, dry-run, and structured logging are all present and tested by 8 passing unit tests.

- **SC-2 (Graceful Degradation):** Both `_run_daily_report` and `_run_weekly_report` have independent per-source guarded fetch blocks. The report builder's `meta_available` / `ga4_available` flags produce visible HTML notices. The `ingestion_log` distinction (failure vs. zero-spend) is implemented for both sources in both report jobs.

- **SC-3 (Sentry + Dead-Man's-Switch):** `sentry-sdk` declared as a dependency, `Settings` has DSN as `SecretStr`, `sentry_sdk.init()` is conditional and inside `async main()` with `AsyncioIntegration` and `send_default_pii=False`. All 5 original catch-and-suppress sites have `capture_exception`. The heartbeat ping (dead-man's-switch) was implemented in Phase 2 (REPORT-05) and remains correctly wired in both report jobs.

---

_Verified: 2026-05-19_
_Verifier: Claude (gsd-verifier)_
