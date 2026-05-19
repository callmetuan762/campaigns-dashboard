---
phase: 01-foundation-walking-skeleton
reviewed: 2026-05-19T00:00:00Z
depth: standard
files_reviewed: 16
files_reviewed_list:
  - src/config.py
  - src/db/schema.py
  - src/db/migrations.py
  - src/db/client.py
  - src/bot/middleware.py
  - src/bot/handlers.py
  - src/bot/setup.py
  - src/logging_setup.py
  - src/main.py
  - src/__main__.py
  - tests/conftest.py
  - tests/test_upsert_idempotency.py
  - tests/test_allowlist.py
  - Dockerfile
  - docker-compose.yml
  - pyproject.toml
findings:
  critical: 1
  warning: 3
  info: 4
  total: 8
status: issues_found
---

# Phase 1: Code Review Report

**Reviewed:** 2026-05-19T00:00:00Z
**Depth:** standard
**Files Reviewed:** 16
**Status:** issues_found

## Summary

The Phase 1 foundation is well-structured. Security non-negotiables from CLAUDE.md are largely satisfied: `AllowlistMiddleware` is registered before any router in `setup.py`, all credentials use `SecretStr`, structlog redaction is in place, and there is no f-string SQL in the UPSERT helpers or query helpers. The DB layer correctly uses parameterized queries throughout — with one exception.

One critical violation of the project's "no f-string SQL" rule exists in `get_row_counts`. Three warnings cover: a dangerous `executescript` side-effect in migrations, missing escape/validation of dynamic data in Markdown responses, and a missing boot-time guard against a fully-empty allowlist configuration. Four info items cover test isolation, the missing lock file, a weak healthcheck, and absence of a boot-time warning for empty allowlists.

---

## Critical Issues

### CR-01: f-string SQL interpolation in `get_row_counts`

**File:** `src/db/client.py:123`

**Issue:** The table name is interpolated directly into a SQL string via an f-string:

```python
row = await self.fetch_one(f"SELECT COUNT(*) AS n FROM {table}")
```

This violates the project rule "no f-string SQL" (CLAUDE.md). Although the `table` variable is populated from a hardcoded tuple in the same method (line 122) and is therefore safe today, the pattern is categorically prohibited because:

1. It normalizes f-string SQL in the codebase, making it easy for future contributors to copy and introduce actual injection.
2. If the tuple is ever extended with a value derived from user input or an environment variable, the injection becomes real with no code-review signal to catch it.

**Fix:** Use a safe allowlist dispatch with parameterized queries or format only known-safe identifiers explicitly:

```python
_ALLOWED_COUNT_TABLES = frozenset(
    {"campaigns", "ad_metrics", "ga4_metrics", "bot_conversations"}
)

async def get_row_counts(self) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in _ALLOWED_COUNT_TABLES:
        # Table names cannot be parameterized in SQLite; validate against
        # an explicit allowlist before interpolating.
        assert table in _ALLOWED_COUNT_TABLES  # belt-and-suspenders
        row = await self.fetch_one(f"SELECT COUNT(*) AS n FROM {table}")  # noqa: S608
        counts[table] = int(row["n"]) if row else 0
    return counts
```

The `noqa: S608` suppresses the Ruff S608 warning at the one intentional, validated callsite rather than normalizing the unchecked pattern. Alternatively, remove the f-string entirely by hardcoding four individual `fetch_one` calls.

---

## Warnings

### WR-01: `executescript` silently commits any open transaction in migrations

**File:** `src/db/migrations.py:39`

**Issue:** The aiosqlite (and underlying sqlite3) `executescript()` method issues an implicit `COMMIT` before executing its SQL block. This is documented Python behavior. In the current migration runner, the sequence is:

```
await conn.executescript(sql)        # <-- implicit COMMIT here
await conn.execute("INSERT OR REPLACE INTO schema_version ...")
await conn.commit()
```

