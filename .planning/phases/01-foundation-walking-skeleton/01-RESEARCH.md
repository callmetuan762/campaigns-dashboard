# Phase 1: Foundation & Walking Skeleton - Research

**Researched:** 2026-05-19
**Domain:** Python async application scaffolding (aiogram 3 + aiosqlite + APScheduler + pydantic-settings + Docker + structlog)
**Confidence:** HIGH

## Summary

Phase 1 builds a single deployable Docker container that bootstraps the entire downstream pipeline: configuration, persistent storage with idempotent UPSERT, an allowlisted Telegram bot, the scheduler shell, and structured logging. The stack is already locked by `CLAUDE.md` and project-level research — no alternatives to evaluate. The work in this phase is almost entirely **wiring discipline**: getting middleware order right (allowlist BEFORE handlers), getting lifecycle order right (config → DB schema → bot → scheduler → polling), and getting Docker right (uv multi-stage, non-root user, persistent SQLite volume).

Two architectural decisions deserve explicit Phase 1 callouts:

1. **Long-polling in Phase 1** (deferred webhook to Phase 5 if needed). Long-polling is simpler, has no public-URL/TLS dependency, and aiogram's `Dispatcher.start_polling()` is the canonical pattern. The 409 conflict risk only matters if multiple bot instances poll simultaneously; a single-container deploy is safe.
2. **Hand-rolled SQL migrations** via a `schema/` directory of versioned `.sql` files and a `schema_version` table — no Alembic. Alembic is overkill for SQLite + a small known schema, and adds a dependency, a CLI, and a config file with no proportional benefit at this scale. The migration runner is ~30 lines of Python.

**Primary recommendation:** Adopt the canonical lifecycle template — `async def main()` instantiates `Settings`, opens an `aiosqlite` pool, runs `migrate()`, builds `Bot` + `Dispatcher` with the `AllowlistMiddleware` registered on `dispatcher.message.middleware` and `dispatcher.callback_query.middleware`, starts `AsyncIOScheduler` sharing the running event loop, then calls `await dp.start_polling(bot)`. Wrap the whole thing in `asyncio.run(main())` from a `__main__.py` entrypoint so `python -m app` and Docker `CMD ["python","-m","app"]` both work.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Secrets / config loading (INFRA-01) | Process boot | Container env | Twelve-factor: env vars injected by Docker → pydantic-settings validates at boot |
| Allowlist enforcement (INFRA-02) | Bot middleware | — | Must execute BEFORE any handler / Claude call; aiogram middleware is the only correct insertion point |
| Canonical metrics persistence (INFRA-03) | Storage layer (aiosqlite) | Schema migrations | UPSERT semantics enforced by `ON CONFLICT` clauses in schema, not application code |
| Container packaging (INFRA-04) | Build/runtime (Docker) | uv build stage | Multi-stage: builder produces `.venv`, runtime copies `.venv` + source, runs as non-root |
| Structured logging (INFRA-05) | Cross-cutting (structlog) | All other tiers | Initialized once at boot before any other component logs; processors strip secrets/PII |
| Scheduler lifecycle | Application core | asyncio event loop | `AsyncIOScheduler` shares the same loop as aiogram polling — single-process concurrency |

## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| INFRA-01 | API keys, Telegram token, account IDs, timezone stored via env-based secrets | pydantic-settings `BaseSettings` + `SecretStr` + `.env` (dev) / env vars (prod) — see Standard Stack and Code Examples |
| INFRA-02 | Telegram bot enforces strict chat-ID + user-ID allowlist before any command/Claude call | aiogram 3 `BaseMiddleware` registered on `dp.message.middleware` + `dp.callback_query.middleware`; returns `None` (drops event) for non-allowlisted senders — see Pattern 2 |
| INFRA-03 | SQLite stores canonical metrics with idempotent UPSERT (re-runs never duplicate) | `aiosqlite` + `INSERT ... ON CONFLICT(...) DO UPDATE SET ...`; schema bootstrap via hand-rolled migration runner with `schema_version` table — see Pattern 4 |
| INFRA-04 | Single Docker container deployable to VPS / Railway / Fly.io | uv multi-stage Dockerfile (builder → runtime), non-root user, named volume for `/data/metrics.db`, HEALTHCHECK probing a `/healthz`-equivalent or DB ping — see Pattern 6 |
| INFRA-05 | Structured logs capture API outcomes, delivery status, errors — never PII or raw ad data | structlog with JSON renderer, redaction processor for known sensitive keys (token, secret, access_token, body), field-allowlist discipline at log call sites — see Pattern 7 |

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| python | 3.12 | Language runtime | [CITED: CLAUDE.md, STACK.md] Async-mature, locked by project |
| uv | latest | Package & venv manager | [CITED: STACK.md] De-facto 2026 standard; reproducible, fast; replaces pip+venv+pip-tools |
| aiogram | ^3.x (3.28+) | Telegram bot framework | [CITED: STACK.md, CLAUDE.md] Async-native, modern, idiomatic for long Claude/API calls |
| aiosqlite | ^0.x | Async SQLite driver | [CITED: CLAUDE.md] Skip SQLAlchemy for v1 — schema is small, hand-rolled SQL is clearer |
| apscheduler | ^3.10 | In-process job scheduling | [CITED: STACK.md] AsyncIOScheduler shares event loop with aiogram; Celery is overkill |
| pydantic-settings | ^2.x | Typed config from env / .env | [CITED: STACK.md] Industry standard; `SecretStr` for tokens; fails fast on missing config |
| structlog | ^24+ | Structured JSON logging | [CITED: STACK.md] Survives Docker stdout/stderr; easy to grep, easy to ship to a log aggregator later |
| tenacity | ^9.x | Retry decorators | [CITED: STACK.md, CLAUDE.md] Exponential backoff for Phase 2 API clients; Phase 1 dependency only |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| python-dotenv | latest | `.env` file loading | Pulled in transitively by pydantic-settings; only needed if you want `.env.dev`-style overrides |
| pytest, pytest-asyncio | latest | Tests | Dev dependency; Phase 1 should ship at least allowlist + UPSERT idempotency tests |
| ruff | latest | Linter + formatter | Dev dependency; configure in `pyproject.toml` |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| aiosqlite (raw SQL) | SQLAlchemy 2.x async + Alembic | Adds dependency weight + Alembic ops for a single-table-per-source schema — not justified at v1 |
| pydantic-settings | dynaconf / hydra / custom env loader | pydantic-settings is lighter and you already need pydantic for Claude tool schemas (Phase 4) |
| structlog | stdlib logging + JSON formatter | Workable but processor chain in structlog makes redaction + context binding much cleaner |
| Long polling (Phase 1) | Webhooks (FastAPI + HTTPS) | Webhooks need public URL, TLS, secret-token verification, idempotency — defer until needed |
| Hand-rolled migration runner | Alembic | Alembic earns its weight on multi-table production schemas with branching history; not here |

**Installation:**

```bash
# from a clean directory
uv init ads-reporting && cd ads-reporting
uv python pin 3.12

# Phase 1 runtime
uv add "aiogram>=3.28"
uv add aiosqlite
uv add "apscheduler>=3.10,<4"
uv add "pydantic>=2" "pydantic-settings>=2"
uv add structlog
uv add tenacity
uv add "tzdata"   # required on Windows / slim Linux containers for zoneinfo

# dev
uv add --dev pytest pytest-asyncio ruff mypy
```

