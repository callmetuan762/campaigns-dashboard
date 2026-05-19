---
plan: "01-entrypoint-docker"
phase: 1
wave: 3
depends_on: ["01-scaffold", "01-database", "01-telegram-bot"]
autonomous: true
files_modified:
  - src/logging_setup.py
  - src/main.py
  - src/__main__.py
  - Dockerfile
  - docker-compose.yml
  - README.md
requirements_addressed:
  - INFRA-04
  - INFRA-05
must_haves:
  truths:
    - "structlog is configured with a JSON renderer in production mode and a redaction processor that strips fields named token, secret, access_token, password, api_key, raw_response, ad_creative_body, email, phone from every log event"
    - "stdlib logging (aiogram, apscheduler, aiosqlite internals) is bridged to structlog so third-party log lines are also subject to redaction"
    - "src/main.py executes the lifecycle: load_settings -> configure_logging -> connect DB + migrate -> create bot+dispatcher -> delete_webhook(drop_pending_updates=True) -> start AsyncIOScheduler with SQLAlchemyJobStore -> dp.start_polling -> graceful shutdown in finally block"
    - "Dockerfile produces a runnable image using ghcr.io/astral-sh/uv:python3.12-bookworm-slim builder, with a python:3.12-slim-bookworm runtime, non-root `app` user, /data volume, and a HEALTHCHECK"
    - "docker-compose.yml mounts ./data into /data, uses .env as env_file, restarts unless-stopped"
    - "README.md explains: copy .env.example to .env, fill values, run docker compose up, /start the bot in Telegram"
    - "Running `python -m src` invokes src.main:main() (entrypoint stub from Plan 01 is replaced)"
  artifacts:
    - path: "src/logging_setup.py"
      provides: "configure_logging() function with JSON + redaction processor + stdlib bridge"
      exports: ["configure_logging"]
    - path: "src/main.py"
      provides: "Async main() that wires the full lifecycle and starts long-polling"
      exports: ["main"]
    - path: "src/__main__.py"
      provides: "Entrypoint: asyncio.run(main())"
    - path: "Dockerfile"
      provides: "Multi-stage uv build then slim runtime with non-root user and /data volume"
      contains: "FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder, FROM python:3.12-slim-bookworm AS runtime, USER app, VOLUME, HEALTHCHECK"
    - path: "docker-compose.yml"
      provides: "Single-service compose file with .env, ./data volume, restart unless-stopped"
      contains: "env_file: .env, volumes: ./data:/data, restart: unless-stopped"
    - path: "README.md"
      provides: "Setup, env config, run instructions, allowlist setup guidance"
      contains: "Quick Start, Environment Variables, How to find your chat ID, Telegram bot setup"
  key_links:
    - from: "src/main.py"
      to: "src/logging_setup.py:configure_logging"
      via: "called immediately after load_settings(), before any other component logs"
      pattern: "configure_logging"
    - from: "src/main.py"
      to: "AsyncIOScheduler + SQLAlchemyJobStore"
      via: "built inside async def main() AFTER event loop is running; jobstore url = sqlite:///{db_path}"
      pattern: "AsyncIOScheduler|SQLAlchemyJobStore"
    - from: "src/main.py"
      to: "bot.delete_webhook(drop_pending_updates=True)"
      via: "called BEFORE dp.start_polling — eliminates Pitfall 6 (409 Conflict from stale webhook)"
      pattern: "delete_webhook.*drop_pending_updates=True"
    - from: "Dockerfile"
      to: "tzdata package + non-root user"
      via: "apt-get install tzdata + RUN useradd app"
      pattern: "tzdata|useradd"
    - from: "src/__main__.py"
      to: "src/main.py:main"
      via: "asyncio.run(main())"
      pattern: "asyncio\\.run\\(main\\(\\)\\)"
---

<objective>
Close out Phase 1 by wiring everything Plans 01–03 produced into a runnable application and packaging it as a deployable Docker container with structured logging and PII redaction. Cover INFRA-04 ("single Docker container deployable to VPS or Railway/Fly.io") and INFRA-05 ("structured logging captures API outcomes, delivery status, errors without logging PII or raw ad data") in full.

Purpose: A real operator can clone the repo, copy `.env.example` to `.env`, fill in their Telegram bot token + chat ID + user ID, run `docker compose up`, and the bot answers `/start` in their Telegram group. Anything less and Phase 1 hasn't shipped.

