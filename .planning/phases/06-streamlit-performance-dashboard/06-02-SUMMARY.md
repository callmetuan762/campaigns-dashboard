---
phase: 06-streamlit-performance-dashboard
plan: "02"
subsystem: dashboard-ai-surface
tags: [streamlit, anthropic, sync, sqlite3, tools, chat, tdd]
dependency_graph:
  requires: [06-01]
  provides: [dashboard-tools-py, dashboard-chat-py]
  affects: [src/dashboard/tools.py, src/dashboard/chat.py]
tech_stack:
  added: []
  patterns: [sync-anthropic-client, contextmanager-conn, frozenset-allowlist, tdd-red-green]
key_files:
  created:
    - src/dashboard/tools.py
    - src/dashboard/chat.py
    - tests/test_dashboard_tools.py
    - tests/test_dashboard_chat.py
decisions:
  - key: TOOLS schema copied verbatim from src/ai/tools.py
    rationale: D-19 — same 5 tool names/descriptions/input_schemas sent to Anthropic API must match Telegram /ask behavior exactly
  - key: query_metrics uses SUM(spend*roas)/SUM(spend) weighted ROAS
    rationale: D-13 intentional divergence — matches builder.py and db.py so KPI cards agree with daily Telegram digest
  - key: _conn() context manager duplicated in both tools.py and chat.py
    rationale: D-19 standalone constraint forbids importing from other dashboard modules; 7-line duplication is acceptable and correct
  - key: Budget gate uses direct sqlite3 in chat.py (not DBClient)
    rationale: D-15/DASH-05 — no async DB client in sync Streamlit context; resolves Open Q1 from 06-RESEARCH.md
  - key: run_chat() returns (final_text, updated_history) tuple
    rationale: Streamlit session_state requires explicit assignment; caller owns the history lifecycle
metrics:
  duration: 333s
  tasks_completed: 2
  files_changed: 4
  completed_date: "2026-05-24"
---

# Phase 6 Plan 02: AI Surface (tools.py + chat.py) Summary

**One-liner:** Standalone sync Claude tool surface created — TOOLS schema verbatim copy, 5 sync sqlite3 tool implementations (weighted ROAS divergence), and sync Anthropic tool-use loop with budget gate, preserving all 4 invariants from src/ai/chat.py.

## What Was Done

### Task 1 — src/dashboard/tools.py: Standalone sync tool surface (TDD)

**RED:** Created `tests/test_dashboard_tools.py` with 15 test cases. All failed with `ModuleNotFoundError`.

**GREEN:** Created `src/dashboard/tools.py` with:
- `TOOLS` list: 5 entries copied verbatim from `src/ai/tools.py` (lines 74–175) — same `name`, `description`, `input_schema` for each of: `query_metrics`, `compare_periods`, `get_campaign_detail`, `list_underperformers`, `get_landing_page_performance`.
- Frozensets `_ALLOWED_METRICS`, `_ALLOWED_SOURCES`, `_ALLOWED_SORT_COLS`, `_META_METRICS`, `_GA4_METRICS` copied verbatim.
- `_conn()` context manager (duplicated from `db.py` — standalone constraint D-19).
- All 5 tool functions ported from async to sync: `async`/`await` removed, `DBClient` replaced with direct `sqlite3` connections via `_conn()`.
- **Intentional divergence:** `_QUERY_META_SQL` uses `CASE WHEN SUM(m.spend) > 0 THEN SUM(m.spend * m.roas) / SUM(m.spend) ELSE 0 END AS roas` — the weighted ROAS formula matching `builder.py` and `db.py` (D-13).
- All `ad_metrics` queries filter `WHERE m.ad_set_id = '' AND m.ad_id = ''` (D-12).
- `dispatch_tool()` sync router returning error strings (no raise) for self-correction.
- Zero imports from `src.ai`, `src.bot`, `aiogram`, `aiosqlite`, `asyncio`, `streamlit`.
- **15/15 tests pass.**

### Task 2 — src/dashboard/chat.py: Sync tool-use loop with budget gate (TDD)

**RED:** Created `tests/test_dashboard_chat.py` with 9 test cases. All failed with `ModuleNotFoundError`.

**GREEN:** Created `src/dashboard/chat.py` with:
- `run_chat(user_text, history, db_path, api_key, settings)` → `(final_text, updated_history)` using `anthropic.Anthropic` (sync, not AsyncAnthropic).
- **4 invariants preserved** from `src/ai/chat.py`:
  1. Full `response.content` appended as assistant turn.
  2. `tool_result` blocks FIRST in user-turn content list.
  3. Max 10 tool-use iterations (`_MAX_TOOL_ITERATIONS = 10`).
  4. Budget gate via `_get_monthly_anthropic_cost()` BEFORE any API call.
