# Ads Reporting Agent

AI-powered conversational agent that pulls data from Meta Ads + GA4, posts auto-generated reports to Telegram, and lets allowlisted team members ask follow-up questions in natural language via Claude tool use.

> **Phase 1 (this release):** secure scaffold only -- Telegram bot answers `/start`, `/status`, `/help`. Real ingestion + reports ship in Phase 2.

## Quick Start

### 1. Prerequisites

- Docker + Docker Compose (or local `uv` + Python 3.12 for non-containerized dev)
- A Telegram bot token from [@BotFather](https://t.me/BotFather)
- Your team's Telegram group chat ID (numeric, usually negative for groups)
- Your own Telegram user ID

**How to find your chat ID:** add [@userinfobot](https://t.me/userinfobot) to your group and DM it -- it replies with both your user ID and the group's chat ID. Remove it after.

### 2. Configure

```bash
cp .env.example .env
# Open .env in your editor and fill in:
#   TELEGRAM_BOT_TOKEN=123456:ABC-...
#   TELEGRAM_ALLOWED_CHAT_IDS=-1001234567890        # the group chat id
#   TELEGRAM_ALLOWED_USER_IDS=123456789,987654321   # comma-separated
# Phase 2+ keys (META_*, GA4_*, ANTHROPIC_*) can stay blank for Phase 1.
```

### 3. Run

```bash
docker compose up --build
```

The bot will log `boot`, `storage_ready`, `webhook_cleared`, `scheduler_started`, `polling_start` and then wait for messages. Send `/start` in your allowlisted group; you should see:

> Ads Reporting Agent online. Use /report for latest data.

### 4. Verify

- `/start` -- confirms the bot is alive
- `/status` -- shows last sync timestamps and row counts (all zero in Phase 1)
- `/help` -- lists commands

Non-allowlisted users get **no response** (silent drop by design -- see Security below).

## Environment Variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | yes | -- | Bot token from @BotFather |
| `TELEGRAM_ALLOWED_CHAT_IDS` | yes | empty | CSV of allowed chat IDs (groups, supergroups) |
| `TELEGRAM_ALLOWED_USER_IDS` | yes | empty | CSV of allowed user IDs (DMs) |
| `META_APP_ID`, `META_APP_SECRET`, `META_ACCESS_TOKEN`, `META_AD_ACCOUNT_ID` | Phase 2 | -- | Meta Ads API |
| `GA4_PROPERTY_ID`, `GA4_SERVICE_ACCOUNT_JSON` | Phase 3 | -- | Google Analytics 4 |
| `SHOPIFY_STORE_DOMAIN`, `SHOPIFY_ADMIN_TOKEN` | funnel-v3 | -- | Shopify Admin API (preorder purchases); unset = clean no-op |
| `SHOPIFY_API_VERSION` | no | `2025-01` | Shopify Admin API version |
| `GA4_EVENT_LIST` | no | `page_view_lp,cta_click,add_to_cart,begin_checkout,purchase,lead_submit,quiz_complete` | Event names pulled by GA4 event-level ingestion (`ga4_events` table) |
| `META_PIXEL_ID` | Tracking Health | -- | Meta pixel/dataset ID for per-event browser/server counts + best-effort EMQ (`pixel_health` table); unset = clean no-op |
| `GOOGLE_SHEETS_LEADS_SPREADSHEET_ID` | Email leads | -- | Preorder Leads Dashboard sheet (`email_leads` table); unset = clean no-op and the Overview "Email leads" section hides. **Share the sheet with the service-account email** or the pull 403s |
| `LEADS_INTERNAL_EMAIL_PATTERNS` | no | `resonancetech.co,nowaplanet,testingthis.com,example.com,nghia,tradanghi,tai@,taihoang,tester` | Substrings flagging an address as internal/test, excluded from every sheet-sourced number. Matched case-insensitively anywhere in the address (catches plus-addressing and any TLD). Rows are stored either way and only flagged, so edits re-classify on the next pull. Keep teammate-name entries as narrow as they can be while still catching every known address — `tai@`/`taihoang` rather than a bare `tai`, which would also swallow real leads with *mountain*/*retail*/*detail*/*captain* in the handle, silently |
| `ANTHROPIC_API_KEY` | Phase 4 | -- | Claude chat backend |
| `DB_PATH` | no | `/data/metrics.db` | SQLite file inside container |
| `LOG_LEVEL` | no | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `REPORT_TIMEZONE` | no | `UTC` | IANA TZ name (e.g. `America/New_York`) for scheduled jobs |

## Dashboard pages

| Page | What it answers |
|---|---|
| Overview | Spend/revenue KPIs split by goal (Paid vs Initiate Checkout), email leads, source reconciliation |
| Campaign Detail | One campaign's daily trend + ad-set breakdown |
| Ads | Creative performance by format / style / concept (Meta only) |
| Funnel | Preorder funnel v3, landing-page table, quiz funnel, tracking-health audit |
| AI Chat | Ad-hoc questions over the same tables |
| Attribution | MMM (modelled), Meta vs GA4 side by side |
| Activity | Meta change history |
| Tracking Health | Pixel/GA4 event health, `(not set)` share, freshness |
| **Orders** | Per-order first/last touch, Meta ad traceback, derived "so what" notes |

Every metric carries a source badge (M = Meta, G = GA4, Shop = Shopify, Sheet =
Google Sheet, Model = derived) so no number's origin has to be guessed.

## Local Development (without Docker)

```bash
uv sync --extra dev
uv run python -m src
```

Run tests:

```bash
uv run pytest
```

## Security

The bot enforces a strict allowlist (chat ID OR user ID match) **before** any handler runs, including before any future Claude call. Non-allowlisted updates are silently dropped -- they are not replied to (replying confirms the bot's existence to drive-by probers). This is enforced by `src/bot/middleware.py` (INFRA-02).

Secrets are loaded via `pydantic-settings` from environment variables / `.env`. The `.env` file is gitignored; only `.env.example` is committed. Logs go through a structlog redaction processor that strips fields named `token`, `secret`, `access_token`, `password`, `api_key`, `raw_response`, `ad_creative_body`, `email`, `phone` (INFRA-05).

## Deploy Targets

- **VPS:** `docker compose up -d` and mount a persistent host directory at `./data`.
- **Fly.io:** create a volume (`fly volumes create ads-data --size 1`), reference it in `fly.toml`, deploy. The Dockerfile is fly-compatible.
- **Railway:** create a service from this repo, set env vars in the dashboard, attach a persistent volume mounted at `/data`.

## Project Layout

```
src/
├── __main__.py          # entrypoint: asyncio.run(main())
├── main.py              # lifecycle wiring
├── config.py            # Settings (pydantic-settings)
├── logging_setup.py     # structlog JSON + redaction
├── bot/
│   ├── middleware.py    # AllowlistMiddleware (INFRA-02)
│   ├── handlers.py      # /start, /status, /help
│   └── setup.py         # create_bot_and_dispatcher
├── db/
│   ├── schema.py        # SQL DDL constants
│   ├── migrations.py    # hand-rolled migration runner
│   └── client.py        # async DB client with UPSERT helpers
└── scheduler/           # reserved for Phase 2 jobs
tests/                   # pytest (allowlist + UPSERT idempotency)
.planning/               # GSD planning artifacts (not shipped in image)
```

## Phase Roadmap

1. **Phase 1 (this):** Foundation + walking skeleton
2. **Phase 2:** Meta Ads ingestion, daily/weekly Telegram reports, alerts
3. **Phase 3:** GA4 ingestion, cross-source UTM join, attribution-honest reports
4. **Phase 4:** Conversational AI via Claude tool use
5. **Phase 5:** Hardening -- Sentry, dead-man's-switch, backfill

See `.planning/ROADMAP.md` for full requirement traceability.
