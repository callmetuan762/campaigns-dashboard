---
plan: 04-02
phase: 04-conversational-ai-recommendations
status: complete
duration: ~15 minutes
tasks: 2
files_created: 1
files_modified: []
tags: [ai, tools, anthropic, sqlite, security]
key-files:
  created:
    - src/ai/tools.py
key-decisions:
  - "Haiku 4.5 pricing set to $1.00/$5.00 per MTok (not $0.80/$4.00 which was Haiku 3.5)"
  - "dispatch_tool catches all exceptions and returns error strings so Claude can self-correct"
  - "Dynamic SQL columns (metric, order_col) use frozenset validation + # noqa: S608 comments"
requirements: [CHAT-02, CHAT-04, CHAT-05, CHAT-08, REC-01, REC-02, REC-03]
---

# Plan 04-02 Summary — AI Tools Module

## What was done

Created `src/ai/tools.py` (516 lines) — the complete Claude tool surface for the chat
orchestrator (04-03 plan).

**Module contents:**
- `_PRICING` dict with corrected Haiku 4.5 rate ($1.00/$5.00 per MTok) and `calculate_cost()`
  helper that falls back to Sonnet rate for unknown models (fails closed on budget tracking)
- Five frozenset allowlists (`_ALLOWED_METRICS`, `_ALLOWED_SOURCES`, `_ALLOWED_SORT_COLS`,
  `_META_METRICS`, `_GA4_METRICS`) guarding all SQL-dynamic parameters
- `TOOLS: list[dict]` — 5 Anthropic-format schemas with `input_schema`, `properties`,
  and `required` fields in exact API shape
- `dispatch_tool(name, tool_input, db)` — async router returning error strings (never raising)
  for unknown names and caught exceptions, enabling Claude self-correction
- 5 async tool functions backed by named-parameter SQLite queries:
  - `query_metrics` — Meta and/or GA4 aggregated metrics by date range
  - `compare_periods` — period-over-period AVG comparison for any allowed metric
  - `get_campaign_detail` — daily rows with LEFT JOIN GA4 side-by-side (never blended)
  - `list_underperformers` — HAVING avg < threshold, ordered ASC worst-first
  - `get_landing_page_performance` — ga4_landing_pages ranked by conversions or sessions

Security controls per CLAUDE.md and threat model T-04-02-01 through T-04-02-07:
- All user-supplied values reach SQL exclusively via named parameters (`:param`)
- Dynamic column names (metric, order_col) only after frozenset membership check
- `int(days_back)` coercion before formatting into `date('now', ...)` modifier
- Every tool output ends with `(Source: ...; as of ingest <fetched_at>)` citation

## Verification

All verify commands pass:

```
python -c "...assert _PRICING['claude-haiku-4-5']==(1.00, 5.00)..."  OK
python -c "...assert names==['query_metrics',...]..."                  OK
python -c "...assert _ALLOWED_SOURCES=={'meta','ga4','both'}..."      OK
python -c "...dispatch_tool('nonexistent', {}, None)..."              OK (returns error string)
python -c "...integration test — invalid inputs + empty DB..."        OK
pytest --collect-only -q                                              115 tests collected, no errors
```

## Deviations from Plan

None — plan executed exactly as written. Both Task 1 (skeleton) and Task 2 (implementations)
were written in a single file creation to keep the module coherent; the `# ---- Tool
implementations (Task 2) ----` marker is present at the correct boundary position.

## Threat Flags

None. All threat mitigations from the plan's STRIDE register (T-04-02-01 through T-04-02-07)
are implemented as specified.

## Self-Check: PASSED

- `src/ai/tools.py` exists: FOUND
- Commit 73426f0 exists: FOUND
- 516 lines (> min 300): PASS
- All 6 verify commands: PASS
- pytest collection (115 tests): PASS
