# Architecture Research: Ads Reporting Agent

**Project:** AI-powered conversational agent for Meta Ads + GA4 data with Telegram delivery
**Researched:** 2026-05-19
**Confidence:** HIGH (Telegram patterns, scheduler, Claude tool use) / MEDIUM (data modeling specifics)

---

## Executive Summary

This system is an **AI-augmented ELT pipeline with a chat surface**, not a traditional analytics product. The architectural center of gravity is a **canonical metrics store** that decouples three independent concerns: (1) periodic ingestion from Meta + GA4, (2) scheduled summary generation pushed to Telegram, and (3) on-demand natural-language Q&A backed by Claude tool use.

The recommended pattern is a **single-process modular monolith** built around an **async Python core** (FastAPI + asyncio), with APScheduler for in-process scheduling, SQLite (or Postgres if multi-user) as the canonical store, the `python-telegram-bot` library for the chat surface (webhook mode in production, polling in dev), and the Anthropic SDK with **tool use** as the AI integration pattern. Tool use is strongly preferred over RAG/embeddings or system-prompt data injection because marketing data is **structured, numeric, queryable, and time-bounded** — exactly the workload tool calling was designed for.

Avoid the trap of building a generic ETL framework. This is a focused 2-source pipeline; treat ingestion as boring code, invest complexity budget in the **canonical metrics schema** and the **Claude tool surface**.

---

## System Components

### Component Map

```
                            +-------------------------+
                            |  Telegram (User Chat)   |
                            +-----+-------------------+
                                  |  webhook POST / long-poll
                                  v
+---------------------+    +--------------------+    +---------------------+
|  APScheduler        |    |  FastAPI App       |    |  Anthropic Claude   |
|  (cron triggers)    +--->+  (entrypoint)      +--->+  Messages API       |
+---------+-----------+    +---------+----------+    +----------+----------+
          |                          |                          |
          |  scheduled ingest        |  /webhook, /healthz      |  tool_use blocks
          v                          v                          v
+---------------------+    +--------------------+    +---------------------+
|  Ingestion Workers  |    |  Bot Handlers      |    |  Tool Executor      |
|  - meta_ads.py      |    |  - report cmd      |    |  - query_metrics    |
|  - ga4.py           |    |  - chat handler    |    |  - get_campaign     |
+---------+-----------+    +---------+----------+    +----------+----------+
          |                          |                          |
          v                          v                          v
+---------------------------------------------------------------+
|                  Canonical Metrics Store                      |
|     (SQLite/Postgres: campaigns, ad_metrics, ga_metrics,      |
|      landing_pages, conversations, report_runs)               |
+---------------------------------------------------------------+
          ^                          ^
          |                          |
+---------+-----------+    +---------+----------+
| Report Generator    |    | Config Layer       |
| - templates         |    | - .env / secrets   |
| - Claude synthesis  |    | - credential vault |
+---------------------+    +--------------------+
```

### Component Responsibilities

| Component | Responsibility | Owns | Talks To |
|-----------|---------------|------|----------|
| **FastAPI app** | HTTP entrypoint, hosts Telegram webhook + healthcheck | Request routing, app lifecycle | Telegram (inbound webhook), Bot Handlers |
| **APScheduler** | In-process cron triggers for ingestion + report generation | Schedule registry, job persistence | Ingestion Workers, Report Generator |
| **Ingestion Workers** | Pull from external APIs, normalize to canonical schema, upsert | API clients (Meta Marketing API, GA4 Data API), retry/backoff, rate-limit handling | External APIs, Metrics Store |
| **Canonical Metrics Store** | Single source of truth for normalized metrics + conversation state | Schema, indexes, query interface | Everyone reads; only Ingestion writes metrics |
| **Bot Handlers** | Parse Telegram updates, route to report send vs chat | Command dispatch, response formatting (Markdown V2) | Metrics Store (reads), Tool Executor, Telegram (outbound) |
| **Tool Executor** | Executes Claude-invoked tools against the Metrics Store | Tool schema definitions, SQL/ORM execution, result serialization | Metrics Store, Claude API |
| **Report Generator** | Builds scheduled summary reports, sends to Telegram group | Report templates, Claude synthesis call, formatting | Claude API, Metrics Store, Telegram |
| **Config Layer** | Loads credentials and runtime config from env/secrets | Credential isolation, validation on boot | Everyone (read-only) |

