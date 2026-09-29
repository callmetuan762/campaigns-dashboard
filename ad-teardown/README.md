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
# M1 — acquire
.venv/bin/python -m teardown pull-own --brand nowa            # ~1 min, ~50 Graph calls
.venv/bin/python -m teardown crawl-competitors --parent nowa  # ~1–2 min per brand
.venv/bin/python -m teardown import my_ads.csv                # manual ads (see csv_import.py)
# M2 — evidence (run media straight after a crawl: competitor media links expire)
.venv/bin/python -m teardown media                            # download + frames/audio, ~10 min
.venv/bin/python -m teardown landing                          # landing pages, 24 h cache
.venv/bin/python -m teardown evidence                         # OCR + ASR → evidence rows (slow first run)
# M3 — classify + offer check (claude -p on your logged-in account)
.venv/bin/python -m teardown analyze --dry-run                # cost estimate, calls nothing
.venv/bin/python -m teardown analyze --budget 10              # hard USD cap per run (default 10)
.venv/bin/python -m teardown analyze --campaign "Last Call" --budget 1   # pilot one campaign
.venv/bin/python -m teardown status
.venv/bin/python -m pytest -q
```

### M3 — how classification works

- **Route:** `claude -p` on the logged-in account (`claude auth status` must say `loggedIn: true`;
  inside the desktop app's terminal, run `/login` once). Amy's choice, 2026-09-28: Haiku + Sonnet.
- **Cascade:** deterministic first (format from media, `cta_intent` from the button text, the
  policy check). Then **Claude Haiku 4.5**, 15 distinct ads per call. Then **Claude Sonnet 5**
  (`--effort low`) for low-confidence critical labels, any mismatch, and (own ads only) a
  low-confidence page comparison. **needs_review** for high-severity mismatches and policy
  conflicts.
- **Guarantees, enforced by `validate.py`, not by trusting the model:** labels only from
  `taxonomy.py`; every cited id must exist in the ad's bundle; a mismatch needs an ad-side AND
  a page-side evidence id; a page we couldn't read can never be a mismatch. Invalid output
  gets one repair, then `analysis_failed`.
- **Budget:** the estimated cost is reserved before every call. A call that would cross the cap
  is not made; those ads become `budget_paused`, and the run is `partial` rather than overspent.
- **Cost, measured:** about $0.004 per distinct ad on Haiku. Duplicated creatives (same
  bundle, different ad set) are analyzed once and validated per ad (1,465 ads → ~950 distinct).
  See `provider.py` for the four tuning steps and their measured effect.
- **Cache:** re-runs skip ads whose evidence hash + taxonomy + prompt version are unchanged.
  Bump `PROMPT_VERSION` / `TAXONOMY_VERSION` on any prompt or label change. Old analyses are
  kept.
- **Price rule (`policy.price_rule`, own ads):** the ad's "then $X" vs the page's "returns to $X", by
  rule. It exists because the model split 15 near-identical Last Call ads into 5 mismatch / 5 partial
  / 5 match: `/pages/preorder` shows both "$149 at retail" and "returns to $249". The dashboard
  leads with the rule and shows the model verdict as a second opinion.
- **Policy check (`policy.py`, own ads):** ad claims vs `config/offer_facts.yaml`. `unconfirmed`
  facts go to Amy's queue; `conflict` with a confirmed fact → `needs_review`. Never auto-edited.

M2 extra setup: `uv pip install --python .venv/bin/python pillow imagehash faster-whisper`.
OCR uses macOS's built-in Vision framework via `tools/ocr.swift`, compiled on first use into
`data/bin/ocr` (needs Xcode command-line tools; runs locally, free). ASR uses faster-whisper
`base.en` on CPU; the ~145 MB model downloads on first use.

Every command is safe to re-run: ads are keyed by `(source, source_ad_id)`, so a re-run
updates `last_seen_at` and never duplicates.

## Where things live

```text
config/brands.yaml     who we analyze: own brands (campaign prefix) + competitors (page_id, status, evidence)
teardown/db.py         SQLite schema (spec §5, trimmed) + upsert helpers
teardown/normalize.py  URL canonicalization, ad-code/format parsing (pure functions)
teardown/sources/      meta_own.py (Marketing API) · meta_adlib.py (Ad Library) · csv_import.py
teardown/media.py      download (byte-sniffed, size-capped) → analysis image / video frames + audio
teardown/landing.py    SSRF-guarded fetch, allowlist, robots, render fallback → page facts
teardown/evidence.py   ad copy + OCR + ASR + landing_dom → citable evidence rows
tools/ocr.swift        local OCR (macOS Vision)
teardown/taxonomy.py   label lists (versioned) + deterministic CTA → intent + output schema
teardown/bundle.py     compact per-ad evidence bundle with short ids (e1, e2…) + evidence_hash
teardown/prompts.py    spec §4 system prompt + definitions; few-shots in config/prompts/fewshot.json
teardown/provider.py   claude -p adapter (cost-tuned) + mock provider for tests
teardown/validate.py   schema/evidence rules applied to every model result
teardown/policy.py     deterministic ad ↔ offer_facts check (own ads)
teardown/analyze.py    cascade, dedup, repair, escalation, budget cap
teardown/report.py     one row per ad → report.json, ads.csv, dashboard.html (same rows → counts reconcile)
report/dashboard.template.html  the dashboard page; data is injected at /*__DATA__*/
config/offer_facts.yaml  the policy source for the M3 offer check — only Amy edits it
data/                  teardown.db, raw/, media/, cache/ (gitignored — competitor media is internal only)
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

- **Landing fetches must not pollute our own analytics.** We fetch the canonical URL (no UTMs),
  and the browser render blocks GA4, Meta Pixel, Clarity, CookieHub and Shopify monorail.
- **Our own pages are always rendered in a browser.** The `/pages/preorder` "N of 500 left"
  counter is spread across DOM nodes ("85" / "of 500 left"), so the extraction patterns
  match across line breaks.
- **robots.txt:** respected for competitors. Our own domains are exempt (`go.nowaplanet.com`
  disallows every crawler).
- **Competitor videos are not kept.** Only frames, audio and the sha256 remain (rights decision).

## Status

- [x] M0 skeleton · [x] M1 acquisition (own + competitor + CSV)
- [x] M2 evidence (media, OCR, ASR, landing pages) · [x] M3 analysis + offer check
- [x] M4 dashboard/export (`teardown report` → data/report/; published as a private Artifact) · [ ] M5 weekly run
