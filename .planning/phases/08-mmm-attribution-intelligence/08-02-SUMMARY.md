---
phase: 08-mmm-attribution-intelligence
plan: 02
subsystem: mmm-scheduler
tags: [mmm, apscheduler, telegram, config, async]
requirements: [MMM-02, MMM-03]
dependency-graph:
  requires:
    - src/mmm/model.py (fit_mmm, MMMResult — delivered by 08-01)
    - src/db/client.py (DBClient.upsert_mmm_result — delivered by 08-01)
    - src/db/schema.py (mmm_results table — delivered by 08-01)
    - src/ga4/ingest.py (register_job_resources pattern reference)
  provides:
    - src.mmm.scheduler.register_job_resources
    - src.mmm.scheduler.run_mmm_weekly_job
    - src.mmm.scheduler.build_mmm_telegram_message
    - Settings.deposit_value_usd
    - DashboardSettings.deposit_value_usd
    - mmm_weekly APScheduler job (Sunday 23:00 in REPORT_TIMEZONE)
  affects:
    - src/main.py (new module import, register_job_resources call, scheduler.add_job)
    - .env.example (DEPOSIT_VALUE_USD documented)
tech-stack:
  added: []
  patterns:
    - "register_job_resources module-globals pattern (mirrors src/ga4/ingest.py)"
    - "asyncio.to_thread() wrapping for CPU-bound fit_mmm (Pattern 6)"
    - "Pure-function build_mmm_telegram_message — testable without bot/DB"
    - "Per-chat send_message try/except — Telegram failures logged, never re-raised"
    - "Plain-text Telegram message (no parse_mode) per D-08"
key-files:
  created:
    - src/mmm/scheduler.py
    - tests/test_mmm_scheduler.py
  modified:
    - src/config.py
    - src/dashboard/settings.py
    - src/main.py
    - .env.example
decisions:
  - "Used module-level SQL string constants (_WEEK_COUNT_SQL, _DATA_LOAD_SQL) — easier to grep/inspect than inline strings, mirrors the _RECENT_FAILURES_SQL convention in src/ga4/ingest.py."
  - "Week count cast to int via int(row['weeks']) with explicit None-guard — SQLite COUNT(DISTINCT) returns 0 for empty result, but row[0]['weeks'] may be None on PRAGMA edge cases."
  - "When data_rows is empty (e.g. weeks >= 4 but ad_metrics has no rows that satisfy the campaign-level filter), log mmm_job_skipped_no_data_rows and return rather than call fit_mmm with empty arrays. This is defensive — fit_mmm's len(spend) < 7 guard would also catch it but logging the skip reason at the scheduler layer is clearer."
  - "Pass deposit_value_usd as a positional arg to fit_mmm (not keyword) — matches the model.py signature order verified from 08-01-SUMMARY's MMMResult contract."
  - "Telegram per-chat send wrapped in try/except — one bad chat_id (e.g. user blocked bot) must not prevent delivery to others, and must not crash the job."
  - "Maturity-driven message branches: 'directional_only' shows ⚠ warning (4-7 weeks), 'early' shows light footnote (8-11 weeks), 'reliable' shows nothing (≥12 weeks). Matches D-06 exactly."
metrics:
  duration_minutes: 12
  completed_date: "2026-05-24"
  tests_added: 18
  tasks_completed: 2
---

# Phase 8 Plan 02: MMM Weekly Scheduler + Telegram Insight Summary

Async APScheduler job that runs the Phase 8 MMM model every Sunday at 23:00, persists the result to `mmm_results`, and posts a D-08-formatted Telegram message to every allowlisted chat. Adds `deposit_value_usd` to both `Settings` and `DashboardSettings` so the model can output either deposits-per-$1000 or true dollar ROAS depending on operator configuration.

## What Was Built

### Task 1 — `src/mmm/scheduler.py` + tests (commits c5bce4d RED + e46e90e GREEN)

- **`register_job_resources(bot, db, settings)`** — module-globals wiring identical in shape to `src/ga4/ingest.py`. Must be called before `scheduler.start()`. Resources never passed as job args (RESEARCH Pitfall 2 — `PicklingError` with `SQLAlchemyJobStore`).
- **`build_mmm_telegram_message(result, week_label, deposit_value_usd=0.0)`** — pure, testable function. Format per D-08 (Pattern 10):
  - Always: `📊 Weekly MMM Insight (week of {date})` header + media/baseline %
  - ROAS line is dual-mode:
    - `deposit_value_usd == 0.0`: `"Meta generated N.N deposits per $1000 spend."`
    - `deposit_value_usd > 0.0`: `"Incremental ROAS: N.Nx (every $1 of Meta spend generated $N.N in deposit value)."`
    - `result.incremental_roas_per_1k is None` (sanity cap >100x triggered): line omitted entirely.
  - Always: `"Optimal daily spend: ~$X — above this, returns diminish sharply."`
  - `maturity_label == "directional_only"` → trailing `⚠ Directional only — N weeks of data`
  - `maturity_label == "early"` → trailing `* Based on N weeks of data. Results strengthen at 3+ months.`
