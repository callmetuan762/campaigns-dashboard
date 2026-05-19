# Ads Reporting Agent

## What This Is

An AI-powered conversational agent that automatically extracts and analyzes data from Looker Studio (Google Analytics + Meta Ads integrations) and Meta Ads directly, generates structured reports delivered to a designated Telegram group, and enables real-time follow-up dialogue — letting teams ask questions like "which campaigns are underperforming?" without ever manually cross-referencing dashboards.

## Core Value

Marketing teams get actionable campaign and landing-page insights delivered proactively to Telegram and can interrogate the data in natural language — replacing the daily manual grind of opening Looker Studio and Meta Ads side-by-side.

## Requirements

### Validated

(None yet — ship to validate)

### Active

- [ ] Automatically pull metrics from Looker Studio (Google Analytics — traffic, engagement, landing pages)
- [ ] Automatically pull metrics from Looker Studio (Meta Ads integration layer)
- [ ] Pull all available metrics directly from the Meta Ads API dashboard
- [ ] Schedule and auto-generate summary reports (campaign performance, website engagement, optimization opportunities)
- [ ] Deliver formatted reports to a designated Telegram group on a configurable schedule
- [ ] Conversational AI chat interface for follow-up questions and deeper insights
- [ ] Answer natural-language queries such as "which landing pages drive most conversions?" and "which ad campaigns are underperforming and why?"
- [ ] Provide AI-generated optimization recommendations based on cross-source data
- [ ] Cross-reference Looker Studio and Meta Ads data in a unified data layer

### Out of Scope

- Building or hosting a custom BI dashboard — reports are delivered via Telegram and chat, not a web UI
- CRM or attribution system integration beyond Meta Ads and Google Analytics — keep scope tight to stated sources
- Paid ad buying / automated campaign management — read-only analysis only
- Real-time sub-minute data streaming — scheduled pulls (hourly/daily) are sufficient

## Context

- The team currently does this workflow manually: open Looker Studio, open Meta Ads, cross-reference both, synthesize insights, share via message.
- The Telegram group already exists; the agent needs to post into it.
- Meta Ads API access is available (credentials to be configured).
- Looker Studio data may be accessed via Google Looker Studio API, Google Analytics Data API (GA4), or Meta Ads connector depending on what is directly addressable.
- The conversational interface should feel natural — not a command-line tool.

## Constraints

- **APIs**: Must use Meta Ads Marketing API, Google Analytics Data API (GA4), and Looker Studio / BigQuery Export as primary data sources
- **Delivery**: Telegram Bot API for report posting and chat
- **AI**: Claude API for natural-language query answering and recommendations
- **Auth**: Secure credential storage for Meta App credentials, Google OAuth, and Telegram Bot Token
- **Scope**: Read-only on ad platforms — no write/bidding operations

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Telegram as report delivery channel | Already the team's communication hub; no new tool adoption required | — Pending |
| GA4 + Meta Ads API (not Looker Studio scraping) | Looker Studio has no official programmatic export API; it is a visualization layer over GA4/Meta APIs — the agent goes to the same underlying sources. GA4 BigQuery export is not configured, so GA4 Data API is the correct path. | — Pending |
| Claude as conversational AI backend | Natural fit for the project; handles multi-source context and nuanced marketing questions well | — Pending |
| Read-only API access | Avoids accidental ad changes; separates analysis from execution | — Pending |
| Meta Ads MCP — augment, don't replace (Phase 4) | Meta launched official MCP support (2026-05-19). Keeping the SQLite ingestion pipeline for scheduled reports and historical analysis; MCP is a candidate for an additional real-time `query_meta_live` tool in the Claude tool surface alongside the SQLite-backed tools. MCP cannot replace ingestion because scheduled digests require cached data and historical windows. Evaluate at Phase 4 planning. | — Pending |

---
*Last updated: 2026-05-19 after initialization*

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state
