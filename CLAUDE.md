# Ads Reporting Agent — Project Guide

## What This Is

An AI-powered conversational agent that pulls data from Meta Ads API and Google Analytics 4, auto-generates reports delivered to a designated Telegram group, and lets team members interrogate campaign performance data in natural language via Claude tool use.

## GSD Workflow

This project uses the GSD planning system. Planning artifacts live in `.planning/`.

### Start / Resume Work
```
/gsd-plan-phase 1      # Plan Phase 1 (Foundation)
/gsd-execute-phase 1   # Execute Phase 1 after plan is approved
/gsd-progress          # See current project state
/gsd-next              # What to work on next
```

### Phase Sequence
1. **Foundation & Walking Skeleton** — Docker, SQLite, aiogram bot, allowlist security, APScheduler
2. **Meta Ads Ingestion + Reports + Alerts** — Meta Marketing API, daily digest, weekly summary, charts, alerts
3. **GA4 Ingestion + Cross-Source Layer** — GA4 Data API, UTM join, side-by-side attribution, coverage warnings
4. **Conversational AI + Recommendations** — Claude tool use, multi-turn chat, optimization recommendations
5. **Hardening & Ops** — Sentry, graceful degradation, backfill, dead-man's-switch

## Architecture Decisions (Do Not Revisit Without Good Reason)

| Decision | Rationale |
|----------|-----------|
| Python + aiogram 3 (async) | Meta/GA4/Anthropic are all Python-first; async is required for fan-out to slow APIs |
| SQLite (v1) | Zero-ops, sufficient for single-tenant; upgrade to Postgres only if multi-tenant |
| APScheduler in-process | Celery is overkill for 2-source daily pipeline |
| Claude tool use (not RAG/embeddings) | Marketing data is structured and queryable — exactly what tool calling is for |
| Long-polling in dev, webhook in prod | Polling is safer for single-instance restart-safe deploys |
| Read-only API access | Eliminates accidental ad changes; agents analyze, never modify campaigns |

## Security Non-Negotiables

These must be implemented in Phase 1 and never weakened:

1. **Chat-ID allowlist** — every Telegram handler checks the allowlist BEFORE doing anything, including before any Claude call
2. **Prompt injection guardrails** — all campaign names, ad copy, and user input are wrapped in `<data>...</data>` delimited tags and never interpolated raw into prompts
3. **Credentials** — never in source code; always from environment variables or a secrets manager
4. **Read-only** — no Meta Ads write/bidding API calls, ever

## Data Model Rules

- Meta conversion fields: `meta_` prefix (e.g., `meta_purchases_7dclick`)
- GA4 conversion fields: `ga4_` prefix (e.g., `ga4_purchases_lastclick`)
- **Never blend or average** Meta and GA4 conversion numbers — always show side-by-side with attribution explanation
- Meta ↔ GA4 join key: exact UTM campaign name match only (no fuzzy matching)

## Stack Versions (Verified May 2026)

```
python = "^3.12"
aiogram = "^3.x"
facebook-business = "^22.0"
google-analytics-data = "^0.22.0"
anthropic = "^0.102.0"
apscheduler = "^3.x"
aiosqlite = "^0.x"
pydantic-settings = "^2.x"
tenacity = "^9.x"
pandas = "^2.x"
```

## Key Pitfalls to Avoid

- **GA4 data freshness:** Default to D-2 (not D-1) to avoid incomplete-day quota issues
- **GA4 quota:** Always pass `returnPropertyQuota: true`; cache results for ≥6h
- **Meta API version:** Target v24.0+ (v23 and below deprecated June 9, 2026)
- **Telegram message limit:** 4096 characters max — auto-split long reports
- **Silent failures:** Dead-man's-switch heartbeat must ping AFTER Telegram API returns 200, not before

## Planning Files

| File | Purpose |
|------|---------|
| `.planning/PROJECT.md` | Project context, constraints, key decisions |
| `.planning/REQUIREMENTS.md` | 38 v1 requirements with REQ-IDs |
| `.planning/ROADMAP.md` | 5-phase execution plan |
| `.planning/STATE.md` | Current progress |
| `.planning/research/` | Stack, features, architecture, pitfalls, summary research |