### Boundary Rules

1. **Only Ingestion Workers write metric tables.** Bot Handlers and Tool Executor are read-only against metric data.
2. **Tool Executor is the only path between Claude and the database.** No system-prompt data injection beyond schema descriptions.
3. **Config Layer is loaded once at boot.** No runtime credential rotation in v1.
4. **Scheduler does not call Claude directly.** It enqueues jobs; the Report Generator owns Claude calls so retry semantics live in one place.

---

## Data Flow

### Flow 1: Scheduled Ingestion (every N hours)

```
APScheduler trigger fires
    -> Ingestion Worker.run(source='meta_ads', date_range=...)
        -> Meta Marketing API: GET /act_{id}/insights (paginated)
        -> Transform: API row -> CanonicalAdMetric (see schema below)
        -> UPSERT into ad_metrics (campaign_id, date, ...)
        -> Write run record to ingestion_log (status, rows, errors)
    -> Ingestion Worker.run(source='ga4', date_range=...)
        -> GA4 Data API: runReport(dimensions=[landingPage, date], metrics=[...])
        -> Transform: API row -> CanonicalGaMetric
        -> UPSERT into ga_metrics
```

**Key properties:**
- **Idempotent** — every ingest is an UPSERT keyed on `(source, entity_id, date)` so re-runs are safe
- **Incremental** — pull only `[last_successful_date - 2 days, today]` to catch late-arriving conversions
- **Decoupled** — Meta and GA4 jobs run independently; one failing does not block the other

### Flow 2: Scheduled Report (e.g., daily 9am)

```
APScheduler trigger fires
    -> ReportGenerator.run(report_type='daily_summary')
        -> Query Metrics Store: aggregate last 24h, last 7d, vs prior period
        -> Build structured context blob (top campaigns, anomalies, totals)
        -> Call Claude (single completion, no tool use needed) with:
            - System prompt: "You write marketing summaries for Telegram"
            - User message: structured data + "summarize"
        -> Format Claude response for Telegram (Markdown V2, escape)
        -> Send via bot.send_message(chat_id=GROUP_ID, ...)
        -> Log to report_runs table
```

**Key properties:**
- Scheduled reports use **direct context injection** (data fits in <8K tokens), not tool use. Single round-trip, predictable cost.
- Tool use is reserved for **interactive Q&A** where Claude must decide what to query.

### Flow 3: Interactive Chat (user asks a question)

```
User posts message in Telegram group / DM
    -> Telegram webhook -> FastAPI -> Bot Handler.on_message
        -> Load recent conversation history from conversations table (last N turns)
        -> Build Claude request:
            - System prompt: tool descriptions + data freshness note + persona
            - Messages: [history..., {user: current_message}]
            - tools: [query_metrics, get_campaign_detail, get_landing_pages, compare_periods]
        -> Agentic loop:
            while response.stop_reason == 'tool_use':
                for tool_use_block in response.content:
                    result = ToolExecutor.execute(tool_use_block.name, tool_use_block.input)
                    append tool_result to messages
                response = claude.messages.create(...)
        -> Extract final text, format for Telegram, send
        -> Persist turn to conversations table
```

**Key properties:**
- **Stateless between requests** — conversation state lives in the DB, not in memory. Survives restarts and webhook horizontal scaling.
- **Tool budget** — cap agentic loop iterations (e.g., max 5) to bound cost
- **Conversation pruning** — keep last 10 turns + summary of older turns to control context size

---

## AI Context Strategy

### Decision: Tool Use, Not RAG, Not System-Prompt Injection

Three patterns compete for "how does Claude see the data":

| Pattern | When to use | Verdict for this project |
|---------|-------------|--------------------------|
| **System-prompt injection** (dump data into the prompt) | Small, fixed dataset that fits in context | Used **only for scheduled reports** (pre-aggregated, bounded size) |
| **RAG / embeddings** | Unstructured text corpus, semantic search | Wrong fit — marketing data is structured numeric time-series, not text. Embeddings give worse recall than SQL on numbers. |
| **Tool use** (Claude calls functions to query DB) | Structured data, dynamic queries, time-bounded recency | **PRIMARY pattern** for interactive Q&A |

### Recommended Tool Surface (v1)

Keep the tool surface small and high-leverage. Each tool should answer a class of questions, not a single question.

