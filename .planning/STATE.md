---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: milestone
current_phase: Phase 7 — Dashboard v2 + 3-Agent AI
status: 3-agent AI architecture delivered — MetaAgent + GA4Agent parallel fan-out, AttributionAgent reconciliation, run_chat_3agent
last_updated: "2026-05-24T15:30:00Z"
progress:
  total_phases: 7
  completed_phases: 5
  total_plans: 32
  completed_plans: 33
  percent: 100
---

# Project State

**Project:** Ads Reporting Agent
**Current phase:** Phase 6 — Streamlit Performance Dashboard
**Last updated:** 2026-05-24

## Project Reference

See: .planning/PROJECT.md
**Core value:** Marketing teams get actionable campaign and landing-page insights delivered proactively to Telegram and can interrogate the data in natural language.
**Current focus:** Phase 7 in progress. 3-agent AI architecture (07-04) complete; 277 tests passing.

## Phase Status

| Phase | Name | Status |
|-------|------|--------|
| 1 | Foundation & Walking Skeleton | Complete ✓ (2026-05-19) |
| 2 | Meta Ads Ingestion + Scheduled Reports + Alerts | Complete ✓ (2026-05-19) — 8 plans, 5 waves, 77 tests |
| 3 | GA4 Ingestion + Cross-Source Layer | Complete ✓ (2026-05-19) — 5 plans, 5 waves, 115 tests |
| 4 | Conversational AI + Recommendations | Complete ✓ (2026-05-19) — 6 plans, 3 waves, 156 tests |
| 5 | Hardening & Ops | Complete ✓ (2026-05-19) — 3 plans, 1 wave, 175 tests |
| 6 | Streamlit Performance Dashboard | Complete ✓ (2026-05-24) — 4 plans, 4 waves, 64 dashboard tests |
| 7 | Dashboard v2 + 3-Agent AI | In Progress (2026-05-24) — 2/6 plans complete |

## Current Position

- **Phase:** Phase 7 — Dashboard v2 + 3-Agent AI (In Progress)
- **Plan:** 07-04 complete (4/6 Phase 7 plans done)
- **Status:** 3-agent AI architecture delivered — MetaAgent + GA4Agent parallel fan-out, AttributionAgent reconciliation, Orchestrator, run_chat_3agent
- **Progress:** [██████████] 100%

## Performance Metrics

