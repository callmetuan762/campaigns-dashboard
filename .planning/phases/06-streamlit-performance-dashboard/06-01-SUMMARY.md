---
phase: 06-streamlit-performance-dashboard
plan: "01"
subsystem: dashboard-foundation
tags: [streamlit, plotly, sqlite3, WAL, dependencies, env]
dependency_graph:
  requires: []
  provides: [streamlit-deps, plotly-deps, dashboard-db-wal]
  affects: [pyproject.toml, .env.example, src/dashboard/db.py]
tech_stack:
  added: [streamlit>=1.35,<2, plotly>=5.20,<6]
  patterns: [contextmanager, PRAGMA journal_mode=WAL, PRAGMA busy_timeout=5000]
key_files:
  modified:
    - pyproject.toml
    - .env.example
    - src/dashboard/db.py
  created:
    - tests/test_dashboard_db_pragmas.py
decisions:
  - key: WAL mode set idempotently on every _conn() open
    rationale: Protects dashboard against fresh/empty DB not yet opened by bot writer; setting WAL again on already-WAL file is a no-op
  - key: busy_timeout=5000 mirrors src/db/client.py
    rationale: Dashboard reads block up to 5s instead of immediately raising "database is locked" during 02:00/03:00 ingest windows
  - key: streamlit + plotly as runtime (not dev) deps
    rationale: Dashboard is part of the production package (D-03 in 06-CONTEXT.md)
metrics:
  duration: 74s
  tasks_completed: 3
  files_changed: 4
  completed_date: "2026-05-24"
---

# Phase 6 Plan 01: Foundation (deps + db.py WAL fix) Summary

**One-liner:** Streamlit + Plotly runtime deps added; dashboard db.py _conn() fixed with WAL journal mode and 5s busy_timeout to prevent "database is locked" during bot ingest windows.

## What Was Done

### Task 1 — pyproject.toml: Add streamlit + plotly deps (commit 38cdec6)

Added two runtime dependencies to `[project].dependencies` after the existing `sentry-sdk` line:

```
"streamlit>=1.35,<2",
"plotly>=5.20,<6",
```

**Rationale:** These are runtime deps (Phase 6 overview page + charts), not dev extras. Upper bound `<2` / `<6` prevents unreviewed major bumps (threat T-06-03).

### Task 2 — .env.example: Document DASHBOARD_PASSWORD (commit 6273f51)

Added a new `Streamlit Dashboard (Phase 6 — optional)` section between the Dead-man's-switch block and the Application block:

```
DASHBOARD_PASSWORD=
```

With a comment warning not to leave blank in production. The actual password lives in the operator's real `.env` (gitignored). No other dashboard fields added — `DB_PATH`, `ANTHROPIC_API_KEY`, `ANTHROPIC_MONTHLY_BUDGET_USD` are already present and reused by `DashboardSettings`.

### Task 3 — src/dashboard/db.py: Fix _conn() with WAL + busy_timeout (commits 9d35bae + 4a08eea, TDD)

**RED:** Created `tests/test_dashboard_db_pragmas.py` with 4 tests; all failed (mode was `delete`, not `wal`).

**GREEN:** Modified `_conn()` to execute two PRAGMAs immediately after `sqlite3.connect()`:

```python
con.execute("PRAGMA journal_mode=WAL;")
con.execute("PRAGMA busy_timeout=5000;")
```

All 4 tests pass. No REFACTOR needed — diff is minimal and correct.

## Pitfall 5 Mitigation Confirmed

The plan's threat T-06-01 (DoS — "database is locked" during 02:00 Meta / 03:00 GA4 ingest) is **fully mitigated**:

- `PRAGMA journal_mode=WAL` — WAL mode allows concurrent readers while the bot writer holds a write lock. The dashboard can execute `SELECT` queries without contention.
- `PRAGMA busy_timeout=5000` — If the lock cannot be obtained immediately (e.g., bot is committing a transaction), SQLite waits up to 5 seconds before raising. This is the same value used in `src/db/client.py` (the async bot writer).
- The pragma is set idempotently on every connection open — no special handling needed if the DB was freshly created.

## Verification Results

1. `pytest tests/test_dashboard_db_pragmas.py -x` — **4/4 passed**
2. `python -c "from src.dashboard import db; print('ok')"` — **ok** (no import error)
3. `pyproject.toml` deps check — **['streamlit>=1.35,<2', 'plotly>=5.20,<6']**
4. `grep DASHBOARD_PASSWORD .env.example` — **1 line** (`DASHBOARD_PASSWORD=`)

## Deviations from Plan

None — plan executed exactly as written.

## Next Plan

**06-02: AI surface** — `src/dashboard/tools.py` (sync TOOLS schema + 5 tool implementations) and `src/dashboard/chat.py` (sync Anthropic tool-use loop). Builds directly on the WAL-safe `_conn()` fixed in this plan.

## Known Stubs

None.

## Threat Flags

No new threat surfaces introduced beyond those documented in the plan's threat model (T-06-01, T-06-02, T-06-03).

## Self-Check: PASSED

- `pyproject.toml` contains `streamlit>=1.35,<2` and `plotly>=5.20,<6`: FOUND
- `.env.example` contains `DASHBOARD_PASSWORD=` and `Streamlit Dashboard` banner: FOUND
- `src/dashboard/db.py` contains `PRAGMA journal_mode=WAL` and `PRAGMA busy_timeout=5000`: FOUND
- `tests/test_dashboard_db_pragmas.py` created: FOUND
- Commits 38cdec6, 6273f51, 9d35bae, 4a08eea: all present in git log
