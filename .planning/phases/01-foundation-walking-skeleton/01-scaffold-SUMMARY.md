---
phase: 1
plan: "01-scaffold"
subsystem: "config+scaffold"
tags: [python, pydantic-settings, configuration, package-layout, security]
dependency_graph:
  requires: []
  provides:
    - pyproject.toml with locked runtime/dev deps
    - src/config.py Settings(BaseSettings) + load_settings()
    - src package layout (src/, src/bot/, src/db/, src/scheduler/)
    - .env.example credential template
    - .gitignore + .dockerignore
  affects: []
tech_stack:
  added:
    - pydantic-settings 2.x (BaseSettings, SecretStr, SettingsConfigDict)
    - pydantic 2.x (Field, field_validator)
    - aiogram >=3.28,<4 (declared)
    - aiosqlite >=0.20,<1 (declared)
    - apscheduler >=3.10,<4 (declared)
    - structlog >=24,<25 (declared)
    - tenacity >=9,<10 (declared)
    - python-dotenv >=1,<2 (declared)
    - tzdata >=2024.1 (declared)
    - facebook-business >=22.0,<23 (declared)
    - google-analytics-data >=0.22.0,<1 (declared)
    - anthropic >=0.102.0,<1 (declared)
    - pandas >=2,<3 (declared)
    - sqlalchemy >=2,<3 (declared)
  patterns:
    - Twelve-factor config via pydantic-settings BaseSettings
    - SecretStr masking for all credential fields
    - CSV-from-env validator for int list fields (pydantic-settings v2 compat)
key_files:
  created:
    - pyproject.toml
    - src/__init__.py
    - src/__main__.py
    - src/config.py
    - src/bot/__init__.py
    - src/db/__init__.py
    - src/scheduler/__init__.py
    - .env.example
    - .gitignore
    - .dockerignore
  modified: []
decisions:
  - "Used str | list[int] union type for allowlist fields instead of plain list[int] to maintain pydantic-settings v2.14 compatibility; validator normalizes to list[int] at runtime"
  - "sqlalchemy added as declared dep for future APScheduler SQLAlchemyJobStore in Plan 04"
  - "src/ chosen as package root (not app/) per plan spec; hatchling build targets packages=[src]"
metrics:
  duration: "284 seconds (~5 minutes)"
  completed: "2026-05-19"
  tasks_completed: 2
  tasks_total: 2
  files_created: 10
  files_modified: 0
---

# Phase 1 Plan 01: Scaffold Summary

**One-liner:** Python project skeleton with pyproject.toml (14 runtime deps), pydantic-settings BaseSettings with SecretStr credentials, CSV-compatible allowlist validator, and src/ package layout for Plans 02-04.

## Tasks Completed

| Task | Name | Commit | Key Files |
|------|------|--------|-----------|
| 1 | Create pyproject.toml | aea470b | pyproject.toml |
| 2 | Settings class, package skeleton, .env.example | 9200631 | src/config.py, src/{bot,db,scheduler}/__init__.py, .env.example, .gitignore, .dockerignore |

## Dependencies Declared (pyproject.toml)

| Package | Version Spec | Phase Used |
|---------|-------------|------------|
| aiogram | >=3.28,<4 | Phase 1+ (bot framework) |
| aiosqlite | >=0.20,<1 | Phase 1+ (SQLite storage) |
| apscheduler | >=3.10,<4 | Phase 1+ (scheduling) |
| pydantic | >=2,<3 | Phase 1+ (data validation) |
| pydantic-settings | >=2,<3 | Phase 1+ (config from env) |
| structlog | >=24,<25 | Phase 1+ (structured logging) |
| tenacity | >=9,<10 | Phase 2+ (retry/backoff) |
| python-dotenv | >=1,<2 | Phase 1+ (.env file support) |
| tzdata | >=2024.1 | Phase 1+ (IANA tz on slim containers) |
| facebook-business | >=22.0,<23 | Phase 2 (Meta Ads API) |
| google-analytics-data | >=0.22.0,<1 | Phase 3 (GA4 Data API) |
| anthropic | >=0.102.0,<1 | Phase 4 (Claude tool use) |
| pandas | >=2,<3 | Phase 2+ (data processing) |
| sqlalchemy | >=2,<3 | Phase 4 (APScheduler SQLAlchemyJobStore) |

## Settings Fields (src/config.py)

| Field | Type | Phase Used | Notes |
|-------|------|-----------|-------|
| telegram_bot_token | SecretStr | Phase 1+ | Required — ValidationError if missing |
| telegram_allowed_chat_ids | list[int] | Phase 1+ | Allowlist middleware (OR semantics) |
| telegram_allowed_user_ids | list[int] | Phase 1+ | Allowlist middleware (OR semantics) |
| meta_app_id | str | None | Phase 2 | Optional — no-op until Phase 2 |
| meta_app_secret | SecretStr | None | Phase 2 | SecretStr masked |
| meta_access_token | SecretStr | None | Phase 2 | SecretStr masked |
| meta_ad_account_id | str | None | Phase 2 | Optional |
| ga4_property_id | str | None | Phase 3 | Optional |
| ga4_service_account_json | Path | None | Phase 3 | Path to service account file |
| anthropic_api_key | SecretStr | None | Phase 4 | SecretStr masked |
| db_path | Path | Phase 1+ | Default: /data/metrics.db |
| log_level | str | Phase 1+ | Default: INFO |
| report_timezone | str | Phase 1+ | Default: UTC |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] pydantic-settings v2.14 CSV list field parsing**
- **Found during:** Task 2 verification
- **Issue:** pydantic-settings v2.14's EnvSettingsSource calls `json.loads()` on env values for `list[int]` fields before `field_validator(mode="before")` runs. CSV values like `"123,456"` fail JSON decode and raise `SettingsError` — not a `ValidationError`.
- **Fix:** Changed field type from `list[int]` to `str | list[int]` union. The union type triggers `allow_parse_failure=True` in pydantic-settings, so JSON decode failure is caught gracefully and the raw string reaches the validator. Also added `isinstance(v, int)` branch to the validator to handle bare integer values (e.g., `"789"` is valid JSON for the integer 999 and arrives as `int` after JSON parse).
- **Behavior preserved:** `telegram_allowed_chat_ids` and `telegram_allowed_user_ids` still expose as `list[int]` at runtime — the validator normalizes the union. Downstream code consuming `settings.telegram_allowed_chat_ids` gets a proper list in all cases.
- **Files modified:** src/config.py
- **Commit:** 9200631

## Known Stubs

None — this plan creates configuration and package structure only. No data flows through any stub.

## Verification Results

All plan acceptance criteria met:
- `python -c "import tomllib, pathlib; tomllib.loads(...)"` exits 0 (valid TOML)
- All 13 Settings fields present (`model_fields.keys()`)
- `.env.example` has 22 lines (> 17 required)
- `git check-ignore .env` exits 0 (.env is gitignored)
- Task 2 automated verify passed: CSV parsing, SecretStr, default values all correct

## Threat Flags

None — this plan introduces no network endpoints, auth paths, or external API calls. The Settings class wraps credentials in SecretStr to prevent accidental logging.

## Self-Check: PASSED

- pyproject.toml: FOUND
- src/config.py: FOUND
- src/__init__.py: FOUND
- src/__main__.py: FOUND
- src/bot/__init__.py: FOUND
- src/db/__init__.py: FOUND
- src/scheduler/__init__.py: FOUND
- .env.example: FOUND
- .gitignore: FOUND
- .dockerignore: FOUND
- Commit aea470b: FOUND
- Commit 9200631: FOUND
