# Stack Research: Ads Reporting Agent

**Project:** AI-powered Ads Reporting Agent (Meta Ads + GA4 -> Claude -> Telegram)
**Researched:** 2026-05-19
**Overall confidence:** HIGH

## TL;DR

Build it in **Python 3.12+** using **aiogram 3** (Telegram), the official **facebook-business** and **google-analytics-data** SDKs, the **anthropic** Python SDK with tool-use for the conversational layer, **APScheduler** for scheduled reports, **SQLite** for persistence/caching, **pandas** for in-memory cross-source joins, **pydantic-settings** for config, and **Docker Compose** on a small VPS (or Fly.io / Railway) for deployment.

This is a single-process, single-tenant async Python service. The whole system fits in one container.

---

## Recommended Stack (2026)

### Language & Runtime

| Component | Choice | Version | Rationale |
|---|---|---|---|
| Language | Python | 3.12+ (3.13 preferred) | Both Google's `google-analytics-data` and Meta's `facebook-business` ship first-class Python SDKs. Node.js has no official Meta Marketing SDK and a worse GA4 story. The Anthropic ecosystem (SDK, tool use examples, cookbooks) is Python-first. |
| Package manager | uv | latest | Fast, reproducible, replaces pip+venv+pip-tools. De-facto 2026 standard. |
| Async runtime | asyncio (stdlib) | - | aiogram, httpx, the Anthropic SDK, and APScheduler 3.10+ all interop with asyncio. |

### Telegram Layer

| Component | Choice | Version | Rationale |
|---|---|---|---|
| Bot framework | **aiogram** | 3.x (3.28+) | Fully async, modern (Py 3.10+), built on aiohttp, idiomatic for AI bots that run long Claude/API calls without blocking. Active maintenance, large 2026 community. |
| Alternative considered | python-telegram-bot | 21.x | Mature, big docs corpus, but its async story is bolted on; aiogram is async-native and a cleaner fit for a long-running agent that fans out to multiple external APIs. |

### Data Source SDKs

| Component | Choice | Version | Rationale |
|---|---|---|---|
| Meta Marketing API | **facebook-business** (Meta Business SDK) | 22.0+ (Graph API v22) | Official Meta-maintained Python SDK. Covers Ads, Insights, Campaigns, AdSets, Ads, Creatives. Direct REST is a fallback but the SDK saves weeks of pagination/error/typing work. |
| Google Analytics 4 | **google-analytics-data** | 0.22.0+ | Official Google client for the GA4 Data API. Provides `BetaAnalyticsDataClient`, `run_report`, and `batch_run_reports`. `run_report` is the workhorse; `batch_run_reports` saves quota for the daily report job. |
| Google auth | google-auth + google-auth-oauthlib | latest | Required by the GA4 client; supports both service-account JSON and OAuth flows. |

### AI Layer

| Component | Choice | Version | Rationale |
|---|---|---|---|
| LLM SDK | **anthropic** (Anthropic Python SDK) | 0.102.0+ | Official, well-maintained, supports streaming, tool use, prompt caching, and the latest Claude models. |
| Pattern | Tool use (function calling) agent loop | - | Define tools like `get_meta_campaign_performance(date_range, account_id)`, `get_ga4_landing_pages(date_range)`, `cross_reference_campaigns_and_landings(date_range)`. Claude decides which to call for follow-up questions. This is the 2026 standard pattern for conversational data agents. |
| Prompt caching | Yes | - | Cache the system prompt + tool definitions + recent report context. Significant cost reduction on chat follow-ups. |

> Note: Use the plain `anthropic` SDK, NOT `claude-agent-sdk`. The Agent SDK wraps the Claude Code CLI and is designed for coding/developer agents — overkill and the wrong shape for a Telegram-bot data analyst.

### Data & Storage

| Component | Choice | Version | Rationale |
|---|---|---|---|
| Primary store | **SQLite** | 3.45+ (stdlib) | Zero-ops, single-file, supports JSON columns, FTS5, and window functions — plenty for storing fetched metric snapshots, report history, and conversation state. The data is bounded (one team, daily/hourly pulls); Postgres is unnecessary infra. |
| ORM / query | **SQLAlchemy 2.x (async)** or raw `aiosqlite` | 2.0+ | SQLAlchemy if you expect schema churn; `aiosqlite` if you prefer hand-rolled SQL. Either is fine. |
| Cache | SQLite tables + in-memory dict | - | No Redis. Cache GA4/Meta responses in SQLite keyed by (source, query_hash, date_range). Redis adds an extra service for no real-time value at this scale. |
| DataFrames | **pandas** | 2.2+ | Familiar, ecosystem-rich, integrates with the GA4/Meta response shapes, more than fast enough for thousands-of-rows ad data. Polars is overkill at this scale. |
| Migrations | Alembic (if using SQLAlchemy) | latest | Schema versioning. Optional for v1 — can defer to plain SQL files initially. |

