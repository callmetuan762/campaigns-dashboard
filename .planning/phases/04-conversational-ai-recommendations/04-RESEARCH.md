# Phase 4: Conversational AI + Recommendations — Research

**Researched:** 2026-05-19
**Domain:** Anthropic tool-use multi-turn loop, aiogram 3.x inline keyboards, SQLite conversation persistence
**Confidence:** HIGH

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** `claude-sonnet-4-6` for all chat/Q&A. `claude-haiku-4-5` stays for TL;DR (Phase 2).
- **D-02:** `ANTHROPIC_MONTHLY_BUDGET_USD = 20.0` added to Settings (env-configurable).
- **D-03:** `anthropic_usage_log` SQLite table tracks per-call cost (schema in D-19).
- **D-04:** On budget exhaustion: graceful user message + operator alert; refuse all Claude calls until next month.
- **D-05:** `max_tokens=2048` per chat request.
- **D-06:** Conversation scope: per `(chat_id, user_id)`. Group-chat users each get independent threads.
- **D-07:** History window: last 10 turns, chronological order.
- **D-08:** Serialization: plain text for text turns; `json.dumps(content_list)` for tool_use/tool_result turns.
- **D-09:** `/clear` command in `handlers.py` — deletes user conversation rows.
- **D-10:** New `src/bot/chat_router.py` with catch-all `MessageHandler(F.text & ~F.text.startswith("/"))`.
- **D-11:** Typing indicator via `bot.send_chat_action(chat_id, "typing")` before Claude call.
- **D-12:** Five tools: `query_metrics`, `compare_periods`, `get_campaign_detail`, `list_underperformers`, `get_landing_page_performance`. SQLite-only, no live API calls.
- **D-13:** Tool input validation: `metric` and `source` params validated against `frozenset` before SQL.
- **D-14:** Four inline buttons after each answer: "Drill down", "Compare to last week", "Why is this happening?", "Show chart".
- **D-15:** Button taps (except "Show chart") go through AI handler; injected text saved to `bot_conversations`.
- **D-16:** "Show chart" inspects last tool call type to pick chart; falls back to daily chart.
- **D-17:** System prompt covers: role, data-only instruction, citation requirement, Meta vs GA4 distinction, prompt-injection defense.
- **D-18:** User messages wrapped: `f"<data>\n{user_text}\n</data>\n\nAnswer the user's question about ad performance."`.
- **D-19:** MIGRATION_004_PHASE4 adds `anthropic_usage_log` table + month index.
- **D-20:** Meta MCP `query_meta_live` tool — deferred to Phase 5.
- **D-21:** New `src/ai/chat.py` — Claude tool-use loop, system prompt, tool dispatch, history, usage logging.
- **D-22:** New `src/ai/tools.py` — 5 tool functions + schemas + allowlists.
- **D-23:** New `src/bot/chat_router.py` — `build_chat_router()` factory.

### Claude's Discretion
None specified — all decisions are locked.

### Deferred Ideas (OUT OF SCOPE)
- Meta MCP `query_meta_live` real-time tool
- `/ask` explicit AI command
- Streaming responses
- Conversation export/search
- Per-user spend caps
- Intent classification for Haiku/Sonnet routing
- Webhook mode for Telegram
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| CHAT-01 | Free-text Q&A for allowlisted users | Catch-all handler pattern; AllowlistMiddleware fires first automatically |
| CHAT-02 | 5 validated tools; no raw SQL to model | Tool schema format, frozenset validation pattern |
| CHAT-03 | Conversation context persisted per session, multi-turn | SQLite JSON serialization, history load pattern |
| CHAT-04 | Responses cite data source and timestamp | System prompt directive + tool output format |
| CHAT-05 | Prompt injection guardrails via `<data>` tags | Established pattern from tldr.py; extend to chat |
| CHAT-06 | Per-request token cap + monthly ceiling with auto-shutdown | `response.usage` fields, cost calculation, budget check SQL |
| CHAT-07 | Inline keyboard buttons after each answer | aiogram `CallbackData`, `InlineKeyboardBuilder`, `callback_query.answer()` |
| CHAT-08 | Answers grounded in data (landing pages, underperformers, recommendations) | Tools cover all data surfaces in schema |
| REC-01 | Evidence-backed recommendations in reports and Q&A | System prompt + tool results provide context; model synthesizes |
| REC-02 | Recommendations reference specific metric values | Tool output format includes raw values; citation directive in system prompt |
| REC-03 | Distinguish Meta-side vs GA4-side signals | System prompt directive separating signal types |
</phase_requirements>

---

## Summary

Phase 4 adds a conversational AI layer on top of the existing SQLite metrics store. The Anthropic Python SDK v0.103.0 (installed) provides `AsyncAnthropic` with async tool-use support. The core pattern is a `while stop_reason == "tool_use"` loop: the assistant emits `tool_use` blocks, the application runs the tool, and the result is sent back as a `tool_result` user message. The loop exits on `"end_turn"`, `"max_tokens"`, `"stop_sequence"`, or `"refusal"`.

