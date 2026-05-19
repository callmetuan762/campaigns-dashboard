---
plan: "01-scaffold"
phase: 1
wave: 1
depends_on: []
autonomous: true
files_modified:
  - pyproject.toml
  - .env.example
  - .gitignore
  - .dockerignore
  - src/__init__.py
  - src/__main__.py
  - src/config.py
  - src/bot/__init__.py
  - src/db/__init__.py
  - src/scheduler/__init__.py
requirements_addressed:
  - INFRA-01
must_haves:
  truths:
    - "All required Python dependencies (aiogram, aiosqlite, APScheduler, pydantic-settings, structlog, tenacity, facebook-business, google-analytics-data, anthropic, pandas) are declared in pyproject.toml with versions matching CLAUDE.md stack pins"
    - "Settings class loads from environment variables and .env and exposes every required v1 key as a typed field"
    - ".env.example exists with every required key listed (no secret values)"
    - "Importing src.config.Settings() with the .env.example values would raise pydantic ValidationError because real values are absent — fail-fast on missing config is proven"
    - "Package layout (src/, src/bot/, src/db/, src/scheduler/) is in place so Plans 02, 03, 04 can drop modules into existing namespaces without creating directories"
  artifacts:
    - path: "pyproject.toml"
      provides: "Project metadata + runtime/dev dependency declarations"
      contains: "aiogram, aiosqlite, apscheduler, pydantic-settings, structlog, tenacity, facebook-business, google-analytics-data, anthropic, pandas, python-dotenv"
    - path: "src/config.py"
      provides: "Settings(BaseSettings) class + load_settings() function"
      exports: ["Settings", "load_settings"]
    - path: ".env.example"
      provides: "Template of every environment variable the app reads"
      contains: "TELEGRAM_BOT_TOKEN, TELEGRAM_ALLOWED_CHAT_IDS, TELEGRAM_ALLOWED_USER_IDS, META_APP_ID, META_APP_SECRET, META_ACCESS_TOKEN, META_AD_ACCOUNT_ID, GA4_PROPERTY_ID, GA4_SERVICE_ACCOUNT_JSON, ANTHROPIC_API_KEY, DB_PATH, LOG_LEVEL, REPORT_TIMEZONE"
    - path: ".gitignore"
      provides: "Excludes .env, data/, *.db, __pycache__, .venv"
      contains: ".env"
    - path: "src/__init__.py"
      provides: "src package marker"
    - path: "src/bot/__init__.py"
      provides: "src.bot package marker"
    - path: "src/db/__init__.py"
      provides: "src.db package marker"
    - path: "src/scheduler/__init__.py"
      provides: "src.scheduler package marker"
  key_links:
    - from: "src/config.py"
      to: "environment variables / .env file"
      via: "pydantic-settings BaseSettings with SettingsConfigDict(env_file='.env')"
      pattern: "BaseSettings|SettingsConfigDict"
    - from: ".env.example"
      to: "src/config.py Settings fields"
      via: "every Settings field name must have a corresponding line in .env.example"
      pattern: "TELEGRAM_BOT_TOKEN=|TELEGRAM_ALLOWED_CHAT_IDS="
---

<objective>
Stand up the Python project skeleton: pyproject.toml with locked dependencies, the typed Settings configuration layer (pydantic-settings v2 + SecretStr), `.env.example`, `.gitignore`/`.dockerignore`, and the empty package directories that Plans 02–04 will populate.

Purpose: Fail-fast configuration boot is the foundation that every other component depends on. INFRA-01 ("API keys, Telegram token, account IDs, timezone stored via env-based secret management — never in source") ships here.

Output: A `uv`-buildable project that does nothing yet but can be imported (`from src.config import load_settings`), has all dependency declarations pinned, and refuses to start unless required env vars are present.
</objective>

<execution_context>
@$HOME/.claude/get-shit-done/workflows/execute-plan.md
@$HOME/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/ROADMAP.md
@.planning/STATE.md
@.planning/REQUIREMENTS.md
@.planning/phases/01-foundation-walking-skeleton/01-RESEARCH.md
@CLAUDE.md

<interfaces>
Settings class fields that downstream plans will consume (this plan creates these — Plans 02, 03, 04 import and use them):

```python
# src/config.py — produced by this plan
class Settings(BaseSettings):
    telegram_bot_token: SecretStr
    telegram_allowed_chat_ids: list[int]
    telegram_allowed_user_ids: list[int]
    meta_app_id: str | None = None
    meta_app_secret: SecretStr | None = None
    meta_access_token: SecretStr | None = None
    meta_ad_account_id: str | None = None
    ga4_property_id: str | None = None
    ga4_service_account_json: Path | None = None
    anthropic_api_key: SecretStr | None = None
    db_path: Path = Path("/data/metrics.db")
    log_level: str = "INFO"
    report_timezone: str = "UTC"

def load_settings() -> Settings: ...
```
</interfaces>
</context>

