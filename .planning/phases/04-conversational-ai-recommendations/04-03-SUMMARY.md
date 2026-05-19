---
plan: 04-03
phase: 04-conversational-ai-recommendations
subsystem: ai/chat
status: complete
duration: 2 minutes
tasks: 2
files_created: 1
files_modified: 1
tags: [ai, chat, anthropic, tool-use, budget, conversation-persistence]
dependency_graph:
  requires: [04-01, 04-02]
  provides: [handle_chat_message, BUDGET_EXHAUSTED_USER_MSG, _SYSTEM_PROMPT]
  affects: [src/reports/daily.py (plan 04-05 wires db param)]
tech_stack:
  patterns: [tool-use loop, for-else iteration cap, message serialization, budget gate]
key_files:
  created:
    - src/ai/chat.py
  modified:
    - src/ai/tldr.py
decisions:
  - "system= argument used for _SYSTEM_PROMPT (not role:system in messages list) per Anthropic SDK v0.50+ convention"
  - "Budget gate fires before API key check so over-budget month always emits operator alert regardless of key config"
  - "User turn persisted before loop starts so /clear after mid-loop error still wipes the user message"
  - "Usage logged once per user turn summing total_input/total_output across all iterations"
  - "Operator alert destination is telegram_allowed_chat_ids[0] (Open Question #3 resolution)"
---

# Phase 4 Plan 03 Summary — Chat Orchestrator

## One-liner

Bounded Claude tool-use loop with budget gate, prompt-injection defense via `<data>` tags, multi-turn persistence, and per-turn usage logging.

## What was done

**Task 1 — src/ai/chat.py (created, 285 lines)**

Created the full Phase 4 chat orchestrator:

- `_CHAT_MODEL = "claude-sonnet-4-6"`, `_CHAT_MAX_TOKENS = 2048`, `_MAX_TOOL_ITERATIONS = 10`, `_HISTORY_LIMIT = 10`
- `BUDGET_EXHAUSTED_USER_MSG` constant for budget-exhaustion user-facing string
- `_SYSTEM_PROMPT` with citation directive, Meta-vs-GA4 signal distinction, and prompt-injection defense (`<data>` tag instruction)
- `_send_operator_budget_alert(bot, settings, monthly_spent)` — sends HTML alert to `telegram_allowed_chat_ids[0]`
- `_wrap_user_text(user_text)` — wraps text in `<data>...</data>` plus data-only instruction (D-18/CHAT-05)
- `_serialize_content(content)` — normalizes SDK content blocks for SQLite storage via `model_dump()`
- `handle_chat_message(user_text, chat_id, user_id, bot, db, settings) -> str` — the full tool-use loop:
  - Budget gate before every Claude call (CHAT-06/D-04)
  - API key check with informative error message
  - Conversation history load (D-06, D-07)
  - User turn persisted before loop (D-08)
  - Bounded `for _iteration in range(_MAX_TOOL_ITERATIONS): ... else:` loop
  - Typing indicator re-armed each iteration (D-11)
  - Full `response.content` appended as assistant turn (Pitfall 1 mitigation)
  - `tool_results` list is sole content of subsequent user turn (Pitfall 2 mitigation)
  - Both tool_use and tool_result turns persisted to `bot_conversations`
  - Final assistant text persisted as plain string
  - Usage logged once per user turn (summed across all iterations)
  - Exceptions: `APIStatusError`, `APIConnectionError` → user-facing message; bare `Exception` → error message; never raises

**Task 2 — src/ai/tldr.py (modified)**

Extended `generate_tldr` with optional `db` parameter:

- Added `from src.ai.tools import calculate_cost` and `from src.db.client import DBClient`
- Signature: `generate_tldr(api_key, campaign_rows, date, db=None)` — fully backward-compatible
- After successful API response, if `db is not None`: calculate cost via `calculate_cost(_TLDR_MODEL, ...)` and call `db.log_anthropic_usage(...)` wrapped in try/except (best-effort, never poisons report)
- Updated docstring to document D-04 behavior and backward-compatibility contract

## Verification

All verify commands pass:

```
python -c "from src.ai.chat import handle_chat_message, BUDGET_EXHAUSTED_USER_MSG, ..." -> OK
python -c "import inspect; ... assert params==['user_text','chat_id','user_id','bot','db','settings']..." -> OK
asyncio integration test: budget exhaustion path, operator alert sent -> OK
python -c "import inspect; from src.ai.tldr import generate_tldr; ... assert params == ['api_key', 'campaign_rows', 'date', 'db']..." -> OK
pytest --collect-only -q -> 115 tests collected, no errors
```

## Deviations from Plan

None — plan executed exactly as written. The module content in the plan's `<action>` was used verbatim.

## Threat Surface Scan

No new trust boundaries introduced beyond those in the plan's threat model (T-04-03-01 through T-04-03-08). All mitigations implemented as specified:

- User text wrapped in `<data>` tags (T-04-03-01)
- `_MAX_TOOL_ITERATIONS = 10` for-else cap (T-04-03-02)
- Monthly budget gate fires before every call (T-04-03-03)
- API key accessed via `SecretStr.get_secret_value()`, never logged (T-04-03-04)
- Operator alert uses `next(iter(settings.telegram_allowed_chat_ids), None)` (T-04-03-06)
- Full `response.content` / sole `tool_results` user turn patterns (T-04-03-07)
- All conversation turns persisted for audit trail (T-04-03-08)

## Self-Check: PASSED

- `src/ai/chat.py` exists and imports cleanly
- `src/ai/tldr.py` modified with correct signature
- Commits: `6a32dcf` (chat.py), `77adbb7` (tldr.py)