### Scheduling & Background Work

| Component | Choice | Version | Rationale |
|---|---|---|---|
| Scheduler | **APScheduler** (AsyncIOScheduler) | 3.10+ | In-process, no broker, persistent jobstore via SQLAlchemy/SQLite. Perfect for "run the daily report at 9am, refresh metric cache every hour". Celery + Redis + worker process is wildly disproportionate for this workload. |
| Retries | **tenacity** | 9.x | Decorator-based exponential backoff for Meta/GA4 API calls. Meta's API is notoriously rate-limited and times out on heavy Insights queries. |
| HTTP client | **httpx** (async) | 0.27+ | For any direct REST you do outside the SDKs (e.g., Telegram media uploads, ad-hoc Graph API calls). |
| Rate limiting | **pyrate-limiter** or aiometer | latest | Token-bucket limiter for Meta Insights to stay under app-level limits. |

### Configuration & Secrets

| Component | Choice | Version | Rationale |
|---|---|---|---|
| Config | **pydantic-settings** | 2.x | Typed, validated env-var loading with `SecretStr` masking for tokens. Industry-standard 2026 pattern. |
| Local secrets | `.env` (python-dotenv via pydantic-settings) | - | Per-environment `.env.dev`, `.env.prod`. Never commit; never use production secrets locally. |
| Production secrets | Docker/host env vars injected from a secret manager (Doppler, 1Password, Infisical, or the platform's secret store) | - | Twelve-factor pattern. |

### Logging & Observability

| Component | Choice | Version | Rationale |
|---|---|---|---|
| Logging | **structlog** | 24+ | Structured JSON logs that survive in Docker. Easy to grep, easy to ship later. |
| Error tracking | **Sentry** (optional, free tier) | sentry-sdk 2.x | Catches Telegram handler crashes and Claude/API errors in production. Optional v1, recommended before going live. |

### Deployment

| Component | Choice | Rationale |
|---|---|---|
| Containerization | **Docker + docker-compose** | One service (`bot`), one volume (`./data` for SQLite), trivial to move between hosts. |
| Hosting (recommended) | **Small VPS** (Hetzner CX22, DigitalOcean $6 droplet, etc.) with Docker Compose | Cheapest, simplest, and the workload is a single always-on Python process. ~$5-10/mo. |
| Hosting (managed alt) | **Railway** or **Fly.io** | Railway: easiest "git push -> live" with Docker Compose support. Fly.io: better for global edge, fine-grained control. Both are reasonable if you'd rather not run a VPS. |
| Not recommended | AWS Lambda / Cloud Functions | The bot needs a long-lived process to hold Telegram long-polling (or a webhook + a persistent scheduler). Serverless is awkward for both. Save it for the cron-only variant if you ever drop the chat feature. |

---

## Alternatives Considered

| Category | Recommended | Alternative | Why Not |
|---|---|---|---|
| Language | Python | Node.js / TypeScript | No official Meta Marketing SDK for Node; GA4 client exists but ecosystem is thinner; Claude tool-use examples are Python-first; pandas has no real JS equivalent. Nothing about Node makes this project easier. |
| Telegram | aiogram 3 | python-telegram-bot | Mature but async is grafted on; aiogram is async-native, lighter, and the more modern 2026 default for AI bots. |
| Telegram | aiogram 3 | telethon / pyrogram | These are MTProto user-client libraries (act as a user), not Bot API. Wrong tool — you want the official Bot API. |
| Telegram | aiogram 3 | Telegraf (Node) | Excellent library, but pulls you into Node where the rest of the stack is weaker. |
| Meta SDK | facebook-business | Direct REST + httpx | Possible, but you re-implement pagination, retries, typing, and field schemas. Use the SDK; drop to REST only for endpoints the SDK doesn't expose. |
| GA4 | google-analytics-data | Looker Studio API / scraping | Looker Studio has no official programmatic export API. Going direct to GA4 (and Meta) is more reliable — already a Key Decision in PROJECT.md. |
| LLM SDK | anthropic | claude-agent-sdk | Agent SDK is for coding agents that drive the Claude Code CLI. For a chat-with-data agent, plain `anthropic` + tool use is simpler, cheaper, and more controllable. |
| LLM SDK | anthropic | LangChain / LangGraph | Adds a heavy abstraction layer for a use case that's a straightforward agentic loop with ~5-10 tools. Direct SDK is more debuggable and the 2026 trend is away from LangChain for narrow agents. |
| Storage | SQLite | PostgreSQL | Postgres is the safe default for "any new app," but this app is single-tenant, single-writer, with bounded data. SQLite removes a whole service. If multi-tenant or write-concurrent later, migrate via Alembic. |
| Cache | SQLite tables | Redis | Redis is justified for sub-millisecond cache, pub/sub, or distributed locks. None apply here. Stick with SQLite-backed memoization. |
| DataFrames | pandas | Polars / DuckDB | Polars/DuckDB shine at millions-of-rows-per-day pipelines. Ad accounts produce hundreds of rows/day per source. Pandas is sufficient and the ecosystem fit (matplotlib/plotly if you ever render charts) is better. |
| Scheduler | APScheduler | Celery + Redis/RabbitMQ + Beat | Celery requires a broker + worker process + beat process. Massive overkill for "run a report at 9am". |
| Scheduler | APScheduler | cron + standalone script | Workable but you lose: shared in-memory state with the bot, easy programmatic schedule changes from chat ("send me the report at 10 instead"), and unified logging. APScheduler runs inside the bot process and wins on every axis here. |
| Config | pydantic-settings | dynaconf / hydra | Both fine, but pydantic-settings is lighter and integrates with the pydantic types you'll already use for the Claude tool schemas. |
| Deployment | Docker on VPS | Kubernetes | One container. Don't. |

---

## Key Library Details

### Install commands

```bash
# Initialize project (uv)
uv init ads-reporting && cd ads-reporting
uv python pin 3.12

# Core runtime
uv add aiogram                       # Telegram bot framework (async)
uv add anthropic                     # Claude SDK
uv add facebook-business             # Meta Marketing API SDK
uv add google-analytics-data         # GA4 Data API client
uv add google-auth                   # GA4 auth

# Scheduling + HTTP + retries
uv add "apscheduler>=3.10"
uv add httpx
uv add tenacity
uv add pyrate-limiter

# Data
uv add pandas
uv add sqlalchemy aiosqlite          # or just aiosqlite if you skip the ORM

# Config + secrets
uv add pydantic pydantic-settings python-dotenv

# Logging + errors
uv add structlog
uv add sentry-sdk                    # optional but recommended pre-prod

# Dev
uv add --dev pytest pytest-asyncio respx ruff mypy
```

### Version pins to be aware of

| Library | Min version | Note |
|---|---|---|
| `aiogram` | 3.28+ | The 2.x line is unmaintained; ensure you're on 3.x. Requires Python 3.10+. |
| `facebook-business` | 22.0+ | Tracks Graph API v22 (released March 2026). Older versions target deprecated Graph API versions. |
| `google-analytics-data` | 0.22.0+ | Latest as of May 2026; backwards-compatible API. |
| `anthropic` | 0.102.0+ | Required for current Claude model IDs and prompt-caching headers. |
| `apscheduler` | 3.10+ | Use `AsyncIOScheduler`. (APScheduler 4.x is in beta — avoid for v1.) |
| `pydantic` | 2.x | Required by `pydantic-settings` 2.x. |

### Architectural notes Claude will need at implementation time

1. **Telegram long-polling vs webhook:** Start with long-polling (`bot.start_polling()` in aiogram). Switch to webhooks only if you need lower latency or hit polling limits — neither applies for a small team.
2. **Meta Insights async jobs:** Heavy Insights queries should use Meta's async report endpoint (`POST .../insights` with `async=true`, then poll `report_run_id`). The SDK exposes this via `AdAccount.get_insights_async()`. Critical for monthly/large-account pulls; synchronous calls time out.
3. **GA4 quota:** GA4 Data API quota is "tokens per project per day" plus per-property limits. Batch related queries with `batch_run_reports` and cache aggressively (response data is immutable for past dates).
4. **Conversation state:** Store the last N turns + the most recent report context in SQLite keyed by Telegram `chat_id`. Pass to Claude as conversation history. Use prompt caching on the system prompt + tool definitions.
5. **Tool definitions live with code:** Define one Python function per tool, use type hints + docstrings, generate the JSON schema from pydantic models. Single source of truth.
6. **Time zones:** Ad accounts and GA4 properties each have their own configured timezone. Always normalize to a single project timezone for reports and surface the source timezone in the report footer.

---

## Confidence Notes

| Recommendation | Confidence | Why |
|---|---|---|
| Python over Node | HIGH | Meta has no official Node Marketing SDK; Python is the obvious choice. Verified via Meta's official GitHub. |
| aiogram 3 for Telegram | HIGH | Verified via PyPI, official docs, multiple 2026 comparison articles. |
| facebook-business SDK | HIGH | Official Meta repo, current version 22.0 tracking Graph API v22 (March 2026 release). |
| google-analytics-data SDK | HIGH | Official Google client, latest version 0.22.0 released May 2026. |
| anthropic SDK + tool use | HIGH | Verified against Claude API docs and the data-analyst-agent cookbook. anthropic 0.102.0 confirmed latest as of May 2026. |
| Plain `anthropic` over `claude-agent-sdk` | HIGH | Agent SDK is explicitly for Claude Code CLI agents; wrong shape here. |
| APScheduler over Celery | HIGH | Multiple 2026 comparisons agree: Celery is overkill without distributed needs. |
| SQLite over Postgres for v1 | MEDIUM | Defensible for single-tenant bounded data, but Postgres is the safer default if multi-team / multi-account use ever appears. Migration path via SQLAlchemy is straightforward. |
| pandas over Polars | HIGH | Ad data volumes are small; pandas ecosystem fit is better. |
| pydantic-settings for config | HIGH | De-facto standard; backed by official Pydantic docs. |
| Docker Compose on VPS | MEDIUM | Cheapest and simplest; Railway/Fly.io equally valid if the team prefers managed. Not a technical risk either way. |
| Long-polling over webhooks | MEDIUM | Long-polling is simpler for v1; webhooks may be preferable if deploying behind a load balancer with HTTPS already terminated. Re-evaluate at deploy time. |

---

## Sources

- [facebook/facebook-python-business-sdk (GitHub)](https://github.com/facebook/facebook-python-business-sdk)
- [facebook-business on PyPI](https://pypi.org/project/facebook-business/)
- [Meta Marketing API docs](https://developers.facebook.com/docs/marketing-api/)
- [Graph API v24 changelog](https://developers.facebook.com/docs/graph-api/changelog/version24.0/)
- [google-analytics-data on PyPI](https://pypi.org/project/google-analytics-data/)
- [Google Analytics Data API overview](https://developers.google.com/analytics/devguides/reporting/data/v1)
- [Python Client for Google Analytics Data (docs)](https://googleapis.dev/python/analyticsdata/latest/)
- [anthropic on PyPI](https://pypi.org/project/anthropic/)
- [anthropics/anthropic-sdk-python (GitHub)](https://github.com/anthropics/anthropic-sdk-python)
- [Claude tool use overview](https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview)
- [Claude data analyst agent cookbook](https://platform.claude.com/cookbook/managed-agents-data-analyst-agent)
- [aiogram (GitHub)](https://github.com/aiogram/aiogram)
- [aiogram docs](https://docs.aiogram.dev/)
- [python-telegram-bot vs aiogram comparison](https://piptrends.com/compare/python-telegram-bot-vs-aiogram)
- [APScheduler vs Celery Beat (Leapcell)](https://leapcell.io/blog/scheduling-tasks-in-python-apscheduler-vs-celery-beat)
- [Redis vs SQLite for solo developers](https://solodevstack.com/blog/redis-vs-sqlite-solo-developers)
- [Redis vs PostgreSQL caching 2026 (Nordync)](https://www.nordync.com/blog/redis-vs-postgresql-caching-2026)
- [Polars vs Pandas 2026 (Kanaries)](https://docs.kanaries.net/articles/polars-vs-pandas)
- [Pydantic Settings docs](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)
- [pyrate-limiter on PyPI](https://pypi.org/project/pyrate-limiter/)
- [Meta Graph API timeout limits (Ryze)](https://www.get-ryze.ai/blog/meta-graph-api-timeout-limit-for-complex-ads-and-insights-requests)
- [Railway vs Fly.io 2026](https://thesoftwarescout.com/fly-io-vs-railway-2026-which-developer-platform-should-you-deploy-on/)
- [Railway Docker Compose guide](https://docs.railway.com/guides/docker-compose)
