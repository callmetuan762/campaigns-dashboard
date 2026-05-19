# Project State

**Project:** Ads Reporting Agent
**Current phase:** Phase 2 — Meta Ads Ingestion + Scheduled Reports + Alerts
**Last updated:** 2026-05-19

## Project Reference
See: .planning/PROJECT.md
**Core value:** Marketing teams get actionable campaign and landing-page insights delivered proactively to Telegram and can interrogate the data in natural language.
**Current focus:** Phase 1 complete — ready to plan Phase 2

## Phase Status
| Phase | Name | Status |
|-------|------|--------|
| 1 | Foundation & Walking Skeleton | Complete ✓ (2026-05-19) |
| 2 | Meta Ads Ingestion + Scheduled Reports + Alerts | Not started |
| 3 | GA4 Ingestion + Cross-Source Layer | Not started |
| 4 | Conversational AI + Recommendations | Not started |
| 5 | Hardening & Ops | Not started |

## Current Position
- **Phase:** Phase 2 — Meta Ads Ingestion + Scheduled Reports + Alerts
- **Plan:** Not yet planned
- **Status:** Ready to plan Phase 2
- **Progress:** 5 / 38 v1 requirements complete (INFRA-01 through INFRA-05)

## Performance Metrics
- Phases completed: 1 / 5
- v1 requirements shipped: 5 / 38

## Accumulated Context

### Decisions
- Telegram is the single delivery channel for v1 (no web UI, no email)
- Direct GA4 + Meta APIs (not Looker Studio scraping)
- Claude tool-use for conversational AI; no raw SQL exposed
- Read-only access to ad platforms
- SQLite as canonical metrics store with idempotent UPSERT
- Meta Ads MCP (2026-05-19): keep facebook-business SDK ingestion pipeline for scheduled reports; consider adding Meta MCP as an additional real-time tool in Phase 4 Claude tool surface

### Phase 1 Decisions
- pydantic-settings v2.14 requires `str | list[int]` union type + validator branch for CSV env values (bare integer JSON handling)
- ad_metrics PK widened to (campaign_id, date, ad_set_id, ad_id) with NOT NULL DEFAULT '' sentinels — avoids costly table rebuild in Phase 2 META-03
- Table name allowlist frozenset added to DBClient.get_row_counts() to satisfy no-f-string-SQL rule
- ParseMode.MARKDOWN used for Phase 1 simplicity; Phase 2 should add _md_escape() helper for campaign names

### Open Questions (from research)
- Phase 2: Meta Standard tier access status? Ad-account timezone? Webhook vs long-polling for v1 deploy target? Report-failure fallback notification path?
- Phase 3: Is UTM tagging consistently applied to existing Meta campaigns?
- Phase 4: Monthly Anthropic budget ceiling? Haiku vs Sonnet trade-off for summaries?
- Phase 5: Who is on the chat-ID allowlist? Retention window for raw API snapshots?

### Human UAT Outstanding
- 01-HUMAN-UAT.md: Docker build + bot liveness test (2 items pending)

### Todos
(none)

### Blockers
(none)

## Session Continuity
- Last action: Phase 1 executed and verified complete on 2026-05-19 (4 plans, 3 waves, 7/7 tests passing)
- Next action: `/gsd-discuss-phase 2` or `/gsd-plan-phase 2` to begin Meta Ads ingestion
