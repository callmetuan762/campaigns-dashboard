# Phase 6: Streamlit Performance Dashboard — Context

**Gathered:** 2026-05-24
**Status:** Ready for planning
**Mode:** --auto (all choices are recommended defaults)

<domain>
## Phase Boundary

Phase 6 adds a standalone Streamlit web dashboard that reads the existing SQLite database
(written by the bot) and gives the marketing team a visual performance overview and an
embedded AI chat bar backed by the same 5 Claude tools as the Telegram /ask command.

**Scope:**
1. **Overview page** — KPI cards (spend, ROAS, form_submit_deposit/CPD, GA4 sessions) + spend-vs-deposits dual-axis trend chart + Meta 7d-click vs GA4 last-click grouped bar chart + campaign table sorted by NSM (CPD), with date range picker
2. **Multi-page skeleton** — Sidebar nav set up for future Campaigns / Attribution / AI Chat pages; only Overview is built in Phase 6
3. **Embedded AI chat bar** — `st.chat_input` fixed at bottom of every page; same 5 Claude tools; sync Anthropic client; session_state conversation history
4. **Auth gate** — DASHBOARD_PASSWORD env var; empty = open access for local dev
5. **Standalone** — zero aiogram/Telegram imports; reads same SQLite file via sqlite3 (sync); new src/dashboard/ package

**Out of scope for Phase 6:**
- Campaigns drill-down page (individual campaign detail)
- Attribution analysis page
- Dedicated AI Chat page (chat bar appears on Overview; full page is a future phase)
- 3-agent AI architecture (single Claude context, same as Telegram /ask)
- Robyn/MMM integration
- Streamlit Cloud / multi-user auth beyond single shared password

</domain>

<decisions>
## Implementation Decisions

### Package Structure
- **D-01:** New package `src/dashboard/` with 5 files:
  - `__init__.py` (empty)
  - `settings.py` — `DashboardSettings(BaseSettings)` reading from `.env`; **no** `telegram_bot_token` field (avoids import failure when running without Telegram creds)
  - `db.py` — sync `sqlite3` data access (5 query functions for dashboard data)
  - `tools.py` — sync tool implementations + TOOLS schema copy (standalone, no `src.ai` imports needed)
  - `chat.py` — sync Anthropic tool-use loop
  - `app.py` — Streamlit entrypoint (the overview page)
- **D-02:** Run command: `streamlit run src/dashboard/app.py` from repo root. `.env` is read by `DashboardSettings` relative to working directory.

### Dependencies
- **D-03:** Add `streamlit>=1.35,<2` and `plotly>=5.20,<6` to `pyproject.toml` dependencies.
- **D-04:** Add `DASHBOARD_PASSWORD` to `.env.example`.

### Overview Page Layout
- **D-05:** KPI row: 6 `st.metric` cards — Total Spend | Blended ROAS | Deposits (NSM) | CPD | GA4 Sessions | Active Campaigns. ROAS delta indicator: green if ≥2.0, red if <1.0 (matches builder.py thresholds).
- **D-06:** Charts side-by-side in `st.columns(2)`:
  - Left: Spend vs Deposits — Plotly dual-axis chart (bars for spend, line+markers for deposits)
  - Right: Attribution comparison — Plotly grouped bar chart (Meta form_submit_deposit vs GA4 ga4_purchases_lastclick per campaign, side-by-side)
- **D-07:** Attribution chart caption: "Meta uses 7-day click attribution · GA4 uses last-click · Never blend these numbers" — enforces CROSS-02 attribution honesty rule visually
- **D-08:** Campaign table: `st.dataframe` with pandas DataFrame. Columns: Campaign | Spend ($) | ROAS | Impressions | Deposits | CPD ($) | GA4 Sessions. Sorted by Deposits DESC. ROAS formatted as emoji indicator (🟢 ≥2.0, ⚠️ 1.0–2.0, 🔴 <1.0). Hidden index.
- **D-09:** Sidebar date range picker with "7d" / "30d" quick buttons and a `st.date_input`. Default: last 7 days (yesterday as end, 7 days before as start). Data freshness (Meta last date, GA4 last date) shown at bottom of sidebar.