If `executescript` raises mid-script (e.g., a DDL statement fails after one table is created), the partial DDL is committed but the `schema_version` row is never written. On the next startup, the migration re-runs and the already-created tables silently re-execute their `CREATE TABLE IF NOT EXISTS` (harmless for DDL, but the window exists for data-altering migrations in future phases to run twice).

**Fix:** Wrap the migration body in an explicit transaction using `BEGIN`/`COMMIT` inside the SQL string itself (since `executescript` cannot be wrapped in an outer aiosqlite transaction), and record the schema_version row inside the same script:

```python
migration_sql = (
    "BEGIN;\n"
    + sql
    + f"\nINSERT OR REPLACE INTO schema_version (version) VALUES ('{version}');\n"
    "COMMIT;\n"
)
await conn.executescript(migration_sql)
applied.append(version)
```

This makes the DDL and the version record atomic within the script itself. Remove the separate `INSERT OR REPLACE` and `await conn.commit()` lines that follow.

---

### WR-02: Dynamic database values interpolated into Markdown without escaping

**File:** `src/bot/handlers.py:29-41`

**Issue:** The `/status` handler builds a Telegram message with `ParseMode.MARKDOWN` (set globally in `setup.py:37`) and interpolates database-sourced values directly into the string:

```python
f"Meta last sync: `{last.get('meta_ads') or 'never'}`",
f"GA4 last sync: `{last.get('ga4') or 'never'}`",
f"campaigns: `{counts.get('campaigns', 0)}`",
```

If a `fetched_at` timestamp stored in SQLite ever contains a Markdown special character (underscore is common in ISO timestamps on some locales, and backtick sequences can break code-span parsing), Telegram will return a `BadRequest: can't parse entities` error and the status message will silently fail to deliver.

The numeric row counts are safe (integers), but the timestamp strings come from `datetime('now')` which produces values like `2026-05-19 12:34:56` — safe today. The risk is low in Phase 1 but becomes real in Phase 2 when campaign names (which can contain arbitrary characters) are surfaced in reports.

**Fix:** For Phase 1 the immediate fix is to add a simple escape helper and apply it to all non-literal string values:

