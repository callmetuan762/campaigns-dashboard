---
plan: "01-telegram-bot"
phase: 1
wave: 2
depends_on: ["01-scaffold", "01-database"]
autonomous: true
files_modified:
  - src/bot/middleware.py
  - src/bot/handlers.py
  - src/bot/setup.py
  - tests/test_allowlist.py
requirements_addressed:
  - INFRA-02
must_haves:
  truths:
    - "AllowlistMiddleware drops every Telegram update where neither chat_id nor user_id is on the configured allowlist — proven by an automated test"
    - "Allowed updates (chat_id OR user_id matches) pass through to the handler"
    - "Allowlist rejection is logged via structlog with chat_id, user_id, and event_type fields, but NEVER the message text"
    - "Three commands are registered: /start (returns 'Ads Reporting Agent online. Use /report for latest data.'), /status (returns last sync timestamps and row counts from DBClient), /help (returns command list)"
    - "create_bot_and_dispatcher(settings, db_client) returns a fully wired (Bot, Dispatcher) tuple with AllowlistMiddleware attached on dp.message.middleware AND dp.callback_query.middleware BEFORE any handler can run"
  artifacts:
    - path: "src/bot/middleware.py"
      provides: "AllowlistMiddleware class inheriting aiogram.BaseMiddleware"
      exports: ["AllowlistMiddleware"]
    - path: "src/bot/handlers.py"
      provides: "Router with /start, /status, /help command handlers"
      exports: ["build_router"]
    - path: "src/bot/setup.py"
      provides: "create_bot_and_dispatcher() factory wiring bot + dispatcher + middleware + handlers"
      exports: ["create_bot_and_dispatcher"]
    - path: "tests/test_allowlist.py"
      provides: "Pytest proving INFRA-02 — synthetic non-allowlisted update is dropped, allowlisted update reaches handler"
      contains: "test_disallowed_chat_dropped, test_allowed_chat_passes, test_allowed_user_passes, test_message_text_not_logged"
  key_links:
    - from: "src/bot/setup.py:create_bot_and_dispatcher"
      to: "AllowlistMiddleware"
      via: "dp.message.middleware(allowlist); dp.callback_query.middleware(allowlist) BEFORE include_router"
      pattern: "dp\\.message\\.middleware|dp\\.callback_query\\.middleware"
    - from: "src/bot/handlers.py:/status"
      to: "src/db/client.py:DBClient"
      via: "db.get_last_sync() + db.get_row_counts() injected via dispatcher workflow_data"
      pattern: "get_last_sync|get_row_counts"
    - from: "src/bot/middleware.py:AllowlistMiddleware.__call__"
      to: "structlog logger"
      via: "logger.info('rejected_update', chat_id=..., user_id=..., event_type=...) — no message.text"
      pattern: "rejected_update"
---