Output: `src/main.py` lifecycle, `src/logging_setup.py` with JSON + redaction, a multi-stage `Dockerfile`, a `docker-compose.yml`, and a `README.md` operator runbook. This is the deliverable that "marks Phase 1 complete" per the ROADMAP success criteria.
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

# Prior-plan SUMMARYs for the interfaces this plan composes:
@.planning/phases/01-foundation-walking-skeleton/01-scaffold-SUMMARY.md
@.planning/phases/01-foundation-walking-skeleton/01-database-SUMMARY.md
@.planning/phases/01-foundation-walking-skeleton/01-telegram-bot-SUMMARY.md

<interfaces>
This plan composes interfaces produced by Plans 01, 02, 03:

```python
# From src/config.py (Plan 01)
class Settings:
    telegram_bot_token: SecretStr
    db_path: Path
    log_level: str
    report_timezone: str

def load_settings() -> Settings: ...

# From src/db/client.py (Plan 02)
class DBClient:
    def __init__(self, db_path: Path) -> None: ...
    async def connect(self) -> None: ...
    async def close(self) -> None: ...

# From src/bot/setup.py (Plan 03)
def create_bot_and_dispatcher(settings: Settings, db_client: DBClient) -> tuple[Bot, Dispatcher]: ...
```

APScheduler 3 with SQLAlchemyJobStore reference:
```python
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

jobstore = SQLAlchemyJobStore(url=f"sqlite:///{settings.db_path}")
scheduler = AsyncIOScheduler(jobstores={"default": jobstore}, timezone=settings.report_timezone)
scheduler.start()  # MUST be called inside async def main() after loop is running
```
</interfaces>
</context>

<tasks>

<task type="auto">
  <name>Task 1: Implement structlog configuration with JSON renderer, redaction processor, and stdlib bridge</name>
  <files>src/logging_setup.py</files>
  <read_first>
    - .planning/phases/01-foundation-walking-skeleton/01-RESEARCH.md (Pattern 7: Structlog JSON Logging with PII Redaction, lines 712-787; Pitfall 5 at lines 914-918)
    - CLAUDE.md (Security Non-Negotiables, Data Model Rules — confirms which fields are sensitive)
    - src/config.py (Plan 01 — confirm log_level field name and default)
  </read_first>
  <action>
Create `src/logging_setup.py` with EXACTLY this content:

```python
"""structlog configuration with JSON output and PII/secret redaction.

INFRA-05: Structured logging captures API call outcomes, report delivery status,
and errors WITHOUT logging PII (email, phone) or raw ad data (ad copy, creative
bodies) or secrets (tokens, API keys, passwords).

Design:
- Default to JSON renderer (Docker stdout friendly; easy to ship to a log aggregator).
- The _redact_processor runs BEFORE serialization and substitutes the literal
  string "***REDACTED***" for any value whose key is in the deny list.
- stdlib logging (used by aiogram, apscheduler, aiosqlite) is bridged through
  structlog's ProcessorFormatter so third-party log lines also pass through
  the redaction processor.
"""
from __future__ import annotations

import logging
import sys

import structlog

# Lowercase keys whose VALUES are always replaced with ***REDACTED*** regardless
# of where they appear in the structured event dict.
_REDACT_KEYS: frozenset[str] = frozenset(
    {
        # Secrets
        "token",
        "telegram_bot_token",
        "anthropic_api_key",
        "meta_access_token",
        "meta_app_secret",
        "google_credentials",
        "ga4_service_account_json",
        "access_token",
        "secret",
        "password",
        "api_key",
        "authorization",
        # PII
        "email",
        "phone",
        # Raw upstream payloads / ad creative
        "raw_response",
        "response_body",
        "ad_creative_body",
        "ad_copy",
        "message_text",
        "text",
    }
)


def _redact_processor(_logger, _method_name, event_dict: dict) -> dict:
    """Replace sensitive values with ***REDACTED*** in-place."""
    for key in list(event_dict.keys()):
        if key.lower() in _REDACT_KEYS:
            event_dict[key] = "***REDACTED***"
    return event_dict


def configure_logging(level: str = "INFO", fmt: str = "json") -> None:
    """Initialize structlog and bridge stdlib logging.

    Args:
        level: One of "DEBUG", "INFO", "WARNING", "ERROR".
        fmt: "json" for production, "console" for local development.
    """
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)

    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        timestamper,
        _redact_processor,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if fmt == "json":
        renderer: structlog.types.Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    log_level = getattr(logging, level.upper(), logging.INFO)

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    # Bridge stdlib logging (aiogram, apscheduler, aiosqlite, etc.) into the
    # same processor pipeline so their messages also pass through _redact_processor.
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    # Replace any existing handlers (Docker re-runs, test sessions) with ours.
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(log_level)

    # Tame the chattiest third-party loggers in production.
    logging.getLogger("aiogram").setLevel(logging.INFO)
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.INFO)
    logging.getLogger("aiosqlite").setLevel(logging.WARNING)
```

