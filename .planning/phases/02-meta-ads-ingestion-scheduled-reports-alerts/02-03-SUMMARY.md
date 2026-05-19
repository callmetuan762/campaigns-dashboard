---
phase: "02"
plan: "03"
subsystem: reports
tags: [reports, charts, ai, tldr, html, matplotlib, anthropic]
dependency_graph:
  requires: [02-01, 02-02]
  provides: [report-builder, chart-generator, tldr-generator, html-splitter]
  affects: [02-05, 02-06]
tech_stack:
  added: [matplotlib==3.10.9, pandas==3.0.3, anthropic==0.103.0]
  patterns: [OO matplotlib API, html.escape security, prompt injection guardrails, graceful degradation]
key_files:
  created:
    - src/reports/__init__.py
    - src/reports/splitter.py
    - src/reports/charts.py
    - src/reports/builder.py
    - src/ai/__init__.py
    - src/ai/tldr.py
  modified: []
decisions:
  - matplotlib Agg backend set before pyplot import to prevent crash in headless environments
  - OO matplotlib API (fig/ax/plt.close) used throughout to prevent memory leaks in long-running scheduler
  - generate_tldr returns None (not raises) on any Anthropic API error — report job continues without TL;DR
  - All campaign data in generate_tldr wrapped in <data>...</data> XML tags per CLAUDE.md prompt injection guardrail
  - pandas include_groups=False passed to groupby().apply() to silence FutureWarning in pandas 3.x
metrics:
  duration: "~7 minutes"
  completed: "2026-05-19T08:18:00Z"
  tasks_completed: 2
  files_created: 6
---

# Phase 2 Plan 03: Report Builders, Charts, and TL;DR Generator Summary

Pure utility modules for Telegram report generation: HTML splitter, matplotlib chart generators, HTML report assembler (daily + weekly), and Anthropic TL;DR generator.

## Status: COMPLETE

## Files Created

| File | Purpose |
|------|---------|
| `src/reports/__init__.py` | Package init |
| `src/reports/splitter.py` | `split_html_message()` — paragraph-boundary split with hard-split fallback |
| `src/reports/charts.py` | Three matplotlib chart functions returning PNG bytes (spend trend, ROAS trend, top campaigns) |
| `src/reports/builder.py` | `build_daily_report_html()`, `build_weekly_report_html()`, `get_wow_date_ranges()` |
| `src/ai/__init__.py` | Package init |
| `src/ai/tldr.py` | `generate_tldr()` — Anthropic claude-haiku-4-5, 300 tokens, graceful degradation |

## Test Results

- All 29 original tests pass (test_allowlist, test_meta_client, test_upsert_idempotency)
- 42 of 43 total tests pass
- 1 pre-existing TDD RED test (`test_spend_spike_fires_when_spend_above_threshold` from plan 02-04) continues to fail as designed — implementation in 02-04

Inline verification assertions all passed:
- `split_html_message` paragraph split, single-newline fallback, hard split
- All chart functions return non-empty PNG bytes for valid input, `b""` for empty input
- `build_daily_report_html` escapes `<script>` tags, TL;DR appears before Overall
- `get_wow_date_ranges` computes correct ISO date strings for WoW windows

## Acceptance Criteria Verification

- `matplotlib.use("Agg")` is first matplotlib call in charts.py (before pyplot import): YES
- `plt.close(fig)` called after every `fig.savefig()` (3 chart functions): YES
- `plt.subplots` used throughout, no `plt.figure()` or bare `plt.plot()`: YES
- `<data>` and `</data>` tags wrap campaign data in generate_tldr prompt: YES
- `generate_tldr` returns None on APIStatusError and APIConnectionError: YES
- `html.escape()` on all dynamic strings in builder.py: YES

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Dependency] matplotlib and anthropic not installed**
- Found during: Task 1 (import verification)
- Issue: `matplotlib`, `pandas`, and `anthropic` packages not installed in the Python environment
- Fix: Ran `python -m pip install matplotlib pandas` and `python -m pip install anthropic`
- Files modified: none (dependency install only)
- Commits: n/a

**2. [Rule 1 - Bug] pandas 3.x include_groups deprecation**
- Found during: Task 1 implementation
- Issue: `df.groupby("date").apply(lambda g: ...)` raises FutureWarning in pandas 3.x when lambda uses columns from the groupby key
- Fix: Added `include_groups=False` to the `.apply()` call in `generate_roas_trend_chart`
- Files modified: `src/reports/charts.py`
- Commits: c785160

## Known Stubs

None — all functions are fully implemented with real logic.

## Threat Flags

None — these are pure utility functions with no network endpoints, auth paths, or file access. The TL;DR module handles the Anthropic API key as a parameter (never hardcoded), consistent with CLAUDE.md credentials non-negotiable.

## Self-Check: PASSED

All 6 created files confirmed present on disk. Both task commits (c785160, 9ed2bc0) confirmed in git log.
