# ad-teardown

Research pipeline that takes apart Meta ads — **ours (Nowa) with real performance** and
**competitors' from the public Ad Library** — so the next round of ad optimisation starts
from evidence instead of guesses. US market only.

> **Standalone.** This folder shares the repo with the dashboard but nothing else: its own
> venv, its own dependencies, its own SQLite file. It never imports from `src/`, and the
> dashboard never imports from here. The Docker image copies only `src/` + `.streamlit/`, and
> the root pytest only collects `tests/`, so nothing in here can change the dashboard's
> build or test run.

Source spec: *Competitor Ad Teardown: Implementation Specification v1.0* (2026-09-25).
Plan: `nowa-web/docs/plans/ad-teardown-system-plan.md`. This is the "simple stack" version.
The spec's contracts are kept (taxonomy, evidence IDs, mismatch rule, `unknown` handling,
versioned analyses); its infrastructure (Postgres/Redis/Next.js) is not.

## Two lanes — never blended

| Lane | Source | What we know | What we never claim |
|---|---|---|---|
| **own** (`nowa`; `pawcast` later) | Marketing API, read-only | copy, creative, destination, **spend/CTR/CPM/LPV/ATC/purchases/leads** per ad, per country | — |
| **competitor** | Ad Library UI (page-scoped, US) | copy, creative, destination, platforms, start date, versions | spend, impressions, CTR, conversions. **Longevity is a proxy**, labelled as such |

Competitor ads never get an `ad_metrics` row, so no query can mix the lanes by accident.

## Setup (one time)

```bash
cd ad-teardown
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python requests pyyaml beautifulsoup4 "playwright==1.62.0" pytest ruff
```

Why `playwright==1.62.0`: it matches the Chromium build already cached on this Mac (the
brand-funnel-xray skill uses the same one). A newer Playwright asks for a new browser download.

Meta token: read from `META_ACCESS_TOKEN`, else from the `export` line in `~/.zshrc` (the
never-expiring "n8n x RT" SYSTEM_USER token). It is never printed or written to disk.

## Commands

Run from `ad-teardown/`:

```bash
.venv/bin/python -m teardown pull-own --brand nowa            # ~1 min, ~50 Graph calls
.venv/bin/python -m teardown crawl-competitors --parent nowa  # ~1–2 min per brand
.venv/bin/python -m teardown import my_ads.csv                # manual ads (see csv_import.py)
.venv/bin/python -m teardown status
.venv/bin/python -m pytest -q
```

Every command is safe to re-run: ads are keyed by `(source, source_ad_id)`, so a re-run
updates `last_seen_at` and never duplicates.

## Where things live

```text
config/brands.yaml     who we analyze: own brands (campaign prefix) + competitors (page_id, status, evidence)
teardown/db.py         SQLite schema (spec §5, trimmed) + upsert helpers
teardown/normalize.py  URL canonicalization, ad-code/format parsing (pure functions)
teardown/sources/      meta_own.py (Marketing API) · meta_adlib.py (Ad Library) · csv_import.py
data/                  teardown.db + raw/ payloads (gitignored — contains competitor media refs)
tests/                 synthetic fixtures only, no network
```

## Traps already handled (don't undo)

- **Pawcast shares the ad account.** Brands are scoped by campaign-name prefix, always.
- **`effective_status` filter param lies** (it returns ads with a paused parent too). We list all
  ads and read the returned field.
- **Full creative fields on the paginated `/ads` call return a 500.** We list ids first, then
  fetch detail 25 at a time.
- **The ad-set name is not the destination or the format.** The link comes from the creative,
  and the format comes from media facts. The name-derived format is a cross-check only.
  `Static+Carousel` in an ad-set name says nothing about the ad.
- **Ad Library has regional pages** (tonies has 3, Yoto 2). Only the page whose ads link to the
  US domain is used. Evidence is recorded in `brands.yaml`.
- **An unbounded Ad Library scroll hangs.** Scrolling stops after 6 stalls. Brands with more
  ads than one crawl captures are a sample, not a census; `reported_results` is stored next to
  the parsed count.

## Status

- [x] M0 skeleton · [x] M1 acquisition (own + competitor + CSV)
- [ ] M2 evidence (image/video text, landing pages) · [ ] M3 analysis + offer check
- [ ] M4 dashboard/export · [ ] M5 weekly run