```python
tools = [
    {
        "name": "query_metrics",
        "description": "Run an aggregated query against the unified metrics store. Use for questions about totals, averages, trends across campaigns or landing pages over a date range.",
        "input_schema": {
            "type": "object",
            "properties": {
                "source": {"enum": ["meta_ads", "ga4", "joined"]},
                "metrics": {"type": "array", "items": {"enum": ["spend","impressions","clicks","ctr","cpc","conversions","sessions","engagement_rate","conversion_rate"]}},
                "dimensions": {"type": "array", "items": {"enum": ["date","campaign","ad_set","ad","landing_page","device"]}},
                "date_from": {"type": "string", "format": "date"},
                "date_to": {"type": "string", "format": "date"},
                "filters": {"type": "object"},
                "order_by": {"type": "string"},
                "limit": {"type": "integer", "default": 50}
            },
            "required": ["metrics", "date_from", "date_to"]
        }
    },
    {
        "name": "get_campaign_detail",
        "description": "Get full detail for a single campaign by name or id including recent daily timeline.",
        ...
    },
    {
        "name": "compare_periods",
        "description": "Compare metrics between two date ranges (e.g., this week vs last week). Returns deltas and % changes.",
        ...
    },
    {
        "name": "list_underperformers",
        "description": "Return campaigns or landing pages flagged by simple heuristics (high spend low conversions, falling CTR, etc.) over a window.",
        ...
    }
]
```

### Why this beats raw SQL-as-a-tool

A `run_sql` tool would technically work but is dangerous (SQL injection paths, schema leakage, model hallucinating columns) and expensive (Claude generates long SQL strings). A small **purpose-built tool surface** is:
- Faster (smaller token footprint per tool call)
- Safer (parameters are validated; no arbitrary execution)
- More accurate (Claude does not need to guess column names)
- Easier to evolve (add a new tool without retraining intuition)

### Context Composition

For each chat turn, the Claude request is composed as:

```
system:
  - Persona: "You are a marketing analyst assistant..."
  - Data freshness: "Data was last ingested at {ingestion_log.last_success}. Meta data has ~6h delay for conversions."
  - Schema hints: "Available metrics are X, Y, Z. Date format YYYY-MM-DD. Today is {date}."
  - Guardrails: "If a user asks about platforms other than Meta or GA4, decline."

messages:
  - last N conversation turns (alternating user/assistant)
  - current user message
  - [tool_use / tool_result loops]
```

**Prompt caching:** The system prompt + tool definitions are stable across turns. Use **Anthropic prompt caching** to cache this prefix; cached tokens cost 10% of standard input. Significant cost reduction for an interactive bot.

### Model Selection

- **Sonnet 4.6** for interactive chat (default) — best balance of cost and reasoning over structured data
- **Haiku 4.5** for scheduled summary reports (template-driven, lower complexity) — ~3x cheaper
- **Opus 4.7** only for opt-in "deep analysis" command if added later

---

## Telegram Bot Architecture

### Library: `python-telegram-bot` v22+

Recommended over `aiogram` and `pyTelegramBotAPI` for: most mature, official-feeling, excellent `ConversationHandler` (though we will not use it heavily — see below), async-first, integrates cleanly with FastAPI via webhooks.

### Deployment Mode: Webhook in production, polling in dev

| Concern | Polling | Webhook |
|---------|---------|---------|
| Local dev | Trivial — `application.run_polling()` works | Needs ngrok / public URL |
| Production latency | 100ms-30s depending on long-poll cycle | <500ms typical |
| Multi-instance safety | **409 Conflict if two processes poll** | Stateless — load balancer friendly |
| Public URL required | No | Yes (HTTPS) |
| Resource use | Persistent connection always | Idle when no traffic |

**Decision:** Webhook in production. The bot needs a public HTTPS endpoint anyway (FastAPI is already there). The 409 conflict in polling is a hard blocker for any future horizontal scaling or even blue/green deploys.

### Conversation State: DB-backed, not `ConversationHandler`

`python-telegram-bot`'s `ConversationHandler` is a finite-state machine for **wizard-like flows** (e.g., onboarding: ask name -> ask email -> ask company). It is the **wrong tool** for free-form chat with an LLM.

For this project:
- **Conversation history lives in the `conversations` table** keyed by `(chat_id, user_id)`
- Each Telegram update triggers a stateless handler that loads history, calls Claude, persists the new turn
- This survives restarts, allows multiple workers, and gives us audit/debug data