aiogram 3.x (v3.28.2 installed) provides `InlineKeyboardBuilder` and `CallbackData` for type-safe button routing. The catch-all text handler uses `F.text & ~F.text.startswith("/")` and must be registered in a separate router that is included AFTER the command router in `setup.py`. Callback queries are handled via `@router.callback_query(MyCallbackData.filter(...))`.

The conversation persistence design (D-08) stores tool_use/tool_result turns as JSON in the existing `bot_conversations.message` TEXT column. The loader attempts `json.loads(message)` and falls back to treating the value as a plain text string — making it backward-compatible with Phase 1-3 status messages already in the table.

**Primary recommendation:** Follow the existing `src/ai/tldr.py` patterns exactly (per-call `AsyncAnthropic` instantiation, `<data>` wrapping, None-on-failure) and extend them with the tool-use loop documented below.

**Pricing correction (CRITICAL):** CONTEXT.md D-19 cost table lists `claude-haiku-4-5` at $0.80/$4.00 per MTok — this was the Haiku 3.5 price. `claude-haiku-4-5` (current) is **$1.00/$5.00** per MTok. Update the hardcoded pricing lookup in `src/ai/tools.py` accordingly.

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Allowlist enforcement | Bot middleware (aiogram) | — | AllowlistMiddleware fires before any handler body |
| Free-text routing | Bot (chat_router.py) | — | Catch-all handler after command router |
| Tool-use loop | AI layer (chat.py) | — | App-side loop; Claude never executes tools |
| Data queries (tools) | AI tools layer (tools.py) | Database (SQLite) | Tools issue parameterized SQL; DB layer executes |
| Conversation history | Database (SQLite) | AI layer (chat.py) | SQLite persists; chat.py loads/saves per request |
| Token cost tracking | Database (SQLite) | AI layer (chat.py) | anthropic_usage_log written after each API call |
| Budget enforcement | AI layer (chat.py) | Bot (alert delivery) | Check before each Claude call; alert via existing engine |
| Inline button routing | Bot (chat_router.py) | AI layer (chat.py) | CallbackQuery handler delegates to AI handler or chart |
| Chart generation | Reports layer (charts.py) | Bot (chat_router.py) | "Show chart" button calls existing matplotlib generator |
| Typing indicator | Bot (chat_router.py) | — | send_chat_action before each Claude call |

---

## Standard Stack

### Core (already installed, no new installs needed)

| Library | Version | Purpose | Source |
|---------|---------|---------|--------|
| anthropic | 0.103.0 | Async Claude API client, tool-use support | [VERIFIED: pip show] |
| aiogram | 3.28.2 | Telegram bot framework, InlineKeyboard, CallbackQuery | [VERIFIED: pip show] |
| aiosqlite | current | Async SQLite — conversation + usage persistence | [VERIFIED: existing codebase] |
| structlog | current | Structured logging — follow existing patterns | [VERIFIED: existing codebase] |

### No new packages required

All Phase 4 dependencies are already installed. The only changes are new Python modules.

**Installation:** None required.

---

## Architecture Patterns

### System Architecture Diagram

```
Telegram User
      |
      | text message (non-command)
      v
AllowlistMiddleware ──(rejected)──> silently drop
      |
      | (allowed)
      v
chat_router.py: MessageHandler(F.text & ~F.text.startswith("/"))
      |
      | budget check
      v
DBClient.get_monthly_anthropic_cost()
      |─── over budget ──> user message + operator alert (existing alert engine)
      |
      | (within budget)
      v
bot.send_chat_action("typing")
      |
      v
DBClient.get_conversation_history(chat_id, user_id, limit=10)
      |
      v
chat.py: handle_chat_message()
      |
      | build messages list:
      | [system_prompt] + history + [new user message (data-wrapped)]
      v
AsyncAnthropic.messages.create(model, tools, messages, max_tokens=2048)
      |
      |── stop_reason == "tool_use" ──────────────────────────────────┐
      |                                                                 |
      |                             tools.py: dispatch_tool(name, input)
      |                             (validate frozenset, run SQL, return str)
      |                                                                 |
      |                             append assistant content to messages|
      |                             append tool_result user message     |
      |                             repeat send_chat_action("typing")   |
      |<───────────────────────────────────────────────────────────────┘
      |
      |── stop_reason == "end_turn" ──> extract final text
      |── stop_reason == "max_tokens" ──> truncation warning + best-effort final text
      |── stop_reason == "refusal" ──> safe refusal message to user
      |── max_iterations hit ──> error message + log
      |
      v
DBClient.log_anthropic_usage(model, input_tokens, output_tokens, cost_usd, chat_id, user_id)
DBClient.save_conversation_turn(user turn)
DBClient.save_conversation_turn(assistant final turn)
      |
      v
Send Telegram reply (HTML-escaped, auto-split if > 4096 chars)
+ InlineKeyboardMarkup (4 buttons)
      |
      v
      [Inline button tap]
      |
      v
chat_router.py: CallbackQuery handler
      |─── "show_chart" ──> charts.py (no Claude call)
      └─── other buttons ──> inject synthetic user message ──> back to top
```

### Recommended Project Structure

