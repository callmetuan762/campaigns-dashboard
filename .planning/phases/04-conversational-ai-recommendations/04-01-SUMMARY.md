---
plan: 04-01
phase: 04-conversational-ai-recommendations
status: complete
duration: 1m 28s
tasks: 2
files_modified: 3
tags: [foundation, schema, sqlite, anthropic-budget]
requirements: [CHAT-03, CHAT-06]
key-files:
  modified:
    - src/config.py
    - src/db/schema.py
    - src/db/client.py
decisions:
  - "Used COALESCE(SUM(cost_usd), 0.0) so empty-table returns 0.0 (not None) to prevent budget-gate NoneType bugs"
  - "_deserialize_message is a module-level free function (not a method) to allow DBClient.get_conversation_history to call it without self. prefix"
  - "get_conversation_history fetches ORDER BY created_at DESC then calls rows.reverse() so the LIMIT applies to most-recent rows and result is chronological for Anthropic messages array"
metrics:
  completed: 2026-05-19T12:57:29Z
---

# Phase 4 Plan 01 Summary — Phase 4 Foundation

## What was done

- Added `anthropic_monthly_budget_usd: float = 20.0` to `Settings` in `src/config.py` — no validator needed (pydantic-settings handles float natively).
- Added `MIGRATION_004_PHASE4` to `src/db/schema.py` introducing the `anthropic_usage_log` table (7-column shape: id, request_at, model, input_tokens, output_tokens, cost_usd, chat_id, user_id) and the `idx_usage_log_month` index. Registered as the 4th tuple in `ALL_MIGRATIONS`.
- Added `import json` to `src/db/client.py`.
- Added module-level `_deserialize_message(role, raw_message) -> dict` free function — maps `role='tool'` to API `role='user'`, tries `json.loads`, falls back to raw string on `JSONDecodeError`/`ValueError` or when `json.loads` returns a plain `str`.
- Added 5 new async methods to `DBClient` with `_UPPER_SQL` class-attribute SQL constants (all named-parameter, no f-string SQL):
  - `log_anthropic_usage` — inserts into `anthropic_usage_log`
  - `get_monthly_anthropic_cost` — `COALESCE(SUM(cost_usd), 0.0)` for current calendar month
  - `get_conversation_history` — DESC fetch + `rows.reverse()` for chronological Anthropic API order
  - `save_conversation_turn` — inserts a single `bot_conversations` row
  - `clear_conversation` — deletes all rows for `(chat_id, user_id)`

## Verification

- All automated verify commands pass:
  - `Settings.anthropic_monthly_budget_usd == 20.0` ✓
  - `ALL_MIGRATIONS[-1] == ('004_phase4', MIGRATION_004_PHASE4)` and `len(ALL_MIGRATIONS) == 4` ✓
  - `_deserialize_message` unit tests (tool-role mapping, JSON list parse, quoted-string fallback) ✓
  - DBClient method membership check (all 5 present) ✓
  - Integration test: empty-table returns 0.0, insert+sum, conversation round-trip, clear ✓
  - All 4 migrations applied cleanly to fresh DB ✓
- pytest collection passes, 115 tests collected, no errors ✓

## Deviations from Plan

None — plan executed exactly as written.

## Threat Surface Scan

No new network endpoints, auth paths, file access patterns, or schema changes at external trust boundaries introduced. The `anthropic_usage_log` table and new DBClient methods are internal storage primitives with no direct user-input-to-SQL path (all SQL constants, no f-string SQL).

## Self-Check

- `src/config.py` modified: FOUND ✓
- `src/db/schema.py` modified: FOUND ✓
- `src/db/client.py` modified: FOUND ✓
- Task 1 commit `3f8da11`: FOUND ✓
- Task 2 commit `57682dc`: FOUND ✓

## Self-Check: PASSED