- `build_system_prompt(db_path)` includes: today's date, week/yesterday date ranges, Meta + GA4 data freshness, available campaign names, NSM context ("Cost per Deposit is the North Star Metric"), `<data>` sandboxing instruction, never-blend rule.
- `_log_anthropic_usage()` inserts one row to `anthropic_usage_log` per user turn (sum across iterations).
- `BUDGET_EXHAUSTED_USER_MSG` matches `src/ai/chat.py` wording exactly.
- **Deviation [Rule 1 - Bug]:** Docstring contained the literal string `"from src.ai"` which the forbidden-import test caught. Fixed by rephrasing docstring to `"no src.ai, no src.bot"`. No logic change.
- Zero imports from `src.ai`, `src.bot`, `aiogram`, `AsyncAnthropic`, `asyncio`, `streamlit`.
- **9/9 tests pass.**

## TOOLS Schema Verbatim Copy Confirmation

The `TOOLS` list in `src/dashboard/tools.py` is a character-for-character copy of `src/ai/tools.py` lines 74–175. The 5 tool entries have identical:
- `name` fields
- `description` fields (multi-line strings)
- `input_schema` objects (type, properties, required arrays)

No rewording was done. The Anthropic API receives the same tool definitions as the Telegram /ask command.

## One Intentional Divergence from src/ai/tools.py

`_QUERY_META_SQL` in `src/dashboard/tools.py` uses spend-weighted ROAS:

```sql
CASE WHEN SUM(m.spend) > 0
     THEN SUM(m.spend * m.roas) / SUM(m.spend)
     ELSE 0 END AS roas
```

vs `src/ai/tools.py` which uses `AVG(m.roas)`.

**Rationale:** D-13 — dashboard ROAS must match KPI cards (which use `builder.py`'s weighted formula) so the AI chat answer agrees with what the user sees on screen. The `compare_periods` and `list_underperformers` tools use `AVG({metric})` over a single column — switching them requires additional spend joins and is out of scope for Phase 6.

## Budget-Gate Parity (Resolves Open Q1 from 06-RESEARCH.md)

Open Q1 was: "Do we enforce the same $20/month budget ceiling in the dashboard as in Telegram?"

**Resolved:** Yes. `run_chat()` calls `_get_monthly_anthropic_cost(db_path)` before every API call using the same `anthropic_usage_log` table. When `monthly_spent >= settings.anthropic_monthly_budget_usd`, it returns `BUDGET_EXHAUSTED_USER_MSG` identical to the Telegram bot's message — no API call is made.

## 4 src/ai/chat.py Invariants Preserved in run_chat()

| Invariant | Implementation |
|-----------|----------------|
| 1. Full content as assistant turn | `messages.append({"role": "assistant", "content": response.content})` |
| 2. tool_result FIRST in user turn | `messages.append({"role": "user", "content": tool_results})` where tool_results is built first |
| 3. Max 10 iterations | `for _ in range(_MAX_TOOL_ITERATIONS): ... else: return truncation msg` |
| 4. Budget gate BEFORE API call | `_get_monthly_anthropic_cost()` checked before `client.messages.create()` |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Docstring contained forbidden import substring**
- **Found during:** Task 2 GREEN phase, test_module_has_no_forbidden_imports
- **Issue:** Module docstring said `"from src.ai.*"` — the string `"from src.ai"` appeared as a substring, causing the forbidden-import text check to fail
- **Fix:** Rephrased docstring to `"no src.ai, no src.bot"` — no logic change
- **Files modified:** `src/dashboard/chat.py` (docstring only)
- **Commit:** ac83726

## Verification Results

1. `pytest tests/test_dashboard_tools.py -x` — **15/15 passed**
2. `pytest tests/test_dashboard_chat.py -x` — **9/9 passed**
3. `python -c "from src.dashboard import tools, chat; print(len(tools.TOOLS))"` — **5**
4. Forbidden import grep — **CLEAN** (no matches)

## Next Plan

**06-03: Streamlit app (app.py)** — Overview page with KPI cards, dual-axis trend chart, attribution comparison chart, campaign table, auth gate, date range picker, and chat bar wiring `run_chat()` from this plan.

## Known Stubs

None.

## Threat Flags

No new threat surfaces beyond those in the plan's threat model (T-06-04 through T-06-08). All mitigations implemented: frozenset allowlists, dispatch_tool error strings, budget gate, `_MAX_TOOL_ITERATIONS`, api_key never logged.

## Self-Check: PASSED

- `src/dashboard/tools.py` exists: FOUND
- `src/dashboard/chat.py` exists: FOUND
- `tests/test_dashboard_tools.py` exists: FOUND
- `tests/test_dashboard_chat.py` exists: FOUND
- Commits 406729b (test RED tools), c4d9713 (feat GREEN tools), eedce11 (test RED chat), ac83726 (feat GREEN chat): all present in git log
- `len(tools.TOOLS) == 5`: VERIFIED
- Zero forbidden imports: VERIFIED