- **`async def run_mmm_weekly_job() -> None`** — zero-arg APScheduler job:
  1. Asserts `_bot`, `_db`, `_settings` registered (raises `RuntimeError` otherwise).
  2. Counts distinct calendar weeks with `spend > 0` at the campaign level (`ad_set_id='' AND ad_id=''`).
  3. **Skips silently** (no Telegram, no error) when `weeks < 4` per D-06.
  4. Loads daily series via `meta_form_submit_deposit` (NOT `meta_purchases_7dclick` — RESEARCH Pitfall 7).
  5. Wraps `fit_mmm` in `asyncio.to_thread` (Pattern 6 — non-blocking).
  6. Skips Telegram when `fit_mmm` returns `None`; still logs `mmm_fit_failed`.
  7. Persists via `_db.upsert_mmm_result(result)`.
  8. Builds plain-text message (no `parse_mode`).
  9. Sends to every chat_id in `_settings.telegram_allowed_chat_ids`; per-chat failures logged with `mmm_telegram_send_failed`, never re-raised.

**Tests:** 18 unit tests in `tests/test_mmm_scheduler.py` covering:
- 10 pure-function tests for `build_mmm_telegram_message` (D-08 formatting, dual ROAS modes, maturity branches, ROAS-None omission)
- 8 async integration tests for `run_mmm_weekly_job` (resource gate, week-count gate, fit-failure gate, `asyncio.to_thread` invocation, SQL uses `meta_form_submit_deposit`, multi-chat fan-out, send_message error swallowing)

### Task 2 — Config + main.py + .env.example (commit 34323f9)

- **`src/config.py`** — `Settings.deposit_value_usd: float = 0.0` inserted directly after `anthropic_monthly_budget_usd` with inline comment referencing D-09.
- **`src/dashboard/settings.py`** — `DashboardSettings.deposit_value_usd: float = 0.0` after `cpd_target` (Phase 8 dashboard page 08-03 will read it from this).
- **`src/main.py`** — three additive changes:
  1. `import src.mmm.scheduler as mmm_scheduler_module` alongside existing module imports.
  2. `mmm_scheduler_module.register_job_resources(bot, db, settings)` appended to the register block (after `weekly_report_module`).
  3. New `scheduler.add_job(run_mmm_weekly_job, CronTrigger(day_of_week='sun', hour=23, minute=0, timezone=settings.report_timezone), id='mmm_weekly', replace_existing=True, misfire_grace_time=600, coalesce=True, max_instances=1)` block after the `weekly_report` job.
- **`.env.example`** — new `# ── MMM (Phase 8) ──` section with `DEPOSIT_VALUE_USD=0.0` and D-09 explanatory comment placed between Anthropic and Sentry sections.

## Key Numbers

- **Tasks completed:** 2 / 2
- **Commits:** 3 (1 RED + 2 GREEN/feat)
- **New tests:** 18 (all passing on `tests/test_mmm_scheduler.py`)
- **Test suite:** 241 passed (excluding pre-existing failures in `test_chat_router.py::test_build_followup_keyboard_shape`, `test_meta_client.py::test_parse_row_all_keys_present`, and several `test_dashboard_*` files — all confirmed pre-existing in the worktree base and unrelated to MMM scheduler work)
- **Files created:** 2 (`src/mmm/scheduler.py`, `tests/test_mmm_scheduler.py`)
- **Files modified:** 4 (`src/config.py`, `src/dashboard/settings.py`, `src/main.py`, `.env.example`)

## Deviations from Plan

None — plan executed exactly as written. All `must_haves.truths` and `must_haves.artifacts` from the plan frontmatter were satisfied.

One **process note** (not a deviation from the code spec): the initial Write tool calls accidentally targeted the parent repo's absolute path because the worktree path wasn't being resolved by the editor. Detected on first `pytest` run (file not found), corrected by moving the test file into the worktree before commit; no spurious commits to the parent repo occurred (the file was only created on disk, never staged in the parent). All four subsequent file writes/edits used the explicit worktree path.

## Verification

Plan's verification block — all four assertions green:

```
$ python -c "from src.config import Settings; import inspect; assert 'deposit_value_usd' in inspect.getsource(Settings); print('Settings.deposit_value_usd OK')"
Settings.deposit_value_usd OK

$ python -c "from src.dashboard.settings import DashboardSettings; import inspect; assert 'deposit_value_usd' in inspect.getsource(DashboardSettings); print('DashboardSettings.deposit_value_usd OK')"
DashboardSettings.deposit_value_usd OK

$ python -c "from src.mmm.scheduler import register_job_resources, run_mmm_weekly_job, build_mmm_telegram_message; print('scheduler.py imports OK')"
scheduler.py imports OK

$ python -c "txt=open('src/main.py').read(); assert 'mmm_weekly' in txt and 'mmm_scheduler_module' in txt; print('main.py wiring OK')"
main.py wiring OK

$ python -c "env=open('.env.example').read(); assert 'DEPOSIT_VALUE_USD' in env; print('.env.example OK')"
.env.example OK
```

Tests:
```
$ pytest tests/test_mmm_scheduler.py -q
..................                                                       [100%]
18 passed in 0.95s
```

## Pre-existing Failures (Not In Scope)

Confirmed unrelated to this plan's changes — these existed in the worktree base:

- `tests/test_ai_chat.py` — `ImportError: cannot import name '_SYSTEM_PROMPT_TEMPLATE'` from `src.ai.chat` (worktree-base inconsistency between test and prod code)
- `tests/test_chat_router.py::test_build_followup_keyboard_shape` — assertion mismatch on keyboard shape
- `tests/test_meta_client.py::test_parse_row_all_keys_present` — test expects `meta_form_submit_deposit` key in parser output; parser in worktree base doesn't emit it
- `tests/test_dashboard_*.py` (5 files) — `sqlite3.OperationalError` infrastructure issue documented in 08-01-SUMMARY

All are out of scope per the executor's scope boundary (Rules 1-3 only apply to issues caused by current task's changes).

## Threat Surface Check

Plan threat register fully mitigated:

| Threat ID | Mitigation Applied |
|-----------|---------------------|
| T-08-02-01 (Tampering — SQL injection in run_mmm_weekly_job) | Both `_WEEK_COUNT_SQL` and `_DATA_LOAD_SQL` are module-level string literals with hardcoded `ad_set_id='' AND ad_id=''` filter. No user-supplied input enters the query (no params at all). |
| T-08-02-02 (Information Disclosure — ROAS > 100x in Telegram) | Two-layer defense: `fit_mmm()` suppresses ROAS > 100 → returns `incremental_roas_per_1k = None`; `build_mmm_telegram_message` omits the ROAS line entirely when `None` (verified by `test_telegram_message_omits_roas_line_when_roas_is_none`). |
| T-08-02-03 (Spoofing — deposit_value_usd misconfig) | Accepted per plan; relies on operator and `fit_mmm`'s 100x sanity cap. No code change needed. |
| T-08-02-04 (DoS — event loop blocked by fit_mmm) | `asyncio.to_thread(fit_mmm, ...)` wraps the entire fit call (verified by `test_run_mmm_weekly_job_uses_asyncio_to_thread_for_fit_mmm`). |

No new threat surface introduced beyond the plan's threat model.

## What Downstream Plans Now Have

- **Phase 8 Plan 03 (Attribution dashboard)** can read `DashboardSettings.deposit_value_usd` from env to render the saturation-curve KPI cards with the correct units.
- A live scheduled job: every Sunday 23:00 in `REPORT_TIMEZONE`, the bot will post the weekly MMM insight to all allowlisted chats. Once Plan 08-01's `mmm_results` table has its first row (after this job runs Sunday), the Attribution dashboard's empty state will resolve.
- The exact Telegram format the dashboard page should mirror for consistent operator experience: `build_mmm_telegram_message` is the canonical reference.

## Self-Check: PASSED

- src/mmm/scheduler.py — FOUND
- tests/test_mmm_scheduler.py — FOUND
- src/config.py modified (Settings.deposit_value_usd) — FOUND
- src/dashboard/settings.py modified (DashboardSettings.deposit_value_usd) — FOUND
- src/main.py modified (mmm_scheduler_module import + register + add_job) — FOUND
- .env.example modified (DEPOSIT_VALUE_USD section) — FOUND
- Commit c5bce4d (test RED scheduler) — FOUND
- Commit e46e90e (feat GREEN scheduler) — FOUND
- Commit 34323f9 (feat Task 2 — config + main.py + .env.example wiring) — FOUND
