---
phase: 06-streamlit-performance-dashboard
plan: "03"
subsystem: dashboard
tags: [streamlit, plotly, dark-theme, auth, kpi, charts, campaign-table, chat]
dependency_graph:
  requires: ["06-01", "06-02"]
  provides: ["src/dashboard/app.py"]
  affects: ["streamlit run src/dashboard/app.py"]
tech_stack:
  added: ["streamlit>=1.35,<2", "plotly>=5.20,<6"]
  patterns: ["st.cache_data wrappers in app.py", "AppTest smoke test", "tokenize-based first-call assertion"]
key_files:
  created:
    - src/dashboard/app.py
    - tests/test_dashboard_isolation.py
    - tests/test_dashboard_app_smoke.py
  modified: []
decisions:
  - "D-10 dark theme palette defined as module-level constants (single source of truth — no inline hex strings elsewhere in the file)"
  - "Cache wrappers (_cached_kpi, _cached_ga4_kpi, etc.) live in app.py with str db_path args for cache key stability — never in db.py"
  - "Multi-page skeleton uses st.radio (not st.navigation + st.Page) — placeholder for future phases per research note"
  - "test_app_first_streamlit_call_is_set_page_config uses tokenize module instead of str.find to skip docstring occurrences"
metrics:
  duration: "~15 minutes"
  completed: "2026-05-24T10:40:45Z"
  tasks_completed: 2
  tasks_total: 2
  files_created: 3
  files_modified: 0
  tests_added: 7
  tests_total_passing: 198
---

# Phase 6 Plan 03: Streamlit App (app.py — the Overview page) Summary

**One-liner:** Streamlit Overview page with password auth gate, dark-theme Plotly charts, 6 KPI cards, ROAS-emoji campaign table, and embedded sync AI chat bar.

## What Was Built

`src/dashboard/app.py` — the single user-facing artifact of Phase 6. The page composes
`src/dashboard/db.py` (data queries) and `src/dashboard/chat.py` (AI surface) under a
wide-layout dark theme matching the existing HTML report's visual language.

### Page Structure (auth → sidebar → KPIs → charts → table → chat)

1. **`st.set_page_config`** — first call in the file (enforces Pitfall 4; verified by smoke test)
2. **Auth gate (D-21)** — `_check_auth(password_required)`: if `DASHBOARD_PASSWORD` is non-empty, shows an `st.form` with `st.text_input(type="password")`; correct password sets `st.session_state.authenticated = True` + `st.rerun()`; wrong password shows `st.error`. If empty → open access (local dev mode).
3. **DB-existence check** — Friendly `st.error()` if `settings.db_path` doesn't exist; never raw traceback.
4. **Sidebar (D-09)** — Multi-page nav skeleton via `st.radio` (Overview active; Campaigns/Attribution/AI Chat show info notice). Date picker: "Last 7 days" / "Last 30 days" quick buttons + `st.date_input` range picker guarded against single-date mid-edit state. "Refresh data" button clears `st.cache_data` (resolves Open Q2). Data freshness (`meta_last_date`, `ga4_last_date`) at bottom.
5. **KPI row (D-05)** — 6 `st.metric` cards: Total Spend | Blended ROAS (with 🟢/⚠️/🔴 delta) | Deposits (NSM) | CPD | GA4 Sessions | Active Campaigns.
6. **Charts (D-06)** — `st.columns(2)`:
   - Left: Plotly dual-axis (spend bars `rgba(99,125,255,0.6)` + deposits line `#34d399`)
   - Right: Plotly grouped bars (Meta `#60a5fa` vs GA4 `#a78bfa`) with never-blend caption
7. **Campaign table (D-08)** — `st.dataframe` with `hide_index=True`, sorted Deposits DESC, ROAS column uses `_roas_indicator()` (🟢 ≥2.0, ⚠️ 1.0–2.0, 🔴 <1.0), `column_config` for `$` formatting.
8. **AI chat bar (D-16, D-17)** — `st.chat_input` at bottom; history rendered via `st.chat_message`; `st.session_state.chat_history` persists across reruns; calls `run_chat()` from `src.dashboard.chat`.

### D-10 Dark Theme Palette

All colors defined as module-level constants — zero inline hex strings in figure builders:

| Constant | Value | Used for |
|----------|-------|---------|
| `COLOR_BG_PAPER` | `#0f1117` | Plotly paper background |
| `COLOR_BG_PLOT` | `#1a1d27` | Plotly plot background |
| `COLOR_FONT` | `#e4e7ef` | All chart text |
| `COLOR_GRID` | `#2a2e3a` | Gridlines |
| `COLOR_SPEND` | `rgba(99,125,255,0.6)` | Spend bar chart |
| `COLOR_DEPOSITS` | `#34d399` | Deposits line+markers |
| `COLOR_META` | `#60a5fa` | Meta attribution bars |
| `COLOR_GA4` | `#a78bfa` | GA4 attribution bars |

### Cache Architecture

Six `@st.cache_data(ttl=300, show_spinner=False)` wrappers live in `app.py` (not `db.py`) — the `db_path` argument is a `str` (not `Path`) to ensure stable Streamlit cache key hashing. All wrappers do a local `from pathlib import Path` before calling `db.*` functions.

### Multi-Page Skeleton

Sidebar uses `st.radio` with 4 options: Overview (active), Campaigns (coming soon), Attribution (coming soon), AI Chat (coming soon). Clicking a stub shows `st.info("This page is planned for a future phase.")` and continues to render Overview. A future phase will replace this with `st.navigation` + `st.Page`.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] `test_app_first_streamlit_call_is_set_page_config` used str.find which matched docstring**
- **Found during:** Task 2 RED run
- **Issue:** The module docstring contains "6 st.metric cards" — `src.find("st.")` returned the docstring occurrence instead of the first executable `st.set_page_config` call, causing the assertion to fail.
- **Fix:** Replaced `str.find` approach with Python `tokenize` module to walk actual code tokens and skip string literals and comments. The test now correctly finds `st.set_page_config` as the first `st.*` token in executable code.
- **Files modified:** `tests/test_dashboard_app_smoke.py`
- **Commit:** `dfd794c`

## Known Stubs

None — the page renders real data from `metrics.db`. Empty-state `st.info()` messages are shown when no data exists in the selected date range (not stubs, correct behavior).

## Manual Smoke Result

`streamlit run src/dashboard/app.py` boots cleanly against the dev DB at `./data/metrics.db`. AppTest full-boot test (`test_app_boots_with_apptest`) passes with a seeded fixture DB — title "Ads Performance Dashboard" confirmed rendered, no exceptions.

## Threat Surface Scan

No new threat surface beyond the threat register in 06-03-PLAN.md:
- T-06-09 (auth gate): `_check_auth` correctly prevents `st.session_state.authenticated` from being set on wrong password
- T-06-10 (date range): `st.date_input` returns Python `date` objects; ISO conversion is server-side; all SQL uses parameterized `?` queries
- T-06-11 (error states): DB-missing and no-API-key paths show friendly `st.error()`/`st.info()` — no raw tracebacks

## Self-Check

### Files exist
- [x] `src/dashboard/app.py` — 411 lines
- [x] `tests/test_dashboard_isolation.py`
- [x] `tests/test_dashboard_app_smoke.py`

### Commits exist
- [x] `fb625c1` — feat(06-03): create src/dashboard/app.py
- [x] `dfd794c` — test(06-03): add isolation and smoke tests

## Self-Check: PASSED

## Next Plan

**06-04** — Final integration tests, accessibility checks, and Phase 6 closure.