**Version verification:** All versions cross-referenced against `.planning/research/STACK.md` (verified May 2026 against PyPI). Confirm at install time with `uv pip list` after `uv sync`. If the planner wants harder verification, run `npm view`-equivalents (`uv pip index versions <package>` or `pip index versions <package>`) before committing the lockfile. [VERIFIED: STACK.md sources cite PyPI + official GitHub repos]

## Architecture Patterns

### System Architecture Diagram

```
                                  +-------------------+
                                  | Telegram servers  |
                                  +---------+---------+
                                            |
                          (long-poll: getUpdates loop)
                                            |
                                            v
+---------------------+         +------------------------+        +----------------------+
| Operator env vars   |  boot   | async def main()       |  ev    | AllowlistMiddleware  |
| (Docker / .env)     +-------->+  - load Settings       +------->+ (drops non-allowed)  |
+---------------------+         |  - configure structlog |        +----------+-----------+
                                |  - open aiosqlite pool |                   | (allowed updates)
+---------------------+         |  - run migrate()       |                   v
| schema/*.sql files  |  load   |  - build Bot + Dispatch|        +----------------------+
| schema_version tbl  +-------->+  - register middleware |        | Handlers             |
+---------------------+         |  - start AsyncIOSched  |        |  /start, /ping       |
                                |  - dp.start_polling()  |        |  (Phase 2: /report)  |
                                +-----+-------------+----+        +----------+-----------+
                                      |             |                        |
                       schedules jobs |             | sends responses        |
                                      v             v                        v
                                +-----------+   +-----------+        +----------------+
                                |APScheduler|   |  Bot      |        | Storage layer  |
                                |(AsyncIO)  |   |  (aiogram)|<-------+  aiosqlite     |
                                +-----+-----+   +-----+-----+ writes |  UPSERT only   |
                                      |               ^              |  via storage.py|
                          tick / cron |               | reads (P2+)  +----------------+
                                      v               |
                                +-----------------+   |
                                | (Phase 2)       |---+
                                | ingestion jobs  |
                                +-----------------+

structlog (JSON to stdout) is invoked by every component above — boot, middleware, handlers,
storage, scheduler. Redaction processor strips token/secret/PII keys from event_dict
before serialization.
```

Data flow during Phase 1 (no external APIs yet):
- Telegram update arrives → middleware checks allowlist → either drops (logged at INFO) or passes → handler reads/writes SQLite via storage layer → response sent via `bot.send_message`.
- APScheduler tick fires a placeholder job (e.g., `heartbeat_log`) → writes a row to `ingestion_log` → logs via structlog.

### Recommended Project Structure

```
ads-reporting/
├── pyproject.toml          # uv-managed; python = "^3.12"
├── uv.lock
├── .env.example            # committed template; lists every required key
├── .gitignore              # .env, data/, *.db, __pycache__/
├── Dockerfile              # multi-stage uv build (see Pattern 6)
├── .dockerignore           # exclude .venv, data/, .git, tests
├── README.md
├── app/
│   ├── __init__.py
│   ├── __main__.py         # `python -m app` entrypoint -> asyncio.run(main())
│   ├── main.py             # async def main() lifecycle wiring
│   ├── config.py           # Settings(BaseSettings) — pydantic-settings
│   ├── logging_setup.py    # structlog configure_once()
│   ├── bot/
│   │   ├── __init__.py
│   │   ├── middleware.py   # AllowlistMiddleware
│   │   └── handlers.py     # /start, /ping (Phase 1); commands grow in later phases
│   ├── storage/
│   │   ├── __init__.py
│   │   ├── db.py           # aiosqlite connection + migrate()
│   │   └── repository.py   # UPSERT helpers (per-source repositories grow in P2-P3)
│   ├── scheduler/
│   │   ├── __init__.py
│   │   └── jobs.py         # build_scheduler(settings, deps) -> AsyncIOScheduler
│   └── schema/
│       ├── 001_initial.sql # schema_version + ingestion_log + report_runs + canonical fact stubs
│       └── 002_*.sql       # future migrations
├── tests/
│   ├── conftest.py
│   ├── test_allowlist.py
│   ├── test_upsert_idempotency.py
│   └── test_config_loads.py
└── data/                   # NOT committed; mounted as Docker volume; holds metrics.db
```

Rationale: package name `app` is short and avoids colliding with PyPI names. The schema/ directory at the package root makes migrations discoverable both at runtime (importlib.resources) and in code review.

### Pattern 1: Pydantic-Settings Multi-Source Config (INFRA-01)

**What:** Single `Settings` object loaded once at boot. Reads env vars first, falls back to `.env` file. All credentials wrapped in `SecretStr` so accidental logging shows `**********`.

**When to use:** Always — replace any `os.environ.get(...)` scattered code.

**Example:**

```python
# app/config.py
# Source pattern: pydantic-settings official docs (CITED: STACK.md sources)
from pydantic import SecretStr, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path


class Settings(BaseSettings):
    # ---- Telegram ----
    telegram_bot_token: SecretStr
    telegram_group_chat_id: int                    # primary group for reports
    telegram_admin_chat_id: int | None = None      # fallback / error channel
    allowed_chat_ids: list[int] = Field(default_factory=list)
    allowed_user_ids: list[int] = Field(default_factory=list)

    # ---- External APIs (Phase 2+, declared now to fail fast) ----
    meta_access_token: SecretStr | None = None
    meta_ad_account_id: str | None = None
    ga4_property_id: str | None = None
    google_credentials_path: Path | None = None
    anthropic_api_key: SecretStr | None = None

    # ---- App config ----
    database_path: Path = Path("/data/metrics.db")
    timezone: str = "UTC"                           # canonical project tz; ad-acct tz handled per source
    log_level: str = "INFO"
    log_format: str = "json"                        # "json" | "console"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @field_validator("allowed_chat_ids", "allowed_user_ids", mode="before")
    @classmethod
    def _split_csv(cls, v):
        if isinstance(v, str):
            return [int(x.strip()) for x in v.split(",") if x.strip()]
        return v


def load_settings() -> Settings:
    """Call once at boot. Raises ValidationError immediately on missing required config."""
    return Settings()  # type: ignore[call-arg]
```

**`.env.example` (committed):**

```
TELEGRAM_BOT_TOKEN=changeme
TELEGRAM_GROUP_CHAT_ID=-1001234567890
TELEGRAM_ADMIN_CHAT_ID=123456789
ALLOWED_CHAT_IDS=-1001234567890
ALLOWED_USER_IDS=123456789,987654321
DATABASE_PATH=/data/metrics.db
TIMEZONE=America/New_York
LOG_LEVEL=INFO
LOG_FORMAT=json
# Phase 2+:
META_ACCESS_TOKEN=
META_AD_ACCOUNT_ID=
GA4_PROPERTY_ID=
GOOGLE_CREDENTIALS_PATH=/secrets/ga4.json
ANTHROPIC_API_KEY=
```

**Notes:**
- `allowed_chat_ids` and `allowed_user_ids` use CSV-from-env via the `before` validator — pydantic-settings v2 has native list parsing only for JSON syntax; CSV is friendlier in `.env`. [VERIFIED: pydantic-settings v2 docs]
- Use `secret.get_secret_value()` only at the point of use (e.g., when constructing the `Bot(token=...)`); never log the result.

### Pattern 2: aiogram 3 Allowlist Middleware (INFRA-02) — CRITICAL SECURITY

