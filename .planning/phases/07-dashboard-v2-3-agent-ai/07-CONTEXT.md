# Phase 7: Dashboard v2 + 3-Agent AI -- Context

**Gathered:** 2026-05-24
**Status:** Ready for planning
**Mode:** --auto (all choices are recommended defaults)

<domain>
## Phase Boundary

Phase 7 upgrades the existing Phase 6 Streamlit dashboard with four independent
deliverables, all inside the src/dashboard/ package:

1. **TIER action tags (DASH-06)** -- CPD-vs-target column on the campaign table
   (SCALE / MAINTAIN / REDUCE / PAUSED), driven by cpd_target in DashboardSettings.
2. **Campaign drill-down page (DASH-07)** -- Streamlit multi-page file
   pages/1_Campaign_Detail.py; navigated via st.query_params[campaign];
   daily trend + Meta/GA4 side-by-side for the selected campaign.
3. **Dedicated AI Chat page (DASH-08)** -- pages/2_AI_Chat.py; full-screen
   layout; reuses run_chat_3agent() from chat.py.
4. **3-Agent AI architecture (DASH-09, DASH-10)** -- new src/dashboard/agents.py
   with MetaAgent, GA4Agent, AttributionAgent, and Orchestrator; parallel fan-out
   using concurrent.futures.ThreadPoolExecutor; existing run_chat() preserved as fallback.

**Scope:**
- All changes are confined to src/dashboard/.
- No changes to src/ai/, src/bot/, src/reports/, or the SQLite schema.
- All 64 Phase 6 dashboard tests must remain green after Phase 7 is merged.

**Out of scope for Phase 7:**
- Robyn/MMM integration
- Streamlit Cloud / OAuth2 auth (replace password gate)
- Per-segment trend sparklines in campaign table
- Alert panel showing recent alert_log entries
- Attribution analysis page (dedicated deep-dive beyond the drill-down)
- New data ingestion or schema changes

</domain>

<decisions>
## Implementation Decisions

### Settings Extension
- **D-01:** Add cpd_target: float = 0.0 to DashboardSettings in
  src/dashboard/settings.py. Value 0.0 means TIER tags hidden -- column omitted
  when no target is configured. Keeps Phase 6 view unchanged.
- **D-02:** Add CPD_TARGET= to .env.example (0.0 hides TIER tags).

### TIER Tag Logic (DASH-06)
- **D-03:** TIER classification is a pure Python function applied per-campaign
  row after the DB query returns, using the existing cpd value from
  get_campaign_table(). No new SQL column needed.
  - deposits == 0 or cpd is None => PAUSED (grey)
  - cpd <= cpd_target => SCALE (green)
  - cpd <= cpd_target * 1.3 => MAINTAIN (amber)
  - cpd > cpd_target * 1.3 => REDUCE (red)
- **D-04:** TIER column added only when settings.cpd_target > 0.0. When hidden,
  _format_campaign_df() returns the same 7-column DataFrame as Phase 6.
- **D-05:** TIER display is a plain text string in st.dataframe. Column label: TIER.
  Color palette additions to app.py COLOR_* block:
  COLOR_TIER_SCALE = "#34d399", COLOR_TIER_MAINTAIN = "#facc15",
  COLOR_TIER_REDUCE = "#f87171", COLOR_TIER_PAUSED = "#6b7280".

### Drill-down Page (DASH-07)
- **D-06:** New file src/dashboard/pages/1_Campaign_Detail.py.
  Streamlit includes pages/ subdirectory files in multi-page navigation.
- **D-07:** Navigation via st.query_params["campaign"]. Overview page adds a
  selectbox from db.get_campaign_names() and a View detail button that sets
  st.query_params["campaign"] and calls st.switch_page. Campaign names never
  injected into HTML links directly (security: untrusted data).
- **D-08:** New DB function get_campaign_daily(db_path, campaign_name, start_date,
  end_date) -> list[dict] added to src/dashboard/db.py. Returns: date, spend,
  deposits, sessions, roas, meta_purchases, ga4_purchases. LEFT JOIN
  ga4_metrics by exact UTM match; campaign-level filter enforced
  (ad_set_id = '' AND ad_id = ''). Campaign name as positional ? param.
- **D-09:** Drill-down page layout:
  - Page header from query param (st.title; never raw SQL input).
  - Date range picker (same sidebar pattern as Overview).
  - Line chart: daily spend / deposits / sessions with COLOR_* constants.
  - Side-by-side bar chart: Meta form_submit_deposit vs GA4 purchases per date.
    Attribution caption: Meta uses 7-day click, GA4 uses last-click, never blend.
  - Back button: st.page_link("app.py", label="Back to Overview").
- **D-10:** @st.cache_data(ttl=300, show_spinner=False) on get_campaign_daily
  wrapper in drill-down page.

### Dedicated AI Chat Page (DASH-08)
- **D-11:** New file src/dashboard/pages/2_AI_Chat.py.
  st.set_page_config(layout="wide") at top.
