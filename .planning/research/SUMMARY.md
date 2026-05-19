# Research Summary: Ads Reporting Agent

**Synthesized:** 2026-05-19
**Source files:** STACK.md, FEATURES.md, ARCHITECTURE.md, PITFALLS.md
**Overall confidence:** HIGH

---

## Recommended Stack

- **Language / runtime:** Python 3.12+ managed with `uv` and an asyncio core (Meta + GA4 + Anthropic are all Python-first; no real Node alternative).
- **Telegram framework:** `aiogram 3` (async-native, modern, clean fit for long Claude/API calls); webhook in production, long-polling in dev.
- **Scheduler:** `APScheduler` (`AsyncIOScheduler`) in-process with SQLite jobstore — Celery is overkill for a single-tenant 2-source pipeline.
- **Storage:** SQLite (`aiosqlite`, optionally SQLAlchemy 2.x async) as the canonical metrics store; pandas for in-memory cross-source work; tenacity + pyrate-limiter for resilience.
- **AI + deployment:** Anthropic Python SDK with **tool use** (Sonnet for chat, Haiku for scheduled summaries) + prompt caching; Docker Compose on a small VPS or Railway/Fly.io.

## Table Stakes Features

V1 must ship credibly with: scheduled **daily digest** (Markdown, Tier-1 metrics, WoW comparison, plain-English TL;DR), **3-5 default alerts** (spend spike, ROAS drop, zero-conversion, budget pacing, CPC spike), **conversational Q&A** with context retention and source citations, **inline keyboard follow-ups** (Drill down / Compare / Why / Show chart), **chart images** for trends, **last-sync timestamps**, and **read-only by design**. Cross-source Meta-vs-GA4 numbers must always be shown **side-by-side with attribution explanation** — never blended.

## Architecture Pattern

A **single-process modular monolith** organized as an AI-augmented ELT pipeline around a **canonical metrics store** that decouples three concerns: (1) idempotent UPSERT ingestion from Meta + GA4 on independent schedules, (2) scheduled report generation with direct context injection to Claude (Haiku), and (3) on-demand Q&A via a small, validated **Claude tool surface** (`query_metrics`, `compare_periods`, `get_campaign_detail`, `list_underperformers`) — never raw SQL. Only ingestion writes metric tables; the tool executor is the sole DB-to-Claude path; conversation state lives in the DB, not memory.

## Top 5 Watch-Out Pitfalls

1. **Prompt injection via campaign names / ad copy** — wrap all ingested text in delimited tags, instruct Claude to treat as data, strip injection phrases. Design in from Phase 1; cannot be retrofitted safely.
2. **Silent report failures** — implement a dead-man's-switch (Healthchecks.io / Cronitor) that pings only after Telegram send returns 200. Trust dies on the first unnoticed gap.
3. **Open-bot data/cost exfiltration** — enforce a strict chat-ID/user-ID allowlist at the handler entry **before** any Claude call; disable join-groups via BotFather. Hard daily/monthly Anthropic spend caps.
4. **Treating Meta conversions == GA4 conversions** — store as distinct fields (`meta_purchases_7dclick`, `ga4_purchases_lastclick`), always present side-by-side with an attribution explanation, never average or reconcile.
5. **API quota burnout** — Meta: use async insights jobs for wide breakdowns, request Standard tier early, watch throttle headers. GA4: set `returnPropertyQuota: true`, batch dimensions, cache >=6h, default to D-2 freshness.

## Phase Build Order

1. **Phase 1 — Foundation & walking skeleton:** FastAPI + Pydantic Settings + SQLite + aiogram polling + APScheduler + chat-ID allowlist. Security baseline established.
2. **Phase 2 — Meta Ads ingestion + template report:** Canonical schema, idempotent UPSERT, exponential backoff, ad-account-timezone-aware queries, daily/weekly digest via SQL templates delivered to Telegram.
3. **Phase 3 — GA4 ingestion + cross-source layer:** GA4 client, `ga_metrics` table, UTM-based hard-match join, mismatch warnings, D-2 freshness default.
4. **Phase 4 — Conversational AI (tool use):** Claude tool surface, agentic loop, DB-backed conversation history, prompt caching, prompt-injection guardrails, token/cost caps.
5. **Phase 5 — Hardening & ops:** Dead-man's-switch, graceful per-source degradation, alert engine, Sentry, backfill command, anomaly explanations.

## Key Open Questions

- **Phase 1:** Has Meta Standard tier access been granted? What are the ad-account and GA4 property timezones?
- **Phase 2:** Webhook vs long-polling for v1 deploy target? What is the report-failure fallback notification path?
- **Phase 3:** Is UTM tagging consistently applied to existing Meta campaigns? (If not, cross-source join will be unreliable.)
- **Phase 4:** Monthly Anthropic budget ceiling? Acceptable Haiku vs Sonnet trade-off on summary quality?
- **Phase 5:** Who is on the chat-ID allowlist (group ID + individual user IDs)? Retention window for raw API snapshots?

---

## Confidence Assessment

| Area | Confidence | Notes |
|---|---|---|
| Stack | HIGH | Verified against official SDK repos and 2026 PyPI versions |
| Features | MEDIUM-HIGH | Strong industry consensus; specific alert thresholds vary by account |
| Architecture | HIGH (patterns) / MEDIUM (schema specifics) | UTM join key is the main schema risk |
| Pitfalls | HIGH | Verified against Meta, Google, Telegram, and Anthropic official docs |

**Biggest residual gap:** the Meta-to-GA4 join key contract depends on UTM hygiene the project does not yet control — flag as a Phase 3 prerequisite.