```
src/
├── ai/
│   ├── tldr.py          # existing (unchanged)
│   ├── chat.py          # NEW: tool-use loop, system prompt, history, usage log
│   └── tools.py         # NEW: 5 tool functions, schemas, frozenset allowlists
├── bot/
│   ├── handlers.py      # existing: add /clear command
│   ├── chat_router.py   # NEW: build_chat_router(), catch-all + callback handler
│   ├── middleware.py     # existing (unchanged)
│   └── setup.py         # modified: include build_chat_router() AFTER build_router()
└── db/
    ├── client.py         # modified: 5 new methods (log_usage, get_cost, get_history, save_turn, clear_conv)
    └── schema.py         # modified: add MIGRATION_004_PHASE4
```

---

## Implementation Patterns

### Anthropic Tool-Use Loop

The agentic loop is keyed on `stop_reason`. The SDK returns a `Message` object; `response.content` is a list of content blocks. The assistant turn must be appended to the messages list as `{"role": "assistant", "content": response.content}` — passing the SDK list directly works because aiosdk auto-serializes it.

**Stop reason values** [VERIFIED: platform.claude.com/docs]:
- `"tool_use"` — Claude wants to call one or more tools; continue the loop
- `"end_turn"` — Claude produced a final answer; exit the loop
- `"max_tokens"` — Response hit the `max_tokens` cap; handle gracefully (partial answer)
- `"stop_sequence"` — A stop sequence was hit; treat like end_turn
- `"refusal"` — Content policy refusal; surface a safe message to user

**Critical formatting rule** [VERIFIED: platform.claude.com/docs/handle-tool-calls]:
In the `tool_result` user message, `tool_result` blocks MUST come FIRST in the content array. Any text must come AFTER. Putting text before tool_result causes a 400 error.

**Multiple tool_use blocks:** A single response can contain multiple `tool_use` blocks. The loop must collect ALL tool calls from the response and submit ALL results in a single user message before the next API call.

```python
# Source: platform.claude.com/docs/en/agents-and-tools/tool-use/build-a-tool-using-agent
# Adapted to async + multi-tool + max_iter guard

_MAX_TOOL_ITERATIONS = 10  # prevent infinite loops

async def run_tool_loop(
    client: AsyncAnthropic,
    messages: list[dict],
    tools: list[dict],
    max_tokens: int,
    model: str,
) -> tuple[str, anthropic.types.Usage]:
    """Run the agentic tool-use loop. Returns (final_text, total_usage)."""
    total_input = 0
    total_output = 0

    for _iteration in range(_MAX_TOOL_ITERATIONS):
        response = await client.messages.create(
            model=model,
            max_tokens=max_tokens,
            tools=tools,
            tool_choice={"type": "auto"},
            messages=messages,
        )
        total_input += response.usage.input_tokens
        total_output += response.usage.output_tokens

        if response.stop_reason == "tool_use":
            # Collect ALL tool_use blocks from this response
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result = await dispatch_tool(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })

            # Append assistant turn (full content list, not just text blocks)
            messages.append({"role": "assistant", "content": response.content})
            # tool_result blocks MUST come first in the content array
            messages.append({"role": "user", "content": tool_results})

        else:
            # end_turn, max_tokens, stop_sequence, refusal
            final_text = next(
                (b.text for b in response.content if b.type == "text"),
                "[No text response]",
            )
            # Synthesize a Usage-like total (sum over all iterations)
            return final_text, response.usage  # caller sums if needed

    # Max iterations hit — return best-effort
    return "[Response truncated: too many tool calls]", response.usage
```

**Usage tracking:** `response.usage.input_tokens` and `response.usage.output_tokens` are available on every `Message` response. Accumulate across all iterations within one user turn to get the full cost of that interaction. [VERIFIED: Anthropic docs pricing page]

**Tool use adds overhead tokens:** With `tool_choice="auto"`, Anthropic adds a 346-token system prompt overhead automatically (for Sonnet 4.6 and Haiku 4.5). This is billed as input tokens. Factor into cost estimation. [VERIFIED: platform.claude.com/docs pricing]

### Message History Persistence

The `bot_conversations` table already exists (MIGRATION_001). Phase 4 adds read/write access with JSON serialization for multi-content turns.

**Serialization contract** (D-08):

| Turn type | `role` value | `message` stored as |
|-----------|-------------|---------------------|
| User question (text) | `'user'` | Plain string |
| Assistant final text | `'assistant'` | Plain string |
| Tool_use turn (assistant) | `'assistant'` | `json.dumps([{"type": "tool_use", "id": ..., "name": ..., "input": ...}, ...])` |
| Tool_result turn | `'tool'` | `json.dumps([{"type": "tool_result", "tool_use_id": ..., "content": ...}])` |

**Loading history back:** Attempt `json.loads(message)` for each row; if it fails or returns a string, treat the raw `message` value as a plain text content block. This handles Phase 1-3 rows and plain-text assistant turns with the same code path:

```python
def _deserialize_message(role: str, raw_message: str) -> dict:
    """Convert a stored message row into an Anthropic messages-API dict."""
    try:
        content = json.loads(raw_message)
        if isinstance(content, str):
            # json.loads of a quoted string returns a string — treat as text
            content = raw_message
    except (json.JSONDecodeError, ValueError):
        content = raw_message  # plain text — pass directly

    # Normalize role: 'tool' -> 'user' for the Anthropic API
    api_role = "user" if role == "tool" else role
    return {"role": api_role, "content": content}
```