- **D-12:** Renders only auth gate, chat history, and st.chat_input.
  No KPI cards or charts -- maximum vertical space for conversations.
- **D-13:** Uses run_chat_3agent(). Overview page also switches to
  run_chat_3agent(). Existing run_chat() preserved for Phase 6 tests.
- **D-14:** History key: st.session_state.chat_history -- shared across Overview
  and Chat page within one browser session.

### 3-Agent Architecture (DASH-09, DASH-10)
- **D-15:** New module src/dashboard/agents.py with MetaAgent, GA4Agent,
  AttributionAgent, Orchestrator. All sync (no asyncio). Each instantiates
  its own anthropic.Anthropic() client.
- **D-16:** Tool split:
  - MetaAgent receives TOOLS (all 5 existing tools). System prompt: Meta-focused.
  - GA4Agent receives GA4_TOOLS: [get_landing_page_performance, ga4_query_metrics].
    ga4_query_metrics (D-22) forces source=ga4 internally.
  - AttributionAgent receives no tools -- reasoning-only over agent text outputs.
  - Orchestrator always fans out to both agents (no classification step).
- **D-17:** Parallel fan-out via ThreadPoolExecutor(max_workers=2).
  concurrent.futures.wait(timeout=60). Timeout/exception => graceful degradation
  (AttributionAgent still runs with partial data).
- **D-18:** run_chat_3agent(user_text, history, db_path, api_key, settings)
  added to src/dashboard/chat.py. Same signature as run_chat(). Returns
  (final_text, updated_history). Orchestrator instance per call.
- **D-19:** Budget gate before fan-out (_get_monthly_anthropic_cost()). Three
  _log_anthropic_usage() rows per user turn: MetaAgent, GA4Agent, AttributionAgent.
- **D-20:** Only the final synthesized response stored in history -- agent-internal
  tool traces not persisted. Prevents context window bloat.
- **D-21:** System prompts:
  - MetaAgent: build_system_prompt(db_path) + Meta Ads specialist instruction.
  - GA4Agent: build_system_prompt(db_path) + GA4 specialist instruction.
  - AttributionAgent: custom prompt -- reconcile Meta vs GA4 discrepancies,
    show side-by-side, produce unified user-facing answer.
  - Orchestrator uses AttributionAgent output as final_text directly.

### GA4 Query Tool Addition
- **D-22:** New tool schema ga4_query_metrics in src/dashboard/tools.py.
  Same as query_metrics but source property removed; implementation forces
  source=ga4. Exported in GA4_TOOLS list. Existing TOOLS list unchanged.
- **D-23:** dispatch_tool() handler for ga4_query_metrics calls
  query_metrics(db_path, source="ga4", **tool_input).

### File Layout
- **D-24:** Changes in Phase 7:
  - src/dashboard/settings.py -- add cpd_target (D-01)
  - src/dashboard/db.py -- add get_campaign_daily() (D-08)
  - src/dashboard/tools.py -- add ga4_query_metrics + GA4_TOOLS + handler (D-22, D-23)
  - src/dashboard/chat.py -- add run_chat_3agent() (D-18)
  - src/dashboard/agents.py -- new file (D-15)
  - src/dashboard/app.py -- TIER logic, selectbox nav, switch to run_chat_3agent
  - src/dashboard/pages/ -- new directory (no __init__.py)
  - src/dashboard/pages/1_Campaign_Detail.py -- new file (D-06)
  - src/dashboard/pages/2_AI_Chat.py -- new file (D-11)
  - .env.example -- add CPD_TARGET= (D-02)

### Claude Discretion
- Exact wording of no TIER target set helper text
- Whether agent summaries are shown collapsed (st.expander) or inline
- Exact column widths in updated campaign table with TIER column
- Error message wording when drill-down campaign name is missing

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Prior Phase Context (all decisions carry forward)
- .planning/phases/06-streamlit-performance-dashboard/06-CONTEXT.md -- D-01..D-21
  (package structure, auth, dark theme, cache TTL, sync client, ROAS thresholds,
  TOOLS schema, budget gate, history format). All Phase 6 decisions are locked.

### Phase 7 Source Files to Read Before Implementing
- src/dashboard/app.py -- 411-line Overview page
- src/dashboard/db.py -- 6 query functions
- src/dashboard/tools.py -- TOOLS list, frozenset allowlists, dispatch_tool
- src/dashboard/chat.py -- run_chat() pattern
- src/dashboard/settings.py -- DashboardSettings

### Project Core
- .planning/ROADMAP.md Phase 7 section -- DASH-06..DASH-10 success criteria
- CLAUDE.md -- Security non-negotiables, data model rules, stack versions

### DB Schema
- src/db/schema.py -- Table definitions (verify column names before writing
  get_campaign_daily SQL)

