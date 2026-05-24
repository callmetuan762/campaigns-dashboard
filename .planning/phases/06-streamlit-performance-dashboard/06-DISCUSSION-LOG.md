# Phase 6: Streamlit Performance Dashboard — Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-05-24
**Phase:** 06-streamlit-performance-dashboard
**Mode:** --auto (Claude selected recommended defaults for all areas)
**Areas discussed:** Package Structure, Page Layout, Chart Library, AI Chat Architecture, Auth

---

## Package Structure

| Option | Description | Selected |
|--------|-------------|----------|
| src/dashboard/ package | Separate package with settings/db/tools/chat/app modules | ✓ |
| Root dashboard.py | Single file at repo root | |
| Streamlit Cloud app dir | Cloud-native with pages/ convention | |

**Selected:** `src/dashboard/` multi-module package
**Notes:** Standalone — no src.ai.* imports to avoid asyncio/aiosqlite in sync context. DashboardSettings is separate from src/config.py to avoid requiring TELEGRAM_BOT_TOKEN at startup.

---

## Page Layout

| Option | Description | Selected |
|--------|-------------|----------|
| Overview only | Single page, no nav | |
| Multi-page skeleton | Sidebar nav, overview built, other pages stubbed | ✓ |
| Full 4 pages | All 4 pages in Phase 6 | |

**Selected:** Multi-page skeleton with only Overview built in Phase 6
**Notes:** User explicitly said "build overview page first." Multi-page skeleton (sidebar nav) is forward-compatible and minimal extra effort.

---

## Chart Library

| Option | Description | Selected |
|--------|-------------|----------|
| Matplotlib | Already used in reports/charts.py | |
| Plotly | Interactive, dark theme, native Streamlit support | ✓ |
| Altair | Declarative, Vega-based | |
| Streamlit native (st.line_chart) | Built-in, minimal config | |

**Selected:** Plotly Graph Objects
**Notes:** Interactive tooltips are valuable in a dashboard. Dark theme matches report aesthetic. st.plotly_chart renders natively. Existing matplotlib stays in reports module unchanged.

---

## AI Chat Architecture

| Option | Description | Selected |
|--------|-------------|----------|
| Import from src.ai | Reuse existing async chat.py with asyncio.run() | |
| Sync rewrite in src/dashboard | Standalone sync tools + chat, no asyncio | ✓ |
| No AI chat in Phase 6 | Chat is a separate page | |

**Selected:** Standalone sync rewrite in src/dashboard/tools.py and src/dashboard/chat.py
**Notes:** Streamlit is synchronous. asyncio.run() in Streamlit has known event loop conflicts. Clean solution: sync Anthropic client (blocking), sqlite3 for tool queries. TOOLS schema copied verbatim from src/ai/tools.py for API compatibility.

---

## Auth Gate

| Option | Description | Selected |
|--------|-------------|----------|
| DASHBOARD_PASSWORD env var | Simple session_state gate; empty = open | ✓ |
| Streamlit secrets.toml | Streamlit Cloud native | |
| No auth | Open by default | |

**Selected:** DASHBOARD_PASSWORD env var
**Notes:** Zero additional dependencies. Right level for a single-team shared URL. Empty value means open access for local dev — no friction. Can upgrade to Streamlit Cloud auth later.

---

## Chat History Persistence

| Option | Description | Selected |
|--------|-------------|----------|
| st.session_state only | In-memory per tab, resets on refresh | ✓ |
| SQLite (same as Telegram bot) | Persistent cross-session history | |

**Selected:** st.session_state only
**Notes:** Web-app expectation is tab-isolated sessions. Avoids schema changes. Can add SQLite persistence in a future phase if teams want conversation history across sessions.

---

## Claude's Discretion

- Exact empty state wording
- Whether to show a "Last updated" timestamp on KPI cards
- Exact column widths in campaign table

## Deferred Ideas

- Campaigns drill-down page, Attribution page, AI Chat page (future phases)
- 3-agent architecture (Meta + GA4 + Attribution agents) — memory note from post-v1 session
- Robyn/MMM integration
- TIER-style action tags (★ SCALE, MAINTAIN, REDUCE) requiring CPD target threshold