- Phases completed: 6 / 6
- v1 requirements shipped: 38 / 38 (all v1 reqs in phases 1-4; phases 1-3 done)
- Phase 2 plans completed: 8 / 8 (02-01 foundation extension: 1m 44s, 2 tasks, 5 files; 02-02 meta client: 2m 17s, 1 task, 3 files; 02-03 report builders: 7m, 2 tasks, 6 files; 02-04 alert engine: 3m, 1 task TDD, 3 files; 02-05 meta ingest job: 2min, 1 task, 1 file; 02-06 report jobs: 2min, 2 tasks, 2 files; 02-07 scheduler wiring: 5min, 2 tasks, 2 files; 02-08 test suite: 3min, 2 tasks, 7 files, 43→77 tests)
- Phase 3 plans completed: 5 / 5 (03-01 foundation: schema+config; 03-02 GA4 package: client+ingest; 03-03 builder: GA4 section; 03-04 wiring: daily+weekly+main; 03-05 test suite: 77→115 tests)
- Phase 4 plans completed: 6 / 6 (04-01 foundation: anthropic_monthly_budget_usd setting + MIGRATION_004_PHASE4 + 5 DBClient methods + _deserialize_message; 1m 28s, 2 tasks, 3 files; 04-02 AI tools module: TOOLS list + 5 tool functions + dispatch_tool + calculate_cost + frozenset allowlists; ~15m, 2 tasks, 1 file; 04-03 chat.py: handle_chat_message + agentic loop + budget gate + tool dispatch; 2 tasks, 1 file; 04-04 chat_router.py: catch-all handler + inline keyboard + /clear + /help update; ~2m, 2 tasks, 2 files; 04-05 wiring: chat_router + settings injected into dispatcher, db=db plumbed to generate_tldr; 103s, 2 tasks, 3 files; 04-06 test suite: 115→156 tests, 4 files, all 11 req IDs covered, Haiku pricing + loop cap regression-guarded; ~12m, 2 tasks, 4 files)
- Phase 5 plans completed: 3 / 3 (05-01 Sentry: sentry-sdk + Settings + init + 5 capture sites + test suite; 2m 32s, 3 tasks, 9 files, 156→160 tests; 05-02 Graceful degradation: builder flags + per-source daily/weekly refactor + test suite; ~12m, 3 tasks, 4 files, 160→167 tests; 05-03 Backfill CLI: ingest param extensions + public wrappers + src/backfill.py + test suite; 2m 33s, 3 tasks, 4 files, 167→175 tests)
- Phase 6 plans completed: 4 / 4 (06-01 db.py WAL scaffold: 4 tests, 191 total; 06-02 AI surface: tools.py + chat.py sync, 25 tests, 191→198 total; 06-03 app.py Streamlit Overview: auth + KPIs + charts + chat bar, 7 tests; 06-04 test pyramid closure: db + settings + charts + auth unit tests, 29 new tests, 64 dashboard tests total; ~3min, 3 tasks, 4 files)
- Phase 7 plans completed: 4 / 6 (07-01 data+config foundation: cpd_target field + get_campaign_daily query + .env.example doc, 9 new tests, 219→248 total; ~3min, 2 TDD tasks, 5 files; 07-02 TIER tags: _tier_tag pure function + COLOR_TIER_* constants + conditional TIER column, 12 new tests, 248→260 total; ~5min, 2 TDD tasks, 2 files; 07-03 campaign drill-down: pages/__init__.py + 1_Campaign_Detail.py + Overview nav, 260 tests pass, ~2min, 3 tasks, 3 files; 07-04 3-agent AI: MetaAgent + GA4Agent + AttributionAgent + Orchestrator + GA4_TOOLS + run_chat_3agent, 17 new tests, 260→277 total; ~15min, 3 TDD tasks, 6 files)

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
- Chat router included AFTER command router in setup.py to prevent F.text catch-all intercepting /commands (Pitfall 4)
- dp["settings"] = settings injected at dispatcher init so chat_router handlers resolve settings: Settings parameter
- generate_tldr called with db=db in daily + weekly report jobs so TL;DR token usage counted against monthly budget ceiling (Pitfall 8)
- WAL mode set idempotently on every _conn() open — protects dashboard against fresh DB not yet written by bot
- streamlit + plotly added as runtime (not dev) deps per D-03 in 06-CONTEXT.md
- dashboard/tools.py TOOLS schema copied verbatim from src/ai/tools.py — same Anthropic API call shape for /ask and dashboard chat
- dashboard/tools.py query_metrics uses SUM(spend*roas)/SUM(spend) weighted ROAS, not AVG — matches builder.py so KPI cards agree with AI chat answers (D-13)
- dashboard/chat.py uses sync anthropic.Anthropic() not AsyncAnthropic — Streamlit is sync (D-15)
- run_chat() budget gate reads anthropic_usage_log via sync sqlite3, same $20/month ceiling as Telegram /ask — resolves Open Q1 from 06-RESEARCH.md
- D-10 dark theme palette defined as module-level constants in app.py — single source of truth for all chart colors
- Cache wrappers live in app.py with str db_path for cache key stability — never in db.py
- test_app_first_streamlit_call_is_set_page_config uses tokenize module to skip docstring occurrences of st.*
- _tier_tag uses cpd_target=0.0 default to preserve Phase 6 7-column _format_campaign_df shape; PAUSED guard runs before CPD comparison
- Campaign Detail page re-declares palette constants (D-19 standalone rule) — each Streamlit page is an independent script; no cross-page imports
- Overview selectbox uses campaign_rows already fetched for table (no extra DB call); st.query_params + st.switch_page pattern for URL-shareable drill-down

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

