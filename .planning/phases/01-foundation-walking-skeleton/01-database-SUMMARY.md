---
phase: 1
plan: "01-database"
subsystem: "storage"
tags: ["sqlite", "aiosqlite", "schema", "migrations", "upsert", "infra"]
dependency_graph:
  requires: []
  provides: ["src.db.client.DBClient", "src.db.migrations.run_migrations", "src.db.schema.ALL_MIGRATIONS"]
  affects: ["01-scaffold", "01-bot", "Phase 2 Meta ingestion", "Phase 3 GA4 ingestion", "Phase 4 conversation state"]
tech_stack:
  added: ["aiosqlite ^0.22", "structlog ^25.5", "pytest-asyncio ^1.3"]
  patterns: ["hand-rolled migration runner", "INSERT ON CONFLICT DO UPDATE UPSERT", "WAL journal mode", "named-parameter SQL only"]
key_files:
  created:
    - src/db/schema.py
    - src/db/migrations.py
    - src/db/client.py
    - src/__init__.py
    - src/db/__init__.py
    - tests/__init__.py
    - tests/conftest.py
    - tests/test_upsert_idempotency.py
  modified: []
decisions:
  - "ad_metrics PK widened to (campaign_id, date, ad_set_id, ad_id) to avoid Phase 2 SQLite table-rebuild; Phase 1 uses '' sentinels"
  - "NOT NULL DEFAULT '' on ad_set_id/ad_id: NULL != NULL in SQLite composite PKs would break UPSERT determinism"
  - "INSERT OR REPLACE INTO schema_version: safe even if prior partial run recorded a version mid-failure"
  - "No ORM (SQLAlchemy/Alembic) per project architecture decision; raw aiosqlite ~40-line runner"
  - "Named-parameter SQL (:foo) exclusively — enforces SQL-injection discipline from CLAUDE.md"
metrics:
  duration: "~3 minutes"
  completed: "2026-05-19T06:53:42Z"
  tasks_completed: 2
  tasks_total: 2
  files_created: 8
  files_modified: 0
---

# Phase 1 Plan 01-database: SQLite Storage Layer Summary

SQLite schema DDL, hand-rolled idempotent migration runner, and async aiosqlite DBClient with named-parameter UPSERT helpers — all six Phase 1 tables created, INFRA-03 acceptance criterion closed by 3 passing pytests.

## What Was Built

### Six Phase 1 Tables (src/db/schema.py — MIGRATION_001_INITIAL)

| Table | Primary Key | Purpose |
|-------|-------------|---------|
| `schema_version` | `version` (TEXT) | Migration tracking; idempotent re-run guard |
| `campaigns` | `id` (TEXT) | Meta Ads campaign dimension |
| `ad_metrics` | `(campaign_id, date, ad_set_id, ad_id)` | Meta daily fact — widened PK for Phase 2 META-03 |
| `ga4_metrics` | `(campaign_utm, date)` | GA4 daily fact |
| `bot_conversations` | `id` (AUTOINCREMENT) | Phase 4 multi-turn conversation persistence |
| `ingestion_log` | `id` (AUTOINCREMENT) | Operational per-ingestion-run log |

### CLAUDE.md Prefix Rules Enforced in schema.py

- `meta_purchases_7dclick` (INTEGER) — Meta 7-day click-attribution purchases
- `meta_cost_per_purchase` (REAL) — Meta cost per purchase
- `ga4_purchases_lastclick` (INTEGER) — GA4 last-click attributed purchases

These prefixes are physically enforced in the schema DDL and UPSERT SQL; there is no path for conversion numbers to be blended across sources.

### ad_metrics Widened PK Design Decision

Phase 1 ingestion writes campaign-level rows using sentinel defaults `ad_set_id=''` and `ad_id=''` (`NOT NULL DEFAULT ''`). Phase 2 META-03 will populate real ad-set/ad IDs without a schema migration — SQLite cannot `ALTER TABLE ... ALTER PRIMARY KEY`, so a table-rebuild would otherwise be required. The `NOT NULL DEFAULT ''` sentinel (not `NULL`) is critical because `NULL != NULL` in SQLite composite PKs would make UPSERT non-deterministic.

## Migration Runner (src/db/migrations.py)

```
run_migrations(conn) -> list[str]
  1. Ensures schema_version table exists (CREATE TABLE IF NOT EXISTS)
  2. Reads already-applied version names
  3. For each (version, sql) in ALL_MIGRATIONS:
     - Skip if already in schema_version
     - conn.executescript(sql)
     - INSERT OR REPLACE INTO schema_version
  4. Returns list of newly-applied versions (empty = idempotent no-op)
```

Second call on an already-migrated DB returns `[]`. Proven by `test_migration_is_idempotent`.

## DBClient Public API (src/db/client.py)

```python
class DBClient:
    def __init__(self, db_path: pathlib.Path) -> None
    async def connect(self) -> None          # WAL + foreign_keys + busy_timeout + migrate
    async def close(self) -> None
    async def execute(self, sql: str, params: dict | tuple | None = None) -> None
    async def fetch_one(self, sql: str, params: dict | tuple | None = None) -> dict | None
    async def fetch_all(self, sql: str, params: dict | tuple | None = None) -> list[dict]
    async def upsert_ad_metrics(self, rows: list[dict]) -> int
    async def upsert_ga4_metrics(self, rows: list[dict]) -> int
    async def get_row_counts(self) -> dict[str, int]   # for Plan 03 /status
    async def get_last_sync(self) -> dict[str, str | None]  # for Plan 03 /status
```

PRAGMAs set on every connection: `journal_mode=WAL`, `foreign_keys=ON`, `busy_timeout=5000`.

## INFRA-03 Idempotency Proof

```
python -m pytest tests/test_upsert_idempotency.py -v
```

Three tests, all pass:

1. `test_migration_is_idempotent` — second `run_migrations()` call returns `[]`
2. `test_ad_metrics_upsert_is_idempotent` — 3 inserts (same PK, final spend=150.0) → 1 row, spend=150.0
3. `test_ga4_metrics_upsert_is_idempotent` — 3 inserts (same PK, final sessions=600) → 1 row, sessions=600

## Commits

| Task | Commit | Description |
|------|--------|-------------|
| Task 1: Schema + migrations | a3b23dc | feat(01-database): create schema DDL constants and migration runner |
| Task 2: DBClient + tests | 5e862d0 | feat(01-database): build DBClient with UPSERT helpers and prove idempotency via pytest |

## Deviations from Plan

None — plan executed exactly as written. The `src/__init__.py` package file was created as required by the dependency note (parallel scaffold agent not yet present in this worktree).

## Self-Check: PASSED

- [x] src/db/schema.py exists and exports MIGRATION_001_INITIAL + ALL_MIGRATIONS
- [x] src/db/migrations.py exists with run_migrations() + applied_versions()
- [x] src/db/client.py exists with DBClient class
- [x] tests/test_upsert_idempotency.py exists with all 3 test functions
- [x] Commit a3b23dc exists (Task 1)
- [x] Commit 5e862d0 exists (Task 2)
- [x] All 3 pytest tests pass: 3 passed in 0.17s
- [x] No sqlalchemy imports anywhere in src/db/
- [x] meta_purchases_7dclick + ga4_purchases_lastclick in both schema.py and client.py
- [x] No f-string SQL in src/db/
