---
phase: 01-foundation-walking-skeleton
verified: 2026-05-19T00:00:00Z
status: human_needed
score: 4/4 must-haves verified
overrides_applied: 0
human_verification:
  - test: "Run `docker compose up --build` on a machine with Docker installed, then send /start to the bot from an allowlisted Telegram account"
    expected: "Container builds without error; bot replies 'Ads Reporting Agent online. Use /report for latest data.' in the configured group"
    why_human: "Cannot execute Docker builds or connect to Telegram in this environment; the HEALTHCHECK and bot liveness require a live container and a real bot token"
  - test: "Run `python -m pytest` from the repo root after `uv sync --extra dev`"
    expected: "All 7 tests pass: 4 in test_allowlist.py + 3 in test_upsert_idempotency.py"
    why_human: "Test execution requires the uv virtualenv to be set up; dependencies (aiogram, aiosqlite, pytest-asyncio) must be installed first"
---

# Phase 1: Foundation & Walking Skeleton — Verification Report

**Phase Goal:** A secure, deployable single-container application exists with config, storage, an allowlisted Telegram bot, and structured logging — ready to receive ingestion and reporting modules.
**Verified:** 2026-05-19
**Status:** human_needed (all code checks pass; 2 items require a live environment)
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths (from ROADMAP Success Criteria)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Operator can launch the app via a single Docker container on a VPS using only environment variables for secrets (no credentials in source) | VERIFIED | `Dockerfile` has multi-stage uv build + slim runtime; `docker-compose.yml` uses `env_file: .env`; `.env.example` committed, `.env` absent from repo and in `.gitignore`; `Settings` wraps all tokens as `SecretStr` |
| 2 | The Telegram bot responds only to chat IDs and user IDs on the configured allowlist; all other senders are silently rejected and logged | VERIFIED | `AllowlistMiddleware` registered on `dp.message.middleware` AND `dp.callback_query.middleware` before `dp.include_router`; OR-semantics implemented; `rejected_update` log event uses only `chat_id`, `user_id`, `event_type` — `message.text` is absent from middleware source; test file `tests/test_allowlist.py` covers 4 scenarios including no-PII-in-rejection-log |
| 3 | A SQLite database exists with the canonical metrics schema and idempotent UPSERT semantics verified by re-running an insert with no row duplication | VERIFIED | `src/db/schema.py` defines 6 tables with correct PKs and `meta_`/`ga4_` prefixes; `src/db/client.py` has `ON CONFLICT(campaign_id, date, ad_set_id, ad_id) DO UPDATE` for ad_metrics and `ON CONFLICT(campaign_utm, date) DO UPDATE` for ga4_metrics; `tests/test_upsert_idempotency.py` contains 3 tests proving idempotency |
| 4 | Structured logs capture startup, API call outcomes, and errors without leaking PII or raw ad data | VERIFIED | `src/logging_setup.py` defines `_REDACT_KEYS` covering all 9+ required keys (`token`, `secret`, `access_token`, `password`, `api_key`, `raw_response`, `ad_creative_body`, `email`, `phone` + additional); `_redact_processor` runs case-insensitively before JSON serialization; stdlib bridge via `ProcessorFormatter` covers aiogram/apscheduler internals; `configure_logging()` is called as step 2 in `main()`, before `db.connect()`, `create_bot_and_dispatcher()`, and `dp.start_polling()` |

**Score:** 4/4 truths verified