### Chat Targeting

- **Scheduled reports** -> single `GROUP_CHAT_ID` from config
- **Interactive Q&A** -> reply in same chat where mention/command was received
- Support both **group mentions** (`@botname what was spend yesterday?`) and **DMs**
- Suppress responses in groups unless directly mentioned (to avoid noise)

---

## Scheduler Architecture

### Decision: APScheduler in-process, AsyncIOScheduler

**Rejected alternatives:**

| Option | Why rejected |
|--------|-------------|
| **System cron** | No Python integration, no return values, hard to test, separate deploy artifact |
| **Celery + Beat** | Massive overkill — requires Redis/RabbitMQ, separate worker processes, monitoring. Adds ops burden for a single-tenant 2-source pipeline. |
| **GitHub Actions / cloud schedulers** | Couples ingestion to platform, harder to debug, adds latency to "manual trigger" flows |

**APScheduler chosen because:**
- Lives in the same process as FastAPI — single deploy artifact
- Persistent job store (SQLite-backed) survives restarts
- Async-native (`AsyncIOScheduler`) — shares the event loop with FastAPI
- Cron and interval triggers built-in
- Easy to add `/trigger/{job_id}` admin endpoint for manual runs

### Job Catalog (initial)

| Job | Trigger | Action |
|-----|---------|--------|
| `ingest_meta_ads_recent` | Every 4 hours | Pull last 2 days of Meta insights |
| `ingest_ga4_recent` | Every 4 hours | Pull last 2 days of GA4 data |
| `ingest_meta_ads_backfill` | Daily at 02:00 | Full re-pull of last 30 days (catches late attribution) |
| `daily_summary_report` | Daily at 09:00 local | Generate + send daily Telegram report |
| `weekly_summary_report` | Monday at 09:00 | Weekly digest |

### Failure Semantics

- Each job wraps execution in a try/except, logs to `ingestion_log` / `report_runs`
- Failures notify a fallback Telegram chat (admin) with stack trace
- Retries with exponential backoff for transient API errors (rate limits, 5xx)
- Permanent failures (auth) page the operator via a distinct alert

---

## Multi-Source Data Normalization

### The Canonical Schema

The unified data layer is the **single most important design artifact**. Get this right and every downstream component is easy; get it wrong and the entire system fights you.

#### Core tables (SQLite/Postgres compatible)

```sql
-- Dimension: campaigns (Meta-native)
CREATE TABLE campaigns (
    campaign_id     TEXT PRIMARY KEY,        -- Meta's campaign ID
    name            TEXT NOT NULL,
    objective       TEXT,                    -- AWARENESS, TRAFFIC, CONVERSIONS, etc.
    status          TEXT,                    -- ACTIVE, PAUSED, etc.
    created_at      TIMESTAMP,
    updated_at      TIMESTAMP
);

-- Fact: daily ad performance (Meta)
CREATE TABLE ad_metrics (
    date            DATE NOT NULL,
    campaign_id     TEXT NOT NULL REFERENCES campaigns(campaign_id),
    ad_set_id       TEXT,
    ad_id           TEXT,
    spend           DECIMAL(12,2),
    impressions     BIGINT,
    clicks          BIGINT,
    conversions     INTEGER,
    conversion_value DECIMAL(12,2),
    ctr             DECIMAL(6,4),
    cpc             DECIMAL(8,4),
    cpm             DECIMAL(8,4),
    fetched_at      TIMESTAMP,
    PRIMARY KEY (date, campaign_id, ad_set_id, ad_id)
);

-- Fact: daily landing-page performance (GA4)
CREATE TABLE ga_metrics (
    date            DATE NOT NULL,
    landing_page    TEXT NOT NULL,
    source          TEXT,                    -- traffic source (google, facebook, direct...)
    medium          TEXT,                    -- cpc, organic, social...
    campaign_name   TEXT,                    -- UTM campaign — JOIN KEY to ad_metrics
    sessions        BIGINT,
    users           BIGINT,
    engaged_sessions BIGINT,
    avg_engagement_duration DECIMAL(8,2),
    conversions     INTEGER,
    conversion_value DECIMAL(12,2),
    fetched_at      TIMESTAMP,
    PRIMARY KEY (date, landing_page, source, medium, campaign_name)
);

-- Bridge: optional materialized join for fast cross-source queries
CREATE VIEW campaign_performance_unified AS
SELECT
    a.date,
    a.campaign_id,
    c.name AS campaign_name,
    a.spend,
    a.clicks AS ad_clicks,
    a.conversions AS ad_reported_conversions,
    SUM(g.sessions) AS ga_sessions,
    SUM(g.conversions) AS ga_reported_conversions,
    SUM(g.conversion_value) AS ga_revenue
FROM ad_metrics a
JOIN campaigns c USING (campaign_id)
LEFT JOIN ga_metrics g ON g.campaign_name = c.name AND g.date = a.date
GROUP BY 1,2,3,4,5,6;

-- Operational tables
CREATE TABLE ingestion_log (
    id              INTEGER PRIMARY KEY,
    source          TEXT,
    started_at      TIMESTAMP,
    finished_at     TIMESTAMP,
    status          TEXT,                    -- success, partial, failed
    rows_upserted   INTEGER,
    error_message   TEXT
);

CREATE TABLE conversations (
    id              INTEGER PRIMARY KEY,
    chat_id         BIGINT,
    user_id         BIGINT,
    turn_index      INTEGER,
    role            TEXT,                    -- user, assistant, tool_result
    content         TEXT,                    -- JSON-encoded Anthropic content block
    created_at      TIMESTAMP
);

CREATE TABLE report_runs (
    id              INTEGER PRIMARY KEY,
    report_type     TEXT,
    triggered_at    TIMESTAMP,
    status          TEXT,
    telegram_message_id BIGINT,
    summary         TEXT
);
```