**Note on `role='tool'`:** The `bot_conversations` CHECK constraint allows `'tool'` (added in schema MIGRATION_001). The Anthropic API does NOT have a `"tool"` role — tool results are sent as `"user"` messages with `tool_result` content blocks. When reconstructing the messages list, rows with `role='tool'` must be mapped to `{"role": "user", "content": <parsed_list>}`. [VERIFIED: platform.claude.com/docs/handle-tool-calls]

**History window:** Fetch last 10 turns ordered `DESC`, then reverse for chronological order:

```sql
SELECT role, message, created_at
FROM bot_conversations
WHERE chat_id = :chat_id AND user_id = :user_id
ORDER BY created_at DESC
LIMIT 10
```

Then `rows.reverse()` before constructing the messages list.

**Consecutive role constraint:** The Anthropic API requires alternating `user`/`assistant` roles — you cannot have two consecutive `user` or `assistant` turns. Tool_use turns always come as `assistant`, and tool_result turns as `user`, so storing them separately in `bot_conversations` and loading them in order naturally satisfies this. The 10-turn limit should be implemented as "10 conversation turns" meaning 10 user messages (with their corresponding assistant responses and any intermediate tool turns).

### Token Budget Enforcement

**Verified pricing** [VERIFIED: platform.claude.com/docs/about-claude/pricing — May 2026]:

| Model | Input per MTok | Output per MTok |
|-------|---------------|-----------------|
| `claude-sonnet-4-6` | $3.00 | $15.00 |
| `claude-haiku-4-5` | $1.00 | $5.00 |

**CORRECTION vs CONTEXT.md D-19:** The context file listed Haiku at $0.80/$4.00 — that was Haiku 3.5 (retired). Haiku 4.5 is $1.00/$5.00. Update the cost lookup table in `src/ai/tools.py`.

**Cost calculation** (after each API call):

```python
_PRICING: dict[str, tuple[float, float]] = {
    "claude-sonnet-4-6": (3.00, 15.00),   # (input_per_mtok, output_per_mtok)
    "claude-haiku-4-5": (1.00, 5.00),     # [VERIFIED: anthropic pricing page 2026-05-19]
}

def calculate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    in_price, out_price = _PRICING.get(model, (3.00, 15.00))
    return (input_tokens * in_price + output_tokens * out_price) / 1_000_000
```

**Budget check SQL** (before each Claude call):

```sql
SELECT COALESCE(SUM(cost_usd), 0.0) AS monthly_total
FROM anthropic_usage_log
WHERE request_at >= datetime('now', 'start of month')
```

**Scheduled TL;DR calls also count:** The `generate_tldr()` function in `src/ai/tldr.py` must also call `log_anthropic_usage()` after each successful API call. This keeps the monthly ceiling accurate. Add a `db` parameter or make it a separate DBClient call in the caller (daily_report_module).

### aiogram Inline Keyboard + Callback

**Pattern** [VERIFIED: docs.aiogram.dev/en/latest/dispatcher/filters/callback_data.html]:

```python
from aiogram.filters.callback_data import CallbackData
from aiogram.utils.keyboard import InlineKeyboardBuilder

class ChatAction(CallbackData, prefix="chat"):
    action: str  # "drill_down" | "compare_week" | "why" | "show_chart"

def build_followup_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Drill down", callback_data=ChatAction(action="drill_down"))
    builder.button(text="Compare to last week", callback_data=ChatAction(action="compare_week"))
    builder.button(text="Why is this happening?", callback_data=ChatAction(action="why"))
    builder.button(text="Show chart", callback_data=ChatAction(action="show_chart"))
    builder.adjust(2)  # 2 buttons per row
    return builder.as_markup()

@router.callback_query(ChatAction.filter())
async def handle_chat_action(
    callback: CallbackQuery,
    callback_data: ChatAction,
    db: DBClient,
) -> None:
    # MUST call answer() to dismiss the loading spinner — required for all callback handlers
    await callback.answer()  # empty = silent dismiss; provide text for toast notification

    if callback_data.action == "show_chart":
        # Direct chart generation — no Claude call
        ...
    else:
        # Synthesize as user message and route through AI handler
        injected_text = _ACTION_TO_TEXT[callback_data.action]
        await handle_chat_message(callback.message, injected_text, db)
```

**`callback_query.answer()` is mandatory.** If you do not call it within ~30 seconds, Telegram shows an indefinite loading spinner on the button and eventually the user sees a "Bot is not responding" error. Call it as the FIRST await in any callback handler, before any slow operations. [CITED: Telegram Bot API contract; VERIFIED: aiogram docs pattern]

**`callback_data` size limit:** Telegram limits callback_data strings to 64 bytes. The `CallbackData` packing format is compact — a single `action: str` with a short value is well within the limit. [ASSUMED — standard Telegram constraint]

### Catch-All Text Handler

**Registration order is critical** [VERIFIED: aiogram docs + D-10]:

In `src/bot/setup.py`, the chat router must be included AFTER the command router. aiogram evaluates handlers in registration order across all included routers. The command router's handlers (which use `Command()` filters) are more specific and will match `/commands` before the catch-all sees them.