### Chart Styling
- **D-10:** Dark theme matching the HTML report:
  - `plot_bgcolor="#1a1d27"`, `paper_bgcolor="#0f1117"`, `font_color="#e4e7ef"`
  - Spend bar: `rgba(99, 125, 255, 0.6)` (blue-purple), Deposits line: `#34d399` (green)
  - Meta bar: `#60a5fa` (blue), GA4 bar: `#a78bfa` (purple)
  - Streamlit page config: `st.set_page_config(layout="wide")` for full-width

### Data Access (db.py)
- **D-11:** Five sync query functions using `sqlite3.connect(str(db_path))` with `row_factory = sqlite3.Row`:
  - `get_kpi_summary(db_path, start_date, end_date)` — spend, weighted ROAS, total deposits, CPD, active campaign count
  - `get_ga4_kpi(db_path, start_date, end_date)` — total sessions
  - `get_daily_trend(db_path, start_date, end_date)` — daily (date, spend, deposits, sessions)
  - `get_campaign_table(db_path, start_date, end_date)` — per-campaign (name, spend, roas, impressions, deposits, cpd, ga4_sessions)
  - `get_attribution_comparison(db_path, start_date, end_date)` — per-campaign (name, meta_deposits, ga4_purchases)
  - `get_data_freshness(db_path)` — meta last date, ga4 last date
- **D-12:** All campaign-level ad_metrics queries filter `WHERE ad_set_id = '' AND ad_id = ''` (same as tools.py — campaign-level rows only)
- **D-13:** ROAS aggregation: `SUM(spend * roas) / SUM(spend)` (spend-weighted, not AVG) — same method as builder.py
- **D-14:** `@st.cache_data(ttl=300, show_spinner=False)` wrappers in app.py around all DB calls to prevent re-fetching on every widget interaction. Cache key includes `db_path_str`, `start_date`, `end_date`.

### AI Chat Bar
- **D-15:** Sync Anthropic client: `anthropic.Anthropic(api_key=...)` (not AsyncAnthropic). No `asyncio.run()` wrappers needed — Streamlit is sync.
- **D-16:** `st.chat_input("Ask about campaign performance, ROAS, deposits…")` renders as fixed-bottom input. Message history rendered above via `st.chat_message`.
- **D-17:** Conversation history stored in `st.session_state.chat_history` as Anthropic-format messages list (role/content dicts). Resets on page refresh. No SQLite persistence in Phase 6.
- **D-18:** Tool-use loop in `src/dashboard/chat.py → run_chat()`: same pattern as `src/ai/chat.py` but sync. Max 10 iterations. Returns `(final_text, updated_history)`.
- **D-19:** `src/dashboard/tools.py` contains: (a) `TOOLS` list copied verbatim from `src/ai/tools.py` (same Anthropic schema), (b) sync versions of all 5 tool implementations using `sqlite3`, (c) `dispatch_tool(name, tool_input, db_path)` sync router. Standalone — no imports from `src.ai.*`.
- **D-20:** System prompt for dashboard chat includes today's date, data freshness dates, campaign names, and the NSM context ("Cost per Deposit is the North Star Metric"). Mirrors the Telegram system prompt pattern from Phase 4 D-17.

### Auth
- **D-21:** `DashboardSettings.dashboard_password: str = ""`. If empty string → dashboard open (no gate). If set → show `st.form` password input on all pages. On correct submission → `st.session_state.authenticated = True` + `st.rerun()`. Wrong password → `st.error("Incorrect password")`.

### Claude's Discretion
- Exact wording of empty states (no data for period, DB not found, etc.)
- Whether to show a "Last updated" timestamp on each KPI card
- Exact column widths in campaign table

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Project Core
- `.planning/ROADMAP.md` §Phase 6 — Goal, success criteria, requirements (DASH-01..05)
- `CLAUDE.md` — Security non-negotiables, data model rules (meta_ prefix, ga4_ prefix, never blend), stack versions
- `.planning/phases/04-conversational-ai-recommendations/04-CONTEXT.md` — Tool surface decisions (D-12 to D-23), system prompt design (D-17, D-18), model choice (D-01)

