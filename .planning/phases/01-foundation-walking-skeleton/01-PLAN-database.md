---
plan: "01-database"
phase: 1
wave: 1
depends_on: []
autonomous: true
files_modified:
  - src/db/schema.py
  - src/db/migrations.py
  - src/db/client.py
  - tests/__init__.py
  - tests/conftest.py
  - tests/test_upsert_idempotency.py
requirements_addressed:
  - INFRA-03
must_haves:
  truths:
    - "A SQLite database file is created on first connect at the configured path with WAL journal mode and foreign keys ON"
    - "The schema_version table tracks applied migrations; re-running migrate() on an already-migrated DB is a no-op (no duplicate version row)"
    - "ad_metrics has a composite primary key (campaign_id, date, ad_set_id, ad_id) — widened per RESEARCH Pattern 4 so Phase 2 META-03 (per-adset/per-ad granularity) does not require a costly SQLite table rebuild — with an ON CONFLICT DO UPDATE clause that makes re-inserting the same row update-in-place; proven by an automated test"
    - "ga4_metrics has a composite primary key (campaign_utm, date) and the same UPSERT semantics"
    - "Meta-side conversion columns use the meta_ prefix (meta_purchases_7dclick, meta_cost_per_purchase); GA4-side conversion columns use the ga4_ prefix (ga4_purchases_lastclick) — per CLAUDE.md data model rules"
    - "bot_conversations table exists for Phase 4 to persist per-chat-session multi-turn context"
    - "DBClient exposes async connect/close/execute/fetch_one/fetch_all/upsert_ad_metrics/upsert_ga4_metrics methods"
  artifacts:
    - path: "src/db/schema.py"
      provides: "All SQL DDL as named Python string constants + ALL_MIGRATIONS ordered list"
      exports: ["MIGRATION_001_INITIAL", "ALL_MIGRATIONS"]
    - path: "src/db/migrations.py"
      provides: "Async migration runner that reads schema_version and applies pending migrations"
      exports: ["run_migrations", "applied_versions"]
    - path: "src/db/client.py"
      provides: "DBClient class wrapping aiosqlite with UPSERT helpers"
      exports: ["DBClient"]
    - path: "tests/test_upsert_idempotency.py"
      provides: "Pytest proving the INFRA-03 acceptance criterion (re-runs do not duplicate rows)"
      contains: "test_ad_metrics_upsert_is_idempotent, test_ga4_metrics_upsert_is_idempotent, test_migration_is_idempotent"
  key_links:
    - from: "src/db/migrations.py:run_migrations"
      to: "src/db/schema.py:ALL_MIGRATIONS"
      via: "imports and iterates ALL_MIGRATIONS in declaration order"
      pattern: "from src.db.schema import|ALL_MIGRATIONS"
    - from: "src/db/client.py:DBClient.upsert_ad_metrics"
      to: "ad_metrics table"
      via: "INSERT ... ON CONFLICT(campaign_id, date, ad_set_id, ad_id) DO UPDATE SET"
      pattern: "ON CONFLICT.*campaign_id.*date.*ad_set_id.*ad_id.*DO UPDATE"
    - from: "tests/test_upsert_idempotency.py"
      to: "DBClient + migrations"
      via: "Connects to a temp file DB, runs migrations, inserts the same row twice, asserts COUNT(*) == 1"
      pattern: "tmp_path|aiosqlite\\.connect|COUNT\\(\\*\\)"
---

<objective>
Ship the canonical metrics storage layer: schema DDL, hand-rolled migration runner with `schema_version` tracking, and an async aiosqlite client with idempotent UPSERT helpers. Cover INFRA-03 ("SQLite database stores canonical metrics with idempotent UPSERT so re-runs never duplicate data") in full with an automated pytest proof.

Purpose: Every later phase (Meta ingestion, GA4 ingestion, conversation state) writes through this layer. Getting UPSERT semantics right at the SQL layer eliminates a whole class of race-condition bugs in Phase 2/3 ingestion.

Output: A SQLite DB that can be opened, migrated to the canonical Phase 1 schema, written to via typed UPSERT helpers, and verified idempotent by a passing test.
</objective>