**What:** A `BaseMiddleware` subclass registered on `dispatcher.message.middleware` and `dispatcher.callback_query.middleware`. It inspects `event.chat.id` and `event.from_user.id`; if either is on the allowlist, it calls `await handler(event, data)`. Otherwise it logs and returns `None`, which silently drops the event before any handler runs.

**When to use:** Always — this is the project's single most important security control. The allowlist check MUST execute before any handler, and especially before any future Claude call.

**Example:**

```python
# app/bot/middleware.py
# Source pattern: aiogram 3 middleware docs (CITED: docs.aiogram.dev)
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message, CallbackQuery
import structlog

logger = structlog.get_logger(__name__)


class AllowlistMiddleware(BaseMiddleware):
    """Reject any update whose chat or user is not on the allowlist.

    Drops occur BEFORE the handler chain — i.e., before any Claude call, DB write,
    or response is sent. This is the primary defense for INFRA-02 / CHAT-06 cost runaway.
    """

    def __init__(self, allowed_chat_ids: set[int], allowed_user_ids: set[int]) -> None:
        self._chats = allowed_chat_ids
        self._users = allowed_user_ids

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        chat_id, user_id = self._extract_ids(event)

        if chat_id in self._chats or user_id in self._users:
            return await handler(event, data)

        # Silent drop — log at INFO without echoing message content to avoid log poisoning
        logger.info(
            "rejected_update",
            chat_id=chat_id,
            user_id=user_id,
            event_type=type(event).__name__,
        )
        return None

    @staticmethod
    def _extract_ids(event: TelegramObject) -> tuple[int | None, int | None]:
        if isinstance(event, Message):
            return event.chat.id, (event.from_user.id if event.from_user else None)
        if isinstance(event, CallbackQuery):
            return (
                event.message.chat.id if event.message else None,
                event.from_user.id if event.from_user else None,
            )
        return None, None
```

**Registration (in `main.py`):**

```python
allowlist = AllowlistMiddleware(
    allowed_chat_ids=set(settings.allowed_chat_ids),
    allowed_user_ids=set(settings.allowed_user_ids),
)
dp.message.middleware(allowlist)
dp.callback_query.middleware(allowlist)
# When other update types are added (inline_query, my_chat_member, etc.), register on each.
```

**Critical:** Two semantic choices to pin in the plan:
1. **OR semantics** (chat OR user allowed) means a non-allowlisted user in an allowlisted group is allowed. This matches the project intent (the team group is the trust boundary). If stricter (AND) is wanted, both must match — call this out in the planner.
2. **Silent drop vs reply:** Drop silently per `.planning/research/PITFALLS.md` recommendation — replying confirms the bot's existence to drive-by users and helps username probes.

### Pattern 3: aiogram 3 Long-Polling Bot Setup

**What:** Build `Bot` and `Dispatcher`, register routers, attach middleware, start polling. aiogram's `dispatcher.start_polling(bot)` handles update loops, retries, graceful shutdown on SIGINT/SIGTERM.

```python
# app/bot/__init__.py
# Source pattern: aiogram 3 quickstart (CITED: docs.aiogram.dev)
from aiogram import Bot, Dispatcher, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.types import Message


def build_router() -> Router:
    router = Router(name="phase1")

    @router.message(CommandStart())
    async def on_start(message: Message) -> None:
        await message.answer("Ads reporting bot online. Phase 1 (foundation) ready.")

    @router.message(Command("ping"))
    async def on_ping(message: Message) -> None:
        await message.answer("pong")

    return router


def build_bot(token: str) -> Bot:
    return Bot(
        token=token,
        default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN_V2),
    )


def build_dispatcher() -> Dispatcher:
    dp = Dispatcher()
    dp.include_router(build_router())
    return dp
```

**Notes:**
- `DefaultBotProperties(parse_mode=ParseMode.MARKDOWN_V2)` sets a default; individual `send_message` calls can override. MarkdownV2 requires escaping reserved characters — Phase 2 ships an `escape_md_v2()` helper. [VERIFIED: aiogram 3 docs, telegram bot api docs]
- Polling: aiogram automatically calls `getUpdates` with long-poll timeout 30s by default. No conflict if only one container instance runs. For deploy safety, use `bot.delete_webhook(drop_pending_updates=True)` before polling starts to clean up any stale webhook configuration. [CITED: aiogram 3 docs]

### Pattern 4: aiosqlite + Hand-Rolled Migration Runner (INFRA-03)

**What:** A `schema_version` table tracks applied migrations. On boot, the migrator scans `app/schema/*.sql` in lexical order, applies any not yet recorded, all inside a transaction. Idempotent UPSERT is enforced at the SQL layer via `INSERT ... ON CONFLICT(...) DO UPDATE SET ...` — never in Python.

**Why hand-rolled (not Alembic):** SQLite + a small bounded schema (canonical metrics tables = single-digit count) means Alembic's autogeneration, branching, and SQLAlchemy metadata don't pay rent. The runner is ~40 lines, has no dependencies, and is trivially testable.

**Migration runner:**

```python
# app/storage/db.py
# Source pattern: aiosqlite docs (CITED: aiosqlite.omnilib.dev) + common project idiom
from importlib import resources
from pathlib import Path
import aiosqlite
import structlog

logger = structlog.get_logger(__name__)

SCHEMA_PACKAGE = "app.schema"


async def connect(database_path: Path) -> aiosqlite.Connection:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(database_path)
    await conn.execute("PRAGMA journal_mode=WAL;")        # better concurrent-read story
    await conn.execute("PRAGMA foreign_keys=ON;")
    await conn.execute("PRAGMA busy_timeout=5000;")
    await conn.commit()
    return conn


async def migrate(conn: aiosqlite.Connection) -> None:
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version ("
        "  version TEXT PRIMARY KEY,"
        "  applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
        ")"
    )
    await conn.commit()

    applied: set[str] = set()
    async with conn.execute("SELECT version FROM schema_version") as cur:
        applied = {row[0] async for row in cur}

    # importlib.resources keeps the schema discoverable inside the Docker image
    for resource in sorted(resources.files(SCHEMA_PACKAGE).iterdir(), key=lambda p: p.name):
        if not resource.name.endswith(".sql"):
            continue
        if resource.name in applied:
            continue
        sql = resource.read_text(encoding="utf-8")
        logger.info("applying_migration", file=resource.name)
        await conn.executescript(sql)
        await conn.execute("INSERT INTO schema_version(version) VALUES (?)", (resource.name,))
        await conn.commit()
```

**Initial schema (`app/schema/001_initial.sql`):**

