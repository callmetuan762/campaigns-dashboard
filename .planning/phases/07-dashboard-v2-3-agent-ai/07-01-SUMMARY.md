---
phase: 07-dashboard-v2-3-agent-ai
plan: "01"
subsystem: database
tags: [sqlite, pydantic-settings, dashboard, cpd, tdd]

# Dependency graph
requires:
  - phase: 06-streamlit-dashboard
    provides: "DashboardSettings base class, db.py query pattern, _conn helper, test fixtures"
provides:
  - "DashboardSettings.cpd_target: float = 0.0 (TIER tag foundation)"
  - "db.get_campaign_daily(db_path, campaign_name, start_date, end_date) -> list[dict] (drill-down foundation)"
  - ".env.example CPD_TARGET documentation"
affects:
  - "07-02 (TIER tags read cpd_target from settings)"
  - "07-03 (drill-down page calls get_campaign_daily)"

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "TDD RED/GREEN for new settings fields: tests fail before field added, then pass"
    - "Positional ? params for all SQL user input — never f-string or format()"
    - "get_campaign_daily follows _conn helper + list[dict] return pattern from Phase 6"

key-files:
  created:
    - tests/test_dashboard_settings.py (3 new cpd_target tests appended)
    - tests/test_dashboard_db.py (6 new get_campaign_daily tests appended)
  modified:
    - src/dashboard/settings.py
    - src/dashboard/db.py
    - .env.example

key-decisions:
  - "cpd_target default 0.0 — zero hides TIER column downstream; no behavioral change to existing dashboard"
  - "get_campaign_daily uses exact same _conn + positional ? param pattern as all other db.py functions"
  - "No @st.cache_data wrapper in db.py — caching responsibility belongs in the consumer page (07-03)"

patterns-established:
  - "SQL parameter order: campaign_name first, then start_date, end_date — matches ? order in WHERE clause"
  - "COALESCE(SUM(...), 0) for all aggregated columns — no NULL leakage to callers"

requirements-completed: [DASH-06, DASH-07]

# Metrics
duration: 3min
completed: 2026-05-24
---

# Phase 7 Plan 01: Data + Config Foundation Summary

**DashboardSettings.cpd_target float field (default 0.0) and db.get_campaign_daily() daily Meta+GA4 query function added as foundation for Phase 7 TIER tags and drill-down page**

## Performance

- **Duration:** ~3 min
- **Started:** 2026-05-24T14:44:47Z
- **Completed:** 2026-05-24T14:47:08Z
- **Tasks:** 2 (both TDD)
- **Files modified:** 5

## Accomplishments
- `DashboardSettings.cpd_target: float = 0.0` added — loaded from `CPD_TARGET` env var; default 0.0 keeps TIER column hidden until operator configures a target
- `db.get_campaign_daily()` returns 7-key dicts per day: `{date, spend, deposits, sessions, roas, meta_purchases, ga4_purchases}` — campaign-level only, exact UTM join, no cross-campaign leakage
- `.env.example` documents `CPD_TARGET=0.0` with explanatory comment about TIER tag behavior
- 29 tests now pass for settings + db modules (was 23 before this plan)
- Full suite: 248 tests pass, 0 failures

## Task Commits

1. **Task 1 RED: cpd_target failing tests** - `bbdab41` (test)
2. **Task 1 GREEN: cpd_target implementation** - `0fe51a9` (feat)
3. **Task 2 RED: get_campaign_daily failing tests** - `6bd2e47` (test)
4. **Task 2 GREEN: get_campaign_daily implementation** - `5f4f4a8` (feat)

## Files Created/Modified

- `src/dashboard/settings.py` — Added `cpd_target: float = 0.0` after `anthropic_monthly_budget_usd`
- `src/dashboard/db.py` — Appended `get_campaign_daily()` after `get_campaign_names()`
- `.env.example` — Added `CPD_TARGET=0.0` with 3-line explanatory comment in Streamlit Dashboard section
- `tests/test_dashboard_settings.py` — 3 new tests appended: default 0.0, direct kwarg, env var load
- `tests/test_dashboard_db.py` — 6 new tests appended: empty DB, 3-row ordering, GA4 join, ad-set filter, date bounds, no cross-campaign leakage

## Decisions Made

- `cpd_target` default 0.0 is the canonical "off" state — TIER tags are hidden when 0.0, avoiding any UI change for users who have not configured the field
- No caching wrapper in `get_campaign_daily` — per Phase 6 architecture, caching belongs in the Streamlit consumer (07-03 will add `@st.cache_data`)
- SQL parameter order matches WHERE clause order exactly: `(campaign_name, start_date, end_date)` — consistent with T-07-01-01 mitigations

## Deviations from Plan

None - plan executed exactly as written.

## Threat Surface Scan

No new network endpoints, auth paths, or file access patterns introduced. `get_campaign_daily` inputs flow through positional `?` params as required by T-07-01-01 (Tampering) and T-07-01-02 (Cross-campaign leakage) in the plan's threat register.

## Issues Encountered

None.

## Next Phase Readiness

- 07-02 (TIER tags): Can import `DashboardSettings` and read `settings.cpd_target` immediately
- 07-03 (drill-down page): Can call `get_campaign_daily(db_path, campaign_name, start, end)` immediately
- No blockers.

---
*Phase: 07-dashboard-v2-3-agent-ai*
*Completed: 2026-05-24*