Implementation notes:
- The redaction processor matches by lowercased key, so `Authorization`, `AUTHORIZATION`, `authorization` all redact.
- `message_text` and `text` are in the deny list as a belt-and-suspenders defense for any handler that accidentally logs them.
- `structlog.stdlib.ProcessorFormatter.remove_processors_meta` strips internal processor metadata that ProcessorFormatter adds; required when chaining renderer after foreign_pre_chain.
- aiogram's `aiogram.event` logger at DEBUG can dump full update payloads — explicitly held at WARNING in production.
  </action>
  <verify>
    <automated>python -c "import sys; sys.path.insert(0,'.'); from src.logging_setup import configure_logging, _redact_processor, _REDACT_KEYS; required = {'token','telegram_bot_token','anthropic_api_key','meta_access_token','google_credentials','access_token','secret','password','api_key','raw_response','ad_creative_body','email','phone'}; missing = required - _REDACT_KEYS; assert not missing, f'missing: {missing}'; out = _redact_processor(None, None, {'token':'abc','msg':'hi','EMAIL':'a@b.c','authorization':'Bearer x'}); assert out['token'] == '***REDACTED***'; assert out['msg'] == 'hi'; assert out['EMAIL'] == '***REDACTED***'; assert out['authorization'] == '***REDACTED***'; configure_logging(level='INFO', fmt='json'); import structlog; structlog.get_logger('test').info('event', token='supersecret', other='kept'); print('OK')"</automated>
  </verify>
  <acceptance_criteria>
    - File `src/logging_setup.py` exists
    - `grep -E '^def configure_logging' src/logging_setup.py` matches once
    - `grep -E '^_REDACT_KEYS' src/logging_setup.py` matches
    - `grep -E '^def _redact_processor' src/logging_setup.py` matches
    - All required redact keys appear in `_REDACT_KEYS`: `token`, `secret`, `access_token`, `password`, `api_key`, `raw_response`, `ad_creative_body`, `email`, `phone` (verifiable individually with grep)
    - `grep -E 'JSONRenderer\(\)' src/logging_setup.py` matches
    - `grep -E 'ProcessorFormatter' src/logging_setup.py` matches (stdlib bridge present)
    - `grep -E 'logging\.getLogger\("aiogram"\)\.setLevel' src/logging_setup.py` matches
    - Automated verify passes: redact processor substitutes `***REDACTED***` for `token`, `EMAIL`, `authorization` (case-insensitive) while leaving non-sensitive keys untouched
  </acceptance_criteria>
  <done>configure_logging is importable. The redaction processor substitutes sensitive values regardless of key case. stdlib logging is bridged so aiogram/apscheduler lines also flow through redaction. INFRA-05 redaction guarantees are verifiable by grep and automated test.</done>
</task>

<task type="auto">
  <name>Task 2: Implement src/main.py lifecycle and entrypoint, replacing the Plan 01 stub</name>
  <files>src/main.py, src/__main__.py</files>
  <read_first>
    - .planning/phases/01-foundation-walking-skeleton/01-RESEARCH.md (Pattern 5: APScheduler in aiogram Lifecycle, lines 574-630; Pattern 8: Wiring It All Together, lines 788-859; Pitfall 2 at lines 897-901; Pitfall 6 at lines 920-924)
    - CLAUDE.md (Stack Versions — confirms apscheduler ^3)
    - src/config.py (Plan 01 — Settings fields)
    - src/db/client.py (Plan 02 — DBClient surface)
    - src/bot/setup.py (Plan 03 — create_bot_and_dispatcher signature)
    - src/logging_setup.py (Task 1 — configure_logging signature)
  </read_first>
  <action>
