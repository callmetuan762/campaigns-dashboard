# Phase 7: Dashboard v2 + 3-Agent AI -- Discussion Log

**Mode:** --auto
**Date:** 2026-05-24
**Status:** All decisions locked (auto mode)

---

## Auto-Selected Decisions

All decisions below were made automatically using the recommended default option.
No user interaction was required.

---

[auto] Settings -- Q: "Where should cpd_target be stored?"
  => Selected: cpd_target: float = 0.0 in DashboardSettings (settings.py).
  Value 0.0 = TIER tags hidden. (recommended default)

[auto] Settings -- Q: "Should a zero cpd_target hide the TIER column entirely?"
  => Selected: Yes -- omit column from DataFrame when cpd_target == 0.0.
  Keeps Phase 6 table layout intact for operators without a configured target.
  (recommended default)

[auto] TIER Logic -- Q: "What thresholds define SCALE / MAINTAIN / REDUCE / PAUSED?"
  => Selected: PAUSED if deposits==0; SCALE if cpd<=target; MAINTAIN if cpd<=target*1.3;
  REDUCE otherwise. 30% tolerance band for MAINTAIN. (recommended default)

[auto] TIER Display -- Q: "Should TIER use emoji or plain text in st.dataframe?"
  => Selected: Plain text strings (SCALE, MAINTAIN, REDUCE, PAUSED).
  Consistent with Streamlit dataframe text rendering; avoids encoding issues.
  (recommended default)

[auto] Drill-down Navigation -- Q: "How should the user navigate to a campaign drill-down?"
  => Selected: Selectbox + View detail button setting st.query_params["campaign"]
  + st.switch_page. Avoids injecting campaign names into HTML links.
  (recommended default)

[auto] Drill-down Navigation -- Q: "Should drill-down use st.query_params or st.session_state?"
  => Selected: st.query_params -- survives page refresh and allows direct URL linking.
  (recommended default)

[auto] Drill-down DB -- Q: "Should get_campaign_daily be a new db.py function or inline SQL?"
  => Selected: New function in db.py following the same pattern as all other
  query functions. Testable in isolation. (recommended default)

[auto] Drill-down DB -- Q: "Should get_campaign_daily use positional ? params?"
  => Selected: Yes -- matches db.py convention; campaign name never interpolated
  into SQL string. (recommended default)

[auto] AI Chat Page -- Q: "Should the Chat page share conversation history with Overview?"
  => Selected: Yes -- same st.session_state.chat_history key. Users can continue
  a conversation started on Overview. (recommended default)

[auto] AI Chat Page -- Q: "Which function does the Chat page use -- run_chat or run_chat_3agent?"
  => Selected: run_chat_3agent -- the reason for Phase 7. run_chat() preserved
  as fallback and for Phase 6 test compatibility. (recommended default)

[auto] 3-Agent Architecture -- Q: "Should the Orchestrator classify user intent before fanning out?"
  => Selected: No classification -- always fan out to both MetaAgent and GA4Agent.
  Simpler and eliminates classification-error failure mode. (recommended default)

[auto] 3-Agent Architecture -- Q: "How should parallel agent calls be implemented?"
  => Selected: concurrent.futures.ThreadPoolExecutor(max_workers=2) with
  concurrent.futures.wait(timeout=60). Stdlib, sync, no new dependency.
  (recommended default)

[auto] 3-Agent Architecture -- Q: "What tools does MetaAgent receive?"
  => Selected: All 5 existing TOOLS from tools.py. System prompt focuses on
  Meta-side analysis. (recommended default)

[auto] 3-Agent Architecture -- Q: "What tools does GA4Agent receive?"
  => Selected: GA4_TOOLS = [ga4_query_metrics, get_landing_page_performance].
  New ga4_query_metrics tool forces source=ga4 to prevent accidental Meta queries.
  (recommended default)

[auto] 3-Agent Architecture -- Q: "Does AttributionAgent get tools?"
  => Selected: No tools -- reasoning-only over MetaAgent and GA4Agent text outputs.
  (recommended default)

[auto] 3-Agent Architecture -- Q: "How is conversation history updated after run_chat_3agent?"
  => Selected: Single synthesized assistant message appended. Internal tool traces
  not stored. Prevents context window bloat. (recommended default)

[auto] 3-Agent Architecture -- Q: "How is budget gating enforced in 3-agent flow?"
  => Selected: Orchestrator checks _get_monthly_anthropic_cost() BEFORE fan-out.
  Each agent call logs its own row via _log_anthropic_usage().
  (recommended default)

[auto] GA4 Tool -- Q: "Should GA4Agent reuse query_metrics or get a dedicated tool?"
  => Selected: Dedicated ga4_query_metrics tool with source property removed
  (forces source=ga4 internally). Prevents the GA4 agent from accidentally
  querying Meta-only data. (recommended default)

[auto] GA4 Tool -- Q: "Does adding ga4_query_metrics change the existing TOOLS list?"
  => Selected: No -- TOOLS list is unchanged. ga4_query_metrics added to
  a new GA4_TOOLS list only. No Phase 6 regression. (recommended default)

[auto] File Layout -- Q: "Where does the Streamlit pages/ directory live?"
  => Selected: src/dashboard/pages/ (adjacent to app.py as required by Streamlit
  multi-page routing). No __init__.py needed. (recommended default)

[auto] File Layout -- Q: "Does Phase 7 require new pyproject.toml dependencies?"
  => Selected: No -- concurrent.futures is stdlib; all other packages already
  present from Phase 6. (recommended default)

---

## Deferred (not in scope for Phase 7)

- Robyn/MMM integration
- Streamlit Cloud / OAuth2 multi-user auth
- Per-segment trend sparklines
- Alert panel (alert_log entries in dashboard)
- Dedicated attribution analysis page
- Async agent architecture
- Per-campaign TIER history chart
- AI-generated TIER commentary

---

*Phase: 07-dashboard-v2-3-agent-ai*
*Log created: 2026-05-24*