```python
def _md_escape(value: str) -> str:
    """Escape characters that break Telegram legacy Markdown."""
    for ch in ("_", "*", "`", "["):
        value = value.replace(ch, f"\\{ch}")
    return value

# In the handler:
f"Meta last sync: `{_md_escape(last.get('meta_ads') or 'never')}`",
```

For Phase 2+ (where campaign names appear in output), switch to `ParseMode.MARKDOWN_V2` which has a well-defined, comprehensive escape spec.

---

### WR-03: No boot-time guard when both allowlists are empty

**File:** `src/bot/setup.py:44-48` / `src/config.py:19-20`

**Issue:** Both `telegram_allowed_chat_ids` and `telegram_allowed_user_ids` default to empty lists (`Field(default_factory=list)`). If a deployment ships without either variable set, `AllowlistMiddleware` is instantiated with two empty sets, and every Telegram update is silently dropped — the bot appears to run but responds to nobody. There is no warning or error at startup.

This is a misconfiguration failure mode that is easy to trigger (e.g., a new deployment from a fresh `.env` template) and difficult to diagnose because the bot starts cleanly with no error output.

**Fix:** Add a `model_validator` (or a check in `create_bot_and_dispatcher`) that raises or warns when both lists are empty:

```python
# In src/config.py, add after the field_validator:
from pydantic import model_validator

@model_validator(mode="after")
def _warn_empty_allowlist(self) -> "Settings":
    if not self.telegram_allowed_chat_ids and not self.telegram_allowed_user_ids:
        import warnings
        warnings.warn(
            "Both TELEGRAM_ALLOWED_CHAT_IDS and TELEGRAM_ALLOWED_USER_IDS are empty. "
            "The bot will silently reject ALL incoming messages.",
            stacklevel=2,
        )
    return self
```

Or raise a `ValueError` to prevent the bot from starting at all in this configuration.

---

## Info

### IN-01: `structlog.configure()` in test mutates global state for subsequent tests

**File:** `tests/test_allowlist.py:65-68`

**Issue:** `test_message_text_not_logged` calls `structlog.configure(...)` globally at test execution time. structlog configuration is process-global and not reset between tests. If this test runs before any other test that relies on the logging configuration set by `configure_logging()`, those tests will use the test's minimal processor chain instead of the production pipeline, potentially masking redaction failures.

**Fix:** Restore structlog state after the test, or use `unittest.mock.patch` to isolate the configuration change:

```python
import structlog
from structlog.testing import capture_logs

async def test_message_text_not_logged():
    """Rejection log must not contain message text."""
    mw = AllowlistMiddleware(allowed_chat_ids={111}, allowed_user_ids={222})
    sentinel = "supersecret-injection-attempt-abc123"

    with capture_logs() as cap:
        await mw((lambda e, d: None), _msg(999, 888, sentinel), {})

    all_log_text = str(cap)
    assert sentinel not in all_log_text, "message text must never appear in rejection logs"
```

`structlog.testing.capture_logs()` is a context manager that temporarily redirects structlog output without mutating the global configuration.

---

### IN-02: `uv.lock` not committed — builds are non-reproducible

**File:** `Dockerfile:17`

**Issue:** The Dockerfile copies `uv.lock*` (with glob wildcard) suggesting the lock file is optional. The lock file does not appear to be committed to the repository. Without a committed `uv.lock`, `uv sync` resolves dependencies fresh on each build, which can silently pick up newer patch versions of dependencies (including security-sensitive ones like `cryptography`, `aiogram`, `anthropic`).

**Fix:** Generate and commit `uv.lock`:

```bash
uv lock
git add uv.lock
git commit -m "chore: commit uv.lock for reproducible builds"
```

Then change the Dockerfile COPY line to fail loudly if the file is missing:

```dockerfile
COPY pyproject.toml uv.lock ./
```

(Remove the `*` glob so the build fails rather than proceeding without a lock file.)

---

### IN-03: Healthcheck verifies file existence, not process liveness

**File:** `Dockerfile:54-55`

**Issue:** The Docker healthcheck confirms that the SQLite database file exists on disk:

```
CMD python -c "import os, sys; sys.exit(0 if os.path.exists(...) else 1)"
```

Once the file is created on first startup, this check will always pass — even if the bot process has crashed, the Telegram polling loop has exited, or the scheduler has died. Docker will report the container as `healthy` while the bot is actually unresponsive.

**Fix:** For Phase 1, a more useful probe checks whether the process is still running and the SQLite file is writable:

```dockerfile
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD python -c "
import os, sys, sqlite3
p = os.environ.get('DB_PATH', '/data/metrics.db')
try:
    conn = sqlite3.connect(p, timeout=3)
    conn.execute('SELECT 1')
    conn.close()
    sys.exit(0)
except Exception:
    sys.exit(1)
"
```

A Phase 5 / ops-hardening improvement would use a dedicated `/healthz` HTTP endpoint or a pid-file check.

---

### IN-04: `telegram_allowed_chat_ids` validator accepts negative integers

**File:** `src/config.py:63`

**Issue:** The `_split_csv` validator applies `int(x.strip())` without range checking. Negative integers are accepted silently (e.g., `TELEGRAM_ALLOWED_CHAT_IDS=-123456789`). Telegram group/supergroup chat IDs are negative integers (e.g., `-1001234567890`), so this is actually intentional — but the code and comments do not document this, and the validator does not reject values that are clearly invalid (e.g., `0`, very large integers outside the Telegram ID space).

This is an info item rather than a bug because the current behavior is functionally correct for the Telegram use case. A brief inline comment clarifying that negative IDs are expected for groups would prevent future "fix" PRs that add a `>= 0` check and break group chat support.

**Fix:** Add a comment:

```python
# Telegram group/channel IDs are negative integers (e.g. -1001234567890).
# Do NOT add a >= 0 check here.
return [int(x.strip()) for x in stripped.split(",") if x.strip()]
```

---

_Reviewed: 2026-05-19T00:00:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