### Phase 5 Decisions

- sentry_sdk.init() called inside async main() (after load_settings, before configure_logging) — AsyncioIntegration requires event loop to exist (Pitfall 1 avoided)
- sentry_sdk.capture_exception(exc) at all 5 outer catch-and-suppress sites; no per-call DSN guard needed (SDK is a no-op when uninitialized in 2.x)
- SENTRY_DSN stored as SecretStr; .get_secret_value() used only at init site; send_default_pii=False
- sentry_sdk.init() skipped entirely when SENTRY_DSN is absent — optional integration pattern
- Per-source graceful degradation: Meta and GA4 data queries in independent try/except blocks; each sets meta_available/ga4_available flag
- ingestion_log queried (source='meta_ads'|'ga4') to distinguish failed ingestion from zero-spend days — empty rows alone do NOT flag unavailability (Pitfall 5)
- ping_heartbeat stays inside outermost try block after Telegram send — never in finally (D-20 ordering invariant)
- Backfill suppresses alerts (suppress_alerts=True in run_meta_ingest_for_date) and bypasses GA4 6-hour cache (skip_cache=True in run_ga4_ingest_for_date)
- APScheduler job entry points unchanged — new params have defaults preserving existing behavior
- date.fromisoformat() in argparse __main__ block provides date validation fail-fast before any DB access
- Dead-man's-switch: no new code — ping_heartbeat already implemented; operator must configure external service (healthchecks.io) with HEARTBEAT_URL

### Phase 7 Decisions

- cpd_target default 0.0 in DashboardSettings — zero is the canonical "off" state; TIER tags hidden until operator sets a positive CPD target
- get_campaign_daily has no @st.cache_data wrapper — per Phase 6 architecture, cache lives in the consumer page (07-03 will add the wrapper)
- get_campaign_daily SQL parameter order: campaign_name first, then start_date, end_date — matches ? positions in WHERE clause, prevents param order bugs
- GA4_TOOLS = [get_landing_page_performance, ga4_query_metrics] — MetaAgent keeps all 5 TOOLS; ga4_query_metrics forces source=ga4 internally so schema has no source property (D-22, D-23)
- Lazy import of Orchestrator/BudgetExhaustedError inside run_chat_3agent() body — prevents circular import (agents.py imports from chat.py)
- Each agent creates its own anthropic.Anthropic() client instance per .run() call — enables clean mock patching per agent in unit tests
- AttributionAgent wraps user_text + meta_result + ga4_result in <data> tags (T-07-04-01, T-07-04-02 mitigations)
- Budget gate in Orchestrator.run() checked ONCE before ThreadPoolExecutor block — cannot be raced (D-19)
- 5 iterations max per agent (_AGENT_MAX_ITERATIONS=5) vs run_chat's 10 — tighter per-agent loop cap
- run_chat_3agent persists only final synthesized text into history (D-20) — agent-internal tool traces dropped to prevent context-window bloat

### Phase 6 Decisions

- test_dashboard_settings.py uses importlib.reload() to isolate env mutations — monkeypatch alone does not flush pydantic-settings cached module-level instance
- test_dashboard_charts.py uses _import_app() singleton guard to avoid double st.set_page_config() calls across test runs
- test_dashboard_auth.py catches all exceptions from _check_auth() outside AppTest context — treated as False return (gate blocked)
- Pre-existing failures in test_ai_chat.py, test_chat_router.py, test_meta_client.py, test_upsert_idempotency.py are from prior phases — deferred to future hardening

## Session Continuity

- Last action: Phase 7 plan 07-04 complete (2026-05-24) — 3-agent AI architecture (agents.py + GA4_TOOLS + run_chat_3agent), 277 tests passing
- Stopped at: 07-04 complete; next is 07-05 (dedicated AI Chat page or remaining Phase 7 plans)
- Resume file: None