### The Join Key Problem

**The hardest data modeling decision:** how to link Meta campaigns to GA4 sessions.

Meta's `campaign.name` and GA4's `utm_campaign` should match — **but only if UTM parameters are correctly tagged on Meta ads**. They often are not.

**Recommended approach:**

1. **Primary join:** `campaigns.name == ga_metrics.campaign_name` (utm_campaign)
2. **Validate on ingest:** Log warnings when Meta campaigns have no matching GA4 sessions for >3 days (likely missing UTMs)
3. **Surface ambiguity to the user:** When Claude reports cross-source numbers, include a confidence note ("Note: 12 Meta campaigns have no matching GA4 traffic — UTM tagging may be incomplete")
4. **Do not try to be clever** — fuzzy matching on campaign names will produce wrong numbers silently. Hard match or no match.

### Database Choice: SQLite First, Postgres When Needed

| Stage | DB | Why |
|-------|----|----|
| **v1 (MVP)** | SQLite via `aiosqlite` | Zero ops, one file, easy backups, sufficient for single team, supports concurrent reads + serialized writes |
| **v2 (if needed)** | Postgres | If multi-tenant, if data >10GB, if multiple concurrent ingest writers, if TimescaleDB compression needed |

Design schema in standard SQL so the migration is mechanical when triggered.

---

## Configuration Management

### Credential Inventory

| Credential | Used by | Rotation impact |
|------------|---------|-----------------|
| `META_APP_ID`, `META_APP_SECRET`, `META_ACCESS_TOKEN`, `META_AD_ACCOUNT_ID` | Meta Ads ingestion | Long-lived tokens; rotate via Meta Business Manager |
| `GOOGLE_APPLICATION_CREDENTIALS` (service account JSON) or OAuth client | GA4 ingestion | Service account recommended over OAuth for unattended jobs |
| `GA4_PROPERTY_ID` | GA4 ingestion | Static per property |
| `TELEGRAM_BOT_TOKEN` | Telegram bot | Issued by @BotFather |
| `TELEGRAM_GROUP_CHAT_ID`, `TELEGRAM_ADMIN_CHAT_ID` | Report delivery | Static per environment |
| `ANTHROPIC_API_KEY` | Claude calls | Rotate per Anthropic guidance |
| `WEBHOOK_SECRET_TOKEN` | Telegram webhook verification | Generated at deploy |
| `DATABASE_URL` | All DB access | `sqlite:///./data/metrics.db` in dev |

### Recommended Pattern

1. **Pydantic Settings (`pydantic-settings`)** for typed loading from `.env`
2. **Single `Config` object loaded once at boot**, passed via dependency injection
3. **`.env` for dev**, **secrets manager (or env vars) for production** (Doppler, AWS Secrets Manager, or Fly.io secrets)
4. **Fail fast on missing config** — validate all required credentials at startup before accepting traffic
5. **Never log credentials** — Pydantic Settings can mark fields as `SecretStr`