```sql
-- Phase 1 schema bootstrap.
-- All metric tables defined here as STUBS; Phase 2 / Phase 3 add columns via 002_*.sql.

-- Dimension: campaigns (Meta-native).
CREATE TABLE IF NOT EXISTS campaigns (
    campaign_id     TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    objective       TEXT,
    status          TEXT,
    timezone_name   TEXT,                            -- ad-account tz, populated in Phase 2
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Fact: daily ad performance (Meta). meta_-prefixed conversion fields (CLAUDE.md rule).
CREATE TABLE IF NOT EXISTS ad_metrics (
    date            TEXT NOT NULL,                    -- ISO YYYY-MM-DD in ad-account tz
    campaign_id     TEXT NOT NULL,
    ad_set_id       TEXT NOT NULL DEFAULT '',        -- '' rather than NULL so PK is well-defined
    ad_id           TEXT NOT NULL DEFAULT '',
    spend                       REAL,
    impressions                 INTEGER,
    clicks                      INTEGER,
    ctr                         REAL,
    cpc                         REAL,
    cpm                         REAL,
    reach                       INTEGER,
    frequency                   REAL,
    meta_purchases_7dclick      INTEGER,
    meta_purchase_value_7dclick REAL,
    meta_cost_per_purchase      REAL,
    attribution_window          TEXT,                 -- e.g. "1d_view,7d_click"
    fetched_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (date, campaign_id, ad_set_id, ad_id),
    FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id) ON DELETE CASCADE
);

-- Fact: daily GA4 landing-page performance. ga4_-prefixed conversion fields (CLAUDE.md rule).
CREATE TABLE IF NOT EXISTS ga_metrics (
    date            TEXT NOT NULL,                    -- ISO YYYY-MM-DD in property tz
    landing_page    TEXT NOT NULL,
    source          TEXT NOT NULL DEFAULT '',
    medium          TEXT NOT NULL DEFAULT '',
    campaign_name   TEXT NOT NULL DEFAULT '',         -- utm_campaign — JOIN KEY to campaigns.name (Phase 3)
    sessions                INTEGER,
    users                   INTEGER,
    new_users               INTEGER,
    bounce_rate             REAL,
    avg_engagement_duration REAL,
    pageviews               INTEGER,
    ga4_purchases_lastclick INTEGER,
    ga4_revenue_lastclick   REAL,
    attribution_window      TEXT,                     -- e.g. "last_click"
    fetched_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (date, landing_page, source, medium, campaign_name)
);

-- Operational: per-ingest run log.
CREATE TABLE IF NOT EXISTS ingestion_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT NOT NULL,                    -- 'meta_ads' | 'ga4'
    started_at      TIMESTAMP NOT NULL,
    finished_at     TIMESTAMP,
    status          TEXT NOT NULL,                    -- 'success' | 'partial' | 'failed'
    rows_upserted   INTEGER DEFAULT 0,
    error_message   TEXT
);

-- Operational: per-report run log (Phase 2 wires send-confirmation here).
CREATE TABLE IF NOT EXISTS report_runs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    report_type         TEXT NOT NULL,                -- 'daily_digest' | 'weekly_summary' | 'alert' | etc.
    triggered_at        TIMESTAMP NOT NULL,
    delivered_at        TIMESTAMP,
    status              TEXT NOT NULL,                -- 'success' | 'failed'
    telegram_message_id INTEGER,
    error_message       TEXT
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_ad_metrics_date         ON ad_metrics(date);
CREATE INDEX IF NOT EXISTS idx_ad_metrics_campaign     ON ad_metrics(campaign_id);
CREATE INDEX IF NOT EXISTS idx_ga_metrics_date         ON ga_metrics(date);
CREATE INDEX IF NOT EXISTS idx_ga_metrics_campaign     ON ga_metrics(campaign_name);
CREATE INDEX IF NOT EXISTS idx_ingestion_log_source    ON ingestion_log(source, started_at DESC);
```

**UPSERT idempotency proof (the INFRA-03 acceptance test):**

```python
# app/storage/repository.py — example for ad_metrics; Phase 2 adds the Meta-side caller.
async def upsert_ad_metrics(conn, rows: list[dict]) -> int:
    sql = """
    INSERT INTO ad_metrics (
      date, campaign_id, ad_set_id, ad_id,
      spend, impressions, clicks, ctr, cpc, cpm, reach, frequency,
      meta_purchases_7dclick, meta_purchase_value_7dclick, meta_cost_per_purchase,
      attribution_window
    ) VALUES (
      :date, :campaign_id, :ad_set_id, :ad_id,
      :spend, :impressions, :clicks, :ctr, :cpc, :cpm, :reach, :frequency,
      :meta_purchases_7dclick, :meta_purchase_value_7dclick, :meta_cost_per_purchase,
      :attribution_window
    )
    ON CONFLICT(date, campaign_id, ad_set_id, ad_id) DO UPDATE SET
      spend                       = excluded.spend,
      impressions                 = excluded.impressions,
      clicks                      = excluded.clicks,
      ctr                         = excluded.ctr,
      cpc                         = excluded.cpc,
      cpm                         = excluded.cpm,
      reach                       = excluded.reach,
      frequency                   = excluded.frequency,
      meta_purchases_7dclick      = excluded.meta_purchases_7dclick,
      meta_purchase_value_7dclick = excluded.meta_purchase_value_7dclick,
      meta_cost_per_purchase      = excluded.meta_cost_per_purchase,
      attribution_window          = excluded.attribution_window,
      fetched_at                  = CURRENT_TIMESTAMP;
    """
    await conn.executemany(sql, rows)
    await conn.commit()
    return len(rows)
```

**Why `NOT NULL DEFAULT ''` for nullable PK components:** SQLite allows NULLs in composite PK columns and treats `NULL != NULL`, which would break UPSERT for rows where `ad_set_id` is absent. Forcing `''` as the sentinel makes UPSERTs deterministic. [VERIFIED: SQLite docs — UNIQUE constraint and NULL semantics]

### Pattern 5: APScheduler in aiogram Lifecycle

**What:** Build `AsyncIOScheduler` after the asyncio event loop is running (i.e., from inside `async def main()`). Start it just before `dp.start_polling(bot)`. Use a structlog-bound logger and inject dependencies (Settings, DB connection) via closures or `kwargs=`.

**Why this order matters:** `AsyncIOScheduler` binds to `asyncio.get_running_loop()` at start time. If built outside the loop (module-level), it falls back to `asyncio.new_event_loop()` and fires jobs on a different loop than aiogram — causing `Future attached to a different loop` errors. [VERIFIED: apscheduler v3 docs]

**Example:**

```python
# app/scheduler/jobs.py
# Source pattern: APScheduler v3 docs (CITED: apscheduler.readthedocs.io)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from zoneinfo import ZoneInfo
import structlog

logger = structlog.get_logger(__name__)


def build_scheduler(timezone: str) -> AsyncIOScheduler:
    tz = ZoneInfo(timezone)
    scheduler = AsyncIOScheduler(timezone=tz)
    # Phase 1: a single placeholder heartbeat job proves the wiring.
    scheduler.add_job(
        _heartbeat,
        trigger=CronTrigger(minute="*/15", timezone=tz),
        id="heartbeat",
        replace_existing=True,
        misfire_grace_time=60,
        coalesce=True,
        max_instances=1,
    )
    return scheduler


async def _heartbeat() -> None:
    logger.info("scheduler_heartbeat")
```

**Critical pitfalls (from PITFALLS.md):**
- Always set `timezone=ZoneInfo(settings.timezone)` on the scheduler AND on every CronTrigger. Server-local TZ + DST = reports arriving an hour off twice a year. [CITED: PITFALLS.md]
- `misfire_grace_time` matters when the container restarts mid-window: the job catches up rather than dropping silently.
- `coalesce=True` + `max_instances=1` prevent two daily reports firing back-to-back after a long restart.

**Shutdown order in `main.py`:**

```python
try:
    scheduler.start()
    await dp.start_polling(bot)
finally:
    scheduler.shutdown(wait=False)
    await bot.session.close()
    await conn.close()
```

