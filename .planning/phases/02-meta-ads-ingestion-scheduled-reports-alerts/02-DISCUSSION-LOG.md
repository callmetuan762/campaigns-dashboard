# Phase 2: Meta Ads Ingestion + Scheduled Reports + Alerts - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-05-19
**Phase:** 02-meta-ads-ingestion-scheduled-reports-alerts
**Mode:** auto (workflow.auto_advance = true, workflow._auto_chain_active = true)
**Areas discussed:** Ingestion scheduling, Telegram message format, Chart generation, Alert threshold config, Heartbeat mechanism

---

## Ingestion + Report Job Separation

| Option | Description | Selected |
|--------|-------------|----------|
| Single combined job | One job: ingest then immediately report | |
| Separate jobs (recommended) | `meta_ingest` at 02:00, `daily_report` at 09:00, `weekly_report` Mon 09:00 | ✓ |

**Auto-selected:** Separate jobs (recommended default)
**Notes:** Decoupled retry semantics. Report reads from DB so it can succeed even if ingest was partial. Aligns with REPORT-01 "default 09:00" schedule requirement.

---

## Telegram Message Format

| Option | Description | Selected |
|--------|-------------|----------|
| ParseMode.MARKDOWN_V2 | Full Markdown; 18+ special chars to escape | |
| ParseMode.HTML (recommended) | HTML mode; only `<>&"` to escape; simpler and safer | ✓ |
| ParseMode.MARKDOWN (legacy) | Phase 1 placeholder; known to break on campaign names | |

**Auto-selected:** ParseMode.HTML (recommended default)
**Notes:** Phase 1 already flagged MARKDOWN as problematic for campaign names. HTML mode reduces escaping complexity from 18+ chars to 4. Phase 1 memory: "Phase 2 MUST add `_md_escape()` helper" — resolved by switching to HTML + `html.escape()`.

---

## Chart Generation Library

| Option | Description | Selected |
|--------|-------------|----------|
| matplotlib + pandas (recommended) | Static PNGs in BytesIO; pandas already in stack | ✓ |
| plotly | Interactive HTML charts; no benefit for Telegram static images | |

**Auto-selected:** matplotlib + pandas (recommended default)
**Notes:** pandas already declared in pyproject.toml. matplotlib is the standard for static chart generation. Three chart types: spend trend, ROAS trend, top campaigns bar.

---

## Alert Threshold Configuration

| Option | Description | Selected |
|--------|-------------|----------|
| Environment variables (recommended) | Extends existing Settings; defaults in code | ✓ |
| SQLite config table | More flexible; adds CRUD complexity for v1 | |

**Auto-selected:** Environment variables (recommended default)
**Notes:** REQUIREMENTS.md Out-of-Scope explicitly states "No Settings UI / dashboard for alert configuration" for v1. Env vars are consistent with the rest of the config approach.

---

## Heartbeat Mechanism

| Option | Description | Selected |
|--------|-------------|----------|
| HEARTBEAT_URL env var (recommended) | Optional HTTP GET to healthchecks.io / betteruptime style service | ✓ |
| Custom polling endpoint | Adds server infrastructure; out of scope | |

**Auto-selected:** HEARTBEAT_URL env var (recommended default)
**Notes:** Fires after Telegram send_message returns 200 (REPORT-05: must be AFTER delivery confirmation). Uses httpx for async HTTP GET (fire-and-forget). Optional — if HEARTBEAT_URL is not set, silently skipped.

---

## Claude's Discretion

- Matplotlib chart aesthetics and color palette
- Internal module structure (src/meta/, src/reports/, src/alerts/)
- Tenacity retry parameter values

## Deferred Ideas

- Webhook mode — deferred to Phase 5
- Multi-account Meta support — Phase 5 / v2
- Alert configuration UI — out of scope for v1
