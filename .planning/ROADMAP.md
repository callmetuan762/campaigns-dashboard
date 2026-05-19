# Roadmap: Ads Reporting Agent

**Version:** 1.0
**Total phases:** 5
**Requirements:** 38 v1 requirements
**Granularity:** coarse (5 phases)
**Last updated:** 2026-05-19

## Phases

- [x] **Phase 1: Foundation & Walking Skeleton** - Secure, deployable scaffold with config, storage, allowlisted Telegram bot, and structured logging — completed 2026-05-19
- [ ] **Phase 2: Meta Ads Ingestion + Scheduled Reports + Alerts** - Daily/weekly Meta-driven Telegram reports with charts, heartbeat, and alert engine
- [ ] **Phase 3: GA4 Ingestion + Cross-Source Layer** - GA4 metrics joined to Meta via UTM with side-by-side attribution and coverage warnings
- [ ] **Phase 4: Conversational AI + Recommendations** - Claude tool-use chat with multi-turn context, guardrails, and evidence-backed optimization advice
- [ ] **Phase 5: Hardening & Ops** - (Reserved for post-v1 hardening; all 38 v1 requirements ship in phases 1-4)

## Phase Details

### Phase 1: Foundation & Walking Skeleton
**Goal:** A secure, deployable single-container application exists with config, storage, an allowlisted Telegram bot, and structured logging — ready to receive ingestion and reporting modules.
**Depends on:** Nothing (first phase)
**Requirements:** INFRA-01, INFRA-02, INFRA-03, INFRA-04, INFRA-05
**Success Criteria** (what must be TRUE):
  1. Operator can launch the app via a single Docker container on a VPS/Railway/Fly.io using only environment variables for secrets (no credentials in source)
  2. The Telegram bot responds only to chat IDs and user IDs on the configured allowlist; all other senders are silently rejected and logged
  3. A SQLite database exists with the canonical metrics schema and idempotent UPSERT semantics verified by re-running an insert with no row duplication
  4. Structured logs capture startup, API call outcomes, and errors without leaking PII or raw ad data
**Plans:** 4 plans

Plans:
- [x] 01-PLAN-scaffold.md — Python project scaffold, config, structured logging
- [x] 01-PLAN-database.md — SQLite schema, migrations, DBClient UPSERT helpers
- [x] 01-PLAN-telegram-bot.md — aiogram bot, AllowlistMiddleware, handlers
- [x] 01-PLAN-entrypoint-docker.md — main.py lifecycle, APScheduler placeholder, Dockerfile

### Phase 2: Meta Ads Ingestion + Scheduled Reports + Alerts
**Goal:** Marketing team receives an automated daily digest, weekly summary, and threshold-based alerts about Meta Ads performance in their Telegram group.
**Depends on:** Phase 1
**Requirements:** META-01, META-02, META-03, META-04, META-05, REPORT-01, REPORT-02, REPORT-03, REPORT-04, REPORT-05, REPORT-06, ALERT-01, ALERT-02, ALERT-03, ALERT-04, ALERT-05
**Success Criteria** (what must be TRUE):
  1. Daily Meta campaign metrics (spend, impressions, clicks, CTR, CPC, CPM, ROAS, purchases, cost-per-purchase, reach, frequency) are ingested into the canonical store via a long-lived System User token with idempotent UPSERTs
  2. A formatted daily digest (and Monday weekly summary with WoW deltas) is posted to the configured Telegram group on schedule, including a plain-English AI-generated TL;DR, top/bottom campaigns, spend pacing, and chart images — splitting at the 4096-char limit when needed
  3. After each successful Telegram delivery, a dead-man's-switch heartbeat is pinged so silent failures become detectable
  4. The five alert types (spend spike, ROAS drop, zero-conversion, budget pacing, CPC spike) fire to Telegram when configurable thresholds are breached against ingested Meta data
**Plans:** 8 plans

Plans:
- [ ] 02-01-PLAN.md — Foundation extension: Settings + MIGRATION_002_PHASE2 + DBClient helpers + ParseMode.HTML
- [ ] 02-02-PLAN.md — Meta API client: SDK init, fetch functions, action parsing, tenacity retry
- [ ] 02-03-PLAN.md — Report builders: HTML splitter, matplotlib charts, daily/weekly assemblers, AI TL;DR
- [ ] 02-04-PLAN.md — Alert engine: 5 alert types, rolling average SQL, deduplication via alert_log
- [ ] 02-05-PLAN.md — Meta ingest job: APScheduler zero-arg job, ingestion_log lifecycle, circuit breaker
- [ ] 02-06-PLAN.md — Report jobs: daily and weekly APScheduler jobs, heartbeat delivery
- [ ] 02-07-PLAN.md — Wiring: 3 CronTrigger jobs in main.py, /report handler in handlers.py
- [ ] 02-08-PLAN.md — Tests: full Phase 2 test suite (9 test files, all 16 requirement IDs)

**UI hint:** yes

### Phase 3: GA4 Ingestion + Cross-Source Layer
**Goal:** Reports and stored data combine Meta and GA4 sources so teams see landing-page and website-engagement context alongside ad performance, with attribution honesty.
**Depends on:** Phase 2
**Requirements:** GA4-01, GA4-02, GA4-03, GA4-04, GA4-05, CROSS-01, CROSS-02, CROSS-03
**Success Criteria** (what must be TRUE):
  1. Daily GA4 metrics (sessions, users, new users, bounce rate, avg engagement time, pageviews by landing page, goal conversions/events) are pulled via a Viewer-only service account, default to D-2 freshness, are stored with `ga4_` prefixed conversion fields, and have quota usage tracked plus a >=6h cache
  2. Meta and GA4 rows are joinable by exact UTM campaign-name match in the canonical store, and the daily/weekly digest surfaces website sessions and top 3 landing pages by conversions
  3. When Meta and GA4 conversion numbers disagree, both are shown side-by-side in reports with a brief attribution-model explanation — never blended into one number
  4. When Meta campaigns cannot be matched to GA4 data (missing or inconsistent UTM tagging), a UTM-coverage warning is included in the affected report
