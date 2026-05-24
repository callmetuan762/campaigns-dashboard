---
phase: 07-dashboard-v2-3-agent-ai
plan: "03"
subsystem: ui
tags: [streamlit, plotly, dashboard, drill-down, campaign-detail, query-params]

requires:
  - phase: 07-01
    provides: get_campaign_daily() — daily Meta+GA4 rows per campaign with positional ? params
  - phase: 06-streamlit-performance-dashboard
    provides: app.py Overview page, dark Plotly theme constants, _check_auth pattern

provides:
  - Campaign Detail drill-down page (pages/1_Campaign_Detail.py) — URL-shareable via ?campaign=
  - Overview selectbox + "View detail ->" button that navigates to detail page
  - Dual Plotly charts: spend/deposits/sessions trend + Meta-vs-GA4 grouped bar

affects: [future dashboard pages, 07-04-ai-agent-chat-v2]

tech-stack:
  added: []
  patterns:
    - "D-19 standalone page rule: each Streamlit page re-declares constants and auth gate independently"
    - "URL-driven navigation: st.query_params['campaign'] + st.switch_page"
    - "Cache wrapper pattern: @st.cache_data(ttl=300) with db_path as str key"

key-files:
  created:
    - src/dashboard/pages/__init__.py
    - src/dashboard/pages/1_Campaign_Detail.py
  modified:
    - src/dashboard/app.py

key-decisions:
  - "Re-declare palette constants in detail page rather than importing from app.py (D-19 standalone rule — each Streamlit page is an independent script)"
  - "Use campaign_rows already fetched for the table to populate selectbox (no extra DB call)"
  - "Sidebar date range defaults to 30d window (vs 7d on Overview) — more useful for per-campaign trend analysis"

patterns-established:
  - "URL navigation pattern: set st.query_params then call st.switch_page for bookmarkable URLs"
  - "Missing-param guard: check query_params.get() early, st.stop() with helpful message before any data fetch"

requirements-completed: [DASH-07]

duration: 2min
completed: "2026-05-24"
---

# Phase 7 Plan 03: Campaign Detail Drill-Down Page Summary

**Streamlit campaign drill-down page with URL-shareable ?campaign= param, dual Plotly dark-theme charts (spend/deposits/sessions trend + Meta-vs-GA4 grouped bars), and Overview selectbox navigation**

## Performance

- **Duration:** ~2 min
- **Started:** 2026-05-24T00:00:26Z
- **Completed:** 2026-05-24T00:02:21Z
- **Tasks:** 3
- **Files modified:** 3 (2 created, 1 modified)

## Accomplishments
- Created `src/dashboard/pages/1_Campaign_Detail.py` — full Streamlit drill-down page with auth gate, query param handling, date picker sidebar, and two Plotly charts
- Created `src/dashboard/pages/__init__.py` — empty marker for Streamlit page discovery
- Added campaign selectbox + "View detail ->" button to Overview page (app.py) that sets `st.query_params["campaign"]` and calls `st.switch_page`
- All 260 tests pass with 0 regressions

## Navigation Flow

```
Overview page
  └─ Campaign table (st.dataframe, already rendered)
  └─ Selectbox (campaign names from campaign_rows)
  └─ "View detail →" button
       └─ st.query_params["campaign"] = selected_campaign
       └─ st.switch_page("pages/1_Campaign_Detail.py")
            └─ Campaign Detail page
                 └─ reads st.query_params.get("campaign")
                 └─ if missing → "Select a campaign" + st.stop()
                 └─ if present → fetch via _cached_daily() → render charts
```

## Chart Shapes

**Chart 1 — Daily trend (fig_trend)**
- Bar trace: spend (left y-axis, COLOR_SPEND)
- Scatter line: deposits (right y-axis, COLOR_DEPOSITS, lines+markers)
- Scatter line: GA4 sessions (right y-axis, COLOR_GA4, dotted)
- Height: 420px, dark theme

**Chart 2 — Meta vs GA4 attribution (fig_attr)**
- Grouped bars: meta_purchases (COLOR_META #60a5fa) + ga4_purchases (COLOR_GA4 #a78bfa)
- barmode="group" — side-by-side per date, never blended
- Caption: "Never blend — Meta uses 7-day click; GA4 uses last-click."
- Height: 380px, dark theme

## Auth Preservation Across Pages

Each Streamlit page is an independent script. The `_check_auth()` function is duplicated in `1_Campaign_Detail.py` (per D-19 standalone rule). It checks `st.session_state.get("authenticated")` — session state persists across Streamlit page navigations within the same browser session, so users authenticated on Overview are already authenticated on the detail page without re-entering the password.

## Task Commits

1. **Task 1: pages/__init__.py + Campaign Detail skeleton** - `e6b10bb` (feat)
2. **Task 2: Wire date picker + dual Plotly charts** - `0619a60` (feat)
3. **Task 3: Campaign selectbox + navigation in Overview** - `e3de94e` (feat)

## Files Created/Modified

- `src/dashboard/pages/__init__.py` — Empty Streamlit pages/ marker file
- `src/dashboard/pages/1_Campaign_Detail.py` — Full drill-down page (auth, query param, sidebar date picker, two charts, attribution caption)
- `src/dashboard/app.py` — Added selectbox + "View detail ->" button below campaign table

## Decisions Made

- Re-declared palette constants in detail page rather than importing from app.py — D-19 standalone rule ensures each Streamlit page is a fully self-contained script with no cross-page import dependencies
- Populated selectbox from `campaign_rows` (already fetched for the campaign table) rather than calling `get_campaign_names()` separately — avoids a redundant DB round-trip and ensures selectbox only shows campaigns with data in the current date range
- Detail page defaults to 30-day window (vs 7-day on Overview) — more useful context for per-campaign trend analysis

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

None — the detail page renders live data from `get_campaign_daily()` (07-01). If no rows exist for a campaign+date range, an informative `st.info()` message is shown rather than an empty chart.

## Threat Flags

No new security surface beyond what was planned. Campaign param flows through `get_campaign_daily` positional `?` SQL binding (T-07-03-01 mitigated). `st.header()` used instead of `st.title()` for campaign name display — both escape HTML by default (T-07-03-04 accepted).

## Self-Check

- `src/dashboard/pages/__init__.py` exists: YES
- `src/dashboard/pages/1_Campaign_Detail.py` exists: YES
- Commit e6b10bb exists: YES
- Commit 0619a60 exists: YES
- Commit e3de94e exists: YES
- 260 tests pass: YES

## Self-Check: PASSED

## Issues Encountered

None.

## Next Phase Readiness

- Campaign drill-down page is fully functional and navigable from Overview
- 07-04 (AI agent chat v2) can proceed independently — no dependencies on this plan's pages
- Manual smoke test recommended: `streamlit run src/dashboard/app.py`, sign in, pick a campaign, click "View detail →"

---
*Phase: 07-dashboard-v2-3-agent-ai*
*Completed: 2026-05-24*
