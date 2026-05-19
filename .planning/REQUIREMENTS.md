# Requirements: Ads Reporting Agent

**Defined:** 2026-05-19
**Core Value:** Marketing teams get actionable campaign and landing-page insights delivered proactively to Telegram and can interrogate the data in natural language — replacing the daily manual grind of opening Looker Studio and Meta Ads side-by-side.

---

## v1 Requirements

### Foundation & Infrastructure

- [ ] **INFRA-01**: System configuration (API keys, Telegram token, account IDs, timezone) is stored securely using environment-based secret management — never in source code
- [ ] **INFRA-02**: Telegram bot enforces a strict allowlist of permitted chat IDs and user IDs before executing any command or Claude call
- [ ] **INFRA-03**: SQLite database stores canonical metrics with idempotent UPSERT so re-runs never duplicate data
- [ ] **INFRA-04**: Application runs as a single Docker container deployable to a VPS or Railway/Fly.io
- [ ] **INFRA-05**: Structured logging captures API call outcomes, report delivery status, and errors without logging PII or raw ad data

### Meta Ads Data Ingestion

- [ ] **META-01**: Agent authenticates to Meta Marketing API v24+ using a long-lived System User token
- [ ] **META-02**: Agent pulls campaign-level metrics daily: spend, impressions, clicks, CTR, CPC, CPM, ROAS, purchases, cost-per-purchase, reach, frequency
- [ ] **META-03**: Agent pulls ad-set and ad-level breakdowns on demand or on a configurable schedule
- [ ] **META-04**: All Meta API calls use exponential backoff with circuit breaker to handle rate limits and transient failures gracefully
- [ ] **META-05**: Meta data is stored per-campaign per-date in the canonical metrics store with `meta_` prefixed conversion fields to avoid source confusion

### Google Analytics 4 Data Ingestion

- [ ] **GA4-01**: Agent authenticates to GA4 Data API using a service account with Viewer-only permissions
- [ ] **GA4-02**: Agent pulls daily metrics: sessions, users, new users, bounce rate, avg engagement time, pageviews by landing page, goal conversions/events
- [ ] **GA4-03**: GA4 data defaults to D-2 freshness (yesterday minus 1 day) to avoid incomplete-day quota issues
- [ ] **GA4-04**: GA4 quota usage is tracked per request (`returnPropertyQuota: true`) and requests are cached for at least 6 hours to prevent quota exhaustion
- [ ] **GA4-05**: GA4 data is stored with `ga4_` prefixed conversion fields; attribution source (last-click) is always noted in report output

### Cross-Source Data Layer

- [ ] **CROSS-01**: Meta and GA4 metrics are joined on UTM campaign name matching (hard exact match only — no fuzzy matching)
- [ ] **CROSS-02**: When Meta and GA4 conversion numbers differ, both values are shown side-by-side with a brief attribution model explanation — never blended into a single number
- [ ] **CROSS-03**: UTM coverage warnings are surfaced in reports when Meta campaigns cannot be matched to GA4 data (missing or inconsistent UTM tagging detected)

### Scheduled Reporting (Telegram)

- [ ] **REPORT-01**: Daily digest report is auto-generated and posted to the designated Telegram group each morning (configurable schedule, default 09:00 in account timezone)
- [ ] **REPORT-02**: Daily digest includes: total spend, ROAS, top 3 and bottom 3 campaigns by ROAS, spend pacing vs daily budget, website sessions and top 3 landing pages by conversions, plain-English AI-generated TL;DR summary
- [ ] **REPORT-03**: Weekly summary report is posted every Monday with WoW comparisons for all Tier-1 metrics and an AI-generated narrative of key trends
- [ ] **REPORT-04**: Reports are formatted with Telegram Markdown (bold headers, emoji status indicators 🟢🔴⚠️), respecting the 4096-character message length limit (auto-split if needed)
- [ ] **REPORT-05**: After each report is successfully delivered (Telegram API returns 200), a dead-man's-switch heartbeat is pinged so monitoring systems can detect silent failures
- [ ] **REPORT-06**: Chart images (spend trend, ROAS trend, top campaigns bar chart) are generated and sent as Telegram photo messages alongside text reports

### Alerts

- [ ] **ALERT-01**: Spend spike alert fires when any campaign's daily spend exceeds its average by a configurable threshold (default: +50%)
- [ ] **ALERT-02**: ROAS drop alert fires when a campaign's ROAS falls below a configurable floor (default: 1.0 / break-even)
- [ ] **ALERT-03**: Zero-conversion alert fires when a campaign has spent above a configurable threshold with zero reported conversions
- [ ] **ALERT-04**: Budget pacing alert fires when cumulative monthly spend is tracking to over- or under-deliver by >20% vs the monthly budget
- [ ] **ALERT-05**: CPC spike alert fires when a campaign's CPC exceeds its 7-day average by a configurable multiplier (default: 2×)

### Conversational AI (Chat Interface)