```python
# In setup.py:
dp.include_router(build_router())       # command router FIRST
dp.include_router(build_chat_router())  # catch-all SECOND
```

**Catch-all filter pattern:**

```python
from aiogram import F
from aiogram.types import Message

@router.message(F.text & ~F.text.startswith("/"))
async def handle_text_message(message: Message, db: DBClient) -> None:
    ...
```

The `~` (bitwise NOT / inversion) operator on aiogram magic filters negates the condition. `F.text` ensures the message has text content (not a photo, sticker, etc.). Together: "has text AND does not start with /".

**Important:** `F.text` is falsy for messages without text (photos, audio, etc.). The `&` ensures only text messages reach the handler. Non-text messages fall through to aiogram's default unhandled-update behavior (silently ignored). [VERIFIED: aiogram docs magic filters]

### Typing Indicator Pattern

The Telegram `typing` chat action expires after approximately 5 seconds. For long Claude calls (especially multi-turn tool-use loops with 3+ tool calls), the indicator must be re-sent periodically. [CITED: Telegram Bot API docs — sendChatAction expires in "about 5 seconds"]

**Pattern for long operations:**

```python
import asyncio

async def send_with_typing(bot: Bot, chat_id: int, coro):
    """Run coro while keeping typing indicator alive."""
    async def keep_typing():
        while True:
            await bot.send_chat_action(chat_id=chat_id, action="typing")
            await asyncio.sleep(4)  # re-send before 5-second expiry

    typing_task = asyncio.create_task(keep_typing())
    try:
        result = await coro
    finally:
        typing_task.cancel()
        try:
            await typing_task
        except asyncio.CancelledError:
            pass
    return result
```

For Phase 4's typical case (1-3 tool calls, ~5-15 seconds total), a simpler approach is acceptable: send `typing` before each Claude API call within the loop. This re-arms the indicator for each round trip without a background task:

```python
# Before each messages.create() call in the loop:
await bot.send_chat_action(chat_id=chat_id, action="typing")
response = await client.messages.create(...)
```

The simpler pattern is sufficient because each API round trip takes 2-8 seconds, and re-sending within the loop naturally keeps the indicator fresh.

### Tool Input Validation

**The core rule** (CLAUDE.md + D-13): No dynamic SQL column names or table names without a `frozenset` allowlist. Tool parameters that become part of SQL must be validated before use.

```python
_ALLOWED_METRICS: frozenset[str] = frozenset({
    "spend", "impressions", "clicks", "ctr", "cpc", "cpm", "roas",
    "meta_purchases_7dclick", "meta_cost_per_purchase", "reach", "frequency",
    "sessions", "users", "new_users", "bounce_rate", "avg_engagement_time",
    "ga4_purchases_lastclick",
})

_ALLOWED_SOURCES: frozenset[str] = frozenset({"meta", "ga4", "both"})

_ALLOWED_SORT_COLS: frozenset[str] = frozenset({"conversions", "sessions"})

def _validate_metric(metric: str) -> str | None:
    """Return metric if valid, None if invalid."""
    return metric if metric in _ALLOWED_METRICS else None
```

**Invalid input returns an error string (not raise):** When a tool receives an invalid `metric` or `source`, return an error string like `"Error: metric 'foo' is not a recognised metric. Valid metrics: spend, roas, ..."`. Claude will see this as a `tool_result` and self-correct on the next iteration, typically 2-3 retries before apologizing. [VERIFIED: platform.claude.com/docs/handle-tool-calls "Invalid tool name" section]

**Strict tool use option:** The Anthropic SDK supports `"strict": true` on individual tool definitions to guarantee schema conformance. This eliminates invalid tool calls at the API level (returns 400 for schema violations before they reach your code). Recommended for production but not required — frozenset validation handles it defensively. [VERIFIED: platform.claude.com/docs/en/agents-and-tools/tool-use/strict-tool-use mentioned]

### Message Threading (Telegram Reply)

To visually thread the bot's answer to the user's question (especially in group chats):

```python
await message.reply(text, reply_markup=keyboard)
# or equivalently:
await bot.send_message(
    chat_id=message.chat.id,
    text=text,
    reply_to_message_id=message.message_id,
    reply_markup=keyboard,
)
```

`message.reply()` automatically sets `reply_to_message_id`. This is the simplest approach. [VERIFIED: aiogram patterns in existing codebase handlers.py]

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Inline button data packing | Custom string serialization | aiogram `CallbackData` with typed fields | Type safety; 64-byte limit handled; auto-unpack in handler |
| Tool schema validation | Custom JSON schema validator | Anthropic's `input_schema` + `frozenset` for SQL-dangerous params | SDK validates types; frozenset guards SQL injection |
| Message splitting | Custom 4096-char splitter | Existing `src/reports/splitter.py` | Already tested, handles HTML tags correctly |
| Chart for "Show chart" button | New chart code | Existing `src/reports/charts.py` | All chart types already implemented |
| Operator budget alert | New Telegram send | Existing `src/alerts/engine.py _send_alert()` / `bot.send_message()` pattern | Alert delivery already handles errors |
| Typing indicator loop | Custom asyncio task | Simple re-send before each API call (see pattern above) | Sufficient for 1-3 tool call depth; no extra complexity |