Replace the placeholder `src/__main__.py` (from Plan 01) with:

```python
"""Module entrypoint: `python -m src` → asyncio.run(main())."""
import asyncio

from src.main import main

if __name__ == "__main__":
    asyncio.run(main())
```

Create `src/main.py` with EXACTLY this lifecycle:

```python
"""Application lifecycle for Phase 1: foundation & walking skeleton.

Order (do not reorder — each step depends on the previous):
    1. load_settings()       — fail fast on missing required env vars
    2. configure_logging()   — every subsequent component logs through this pipeline
    3. DBClient.connect()    — opens aiosqlite, applies migrations
    4. create_bot_and_dispatcher() — Bot + Dispatcher with allowlist registered
    5. bot.delete_webhook(drop_pending_updates=True) — avoids Pitfall 6 (409 Conflict)
    6. AsyncIOScheduler with SQLAlchemyJobStore — built INSIDE the loop (Pitfall 2)
    7. scheduler.start() then dp.start_polling(bot)
    8. finally: scheduler.shutdown -> bot.session.close -> db.close
"""
from __future__ import annotations

import asyncio

import structlog
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from src.bot.setup import create_bot_and_dispatcher
from src.config import load_settings
from src.db.client import DBClient
from src.logging_setup import configure_logging


async def _scheduler_heartbeat() -> None:
    """Phase 1 placeholder job — proves the scheduler is wired and firing.

    Phase 2 replaces this with the real Meta ingest job; Phase 2/3 add the
    daily digest / weekly summary jobs.
    """
    structlog.get_logger(__name__).info("scheduler_heartbeat")


async def main() -> None:
    # 1. Config (fail fast on missing required env)
    settings = load_settings()

    # 2. Logging (everything below this point logs through redaction)
    configure_logging(level=settings.log_level, fmt="json")
    log = structlog.get_logger(__name__)
    log.info("boot", phase=1, timezone=settings.report_timezone, db_path=str(settings.db_path))

    # 3. Storage
    db = DBClient(settings.db_path)
    await db.connect()
    log.info("storage_ready", path=str(settings.db_path))

    # 4. Bot + Dispatcher (allowlist registered inside the factory)
    bot, dp = create_bot_and_dispatcher(settings, db)

    # 5. Clear any stale webhook so long-polling won't get 409 (Pitfall 6)
    await bot.delete_webhook(drop_pending_updates=True)
    log.info("webhook_cleared")

    # 6. Scheduler (constructed INSIDE the running loop — Pitfall 2)
    jobstore = SQLAlchemyJobStore(url=f"sqlite:///{settings.db_path}")
    scheduler = AsyncIOScheduler(
        jobstores={"default": jobstore},
        timezone=settings.report_timezone,
    )
    scheduler.add_job(
        _scheduler_heartbeat,
        trigger=CronTrigger(minute="*/15", timezone=settings.report_timezone),
        id="phase1_heartbeat",
        replace_existing=True,
        misfire_grace_time=60,
        coalesce=True,
        max_instances=1,
    )
    scheduler.start()
    log.info("scheduler_started", jobs=len(scheduler.get_jobs()))

    # 7. Long-polling (blocking until SIGINT/SIGTERM)
    try:
        log.info("polling_start")
        await dp.start_polling(bot)
    finally:
        log.info("shutdown_start")
        try:
            scheduler.shutdown(wait=False)
        except Exception as e:  # noqa: BLE001
            log.warning("scheduler_shutdown_error", error=str(e))
        try:
            await bot.session.close()
        except Exception as e:  # noqa: BLE001
            log.warning("bot_close_error", error=str(e))
        await db.close()
        log.info("shutdown_complete")


if __name__ == "__main__":
    asyncio.run(main())
```