<execution_context>
@$HOME/.claude/get-shit-done/workflows/execute-plan.md
@$HOME/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/ROADMAP.md
@.planning/STATE.md
@.planning/REQUIREMENTS.md
@.planning/phases/01-foundation-walking-skeleton/01-RESEARCH.md
@CLAUDE.md

<interfaces>
Downstream consumers (Plan 03 handlers `/status`, Plan 04 `main.py`, future Phase 2 ingest) will use this surface:

```python
# src/db/client.py — produced by this plan
class DBClient:
    def __init__(self, db_path: pathlib.Path) -> None: ...
    async def connect(self) -> None: ...
    async def close(self) -> None: ...
    async def execute(self, sql: str, params: dict | tuple | None = None) -> None: ...
    async def fetch_one(self, sql: str, params: dict | tuple | None = None) -> dict | None: ...
    async def fetch_all(self, sql: str, params: dict | tuple | None = None) -> list[dict]: ...
    async def upsert_ad_metrics(self, rows: list[dict]) -> int: ...
    async def upsert_ga4_metrics(self, rows: list[dict]) -> int: ...
    async def get_row_counts(self) -> dict[str, int]: ...   # used by /status in Plan 03
    async def get_last_sync(self) -> dict[str, str | None]: ...  # used by /status in Plan 03

# src/db/migrations.py
async def run_migrations(conn: aiosqlite.Connection) -> list[str]: ...   # returns applied version names
async def applied_versions(conn: aiosqlite.Connection) -> set[str]: ...

# src/db/schema.py
MIGRATION_001_INITIAL: str  # SQL DDL string
ALL_MIGRATIONS: list[tuple[str, str]]  # [(version_name, sql), ...]
```
</interfaces>
</context>

<tasks>

<task type="auto">
  <name>Task 1: Create schema DDL constants and migration runner</name>
  <files>src/db/schema.py, src/db/migrations.py</files>
  <read_first>
    - .planning/phases/01-foundation-walking-skeleton/01-RESEARCH.md (Pattern 4: aiosqlite + Hand-Rolled Migration Runner, lines 388–533, especially the initial schema SQL at lines 446–533)
    - CLAUDE.md (Data Model Rules section — `meta_` and `ga4_` prefix rules, side-by-side never-blended constraint, UTM exact-match join key)
    - src/db/__init__.py (verify package exists from Plan 01)
  </read_first>
  <action>
**Create `src/db/schema.py`** containing exactly two top-level definitions: a multi-line string constant `MIGRATION_001_INITIAL` and a list `ALL_MIGRATIONS`.

`MIGRATION_001_INITIAL` must contain these CREATE TABLE statements (use `CREATE TABLE IF NOT EXISTS` for every table):