<tasks>

<task type="auto">
  <name>Task 1: Create pyproject.toml with locked stack and dev tooling</name>
  <files>pyproject.toml</files>
  <read_first>
    - CLAUDE.md (Stack Versions section — lines 70–82 of project guide; verbatim version pins)
    - .planning/phases/01-foundation-walking-skeleton/01-RESEARCH.md (Standard Stack / Installation section, lines 39–90)
  </read_first>
  <action>
Create `pyproject.toml` at the repository root. Use the `uv`-compatible PEP 621 `[project]` table. Set:

- `name = "ads-reporting"`
- `version = "0.1.0"`
- `description = "AI-powered ads reporting agent (Meta Ads + GA4 → Telegram)"`
- `requires-python = ">=3.12,<3.13"`
- `dependencies` — EXACT list (use these version specifiers verbatim):
  - `"aiogram>=3.28,<4"`
  - `"aiosqlite>=0.20,<1"`
  - `"apscheduler>=3.10,<4"`
  - `"pydantic>=2,<3"`
  - `"pydantic-settings>=2,<3"`
  - `"structlog>=24,<25"`
  - `"tenacity>=9,<10"`
  - `"python-dotenv>=1,<2"`
  - `"tzdata>=2024.1"`
  - `"facebook-business>=22.0,<23"`
  - `"google-analytics-data>=0.22.0,<1"`
  - `"anthropic>=0.102.0,<1"`
  - `"pandas>=2,<3"`
  - `"sqlalchemy>=2,<3"` (required because we'll use SQLAlchemyJobStore for APScheduler in Plan 04)

Add `[project.optional-dependencies]` table with `dev = ["pytest>=8", "pytest-asyncio>=0.23", "ruff>=0.5", "mypy>=1.10"]`.

Add `[tool.ruff]` table: `line-length = 100`, `target-version = "py312"`. Add `[tool.ruff.lint]` with `select = ["E", "F", "I", "B", "UP", "S"]` and `ignore = ["S101"]` (allow `assert` in tests).

Add `[tool.pytest.ini_options]` with `asyncio_mode = "auto"` and `testpaths = ["tests"]`.

Add `[build-system]` with `requires = ["hatchling"]` and `build-backend = "hatchling.build"`.

Add `[tool.hatch.build.targets.wheel]` with `packages = ["src"]`.

Do NOT run `uv sync` or `uv lock` as part of this task — executor produces the manifest only; lockfile is generated when the user first runs `uv sync` locally. If `uv` is installed in the execution environment, MAY run `uv lock` to produce a `uv.lock` for committing, but failure to lock is non-fatal for this task.
  </action>
  <verify>
    <automated>python -c "import tomllib, pathlib; d = tomllib.loads(pathlib.Path('pyproject.toml').read_text()); deps = d['project']['dependencies']; required = ['aiogram', 'aiosqlite', 'apscheduler', 'pydantic-settings', 'structlog', 'tenacity', 'facebook-business', 'google-analytics-data', 'anthropic', 'pandas', 'python-dotenv', 'tzdata', 'sqlalchemy']; missing = [r for r in required if not any(r in dep for dep in deps)]; assert not missing, f'missing deps: {missing}'; assert d['project']['requires-python'].startswith('>=3.12'), 'wrong python version'; print('OK')"</automated>
  </verify>
  <acceptance_criteria>
    - File `pyproject.toml` exists at repo root
    - `grep -E '^name = "ads-reporting"' pyproject.toml` matches exactly once
    - `grep -E 'requires-python = ">=3.12' pyproject.toml` matches
    - All 14 runtime dependencies above appear in the `dependencies` array (substring match per package name acceptable)
    - `[tool.pytest.ini_options]` table includes `asyncio_mode = "auto"`
    - `[tool.hatch.build.targets.wheel]` declares `packages = ["src"]`
    - `python -c "import tomllib, pathlib; tomllib.loads(pathlib.Path('pyproject.toml').read_text())"` exits 0 (file is valid TOML)
  </acceptance_criteria>
  <done>pyproject.toml is parseable, every required dependency from CLAUDE.md is present with the correct version specifier, and dev tooling (pytest, ruff, mypy) is declared as an optional dependency group.</done>
</task>

<task type="auto">
  <name>Task 2: Create Settings class, package skeleton, and .env.example</name>
  <files>src/config.py, src/__init__.py, src/__main__.py, src/bot/__init__.py, src/db/__init__.py, src/scheduler/__init__.py, .env.example, .gitignore, .dockerignore</files>
  <read_first>
    - .planning/phases/01-foundation-walking-skeleton/01-RESEARCH.md (Pattern 1: Pydantic-Settings Multi-Source Config, lines 182–262)
    - CLAUDE.md (Security Non-Negotiables section — confirms SecretStr usage requirement)
    - pyproject.toml (created by Task 1 — confirm pydantic-settings is declared)
  </read_first>
  <action>
**Create `src/config.py`** with exactly this structure:

```python
"""Application configuration loaded from environment variables / .env file.

INFRA-01: All credentials stored via env-based secret management; never in source.
"""
from __future__ import annotations

from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # ---- Telegram (required) ----
    telegram_bot_token: SecretStr
    telegram_allowed_chat_ids: list[int] = Field(default_factory=list)
    telegram_allowed_user_ids: list[int] = Field(default_factory=list)

    # ---- Meta Ads (Phase 2; declared now to fail fast on misspelled keys) ----
    meta_app_id: str | None = None
    meta_app_secret: SecretStr | None = None
    meta_access_token: SecretStr | None = None
    meta_ad_account_id: str | None = None

    # ---- GA4 (Phase 3) ----
    ga4_property_id: str | None = None
    ga4_service_account_json: Path | None = None

    # ---- Anthropic (Phase 4) ----
    anthropic_api_key: SecretStr | None = None

    # ---- App config ----
    db_path: Path = Path("/data/metrics.db")
    log_level: str = "INFO"
    report_timezone: str = "UTC"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @field_validator("telegram_allowed_chat_ids", "telegram_allowed_user_ids", mode="before")
    @classmethod
    def _split_csv(cls, v: object) -> object:
        """Accept comma-separated values from .env (e.g. "123,456") in addition to JSON."""
        if isinstance(v, str):
            stripped = v.strip()
            if not stripped:
                return []
            if stripped.startswith("["):
                return v  # let pydantic parse JSON
            return [int(x.strip()) for x in stripped.split(",") if x.strip()]
        return v


def load_settings() -> Settings:
    """Load settings once at boot. Raises ValidationError immediately on missing required config."""
    return Settings()  # type: ignore[call-arg]
```

**Create empty package markers** (each file contains a single one-line docstring):

- `src/__init__.py` containing `"""Ads reporting agent package."""`
- `src/bot/__init__.py` containing `"""Telegram bot subsystem."""`
- `src/db/__init__.py` containing `"""SQLite storage subsystem."""`
- `src/scheduler/__init__.py` containing `"""APScheduler subsystem."""`

**Create `src/__main__.py`** as a placeholder entrypoint (Plan 04 replaces this with real wiring):

```python
"""Entrypoint stub. Plan 04 wires this to src.main:main()."""
import sys

if __name__ == "__main__":
    print("ads-reporting: scaffold installed. Run main() to be wired in Plan 04.", file=sys.stderr)
    sys.exit(0)
```

**Create `.env.example`** at repo root with EXACTLY these lines (no values, no quotes around values):

```
# Telegram (REQUIRED)
TELEGRAM_BOT_TOKEN=
TELEGRAM_ALLOWED_CHAT_IDS=
TELEGRAM_ALLOWED_USER_IDS=

# Meta Ads (Phase 2)
META_APP_ID=
META_APP_SECRET=
META_ACCESS_TOKEN=
META_AD_ACCOUNT_ID=

# Google Analytics 4 (Phase 3)
GA4_PROPERTY_ID=
GA4_SERVICE_ACCOUNT_JSON=/secrets/ga4.json

# Anthropic (Phase 4)
ANTHROPIC_API_KEY=

# Application
DB_PATH=/data/metrics.db
LOG_LEVEL=INFO
REPORT_TIMEZONE=UTC
```

**Create `.gitignore`** at repo root with these entries (one per line):

```
.env
.env.local
.venv/
__pycache__/
*.pyc
*.pyo
data/
*.db
*.db-journal
*.db-wal
*.db-shm
.pytest_cache/
.mypy_cache/
.ruff_cache/
*.egg-info/
dist/
build/
```

**Create `.dockerignore`** at repo root with these entries:

```
.git
.gitignore
.env
.env.local
.venv/
__pycache__/
*.pyc
data/
*.db
.pytest_cache/
.mypy_cache/
.ruff_cache/
tests/
README.md
```

Use Python `SecretStr` for all token fields (telegram_bot_token, meta_app_secret, meta_access_token, anthropic_api_key) so accidental logging shows `**********` — INFRA-05 prerequisite.

The OR-semantics of the allowlist (chat_id in allowed_chats OR user_id in allowed_users) is implemented in Plan 03's middleware — Settings here just exposes both lists.
  </action>
  <verify>
    <automated>python -c "import sys; sys.path.insert(0, '.'); from src.config import Settings, load_settings; import os; [os.environ.pop(k, None) for k in list(os.environ) if k.startswith(('TELEGRAM_','META_','GA4_','ANTHROPIC_','DB_','LOG_','REPORT_'))]; os.environ['TELEGRAM_BOT_TOKEN']='test:abc'; os.environ['TELEGRAM_ALLOWED_CHAT_IDS']='123,456'; os.environ['TELEGRAM_ALLOWED_USER_IDS']='789'; s = load_settings(); assert s.telegram_bot_token.get_secret_value() == 'test:abc'; assert s.telegram_allowed_chat_ids == [123, 456]; assert s.telegram_allowed_user_ids == [789]; assert s.db_path.as_posix() == '/data/metrics.db'; assert s.log_level == 'INFO'; assert s.report_timezone == 'UTC'; print('OK')"</automated>
  </verify>
  <acceptance_criteria>
    - File `src/config.py` exists and contains a class named `Settings(BaseSettings)`
    - `grep -E 'class Settings\(BaseSettings\):' src/config.py` matches exactly once
    - `grep -E 'telegram_bot_token: SecretStr' src/config.py` matches
    - `grep -E 'def load_settings\(\) -> Settings:' src/config.py` matches
    - `grep -E 'env_file=".env"' src/config.py` matches (confirms .env loading is configured)
    - Files exist: `src/__init__.py`, `src/__main__.py`, `src/bot/__init__.py`, `src/db/__init__.py`, `src/scheduler/__init__.py`
    - File `.env.example` exists and contains lines starting with: `TELEGRAM_BOT_TOKEN=`, `TELEGRAM_ALLOWED_CHAT_IDS=`, `TELEGRAM_ALLOWED_USER_IDS=`, `META_APP_ID=`, `META_APP_SECRET=`, `META_ACCESS_TOKEN=`, `META_AD_ACCOUNT_ID=`, `GA4_PROPERTY_ID=`, `GA4_SERVICE_ACCOUNT_JSON=`, `ANTHROPIC_API_KEY=`, `DB_PATH=`, `LOG_LEVEL=`, `REPORT_TIMEZONE=` (one match per pattern via `grep -E '^TELEGRAM_BOT_TOKEN=' .env.example` etc.)
    - File `.gitignore` contains literal lines: `.env`, `data/`, `*.db`, `__pycache__/`, `.venv/`
    - File `.dockerignore` contains literal lines: `.env`, `data/`, `tests/`, `.git`
    - Loading `Settings()` with no env vars set raises `pydantic.ValidationError` (required field `telegram_bot_token` is missing) — proven by the inverse: setting `TELEGRAM_BOT_TOKEN` makes it load successfully (the automated verify command)
    - The CSV validator correctly parses `"123,456"` into `[123, 456]` (covered by automated verify)
  </acceptance_criteria>
  <done>The `src/` package is importable, `load_settings()` returns a fully typed Settings object when env vars are present, fails fast with ValidationError when `TELEGRAM_BOT_TOKEN` is missing, and `.env.example` mirrors every Settings field one-to-one. `.gitignore` and `.dockerignore` exclude all secret/database/cache paths.</done>
</task>

</tasks>

<verification>
- `python -c "import tomllib, pathlib; tomllib.loads(pathlib.Path('pyproject.toml').read_text())"` exits 0
- `python -c "import sys; sys.path.insert(0,'.'); from src.config import Settings; print(Settings.model_fields.keys())"` lists all 13 fields
- `grep -c '^' .env.example` >= 17 (header comments + 13 var lines)
- All 6 scaffold files (`src/__init__.py`, `src/__main__.py`, `src/config.py`, `src/bot/__init__.py`, `src/db/__init__.py`, `src/scheduler/__init__.py`) exist
- `git check-ignore .env` exits 0 (confirms `.env` is ignored)
</verification>

<success_criteria>
INFRA-01 foundation is laid: typed Settings class with SecretStr-wrapped credentials, env-only secret loading (no values in source), .env.example as a checked-in template, and a package layout ready for Plans 02–04 to fill in. Running `python -c "from src.config import load_settings; load_settings()"` with no environment yields a ValidationError (fail-fast) and with the values set in .env.example would yield a populated Settings instance.
</success_criteria>

<output>
After completion, create `.planning/phases/01-foundation-walking-skeleton/01-scaffold-SUMMARY.md` describing:
- pyproject.toml dependency list (exact versions)
- Settings class field list and which phase each field is consumed by
- Any deviations from RESEARCH Pattern 1 (e.g., field renames for INFRA-01 mapping)
- Confirmation that .env is gitignored
</output>
