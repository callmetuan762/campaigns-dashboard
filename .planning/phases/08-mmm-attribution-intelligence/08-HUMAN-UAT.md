---
status: partial
phase: 08-mmm-attribution-intelligence
source: [08-VERIFICATION.md]
started: 2026-05-24
updated: 2026-05-24
---

## Current Test

[awaiting human testing]

## Tests

### 1. Attribution page visual render
expected: The 3_Attribution page loads in the Streamlit dashboard. When MMM has run, it shows: Row 1 KPI cards (Media %, ROAS or deposits/$1k, Optimal daily spend, Maturity label); Row 2 saturation curve (with vertical dashed line at current avg spend + shaded optimal range) and stacked contribution bar (12 weeks); Row 3 Meta vs GA4 attribution table with "Never blend" caption. All charts use the dark Plotly theme consistent with other dashboard pages.
result: [pending]

### 2. Run MMM now empty-state flow
expected: When mmm_results table is empty, the Attribution page shows "MMM has not run yet. The weekly job runs Sunday at 23:00." with a "Run MMM now" button. Clicking the button shows a spinner, runs fit_mmm() synchronously (no Telegram push), inserts the result into mmm_results, and reruns the page to display the KPI cards and charts.
result: [pending]

## Summary

total: 2
passed: 0
issues: 0
pending: 2
skipped: 0
blocked: 0

## Gaps
