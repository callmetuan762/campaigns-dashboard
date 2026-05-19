---
plan: 04-04
phase: 04-conversational-ai-recommendations
status: complete
duration: ~2 minutes
tasks: 2
files_created: 1
files_modified: 1
completed: 2026-05-19T13:03:50Z
tags: [bot, aiogram, callback, inline-keyboard, phase4]
key-decisions:
  - "Deferred import of handle_chat_message inside handler bodies (not module top-level) to avoid import failure when chat.py is created concurrently in Wave 2 (04-03)"
  - "callback.answer() placed as first await in handle_chat_action per Pitfall 3 (Telegram spinner dismissal)"
  - "show_chart bypasses Claude entirely — delegates directly to generate_spend_trend_chart (D-16)"
  - "/clear scopes by BOTH chat_id AND user_id to prevent cross-user history wipes in group chats"
key-files:
  created:
    - src/bot/chat_router.py
  modified:
    - src/bot/handlers.py
dependency-graph:
  requires:
    - src/db/client.py (clear_conversation, fetch_all)
    - src/reports/charts.py (generate_spend_trend_chart)
    - src/reports/splitter.py (split_html_message)
    - src/config.py (Settings)
  provides:
    - build_chat_router factory (phase4_chat Router)
    - ChatAction CallbackData
    - build_followup_keyboard
    - /clear command in handlers.py
  affects:
    - src/bot/setup.py (04-05 will wire chat_router in)
tech-stack:
  patterns:
    - aiogram CallbackData with prefix for typed callback payloads
    - InlineKeyboardBuilder.adjust(2) for 2-per-row layout
    - Deferred imports inside async handler bodies for Wave 2 safety
    - html.escape on AI response before Telegram send (defense-in-depth)
requirements: [CHAT-01, CHAT-07, CHAT-08]
---

# Plan 04-04 Summary — Chat Router

## What was done

**Task 1 — src/bot/chat_router.py (new file, 218 lines):**
- `ChatAction(CallbackData, prefix="chat")` with single `action: str` field; packed payload verified under 64 bytes
- `_ACTION_TO_TEXT` dict mapping drill_down/compare_week/why to canned user messages
- `_SHOW_CHART_SQL` named-parameter query for 7-day ad_metrics spend trend (no f-string SQL)
- `build_followup_keyboard()` producing 4 buttons in 2 rows via `builder.adjust(2)`: "Drill down", "Compare to last week", "Why is this happening?", "Show chart"
- `_send_chat_response()` applying html.escape then split_html_message, with keyboard attached to last part only
- `build_chat_router()` returning `Router(name="phase4_chat")` with:
  - `@router.message(F.text & ~F.text.startswith("/"))` catch-all text handler
  - `@router.callback_query(ChatAction.filter())` callback handler with `callback.answer()` as first await
  - `handle_chat_message` import deferred inside both handler bodies (Wave 2 safety)
  - show_chart branch delegates directly to `generate_spend_trend_chart` with no Claude call (D-16)

**Task 2 — src/bot/handlers.py (modified):**
- Added `/clear` command handler inside `build_router()`, before `return router`
- Per-user scoping: `db.clear_conversation(message.chat.id, user_id)` — never wipes another user's history
- Guards `message.from_user is not None` before touching DB
- Updated `/help` text to list `/clear` and replace Phase 2 footer with Phase 4 footer

## Verification

- All verify commands pass (keyboard shape + ChatAction round-trip + two-user scoping test)
- `python -c "import src.bot.chat_router"` — module imports cleanly
- pytest --collect-only -q: 115 tests collected, no errors

## Deviations from Plan

None — plan executed exactly as written.

## Threat Surface Scan

No new network endpoints, auth paths, or schema changes introduced. AllowlistMiddleware inherited from Phase 1 setup.py. html.escape applied to AI output (T-04-04-04 mitigated). callback.answer() first await (T-04-04-07 mitigated). /clear scopes by both chat_id and user_id (T-04-04-05 mitigated).

## Known Stubs

None. chat_router is not yet wired into setup.py — that is plan 04-05 (by design, not a stub).

## Self-Check: PASSED

- src/bot/chat_router.py: FOUND
- src/bot/handlers.py: FOUND (modified)
- Commit 4caf2a8: feat(04-04): create chat_router.py
- Commit 834f13f: feat(04-04): add /clear command and update /help text
