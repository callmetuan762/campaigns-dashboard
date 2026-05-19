---
phase: 1
plan: "01-telegram-bot"
subsystem: bot
tags: [security, allowlist, middleware, telegram, aiogram, infra-02]
dependency_graph:
  requires: ["01-scaffold", "01-database"]
  provides: ["src.bot.setup.create_bot_and_dispatcher"]
  affects: ["01-main"]
tech_stack:
  added: []
  patterns: ["aiogram BaseMiddleware", "aiogram workflow_data injection", "structlog structured logging"]
key_files:
  created:
    - src/bot/middleware.py
    - src/bot/handlers.py
    - src/bot/setup.py
    - tests/test_allowlist.py
  modified: []
decisions:
  - "OR semantics for allowlist: allowed if chat_id in allowed_chats OR user_id in allowed_users (research assumption A1 — team group trust boundary + individual DM support)"
  - "Silent drop (no reply) for non-allowlisted updates — replying confirms bot existence to probers"
  - "ParseMode.MARKDOWN (not MARKDOWN_V2) for Phase 1 simplicity; Phase 2 will add escape_md_v2 helper"
  - "Middleware registered before include_router in source order — enforced structurally, not by convention"
  - "DBClient injected via dp['db'] / dispatcher.workflow_data for clean handler signature injection"
metrics:
  duration: "135 seconds"
  completed: "2026-05-19T07:01:25Z"
  tasks_completed: 2
  tasks_total: 2
  files_created: 4
  files_modified: 0
---

# Phase 1 Plan 01-telegram-bot: Telegram Bot Subsystem Summary

AllowlistMiddleware with OR semantics (INFRA-02), /start /status /help handlers, and create_bot_and_dispatcher factory with security-critical middleware-before-router ordering — 4 tests prove non-allowlisted updates are dropped before any handler runs.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | AllowlistMiddleware with OR semantics and structlog rejection logging | 3a2562c | src/bot/middleware.py, tests/test_allowlist.py |
| 2 | /start, /status, /help handlers and create_bot_and_dispatcher factory | 1051173 | src/bot/handlers.py, src/bot/setup.py |

## What Was Built

### AllowlistMiddleware (src/bot/middleware.py)

`AllowlistMiddleware(BaseMiddleware)` implements the project's #1 security control. Key behaviors:

- **OR semantics:** An update is allowed if `chat_id in allowed_chats OR user_id in allowed_users`. This design (research assumption A1) makes the Telegram group the trust boundary while also permitting DMs from specifically allowlisted individual users.
- **Silent drop:** Non-allowlisted updates return `None` immediately — aiogram 3 short-circuits dispatch on `None` return from middleware, so no handler ever runs. No reply is sent (replying would confirm bot existence to probers).
- **Structured log:** Rejection calls `logger.info("rejected_update", chat_id=..., user_id=..., event_type=...)` — only IDs and the event class name. `message.text` is never read or logged.
- **Two observer registrations:** Both `dp.message.middleware(allowlist)` and `dp.callback_query.middleware(allowlist)` are required — omitting callback_query would leave inline keyboard interactions unprotected.

### Command Handlers (src/bot/handlers.py)

`build_router() -> Router` registers three commands on a named `phase1_commands` Router:

- `/start` (CommandStart): answers `"Ads Reporting Agent online. Use /report for latest data."`
- `/status` (Command("status")): calls `db.get_last_sync()` and `db.get_row_counts()`, formats a Markdown status message with last sync times and row counts for all four tables
- `/help` (Command("help")): returns formatted command list with a note that more commands ship in Phase 2

The `/status` handler uses aiogram's workflow_data injection: declaring `db: DBClient` in the signature causes aiogram to inject `dp["db"]` automatically — no global state or explicit lookup required.

### Factory (src/bot/setup.py)

`create_bot_and_dispatcher(settings: Settings, db_client: DBClient) -> tuple[Bot, Dispatcher]`:

1. Creates `Bot` with `DefaultBotProperties(parse_mode=ParseMode.MARKDOWN)`
2. Creates `Dispatcher()`
3. Injects `dp["db"] = db_client`
4. Registers `AllowlistMiddleware` on `dp.message.middleware` AND `dp.callback_query.middleware`
5. THEN calls `dp.include_router(build_router())`

The middleware-before-router ordering is enforced structurally in source (not by convention or documentation alone). The factory is side-effect-free — it does not call `bot.delete_webhook()` or start polling, leaving those lifecycle responsibilities to `src/main.py` (Plan 04).

### Test Suite (tests/test_allowlist.py)

Four tests prove INFRA-02:

| Test | What It Proves |
|------|----------------|
| `test_disallowed_chat_dropped` | Non-allowlisted update returns None AND handler is never invoked |
| `test_allowed_chat_passes` | chat_id match alone grants access (OR semantics) |
| `test_allowed_user_passes` | user_id match alone grants access even with unknown chat (OR semantics) |
| `test_message_text_not_logged` | Rejection log never contains the message text sentinel |

Tests use synthetic `Message` objects constructed from aiogram type primitives — no live Telegram connection required.

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

None — all three handlers are fully wired. `/status` pulls live data from DBClient. No hardcoded placeholders or TODO markers.

## Threat Flags

None — no new network endpoints, auth paths, or trust boundary surface introduced beyond what the plan specifies. The allowlist middleware closes INFRA-02.

## Self-Check: PASSED

| Check | Result |
|-------|--------|
| src/bot/middleware.py exists | FOUND |
| src/bot/handlers.py exists | FOUND |
| src/bot/setup.py exists | FOUND |
| tests/test_allowlist.py exists | FOUND |
| Commit 3a2562c exists | FOUND |
| Commit 1051173 exists | FOUND |