```python
class Settings(BaseSettings):
    meta_access_token: SecretStr
    meta_ad_account_id: str
    ga4_property_id: str
    google_credentials_path: Path
    telegram_bot_token: SecretStr
    telegram_group_chat_id: int
    anthropic_api_key: SecretStr
    database_url: str = "sqlite+aiosqlite:///./data/metrics.db"
    webhook_url: str | None = None
    log_level: str = "INFO"

    model_config = SettingsConfigDict(env_file=".env")
```

### Storage Layout

```
project/
  .env                     # NOT committed
  .env.example             # committed template
  data/
    metrics.db             # SQLite, .gitignored
    google_credentials.json # NOT committed
  src/
    config.py
    ingestion/
    bot/
    report/
    tools/
    db/
```

---

## Component Boundaries & Interface Contracts

### `ingestion/` -> `db/`

```python
class CanonicalAdMetric(BaseModel):
    date: date
    campaign_id: str
    ad_set_id: str | None
    ad_id: str | None
    spend: Decimal
    impressions: int
    clicks: int
    conversions: int
    # ...

class IngestionResult(BaseModel):
    rows_upserted: int
    rows_skipped: int
    errors: list[str]

# Contract
async def ingest_meta_ads(date_from: date, date_to: date) -> IngestionResult: ...
async def ingest_ga4(date_from: date, date_to: date) -> IngestionResult: ...
```

### `tools/` -> `db/`

Each Claude tool maps to one async function returning JSON-serializable dict. **Never return raw DB rows.** Always project to a stable contract:

```python
async def query_metrics(input: QueryMetricsInput) -> QueryMetricsOutput:
    rows = await db.execute(build_query(input))
    return {"rows": [...], "row_count": ..., "date_range": ..., "notes": [...]}
```

### `bot/` -> `tools/` + Claude

The bot handler is the **agentic loop owner**:

```python
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    history = await load_history(update.effective_chat.id, limit=10)
    response = await claude_agent_loop(
        user_message=update.message.text,
        history=history,
        tools=ALL_TOOLS,
        max_iterations=5,
    )
    await update.message.reply_text(response, parse_mode="MarkdownV2")
    await persist_turn(...)
```

### `report/` -> `bot/`

The Report Generator does not own a Telegram client — it asks the bot module to send:

```python
async def send_to_group(text: str, parse_mode: str = "MarkdownV2") -> int:
    """Returns the message_id."""
```

This keeps Telegram client config in one module.

---

## Suggested Build Order

Strict ordering optimized for **fastest end-to-end demo** and **highest learning-per-day**. Each phase produces something demonstrable.

### Phase 1 — Walking Skeleton (1-2 days)
**Goal:** Prove the wiring end-to-end with one fake metric.

1. FastAPI app + healthcheck + Pydantic Settings
2. SQLite + Alembic migrations + one `ad_metrics` table
3. `python-telegram-bot` in polling mode locally
4. Hardcode one fake metric -> bot can `/report` and respond with "Spend yesterday: $123"
5. APScheduler with a single job that logs "tick"

**Exit criterion:** You can run `python -m src` and `/report` in Telegram returns a stub response. No external APIs yet.

### Phase 2 — One Real Data Source (2-3 days)
**Goal:** Real numbers from Meta only.

1. Meta Marketing API client with paginated `insights` fetch
2. Canonical schema + UPSERT logic
3. APScheduler job runs ingestion every 4 hours
4. `/report` queries DB and returns a real summary (no Claude yet, just SQL + template)

**Exit criterion:** Telegram report shows real Meta data updated every 4 hours.

### Phase 3 — Add Claude for Summaries (1-2 days)
**Goal:** AI-written daily summaries.

1. Anthropic SDK integration
2. Daily report job: query DB, build context blob, call Claude, send to Telegram
3. Prompt template + few-shot examples for tone
4. Token usage logging

**Exit criterion:** Daily 9am Telegram message is Claude-generated and reads naturally.

### Phase 4 — Add GA4 (2 days)
**Goal:** Second data source online.

1. GA4 Data API client (service account auth)
2. `ga_metrics` table + ingestion job
3. Update daily report to include GA4 sessions + landing pages
4. Validate UTM join — log mismatches

**Exit criterion:** Reports include both Meta spend and GA4 landing-page performance.