### Existing AI Surface (reuse without modification)
- `src/ai/tools.py` — TOOLS schema (copy to src/dashboard/tools.py), all 5 tool SQL queries, frozenset allowlists
- `src/ai/chat.py` — Async tool-use loop pattern to adapt to sync Anthropic client

### DB Schema (read before writing any SQL)
- `src/db/schema.py` — Table definitions: ad_metrics, campaigns, ga4_metrics, ga4_landing_pages
- `src/db/client.py` — Existing query patterns (_QUERY_META_SQL, _QUERY_GA4_SQL from tools.py) and campaign-level filter sentinel (`ad_set_id = '' AND ad_id = ''`)

### Reference for Patterns
- `src/reports/builder.py` — ROAS thresholds (🟢 ≥2.0, ⚠️ 1.0–2.0, 🔴 <1.0), weighted ROAS calculation, CPD display
- `src/config.py` — Settings field names to replicate in DashboardSettings (db_path, anthropic_api_key, report_timezone)

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `src/ai/tools.py` — TOOLS list (copy to dashboard/tools.py), SQL queries (port to sync), frozenset allowlists (copy verbatim)
- `src/ai/chat.py` — Tool-use loop logic (adapt to sync Anthropic client, replace aiosqlite calls with sqlite3)
- `src/reports/builder.py` — ROAS indicator logic, weighted ROAS aggregation formula, CPD display pattern

### Established Patterns
- Campaign-level filter: `WHERE ad_set_id = '' AND ad_id = ''` (always required for ad_metrics queries)
- Join key: `ga4_metrics.campaign_utm = campaigns.name` (exact UTM match, no fuzzy)
- Never blend: always show Meta and GA4 numbers with source labels
- Named SQL params: `?` positional (sqlite3 style) or `:name` named — pick one style per function

### Integration Points
- `pyproject.toml` — Add `streamlit>=1.35,<2` and `plotly>=5.20,<6` to main dependencies
- `.env.example` — Add `DASHBOARD_PASSWORD=` with comment
- `src/dashboard/settings.py` — Standalone pydantic-settings; `extra="ignore"` so all other .env vars are silently skipped; `env_ignore_empty=True`

</code_context>

<specifics>
## Specific Details

### HTML Report Reference
The HTML report at `Downloads/Nowa Segment Comparison — 2026-05-15 → 2026-05-21 (2).html`
(passed in-session) demonstrates the exact visual language the team uses:
- Dark background (#0f1117), green highlights for top performers, red for bottom
- CPD (Cost per Deposit) as the NSM — lower is better
- TIER tags (★ SCALE, MAINTAIN, REDUCE, PAUSED) signal budget action
- Segments ranked by NSM then volume
- Note: segment names in the report map to the existing campaign structure in the DB

### CPD Target
The HTML report shows target CPD as blank (not yet set). Dashboard should display CPD as a
plain number without a "vs target" delta for Phase 6. The target can be added as a config
field in a future phase.

### NSM Label
The report calls it "Cost per Paid Deposit." In the DB it's `meta_form_submit_deposit`.
Dashboard labels: "Deposits" (count), "CPD" (cost per deposit = spend ÷ deposits).

</specifics>

<deferred>
## Deferred Ideas

- Campaigns drill-down page (click a row → see daily detail chart + Meta/GA4 side-by-side)
- Attribution analysis page (deeper Meta vs GA4 comparison, UTM coverage warnings)
- Dedicated AI Chat page (full-screen chat without the overview widgets above)
- 3-agent architecture (Meta Agent + GA4 Agent + Attribution Agent + Orchestrator) — Phase 4 memory note
- Robyn/MMM integration — weekly saturation curves, budget allocation recommendations
- Streamlit Cloud auth (OAuth2 / email allowlist) — replace password gate for multi-user teams
- TIER-style action tags (★ SCALE, MAINTAIN, REDUCE) on campaign table rows — requires CPD target threshold
- Alert panel showing recent alert_log entries
- Per-segment trend sparklines in campaign table

</deferred>

---

*Phase: 06-streamlit-performance-dashboard*
*Context gathered: 2026-05-24*