Implementation notes:
- `SQLAlchemyJobStore` uses the same SQLite file as application data so APScheduler job state survives container restarts. SQLAlchemy is already declared in pyproject.toml (Plan 01).
- The heartbeat job uses `CronTrigger(minute="*/15", timezone=settings.report_timezone)` — passing timezone to BOTH scheduler and trigger per RESEARCH "Critical pitfalls" line 614.
- `misfire_grace_time=60`, `coalesce=True`, `max_instances=1` together implement the restart-safe behavior recommended by RESEARCH Pattern 5.
- The `finally` block catches sub-shutdown errors so one failure doesn't prevent the others from running.
- `dp.start_polling(bot)` handles SIGINT/SIGTERM internally via aiogram's signal handling.
  </action>
  <verify>
    <automated>python -c "import pathlib, re; s = pathlib.Path('src/main.py').read_text(); order = lambda p: re.search(p, s).start() if re.search(p, s) else -1; steps = [('load_settings', order(r'load_settings\(\)')), ('configure_logging', order(r'configure_logging\(')), ('db.connect', order(r'await db\.connect\(\)')), ('create_bot_and_dispatcher', order(r'create_bot_and_dispatcher\(')), ('delete_webhook', order(r'delete_webhook\(drop_pending_updates=True\)')), ('AsyncIOScheduler', order(r'AsyncIOScheduler\(')), ('scheduler.start', order(r'scheduler\.start\(\)')), ('start_polling', order(r'dp\.start_polling\(bot\)'))]; [print(n, p) for n, p in steps]; assert all(p > 0 for _, p in steps), f'missing steps: {steps}'; positions = [p for _, p in steps]; assert positions == sorted(positions), f'out of order: {steps}'; assert 'SQLAlchemyJobStore' in s; assert 'finally:' in s; print('lifecycle OK')"</automated>
  </verify>
  <acceptance_criteria>
    - File `src/main.py` exists with `async def main() -> None:` defined
    - `grep -E '^async def main\(\) -> None:' src/main.py` matches once
    - File `src/__main__.py` contains `from src.main import main` and `asyncio.run(main())`
    - The eight lifecycle steps appear in source order: load_settings → configure_logging → db.connect → create_bot_and_dispatcher → delete_webhook(drop_pending_updates=True) → AsyncIOScheduler → scheduler.start → dp.start_polling (verified by the automated verify command)
    - `grep -E 'SQLAlchemyJobStore\(url=f"sqlite:///' src/main.py` matches (job persistence wired)
    - `grep -E 'misfire_grace_time=60' src/main.py` matches; `grep -E 'coalesce=True' src/main.py` matches; `grep -E 'max_instances=1' src/main.py` matches
    - `grep -E 'CronTrigger\(minute="\*/15"' src/main.py` matches
    - `grep -E 'finally:' src/main.py` matches (graceful shutdown wrapped)
    - `grep -E 'scheduler\.shutdown\(wait=False\)' src/main.py` matches
    - `grep -E 'await bot\.session\.close\(\)' src/main.py` matches
    - `grep -E 'await db\.close\(\)' src/main.py` matches
    - Importing `src.main:main` does not raise (`python -c "from src.main import main; print(main.__name__)"` prints `main`)
  </acceptance_criteria>
  <done>src/main.py implements the canonical 8-step lifecycle in the correct order with graceful shutdown. `python -m src` invokes main() via the updated __main__.py. The lifecycle ordering is verifiable by static grep + the automated source-position check.</done>
</task>

<task type="auto">
  <name>Task 3: Write Dockerfile, docker-compose.yml, and README operator runbook</name>
  <files>Dockerfile, docker-compose.yml, README.md</files>
  <read_first>
    - .planning/phases/01-foundation-walking-skeleton/01-RESEARCH.md (Pattern 6: Docker Multi-Stage Build with uv, lines 632-710; Pitfall 4 at lines 908-912; Pitfall 7 at lines 926-930)
    - pyproject.toml (Plan 01 — confirm package layout uses `src/` and hatchling backend)
    - .env.example (Plan 01 — for README env table)
    - .dockerignore (Plan 01 — confirm exists so layer cache is clean)
  </read_first>
  <action>
Create `Dockerfile` at repo root with EXACTLY this content (newlines preserved):