### Phase 5 — Interactive Q&A with Tool Use (3-4 days)
**Goal:** The "ask anything" experience.

1. Define tool schemas (`query_metrics`, `compare_periods`, `get_campaign_detail`)
2. Implement tool executor
3. Agentic loop in bot handler
4. Conversation persistence
5. Switch to webhook mode for production deploy
6. Prompt caching for system prompt + tools

**Exit criterion:** User asks "which campaigns are underperforming this week?" in Telegram and gets a grounded, accurate answer.

### Phase 6 — Hardening (ongoing)
- Error handling, retry semantics, alerting
- Cost monitoring (Anthropic token usage dashboard)
- Backfill jobs for historical data
- `list_underperformers` heuristic tool
- Anomaly-driven push reports (not just scheduled)

### Why this order?

1. **Walking skeleton first** unblocks the long tail of "how does X talk to Y" questions before they bite you mid-feature
2. **Meta before GA4** because Meta's API is the harder one (versioning, token expiry, rate limits); de-risk it first
3. **Template reports before Claude** so you can compare AI-written vs deterministic and prove Claude is adding value, not just spending money
4. **Tool use is last** because it requires the canonical schema to be stable — building it before ingestion is reliable produces a frustrating debugging surface

---

## Anti-Patterns to Avoid

1. **Storing API responses verbatim in JSON columns and parsing at query time.** Normalize on ingest. The CDM is the whole point.
2. **Using `ConversationHandler` for chat state.** It is for wizard flows, not LLM dialogue.
3. **Letting Claude write arbitrary SQL.** Use a tool surface with validated parameters.
4. **Polling Telegram in production.** 409 conflicts on any restart or redeploy.
5. **Running scheduled jobs in the FastAPI request handler.** Use APScheduler with its own job store.
6. **Coupling report content to the database schema directly.** Reports go through a Claude synthesis step or a template — never raw row dumps.
7. **Caching Claude responses by user question.** Marketing data changes; cache the system prompt prefix (Anthropic prompt caching), not the answers.
8. **Fuzzy-matching campaign names across Meta and GA4.** Hard match on UTMs or surface as separate datasets.
9. **One giant `ingest_everything()` job.** Per-source jobs with independent failure isolation.
10. **Synchronous Claude calls inside the Telegram webhook handler with no timeout.** Telegram retries webhooks that take >60s. Cap Claude calls at 30s, return a "still thinking" message if exceeded.

---

## Sources

- [ConversationHandler — python-telegram-bot docs](https://docs.python-telegram-bot.org/en/v21.8/telegram.ext.conversationhandler.html) — HIGH confidence
- [Long Polling vs. Webhook — grammY guide (general Telegram bot architecture)](https://grammy.dev/guide/deployment-types) — HIGH confidence
- [Polling vs Webhook in Telegram Bots — Hostman](https://hostman.com/tutorials/difference-between-polling-and-webhook-in-telegram-bots/) — MEDIUM confidence
- [Tool use with Claude — Anthropic API Docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview) — HIGH confidence
- [Programmatic tool calling — Anthropic API Docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/programmatic-tool-calling) — HIGH confidence
- [Building Real Apps With Claude API — Tool Use, RAG, and Agent Patterns](https://dev.to/ji_ai/building-real-apps-with-the-claude-api-tool-use-rag-and-agent-patterns-explained-kcb) — MEDIUM confidence
- [Claude API Pricing 2026 — Anthropic](https://platform.claude.com/docs/en/about-claude/pricing) — HIGH confidence
- [Scheduling Tasks in Python: APScheduler vs Celery Beat — Leapcell](https://leapcell.io/blog/scheduling-tasks-in-python-apscheduler-vs-celery-beat) — MEDIUM confidence
- [Task Scheduling and Background Jobs in Python](https://blog.naveenpn.com/task-scheduling-and-background-jobs-in-python-the-ultimate-guide) — MEDIUM confidence
- [The Right ETL Architecture for Multi-Source Data Integration — DZone](https://dzone.com/articles/etl-architecture-multi-source-data-integration) — MEDIUM confidence
- [Data Pipeline Architecture Explained — Monte Carlo](https://www.montecarlodata.com/blog-data-pipeline-architecture-explained/) — MEDIUM confidence
- [SQLite vs PostgreSQL — Airbyte](https://airbyte.com/data-engineering-resources/sqlite-vs-postgresql) — HIGH confidence
