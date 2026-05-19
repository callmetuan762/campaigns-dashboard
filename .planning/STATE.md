---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: milestone
current_phase: Phase 4 — Conversational AI + Recommendations
status: executing
last_updated: "2026-05-19T13:04:31.799Z"
progress:
  total_phases: 5
  completed_phases: 2
  total_plans: 19
  completed_plans: 21
  percent: 100
---

# Project State

**Project:** Ads Reporting Agent
**Current phase:** Phase 4 — Conversational AI + Recommendations
**Last updated:** 2026-05-19

## Project Reference

See: .planning/PROJECT.md
**Core value:** Marketing teams get actionable campaign and landing-page insights delivered proactively to Telegram and can interrogate the data in natural language.
**Current focus:** Phase 4 planned (6 plans, 3 waves) — ready to execute

## Phase Status

| Phase | Name | Status |
|-------|------|--------|
| 1 | Foundation & Walking Skeleton | Complete ✓ (2026-05-19) |
| 2 | Meta Ads Ingestion + Scheduled Reports + Alerts | Complete ✓ (2026-05-19) — 8 plans, 5 waves, 77 tests |
| 3 | GA4 Ingestion + Cross-Source Layer | Complete ✓ (2026-05-19) — 5 plans, 5 waves, 115 tests |
| 4 | Conversational AI + Recommendations | Planned ✓ (2026-05-19) — 6 plans, 3 waves, ready to execute |
| 5 | Hardening & Ops | Not started |

## Current Position

- **Phase:** Phase 4 — Conversational AI + Recommendations
- **Plan:** 04-04 complete; 04-05 next
- **Status:** Executing Phase 4 — 4/6 plans complete
- **Progress:** [████████░░] 72%

## Performance Metrics

- Phases completed: 3 / 5
- v1 requirements shipped: 38 / 38 (all v1 reqs in phases 1-4; phases 1-3 done)
- Phase 2 plans completed: 8 / 8 (02-01 foundation extension: 1m 44s, 2 tasks, 5 files; 02-02 meta client: 2m 17s, 1 task, 3 files; 02-03 report builders: 7m, 2 tasks, 6 files; 02-04 alert engine: 3m, 1 task TDD, 3 files; 02-05 meta ingest job: 2min, 1 task, 1 file; 02-06 report jobs: 2min, 2 tasks, 2 files; 02-07 scheduler wiring: 5min, 2 tasks, 2 files; 02-08 test suite: 3min, 2 tasks, 7 files, 43→77 tests)
- Phase 3 plans completed: 5 / 5 (03-01 foundation: schema+config; 03-02 GA4 package: client+ingest; 03-03 builder: GA4 section; 03-04 wiring: daily+weekly+main; 03-05 test suite: 77→115 tests)
- Phase 4 plans completed: 4 / 6 (04-01 foundation: anthropic_monthly_budget_usd setting + MIGRATION_004_PHASE4 + 5 DBClient methods + _deserialize_message; 1m 28s, 2 tasks, 3 files; 04-02 AI tools module: TOOLS list + 5 tool functions + dispatch_tool + calculate_cost + frozenset allowlists; ~15m, 2 tasks, 1 file; 04-03 chat.py: handle_chat_message + agentic loop + budget gate + tool dispatch; 2 tasks, 1 file; 04-04 chat_router.py: catch-all handler + inline keyboard + /clear + /help update; ~2m, 2 tasks, 2 files)

## Accumulated Context

### Decisions

