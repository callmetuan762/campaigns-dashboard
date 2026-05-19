# Project State

**Project:** Ads Reporting Agent
**Current phase:** Not started
**Last updated:** 2026-05-19

## Project Reference
See: .planning/PROJECT.md
**Core value:** Marketing teams get actionable campaign and landing-page insights delivered proactively to Telegram and can interrogate the data in natural language.
**Current focus:** Ready to begin Phase 1

## Phase Status
| Phase | Name | Status |
|-------|------|--------|
| 1 | Foundation & Walking Skeleton | Not started |
| 2 | Meta Ads Ingestion + Scheduled Reports + Alerts | Not started |
| 3 | GA4 Ingestion + Cross-Source Layer | Not started |
| 4 | Conversational AI + Recommendations | Not started |
| 5 | Hardening & Ops | Not started |

## Current Position
- **Phase:** None (pre-Phase 1)
- **Plan:** None
- **Status:** Roadmap created; awaiting `/gsd-plan-phase 1`
- **Progress:** 0 / 38 v1 requirements complete

## Performance Metrics
- Phases completed: 0 / 5
- v1 requirements shipped: 0 / 38

## Accumulated Context

### Decisions
- Telegram is the single delivery channel for v1 (no web UI, no email)
- Direct GA4 + Meta APIs (not Looker Studio scraping)
- Claude tool-use for conversational AI; no raw SQL exposed
- Read-only access to ad platforms
- SQLite as canonical metrics store with idempotent UPSERT

### Open Questions (from research)
- Phase 1: Meta Standard tier access status? Ad-account and GA4 property timezones?
- Phase 2: Webhook vs long-polling for v1 deploy target? Report-failure fallback notification path?
- Phase 3: Is UTM tagging consistently applied to existing Meta campaigns?
- Phase 4: Monthly Anthropic budget ceiling? Haiku vs Sonnet trade-off for summaries?
- Phase 5: Who is on the chat-ID allowlist? Retention window for raw API snapshots?

### Todos
(none yet)

### Blockers
(none)

## Session Continuity
- Last action: Roadmap and state initialized on 2026-05-19
- Next action: `/gsd-plan-phase 1` to decompose Phase 1 into executable plans
