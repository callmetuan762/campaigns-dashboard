# Phase 3: GA4 Ingestion + Cross-Source Layer - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-05-19
**Phase:** 03-ga4-ingestion-cross-source-layer
**Areas discussed:** Cross-source report design, Landing page metrics scope, UTM coverage warnings, GA4 events / conversion tracking

---

## Cross-Source Report Design

| Option | Description | Selected |
|--------|-------------|----------|
| New section after Meta section | Daily digest keeps its existing Meta section, then a new '--- Website (GA4) ---' section follows with sessions, top landing pages, and conversions. Clean separation between ad-spend and website data. | ✓ |
| Inline with Meta data | Each campaign row shows Meta metrics + GA4 sessions/conversions side-by-side. Requires UTM match for every campaign row. | |
| Separate report, same schedule | GA4 data is sent as a second Telegram message right after the Meta digest. | |

**User's choice:** New section after Meta section

---

| Option | Description | Selected |
|--------|-------------|----------|
| Side-by-side with a one-line explanation | e.g., 'Purchases: Meta 7-day click: 12 \| GA4 last-click: 8  (Attribution difference is normal — Meta counts across 7 days, GA4 uses last-click on the day of conversion.)' | ✓ |
| Side-by-side numbers only, no explanation | e.g., 'Purchases: Meta 12 \| GA4 8'. Cleaner, less verbose. | |
| Only show when numbers diverge significantly | Show side-by-side attribution only when Meta and GA4 differ by more than a configurable threshold. | |

**User's choice:** Side-by-side with a one-line explanation

---

## Landing Page Metrics Scope

| Option | Description | Selected |
|--------|-------------|----------|
| Top 3 by conversions | Matches the existing 'top 3 campaigns by ROAS' pattern. Matches REQUIREMENTS.md REPORT-02. | ✓ |
| Top 5 by conversions | More data per report. Risk: messages grow longer. | |
| Top 3 by sessions, with conversion count shown | Ranks by traffic volume rather than conversions. | |

**User's choice:** Top 3 by conversions

---

| Option | Description | Selected |
|--------|-------------|----------|
| Yesterday only (D-2 per CLAUDE.md freshness rule) | GA4 data always defaults to D-2. Landing pages show yesterday's top performers. | |
| 7-day rolling average | Smooths single-day noise. | |
| Both: yesterday + 7-day trend | Show yesterday's top 3 AND a 7-day trend summary. | ✓ |

**User's choice:** Both: yesterday + 7-day trend

---

| Option | Description | Selected |
|--------|-------------|----------|
| Yes — top 3 landing pages with WoW session + conversion deltas | Consistent with how Meta campaigns are shown in the weekly summary. | ✓ |
| No — weekly GA4 section shows totals only | Simpler. Just show the same top-3 landing pages with absolute numbers. | |

**User's choice:** Yes — WoW deltas for top 3 landing pages

---

## UTM Coverage Warnings

| Option | Description | Selected |
|--------|-------------|----------|
| Summary line with count | e.g., '⚠️ UTM coverage: 5/8 campaigns matched to GA4. 3 campaigns have no website data.' Keeps the report clean. | ✓ |
| Per-campaign warning list | Lists every unmatched campaign. More diagnostic but adds length. | |
| Only warn when coverage is below a threshold | Only show if fewer than 50% of campaigns have GA4 matches. | |

**User's choice:** Summary line with count

---

| Option | Description | Selected |
|--------|-------------|----------|
| Bottom of GA4 section | The warning naturally follows the GA4 data. | ✓ |
| Top of report, before Meta section | High-visibility placement. | |
| Only in a separate /utm_audit command, not in daily digest | Keeps the daily digest clean. | |

**User's choice:** Bottom of GA4 section

---

## GA4 Events / Conversion Tracking

| Option | Description | Selected |
|--------|-------------|----------|
| purchase event | GA4's standard e-commerce event. Directly comparable to Meta's 'purchases' metric. | |
| A custom goal/conversion event | If your GA4 property has a specific marked conversion event. | ✓ |
| All marked conversion events combined | Count all events marked as conversions in GA4 property settings. | |

**User's choice:** A custom goal/conversion event

---

| Option | Description | Selected |
|--------|-------------|----------|
| I'll configure it via env var (Claude's discretion) | Add a GA4_CONVERSION_EVENT env var with a sensible default (e.g., 'purchase'). Operator can override per deployment. | ✓ |
| purchase (standard GA4 e-commerce) | Use the standard GA4 purchase event as default. | |
| Other — I'll type the event name | Provide the exact GA4 event name for your property. | |

**User's choice:** I'll configure it via env var (Claude's discretion)

---

## Claude's Discretion

- Exact GA4 Data API dimension names (`pagePath` vs `landingPage`)
- GA4 SDK sync vs async client choice
- Retry parameters for GA4 tenacity decorator
- Exact Telegram message formatting details for GA4 section
- Schema migration number for Phase 3 (if needed)
- GA4 ingest scheduling time (recommended: 01:00, before Meta at 02:00)

## Deferred Ideas

- `/utm_audit` command — Phase 4 conversational AI
- Multi-property GA4 support — v2
- Attribution model comparison view — v2
- GA4 BigQuery export path — out of scope
