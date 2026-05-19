---
plan: 04-05
phase: 04-conversational-ai-recommendations
status: complete
duration: 103s
tasks: 2
files_modified: 3
tags: [wiring, setup, scheduler, integration]
subsystem: bot/reports
key-files:
  modified:
    - src/bot/setup.py
    - src/reports/daily.py
    - src/reports/weekly.py
decisions:
  - "Chat router included AFTER command router in setup.py to prevent F.text catch-all intercepting /commands (Pitfall 4)"
  - "dp['settings'] = settings injected at dispatcher init so chat_router handlers resolve settings: Settings parameter"
  - "generate_tldr called with db=db in both report jobs so TL;DR token usage is logged to anthropic_usage_log (Pitfall 8)"
metrics:
  completed: "2026-05-19"
dependency-graph:
  requires: ["04-03", "04-04"]
  provides: ["04-06"]
  affects: ["src/bot/setup.py", "src/reports/daily.py", "src/reports/weekly.py"]
tech-stack:
  patterns:
    - "Dispatcher workflow_data injection pattern: dp['key'] = value for handler parameter resolution"
    - "Router priority ordering: command router before catch-all to prevent filter shadowing"
---

# Plan 04-05 Summary — Wiring

## One-liner

Three additive wiring changes connecting Phase 4 chat/AI modules into the running dispatcher and scheduled report jobs.

## What was done

**Task 1 — src/bot/setup.py:**
- Added `from src.bot.chat_router import build_chat_router` import directly below the existing `build_router` import
- Added `dp["settings"] = settings` injection after `dp["db"] = db_client`, giving chat_router handlers access to Settings via parameter declaration
- Replaced the single `dp.include_router(build_router())` line with the two-router ordered block: command router first, chat router second (Pitfall 4 mitigation — prevents F.text catch-all shadowing /commands)
- Added `phase=4` key to the `logger.info("bot_dispatcher_ready", ...)` call

**Task 2 — src/reports/daily.py and src/reports/weekly.py:**
- daily.py: `generate_tldr(api_key, yesterday_rows, yesterday)` updated to `generate_tldr(api_key, yesterday_rows, yesterday, db=db)` — TL;DR usage now logged to anthropic_usage_log
- weekly.py: `generate_tldr(api_key, this_week_rows, f"week ending {week_end}")` updated with `db=db` — weekly TL;DR usage now counted against monthly budget ceiling (Pitfall 8 mitigated)
- No other changes to either file; heartbeat ordering, chart generation, and splitter wiring untouched

## Verification

- All verify commands pass
- pytest collection passes: 115 tests collected, no errors

## Commits

| Task | Hash | Description |
|------|------|-------------|
| 1 | 5e02d8e | feat(04-05): wire chat_router + settings injection in setup.py |
| 2 | 761fe55 | feat(04-05): pass db=db to generate_tldr in daily and weekly reports |

## Deviations from Plan

None - plan executed exactly as written.

## Threat Flags

None — no new network endpoints, auth paths, or schema changes introduced. dp["settings"] injection stays within the established workflow_data pattern already used for dp["db"].

## Self-Check: PASSED

- src/bot/setup.py: modified, committed at 5e02d8e
- src/reports/daily.py: modified, committed at 761fe55
- src/reports/weekly.py: modified, committed at 761fe55
- All verification commands confirmed OK
- pytest --collect-only: 115 tests collected, no errors
