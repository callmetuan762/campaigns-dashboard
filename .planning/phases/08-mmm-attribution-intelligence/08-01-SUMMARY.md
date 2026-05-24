---
phase: 08-mmm-attribution-intelligence
plan: 01
subsystem: mmm-data-layer
tags: [mmm, statsmodels, scipy, sqlite, migration, dashboard]
requirements: [MMM-01, DASH-12]
dependency-graph:
  requires:
    - src/db/client.py (DBClient base)
    - src/db/schema.py (ALL_MIGRATIONS pattern)
    - src/dashboard/db.py (_conn() pattern)
  provides:
    - src.mmm.model.fit_mmm
    - src.mmm.model.MMMResult
    - src.mmm.model.adstock
    - src.mmm.model.hill_saturation
    - src.db.client.DBClient.upsert_mmm_result
    - src.db.client.DBClient.get_mmm_results
    - src.dashboard.db.get_latest_mmm_result
    - src.dashboard.db.get_weekly_contributions
    - mmm_results SQLite table (MIGRATION_006_PHASE8)
  affects:
    - pyproject.toml (new runtime deps: statsmodels, scipy)
tech-stack:
  added:
    - statsmodels>=0.14 (OLS)
    - scipy>=1.13 (curve_fit)
  patterns:
    - "two-pass Hill+theta fit (init at theta=0 → grid search → refit)"
    - "named OLS params access (result.params['const']/'media'), never positional"
    - "media_pct clamp to [0, 1] when OLS intercept is negative"
    - "ROAS sanity cap at 100x — suppress to None and log"
    - "append-only mmm_results (no UPSERT) — dashboard reads latest via ORDER BY DESC LIMIT 1"
    - "OperationalError catch in dashboard reads — graceful empty-state on fresh DB"
key-files:
  created:
    - src/mmm/__init__.py
    - src/mmm/model.py
    - tests/test_mmm_model.py
    - tests/test_mmm_persistence.py
  modified:
    - pyproject.toml
    - src/db/schema.py
    - src/db/client.py
    - src/dashboard/db.py
decisions:
  - "MMMResult.to_dict() field order matches mmm_results column order — keeps INSERT param binding via named placeholders robust"
  - "fit_mmm is a pure function — no module globals, no I/O (RESEARCH Pitfall 4 mitigation)"
  - "Two-pass Hill fit with theta grid search converges reliably on <8 weeks data; single-pass diverged in early prototypes"
  - "Boundary check n ∈ (0.51, 2.99) — fits that hit curve_fit bounds treated as non-converged (RESEARCH Pitfall 6)"
  - "deposits_norm = deposits / deposits.max() — Hill output ∈ [0,1] means curve_fit's residuals are well-scaled for sparse-data convergence"
  - "ROAS dual meaning: deposits-per-\$1000 when deposit_value_usd=0; true dollar ROAS otherwise. Same field name, both <=100x or None"
  - "MIGRATION_006_PHASE8 numbered to skip 005 in this worktree (005 was a post-v1 fix not yet merged into the worktree base); the migration registry still tolerates the gap"
  - "get_weekly_contributions splits total deposits by stored media_pct ratio (not re-running fit_mmm) — keeps dashboard reads cheap and consistent with the most-recent fit"
metrics:
  duration_minutes: 15
  completed_date: "2026-05-24"
  tests_added: 32
  tasks_completed: 2
---

# Phase 8 Plan 01: MMM Data Layer Foundation Summary

Lightweight Marketing Mix Model (geometric adstock + Hill saturation + OLS decomposition) with SQLite persistence (`mmm_results` table) and dashboard read helpers — all the data-layer plumbing downstream plans need before they can wire the scheduler job and Attribution dashboard page.

## What Was Built

### Task 1 — MMM model package (commits 5d635ca + 73cb8c7)

- `src/mmm/__init__.py` — package marker
- `src/mmm/model.py` — `adstock()`, `hill_saturation()`, `MMMResult` dataclass, `fit_mmm()`
  - `adstock(spend, theta)`: sequential recursive decay (NOT cumsum approximation per RESEARCH anti-pattern)
  - `hill_saturation(x, km, n)`: standard Hill formula `x^n / (km^n + x^n)`
  - `MMMResult`: 10 fields matching D-12 mmm_results columns; `to_dict()` returns plain Python types ready for SQLite INSERT
  - `fit_mmm()`: 4 guard conditions (return None) + 3-pass fit (initial Hill → theta grid search → re-fit Hill on adstocked spend) + boundary check on `n` + OLS decomposition + optimal_daily_spend = km*4^(1/n) + ROAS sanity cap at 100x
- 19 unit tests in `tests/test_mmm_model.py` (MMM-01)

### Task 2 — Persistence layer (commits 29be9eb + ef23cd8)

- `src/db/schema.py` — `MIGRATION_006_PHASE8` adds `mmm_results` table with all D-12 columns + index; registered as last tuple in `ALL_MIGRATIONS`
- `src/db/client.py` — `DBClient.upsert_mmm_result(result: MMMResult)` (append-only INSERT, no ON CONFLICT) and `DBClient.get_mmm_results(limit=10)` (ORDER BY run_date DESC). `MMMResult` imported under `TYPE_CHECKING` to avoid circular dep.
- `src/dashboard/db.py` — sync helpers `get_latest_mmm_result(db_path)` (returns dict or None; catches `sqlite3.OperationalError` for fresh DB) and `get_weekly_contributions(db_path, weeks=12)` (aggregates ad_metrics by ISO week, splits total deposits via stored `media_pct` ratio, returns ASC by week)
- 13 tests in `tests/test_mmm_persistence.py` (DASH-12)