<objective>
Build the Telegram bot subsystem: AllowlistMiddleware (the project's #1 security control), three Phase 1 command handlers, and a `create_bot_and_dispatcher` factory that wires them in the correct order. Cover INFRA-02 ("Telegram bot enforces a strict allowlist of permitted chat IDs and user IDs before executing any command or Claude call") with an automated pytest proof.

Purpose: INFRA-02 is a security non-negotiable per CLAUDE.md. The middleware must execute BEFORE the handler chain — that ordering is what protects every future Claude call (Phase 4) from drive-by users draining the Anthropic budget. The handler set is intentionally minimal so the entire Phase 1 deliverable can be exercised end-to-end (a real user sending /start to the bot).

Output: An importable bot setup that, given a Settings + DBClient, produces a `(Bot, Dispatcher)` pair with the allowlist active and three commands registered, plus a passing test proving disallowed updates are silently dropped.
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
This plan consumes interfaces from Plans 01 and 02, and produces an interface for Plan 04:

```python
# Consumed from src/config.py (Plan 01):
class Settings:
    telegram_bot_token: SecretStr
    telegram_allowed_chat_ids: list[int]
    telegram_allowed_user_ids: list[int]

# Consumed from src/db/client.py (Plan 02):
class DBClient:
    async def get_row_counts(self) -> dict[str, int]: ...
    async def get_last_sync(self) -> dict[str, str | None]: ...

# Produced for src/main.py (Plan 04):
def create_bot_and_dispatcher(settings: Settings, db_client: DBClient) -> tuple[Bot, Dispatcher]: ...
```

aiogram 3 API reference (see RESEARCH Pattern 2–3):
- `BaseMiddleware.__call__(self, handler, event, data)` — return `await handler(event, data)` to pass, return `None` to drop.
- `dp.message.middleware(mw_instance)` and `dp.callback_query.middleware(mw_instance)` — both registrations required.
- `dispatcher.workflow_data` — dict passed into every handler invocation; use to inject DBClient.
- `Bot(token=...)` accepts a plain string token.
</interfaces>
</context>

<tasks>

<task type="auto">
  <name>Task 1: Implement AllowlistMiddleware with OR semantics and structlog rejection logging</name>
  <files>src/bot/middleware.py, tests/test_allowlist.py</files>
  <read_first>
    - .planning/phases/01-foundation-walking-skeleton/01-RESEARCH.md (Pattern 2: aiogram 3 Allowlist Middleware, lines 263–342; Pitfall 1 at lines 890–895; A1 assumption at line 966)
    - CLAUDE.md (Security Non-Negotiables section — confirms chat-id allowlist BEFORE any handler/Claude call)
    - src/config.py (Plan 01 — confirm telegram_allowed_chat_ids and telegram_allowed_user_ids field names)
    - pyproject.toml (confirm aiogram>=3.28 and pytest-asyncio are declared)
  </read_first>
  <action>
**Create `src/bot/middleware.py`** with exactly this content:

```python
"""Telegram update allowlist middleware.

INFRA-02 / CLAUDE.md Security Non-Negotiable #1:
    The Telegram bot enforces a strict allowlist of permitted chat IDs AND user IDs
    BEFORE executing any command or Claude call. Non-allowlisted updates are
    silently dropped (never replied to — replying confirms the bot's existence
    to drive-by probers, per RESEARCH.md PITFALLS.md).

Semantics: OR — an update is allowed if chat_id is in the chat allowlist OR
user_id is in the user allowlist. This lets the team group be the trust boundary
(every member of the group is implicitly trusted) while still permitting solo
DMs from specifically allowlisted users.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

logger = structlog.get_logger(__name__)


class AllowlistMiddleware(BaseMiddleware):
    """Drop Telegram updates whose chat AND user are both outside the allowlist."""

    def __init__(
        self,
        allowed_chat_ids: set[int],
        allowed_user_ids: set[int],
    ) -> None:
        self._chats = set(allowed_chat_ids)
        self._users = set(allowed_user_ids)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        chat_id, user_id = self._extract_ids(event)

        if (chat_id is not None and chat_id in self._chats) or (
            user_id is not None and user_id in self._users
        ):
            return await handler(event, data)

        # Silent drop. Do NOT reply. Do NOT echo message text into the log.
        logger.info(
            "rejected_update",
            chat_id=chat_id,
            user_id=user_id,
            event_type=type(event).__name__,
        )
        return None

    @staticmethod
    def _extract_ids(event: TelegramObject) -> tuple[int | None, int | None]:
        if isinstance(event, Message):
            return (
                event.chat.id,
                event.from_user.id if event.from_user else None,
            )
        if isinstance(event, CallbackQuery):
            return (
                event.message.chat.id if event.message else None,
                event.from_user.id if event.from_user else None,
            )
        return None, None
```

**Create `tests/test_allowlist.py`** that proves the security control works WITHOUT requiring a live Telegram connection. Use aiogram type constructors with synthetic `Message` objects:

```python
"""Prove INFRA-02: AllowlistMiddleware drops non-allowlisted updates and logs without PII."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
import structlog
from aiogram.types import Chat, Message, User

from src.bot.middleware import AllowlistMiddleware

pytestmark = pytest.mark.asyncio


def _msg(chat_id: int, user_id: int, text: str = "secret content") -> Message:
    return Message(
        message_id=1,
        date=datetime.now(timezone.utc),
        chat=Chat(id=chat_id, type="private"),
        from_user=User(id=user_id, is_bot=False, first_name="Test"),
        text=text,
    )


async def _capture_handler(call_log: list):
    async def handler(event, data):
        call_log.append(event)
        return "HANDLED"
    return handler


async def test_disallowed_chat_dropped():
    mw = AllowlistMiddleware(allowed_chat_ids={111}, allowed_user_ids={222})
    calls: list = []
    h = await _capture_handler(calls)
    result = await mw(h, _msg(chat_id=999, user_id=888), {})
    assert result is None, "non-allowlisted update must return None"
    assert calls == [], "handler must NOT have been invoked"


async def test_allowed_chat_passes():
    mw = AllowlistMiddleware(allowed_chat_ids={111}, allowed_user_ids=set())
    calls: list = []
    h = await _capture_handler(calls)
    result = await mw(h, _msg(chat_id=111, user_id=999), {})
    assert result == "HANDLED"
    assert len(calls) == 1


async def test_allowed_user_passes():
    """OR semantics: user-id match alone grants access even if chat is unknown."""
    mw = AllowlistMiddleware(allowed_chat_ids=set(), allowed_user_ids={222})
    calls: list = []
    h = await _capture_handler(calls)
    result = await mw(h, _msg(chat_id=999, user_id=222), {})
    assert result == "HANDLED"
    assert len(calls) == 1


async def test_message_text_not_logged(caplog):
    """Rejection log must contain chat_id/user_id/event_type but NEVER message text."""
    # Configure structlog to write through stdlib for caplog capture.
    structlog.configure(
        processors=[structlog.processors.KeyValueRenderer()],
        logger_factory=structlog.PrintLoggerFactory(),
    )
    mw = AllowlistMiddleware(allowed_chat_ids={111}, allowed_user_ids={222})
    sentinel = "supersecret-injection-attempt-abc123"
    result = await mw(
        (lambda e, d: None),  # never called
        _msg(chat_id=999, user_id=888, text=sentinel),
        {},
    )
    assert result is None
    # The sentinel string must NEVER appear in any captured log output.
    # caplog captures stdlib logging; structlog with PrintLoggerFactory goes to stdout.
    # We capture stdout for the check.
    import io
    import sys
    buf = io.StringIO()
    saved = sys.stdout
    sys.stdout = buf
    try:
        await mw((lambda e, d: None), _msg(999, 888, sentinel), {})
    finally:
        sys.stdout = saved
    assert sentinel not in buf.getvalue(), "message text must never appear in rejection logs"
```

Implementation notes:
- The middleware uses `set` membership for O(1) lookups — `Settings.telegram_allowed_*` arrives as `list[int]` and is converted in `create_bot_and_dispatcher` (Task 2).
- Returning `None` from a middleware's `__call__` short-circuits dispatch in aiogram 3 — no handler runs.
- Per RESEARCH PITFALLS.md, NEVER reply to non-allowlisted senders (replying confirms bot existence; aids username probing).
- Per RESEARCH Pattern 7 logging discipline, NEVER include `message.text` in log fields — only the IDs and the event class name.
  </action>
  <verify>
    <automated>python -m pytest tests/test_allowlist.py -v -x --tb=short</automated>
  </verify>
  <acceptance_criteria>
    - File `src/bot/middleware.py` exists with `class AllowlistMiddleware(BaseMiddleware):`
    - `grep -E 'class AllowlistMiddleware\(BaseMiddleware\):' src/bot/middleware.py` matches once
    - `grep -E 'async def __call__' src/bot/middleware.py` matches once
    - `grep -E 'rejected_update' src/bot/middleware.py` matches (structlog event name)
    - `grep -nE 'message\.text|event\.text' src/bot/middleware.py` returns NO matches (text never read into logs)
    - File `tests/test_allowlist.py` defines four tests: `test_disallowed_chat_dropped`, `test_allowed_chat_passes`, `test_allowed_user_passes`, `test_message_text_not_logged`
    - `python -m pytest tests/test_allowlist.py -x` exits 0 (all four tests pass)
    - The disallowed-chat test asserts the underlying handler was NEVER invoked (this is the security proof — drops happen BEFORE handler dispatch)
  </acceptance_criteria>
  <done>AllowlistMiddleware drops non-allowlisted updates with OR semantics, logs only structured ID fields (no message text), and the security guarantee is verified by passing pytest. The four tests collectively prove INFRA-02.</done>
</task>

<task type="auto">
  <name>Task 2: Build /start, /status, /help handlers and create_bot_and_dispatcher factory</name>
  <files>src/bot/handlers.py, src/bot/setup.py</files>
  <read_first>
    - .planning/phases/01-foundation-walking-skeleton/01-RESEARCH.md (Pattern 3: aiogram 3 Long-Polling Bot Setup, lines 343–387)
    - src/bot/middleware.py (Task 1 output)
    - src/config.py (Plan 01)
    - src/db/client.py (Plan 02 — confirm get_row_counts and get_last_sync signatures)
  </read_first>
  <action>
**Create `src/bot/handlers.py`** with exactly:

```python
"""Phase 1 Telegram command handlers: /start, /status, /help.

Handlers access DBClient via dispatcher.workflow_data['db'] — wired in setup.py.
"""
from __future__ import annotations

import structlog
from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from src.db.client import DBClient

logger = structlog.get_logger(__name__)


def build_router() -> Router:
    router = Router(name="phase1_commands")

    @router.message(CommandStart())
    async def cmd_start(message: Message) -> None:
        logger.info("cmd_start", chat_id=message.chat.id)
        await message.answer("Ads Reporting Agent online. Use /report for latest data.")

    @router.message(Command("status"))
    async def cmd_status(message: Message, db: DBClient) -> None:
        last = await db.get_last_sync()
        counts = await db.get_row_counts()
        lines = [
            "*Status*",
            f"Meta last sync: `{last.get('meta_ads') or 'never'}`",
            f"GA4 last sync: `{last.get('ga4') or 'never'}`",
            "",
            "*Row counts*",
            f"campaigns: `{counts.get('campaigns', 0)}`",
            f"ad_metrics: `{counts.get('ad_metrics', 0)}`",
            f"ga4_metrics: `{counts.get('ga4_metrics', 0)}`",
            f"bot_conversations: `{counts.get('bot_conversations', 0)}`",
        ]
        logger.info("cmd_status", chat_id=message.chat.id)
        await message.answer("\n".join(lines))

    @router.message(Command("help"))
    async def cmd_help(message: Message) -> None:
        logger.info("cmd_help", chat_id=message.chat.id)
        await message.answer(
            "*Available commands*\n"
            "/start — confirm bot is online\n"
            "/status — show last sync time and row counts\n"
            "/help — show this message\n"
            "_(more commands ship in Phase 2)_"
        )

    return router
```

**Create `src/bot/setup.py`** with exactly:

```python
"""Factory: build the Bot + Dispatcher with allowlist + handlers wired in correct order.

Critical ordering (per CLAUDE.md security non-negotiable + RESEARCH Pitfall 1):
    1. Build Bot + Dispatcher
    2. Inject DBClient into dispatcher.workflow_data (so handlers receive it)
    3. Register AllowlistMiddleware on dp.message.middleware AND dp.callback_query.middleware
    4. THEN include the handler router

Caller (src/main.py — Plan 04) must NOT register additional routers before this returns.
"""
from __future__ import annotations

import structlog
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from src.bot.handlers import build_router
from src.bot.middleware import AllowlistMiddleware
from src.config import Settings
from src.db.client import DBClient

logger = structlog.get_logger(__name__)


def create_bot_and_dispatcher(
    settings: Settings,
    db_client: DBClient,
) -> tuple[Bot, Dispatcher]:
    """Return a (Bot, Dispatcher) pair with allowlist and handlers wired.

    The caller is responsible for: bot.delete_webhook(drop_pending_updates=True)
    BEFORE dp.start_polling(bot), and for graceful shutdown via bot.session.close().
    """
    bot = Bot(
        token=settings.telegram_bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN),
    )
    dp = Dispatcher()

    # Inject DBClient so handlers can declare `db: DBClient` as a parameter.
    dp["db"] = db_client

    # STEP 1: Register allowlist BEFORE any router. Order is security-critical.
    allowlist = AllowlistMiddleware(
        allowed_chat_ids=set(settings.telegram_allowed_chat_ids),
        allowed_user_ids=set(settings.telegram_allowed_user_ids),
    )
    dp.message.middleware(allowlist)
    dp.callback_query.middleware(allowlist)

    # STEP 2: Include handler router AFTER middleware registration.
    dp.include_router(build_router())

    logger.info(
        "bot_dispatcher_ready",
        allowed_chats=len(settings.telegram_allowed_chat_ids),
        allowed_users=len(settings.telegram_allowed_user_ids),
    )
    return bot, dp
```

Implementation notes:
- aiogram 3 supports keyword injection of `dp[...]` values directly into handler signatures — declaring `async def cmd_status(message: Message, db: DBClient)` causes aiogram to pass the injected `db` automatically.
- `ParseMode.MARKDOWN` (NOT `MARKDOWN_V2`) is used for Phase 1 simplicity — Phase 2 will introduce an `escape_md_v2` helper when long generated reports require it. MARKDOWN is more forgiving for the short status text we send in Phase 1.
- The middleware MUST be registered before `include_router` — this is the central lesson from RESEARCH Pitfall 1.
- Do NOT call `bot.delete_webhook()` here — that's the responsibility of `main.py` (Plan 04) so the factory remains side-effect-free and easy to unit-test.
  </action>
  <verify>
    <automated>python -c "import sys, os, tempfile, asyncio, pathlib; sys.path.insert(0,'.');
os.environ['TELEGRAM_BOT_TOKEN']='123:fake'; os.environ['TELEGRAM_ALLOWED_CHAT_IDS']='111'; os.environ['TELEGRAM_ALLOWED_USER_IDS']='222';
from aiogram import Bot, Dispatcher
from src.config import load_settings
from src.db.client import DBClient
from src.bot.setup import create_bot_and_dispatcher
async def t():
    with tempfile.TemporaryDirectory() as td:
        db = DBClient(pathlib.Path(td)/'t.db')
        await db.connect()
        s = load_settings()
        bot, dp = create_bot_and_dispatcher(s, db)
        # Behavioral: factory returns the correct types
        assert isinstance(bot, Bot), f'expected Bot, got {type(bot)}'
        assert isinstance(dp, Dispatcher), f'expected Dispatcher, got {type(dp)}'
        # Behavioral: db is injected so handlers can declare db: DBClient
        assert dp['db'] is db, 'DBClient not injected into dispatcher workflow_data'
        # Behavioral: a router was included (handlers reachable)
        assert len(dp.sub_routers) == 1, f'expected 1 router, got {dp.sub_routers}'
        await bot.session.close(); await db.close()
        print('OK')
asyncio.run(t())"
# Behavioral proof that AllowlistMiddleware actually drops non-allowlisted updates
# lives in tests/test_allowlist.py (Task 1) — running it again here is redundant.
# This verify focuses on the factory's contract; the security guarantee is owned by Task 1.
python -m pytest tests/test_allowlist.py -v -x --tb=short</automated>
  </verify>
  <acceptance_criteria>
    - File `src/bot/handlers.py` defines `build_router() -> Router`
    - `grep -E 'def build_router\(\) -> Router' src/bot/handlers.py` matches once
    - Handler for `/start` answers exactly: `"Ads Reporting Agent online. Use /report for latest data."`
    - `grep -F 'Ads Reporting Agent online. Use /report for latest data.' src/bot/handlers.py` matches once
    - Handler for `/status` calls both `db.get_last_sync()` and `db.get_row_counts()`
    - `grep -E 'get_last_sync\(\)' src/bot/handlers.py` matches; `grep -E 'get_row_counts\(\)' src/bot/handlers.py` matches
    - Handler for `/help` exists with `Command("help")` filter
    - File `src/bot/setup.py` defines `create_bot_and_dispatcher(settings, db_client) -> tuple[Bot, Dispatcher]`
    - `grep -E 'def create_bot_and_dispatcher' src/bot/setup.py` matches once
    - `grep -E 'dp\.message\.middleware\(allowlist\)' src/bot/setup.py` matches
    - `grep -E 'dp\.callback_query\.middleware\(allowlist\)' src/bot/setup.py` matches
    - `grep -E 'dp\.include_router\(build_router\(\)\)' src/bot/setup.py` matches
    - In the source order of setup.py, BOTH `dp.message.middleware(...)` lines appear BEFORE `dp.include_router(...)` — verifiable with: `python -c "import re,pathlib; s=pathlib.Path('src/bot/setup.py').read_text(); a=re.search(r'dp\.message\.middleware', s).start(); b=re.search(r'dp\.include_router', s).start(); assert a < b, 'middleware must be registered before router'; print('order OK')"`
    - Automated verify command exits 0: confirms the factory returns (Bot, Dispatcher), `dp['db']` is the injected client, the handler router is included, AND `tests/test_allowlist.py` (Task 1) passes — providing the behavioral proof that AllowlistMiddleware drops non-allowlisted updates (no reliance on aiogram private attrs like `_middlewares`)
  </acceptance_criteria>
  <done>Calling `create_bot_and_dispatcher(settings, db_client)` returns a `(Bot, Dispatcher)` pair where AllowlistMiddleware is registered on both `message` and `callback_query` observers BEFORE the handler router. The /start, /status, and /help commands are wired and /status pulls live row counts and sync timestamps from the DB. Plan 04 imports this function directly.</done>
</task>

</tasks>

<verification>
- `python -m pytest tests/test_allowlist.py -v` reports 4 passed
- Source-order check: `python -c "import pathlib,re; s=pathlib.Path('src/bot/setup.py').read_text(); assert re.search(r'dp\.message\.middleware', s).start() < re.search(r'dp\.include_router', s).start(); print('order OK')"` exits 0
- `grep -rE 'message\.text|event\.text' src/bot/middleware.py` returns nothing (no PII paths in middleware)
- `python -c "from src.bot.setup import create_bot_and_dispatcher; from src.bot.middleware import AllowlistMiddleware; from src.bot.handlers import build_router; print('imports OK')"` exits 0
</verification>

<success_criteria>
INFRA-02 fully closed: the allowlist middleware drops non-allowlisted updates BEFORE any handler runs (proven by passing pytest); /start, /status, /help are registered and accessible to allowlisted users; the create_bot_and_dispatcher factory enforces the security-critical middleware-before-router order. Plan 04 can compose this with `src/main.py` without rewriting any of it.
</success_criteria>

<output>
After completion, create `.planning/phases/01-foundation-walking-skeleton/01-telegram-bot-SUMMARY.md` describing:
- AllowlistMiddleware OR-semantics decision and the rationale (A1 from RESEARCH)
- The exact /start, /status, /help message text/templates
- The middleware-before-router ordering and where it's enforced in setup.py
- The four allowlist tests and what each one proves
</output>