### Reference for Patterns
- src/reports/builder.py -- ROAS thresholds, weighted ROAS formula
- src/dashboard/app.py lines 36-48 -- COLOR_* constants (reuse in pages)
- src/dashboard/app.py lines 55-88 -- @st.cache_data wrapper pattern

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets from Phase 6
- app.py COLOR_* constants (COLOR_BG_PAPER, COLOR_BG_PLOT, COLOR_FONT,
  COLOR_GRID, COLOR_SPEND, COLOR_DEPOSITS, COLOR_META, COLOR_GA4) -- import
  or copy to pages/1_Campaign_Detail.py for visual consistency.
- chat.py build_system_prompt() -- base system prompt reused by MetaAgent and
  GA4Agent with specialist instruction appended.
- chat.py _conn(), _get_monthly_anthropic_cost(), _log_anthropic_usage(),
  _calculate_cost() -- import from chat.py into agents.py.
- tools.py TOOLS list and dispatch_tool() -- MetaAgent uses TOOLS; GA4Agent
  uses GA4_TOOLS (new list to be added in D-22).
- db.py get_campaign_table() -- returns cpd (nullable float) per campaign;
  TIER classification reads this value in app.py without modifying the SQL.

### Established Patterns
- @st.cache_data(ttl=300, show_spinner=False) wrapping all DB calls -- replicate
  for get_campaign_daily() in the drill-down page.
- Campaign-level filter: WHERE ad_set_id = '' AND ad_id = '' -- required in
  get_campaign_daily().
- db.py query functions use ? positional params -- new function must match.
- concurrent.futures.ThreadPoolExecutor is stdlib -- no new dependency.

### Integration Points
- src/dashboard/pages/ directory does not exist yet -- must be created.
- src/dashboard/pages/__init__.py is NOT needed (filesystem-based discovery).
- .env.example -- add CPD_TARGET=0 with comment.
- pyproject.toml -- no new dependencies required for Phase 7.

</code_context>

<specifics>
## Specific Details

### CPD Target Threshold Logic

    def _tier_label(cpd, deposits, cpd_target):
        if deposits == 0 or cpd is None:
            return "PAUSED"
        if cpd <= cpd_target:
            return "SCALE"
        if cpd <= cpd_target * 1.3:
            return "MAINTAIN"
        return "REDUCE"

Color additions to app.py COLOR_* block:
- COLOR_TIER_SCALE = "#34d399" (reuse COLOR_DEPOSITS green)
- COLOR_TIER_MAINTAIN = "#facc15" (amber, new)
- COLOR_TIER_REDUCE = "#f87171" (red, new)
- COLOR_TIER_PAUSED = "#6b7280" (grey, new)

### Threading Pattern for Parallel Agent Fan-out

    from concurrent.futures import ThreadPoolExecutor, wait

    with ThreadPoolExecutor(max_workers=2) as pool:
        fut_meta = pool.submit(meta_agent.run, user_text, history_snapshot)
        fut_ga4  = pool.submit(ga4_agent.run,  user_text, history_snapshot)
        done, _  = wait([fut_meta, fut_ga4], timeout=60)

    meta_result = fut_meta.result() if fut_meta in done else "MetaAgent timed out."
    ga4_result  = fut_ga4.result()  if fut_ga4 in done  else "GA4Agent timed out."

history_snapshot = list(history) taken before fan-out; agents work on a copy.

### get_campaign_daily SQL Pattern

    SELECT
        m.date,
        COALESCE(SUM(m.spend), 0)                        AS spend,
        COALESCE(SUM(m.meta_form_submit_deposit), 0)     AS deposits,
        COALESCE(SUM(g.sessions), 0)                     AS sessions,
        CASE WHEN SUM(m.spend) > 0
             THEN SUM(m.spend * m.roas) / SUM(m.spend)
             ELSE 0 END                                  AS roas,
        COALESCE(SUM(m.meta_purchases_7dclick), 0)       AS meta_purchases,
        COALESCE(SUM(g.ga4_purchases_lastclick), 0)      AS ga4_purchases
    FROM ad_metrics m
    JOIN campaigns c ON m.campaign_id = c.id
    LEFT JOIN ga4_metrics g ON g.campaign_utm = c.name AND g.date = m.date
    WHERE m.ad_set_id = '' AND m.ad_id = ''
      AND c.name = ?
      AND m.date BETWEEN ? AND ?
    GROUP BY m.date
    ORDER BY m.date

Campaign name passed as positional ? param -- never interpolated raw into SQL.

</specifics>

<deferred>
## Deferred Ideas

- Robyn/MMM integration (post-v1 milestone)
- Streamlit Cloud auth (OAuth2 / email allowlist) to replace password gate
- Per-segment trend sparklines in campaign table
- Alert panel showing recent alert_log entries in the dashboard
- Attribution analysis page (dedicated deep-dive: UTM coverage heatmap)
- Async agent architecture (asyncio-based if Streamlit adds native async support)
- Per-campaign TIER history chart (trend of TIER changes over time)
- AI-generated TIER commentary (narrative for why a campaign changed tier)

</deferred>

---

*Phase: 07-dashboard-v2-3-agent-ai*
*Context gathered: 2026-05-24*