`dp.start_polling()` handles its own SIGINT/SIGTERM trap. The `finally` block ensures the scheduler stops cleanly so APScheduler's SQLite jobstore (if added later in Phase 2) isn't left mid-write.

### Pattern 6: Docker Multi-Stage Build with uv (INFRA-04)

**What:** Stage 1 (builder) uses the official `ghcr.io/astral-sh/uv` base image to install dependencies into a `.venv`. Stage 2 (runtime) uses `python:3.12-slim`, copies the `.venv` and source, runs as a non-root user.

**Example:**

```dockerfile
# Source pattern: uv official Docker docs (CITED: docs.astral.sh/uv/guides/integration/docker)
# Stage 1: build the .venv with uv
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Install deps first (cache-friendly) using only the lock + manifest
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# Now copy the app and install it (no dev deps in runtime image)
COPY app/ ./app/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev


# Stage 2: minimal runtime
FROM python:3.12-slim-bookworm AS runtime

# tzdata is required for zoneinfo on slim images
RUN apt-get update && apt-get install -y --no-install-recommends \
        tzdata ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Non-root user
RUN groupadd --system app && useradd --system --gid app --home /app app

WORKDIR /app

# Copy venv + source from builder
COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --from=builder --chown=app:app /app/app /app/app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Persistent SQLite volume (mount as -v ads-data:/data)
RUN mkdir -p /data && chown app:app /data
VOLUME ["/data"]

USER app

# Healthcheck: opens the DB and runs the migrator's check, exits 0/1
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD python -c "import sqlite3,os,sys; sys.exit(0 if os.path.exists(os.environ.get('DATABASE_PATH','/data/metrics.db')) else 1)"

CMD ["python", "-m", "app"]
```

**Deploy validation steps:**

```bash
# local
docker build -t ads-reporting:dev .
docker run --rm \
  --env-file .env \
  -v ads-data:/data \
  ads-reporting:dev
```

