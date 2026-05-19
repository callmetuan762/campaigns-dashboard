# Phase 3: GA4 Ingestion + Cross-Source Layer - Context

**Gathered:** 2026-05-19
**Status:** Ready for planning

<domain>
## Phase Boundary

Phase 3 adds Google Analytics 4 as a second data source and surfaces cross-source insights in existing reports:

1. **GA4 ingestion** — daily pull of session, user, landing-page, and conversion metrics via the GA4 Data API using a Viewer-only service account; stored in the already-defined `ga4_metrics` table with `ga4_` prefixed conversion columns (GA4-01 through GA4-05)
2. **Cross-source report layer** — daily digest and weekly summary extended with a new "Website (GA4)" section, side-by-side attribution comparison when Meta and GA4 conversions exist for the same campaign, and UTM coverage warnings (CROSS-01 through CROSS-03)

Conversational AI, Claude tool-use, and advanced analytics are out of scope for this phase.

</domain>

<decisions>
## Implementation Decisions

### Cross-Source Report Design

- **D-01:** GA4 data appears as a **new section after the existing Meta section** in both the daily digest and weekly summary. Section header: `--- Website (GA4) ---`. Clean separation between ad-spend (Meta) and website (GA4) data — no interleaving of metric rows.
- **D-02:** Attribution comparison is **side-by-side with a one-line explanation** when both Meta and GA4 conversions exist for the same UTM-matched campaign. Example format: `Purchases: Meta 7d-click: 12 | GA4 last-click: 8  (Attribution difference is normal — Meta counts across 7 days, GA4 uses last-click on conversion day.)` This satisfies CROSS-02 (never blend, always explain attribution model).

### Landing Page Metrics