```dockerfile
# syntax=docker/dockerfile:1.7
# Multi-stage build: uv builder produces .venv; slim runtime copies it.

# ---------- Stage 1: builder ----------
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Install runtime deps first for cache friendliness
COPY pyproject.toml ./
COPY uv.lock* ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-install-project --no-dev

# Now copy the source and install the project itself
COPY src/ ./src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev


# ---------- Stage 2: runtime ----------
FROM python:3.12-slim-bookworm AS runtime

# tzdata required for zoneinfo (Pitfall 4); ca-certificates for HTTPS to Telegram/Meta/GA4
RUN apt-get update && apt-get install -y --no-install-recommends \
        tzdata \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Non-root user
RUN groupadd --system app && useradd --system --gid app --home /app app

WORKDIR /app

# Copy the venv and source from the builder stage
COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --from=builder --chown=app:app /app/src /app/src

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DB_PATH=/data/metrics.db

# Persistent SQLite volume (Pitfall 7: never write DB to ephemeral fs)
RUN mkdir -p /data && chown app:app /data
VOLUME ["/data"]

USER app

# Healthcheck: confirm the DB file is reachable. Phase 5 may promote to a richer probe.
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD python -c "import os, sys; sys.exit(0 if os.path.exists(os.environ.get('DB_PATH','/data/metrics.db')) else 1)"

CMD ["python", "-m", "src"]
```

Create `docker-compose.yml` at repo root with EXACTLY this content:

```yaml
services:
  bot:
    build:
      context: .
      dockerfile: Dockerfile
    container_name: ads-reporting
    env_file:
      - .env
    volumes:
      - ./data:/data
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "python", "-c", "import os, sys; sys.exit(0 if os.path.exists(os.environ.get('DB_PATH','/data/metrics.db')) else 1)"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 20s
```

Create `README.md` at repo root with the following content (operator-focused, not marketing fluff):

```markdown
# Ads Reporting Agent

AI-powered conversational agent that pulls data from Meta Ads + GA4, posts auto-generated reports to Telegram, and lets allowlisted team members ask follow-up questions in natural language via Claude tool use.

> **Phase 1 (this release):** secure scaffold only — Telegram bot answers `/start`, `/status`, `/help`. Real ingestion + reports ship in Phase 2.

## Quick Start

### 1. Prerequisites

- Docker + Docker Compose (or local `uv` + Python 3.12 for non-containerized dev)
- A Telegram bot token from [@BotFather](https://t.me/BotFather)
- Your team's Telegram group chat ID (numeric, usually negative for groups)
- Your own Telegram user ID

**How to find your chat ID:** add [@userinfobot](https://t.me/userinfobot) to your group and DM it — it replies with both your user ID and the group's chat ID. Remove it after.

### 2. Configure

```bash
cp .env.example .env
# Open .env in your editor and fill in:
#   TELEGRAM_BOT_TOKEN=123456:ABC-...
#   TELEGRAM_ALLOWED_CHAT_IDS=-1001234567890        # the group chat id
#   TELEGRAM_ALLOWED_USER_IDS=123456789,987654321   # comma-separated
# Phase 2+ keys (META_*, GA4_*, ANTHROPIC_*) can stay blank for Phase 1.
```

### 3. Run

```bash
docker compose up --build
```

The bot will log `boot`, `storage_ready`, `webhook_cleared`, `scheduler_started`, `polling_start` and then wait for messages. Send `/start` in your allowlisted group; you should see:

> Ads Reporting Agent online. Use /report for latest data.

### 4. Verify

- `/start` — confirms the bot is alive
- `/status` — shows last sync timestamps and row counts (all zero in Phase 1)
- `/help` — lists commands

Non-allowlisted users get **no response** (silent drop by design — see Security below).

## Environment Variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | yes | — | Bot token from @BotFather |
| `TELEGRAM_ALLOWED_CHAT_IDS` | yes | empty | CSV of allowed chat IDs (groups, supergroups) |
| `TELEGRAM_ALLOWED_USER_IDS` | yes | empty | CSV of allowed user IDs (DMs) |
| `META_APP_ID`, `META_APP_SECRET`, `META_ACCESS_TOKEN`, `META_AD_ACCOUNT_ID` | Phase 2 | — | Meta Ads API |
| `GA4_PROPERTY_ID`, `GA4_SERVICE_ACCOUNT_JSON` | Phase 3 | — | Google Analytics 4 |
| `ANTHROPIC_API_KEY` | Phase 4 | — | Claude chat backend |
| `DB_PATH` | no | `/data/metrics.db` | SQLite file inside container |
| `LOG_LEVEL` | no | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `REPORT_TIMEZONE` | no | `UTC` | IANA TZ name (e.g. `America/New_York`) for scheduled jobs |

## Local Development (without Docker)

```bash
uv sync --extra dev
uv run python -m src
```

Run tests:

```bash
uv run pytest
```

## Security

The bot enforces a strict allowlist (chat ID OR user ID match) **before** any handler runs, including before any future Claude call. Non-allowlisted updates are silently dropped — they are not replied to (replying confirms the bot's existence to drive-by probers). This is enforced by `src/bot/middleware.py` (INFRA-02).

Secrets are loaded via `pydantic-settings` from environment variables / `.env`. The `.env` file is gitignored; only `.env.example` is committed. Logs go through a structlog redaction processor that strips fields named `token`, `secret`, `access_token`, `password`, `api_key`, `raw_response`, `ad_creative_body`, `email`, `phone` (INFRA-05).

## Deploy Targets

- **VPS:** `docker compose up -d` and mount a persistent host directory at `./data`.
- **Fly.io:** create a volume (`fly volumes create ads-data --size 1`), reference it in `fly.toml`, deploy. The Dockerfile is fly-compatible.
- **Railway:** create a service from this repo, set env vars in the dashboard, attach a persistent volume mounted at `/data`.

## Project Layout

```
src/
├── __main__.py          # entrypoint: asyncio.run(main())
├── main.py              # lifecycle wiring
├── config.py            # Settings (pydantic-settings)
├── logging_setup.py     # structlog JSON + redaction
├── bot/
│   ├── middleware.py    # AllowlistMiddleware (INFRA-02)
│   ├── handlers.py      # /start, /status, /help
│   └── setup.py         # create_bot_and_dispatcher
├── db/
│   ├── schema.py        # SQL DDL constants
│   ├── migrations.py    # hand-rolled migration runner
│   └── client.py        # async DB client with UPSERT helpers
└── scheduler/           # reserved for Phase 2 jobs
tests/                   # pytest (allowlist + UPSERT idempotency)
.planning/               # GSD planning artifacts (not shipped in image)
```

## Phase Roadmap

1. **Phase 1 (this):** Foundation + walking skeleton
2. **Phase 2:** Meta Ads ingestion, daily/weekly Telegram reports, alerts
3. **Phase 3:** GA4 ingestion, cross-source UTM join, attribution-honest reports
4. **Phase 4:** Conversational AI via Claude tool use
5. **Phase 5:** Hardening — Sentry, dead-man's-switch, backfill

See `.planning/ROADMAP.md` for full requirement traceability.
```