**Key insight:** Phases 1-3 built most of the infrastructure Phase 4 needs. The core new work is the tool-use loop in `chat.py` and the tool functions in `tools.py`.

---

## Common Pitfalls

### Pitfall 1: Appending Only Text Blocks as the Assistant Turn

**What goes wrong:** `messages.append({"role": "assistant", "content": response.text})` — using only the text portion instead of the full content list.

**Why it happens:** `response.content[0].text` is the natural way to get "the answer", but during tool-use, `response.content` is a list that includes `tool_use` blocks. The API requires the complete content list to match the `tool_use` block IDs when you send back `tool_result` blocks.

**How to avoid:** Always append `{"role": "assistant", "content": response.content}` — the full SDK list, not just text. [VERIFIED: official Python example code]

**Warning signs:** `400: tool_use ids were found without tool_result blocks immediately after`

### Pitfall 2: Text Before tool_result in User Message

**What goes wrong:** Building the tool_result user message as `[{"type": "text", ...}, {"type": "tool_result", ...}]`.

**How to avoid:** `tool_result` blocks must be FIRST in the content array. Any additional text (e.g., "Here are the results:") must come AFTER the tool_result blocks. Simplest: use only tool_result blocks, no text. [VERIFIED: platform.claude.com/docs/handle-tool-calls — explicit warning]

**Warning signs:** `400 Bad Request` with message about tool_result ordering.

### Pitfall 3: Forgetting to call callback_query.answer()

**What goes wrong:** Callback handler processes the request but never calls `await callback.answer()`. The Telegram client shows an indefinite spinning indicator on the button.

**How to avoid:** `await callback.answer()` as the first line of every callback handler, before any await that could fail. [CITED: Telegram Bot API contract]

**Warning signs:** User reports button "never stops loading"; no error in bot logs.

### Pitfall 4: chat_router Registered Before command Router

**What goes wrong:** If `build_chat_router()` is included before `build_router()` in `setup.py`, the catch-all `F.text` handler intercepts `/commands` before the command handlers see them.

**How to avoid:** Always: `dp.include_router(build_router())` then `dp.include_router(build_chat_router())`. [VERIFIED: aiogram handler ordering docs]

**Warning signs:** `/start`, `/status`, `/help`, `/report` stop responding; they all go to the AI handler.

### Pitfall 5: Haiku 4.5 Pricing Error in Cost Calculation

**What goes wrong:** Using $0.80/$4.00 per MTok for `claude-haiku-4-5` (the Haiku 3.5 price). Monthly budget will be underestimated by ~20%.

**How to avoid:** Haiku 4.5 is $1.00/$5.00 per MTok. [VERIFIED: platform.claude.com/docs/about-claude/pricing 2026-05-19]

**Warning signs:** Monthly cost tracking shows lower spend than actual Anthropic invoice.

### Pitfall 6: Infinite Tool-Use Loop

**What goes wrong:** No max iteration guard — if Claude keeps calling tools (perhaps due to error strings returning without satisfying the model), the loop runs indefinitely, racking up token costs.

**How to avoid:** Set `_MAX_TOOL_ITERATIONS = 10` (or lower). After exhausting iterations, break and return an error message. Log the event for operator review. [ASSUMED — standard agentic safety practice]

**Warning signs:** Single user message triggers 10+ API calls; monthly budget spikes unexpectedly.

### Pitfall 7: f-string SQL with Dynamic Column Names

**What goes wrong:** `f"SELECT AVG({metric}) FROM ad_metrics"` where `metric` comes from Claude's tool input — potential SQL injection.

**How to avoid:** Validate `metric` against `_ALLOWED_METRICS` frozenset FIRST. Only then construct the query using the validated string. Named parameters (`:param`) handle values; the column name is safe only after frozenset validation. [VERIFIED: established pattern in DBClient.get_row_counts()]

**Warning signs:** Tool accepts any `metric` string without rejection.

### Pitfall 8: TL;DR Calls Not Logged Against Monthly Budget

**What goes wrong:** `generate_tldr()` makes API calls but doesn't write to `anthropic_usage_log`. The budget check query (`SUM(cost_usd) FROM anthropic_usage_log`) underestimates total spend.

**How to avoid:** D-04 states scheduled TL;DR calls also count. The daily report job must call `db.log_anthropic_usage(...)` after each `generate_tldr()` call. Pass `db` to the daily report job and add the usage logging call. [ASSUMED — inferred from D-04 requirement; verify against daily_report.py]

**Warning signs:** actual Anthropic bill higher than `anthropic_usage_log` total.

### Pitfall 9: Consecutive Role Violation When Loading History

**What goes wrong:** Loading all 10 rows from `bot_conversations` and mapping them to messages naively — if a user sends a message immediately after another user message (edge case), or if tool turns are not correctly paired, the messages array may have consecutive `user` or consecutive `assistant` entries, causing a 400 error.

**How to avoid:** Validate that the loaded history alternates roles before submitting. The simplest guard: if the last message in the loaded history has the same role as the new message you're about to append, log a warning and truncate the history. [ASSUMED]

