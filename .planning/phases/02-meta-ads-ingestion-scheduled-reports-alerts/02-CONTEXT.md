# Phase 2: Meta Ads Ingestion + Scheduled Reports + Alerts - Context

**Gathered:** 2026-05-19
**Status:** Ready for planning

<domain>
## Phase Boundary

Phase 2 delivers three tightly coupled capabilities that form the first working product value loop:

1. **Meta Ads ingestion** — daily pull of campaign metrics from the Meta Marketing API into the canonical SQLite store (META-01 through META-05)
2. **Scheduled Telegram reports** — daily digest + Monday weekly summary auto-posted to the configured group (REPORT-01 through REPORT-06), including AI-generated TL;DR (Claude API) and chart images
3. **Alert engine** — five threshold-based alert types fired to Telegram when conditions are breached (ALERT-01 through ALERT-05)

GA4 ingestion, cross-source joining, and conversational AI are out of scope for this phase.

</domain>

<decisions>
## Implementation Decisions

### Job Scheduling Design

- **D-01:** Use **three separate `CronTrigger` APScheduler jobs** registered in `main.py`:
  - `meta_ingest` — default 02:00 in `report_timezone` (ingests previous day's data before the morning report)
  - `daily_report` — default 09:00 in `report_timezone` (reads from DB, no live API call)
  - `weekly_report` — Monday 09:00 in `report_timezone` (same DB read, WoW deltas)
- **D-02:** Both report jobs read exclusively from the SQLite store. If `meta_ingest` fails, the report job still fires but includes a "data unavailable" notice (graceful degradation from Phase 5 hardening spec).
- **D-03:** The `meta_ingest` job logs start/finish to the existing `ingestion_log` table (already defined in `src/db/schema.py`).

### Meta API Integration

- **D-04:** Authenticate via **long-lived System User token** stored in `META_ACCESS_TOKEN` env var (already declared in `src/config.py`). No OAuth refresh flow needed for System User tokens — they do not expire.
- **D-05:** Validate the token is present at boot (`load_settings()` already wires this). Add a startup check that attempts a lightweight API call (e.g., `GET /me`) and logs the outcome; do not hard-fail if the API is temporarily unreachable at boot.
- **D-06:** Use the `facebook-business` SDK v22.0+ (already in `pyproject.toml`). Target API version **v24.0+** (CLAUDE.md: v23 and below deprecated June 9, 2026).
- **D-07:** Pull campaign-level metrics for yesterday's date. Pull ad-set and ad-level breakdowns using sentinel-PK rows (ad_set_id, ad_id already in schema) when META-03 fires.
- **D-08:** All Meta API calls wrapped in `tenacity.retry` with exponential backoff (already in `pyproject.toml`). Circuit-breaker: after 3 consecutive failures, mark ingestion as `failed` in `ingestion_log` and send an alert to Telegram (reuse alert delivery path).

### Telegram Message Format

- **D-09:** Use **`ParseMode.HTML`** for all outbound messages (daily digest, weekly summary, alerts). Use Python's stdlib `html.escape()` on every dynamic string (campaign names, metric values rendered as strings) before interpolation.
- **D-10:** Rationale: MarkdownV2 requires escaping 18+ special characters including `-`, `.`, `(`, `)` — campaign names commonly contain these. HTML escaping is `<>&"` only and is reliable.
- **D-11:** REPORT-04 specifies "bold headers, emoji status indicators" — these map directly to `<b>`, `<i>`, and Unicode emoji in HTML mode.
- **D-12:** Auto-split messages at 4096 chars (CLAUDE.md pitfall). Split at paragraph boundaries (double-newline) where possible; fall back to hard 4096-char split. Send chart images as separate `send_photo()` calls.

### Chart Generation

- **D-13:** Use **matplotlib + pandas** to generate chart PNGs in memory (`io.BytesIO`). Send via `bot.send_photo(chat_id, photo=BufferedInputFile(buf.read(), filename="chart.png"))`.
- **D-14:** Three chart types (REPORT-06): spend trend (line, 7-day), ROAS trend (line, 7-day), top campaigns bar chart (horizontal bar, top 10 by spend).
- **D-15:** Charts rendered with a minimal style (no heavy theming); figsize approx 10×4 for trend charts, 10×6 for bar charts. No interactive charts — static PNG only.

### Alert Threshold Configuration

- **D-16:** All thresholds stored as **environment variables** with defaults in `Settings` (extend `src/config.py`):
  - `ALERT_SPEND_SPIKE_PCT` — default 50 (percent above rolling average)
  - `ALERT_ROAS_FLOOR` — default 1.0 (break-even)
  - `ALERT_ZERO_CONV_SPEND_THRESHOLD` — default 50.0 (USD spend before zero-conversion fires)
  - `ALERT_BUDGET_PACING_PCT` — default 20 (% over/under vs monthly budget)
  - `ALERT_CPC_SPIKE_MULTIPLIER` — default 2.0 (× 7-day average)
- **D-17:** Alert evaluation runs immediately after `meta_ingest` completes (same scheduler job, final step), not as a separate job. Avoids an additional DB read cycle.
- **D-18:** Alerts are deduplicated: one alert per campaign per alert-type per calendar day (tracked in a new `alert_log` table). This prevents re-alerting on unchanged data if ingest runs multiple times.

### Heartbeat / Dead-Man's-Switch

- **D-19:** Add `HEARTBEAT_URL` optional env var to `Settings`. After each successful `send_message()` or `send_photo()` 200 response for a scheduled report, fire `httpx.AsyncClient.get(heartbeat_url)` (fire-and-forget, no retry).
- **D-20:** REPORT-05: heartbeat fires **after** Telegram API returns 200, not before. A delivery failure must prevent the heartbeat.
- **D-21:** Use `httpx` (async-native) rather than `aiohttp` — adds one lightweight dependency consistent with the rest of the async stack.

### Claude API / AI TL;DR

- **D-22:** REPORT-02 requires "plain-English AI-generated TL;DR summary" per daily digest. Use `anthropic` SDK (already in `pyproject.toml`) with `claude-haiku-4-5` for cost efficiency (summaries are short, factual; Haiku is sufficient).
- **D-23:** TL;DR prompt wraps all campaign data in `<data>...</data>` delimited tags (CLAUDE.md prompt injection guardrail). Ask for a 3-bullet plain-English summary. If Anthropic API is unavailable, omit the TL;DR block with a note — don't fail the entire report.
- **D-24:** No per-request token budget enforcement in Phase 2 (that's Phase 4 CHAT-06). Apply a reasonable `max_tokens=300` cap on the TL;DR generation call.

### Schema Extension (Phase 2)

- **D-25:** Add `alert_log` table in a new `MIGRATION_002_PHASE2` migration:
  ```sql
  CREATE TABLE IF NOT EXISTS alert_log (
      id           INTEGER PRIMARY KEY AUTOINCREMENT,
      alert_type   TEXT NOT NULL,
      campaign_id  TEXT NOT NULL,
      date         TEXT NOT NULL,
      fired_at     TEXT NOT NULL DEFAULT (datetime('now')),
      UNIQUE(alert_type, campaign_id, date)
  );
  ```
- **D-26:** No other schema changes needed for Phase 2 — `ad_metrics`, `campaigns`, and `ingestion_log` are already defined.

### Claude's Discretion

- Exact matplotlib color palette and chart aesthetics — open to sensible defaults.
- Internal module layout (`src/meta/`, `src/reports/`, `src/alerts/`) — planner decides based on Phase 1 patterns.
- Exact retry parameters for tenacity decorators (e.g., `wait_exponential(min=1, max=60)`, `stop_after_attempt(5)`) — standard values are fine.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Project Core
- `.planning/REQUIREMENTS.md` — Full requirement specs for META-01–05, REPORT-01–06, ALERT-01–05
- `.planning/ROADMAP.md` — Phase 2 goal, success criteria, dependency on Phase 1
- `CLAUDE.md` — Security non-negotiables, data model rules (meta_ / ga4_ prefixes, no blending), key pitfalls (Telegram 4096 limit, dead-man's-switch ordering)

### Phase 1 Foundation (must understand before extending)
- `src/config.py` — Settings class; Phase 2 adds alert threshold vars and HEARTBEAT_URL
- `src/db/schema.py` — Canonical schema; Phase 2 adds MIGRATION_002_PHASE2 with alert_log table
- `src/db/client.py` — DBClient UPSERT helpers; Phase 2 adds `upsert_campaign`, alert log helper
- `src/main.py` — Lifecycle and scheduler wiring; Phase 2 replaces `_scheduler_heartbeat` placeholder with real jobs
- `src/bot/handlers.py` — Telegram handlers; Phase 2 may add `/report` manual trigger

### External APIs
- Meta Marketing API v24.0+ (CLAUDE.md: target v24.0+; v23 deprecated June 9 2026)
- facebook-business SDK v22.0+ (`pyproject.toml`)

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `src/db/client.py → DBClient.execute / fetch_one / fetch_all` — Use for all DB queries in Phase 2 (report queries, alert log checks)
- `src/db/client.py → DBClient.upsert_ad_metrics` — Already implements the Phase 2 UPSERT; Phase 2 just needs to call it with real API data
- `src/config.py → Settings` — Already has `meta_app_id`, `meta_app_secret`, `meta_access_token`, `meta_ad_account_id`; extend with alert threshold vars
- `src/main.py → AsyncIOScheduler` — Scheduler already instantiated with `SQLAlchemyJobStore`; Phase 2 adds 3 real jobs to it
- `src/bot/setup.py → create_bot_and_dispatcher` — Bot instance and dispatcher available for Phase 2 to call `send_message` / `send_photo`

### Established Patterns
- All SQL uses named parameters (`:foo`) — no f-string SQL (CLAUDE.md rule, enforced in Phase 1 review)
- structlog `log.info(event, **kwargs)` style throughout — Phase 2 continues same pattern
- `AsyncIOScheduler` with `CronTrigger`, `replace_existing=True`, `misfire_grace_time`, `coalesce=True`, `max_instances=1` — Phase 2 uses same kwargs pattern
- `ingestion_log` table: write `status='running'` on start, update to `success`/`failed` on finish

### Integration Points
- Phase 2 ingest job replaces `_scheduler_heartbeat` in `src/main.py`; the 3 new jobs are registered in the same `scheduler.add_job(...)` block
- `db` is passed into Phase 2 modules via dependency injection (same `dp["db"]` pattern or direct parameter passing from `main.py`)
- Telegram `bot` object needs to be accessible from both report jobs and alert delivery — pass as a closure or inject into job functions

</code_context>

<specifics>
## Specific Ideas

- Keep chart image generation minimal — no brand theming, just clear readable charts. Matplotlib defaults with `tight_layout()` are sufficient.
- The weekly summary WoW delta should show absolute and percentage change: e.g. "Spend: $1,200 → $1,450 (+$250 / +21%)".
- For the daily digest, the TL;DR goes at the top (most important signal first), followed by the structured metric table, then the top/bottom campaigns.
- Alert messages should use emoji to convey severity at a glance: 🚨 for spend spike / ROAS drop, ⚠️ for budget pacing / CPC spike, 🔇 for zero-conversion.

</specifics>

<deferred>
## Deferred Ideas

- Webhook mode (vs long-polling) — deferred to Phase 5 hardening as already decided
- Multi-account Meta support — Phase 5 / v2 (MULTI-01)
- Alert configuration UI / dashboard — Out of scope for v1 per REQUIREMENTS.md
- Per-source graceful degradation (Meta failure doesn't block GA4) — Phase 5 INFRA hardening

</deferred>

---

*Phase: 02-meta-ads-ingestion-scheduled-reports-alerts*
*Context gathered: 2026-05-19*
