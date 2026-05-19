# Features Research: Ads Reporting Agent

**Project:** AI-powered Ads Reporting Agent (Meta Ads + GA4 → Telegram + Conversational AI)
**Researched:** 2026-05-19
**Overall confidence:** MEDIUM-HIGH (verified across multiple industry sources for 2025-2026)

---

## Executive Summary

The 2025-2026 marketing analytics landscape has shifted decisively toward **AI-first reporting agents** that (a) auto-summarize multi-source data, (b) detect anomalies before humans notice them, and (c) answer follow-up questions in natural language. Tools like Supermetrics AI, Improvado, Power BI Copilot, and Looker Studio + Gemini have set the new bar — users now expect "tell me what's wrong" rather than "show me a chart."

For a Telegram-delivered agent, the table-stakes bar is well-defined: **scheduled digests + alerts + conversational follow-up + clear recommendations.** The differentiators are in (a) cross-source reasoning (Meta Ads ↔ GA4 attribution gaps), (b) anomaly *explanations* (not just detections), and (c) Telegram-native UX that respects the channel (inline keyboards, chart images, no clutter).

According to Meta's 2025 advertiser survey, automated reporting tools deliver **23% faster issue response times and 18% better ROAS** versus manual workflows — the value proposition is proven.

---

## Table Stakes (Must Have)

These are features users *expect*. Missing any of them makes the product feel incomplete or unprofessional.

### 1. Core Report Content

| Feature | Why Expected |
|---------|--------------|
| **Multi-period comparison** (today vs. yesterday, week-over-week, MTD vs. prior MTD) | Without comparison, numbers have no meaning. Industry standard. |
| **Top movers** (best/worst campaigns, ad sets, landing pages) | Marketers always ask "what changed?" first. |
| **Spend pacing vs. budget** (daily spend vs. daily budget allocation) | Budget overruns are the #1 reason teams want alerts. |
| **ROAS / CPA / conversion totals** at account + campaign + ad set levels | The three metrics every stakeholder reviews. |
| **Cross-source unification** (Meta Ads spend → GA4 conversions on the same landing page) | This is the whole point — it's what users currently do manually. |
| **Plain-English summary** (2-5 sentences at top of every report) | TL;DR is non-negotiable for daily reports in a chat channel. |

### 2. Report Scheduling

Based on industry data (weekly digests get 65% open rates vs. 45% for daily), the cadence patterns users expect:

| Cadence | Purpose | Default Content |
|---------|---------|-----------------|
| **Daily morning digest** (8-9am local) | Quick pulse check, anomaly callouts | 200-400 words: yesterday's totals, biggest movers, any alerts |
| **Weekly summary** (Monday morning) | Strategic review, week-over-week trends | 600-1,000 words: campaign performance, landing page winners, recommendations |
| **Real-time alerts** (event-driven, not scheduled) | Catch problems early | Budget pacing breach, ROAS drop, spend spike, conversion drop |
| **On-demand** (`/report today` command) | Ad-hoc check by user | Same as daily, current data |