**Plans:** TBD

### Phase 4: Conversational AI + Recommendations
**Goal:** Allowlisted users can ask free-text marketing questions in Telegram and receive data-grounded, source-cited answers with concrete optimization recommendations.
**Depends on:** Phase 3
**Requirements:** CHAT-01, CHAT-02, CHAT-03, CHAT-04, CHAT-05, CHAT-06, CHAT-07, CHAT-08, REC-01, REC-02, REC-03

**Design Note — Meta Ads MCP (considered 2026-05-19):**
Meta launched official MCP support for the Ads API (https://www.facebook.com/business/help/1456422242197840). Decision: **do not replace the ingestion pipeline** with MCP calls. Scheduled reports and historical trend analysis require a local SQLite cache — live MCP calls on every Claude query would break scheduled digests, burn API quota per conversation turn, and remove the ability to compare across arbitrary historical windows. **Optional addition:** expose a `query_meta_live` tool in the Claude tool set (Phase 4) that delegates to the Meta Ads MCP for real-time spot-checks (e.g. "what is the live spend right now?") alongside the existing SQLite-backed tools. This gives real-time capability without dismantling the offline analysis layer. Evaluate at Phase 4 planning time whether the MCP is stable enough to include.

**Success Criteria** (what must be TRUE):
  1. A user on the allowlist can ask "which landing pages drive most conversions?", "which campaigns are underperforming and why?", or "give recommendations for optimizing campaign performance" in Telegram and receive a data-grounded answer that cites its source and timestamp
  2. Multi-turn follow-up questions work without re-stating context (conversation state is persisted per chat session in SQLite) and inline keyboard buttons ("Drill down", "Compare to last week", "Why is this happening?", "Show chart") appear after each answer
  3. Claude reaches data only through the validated tool surface (`query_metrics`, `compare_periods`, `get_campaign_detail`, `list_underperformers`, `get_landing_page_performance`) — no raw SQL is ever exposed to the model; all user text and ingested ad strings are wrapped in delimited data tags
  4. Per-request token caps and a configurable monthly Anthropic spend ceiling are enforced (calls auto-shut-down when exceeded); generated recommendations cite the specific triggering metric values and distinguish Meta-side vs GA4-side signals
**Plans:** TBD
**UI hint:** yes

### Phase 5: Hardening & Ops
**Goal:** (Reserved) Post-v1 reliability, observability, and operability work — backfill commands, Sentry, graceful per-source degradation, anomaly explanations. All 38 v1 requirements ship in Phases 1-4; this phase exists as a placeholder for the wrap-up work recommended by research.
**Depends on:** Phase 4
**Requirements:** (none — all v1 requirements are mapped to Phases 1-4)
**Success Criteria** (what must be TRUE):
  1. Operator has a documented backfill command to replay historical Meta/GA4 windows into the canonical store
  2. Per-source ingestion failures degrade gracefully — Meta failure does not block GA4 reports and vice versa, with explicit "data unavailable" notices in the digest
  3. Errors are forwarded to Sentry (or equivalent) and the dead-man's-switch alerts the operator when heartbeats stop
**Plans:** TBD

## Coverage

| Requirement | Phase |
|-------------|-------|
| INFRA-01 | 1 |
| INFRA-02 | 1 |
| INFRA-03 | 1 |
| INFRA-04 | 1 |
| INFRA-05 | 1 |
| META-01 | 2 |
| META-02 | 2 |
| META-03 | 2 |
| META-04 | 2 |
| META-05 | 2 |
| REPORT-01 | 2 |
| REPORT-02 | 2 |
| REPORT-03 | 2 |
| REPORT-04 | 2 |
| REPORT-05 | 2 |
| REPORT-06 | 2 |
| ALERT-01 | 2 |
| ALERT-02 | 2 |
| ALERT-03 | 2 |
| ALERT-04 | 2 |
| ALERT-05 | 2 |
| GA4-01 | 3 |
| GA4-02 | 3 |
| GA4-03 | 3 |
| GA4-04 | 3 |
| GA4-05 | 3 |
| CROSS-01 | 3 |
| CROSS-02 | 3 |
| CROSS-03 | 3 |
| CHAT-01 | 4 |
| CHAT-02 | 4 |
| CHAT-03 | 4 |
| CHAT-04 | 4 |
| CHAT-05 | 4 |
| CHAT-06 | 4 |
| CHAT-07 | 4 |
| CHAT-08 | 4 |
| REC-01 | 4 |
| REC-02 | 4 |
| REC-03 | 4 |

**Total:** 38 mapped, 0 unmapped ✓

## Progress

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Foundation & Walking Skeleton | 4/4 | Complete | 2026-05-19 |
| 2. Meta Ads Ingestion + Scheduled Reports + Alerts | 0/8 | Planned | - |
| 3. GA4 Ingestion + Cross-Source Layer | 0/0 | Not started | - |
| 4. Conversational AI + Recommendations | 0/0 | Not started | - |
| 5. Hardening & Ops | 0/0 | Not started | - |