---

## Code Examples

### Tool Schema Format (Verified)

```python
# Source: platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools
TOOLS = [
    {
        "name": "query_metrics",
        "description": (
            "Query aggregated Meta Ads or GA4 metrics for a date range. "
            "Use this to get spend, ROAS, conversions, sessions, etc. "
            "Always cite the source and date range in your response."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "enum": ["meta", "ga4", "both"],
                    "description": "Data source to query",
                },
                "start_date": {
                    "type": "string",
                    "description": "ISO 8601 date YYYY-MM-DD",
                },
                "end_date": {
                    "type": "string",
                    "description": "ISO 8601 date YYYY-MM-DD",
                },
                "campaign_name": {
                    "type": "string",
                    "description": "Optional filter to a specific campaign name",
                },
            },
            "required": ["source", "start_date", "end_date"],
        },
    },
]
```

### Monthly Budget Check Pattern

```python
# Source: D-03, D-04 from 04-CONTEXT.md
async def check_budget(db: DBClient, budget_usd: float) -> tuple[bool, float]:
    """Returns (is_over_budget, current_month_spend)."""
    row = await db.fetch_one(
        "SELECT COALESCE(SUM(cost_usd), 0.0) AS total "
        "FROM anthropic_usage_log "
        "WHERE request_at >= datetime('now', 'start of month')"
    )
    spent = float(row["total"]) if row else 0.0
    return spent >= budget_usd, spent
```

### /clear Command Addition to handlers.py

```python
# Add to build_router() in handlers.py
@router.message(Command("clear"))
async def cmd_clear(message: Message, db: DBClient) -> None:
    user_id = message.from_user.id if message.from_user else None
    if user_id is None:
        await message.answer("Cannot identify user.")
        return
    await db.execute(
        "DELETE FROM bot_conversations WHERE chat_id = :chat_id AND user_id = :user_id",
        {"chat_id": message.chat.id, "user_id": user_id},
    )
    logger.info("conversation_cleared", chat_id=message.chat.id, user_id=user_id)
    await message.answer("Conversation cleared.")
```

### MIGRATION_004 SQL (from CONTEXT.md D-19)