**Required scheduling features:**
- Configurable timezone (user's local time, not UTC)
- Configurable delivery window (don't post at 3am)
- Pause/resume (vacation mode, weekends optional)
- Per-group customization if bot serves multiple groups

### 3. Conversational Q&A

Users naturally ask these questions — the bot must answer them well:

| Question Pattern | Example | Why Common |
|------------------|---------|------------|
| **Which/What is best/worst?** | "Which campaigns are underperforming?" | Diagnostic — most asked question |
| **Why is X happening?** | "Why did CPA spike yesterday?" | Causal — requires cross-source reasoning |
| **Compare X to Y** | "Compare iOS vs Android performance" | Segmentation analysis |
| **Show me trend** | "How has ROAS trended this month?" | Pattern recognition |
| **What if** / Recommendation | "Should we pause the retargeting campaign?" | Decision support |
| **Drill down** | "Break that down by ad set" | Follow-up on previous answer |

**Required conversational features:**
- **Context retention** across messages ("break that down" must remember what "that" was)
- **Ambiguity handling** — when user says "the campaign," ask which one if multiple
- **Source citations** — answers reference data ("Meta Ads, last 7 days") to build trust
- **Graceful failure** — say "I don't have that data" rather than hallucinate
- **Suggested follow-ups** as inline keyboard buttons (reduces typing on mobile)

### 4. Telegram UX Patterns

| Feature | Why Required |
|---------|--------------|
| **Markdown/HTML formatting** (bold metric names, code blocks for tables) | Plain text reports are unreadable; Telegram natively supports MarkdownV2 |
| **Inline keyboards under reports** ("Drill down", "Compare to last week", "Why?") | Standard pattern; reduces typing; matches user mental model |
| **Chart images** (PNG via QuickChart or similar) for trend lines, comparisons | Tables work for snapshots; charts required for trends. 2025 standard. |
| **Emoji indicators** for sentiment (🟢 up, 🔴 down, ⚠️ alert, 📊 chart) | Telegram-native; scannable on mobile in seconds |
| **Threaded replies / quote replies** when answering follow-up Qs | Maintains conversation context visually |
| **Bot commands menu** (`/report`, `/alerts`, `/help`, `/settings`) | Telegram's standard discoverability mechanism |
| **Group + DM support** (reports in group, complex Qs in DM) | Avoids spamming group with long answers |

### 5. Anomaly Detection & Alerts

Per 2025 industry data, teams with automated anomaly detection catch issues **3-7 days earlier** than manual review.

| Alert Type | Default Threshold | Why Required |
|------------|-------------------|--------------|
| **Spend spike** | >150% of recent daily average | Catches algorithm runaway, audience overlap |
| **Budget pacing breach** | On track to overspend monthly budget by >10% | Most-requested alert by finance-conscious teams |
| **ROAS drop** | <70% of 7-day rolling average for 2+ days | Catches creative fatigue, audience saturation |
| **Zero-conversion alert** | Campaign with >$X spend and 0 conversions for 24h | Catches tracking breakage |
| **CPC spike** | >2x baseline | Catches auction shifts (e.g., BFCM CPC surges) |
| **Conversion drop on landing page** | GA4 conversion rate down >30% week-over-week | Cross-source signal — landing page broken? |

**Required alert features:**
- Configurable thresholds per metric
- Snooze/mute per alert type
- "Why" explanation (rule that triggered + current vs. expected value)
- Severity levels (info, warning, critical)

### 6. Recommendations

Users now expect AI-generated "what should I do" — not just "what happened."

| Recommendation Type | Example |
|---------------------|---------|
| **Pause/reduce** | "Pause Campaign X — CPA is 3x target for 5 consecutive days" |
| **Scale** | "Increase budget on Ad Set Y — ROAS consistently above 4.0 last 14 days" |
| **Creative refresh** | "Ad creative Z is at 5+ frequency and CTR has dropped 40% — consider rotation" |
| **Landing page fix** | "LP /summer-sale has 65% bounce rate vs. 35% account average — investigate" |

Each recommendation must include: **observation → evidence → confidence → suggested action**.

### 7. Operational / Trust Features

| Feature | Why Required |
|---------|--------------|
| **Last-sync timestamp** on every report | Builds trust; users want to know data freshness |
| **Account/property indicator** in reports | If multi-account, must always say which one |
| **Error transparency** ("Meta API rate-limited, retrying at 09:15") | Silent failures destroy trust |
| **Audit log** of what reports were sent when | Useful for debugging and compliance |
| **Read-only by design** (no actions on ad accounts) | Explicit constraint from project scope |

---

## Differentiators (Competitive Advantage)

These features make the product *excellent* rather than just functional. They're what the market mostly doesn't do well yet.

### 1. Cross-Source Causal Reasoning

Most tools report Meta Ads and GA4 side-by-side; few actually *reconcile* them.

- **Attribution gap diagnostics**: "Meta reports 47 conversions but GA4 shows 32 from the same landing page — likely iOS 14.5+ ATT effect (~32% gap is typical)."
- **Conversion path stitching**: "Campaign X drove 200 GA4 sessions, of which 12 converted. Meta reports 18 conversions, suggesting 6 view-through or cross-device."
- **Landing page ↔ ad creative match**: "Ad creative emphasizes 'free shipping' but landing page leads with discount — message mismatch likely cause of 65% bounce."

### 2. Conversational Memory + Context

- Remember the user's mental model across sessions ("our brand campaigns" = specific filter the user defined once)
- Learn vocabulary ("our hero product" → SKU mapping)
- Carry context across the day: morning report → afternoon Q&A naturally references same data window

### 3. Explainable Anomalies

Most anomaly detectors flag — they don't explain. The bar to clear:

| Detection (table stakes) | Explanation (differentiator) |
|--------------------------|------------------------------|
| "Spend spiked 180% on Campaign X" | "Spike correlates with audience expansion you enabled yesterday + Black Friday auction pressure (industry CPCs up 40% sector-wide). Action: cap auto-expansion." |

### 4. Recommendation Quality

Generic ("pause underperforming ads") vs. specific ("Pause Ad Set 'Lookalike 1% iOS' — CPA $87 vs. account $42 target over 7 days, 4-day sustained underperformance, 92% confidence based on similar past patterns").

### 5. Telegram-Native Reporting UI

- **Progressive disclosure**: short summary → tap inline button → expanded section → tap → chart image. Avoids dumping 2000 words at once.
- **Daily summary as a single, well-formatted message** with charts attached as a media album, not 8 separate messages.
- **Smart message editing**: as data refreshes intraday, edit existing pinned message instead of spamming new ones.
- **`/why` command**: tap any number in a report → bot replies with explanation in thread.

### 6. Pre-Computed "Insight Cards"

Instead of always waiting for a Q&A round-trip, deliver pre-computed observations:
- "📌 Today's anomaly: TikTok-style creatives outperforming static ads 2.3x in 25-34 age band"
- "📌 Heads up: Tuesday's typically your worst conversion day — budget skewed toward Mon/Wed may help"

### 7. Conversational Onboarding

Setup via chat ("Hi! Which Meta Ads account should I monitor? Reply with the account ID or pick from the list") — most BI tools require painful config UIs.

### 8. Multi-User Group Awareness

In a group chat, the bot recognizes who's asking — different team members get different default focus (CEO sees revenue; ad ops sees CPC; growth lead sees LTV/CAC).

---

## Anti-Features (Deliberately Skip)

Things the product should *not* do, even though competitors do.

| Anti-Feature | Why Avoid | What to Do Instead |
|--------------|-----------|--------------------|
| **Custom web BI dashboard** | Explicit out-of-scope. Telegram is the surface. | Generate chart images on demand; leave dashboards to Looker. |
| **Write operations on ad accounts** | Explicit out-of-scope; read-only is a stated constraint. | Recommendations only; user executes in Meta Ads Manager. |
| **Real-time sub-minute data streaming** | Out-of-scope; API rate limits make this impractical. | Hourly/daily pulls with clear last-sync timestamps. |
| **Attribution modeling beyond what platforms provide** | Massive scope creep; this is a separate product category. | Use Meta-reported and GA4-reported conversions as-is; explain the gap. |
| **CRM integration** | Out-of-scope; LTV/cohort needs separate sources. | Stay focused on ad → site funnel. |
| **Custom alerting rule builder UI** | Telegram bots can't render complex form UIs well. | Provide 5-10 sensible defaults; allow toggling via inline buttons. Power users can edit a config file. |
| **Multi-language report localization** | Premature optimization; one team, one language. | English defaults; add later only if validated. |
| **PDF/PowerPoint export** | Telegram-native delivery is the point. | If user wants a deck, they screenshot the chat. |
| **Daily reports for *every* metric** | Notification fatigue kills engagement (45% open rate for daily vs. 65% weekly). | Daily = digest + alerts only; weekly = comprehensive. |
| **200+ metrics in every report** | Meta Ads Manager shows 200+, but only ~12 matter daily. | Curate. Top 8-12 metrics. Drill-down on request. |
| **Configurable everything (settings hell)** | Bots with deep settings menus get abandoned. | Strong opinionated defaults; minimal config (timezone, thresholds, channel). |
| **Email digests as alternative delivery** | Splits attention; pick Telegram and commit. | Telegram only. |
| **Sentiment analysis of ad copy** | Adjacent but not core. Out of validated scope. | Defer. |
| **Automated A/B test stat-sig analysis** | Requires statistical rigor and event-level data — beyond scope. | Surface differences; let user decide. |
| **Voice/audio replies** | Telegram supports it but adds complexity for marginal value. | Text + charts is sufficient. |

---

## Metric Catalog

The standard metrics to pull and surface, organized by source and tier.

### Meta Ads (Marketing API)

**Tier 1: Core Performance (every report)**

| Metric | Definition | Why Critical |
|--------|------------|--------------|
| **Spend** | Total ad spend (period) | Budget tracking |
| **Impressions** | Times ads shown | Reach indicator |
| **Clicks** (link clicks specifically) | Link clicks to destination | Top-of-funnel interest |
| **CTR (link click-through rate)** | Link clicks / impressions | Creative quality benchmark (~0.90% retail avg) |
| **CPC (cost per link click)** | Spend / link clicks | Auction efficiency (~$0.70 retail benchmark) |
| **CPM** | Cost per 1000 impressions | Auction pressure indicator |
| **Conversions** (purchases or primary objective) | Total conversion events | Outcome metric |
| **Conversion rate** | Conversions / link clicks | Funnel efficiency (~9.21% retail) |
| **CPA / Cost per result** | Spend / conversions | Unit economics |
| **ROAS** | Conversion value / spend | The metric. Targets: 3x+ e-com, 4x+ digital |
| **Conversion value / Revenue** | Total revenue attributed | Business outcome |

**Tier 2: Diagnostic (drill-down + weekly reports)**

| Metric | Use Case |
|--------|----------|
| **Frequency** | Creative fatigue detection (>5 = problem) |
| **Reach** | Unique users — audience saturation |
| **Quality ranking / Engagement ranking / Conversion ranking** | Meta's own creative scoring |
| **Video view metrics** (ThruPlays, 25%/50%/75%/95% completion) | Video creative analysis |
| **Outbound CTR vs. inline CTR** | Distinguishes interest from interaction |
| **Cost per ThruPlay** | Video efficiency |
| **Attribution window breakdowns** (1d-click, 7d-click, 1d-view) | iOS 14.5+ era essential |
| **Modeled conversions** | Post-iOS 14.5 mandatory field |
| **Action breakdowns** (add-to-cart, initiate-checkout, purchase) | Funnel diagnosis |

**Tier 3: Segment breakdowns (on-demand)**

- By campaign objective
- By placement (Feed, Stories, Reels, Audience Network)
- By device platform (iOS, Android, Desktop)
- By age + gender
- By region/country
- By time-of-day / day-of-week
- By ad creative

### Google Analytics 4 (Data API)

**Tier 1: Traffic & Engagement (every report)**

| Metric | Definition | Why Critical |
|--------|------------|--------------|
| **Sessions** | Total sessions in period | Volume indicator |
| **Total users** | Unique users | Audience size |
| **Engagement rate** | % of engaged sessions (>10s, 2+ pages, or conversion) | GA4's bounce-rate replacement; landing page health |
| **Average engagement time per session** | Time spent engaged | Content quality |
| **Bounce rate** (session-scoped, paired with landing-page dimension) | 1 - engagement rate | Landing page diagnostic |
| **Key events** (formerly conversions) | Configured conversion events | Outcome |
| **Session key event rate** | Sessions with key event / total sessions | Funnel efficiency |
| **Total revenue** | E-commerce revenue | Business outcome |

**Tier 2: Source & Behavior**

| Metric / Dimension | Use Case |
|--------------------|----------|
| **Sessions by source/medium** | Channel attribution |
| **Sessions by landing page** | Entry-point performance — critical for ad analysis |
| **Pages per session** | Engagement depth |
| **New vs. returning users** | Acquisition vs. retention |
| **First-user source/medium/campaign** | Acquisition attribution |
| **Session source/medium/campaign** | Last non-direct click |

**Tier 3: E-commerce + Events**

- Items viewed, items added to cart, checkouts started, purchases
- Cart-to-purchase rate, view-to-purchase rate
- Average order value (AOV)
- Custom event counts (configured per business)

### Cross-Source Derived Metrics

These are computed by joining Meta Ads and GA4 — the unique value of this product.

| Metric | Computation | Insight |
|--------|-------------|---------|
| **Meta-vs-GA4 conversion gap** | (Meta conversions − GA4 conversions) / Meta | iOS attribution loss, view-through, cross-device |
| **Effective landing-page bounce** | GA4 bounce rate filtered to sessions from Meta Ads UTM | Ad-LP message match quality |
| **True CPA (blended)** | Meta spend / GA4 key events for that source | Reality-check on platform-reported CPA |
| **Spend → site behavior chain** | Spend → impressions → clicks → sessions → engaged sessions → conversions | Full-funnel diagnosis |
| **Landing page revenue contribution** | GA4 revenue by landing page × Meta spend driving it | Which LPs deserve more budget |

### Pacing & Trend Metrics (Computed)

- **Daily run-rate** vs. monthly budget
- **Forecast end-of-month spend** (linear projection)
- **Days remaining in budget**
- **7-day rolling average** for noise-smoothing in alerts
- **Week-over-week % change** for all Tier 1 metrics
- **Month-to-date** comparisons (current MTD vs. prior MTD at same day)

---

## Feature Dependencies

```
Data sources (Meta API + GA4 API)
   └── Data fetcher / scheduler
        └── Storage / cache layer
             ├── Report generator → Telegram delivery
             ├── Anomaly detector → Alert delivery
             └── Conversational AI (Claude) ←── Q&A handler
                                              └── Inline keyboard handler
```

**Build order implication:** Data pipeline must be solid before reports; reports must be solid before conversational layer (which depends on the same data).

---

## MVP Recommendation

A defensible Phase 1 ships these table-stakes features only:

1. **Data pipeline**: Meta Ads API + GA4 Data API → unified storage (Tier 1 metrics only)
2. **Daily digest** delivered to Telegram at configurable time (Markdown formatted, Tier 1 metrics, week-over-week comparison)
3. **3-5 default alerts**: spend spike, ROAS drop, zero-conversion, budget pacing, CPC spike
4. **Conversational Q&A** for: "which campaigns are best/worst," "why did X change," "show me [metric] trend" — context retention within session
5. **Inline keyboard follow-ups**: "Drill down", "Compare to last week", "Why?", "Show chart"
6. **Chart images via QuickChart** for trend questions

**Defer to Phase 2+:**
- Weekly summary report (use daily + on-demand initially)
- Recommendation engine (start with anomaly *detection*, add *prescription* later)
- Cross-source causal reasoning (start with side-by-side; add reconciliation when patterns are clear)
- Multi-user awareness in groups
- Pre-computed insight cards

---

## Confidence Assessment

| Area | Confidence | Reason |
|------|------------|--------|
| Table stakes features | HIGH | Consistent across multiple 2025-2026 industry sources |
| Metric catalog | HIGH | Meta + GA4 official docs; widely documented benchmarks |
| Telegram UX patterns | HIGH | Telegram Bot API documentation + multiple 2025 guides agree |
| Anomaly detection patterns | MEDIUM-HIGH | Strong industry consensus; specific thresholds vary by account |
| Conversational Q&A patterns | MEDIUM | Common questions well-documented; implementation details vary |
| Anti-features | MEDIUM | Based on industry signal + explicit project scope; some are opinion |
| Differentiators | MEDIUM | Forward-looking; what tools *should* do, not always what they do |

---

## Sources

- [Best AI Tools for Marketing Analytics 2025 — Dataslayer](https://www.dataslayer.ai/blog/best-ai-tools-for-marketing-analytics-in-2025)
- [Marketing Intelligence Systems 2026 — Improvado](https://improvado.io/blog/marketing-intelligence-tools)
- [9 Best AI Reporting Tools for Marketers 2026](https://www.1clickreport.com/blog/best-ai-reporting-tools-2026)
- [10 Best Facebook Ad Reporting Tools 2025 — Motion](https://motionapp.com/blog/the-best-facebook-ad-reporting-tools-2024)
- [Meta Ads Reporting: What to Review Daily — AdAmigo](https://www.adamigo.ai/blog/meta-ads-reporting-what-to-review-daily)
- [Automated Facebook Ads Reporting Guide — Improvado](https://improvado.io/blog/best-facebook-ads-reports-templates)
- [Campaign Monitoring and Anomaly Detection 2025 — Improvado](https://improvado.io/blog/campaign-monitoring-and-anomaly-detection)
- [How to Detect Meta Ads Anomalies — Madgicx](https://madgicx.com/blog/meta-ads-anomaly-detection)
- [Budget/Pacing Anomaly Detection — Sprinklr](https://www.sprinklr.com/help/articles/ad-campaign-optimization/budgetpacing-anomaly-detection/675902c26bc4e346abf8ee80)
- [Designing an Anomaly Alerting System for SMBs](https://medium.com/@vedika.hansaria/when-ads-go-wrong-quietly-designing-an-anomaly-alerting-system-for-smbs-83ba1ddda4a3)
- [GA4 API Dimensions & Metrics — Google for Developers](https://developers.google.com/analytics/devguides/reporting/data/v1/api-schema)
- [Key GA4 Metrics to Track 2025 — Swydo](https://www.swydo.com/blog/google-analytics-4-metrics/)
- [GA4 Dimensions and Metrics Complete 2026 Reference](https://www.digitalapplied.com/blog/ga4-dimensions-metrics-complete-reference)
- [Key Events in GA4 — Loves Data](https://www.lovesdata.com/blog/google-analytics-key-events/)
- [Telegram Bot Features — Official Docs](https://core.telegram.org/bots/features)
- [Developer's Guide to Building Telegram Bots 2025](https://stellaray777.medium.com/a-developers-guide-to-building-telegram-bots-in-2025-dbc34cd22337)
- [Telegram Inline Keyboard UX Design Guide](https://wyu-telegram.com/blogs/444/)
- [Telegram Bot Keyboard Types Complete Guide — Bitders](https://bitders.com/blog/telegram-bot-keyboard-types-a-complete-guide-to-commands-inline-keyboards-and-reply-keyboards)
- [Telegram Text Formatting — Paprika.bot](https://paprika.bot/blog/telegram-text-formatting/)
- [Flight Data Visualization Chart.js + Telegram Bot — n8n](https://n8n.io/workflows/7238-flight-data-visualization-with-chartjs-quickchart-api-and-telegram-bot/)
- [Conversational Analytics — Coupler.io](https://blog.coupler.io/conversational-analytics/)
- [106 Questions You Can Ask Your Data with AI — Domo](https://www.domo.com/blog/106-questions-you-can-ask-your-data-with-ai-chat)
- [Chatbot Data Analytics 2026 — BlazeSQL](https://www.blazesql.com/blog/chatbot-data-analytics)
- [What Is Conversational Analytics — IBM](https://www.ibm.com/think/topics/conversational-analytics)
- [Marketing Report Scheduling 2025 Guide — Reportsmate](https://www.reportsmate.com/blog/marketing-report-scheduling-daily-weekly-or-monthly-2025-guide)
- [Reporting Cadence for Marketing Teams — 2POINT Agency](https://www.2pointagency.com/glossary/reporting-cadence-for-marketing-teams/)
- [Weekly Report Templates and Examples — AgencyAnalytics](https://agencyanalytics.com/blog/weekly-reports)
- [AI Spend Pacing Anomaly Detection — 2POINT Agency](https://www.2pointagency.com/glossary/ai-for-spend-pacing-anomaly-detection/)