Implementation notes:
- The Dockerfile copies `uv.lock` conditionally (`uv.lock*`) so the build doesn't fail if the lockfile hasn't been generated yet on a clean checkout. Once `uv sync` has run once locally and `uv.lock` is committed, it's used; otherwise uv resolves from `pyproject.toml`.
- `python:3.12-slim-bookworm` (NOT alpine) per RESEARCH anti-pattern: musl libc breaks pandas wheels in Phase 2.
- `tzdata` apt-installed — required because slim images ship without `/usr/share/zoneinfo`, which `ZoneInfo("America/New_York")` reads.
- `VOLUME ["/data"]` declared so persistence survives `docker rm`.
- HEALTHCHECK is intentionally simple in Phase 1 (file existence); Phase 5 may promote to a scheduler-heartbeat probe.
- The compose file does NOT publish ports — bot uses outbound long-polling only. No public URL surface.
  </action>
  <verify>
    <automated>python -c "import pathlib, re; df = pathlib.Path('Dockerfile').read_text(); assert 'FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder' in df; assert 'FROM python:3.12-slim-bookworm AS runtime' in df; assert 'tzdata' in df; assert 'useradd' in df.lower() or 'USER app' in df; assert 'VOLUME [\"/data\"]' in df; assert 'HEALTHCHECK' in df; assert 'CMD [\"python\", \"-m\", \"src\"]' in df; dc = pathlib.Path('docker-compose.yml').read_text(); assert 'env_file' in dc and '.env' in dc; assert './data:/data' in dc; assert 'unless-stopped' in dc; rm = pathlib.Path('README.md').read_text(); assert 'Quick Start' in rm; assert 'TELEGRAM_BOT_TOKEN' in rm; assert 'docker compose up' in rm; assert 'allowlist' in rm.lower(); assert '@userinfobot' in rm or 'chat ID' in rm; print('OK')"</automated>
  </verify>
  <acceptance_criteria>
    - File `Dockerfile` exists at repo root
    - `grep -E 'FROM ghcr\.io/astral-sh/uv:python3\.12-bookworm-slim AS builder' Dockerfile` matches
    - `grep -E 'FROM python:3\.12-slim-bookworm AS runtime' Dockerfile` matches
    - `grep -E 'apt-get install.*tzdata' Dockerfile` matches (Pitfall 4 mitigation)
    - `grep -E 'useradd.*app' Dockerfile` matches AND `grep -E '^USER app' Dockerfile` matches (non-root runtime)
    - `grep -E 'VOLUME \["\/data"\]' Dockerfile` matches (Pitfall 7 mitigation)
    - `grep -E '^HEALTHCHECK' Dockerfile` matches
    - `grep -E 'CMD \["python", "-m", "src"\]' Dockerfile` matches
    - File `docker-compose.yml` exists at repo root
    - `grep -E 'env_file:' docker-compose.yml` matches; `grep -E '^\s*- \.env' docker-compose.yml` matches
    - `grep -E '\./data:/data' docker-compose.yml` matches
    - `grep -E 'restart: unless-stopped' docker-compose.yml` matches
    - File `README.md` exists at repo root
    - `grep -E '^## Quick Start' README.md` matches
    - `grep -E '^## Environment Variables' README.md` matches
    - `grep -E '^## Security' README.md` matches
    - `grep -E 'TELEGRAM_BOT_TOKEN' README.md` matches
    - `grep -E '@userinfobot|chat ID' README.md` matches (operator can find their IDs)
    - `grep -E 'docker compose up' README.md` matches
    - `python -c "import pathlib; assert 'allowlist' in pathlib.Path('README.md').read_text().lower()"` exits 0
  </acceptance_criteria>
  <done>Dockerfile builds a non-root, /data-volume-mounted, tzdata-included slim runtime image. docker-compose.yml mounts ./data, reads .env, restarts unless stopped. README explains every step an operator needs to go from zero to bot-online including how to find their chat ID and the security model. INFRA-04 deliverable is ready for `docker compose up`.</done>