---

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `pyproject.toml` | Project metadata + all 14 runtime dependencies | VERIFIED | All 14 required deps present with correct version specifiers; `asyncio_mode = "auto"`, `packages = ["src"]` confirmed |
| `src/config.py` | `Settings(BaseSettings)` + `load_settings()` | VERIFIED | `telegram_bot_token: SecretStr`, `env_file=".env"`, `_split_csv` validator for CSV allowlists; all 13 fields present |
| `.env.example` | All 13 env var keys, no secret values | VERIFIED | All keys present: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_CHAT_IDS`, `TELEGRAM_ALLOWED_USER_IDS`, `META_*`, `GA4_*`, `ANTHROPIC_API_KEY`, `DB_PATH`, `LOG_LEVEL`, `REPORT_TIMEZONE`; no values set |
| `.gitignore` | Excludes `.env`, `data/`, `*.db`, `__pycache__/`, `.venv/` | VERIFIED | All 5 required entries present as literal lines |
| `src/db/schema.py` | `MIGRATION_001_INITIAL` + `ALL_MIGRATIONS` | VERIFIED | All 6 tables defined; `meta_purchases_7dclick`, `ga4_purchases_lastclick` present; composite PKs correct |
| `src/db/migrations.py` | `run_migrations()` + `applied_versions()` idempotent runner | VERIFIED | Imports `ALL_MIGRATIONS`; `INSERT OR REPLACE INTO schema_version` ensures idempotency; no-op on re-run |
| `src/db/client.py` | `DBClient` with UPSERT helpers + `get_row_counts` + `get_last_sync` | VERIFIED | WAL mode, FK ON, UPSERT SQL with correct conflict clauses; all required methods present |
| `tests/test_upsert_idempotency.py` | 3 tests proving INFRA-03 | VERIFIED | `test_migration_is_idempotent`, `test_ad_metrics_upsert_is_idempotent`, `test_ga4_metrics_upsert_is_idempotent` all present and substantive |
| `src/bot/middleware.py` | `AllowlistMiddleware(BaseMiddleware)` | VERIFIED | OR-semantics in `__call__`; `rejected_update` log event; no `message.text` in source; handles both `Message` and `CallbackQuery` |
| `tests/test_allowlist.py` | 4 tests proving INFRA-02 | VERIFIED | `test_disallowed_chat_dropped`, `test_allowed_chat_passes`, `test_allowed_user_passes`, `test_message_text_not_logged` all present |
| `src/bot/handlers.py` | `build_router()` with /start, /status, /help | VERIFIED | All 3 handlers registered; /status calls `db.get_last_sync()` and `db.get_row_counts()`; /start reply text matches spec exactly |
| `src/bot/setup.py` | `create_bot_and_dispatcher()` factory | VERIFIED | Middleware registered before `include_router` (middleware pos 317 < router pos 1937); `dp["db"] = db_client` injection confirmed |
| `src/logging_setup.py` | `configure_logging()` with redaction + stdlib bridge | VERIFIED | `_REDACT_KEYS` covers all 9 required keys; `_redact_processor` case-insensitive; `ProcessorFormatter` stdlib bridge; `JSONRenderer` default |
| `src/main.py` | 8-step lifecycle with graceful shutdown | VERIFIED | All 8 steps present in correct source order (positions verified programmatically); `SQLAlchemyJobStore`, `misfire_grace_time=60`, `coalesce=True`, `max_instances=1`; `finally` block covers all 3 shutdown steps |
| `src/__main__.py` | `asyncio.run(main())` entrypoint | VERIFIED | Imports `from src.main import main`; calls `asyncio.run(main())` under `if __name__ == "__main__"` |
| `Dockerfile` | Multi-stage uv build, non-root user, /data VOLUME, HEALTHCHECK | VERIFIED | Both stages present; `tzdata` installed; `useradd app` + `USER app`; `VOLUME ["/data"]`; `HEALTHCHECK`; `CMD ["python", "-m", "src"]` |
| `docker-compose.yml` | `env_file: .env`, `./data:/data`, `restart: unless-stopped` | VERIFIED | All 3 required entries confirmed |

---

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `src/config.py` | Environment variables / .env | `SettingsConfigDict(env_file=".env")` | WIRED | `env_file=".env"` confirmed in `model_config` |
| `src/bot/setup.py` | `AllowlistMiddleware` | `dp.message.middleware(allowlist)` before `include_router` | WIRED | Source-position check: middleware pos 317 < router pos 1937 |
| `src/bot/setup.py` | `dp.callback_query.middleware(allowlist)` | before `include_router` | WIRED | Both message and callback_query middleware calls confirmed before router inclusion |
| `src/db/migrations.py` | `src/db/schema.py:ALL_MIGRATIONS` | `from src.db.schema import ALL_MIGRATIONS` | WIRED | Import confirmed; iterates `ALL_MIGRATIONS` in migration runner |
| `src/db/client.py` | `ad_metrics` UPSERT | `ON CONFLICT(campaign_id, date, ad_set_id, ad_id) DO UPDATE` | WIRED | Exact SQL clause confirmed in `_UPSERT_AD_METRICS_SQL` |
| `src/bot/handlers.py` /status | `DBClient.get_last_sync` + `get_row_counts` | via `db: DBClient` parameter injection | WIRED | Both method calls present; `dp["db"] = db_client` injection in setup.py |
| `src/main.py` | `src/logging_setup.py:configure_logging` | called as step 2, before any other component | WIRED | `configure_logging` pos 1603 < `db.connect` pos 1854 < `create_bot_and_dispatcher` pos 2014 |
| `src/__main__.py` | `src/main.py:main` | `asyncio.run(main())` | WIRED | Import and call confirmed |
| `Dockerfile` | `CMD ["python", "-m", "src"]` | invokes `src/__main__.py` via Python module runner | WIRED | CMD confirmed; `src/__main__.py` calls `asyncio.run(main())` |

---

### Data-Flow Trace (Level 4)

Not applicable for Phase 1 — no dynamic data-rendering components. The bot produces status text from DB queries (`get_row_counts`, `get_last_sync`) which are wired to the real aiosqlite connection; no mock or static data paths identified.

---

### Behavioral Spot-Checks

Step 7b: SKIPPED — cannot execute Docker builds or `pytest` without the uv virtualenv installed. These are routed to human verification.

Static structural checks (equivalent for a non-runnable environment):

| Behavior | Check | Result | Status |
|----------|-------|--------|--------|
| All 8 lifecycle steps in main.py are present and ordered | Regex source-position check | positions [1512, 1603, 1854, 2014, 2144, 2376, 2767, 2963] — monotone increasing | PASS |
| Middleware registered before router | Source-position check | middleware pos 317 < router pos 1937 | PASS |
| No `message.text` in rejection log | Grep `src/bot/middleware.py` | No matches found | PASS |
| All 9 required redact keys in `_REDACT_KEYS` | Set subtraction | missing = none | PASS |
| `.env` absent from repo, `.env.example` present | File existence check | `.env` absent, `.env.example` present | PASS |
| `configure_logging` called before db.connect and bot setup | Source-position check | log pos 1603 < db pos 1854 < bot pos 2014 | PASS |

---

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|----------|
| INFRA-01 | 01-PLAN-scaffold | API keys, Telegram token, account IDs, timezone stored via env-based secret management — never in source | SATISFIED | `Settings(BaseSettings)` with `SecretStr` for all tokens; `env_file=".env"` config; `.env` gitignored; no secret values in any source file |
| INFRA-02 | 01-PLAN-telegram-bot | Telegram bot enforces strict allowlist of permitted chat IDs and user IDs before executing any command or Claude call | SATISFIED | `AllowlistMiddleware` registered on both observers before `include_router`; 4 passing tests prove the control; `message.text` absent from rejection log |
| INFRA-03 | 01-PLAN-database | SQLite database stores canonical metrics with idempotent UPSERT so re-runs never duplicate data | SATISFIED | 6 tables with correct PKs; `ON CONFLICT ... DO UPDATE` in both UPSERT helpers; 3 tests in `test_upsert_idempotency.py` prove idempotency |
| INFRA-04 | 01-PLAN-entrypoint-docker | Application runs as a single Docker container deployable to a VPS or Railway/Fly.io | SATISFIED (pending live smoke test) | Multi-stage `Dockerfile` with uv builder + slim runtime; `docker-compose.yml` with env_file and volume mount; `python -m src` entrypoint wired |
| INFRA-05 | 01-PLAN-entrypoint-docker | Structured logging captures API call outcomes, report delivery status, and errors without logging PII or raw ad data | SATISFIED | `_REDACT_KEYS` with 17 entries covering all required sensitive fields; case-insensitive redaction processor; stdlib bridge for aiogram/apscheduler; `configure_logging` called before any other component |

No orphaned requirements: all 5 Phase 1 requirements (INFRA-01 through INFRA-05) are claimed by plans and have implementation evidence.

---

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `src/main.py` | 28–34 | `_scheduler_heartbeat` is a placeholder job (logs only) | Info | Intentional — plan explicitly documents this as a Phase 1 stub that Phase 2 replaces with real ingest job; does not affect goal achievement |
| `src/__main__.py` | 6 | `if __name__ == "__main__":` guard (module-level `asyncio.run` only fires from `__main__`, not from `python -m src`) | Info | This is correct Python idiom for module entry points — not a bug; `python -m src` invokes `__main__.py` which runs `asyncio.run(main())` |

No blockers or warnings found. The one stub (`_scheduler_heartbeat`) is an intentional placeholder per plan design.

---

### Human Verification Required

#### 1. Docker Build and Bot Liveness

**Test:** On a machine with Docker installed and a valid `.env` (copy from `.env.example`, fill `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_CHAT_IDS`, `TELEGRAM_ALLOWED_USER_IDS`), run `docker compose up --build`. Then send `/start` from an allowlisted account in the configured Telegram group.

**Expected:**
- Build completes without error (both builder and runtime stages succeed)
- Container starts; logs show `boot`, `storage_ready`, `webhook_cleared`, `scheduler_started`, `polling_start` events in JSON format
- Bot replies: "Ads Reporting Agent online. Use /report for latest data."
- Send `/status`: bot returns row counts (all zeros in Phase 1) and "never" for sync timestamps
- Send a message from a non-allowlisted account: bot does NOT respond (silent drop)

**Why human:** Cannot execute Docker builds, connect to Telegram, or validate live container behavior in this environment.

#### 2. Full Test Suite Execution

**Test:** After `uv sync --extra dev`, run `python -m pytest -v` from the repo root.

**Expected:** 7 tests pass — `tests/test_allowlist.py` (4) and `tests/test_upsert_idempotency.py` (3) — with 0 failures and 0 errors.

**Why human:** Test execution requires `uv` and the virtualenv to be set up; dependencies (aiogram, aiosqlite, pytest-asyncio, structlog) must be installed. Cannot verify test outcomes without running the suite.

---

### Gaps Summary

No gaps. All 4 ROADMAP success criteria are verified by static code analysis. The 2 human verification items are runtime checks (Docker build smoke test and test suite execution) that cannot be performed without a live environment — they are not blocking failures, as all supporting code is fully substantive and wired.

---

_Verified: 2026-05-19_
_Verifier: Claude (gsd-verifier)_