```sql
-- Verbatim from 04-CONTEXT.md D-19
CREATE TABLE IF NOT EXISTS anthropic_usage_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    request_at   TEXT NOT NULL DEFAULT (datetime('now')),
    model        TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd     REAL NOT NULL DEFAULT 0.0,
    chat_id      INTEGER,
    user_id      INTEGER
);
CREATE INDEX IF NOT EXISTS idx_usage_log_month ON anthropic_usage_log(request_at);
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Haiku 3.5 ($0.80/$4.00) | Haiku 4.5 ($1.00/$5.00) — Haiku 3.5 retired | Circa 2025 | Cost estimate in CONTEXT.md D-19 is wrong; update before implementation |
| Manual tool-use loop | SDK `Tool Runner` abstraction available | 2025 | Tool Runner simplifies the loop but removes control; stick with manual loop for custom history/cost tracking |

**Deprecated/outdated:**
- `claude-haiku-3-5`: Retired except on Bedrock and Vertex AI. Do not use.

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Typing indicator expires in ~5 seconds (Telegram limit) | Typing Indicator Pattern | If shorter, users see gaps; if longer, simpler pattern is still safe |
| A2 | `_MAX_TOOL_ITERATIONS = 10` is sufficient guard against infinite loops | Tool-Use Loop | If too low, complex multi-step queries get truncated; if too high, runaway costs |
| A3 | `callback_data` 64-byte Telegram limit — `ChatAction(action="compare_week")` fits | Inline Keyboard | If tight, shorten action strings (e.g., "cw" instead of "compare_week") |
| A4 | TL;DR calls (generate_tldr) need `db` plumbed in to log usage | Common Pitfalls #8 | If not done, monthly budget check is inaccurate; actual spend higher than tracked |
| A5 | Consecutive role guard needed for history loading | Message History Persistence | If Anthropic is lenient, guard is unnecessary overhead; if strict (likely), missing guard causes 400s |

**If this table is empty:** It is not empty — see above.

---

## Open Questions (RESOLVED)

1. **Does `generate_tldr()` need a `db` parameter for usage logging?**
   - What we know: D-04 says TL;DR calls count against the monthly ceiling.
   - What's unclear: `tldr.py` currently has no `db` dependency. Adding it changes the function signature and requires the daily report job to pass `db`.
   - Recommendation: Add `db: DBClient | None = None` optional parameter to `generate_tldr()`. Log usage when `db` is not None. This is backward-compatible.

   > **RESOLVED:** Plan 04-03 Task 2 adds `db: DBClient | None = None` to `generate_tldr()` — backward-compatible; daily report job passes `db`.

2. **How should "10 turns" be counted for the history window?**
   - What we know: D-07 says "last 10 turns (rows by created_at DESC LIMIT 10)".
   - What's unclear: One user question + one assistant answer + N tool turns = how many "rows"? If a single user question triggers 3 tool rounds, that's 5 rows (user, assistant+tool_use, user+tool_result × 3). 10 rows might only cover 2 complete Q&A pairs.
   - Recommendation: Count "10 user-message rows" — use `WHERE role = 'user' ... LIMIT 10` as the anchor, then fetch all rows between the 10th-oldest user message and now. This better reflects "10 conversation turns" semantically.

   > **RESOLVED:** D-07 followed literally — `LIMIT 10` rows (mix of user/assistant/tool turns) ordered `DESC` then reversed; implementation in plan 04-01 Task 2 (`get_conversation_history`).

3. **Where does the operator alert go when budget is exhausted?**
   - What we know: D-04 says "send to the Telegram report channel via existing alert delivery path." The CONTEXT.md specifics section mentions `telegram_report_channel_id` or `telegram_allowed_chat_ids[0]`.
   - What's unclear: Phase 4 does not add `telegram_report_channel_id` to Settings. Does it use `allowed_chat_ids[0]`?
   - Recommendation: Use `settings.telegram_allowed_chat_ids[0]` as the fallback. Log a warning if the list is empty. Add a `ANTHROPIC_BUDGET_ALERT_CHAT_ID` env var if the operator needs a separate alert destination.

   > **RESOLVED:** Plan 04-03 Task 1 uses `settings.telegram_allowed_chat_ids[0]`; logs a warning if the list is empty. No additional env var added (deferred per D-20 philosophy).

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| anthropic SDK | Claude API calls | Yes | 0.103.0 | — |
| aiogram | Telegram bot | Yes | 3.28.2 | — |
| aiosqlite | Conversation persistence | Yes | current | — |
| AsyncAnthropic | async tool-use | Yes | (part of anthropic 0.103.0) | — |

All dependencies are available. No new installs required.

---

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | No (Telegram handles identity; allowlist is authorization) | AllowlistMiddleware (already implemented) |
| V3 Session Management | Partial | Conversation history scoped to `(chat_id, user_id)`; `/clear` command for session reset |
| V4 Access Control | Yes | AllowlistMiddleware fires before any handler; chat handlers benefit automatically |
| V5 Input Validation | Yes | User text in `<data>` tags; tool inputs validated against frozenset; no raw SQL to Claude |
| V6 Cryptography | No | No secrets handled in Phase 4 code; API key from env var (existing pattern) |

### Known Threat Patterns for This Stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Prompt injection via campaign names | Tampering | `<data>...</data>` tag wrapping (D-18); system prompt instruction to treat as data only (D-17) |
| SQL injection via Claude tool inputs | Tampering | frozenset allowlist for metric/source params; named SQL parameters (`:param`) for all values |
| Budget exhaustion via query flooding | Denial of Service | Monthly ceiling + per-request max_tokens cap; graceful shutdown on ceiling hit |
| Runaway agentic loop | Denial of Service | `_MAX_TOOL_ITERATIONS = 10` hard cap |
| Non-allowlisted user accessing AI | Elevation of Privilege | AllowlistMiddleware on `dp.callback_query.middleware` AND `dp.message.middleware` — buttons also checked |

---

## Sources

### Primary (HIGH confidence)
- `platform.claude.com/docs/en/agents-and-tools/tool-use/how-tool-use-works` — stop_reason values, agentic loop pattern
- `platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls` — tool_result format, ordering rules, is_error
- `platform.claude.com/docs/en/agents-and-tools/tool-use/build-a-tool-using-agent` — verified Python code for agentic loop (Ring 1 and Ring 2)
- `platform.claude.com/docs/en/about-claude/models/overview` — model IDs, pricing, context windows
- `platform.claude.com/docs/en/about-claude/pricing` — verified per-MTok pricing for Sonnet 4.6 and Haiku 4.5
- `docs.aiogram.dev/en/latest/dispatcher/filters/callback_data.html` — CallbackData, InlineKeyboardBuilder pattern
- `docs.aiogram.dev/en/latest/dispatcher/filters/magic_filters.html` — F.text, catch-all pattern, handler ordering
- `pip show anthropic` — version 0.103.0 installed [VERIFIED]
- `pip show aiogram` — version 3.28.2 installed [VERIFIED]
- Existing codebase: `src/ai/tldr.py`, `src/bot/handlers.py`, `src/bot/setup.py`, `src/bot/middleware.py`, `src/db/client.py`, `src/db/schema.py`, `src/config.py` — all read and analyzed [VERIFIED]

### Secondary (MEDIUM confidence)
- CONTEXT.md `04-CONTEXT.md` — all decisions locked by user (D-01 through D-23)

### Tertiary (LOW confidence — see Assumptions Log)
- Typing indicator ~5 second expiry — widely cited in Telegram developer community; not directly verified from official docs in this session

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all packages verified via pip; no new installs
- Anthropic tool-use loop: HIGH — verified from official Python code examples and docs
- Pricing: HIGH — verified from official pricing page (with correction vs CONTEXT.md)
- aiogram patterns: HIGH — verified from aiogram docs
- Pitfalls: HIGH (most) / LOW (A2, A5 — loop depth and role guard)
- History persistence: MEDIUM-HIGH — design from CONTEXT.md D-08, JSON round-trip is standard; consecutive role edge case is assumed

**Research date:** 2026-05-19
**Valid until:** 2026-06-19 (30 days — stack is stable)