**Notes:**
- `UV_LINK_MODE=copy` is required when crossing a Docker mount boundary; symlink mode breaks. [CITED: uv docker docs]
- `tzdata` apt-installed: required for `zoneinfo` on slim images (Python's stdlib looks at `/usr/share/zoneinfo`). Without it, `ZoneInfo("America/New_York")` raises `ZoneInfoNotFoundError`. [VERIFIED: zoneinfo docs + tzdata package contents]
- The HEALTHCHECK currently just verifies the DB file exists. For richer probing (e.g., "scheduler heartbeat row in last 30 min") promote in Phase 5.
- Railway/Fly.io: both ingest a `Dockerfile` and persistent volume directly — no docker-compose required for v1.

### Pattern 7: Structlog JSON Logging with PII Redaction (INFRA-05)

**What:** Configure structlog once at boot. Default to JSON output for production (Docker-friendly), with a `console` renderer for local dev. A redaction processor removes secrets and known-PII keys from every event_dict.

**Example:**

```python
# app/logging_setup.py
# Source pattern: structlog official docs (CITED: structlog.org)
import logging
import sys
import structlog

# Keys whose values are ALWAYS redacted regardless of nesting.
_REDACT_KEYS = frozenset({
    "token", "telegram_bot_token", "anthropic_api_key",
    "meta_access_token", "google_credentials", "access_token",
    "secret", "password", "authorization",
    # PII / raw ad data — never log these even if accidentally passed
    "email", "phone", "ad_creative_body", "ad_copy",
    "raw_response", "response_body",
})


def _redact(_, __, event_dict):
    for k in list(event_dict.keys()):
        if k.lower() in _REDACT_KEYS:
            event_dict[k] = "***REDACTED***"
    return event_dict


def configure_logging(level: str = "INFO", fmt: str = "json") -> None:
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)

    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        timestamper,
        _redact,
    ]

    if fmt == "json":
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=shared_processors + [renderer],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level)),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    # Bridge stdlib logging (aiogram, apscheduler, aiosqlite use it) into structlog's pipeline
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, level),
    )
```

**Logging discipline at call sites:**

```python
# GOOD — structured fields, allowlisted values
logger.info("update_received", chat_id=chat_id, event_type="message")
logger.info("report_delivered", report_type="daily", message_id=msg.message_id)
logger.warning("upsert_skipped", reason="invalid_attribution_window", count=3)

# BAD — never log raw API responses, message text, or campaign creative
logger.info("got_response", body=response.json())          # leaks PII / competitive data
logger.debug("processing_message", text=message.text)      # leaks user content + injection bait
```

**Field allowlist rule (from `.planning/research/PITFALLS.md`):** Log structured events with explicit field allowlists, NEVER raw response bodies. The `_redact` processor is a safety net; the primary defense is discipline at the call site. [CITED: PITFALLS.md]

### Pattern 8: Wiring It All Together — `main()` Entrypoint

```python
# app/main.py
import asyncio
import signal
import structlog

from app.config import load_settings
from app.logging_setup import configure_logging
from app.storage.db import connect, migrate
from app.bot import build_bot, build_dispatcher
from app.bot.middleware import AllowlistMiddleware
from app.scheduler.jobs import build_scheduler


async def main() -> None:
    # 1. Config first — fail fast if any required var missing
    settings = load_settings()

    # 2. Logging — every subsequent component logs through this pipeline
    configure_logging(level=settings.log_level, fmt=settings.log_format)
    log = structlog.get_logger(__name__)
    log.info("boot", phase=1, timezone=settings.timezone)

    # 3. Storage — open connection and run migrations
    conn = await connect(settings.database_path)
    await migrate(conn)
    log.info("storage_ready", path=str(settings.database_path))

    # 4. Bot + dispatcher
    bot = build_bot(settings.telegram_bot_token.get_secret_value())
    await bot.delete_webhook(drop_pending_updates=True)   # safe-default before polling
    dp = build_dispatcher()

    # 5. Allowlist middleware — registered BEFORE polling starts
    allowlist = AllowlistMiddleware(
        allowed_chat_ids=set(settings.allowed_chat_ids),
        allowed_user_ids=set(settings.allowed_user_ids),
    )
    dp.message.middleware(allowlist)
    dp.callback_query.middleware(allowlist)

    # 6. Scheduler (built inside the loop, started before polling)
    scheduler = build_scheduler(settings.timezone)
    scheduler.start()
    log.info("scheduler_started", job_count=len(scheduler.get_jobs()))

    try:
        log.info("polling_start")
        await dp.start_polling(bot)
    finally:
        log.info("shutdown_start")
        scheduler.shutdown(wait=False)
        await bot.session.close()
        await conn.close()
        log.info("shutdown_complete")


if __name__ == "__main__":
    asyncio.run(main())
```

And `app/__main__.py`:

```python
from app.main import main
import asyncio

if __name__ == "__main__":
    asyncio.run(main())
```

### Anti-Patterns to Avoid

- **Don't construct `AsyncIOScheduler` at module level.** Construct it inside `async def main()` after the loop is running. [CITED: apscheduler v3 docs]
- **Don't register middleware after `start_polling`.** Middleware registration must happen on `dp.<event>.middleware(...)` BEFORE polling starts; aiogram does not re-scan during a running loop.
- **Don't use Alembic for v1.** A simple migration runner is sufficient and reduces dependencies / ops burden.
- **Don't put SQLite database in the image.** Always volume-mount `/data` so persistence survives `docker rm`.
- **Don't log `message.text` or any API response body.** Even at DEBUG. Field-allowlist your log calls.
- **Don't `.env`-commit secrets.** Only `.env.example` ships in git. Add `.env`, `data/`, `*.db` to `.gitignore` and `.dockerignore`.
- **Don't reply to non-allowlisted users.** Silent drop. Replying confirms bot existence and aids username probing.
- **Don't use `python:3.12-alpine`.** musl libc breaks some wheels (pandas in Phase 2 especially). Stick with `python:3.12-slim-bookworm`.
- **Don't start the bot with a stale webhook configured.** Always `await bot.delete_webhook(drop_pending_updates=True)` before `start_polling`.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Config loading + validation | `os.environ.get` + manual casts | pydantic-settings | Type coercion, `SecretStr` masking, missing-key validation, .env support — all free |
| Telegram update routing | Raw `aiohttp` server | aiogram 3 routers + filters | Pagination, retries, MarkdownV2 escaping, FSM, webhook/poll abstraction |
| Cron scheduling | `asyncio.sleep` loops | APScheduler `AsyncIOScheduler` | DST handling, misfire grace, coalescing, persistent jobstore |
| SQL UPSERT | Read-modify-write in Python | `INSERT ... ON CONFLICT ... DO UPDATE` | Atomic at SQL layer; no race conditions; idempotent by construction |
| Retry / backoff | While-loops + sleep | tenacity decorators | Jitter, max attempts, exception predicates, async-aware (Phase 2) |
| Structured logging | `print(json.dumps(...))` | structlog | Processor pipeline (redaction, contextvars, formatting) decoupled from call sites |
| Secret redaction in logs | Ad-hoc `if 'token' in key` | structlog processor at config time | One processor catches all loggers, including aiogram/apscheduler stdlib bridges |
| Timezone arithmetic | `datetime.now()` + offsets | `zoneinfo` + tzdata package | DST + historical TZ rules; required by Phase 2 for ad-account TZ normalization |

**Key insight:** Phase 1 is the wrong place for any custom infra. Every component above has a well-supported standard. The novelty in this project is in Phases 2-4 (cross-source attribution, tool use, alert heuristics). Spend the complexity budget there.

## Common Pitfalls

### Pitfall 1: Allowlist registered AFTER router (security fail)
**What goes wrong:** Handlers fire before the middleware drops disallowed updates. A non-allowlisted user can run `/start` and (in later phases) trigger a Claude call.
**Why it happens:** Confused with aiohttp middleware ordering, or copy-pasted from a stale tutorial.
**How to avoid:** In `main.py`, register middleware on `dp.message.middleware(...)` and `dp.callback_query.middleware(...)` BEFORE `dp.start_polling()`. Write a test (`tests/test_allowlist.py`) that posts a synthetic Update from a non-allowlisted chat and asserts the handler did not execute.
**Warning signs:** Logs show `start_command` events with `chat_id` values not in the configured allowlist.

### Pitfall 2: APScheduler built at module level
**What goes wrong:** `Future attached to a different loop` errors when jobs fire, or jobs silently never fire because the scheduler bound to a loop that was never started.
**Why it happens:** Tutorials show `scheduler = AsyncIOScheduler()` at module scope.
**How to avoid:** Always construct inside `async def main()`. Pass dependencies via closures or `kwargs=` on `add_job`.
**Warning signs:** Logs show `scheduler_started` but no `scheduler_heartbeat` after 15 minutes; or asyncio runtime warnings about cross-loop futures.

### Pitfall 3: SQLite UPSERT broken by NULL in composite PK
**What goes wrong:** Re-running the same ingest row creates duplicates because `NULL != NULL` in SQLite's UNIQUE constraint semantics.
**Why it happens:** Treating ad_set_id / ad_id as nullable for campaign-level ingest rows.
**How to avoid:** Declare PK columns `NOT NULL DEFAULT ''` and write `''` as the sentinel for absent values. Phase 1 acceptance test: run the same `upsert_*` call twice and assert `SELECT COUNT(*)` is unchanged.
**Warning signs:** `ingestion_log.rows_upserted` grows monotonically without proportional table growth — or the inverse.

### Pitfall 4: Container can't find timezones (zoneinfo lookup fails)
**What goes wrong:** `ZoneInfoNotFoundError: 'America/New_York'` on first scheduler tick.
**Why it happens:** `python:3.12-slim` images don't include the IANA tzdb.
**How to avoid:** `apt-get install -y tzdata` in the Dockerfile (already in Pattern 6) OR `uv add tzdata` to install the Python tzdata package as a fallback.
**Warning signs:** Container starts, logs `scheduler_started`, then crashes within seconds with a `ZoneInfoNotFoundError`.

### Pitfall 5: structlog logs leak via stdlib bridge
**What goes wrong:** aiogram or apscheduler log lines bypass the redaction processor because they use stdlib `logging` directly, hitting `logger.warning("sending to %s with token %s", url, token)`.
**Why it happens:** Configuring structlog without bridging stdlib `logging` to it.
**How to avoid:** Add `logging.basicConfig(stream=sys.stdout, level=...)` so stdlib logs end up on stdout in some format; for full unification, use structlog's `ProcessorFormatter` + a stdlib handler that funnels through the same processor chain. As a minimum, set `logging.getLogger("aiogram").setLevel("INFO")` (not DEBUG) in production — aiogram's DEBUG can dump update payloads.
**Warning signs:** Log volume contains raw Telegram update JSON or API response bodies.

### Pitfall 6: Webhook configured from a prior deploy blocks polling
**What goes wrong:** Bot starts, logs `polling_start`, but `getUpdates` returns 409 Conflict immediately. No updates ever flow.
**Why it happens:** A previous deploy (or `@BotFather` experimentation) set a webhook URL on the bot. Polling and webhook are mutually exclusive.
**How to avoid:** Always call `await bot.delete_webhook(drop_pending_updates=True)` before `dp.start_polling(bot)`. [CITED: aiogram docs + Telegram bot API]
**Warning signs:** 409 errors in startup logs; updates appear in Telegram but the bot never responds.

### Pitfall 7: Container writes SQLite to ephemeral filesystem
**What goes wrong:** Each redeploy starts from an empty database. Phase 2 backfill / Phase 3 cross-source history evaporates.
**Why it happens:** Forgetting `VOLUME ["/data"]` in the Dockerfile or `-v ads-data:/data` in `docker run`.
**How to avoid:** Always mount `/data` as a named volume. On Fly.io: `fly volumes create ads-data --size 1` then reference in `fly.toml`. On Railway: persistent volumes via the dashboard. On a VPS: `-v /srv/ads-reporting/data:/data`.
**Warning signs:** First run after `docker rm` returns no rows; `applying_migration` runs every restart.

## Code Examples

All canonical code examples are inline in Patterns 1-8 above. The planner can lift them directly into task actions:

| Where to look | What |
|--------------|------|
| Pattern 1 | `app/config.py` (`Settings`, `load_settings`, `.env.example`) |
| Pattern 2 | `app/bot/middleware.py` (`AllowlistMiddleware`) + registration snippet |
| Pattern 3 | `app/bot/__init__.py` (`build_bot`, `build_dispatcher`, routers) |
| Pattern 4 | `app/storage/db.py` (`connect`, `migrate`) + `app/schema/001_initial.sql` + UPSERT helper shape |
| Pattern 5 | `app/scheduler/jobs.py` (`build_scheduler`, heartbeat) |
| Pattern 6 | `Dockerfile` (multi-stage uv build + healthcheck + non-root user) |
| Pattern 7 | `app/logging_setup.py` (`configure_logging`, `_redact` processor) |
| Pattern 8 | `app/main.py` (lifecycle wiring) + `app/__main__.py` (entrypoint) |

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| pip + venv + pip-tools | uv | 2024-2025 | Faster, single-binary; deterministic lock; now de-facto standard [CITED: STACK.md] |
| python-telegram-bot v13 sync + handlers | aiogram 3 async-native | aiogram 3 GA 2024; v2 unmaintained | Cleaner async fit for long Claude calls [CITED: STACK.md] |
| pydantic v1 `BaseSettings` | pydantic v2 + `pydantic-settings` package | pydantic v2 (2023); -settings split | `pydantic-settings` is a separate package now [CITED: pydantic-settings docs] |
| Alembic for every project | Hand-rolled SQL migrations for small SQLite apps | ongoing 2025-2026 trend | Reduce dependency surface; complexity-proportional tooling |
| Long polling vs webhook flame war | Long polling in dev + small deploys; webhook only when scaling or latency demands | settled by 2024 | Phase 1 uses polling; revisit only if Phase 5 needs HA [CITED: PITFALLS.md] |
| `print` / stdlib `logging` JSON formatters | structlog with processor pipeline | mature 2022+ | First-class structured fields, redaction, contextvars |

**Deprecated / outdated:**
- aiogram 2.x — unmaintained; do not import from aiogram 2 tutorials.
- pydantic v1 `BaseSettings` patterns — APIs and validators changed in v2; always reference v2-tagged docs.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Project uses `OR` semantics for the allowlist (chat-allowed OR user-allowed grants access) | Pattern 2 | If team wants stricter AND semantics, allowlist must be tightened — change is 2 lines in middleware |
| A2 | Phase 1 uses long-polling exclusively; webhook is deferred | Pattern 3, Summary | If deployment target (Railway/Fly.io) prefers webhook + a long-running process model, may require FastAPI/aiohttp wrapper — additive, not breaking |
| A3 | Hand-rolled SQL migration runner is acceptable (no Alembic) | Pattern 4 | If team prefers Alembic for future-proofing, migration plan adds ~half a day; schema content unchanged |
| A4 | Default to `python:3.12-slim-bookworm` runtime (not alpine) | Pattern 6 | Alpine produces smaller images but breaks pandas wheels in Phase 2; staying slim avoids a Phase 2 rewrite |
| A5 | SQLite path defaults to `/data/metrics.db` inside the container | Pattern 1, Pattern 6 | Different deploy targets may prefer other conventions; configurable via `DATABASE_PATH` env var |
| A6 | Operator has a way to look up Telegram chat ID + user IDs (e.g., via @userinfobot) | Pattern 1 | If not, plan should include a 5-min "how to find your chat ID" doc snippet in README |
| A7 | Phase 1 schema stub includes `campaigns`, `ad_metrics`, `ga_metrics` tables even though only `schema_version` + `ingestion_log` + `report_runs` are strictly required for Phase 1 acceptance criteria | Pattern 4 | If planner prefers a smaller Phase 1 schema (just operational tables), defer the fact tables to Phase 2's 002_*.sql migration |
| A8 | No CONTEXT.md / discuss-phase output exists — all decisions are inferred from CLAUDE.md + STACK.md | Throughout | Locked decisions from a discuss session would override these inferences; none exist |

**If this table is non-empty:** flag A1 and A7 explicitly to the human during plan review — both have valid alternatives and the choice is preference-driven.

## Open Questions

1. **Should Phase 1 schema include the canonical fact tables (`campaigns`, `ad_metrics`, `ga_metrics`) or only operational tables?**
   - What we know: INFRA-03 requires "canonical metrics with idempotent UPSERT," and the success criterion specifically tests "no row duplication on re-run."
   - What's unclear: Does that test require fact tables to exist in Phase 1, or can a contrived `test_upsert_idempotency` table satisfy it?
   - Recommendation: Include fact tables now (per A7). The schema is well-researched in `.planning/research/ARCHITECTURE.md` and including them shifts no real risk forward.

2. **What is the production deploy target — VPS Docker Compose, Railway, or Fly.io?**
   - What we know: ROADMAP says "VPS or Railway/Fly.io"; all three are compatible with the Dockerfile in Pattern 6.
   - What's unclear: Which one to write deploy docs for.
   - Recommendation: Plan can include a generic `docker run` invocation + brief notes for each target (Fly.io needs `fly volumes`; Railway needs persistent storage configured in dashboard). Defer specific deploy docs until the target is chosen.

3. **What timezone do reports default to?**
   - What we know: CLAUDE.md says "always normalize to a single project timezone"; Settings has a `timezone` field defaulting to UTC.
   - What's unclear: Whether the operator's local TZ or the ad-account TZ is the canonical "project TZ."
   - Recommendation: Default to UTC in Phase 1; resolve in Phase 2 when ad-account TZ is discovered.

4. **Are there allowlist test fixtures (a known chat-ID + user-ID) we can use, or should the plan create a "test mode" bypass?**
   - What we know: Tests need synthetic Update objects.
   - Recommendation: Build a `make_message_update(chat_id, user_id, text)` test factory in `tests/conftest.py`; no production bypass needed.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python 3.12 | Local dev | (operator must verify) | — | uv installs via `uv python pin 3.12` |
| uv | Project package manager | (operator must verify) | — | `pip install uv` or install script from astral.sh |
| Docker | Containerized deploy + local container test | (operator must verify) | — | None for production; local dev can run without Docker via `uv run python -m app` |
| Telegram Bot Token | Allowlist test, /start verification | Issued by @BotFather | — | None — must obtain before Phase 1 acceptance |
| Telegram group + chat ID | INFRA-02 success criterion | Operator must create or have | — | None — must obtain |
| Internet access (Telegram API) | Long-polling | Required at runtime | — | None |
| tzdata (in container) | zoneinfo | Provided by `apt install tzdata` | — | `uv add tzdata` Python package |

**Missing dependencies with no fallback:**
- A registered Telegram bot token + the team's group chat ID + at least one user ID — these are operator-provided values. Plan should include a "before you start" section listing them.

**Missing dependencies with fallback:**
- Docker locally: optional for development (run via `uv run python -m app` instead). Required for INFRA-04 acceptance.

## Project Constraints (from CLAUDE.md)

These directives have the same authority as locked decisions. Plans must not contradict them:

| Constraint | Source | Phase 1 Implication |
|-----------|--------|--------------------|
| Python 3.12+ with aiogram 3 (async); no Node alternative | CLAUDE.md Architecture Decisions | Stack is locked — no investigation of alternatives |
| SQLite for v1 (not Postgres) | CLAUDE.md Architecture Decisions | Use `aiosqlite`; defer Postgres path to multi-tenant trigger |
| APScheduler in-process (no Celery) | CLAUDE.md Architecture Decisions | `AsyncIOScheduler` only |
| Long-polling in dev, webhook in prod | CLAUDE.md Architecture Decisions | Phase 1 ships long-polling; webhook is a later option |
| Read-only API access | CLAUDE.md Security Non-Negotiables | No Meta write endpoints; not enforceable in Phase 1 but design decision is locked |
| Chat-ID allowlist BEFORE Claude / handler execution | CLAUDE.md Security Non-Negotiables | Middleware registered on `dp.message.middleware` BEFORE polling starts — Pattern 2 |
| Credentials never in source; env vars or secrets manager | CLAUDE.md Security Non-Negotiables | pydantic-settings + `SecretStr`; `.env` gitignored; `.env.example` committed |
| Prompt-injection guardrails (delimited `<data>` tags) | CLAUDE.md Security Non-Negotiables | Not active in Phase 1 (no Claude yet); document the contract in storage layer so Phase 4 wires it correctly |
| Meta conversion fields: `meta_` prefix | CLAUDE.md Data Model Rules | Phase 1 schema uses `meta_purchases_7dclick`, etc. |
| GA4 conversion fields: `ga4_` prefix | CLAUDE.md Data Model Rules | Phase 1 schema uses `ga4_purchases_lastclick`, etc. |
| Never blend Meta + GA4 conversions | CLAUDE.md Data Model Rules | Schema enforces separate columns; reports never `SUM()` across them |
| Meta ↔ GA4 join: exact UTM campaign-name match only | CLAUDE.md Data Model Rules | Schema names `ga_metrics.campaign_name` to match `campaigns.name` exactly; no fuzzy logic anywhere |
| Stack versions: aiogram ^3, facebook-business ^22, anthropic ^0.102, apscheduler ^3, aiosqlite ^0, pydantic-settings ^2, tenacity ^9 | CLAUDE.md Stack Versions | Use these versions verbatim in `pyproject.toml` |
| Meta API target v24.0+ | CLAUDE.md Key Pitfalls | Phase 2 concern; not Phase 1 |
| Telegram 4096-char auto-split | CLAUDE.md Key Pitfalls | Phase 2 concern; Phase 1 doesn't generate long messages |
| Dead-man's-switch pings AFTER Telegram 200 | CLAUDE.md Key Pitfalls | Phase 2/5 concern; `report_runs` schema is shaped to support it |
| `.planning/` is the GSD workspace | CLAUDE.md Planning Files | Research output goes to `.planning/phases/01-foundation-walking-skeleton/01-RESEARCH.md` |

## Security Domain

Phase 1 is the security foundation. ASVS-level controls apply:

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | yes | Telegram bot token via env / SecretStr; never logged; rotated via @BotFather on suspicion |
| V3 Session Management | partial | Telegram handles auth; bot enforces allowlist (chat-ID/user-ID) at middleware boundary |
| V4 Access Control | yes | Allowlist middleware = the ONLY access control surface; rejection happens BEFORE handler / Claude call |
| V5 Input Validation | yes | pydantic-settings validates all config on boot; aiogram filters validate update structure; no free-text input goes to Claude in Phase 1 (deferred to Phase 4) |
| V6 Cryptography | yes (delegated) | TLS provided by Telegram HTTPS; no project crypto rolled |
| V7 Error Handling and Logging | yes | structlog redaction processor for `token`, `secret`, `access_token`, raw bodies; no PII / ad copy in logs (Pattern 7) |
| V8 Data Protection | yes | SQLite stored in mounted volume; secrets in env / secrets manager only; no PII columns in schema |
| V9 Communication | yes (delegated) | All external traffic over HTTPS (Telegram, future Meta/GA4/Anthropic) |
| V10 Malicious Code | partial | Phase 1: nothing executes user-supplied code; Phase 4 (Claude tool use) is the active concern |
| V14 Configuration | yes | `.env` gitignored, `.env.example` committed, `pyproject.toml` pinned, non-root container user, tzdata installed |

### Known Threat Patterns for this stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Drive-by user DMs the bot, drains Claude budget | DoS / Information Disclosure | Allowlist middleware drops at the dispatcher boundary (Pattern 2); confirmed by `tests/test_allowlist.py` |
| Forged Telegram update via webhook | Spoofing | Phase 1 uses long-polling so this is N/A; if webhook is enabled later, set + verify `secret_token` header per Bot API |
| Credentials committed to git | Information Disclosure | `.gitignore` for `.env`, `data/`, `*.db`; pre-commit `gitleaks` hook recommended; only `.env.example` ships |
| SQL injection through future user input (Phase 4 risk surfaced here) | Tampering | All queries use named parameters via aiosqlite (`:foo`); never string-format SQL — discipline locked in by Phase 1 repository pattern |
| Container runs as root, escalation surface | Elevation of Privilege | Non-root `app:app` user in Dockerfile (Pattern 6) |
| Logs leak secrets / PII to log aggregator | Information Disclosure | structlog `_redact` processor + field-allowlist call-site discipline (Pattern 7) |
| Stale webhook blocks polling, bot looks dead | DoS (operational) | `await bot.delete_webhook(drop_pending_updates=True)` on every boot |
| Group admin adds bot to a new group, leaks reports | Information Disclosure / Privilege escalation | Disable join-groups via @BotFather (`/setjoingroups → Disable`); allowlist drops at the chat-id level anyway |

## Sources

### Primary (HIGH confidence)
- `.planning/research/STACK.md` — Stack with verified May 2026 versions [VERIFIED in-repo]
- `.planning/research/ARCHITECTURE.md` — Component map, schema, lifecycle decisions [VERIFIED in-repo]
- `.planning/research/PITFALLS.md` — Security and operational pitfalls with sources [VERIFIED in-repo]
- `CLAUDE.md` — Project-level constraints and stack pins [VERIFIED in-repo]
- aiogram 3 official documentation (docs.aiogram.dev) — middleware, dispatcher, long-polling [CITED]
- pydantic-settings v2 official documentation (docs.pydantic.dev) — `BaseSettings`, `SecretStr`, validators [CITED]
- APScheduler v3 official documentation (apscheduler.readthedocs.io) — AsyncIOScheduler lifecycle [CITED]
- aiosqlite documentation (aiosqlite.omnilib.dev) — connection, executescript [CITED]
- structlog official documentation (structlog.org) — processor chain, JSON renderer [CITED]
- uv Docker integration guide (docs.astral.sh/uv/guides/integration/docker) — multi-stage builds, link modes [CITED]
- SQLite documentation (sqlite.org) — `ON CONFLICT ... DO UPDATE`, NULL semantics, PRAGMAs [CITED]
- Telegram Bot API documentation (core.telegram.org/bots/api) — `deleteWebhook`, MarkdownV2 [CITED]

### Secondary (MEDIUM confidence)
- Industry consensus on hand-rolled SQL migrations for small SQLite apps (pattern frequently advocated in 2025-2026 blog posts on small Python services) [INFERRED from STACK.md "Migrations: optional for v1 — can defer to plain SQL files initially"]

### Tertiary (LOW confidence)
- None — all assertions in this document are either verified from in-repo research or cited from official documentation.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — versions pinned in CLAUDE.md and verified against PyPI in STACK.md (May 2026)
- Architecture patterns: HIGH — derived from `.planning/research/ARCHITECTURE.md` and adapted for the locked aiogram + long-polling Phase 1 decision
- Pitfalls: HIGH — sourced from `.planning/research/PITFALLS.md` (which cites official Meta, Google, Telegram, Anthropic docs)
- Migration runner pattern: MEDIUM — common practice, no single authoritative source; tradeoff is clearly explained
- Docker / uv pattern: HIGH — uv documentation provides this template directly

**Research date:** 2026-05-19
**Valid until:** 2026-06-19 (30 days; stack is stable, but aiogram 3 minors and Meta API versions move quarterly — re-verify before Phase 2 plan)