- [ ] **CHAT-01**: Users in the allowlisted Telegram group or DM can ask free-text questions about campaign and website performance
- [ ] **CHAT-02**: Claude answers questions using a validated tool surface: `query_metrics`, `compare_periods`, `get_campaign_detail`, `list_underperformers`, `get_landing_page_performance` — no raw SQL exposed to the model
- [ ] **CHAT-03**: Conversation context is persisted in the database per chat session, enabling multi-turn follow-up questions without re-stating context
- [ ] **CHAT-04**: Claude responses cite their data source and timestamp (e.g., "Based on Meta Ads data as of 2026-05-18...")
- [ ] **CHAT-05**: All user-provided text (questions) and ingested ad data (campaign names, ad copy) are treated as untrusted input — injected into Claude prompts inside delimited data tags with instructions to treat as data only
- [ ] **CHAT-06**: Each Claude call enforces a per-request token budget cap; a monthly Anthropic spend ceiling is configurable and enforced with auto-shutdown of Claude calls when exceeded
- [ ] **CHAT-07**: Inline keyboard buttons are offered after each answer: "Drill down", "Compare to last week", "Why is this happening?", "Show chart"
- [ ] **CHAT-08**: Bot answers requests like "which landing pages drive most conversions?", "which campaigns are underperforming and why?", and "give recommendations for optimizing campaign performance" with data-grounded responses

### Optimization Recommendations

- [ ] **REC-01**: Claude generates specific, evidence-backed optimization recommendations for underperforming campaigns in both scheduled reports and on-demand Q&A
- [ ] **REC-02**: Recommendations reference the specific metric values that triggered them (e.g., "Campaign X has a CPC of $4.20 vs account average of $1.80 — consider pausing or adjusting targeting")
- [ ] **REC-03**: Recommendations distinguish between Meta-side signals (creative fatigue, audience saturation) and GA4-side signals (landing page bounce, low engagement time)

---

## v2 Requirements

### Enhanced Analytics

- **ENH-01**: Ad creative performance breakdown (image vs video, by creative ID)
- **ENH-02**: Audience segment performance analysis
- **ENH-03**: Dayparting analysis (performance by hour of day / day of week)
- **ENH-04**: Competitor benchmark context (manual input or third-party data)

### Multi-Account Support

- **MULTI-01**: Support multiple Meta ad accounts in a single deployment
- **MULTI-02**: Support multiple GA4 properties
- **MULTI-03**: Per-account report channels (different Telegram groups per account)

### Advanced Reporting

- **ADV-01**: PDF/CSV export of weekly/monthly reports on demand
- **ADV-02**: Custom metric selection for scheduled reports
- **ADV-03**: Scheduled reports via email in addition to Telegram
- **ADV-04**: Attribution model comparison view (7d click vs 1d click vs view-through)

---

## Out of Scope

| Feature | Reason |
|---------|--------|
| Custom web BI dashboard | Explicit scope boundary — Telegram + chat is the delivery layer |
| Ad buying / bidding automation | Read-only constraint — agent analyzes, does not act on ad accounts |
| Real-time sub-minute data streaming | API rate limits + data freshness windows make this impractical |
| CRM / attribution platform integrations (e.g., HubSpot, Northbeam) | Out of stated scope; complexity vs value not justified for v1 |
| Looker Studio API scraping | No official programmatic export API; going directly to GA4 + Meta APIs is correct |
| Email digest delivery | Committed to Telegram as single delivery channel for v1 |
| Multi-tenant SaaS (serving multiple client organizations) | Single-team tool for v1 |
| Settings UI / dashboard for alert configuration | Opinionated defaults via config file only in v1 |

---

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| INFRA-01 | Phase 1 | Pending |
| INFRA-02 | Phase 1 | Pending |
| INFRA-03 | Phase 1 | Pending |
| INFRA-04 | Phase 1 | Pending |
| INFRA-05 | Phase 1 | Pending |
| META-01 | Phase 2 | Pending |
| META-02 | Phase 2 | Pending |
| META-03 | Phase 2 | Pending |
| META-04 | Phase 2 | Pending |
| META-05 | Phase 2 | Pending |
| REPORT-01 | Phase 2 | Pending |
| REPORT-02 | Phase 2 | Pending |
| REPORT-03 | Phase 2 | Pending |
| REPORT-04 | Phase 2 | Pending |
| REPORT-05 | Phase 2 | Pending |
| REPORT-06 | Phase 2 | Pending |
| ALERT-01 | Phase 2 | Pending |
| ALERT-02 | Phase 2 | Pending |
| ALERT-03 | Phase 2 | Pending |
| ALERT-04 | Phase 2 | Pending |
| ALERT-05 | Phase 2 | Pending |
| GA4-01 | Phase 3 | Pending |
| GA4-02 | Phase 3 | Pending |
| GA4-03 | Phase 3 | Pending |
| GA4-04 | Phase 3 | Pending |
| GA4-05 | Phase 3 | Pending |
| CROSS-01 | Phase 3 | Pending |
| CROSS-02 | Phase 3 | Pending |
| CROSS-03 | Phase 3 | Pending |
| CHAT-01 | Phase 4 | Pending |
| CHAT-02 | Phase 4 | Pending |
| CHAT-03 | Phase 4 | Pending |
| CHAT-04 | Phase 4 | Pending |
| CHAT-05 | Phase 4 | Pending |
| CHAT-06 | Phase 4 | Pending |
| CHAT-07 | Phase 4 | Pending |
| CHAT-08 | Phase 4 | Pending |
| REC-01 | Phase 4 | Pending |
| REC-02 | Phase 4 | Pending |
| REC-03 | Phase 4 | Pending |

**Coverage:**
- v1 requirements: 38 total
- Mapped to phases: 38
- Unmapped: 0 ✓

---
*Requirements defined: 2026-05-19*
*Last updated: 2026-05-19 after initial definition*