</task>

</tasks>

<verification>
- `python -c "import tomllib, pathlib; tomllib.loads(pathlib.Path('pyproject.toml').read_text())"` exits 0 (sanity check on Plan 01 output)
- `python -m pytest -x` reports all tests passing (allowlist + upsert idempotency from Plans 02 and 03)
- `python -c "import pathlib, re; s = pathlib.Path('src/main.py').read_text(); ordered = [r'load_settings', r'configure_logging', r'db\.connect', r'create_bot_and_dispatcher', r'delete_webhook', r'AsyncIOScheduler', r'scheduler\.start', r'dp\.start_polling']; positions = [re.search(p, s).start() for p in ordered]; assert positions == sorted(positions), positions; print('order OK')"` exits 0
- `python -c "from src.main import main; from src.logging_setup import configure_logging; print('imports OK')"` exits 0
- `docker build -t ads-reporting:phase1 .` exits 0 (smoke-build; full container run is a human checkpoint deferred to end-of-phase)
- All files referenced in frontmatter `files_modified` exist on disk
</verification>

<success_criteria>
Phase 1 ships: running `docker compose up` with a valid `.env` produces a container that boots, applies migrations, registers the allowlist middleware, deletes any stale webhook, starts the scheduler, and starts long-polling. An allowlisted user sending `/start` receives "Ads Reporting Agent online. Use /report for latest data." in their Telegram group. Logs are JSON-formatted with secrets redacted. INFRA-04 (single deployable container) and INFRA-05 (structured logging without PII) are closed.

Combined with the earlier plans:
- INFRA-01 (env-based secrets) — closed by Plan 01
- INFRA-02 (allowlist enforced before handlers) — closed by Plan 03
- INFRA-03 (idempotent UPSERT) — closed by Plan 02
- INFRA-04 (single deployable container) — closed here
- INFRA-05 (structured logging, no PII) — closed here

All four ROADMAP Phase 1 success criteria are achievable from this codebase.
</success_criteria>

<output>
After completion, create `.planning/phases/01-foundation-walking-skeleton/01-entrypoint-docker-SUMMARY.md` describing:
- The exact lifecycle order in main.py with rationale for each step
- The redaction key list and where it lives in source
- Dockerfile design choices (uv builder, slim runtime, tzdata, non-root user, /data volume, healthcheck)
- The README sections and what each enables an operator to do
- Confirmation that all five INFRA-* requirements are closed across the four Phase 1 plans
</output>