1. **schema_version**
```sql
CREATE TABLE IF NOT EXISTS schema_version (
    version    TEXT PRIMARY KEY,
    applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

2. **campaigns** (Meta dimension table)
```sql
CREATE TABLE IF NOT EXISTS campaigns (
    id          TEXT PRIMARY KEY,
    source      TEXT NOT NULL,                 -- 'meta_ads' (room for 'google_ads' later)
    name        TEXT NOT NULL,
    status      TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
```

3. **ad_metrics** (Meta daily fact) — PK widened to support Phase 2 META-03 per-adset / per-ad granularity without a SQLite table rebuild. Phase 1 ingestion writes campaign-level rows with `ad_set_id=''` and `ad_id=''` sentinels (NOT NULL DEFAULT '' columns); Phase 2 will populate real IDs without a schema migration. See RESEARCH Pattern 4.
```sql
CREATE TABLE IF NOT EXISTS ad_metrics (
    campaign_id              TEXT NOT NULL,
    date                     TEXT NOT NULL,           -- ISO YYYY-MM-DD in ad-account tz
    ad_set_id                TEXT NOT NULL DEFAULT '', -- '' = campaign-level row (Phase 1); real ad-set id in Phase 2 META-03
    ad_id                    TEXT NOT NULL DEFAULT '', -- '' = campaign-level row (Phase 1); real ad id in Phase 2 META-03
    spend                    REAL,
    impressions              INTEGER,
    clicks                   INTEGER,
    ctr                      REAL,
    cpc                      REAL,
    cpm                      REAL,
    roas                     REAL,
    meta_purchases_7dclick   INTEGER,
    meta_cost_per_purchase   REAL,
    reach                    INTEGER,
    frequency                REAL,
    fetched_at               TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (campaign_id, date, ad_set_id, ad_id),
    FOREIGN KEY (campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_ad_metrics_date ON ad_metrics(date);
```

4. **ga4_metrics** (GA4 daily fact)
```sql
CREATE TABLE IF NOT EXISTS ga4_metrics (
    campaign_utm              TEXT NOT NULL,           -- utm_campaign value; exact-match join to campaigns.name
    date                      TEXT NOT NULL,
    sessions                  INTEGER,
    users                     INTEGER,
    new_users                 INTEGER,
    bounce_rate               REAL,
    avg_engagement_time       REAL,
    ga4_purchases_lastclick   INTEGER,
    fetched_at                TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (campaign_utm, date)
);
CREATE INDEX IF NOT EXISTS idx_ga4_metrics_date ON ga4_metrics(date);
CREATE INDEX IF NOT EXISTS idx_ga4_metrics_campaign ON ga4_metrics(campaign_utm);
```

5. **bot_conversations** (Phase 4 conversation persistence)
```sql
CREATE TABLE IF NOT EXISTS bot_conversations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id     INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    message     TEXT NOT NULL,
    role        TEXT NOT NULL CHECK (role IN ('user','assistant','system','tool')),
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_bot_conv_chat ON bot_conversations(chat_id, created_at DESC);
```

6. **ingestion_log** (operational; Phase 2 writes here)
```sql
CREATE TABLE IF NOT EXISTS ingestion_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source        TEXT NOT NULL,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    status        TEXT NOT NULL CHECK (status IN ('success','partial','failed','running')),
    rows_upserted INTEGER NOT NULL DEFAULT 0,
    error_message TEXT
);
CREATE INDEX IF NOT EXISTS idx_ingestion_log_source ON ingestion_log(source, started_at DESC);
```

After the CREATE statements:
```python
ALL_MIGRATIONS: list[tuple[str, str]] = [
    ("001_initial", MIGRATION_001_INITIAL),
]
```

**Create `src/db/migrations.py`** with this exact contract:

```python
"""Hand-rolled SQLite migration runner. No Alembic dependency.

INFRA-03: schema_version table tracks applied migrations; running run_migrations()
on an already-migrated DB is a no-op (idempotent).
"""
from __future__ import annotations

import aiosqlite
import structlog

from src.db.schema import ALL_MIGRATIONS

logger = structlog.get_logger(__name__)


async def applied_versions(conn: aiosqlite.Connection) -> set[str]:
    """Return the set of migration version names already applied to this DB."""
    # The schema_version table may not exist yet on a fresh DB.
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version ("
        "  version TEXT PRIMARY KEY,"
        "  applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP"
        ")"
    )
    await conn.commit()
    async with conn.execute("SELECT version FROM schema_version") as cur:
        return {row[0] async for row in cur}


async def run_migrations(conn: aiosqlite.Connection) -> list[str]:
    """Apply every migration in ALL_MIGRATIONS not yet recorded. Returns the list applied."""
    already = await applied_versions(conn)
    applied: list[str] = []
    for version, sql in ALL_MIGRATIONS:
        if version in already:
            logger.debug("migration_skip", version=version)
            continue
        logger.info("migration_apply", version=version)
        await conn.executescript(sql)
        await conn.execute(
            "INSERT OR REPLACE INTO schema_version (version) VALUES (?)",
            (version,),
        )
        await conn.commit()
        applied.append(version)
    return applied
```

Notes:
- The schema uses `(campaign_id, date, ad_set_id, ad_id)` as the PK for `ad_metrics` per RESEARCH Pattern 4. Phase 1 ingestion writes only campaign-level rows (ad_set_id/ad_id default to `''`). This avoids a costly SQLite table-rebuild migration in Phase 2 when META-03 introduces per-adset/per-ad granularity (SQLite cannot alter a primary key in place — the table would have to be dropped and re-created).
- `INSERT OR REPLACE INTO schema_version` ensures re-running is safe even if a prior partial run somehow recorded a version mid-failure.
- All conversion fields use the prefixes mandated by CLAUDE.md Data Model Rules.
- Do NOT use SQLAlchemy or any ORM — raw aiosqlite per project decision.
  </action>
  <verify>
    <automated>python -c "import asyncio, sys, tempfile, pathlib; sys.path.insert(0,'.'); import aiosqlite; from src.db.migrations import run_migrations, applied_versions; from src.db.schema import ALL_MIGRATIONS; assert ALL_MIGRATIONS and ALL_MIGRATIONS[0][0] == '001_initial'; 
async def test():
    with tempfile.TemporaryDirectory() as td:
        p = pathlib.Path(td)/'t.db'
        c = await aiosqlite.connect(p)
        a1 = await run_migrations(c)
        a2 = await run_migrations(c)
        assert a1 == ['001_initial'], a1
        assert a2 == [], 'second migrate must be no-op'
        async with c.execute('SELECT name FROM sqlite_master WHERE type=\"table\" ORDER BY name') as cur:
            tables = sorted([r[0] async for r in cur])
        await c.close()
        expected = ['ad_metrics','bot_conversations','campaigns','ga4_metrics','ingestion_log','schema_version']
        for t in expected: assert t in tables, f'missing {t}: {tables}'
        print('OK', tables)
asyncio.run(test())"</automated>
  </verify>
  <acceptance_criteria>
    - File `src/db/schema.py` exists; `grep -E '^MIGRATION_001_INITIAL\s*=' src/db/schema.py` matches once
    - `grep -E '^ALL_MIGRATIONS' src/db/schema.py` matches once
    - `grep -q "PRIMARY KEY.*campaign_id.*date.*ad_set_id.*ad_id" src/db/schema.py` matches (ad_metrics PK widened for META-03)
    - `grep -E "ad_set_id\s+TEXT NOT NULL DEFAULT" src/db/schema.py` matches (ad_set_id sentinel column)
    - `grep -E "ad_id\s+TEXT NOT NULL DEFAULT" src/db/schema.py` matches (ad_id sentinel column)
    - `grep -E 'PRIMARY KEY \(campaign_utm, date\)' src/db/schema.py` matches (ga4_metrics PK)
    - `grep -E 'meta_purchases_7dclick' src/db/schema.py` matches (CLAUDE.md prefix rule)
    - `grep -E 'ga4_purchases_lastclick' src/db/schema.py` matches (CLAUDE.md prefix rule)
    - `grep -E 'CREATE TABLE IF NOT EXISTS bot_conversations' src/db/schema.py` matches
    - File `src/db/migrations.py` exists; `grep -E 'async def run_migrations' src/db/migrations.py` matches once
    - `grep -E 'INSERT OR REPLACE INTO schema_version' src/db/migrations.py` matches
    - Automated verify command succeeds: tables ad_metrics, bot_conversations, campaigns, ga4_metrics, ingestion_log, schema_version all exist after migrate(); second run_migrations returns empty list (idempotent)
  </acceptance_criteria>
  <done>schema.py defines all six Phase 1 tables with correct prefixes and PKs; migrations.py applies them in order on a fresh DB, records each in schema_version, and is a no-op on re-run.</done>
</task>

<task type="auto">
  <name>Task 2: Build DBClient with UPSERT helpers and prove idempotency via pytest</name>
  <files>src/db/client.py, tests/__init__.py, tests/conftest.py, tests/test_upsert_idempotency.py</files>
  <read_first>
    - .planning/phases/01-foundation-walking-skeleton/01-RESEARCH.md (Pattern 4 UPSERT idempotency proof at lines 535–572, plus connect() pragmas at lines 409–417)
    - src/db/schema.py (just created — confirm column names for the UPSERT helpers)
    - src/db/migrations.py (just created — used by tests to set up the DB)
    - CLAUDE.md (Data Model Rules — confirm field prefixes match)
    - pyproject.toml (confirm pytest-asyncio is declared so async tests run)
  </read_first>
  <action>
**Create `src/db/client.py`** with the exact contract below:

```python
"""Async aiosqlite client wrapping migrations and UPSERT helpers.

INFRA-03: All writes go through UPSERT helpers using INSERT ... ON CONFLICT DO UPDATE,
making re-runs idempotent at the SQL layer (no Python-side read-modify-write).
"""
from __future__ import annotations

from pathlib import Path

import aiosqlite
import structlog

from src.db.migrations import run_migrations

logger = structlog.get_logger(__name__)


class DBClient:
    """Thin async wrapper over aiosqlite with typed UPSERT helpers."""

    def __init__(self, db_path: Path) -> None:
        self._path = db_path
        self._conn: aiosqlite.Connection | None = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("DBClient.connect() not called")
        return self._conn

    async def connect(self) -> None:
        """Open the connection, set PRAGMAs, and apply migrations."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL;")
        await self._conn.execute("PRAGMA foreign_keys=ON;")
        await self._conn.execute("PRAGMA busy_timeout=5000;")
        await self._conn.commit()
        applied = await run_migrations(self._conn)
        logger.info("db_connected", path=str(self._path), migrations_applied=applied)

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def execute(self, sql: str, params: dict | tuple | None = None) -> None:
        await self.conn.execute(sql, params or ())
        await self.conn.commit()

    async def fetch_one(self, sql: str, params: dict | tuple | None = None) -> dict | None:
        async with self.conn.execute(sql, params or ()) as cur:
            row = await cur.fetchone()
            return dict(row) if row is not None else None

    async def fetch_all(self, sql: str, params: dict | tuple | None = None) -> list[dict]:
        async with self.conn.execute(sql, params or ()) as cur:
            return [dict(r) async for r in cur]

    # ---- UPSERT helpers ----

    _UPSERT_AD_METRICS_SQL = """
        INSERT INTO ad_metrics (
            campaign_id, date, ad_set_id, ad_id, spend, impressions, clicks, ctr, cpc, cpm, roas,
            meta_purchases_7dclick, meta_cost_per_purchase, reach, frequency
        ) VALUES (
            :campaign_id, :date, :ad_set_id, :ad_id, :spend, :impressions, :clicks, :ctr, :cpc, :cpm, :roas,
            :meta_purchases_7dclick, :meta_cost_per_purchase, :reach, :frequency
        )
        ON CONFLICT(campaign_id, date, ad_set_id, ad_id) DO UPDATE SET
            spend                  = excluded.spend,
            impressions            = excluded.impressions,
            clicks                 = excluded.clicks,
            ctr                    = excluded.ctr,
            cpc                    = excluded.cpc,
            cpm                    = excluded.cpm,
            roas                   = excluded.roas,
            meta_purchases_7dclick = excluded.meta_purchases_7dclick,
            meta_cost_per_purchase = excluded.meta_cost_per_purchase,
            reach                  = excluded.reach,
            frequency              = excluded.frequency,
            fetched_at             = datetime('now');
    """

    _UPSERT_GA4_METRICS_SQL = """
        INSERT INTO ga4_metrics (
            campaign_utm, date, sessions, users, new_users, bounce_rate,
            avg_engagement_time, ga4_purchases_lastclick
        ) VALUES (
            :campaign_utm, :date, :sessions, :users, :new_users, :bounce_rate,
            :avg_engagement_time, :ga4_purchases_lastclick
        )
        ON CONFLICT(campaign_utm, date) DO UPDATE SET
            sessions                = excluded.sessions,
            users                   = excluded.users,
            new_users               = excluded.new_users,
            bounce_rate             = excluded.bounce_rate,
            avg_engagement_time     = excluded.avg_engagement_time,
            ga4_purchases_lastclick = excluded.ga4_purchases_lastclick,
            fetched_at              = datetime('now');
    """

    async def upsert_ad_metrics(self, rows: list[dict]) -> int:
        if not rows:
            return 0
        await self.conn.executemany(self._UPSERT_AD_METRICS_SQL, rows)
        await self.conn.commit()
        return len(rows)

    async def upsert_ga4_metrics(self, rows: list[dict]) -> int:
        if not rows:
            return 0
        await self.conn.executemany(self._UPSERT_GA4_METRICS_SQL, rows)
        await self.conn.commit()
        return len(rows)

    # ---- helpers used by /status handler (Plan 03) ----

    async def get_row_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for table in ("campaigns", "ad_metrics", "ga4_metrics", "bot_conversations"):
            row = await self.fetch_one(f"SELECT COUNT(*) AS n FROM {table}")
            counts[table] = int(row["n"]) if row else 0
        return counts

    async def get_last_sync(self) -> dict[str, str | None]:
        out: dict[str, str | None] = {}
        meta = await self.fetch_one("SELECT MAX(fetched_at) AS t FROM ad_metrics")
        ga4 = await self.fetch_one("SELECT MAX(fetched_at) AS t FROM ga4_metrics")
        out["meta_ads"] = meta["t"] if meta else None
        out["ga4"] = ga4["t"] if ga4 else None
        return out
```

**Create `tests/__init__.py`** with a single line: `"""Test package."""`.

**Create `tests/conftest.py`** with:

```python
"""Shared pytest fixtures."""
from __future__ import annotations

from pathlib import Path

import pytest_asyncio

from src.db.client import DBClient


@pytest_asyncio.fixture
async def db_client(tmp_path: Path):
    """Fresh DBClient backed by a temp SQLite file. Migrations applied on connect()."""
    client = DBClient(tmp_path / "test.db")
    await client.connect()
    # Seed a parent campaign so FK-constrained ad_metrics inserts can succeed.
    await client.execute(
        "INSERT INTO campaigns (id, source, name, status) VALUES (?, ?, ?, ?)",
        ("c_1", "meta_ads", "Test Campaign", "ACTIVE"),
    )
    try:
        yield client
    finally:
        await client.close()
```

**Create `tests/test_upsert_idempotency.py`** with these three tests:

```python
"""Prove INFRA-03: re-inserting the same row UPSERT-updates it rather than duplicating."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def test_migration_is_idempotent(db_client):
    # db_client fixture already migrated once on connect(). Run again via the underlying runner.
    from src.db.migrations import run_migrations
    second = await run_migrations(db_client.conn)
    assert second == [], f"second run_migrations must be a no-op, got {second}"


async def test_ad_metrics_upsert_is_idempotent(db_client):
    row = {
        "campaign_id": "c_1",
        "date": "2026-05-18",
        "ad_set_id": "",   # Phase 1: campaign-level sentinel (widened PK supports Phase 2 META-03)
        "ad_id": "",       # Phase 1: campaign-level sentinel
        "spend": 100.0,
        "impressions": 1000,
        "clicks": 50,
        "ctr": 0.05,
        "cpc": 2.0,
        "cpm": 100.0,
        "roas": 3.0,
        "meta_purchases_7dclick": 5,
        "meta_cost_per_purchase": 20.0,
        "reach": 800,
        "frequency": 1.25,
    }
    await db_client.upsert_ad_metrics([row])
    await db_client.upsert_ad_metrics([row])
    await db_client.upsert_ad_metrics([{**row, "spend": 150.0}])  # update spend
    res = await db_client.fetch_all(
        "SELECT * FROM ad_metrics WHERE campaign_id=? AND date=? AND ad_set_id=? AND ad_id=?",
        ("c_1", "2026-05-18", "", ""),
    )
    assert len(res) == 1, f"expected 1 row, got {len(res)}"
    assert res[0]["spend"] == 150.0, "UPSERT must update spend on conflict"


async def test_ga4_metrics_upsert_is_idempotent(db_client):
    row = {
        "campaign_utm": "summer_sale_2026",
        "date": "2026-05-18",
        "sessions": 500,
        "users": 400,
        "new_users": 100,
        "bounce_rate": 0.45,
        "avg_engagement_time": 75.5,
        "ga4_purchases_lastclick": 12,
    }
    await db_client.upsert_ga4_metrics([row])
    await db_client.upsert_ga4_metrics([row])
    await db_client.upsert_ga4_metrics([{**row, "sessions": 600}])
    res = await db_client.fetch_all(
        "SELECT * FROM ga4_metrics WHERE campaign_utm=? AND date=?",
        ("summer_sale_2026", "2026-05-18"),
    )
    assert len(res) == 1, f"expected 1 row, got {len(res)}"
    assert res[0]["sessions"] == 600, "UPSERT must update sessions on conflict"
```

Notes:
- Use named-parameter (`:foo`) SQL exclusively. Never string-format SQL with values — enforces the SQL-injection discipline mandated by RESEARCH.md "Known Threat Patterns" line 1066.
- `executemany` with a list of dicts is the idiomatic aiosqlite batch path.
- The `get_row_counts` and `get_last_sync` helpers exist specifically so Plan 03's `/status` handler can call them without writing more SQL.
- Tests use `tmp_path` fixture (built-in to pytest) so each test gets its own DB file with no shared state.
  </action>
  <verify>
    <automated>python -m pytest tests/test_upsert_idempotency.py -v -x --tb=short</automated>
  </verify>
  <acceptance_criteria>
    - File `src/db/client.py` exists with `class DBClient`
    - `grep -E 'class DBClient' src/db/client.py` matches once
    - `grep -E "ON CONFLICT\(campaign_id, date, ad_set_id, ad_id\) DO UPDATE" src/db/client.py` matches (ad_metrics UPSERT, widened PK)
    - `grep -E 'ON CONFLICT\(campaign_utm, date\) DO UPDATE' src/db/client.py` matches (ga4_metrics UPSERT)
    - `grep -E 'PRAGMA journal_mode=WAL' src/db/client.py` matches (WAL mode set)
    - `grep -E 'PRAGMA foreign_keys=ON' src/db/client.py` matches
    - `grep -E 'async def upsert_ad_metrics' src/db/client.py` matches
    - `grep -E 'async def upsert_ga4_metrics' src/db/client.py` matches
    - `grep -E 'async def get_row_counts' src/db/client.py` matches
    - `grep -E 'async def get_last_sync' src/db/client.py` matches
    - File `tests/test_upsert_idempotency.py` exists with three test functions: `test_migration_is_idempotent`, `test_ad_metrics_upsert_is_idempotent`, `test_ga4_metrics_upsert_is_idempotent`
    - `python -m pytest tests/test_upsert_idempotency.py -x` exits 0 — all three tests pass
    - The middle test proves that inserting the same `(campaign_id, date, ad_set_id, ad_id)` three times (with Phase 1 sentinels `ad_set_id="" ad_id=""`) leaves exactly 1 row in `ad_metrics` and the final `spend=150.0` overrode the initial `spend=100.0`
  </acceptance_criteria>
  <done>DBClient is the single async surface for all DB I/O in this project. UPSERT idempotency is proven by passing pytest. WAL + foreign keys + busy_timeout PRAGMAs are set on every connection. INFRA-03 is fully verified.</done>
</task>

</tasks>

<verification>
- `python -m pytest tests/test_upsert_idempotency.py -v` reports 3 passed
- `grep -r 'sqlalchemy' src/db/` returns no matches (no ORM creep)
- `grep -r 'meta_purchases_7dclick' src/db/` matches in both schema.py and client.py
- `grep -r 'ga4_purchases_lastclick' src/db/` matches in both schema.py and client.py
- `grep -rE 'f["\\\']INSERT|f["\\\']UPDATE|\\.format\\(' src/db/` returns no matches (no f-string SQL)
</verification>

<success_criteria>
INFRA-03 fully closed: SQLite file is auto-created on first connect, six Phase 1 tables exist with correct schemas and prefixes, UPSERT helpers use `ON CONFLICT ... DO UPDATE` so re-inserts update-in-place rather than duplicate, and the idempotency proof is a passing pytest. The DBClient surface is documented in `<interfaces>` so Plans 03 and 04 import without reading client.py.
</success_criteria>

<output>
After completion, create `.planning/phases/01-foundation-walking-skeleton/01-database-SUMMARY.md` describing:
- The six tables created and their PKs
- Where the `meta_` / `ga4_` prefix rule is enforced (schema.py)
- DBClient public method signatures (for Plan 03/04 reference)
- The exact pytest command that proves INFRA-03 idempotency
</output>