- Telegram is the single delivery channel for v1 (no web UI, no email)
- Direct GA4 + Meta APIs (not Looker Studio scraping)
- Claude tool-use for conversational AI; no raw SQL exposed
- Read-only access to ad platforms
- SQLite as canonical metrics store with idempotent UPSERT
- Meta Ads MCP (2026-05-19): keep facebook-business SDK ingestion pipeline for scheduled reports; consider adding Meta MCP as an additional real-time tool in Phase 4 Claude tool surface
- asyncio.to_thread isolates synchronous facebook-business SDK from aiogram event loop
- purchase_roas parsed via _extract_action_value list pattern, not raw float cast (Meta API pitfall)
- matplotlib Agg backend + OO API (fig/ax/plt.close) for memory-safe charts in scheduler
- generate_tldr returns None (not raises) on Anthropic API errors — report job continues without TL;DR
- All campaign data in TL;DR prompt wrapped in <data>...</data> XML tags per CLAUDE.md prompt injection guardrail
- evaluate_alerts() exception-safe top-level try/except ensures alert failure never aborts meta_ingest_job
- Budget pacing alert (ALERT-04) uses days_elapsed < 7 guard to avoid false positives early in month
- COALESCE(SUM(cost_usd), 0.0) in get_monthly_anthropic_cost prevents NoneType budget-gate bug on empty table
- _deserialize_message is module-level (not class method) so DBClient methods call it without self. prefix; role='tool' remapped to 'user' for Anthropic API
- Haiku 4.5 pricing in tools.py: $1.00/$5.00 per MTok (corrected from erroneous $0.80/$4.00 Haiku 3.5 rate)
- dispatch_tool catches TypeError + Exception and returns error strings so Claude self-corrects without crashing the agentic loop
- Dynamic SQL columns in compare_periods/list_underperformers/get_landing_page_performance validated against frozensets before f-string; # noqa: S608 marks intentional dynamic columns
- get_conversation_history fetches DESC then calls rows.reverse() so LIMIT captures most-recent N turns and output is chronological
- Module-globals pattern for APScheduler: register_job_resources() called from main.py before scheduler.start() — avoids PicklingError with SQLAlchemyJobStore
- Module-globals pattern for APScheduler: register_job_resources() called before scheduler.add_job() — /report handler uses same globals set by main.py
- handle_chat_message deferred import inside handler bodies in chat_router.py — avoids import failure when chat.py is created concurrently in Wave 2 (04-03 and 04-04 are both Wave 2)
- show_chart button bypasses Claude entirely and delegates directly to generate_spend_trend_chart (D-16 — no Anthropic cost for chart requests)
- /clear scopes by BOTH chat_id AND user_id — in group chats, one user clearing must not wipe another user's thread (D-06)

### Phase 1 Decisions

- pydantic-settings v2.14 requires `str | list[int]` union type + validator branch for CSV env values (bare integer JSON handling)
- ad_metrics PK widened to (campaign_id, date, ad_set_id, ad_id) with NOT NULL DEFAULT '' sentinels — avoids costly table rebuild in Phase 2 META-03
- Table name allowlist frozenset added to DBClient.get_row_counts() to satisfy no-f-string-SQL rule
- ParseMode.MARKDOWN used for Phase 1 simplicity; Phase 2 replaced with ParseMode.HTML + html.escape() in /status handler (02-01)

### Open Questions (from research)

- Phase 2: Meta Standard tier access status? Ad-account timezone? Webhook vs long-polling for v1 deploy target? Report-failure fallback notification path?
- Phase 3: Is UTM tagging consistently applied to existing Meta campaigns?
- Phase 4: Monthly Anthropic budget ceiling? Haiku vs Sonnet trade-off for summaries? — RESOLVED: Sonnet for chat, $20/month ceiling with app-level enforcement
- Phase 5: Who is on the chat-ID allowlist? Retention window for raw API snapshots?

### Human UAT Outstanding

- 01-HUMAN-UAT.md: Docker build + bot liveness test (2 items pending)

### Todos

(none)

### Blockers

(none)

## Session Continuity

- Last action: Phase 4 plan 04-04 complete (2026-05-19) — chat_router.py + handlers.py /clear + /help, 2 tasks, 2 files, ~2m
- Stopped at: Phase 4 plan 04-04 complete; 04-05 next
- Resume file: None
