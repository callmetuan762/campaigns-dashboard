---
phase: 02-meta-ads-ingestion-scheduled-reports-alerts
plan: "02"
subsystem: meta-client
tags: [meta-ads, facebook-business, asyncio, tenacity, retry]
dependency_graph:
  requires: []
  provides: [src/meta/client.py, src/meta/__init__.py]
  affects: [src/meta/ingest.py (future), src/reports/builder.py (future)]
tech_stack:
  added: [facebook-business SDK wrapping pattern via asyncio.to_thread]
  patterns: [asyncio.to_thread for sync SDK isolation, tenacity exponential backoff]
key_files:
  created:
    - src/meta/__init__.py
    - src/meta/client.py
    - tests/test_meta_client.py
  modified:
    - pyproject.toml
decisions:
  - asyncio.to_thread isolates the synchronous facebook-business SDK from the aiogram async event loop
  - purchase_roas parsed via _extract_action_value (list pattern), not raw float cast
  - Only app_id logged at init; access_token and app_secret never assigned to loggable variables
  - facebook-business upper bound removed (<23 dropped) to allow SDK versions supporting Meta API v24.0+
metrics:
  duration: "2m 17s"
  completed: "2026-05-19"
  tasks_completed: 1
  files_created: 3
  files_modified: 1
---

# Phase 2 Plan 02: Meta API Client Module Summary

**One-liner:** Meta API client with asyncio.to_thread SDK isolation, tenacity retry on FacebookRequestError, and list-aware action/ROAS parsing.

## Status: COMPLETE

## What Was Created

### src/meta/__init__.py
Package init file with module docstring. Marks `src/meta` as a Python package.

### src/meta/client.py
Full Meta Marketing API client module providing:

- **`init_meta_api(settings)`** — Synchronous SDK initializer using `FacebookAdsApi.init()` with `api_version="v24.0"`. Logs only `app_id`; never logs `access_token` or `app_secret` (CLAUDE.md security non-negotiable). Both secrets accessed via `.get_secret_value()`.

- **`_extract_action_value(actions, action_type)`** — Parses Meta's list-of-dicts action fields (`purchase_roas`, `actions`, `cost_per_action_type`). Returns 0.0 for None/empty/no-match. Critical: `purchase_roas` is a `list[{action_type, value}]`, NOT a float scalar (RESEARCH Pitfall 4).

- **`_parse_insight_row(row, date_iso, level)`** — Normalizes raw API row to `ad_metrics` schema shape. Applies `meta_` prefix to conversion fields. Uses `''` sentinel for `ad_set_id`/`ad_id` at campaign level. Handles all missing fields gracefully via `.get()` with 0 defaults.

- **`_fetch_insights_sync(ad_account_id, date_iso, level)`** — Synchronous API call using `AdAccount.get_insights()` with pagination loop. Designed to be called via `asyncio.to_thread()` only.

- **`fetch_campaign_insights()`** / **`fetch_adset_insights()`** / **`fetch_ad_insights()`** — Async public API. Each wraps `_fetch_insights_sync` in `asyncio.to_thread()`. Decorated with tenacity `@retry` targeting `FacebookRequestError` with `stop_after_attempt(5)` and `wait_exponential(multiplier=1, min=2, max=60)`.

### tests/test_meta_client.py
22 unit tests covering:
- `_extract_action_value`: None/empty/match/no-match/multi-entry/type assertions
- `_parse_insight_row`: all keys present, campaign/adset/ad level sentinel behavior, numeric type coercions, missing-field safety
- fetch_* coroutine signature verification

## Verification Results

### Plan Assertions (all passed)
```
assert _extract_action_value(None, 'omni_purchase') == 0.0   PASS
assert _extract_action_value([], 'omni_purchase') == 0.0     PASS
assert _extract_action_value([...3.5...], 'omni_purchase') == 3.5   PASS
assert _extract_action_value([...other...], 'omni_purchase') == 0.0  PASS
assert row['campaign_id'] == 'c1'                            PASS
assert row['roas'] == 3.0                                    PASS
assert row['meta_purchases_7dclick'] == 10                   PASS
assert row['ad_set_id'] == ''                                PASS
assert row['ad_id'] == ''                                    PASS
```

### Acceptance Criteria (all passed)
| Pattern | Found | Required | Result |
|---------|-------|----------|--------|
| `asyncio.to_thread` | 4 | >= 3 | PASS |
| `api_version="v24.0"` | 1 | == 1 | PASS |
| `FacebookRequestError` | 5 | >= 3 | PASS |
| `omni_purchase` | 1 | >= 1 | PASS |
| `offsite_conversion.fb_pixel_purchase` | 4 | >= 2 | PASS |
| `meta_purchases_7dclick` | 1 | >= 1 | PASS |
| `meta_cost_per_purchase` | 1 | >= 1 | PASS |
| `get_secret_value` | 2 | >= 2 | PASS |

### Full Test Suite: 29 / 29 passed

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing correctness] Fixed facebook-business version constraint in pyproject.toml**
- **Found during:** Task 1 implementation
- **Issue:** `pyproject.toml` had `facebook-business>=22.0,<23` which would block SDK versions needed to call Meta API v24.0+. CLAUDE.md requires v24.0+ (v23 deprecated June 9, 2026). The installed SDK is 25.0.1.
- **Fix:** Removed upper bound: `facebook-business>=22.0` (no `<23` cap).
- **Files modified:** pyproject.toml
- **Commit:** 4ae3c92

## TDD Gate Compliance

- RED commit: `0354cfc` — `test(02-02): add failing tests for meta client module`
- GREEN commit: `4ae3c92` — `feat(02-02): add src/meta package with Meta API client module`
- Both gates present in commit history.

## Known Stubs

None — this plan creates a pure API interaction layer with no UI rendering or data display paths.

## Threat Flags

No new security surface beyond what the plan's threat model covers. `init_meta_api` logs only `app_id` (not secrets). T-02-04, T-02-05, T-02-06 dispositions implemented as specified.

## Self-Check: PASSED

All created files found on disk. Both TDD gate commits verified in git log. 29/29 tests pass.