### Dependencies

- `pyproject.toml`: added `statsmodels>=0.14` and `scipy>=1.13` to `[project.dependencies]`
- Installed in venv (`pip install` ran during execution): `statsmodels-0.14.6` + `scipy-1.17.1`

## Key Numbers

- **Tasks completed:** 2 / 2
- **Commits:** 4 (2 RED + 2 GREEN per TDD)
- **New tests:** 32 (19 model + 13 persistence)
- **Files created:** 4 (`src/mmm/__init__.py`, `src/mmm/model.py`, 2 test files)
- **Files modified:** 4 (`pyproject.toml`, `src/db/schema.py`, `src/db/client.py`, `src/dashboard/db.py`)

## Deviations from Plan

- **[Plan adaptation] Migration 005 absent in this worktree base.** The plan instructed "add after `MIGRATION_005_FORM_SUBMIT`" but this worktree's base predates the 005 (`meta_form_submit_deposit` ALTER) merge. I added `MIGRATION_006_PHASE8` immediately after `MIGRATION_004_PHASE4`, keeping the registry name `"006_phase8"` so the migration ID stays stable across the parallel-wave merge. The dashboard `db.py` SQL in this worktree already references `meta_form_submit_deposit` (it was added in a prior phase via direct code change), so the runtime expectation is consistent. If the wave merger lands 005 from another branch, both 005 and 006 will apply on next migration run (their idempotent CREATE TABLE IF NOT EXISTS / ALTER TABLE statements are safe to interleave).
- No other deviations — plan executed exactly as written.

## Verification

Plan's verification block — all four imports green:

```
$ python -c "from src.mmm.model import fit_mmm, MMMResult, adstock, hill_saturation; print('mmm imports OK')"
mmm imports OK
$ python -c "from src.db.schema import ALL_MIGRATIONS; names = [m[0] for m in ALL_MIGRATIONS]; assert '006_phase8' in names; print('Migration 006 present')"
Migration 006 present
$ python -c "from src.dashboard.db import get_latest_mmm_result, get_weekly_contributions; print('dashboard db OK')"
dashboard db OK
$ python -c "import statsmodels; import scipy; print('deps OK')"
deps OK
```

Tests:
```
$ pytest tests/test_mmm_model.py tests/test_mmm_persistence.py tests/test_schema_migration.py -q
....................................                                     [100%]
36 passed in 1.33s
```

Full suite (excluding pre-existing failing files): 212 passed.

## Pre-existing Failures (Not In Scope)

Confirmed via `git stash`/`pop` baseline check — these failures existed before this plan's changes and are documented for the verifier:

- `tests/test_dashboard_charts.py` — 18 sqlite3 OperationalError failures (DB-open issue in dashboard tests under worktree)
- `tests/test_dashboard_auth.py` — 3 sqlite3 OperationalError failures (same root cause)
- `tests/test_dashboard_chat.py`, `tests/test_dashboard_app_smoke.py`, `tests/test_dashboard_isolation.py` — also affected by the same dashboard-test-fixture issue in worktree mode

These are dashboard-test infrastructure issues unrelated to MMM data layer work and out of scope per the executor's deviation rules (scope boundary).

## Threat Surface Check

Threat register from plan was fully mitigated:

| Threat ID | Mitigation Applied |
|-----------|---------------------|
| T-08-01-01 (SQL injection) | All SQL uses positional `?` or named `:param` placeholders; campaign-level filter `ad_set_id='' AND ad_id=''` hardcoded |
| T-08-01-02 (negative intercept / media_pct >1) | `media_pct = max(0.0, min(1.0, …))`; `mmm_negative_intercept` warning logged |
| T-08-01-03 (spoofed ROAS) | Cap at 100x → suppress to None and log `mmm_roas_sanity_cap` |
| T-08-01-04 (curve_fit DoS) | `maxfev=5000` hard cap; `RuntimeError`/`ValueError` caught → return None |

No new threat surface introduced beyond the plan's threat model.

## What Downstream Plans Now Have

- A pure-function `fit_mmm()` ready for `asyncio.to_thread(...)` wrapping in `src/mmm/scheduler.py` (plan 08-02)
- `DBClient.upsert_mmm_result()` for the scheduler to persist results
- `DBClient.get_mmm_results()` for historical-trend tools
- `get_latest_mmm_result(db_path)` for the Attribution dashboard KPI cards (plan 08-03)
- `get_weekly_contributions(db_path)` for the stacked-bar contribution chart (plan 08-03)
- `mmm_results` schema persisted on disk so any DB opened after this plan can read it

## Self-Check: PASSED

- src/mmm/__init__.py — FOUND
- src/mmm/model.py — FOUND
- tests/test_mmm_model.py — FOUND
- tests/test_mmm_persistence.py — FOUND
- pyproject.toml modified — FOUND (statsmodels + scipy)
- src/db/schema.py modified — FOUND (MIGRATION_006_PHASE8 + ALL_MIGRATIONS entry)
- src/db/client.py modified — FOUND (upsert_mmm_result + get_mmm_results)
- src/dashboard/db.py modified — FOUND (get_latest_mmm_result + get_weekly_contributions)
- Commit 5d635ca (test RED model) — FOUND
- Commit 73cb8c7 (feat GREEN model) — FOUND
- Commit 29be9eb (test RED persistence) — FOUND
- Commit ef23cd8 (feat GREEN persistence) — FOUND