- **D-03:** Show **top 3 landing pages by conversions** (consistent with the existing "top 3 campaigns by ROAS" pattern in the Meta section; matches REPORT-02's specification). Ranked by `ga4_purchases_lastclick` (or the configured conversion event — see D-08).
- **D-04:** Daily digest shows landing page metrics for **two windows**: yesterday's top 3 (D-2 per CLAUDE.md freshness rule) AND a 7-day rolling trend summary (e.g., "7-day avg: 45 sessions/day"). This gives both recency signal and noise-smoothed trend.
- **D-05:** Weekly summary includes **WoW deltas** for the top 3 landing pages — sessions and conversions with absolute and percentage change (e.g., "Sessions: 280 → 315 (+35 / +13%)"). Consistent with the Meta WoW format from Phase 2.

### UTM Coverage Warnings

- **D-06:** When Meta campaigns cannot be matched to GA4 data, show a **single summary line at the bottom of the GA4 section**: e.g., `⚠️ UTM coverage: 5/8 campaigns matched to GA4. 3 campaigns have no website data (UTM tags missing or inconsistent).` Do not list individual unmatched campaigns in the digest — keeps the report clean. Shows count of matched vs total.
- **D-07:** The UTM coverage warning appears at the **bottom of the GA4 section** (not at the top of the report, not in a separate command). If all campaigns match, the warning line is omitted entirely.

### GA4 Conversion Event

- **D-08:** Add `GA4_CONVERSION_EVENT` env var to `Settings` with default `"purchase"`. Operator overrides per deployment. The GA4 Data API query uses this event name as the `eventName` dimension filter for conversion counting. Stored as `ga4_purchases_lastclick` in the `ga4_metrics` table (CLAUDE.md: `ga4_` prefix rule). Attribution model noted as "last-click" in report output.

### GA4 Ingestion Pattern (mirrors Meta)

- **D-09:** Follow the same **module-globals APScheduler pattern** as `src/meta/ingest.py`: a `src/ga4/` package with `ingest.py` containing `register_job_resources()` + `ga4_ingest_job()` (zero-arg APScheduler entry point). Called from `main.py` before `scheduler.start()`.
- **D-10:** GA4 Data API default freshness: **D-2** (yesterday minus 1 day). CLAUDE.md rule: "Default to D-2 (not D-1) to avoid incomplete-day quota issues." Use same `ZoneInfo` + `report_timezone` approach as Phase 2's `_get_yesterday_iso()`.
- **D-11:** The `google-analytics-data` SDK (`BetaAnalyticsDataClient`) is synchronous. Wrap all GA4 API calls in `asyncio.to_thread()` — same pattern as `facebook-business` SDK in Phase 2.
- **D-12:** 6-hour cache (GA4-04): before making any GA4 API call, check `ingestion_log` for the last successful GA4 run within the past 6 hours. If found, skip the API call and return. `returnPropertyQuota: true` always passed in API requests.
- **D-13:** Use the existing `ingestion_log` table (already defined) with `source = 'ga4'` — same `log_ingestion_start` / `log_ingestion_finish` helpers from `DBClient`.

### Claude's Discretion

- Exact GA4 Data API dimension names for landing page queries (`pagePath` vs `landingPage` dimension — researcher determines current best practice for GA4 Data API v1)
- Whether to use `BetaAnalyticsDataClient` (sync) or `BetaAnalyticsDataAsyncClient` — both are acceptable; asyncio.to_thread wrapping handles either
- Exact retry parameters for GA4 API calls (tenacity, same pattern as Meta: `stop_after_attempt(5)`, `wait_exponential(min=2, max=60)`)
- Exact Telegram message formatting details for the GA4 section (within ParseMode.HTML + html.escape() constraint from Phase 2)
- Schema migration number: Phase 3 is `MIGRATION_003_PHASE3` if any schema changes are needed (researcher may determine no migration is needed since `ga4_metrics` table is already in MIGRATION_001)

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Project Core
- `.planning/REQUIREMENTS.md` — Full specs for GA4-01–05, CROSS-01–03
- `.planning/ROADMAP.md` — Phase 3 goal, success criteria, depends on Phase 2
- `CLAUDE.md` — GA4 D-2 freshness rule, `returnPropertyQuota: true`, `ga4_` prefix, never blend Meta + GA4, exact UTM match only, Telegram 4096-char limit

### Phase 1–2 Foundation (must understand before extending)
- `src/config.py` — Settings class; Phase 3 adds `GA4_CONVERSION_EVENT` field; already has `ga4_property_id` and `ga4_service_account_json`
- `src/db/schema.py` — `ga4_metrics` table already defined in MIGRATION_001; check if Phase 3 needs any schema additions
- `src/db/client.py` — DBClient helpers; Phase 3 adds `upsert_ga4_metrics(rows)` following same pattern as `upsert_campaign`
- `src/meta/ingest.py` — **Model for Phase 3 GA4 ingest job** (module-globals pattern, `register_job_resources`, circuit breaker, ingestion_log lifecycle)
- `src/reports/builder.py` — daily/weekly HTML builders; Phase 3 extends both with GA4 section + attribution comparison
- `src/reports/daily.py` — daily report job; Phase 3 adds GA4 query and section rendering
- `src/reports/weekly.py` — weekly report job; Phase 3 adds GA4 WoW section
- `src/main.py` — scheduler wiring; Phase 3 adds `ga4_ingest_job` CronTrigger (before `meta_ingest` time, e.g., 01:00)
- `src/bot/handlers.py` — may need `/ga4_audit` or `/utm_audit` command (researcher decides if useful)

### External APIs
- google-analytics-data SDK (`pyproject.toml`) — GA4 Data API v1; use `BetaAnalyticsDataClient`
- GA4 Data API reference — dimensions: `sessionCampaignName`, `pagePath`/`landingPage`; metrics: `sessions`, `totalUsers`, `newUsers`, `bounceRate`, `averageSessionDuration`, `eventCount` (for conversion event), `screenPageViews`

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `src/db/client.py → DBClient.execute / fetch_one / fetch_all` — Use for all GA4 DB queries
- `src/db/schema.py → ga4_metrics` — Already defined in MIGRATION_001; `campaign_utm TEXT NOT NULL` is the join key
- `src/config.py → Settings.ga4_property_id + ga4_service_account_json` — Already declared; Phase 3 just adds `ga4_conversion_event`
- `src/meta/ingest.py` — Full module-globals + APScheduler pattern to clone for `src/ga4/ingest.py`
- `src/reports/builder.py → build_daily_report_html, build_weekly_report_html` — Extend these to accept GA4 rows and generate the GA4 section
- `src/reports/daily.py → _run_daily_report` — Extend to query GA4 data from DB and pass to builder
- `src/reports/daily.py → ping_heartbeat` — Reuse as-is (already shared with weekly.py)

### Established Patterns
- `asyncio.to_thread()` wrapping for all sync SDK calls (facebook-business pattern → GA4 SDK follows same)
- Module-globals `register_job_resources(bot, db, settings)` + zero-arg APScheduler job functions
- `ingestion_log` source column: `'meta'` for Meta, `'ga4'` for GA4
- All SQL uses named parameters (`:foo`) — no f-string SQL
- `html.escape()` on ALL dynamic strings in Telegram messages (campaign names, landing page paths)
- ParseMode.HTML for all Telegram output

### Integration Points
- `src/main.py`: add `import src.ga4.ingest as ga4_ingest_module` + `register_job_resources()` call + `scheduler.add_job(ga4_ingest_job, CronTrigger(hour=1, minute=0))` — before meta_ingest (default 02:00)
- `src/reports/builder.py`: `build_daily_report_html` and `build_weekly_report_html` gain two optional parameters: `ga4_rows` and `unmatched_count`
- `src/db/client.py`: new `upsert_ga4_metrics(rows: list[dict]) -> int` method

</code_context>

<specifics>
## Specific Ideas

- UTM coverage line format: `⚠️ UTM coverage: {matched}/{total} campaigns matched to GA4. {unmatched} campaigns have no website data (UTM tags missing or inconsistent).`
- Attribution comparison format: `Purchases: Meta 7d-click: {meta_val} | GA4 last-click: {ga4_val}  (Attribution difference is normal — Meta counts across 7 days, GA4 uses last-click on conversion day.)`
- Landing page section format should follow the same `<b>` + `html.escape()` pattern: `Top 3 Landing Pages (yesterday | 7-day avg)\n<b>1. {html.escape(page_path)}</b> — {conv} conversions, {sessions} sessions`
- Weekly WoW delta for landing pages: `Sessions: {prev} → {curr} ({delta_abs:+d} / {delta_pct:+.0f}%)`
- GA4 ingest should run at 01:00 (before Meta at 02:00) so that when the 09:00 report fires, both sources are freshly ingested

</specifics>

<deferred>
## Deferred Ideas

- `/utm_audit` command for on-demand UTM coverage breakdown per campaign — Phase 4 or Phase 5 (conversational AI will handle this kind of audit query)
- GA4 BigQuery export path — out of scope for v1 (GA4 Data API is the correct direct path)
- Multi-property GA4 support — v2 (MULTI-02)
- Attribution model comparison (7d-click vs 1d-click vs view-through) — v2 (ADV-04)
- GA4 Realtime API — not needed; D-2 freshness is sufficient for daily reporting cadence

</deferred>

---

*Phase: 03-ga4-ingestion-cross-source-layer*
*Context gathered: 2026-05-19*
