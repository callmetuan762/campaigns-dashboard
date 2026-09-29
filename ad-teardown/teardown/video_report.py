"""`teardown video-report`: which competitor videos keep running, taken apart for a Nowa video plan.

Two kinds of content, kept apart on purpose:
- **Computed on every run** (this module): longevity tiers, label over-index (pooled and
  brand-balanced), spine metrics (length, speech, hook text at 0 s, "screen-free" claims,
  templated copy), the per-brand top set with each ad's hook and timed beats pulled from
  OCR + ASR evidence, and Nowa's own video vs static baselines.
- **Editorial** (`report/video_notes.yaml`, version-controlled): spine templates, notes on
  specific ads, the Nowa idea bank. Humans write these. The report renders them next to the
  data and flags any top-set ad that has no note yet ("new since notes"), so stale
  interpretation is visible instead of silently presented as current.

Longevity is a proxy (the Ad Library shows no spend). Brand-balanced lift exists because one
brand (Finch) can dominate the long-running tier.
"""

from __future__ import annotations

import html
import json
import re
import statistics as st
from pathlib import Path

import yaml

from . import db, report

NOTES = Path(__file__).resolve().parent.parent / "report" / "video_notes.yaml"
OUT = db.DATA_DIR / "report" / "video-teardown.html"
DIMS = ["hook_type", "proof_type", "pain_point", "awareness_stage", "driver", "funnel_stage", "offer_type", "cta_intent"]
SCREEN_FREE = re.compile(r"screen[- ]free|no screens?\b|without (?:the |a )?screens?|zero screens|not another screen", re.IGNORECASE)
MIN_BRAND_VIDEOS, MIN_BRAND_MAX_DAYS = 5, 14


# ---------- data ----------

def _video_facts(conn, row: dict) -> dict:
    dur = conn.execute("SELECT MAX(duration_ms) FROM media_assets WHERE ad_id=? AND kind='video'",
                       (row["id"],)).fetchone()[0] or 0
    ocr, asr = [], []
    for e in conn.execute("SELECT origin, text_value, locator FROM evidence WHERE ad_id=? AND origin IN ('ocr','asr')",
                          (row["id"],)):
        loc = json.loads(e["locator"] or "{}")
        if e["origin"] == "ocr":
            ocr.append((loc.get("frame_t_ms") or 0, e["text_value"] or ""))
        else:
            asr.append((loc.get("start_ms") or 0, e["text_value"] or ""))
    ocr.sort(key=lambda x: x[0])
    asr.sort(key=lambda x: x[0])
    alltext = " ".join([row.get("body") or "", row.get("headline") or "", *(t for _, t in asr), *(t for _, t in ocr)])
    return {"duration_s": round(dur / 1000, 1) if dur else None, "ocr": ocr, "asr": asr,
            "speech": bool(asr), "text_at_0": any(t == 0 and clean_line(x) for t, x in ocr),
            "screen_free": bool(SCREEN_FREE.search(alltext)),
            "templated": "{{product" in (row.get("headline") or "") + (row.get("body") or ""),
            "multi_version": (row.get("versions") or 1) > 1}


def clean_line(s: str) -> str | None:
    """OCR noise filter: keep lines that read as words (e.g. 'Make a long trip feel mini...'),
    drop fragments like '12R2B' or 'БL:5'."""
    s = (s or "").strip()
    letters = sum(c.isalpha() and c.isascii() for c in s)
    if len(s) < 6 or letters / max(1, len(s)) < 0.6 or (" " not in s and len(s) < 12):
        return None
    return s


def hook_of(f: dict) -> dict:
    seen, text = set(), []
    for t, x in f["ocr"]:
        c = clean_line(x)
        if t <= 1000 and c and c.lower() not in seen:
            seen.add(c.lower())
            text.append(c)
    said = [x for t, x in f["asr"] if t < 3000]
    return {"on_screen": text[:4], "said": " ".join(dict.fromkeys(said))[:220] or None}


def beats_of(f: dict, limit: int = 9) -> list[tuple[int, str, str]]:
    """Timeline of what's on screen and what's said, grouped per second."""
    by_t: dict[int, list[str]] = {}
    for t, x in f["ocr"]:
        c = clean_line(x)
        if c:
            by_t.setdefault(t // 1000, [])
            if c.lower() not in (y.lower() for y in by_t[t // 1000]) and len(by_t[t // 1000]) < 3:
                by_t[t // 1000].append(c)
    out = [(s, "screen", " / ".join(v)) for s, v in by_t.items()]
    last = None
    for t, x in f["asr"]:
        if x and x != last:
            out.append((t // 1000, "said", x))
            last = x
    out.sort(key=lambda b: (b[0], b[1] != "screen"))
    return out[:limit]


def _content_key(row: dict, f: dict) -> str:
    base = " ".join(x for _, x in f["asr"]) or " ".join(x for _, x in f["ocr"]) or row.get("body") or ""
    return re.sub(r"\W+", " ", base.lower())[:120]


def compute(conn, min_days: int = 30, proven_days: int = 60, per_brand: int = 4) -> dict:
    rows = [r for r in report.rows(conn) if r["lane"] == "competitor" and r["analysis"] != "pending"
            and r["labels"]["format"] == "video"]
    by_brand: dict[str, list[dict]] = {}
    for r in rows:
        by_brand.setdefault(r["brand"], []).append(r)
    excluded = {b: rs for b, rs in by_brand.items()
                if len(rs) < MIN_BRAND_VIDEOS or max((r["days_running"] or 0) for r in rs) < MIN_BRAND_MAX_DAYS}
    V = [r for r in rows if r["brand"] not in excluded]
    facts = {r["id"]: _video_facts(conn, r) for r in V}
    long_ = [r for r in V if (r["days_running"] or 0) >= min_days]
    proven = [r for r in V if (r["days_running"] or 0) >= proven_days]
    brands = sorted({r["brand"] for r in V})
    topq = {}
    for b in brands:
        bs = sorted([r for r in V if r["brand"] == b], key=lambda r: -(r["days_running"] or 0))
        topq[b] = bs[:max(1, len(bs) // 4)]

    def share(rs, d, v):
        return sum(1 for r in rs if r["labels"][d] == v) / len(rs) if rs else 0.0

    labels = {}
    for d in DIMS:
        vals = []
        for v in sorted({r["labels"][d] for r in V}):
            a = share(V, d, v)
            lifts = [share(topq[b], d, v) - share([r for r in V if r["brand"] == b], d, v) for b in brands]
            vals.append({"value": v, "all": a, "long": share(long_, d, v), "proven": share(proven, d, v),
                         "balanced_lift": sum(lifts) / len(lifts) if lifts else 0.0})
        labels[d] = sorted([x for x in vals if x["all"] >= 0.02 or x["long"] >= 0.02], key=lambda x: -x["balanced_lift"])

    def spine(rs):
        fs = [facts[r["id"]] for r in rs]
        ds = [f["duration_s"] for f in fs if f["duration_s"]]
        q = st.quantiles(ds, n=4) if len(ds) >= 4 else [None, None, None]
        pct = lambda k: sum(f[k] for f in fs) / len(fs) if fs else 0.0
        return {"n": len(rs), "median_s": st.median(ds) if ds else None, "p25_s": q[0], "p75_s": q[2],
                "speech": pct("speech"), "text_at_0": pct("text_at_0"), "screen_free": pct("screen_free"),
                "templated": pct("templated"), "multi_version": pct("multi_version")}

    tiers = {"long": spine(long_), "short": spine([r for r in V if (r["days_running"] or 0) < min_days]),
             "proven": spine(proven)}
    dominant = max(brands, key=lambda b: sum(1 for r in proven if r["brand"] == b)) if proven else None

    top = []
    for b in brands:
        seen = set()
        for r in sorted([r for r in V if r["brand"] == b], key=lambda r: -(r["days_running"] or 0)):
            if (r["days_running"] or 0) < min_days:
                break
            k = _content_key(r, facts[r["id"]])
            if k in seen:
                continue
            seen.add(k)
            f = facts[r["id"]]
            top.append({**{k2: r[k2] for k2 in ("id", "brand", "brand_name", "days_running", "versions", "cta",
                                                 "headline", "body", "landing_final_url", "destination", "source_url")},
                        "labels": r["labels"], "duration_s": f["duration_s"], "speech": f["speech"],
                        "hook": hook_of(f), "beats": beats_of(f)})
            if sum(1 for t in top if t["brand"] == b) >= per_brand:
                break
    top.sort(key=lambda t: -(t["days_running"] or 0))

    own = [r for r in report.rows(conn) if r["lane"] == "own" and r.get("perf")]

    def perf(rs):
        s = sum(r["perf"]["spend"] for r in rs)
        i = sum(r["perf"]["impressions"] for r in rs)
        c = sum(r["perf"]["link_clicks"] for r in rs)
        lpv = sum(r["perf"]["lpv"] for r in rs)
        return {"n": len(rs), "spend": s, "ctr": c / i if i else None, "cpm": 1000 * s / i if i else None,
                "cost_per_lpv": s / lpv if lpv else None}

    own_vid = [r for r in own if r["labels"]["format"] == "video"]
    own_durs = [d for d in (_video_facts(conn, r)["duration_s"] for r in own_vid) if d]
    problem_spend = sum(r["perf"]["spend"] for r in own if r["labels"].get("hook_type") == "problem")
    own_spend = sum(r["perf"]["spend"] for r in own)
    return {
        "generated_at": db.now(), "min_days": min_days, "proven_days": proven_days,
        "n_videos": len(V), "n_long": len(long_), "n_proven": len(proven), "brands": brands,
        "excluded": {b: {"videos": len(rs), "max_days": max((r["days_running"] or 0) for r in rs)} for b, rs in excluded.items()},
        "proven_by_brand": {b: sum(1 for r in proven if r["brand"] == b) for b in brands},
        "dominant_brand": dominant,
        "observed_at": max((r["observed_at"] for r in rows), default=None),
        "labels": labels, "tiers": tiers, "top": top,
        "nowa": {"video": perf(own_vid), "static": perf([r for r in own if r["labels"]["format"] == "static_image"]),
                 "video_median_s": st.median(own_durs) if own_durs else None,
                 "video_range_s": [min(own_durs), max(own_durs)] if own_durs else None,
                 "problem_hook_spend_share": problem_spend / own_spend if own_spend else None},
    }


# ---------- render ----------

E = html.escape
pct = lambda x, d=0: "–" if x is None else f"{100 * x:.{d}f}%"
secs = lambda x: "–" if x is None else f"{x:.0f} s"
pretty = lambda s: str(s or "unknown").replace("_", " ")


def _lift(x: float) -> str:
    cls = "up" if x > 0.02 else "down" if x < -0.02 else ""
    return f'<td class="n {cls}">{100 * x:+.1f}</td>'


def summary_points(c: dict) -> list[str]:
    t, n = c["tiers"], c["nowa"]
    hooks = c["labels"]["hook_type"]
    up = [h for h in hooks if h["balanced_lift"] > 0.02][:2]
    down = [h for h in hooks if h["balanced_lift"] < -0.02][-2:]
    pts = [
        (f"<b>Short.</b> Videos that ran {c['min_days']}+ days have a median length of {secs(t['long']['median_s'])} "
         f"(middle half {secs(t['long']['p25_s'])}–{secs(t['long']['p75_s'])}). Nowa's videos: median {secs(n['video_median_s'])}."),
        (f"<b>The hook is on screen in the first second</b> in {pct(t['long']['text_at_0'])} of long-runners, and "
         f"{pct(1 - t['long']['speech'])} have no speech at all, so they work muted."),
    ]
    if up or down:
        pts.append("<b>Hooks, brand-balanced:</b> " + ", ".join(f"{pretty(h['value'])} {100 * h['balanced_lift']:+.0f} pts" for h in up + down)
                   + (f". {pct(n['problem_hook_spend_share'])} of Nowa's US spend went to problem hooks." if n["problem_hook_spend_share"] is not None else "."))
    pts.append(f"<b>\"Screen-free\" is claimed</b> in {pct(t['long']['screen_free'])} of long-runners vs {pct(t['short']['screen_free'])} of "
               "short-lived ads. Nowa has a display, so Nowa's version is \"a pet, not a screen to scroll\", never \"screen-free\".")
    v, s_ = n["video"], n["static"]
    if v["cpm"] and s_["cpm"]:
        pts.append(f"<b>Nowa's baseline to beat:</b> video ${v['cpm']:.2f} CPM and ${v['cost_per_lpv'] or 0:.2f} per landing-page view, "
                   f"vs static ${s_['cpm']:.2f} and ${s_['cost_per_lpv'] or 0:.2f} ({v['n']} video ads, ${v['spend']:,.0f} US).")
    return pts


def render(c: dict, notes: dict) -> str:
    css = (Path(__file__).resolve().parent.parent / "report" / "video.css").read_text()
    ad_notes = {str(k): v for k, v in (notes.get("ad_notes") or {}).items()}
    t = c["tiers"]
    lib = lambda i: i.split(":", 1)[-1]
    new_since = [a for a in c["top"] if lib(a["id"]) not in ad_notes]
    parts = ["<title>Competitor Video Teardown</title>", css, f"""
<header class="night"><div class="night-inner">
  <div class="brand"><span class="bcell" aria-hidden="true"></span>nowa · ad teardown</div>
  <h1>Competitor Video Teardown</h1>
  <p class="lede">Which competitor video ads keep running, taken apart hook by hook, and a Nowa video idea bank built on what lasts.</p>
  <div class="meta"><span><b>{c['n_videos']}</b> competitor videos (US)</span><span><b>{c['n_long']}</b> ran {c['min_days']}+ days · <b>{c['n_proven']}</b> ran {c['proven_days']}+</span>
  <span>observed {E((c['observed_at'] or '')[:10])} · generated {E(c['generated_at'][:10])}</span>
  {''.join(f'<span>{E(b)} excluded: {x["videos"]} videos, max {x["max_days"]} days</span>' for b, x in c['excluded'].items())}</div>
</div></header><main>"""]

    dom = c["dominant_brand"]
    parts.append(f"""<section><span class="eyebrow"><span class="cell"></span>Read this first</span><div class="card warn"><p>
<b>"Top performing" means "ran the longest".</b> The Ad Library shows no spend or results, so an ad still running after
{c['min_days']}–{c['proven_days']} days is the best public signal a brand keeps paying for it: a proxy, not proof it converts.
Always-on dynamic creative lives longer by design ({pct(t['long']['templated'])} of long-runners use templated copy vs {pct(t['short']['templated'])} of short-lived ads).
{E(dom or '')} supplies {c['proven_by_brand'].get(dom, 0)} of the {c['n_proven']} videos that ran {c['proven_days']}+ days, so each label is shown pooled
and <b>brand-balanced</b> (each brand's longest-running quarter vs that brand's own average).</p></div></section>""")

    parts.append('<section><span class="eyebrow"><span class="cell"></span>Summary</span><h2>What the long-runners have in common</h2><div class="card"><ol class="summary">'
                 + "".join(f"<li><span>{p}</span></li>" for p in summary_points(c) + [E(x) for x in notes.get("summary_extra") or []])
                 + "</ol></div></section>")

    meaning = notes.get("label_meaning") or {}
    rows_html = []
    for d in DIMS:
        vals = c["labels"][d]
        for i, v in enumerate(vals):
            head = f'<td rowspan="{len(vals)}"><b>{E(pretty(d))}</b></td>' if i == 0 else ""
            mean = f'<td rowspan="{len(vals)}">{E(meaning.get(d, ""))}</td>' if i == 0 else ""
            rows_html.append(f"<tr>{head}<td>{E(pretty(v['value']))}</td><td class='n'>{pct(v['all'])}</td><td class='n'>{pct(v['long'])}</td>"
                             f"<td class='n'>{pct(v['proven'])}</td>{_lift(v['balanced_lift'])}{mean}</tr>")
    parts.append(f"""<section><span class="eyebrow"><span class="cell"></span>Label by label</span><h2>Which labels over-index among long-running videos</h2>
<p class="note">Share of competitor videos with each label. Balanced lift = average across brands of (share in the brand's longest-running quarter − share in all its videos), in points.</p>
<div class="card"><div class="scroll"><table><thead><tr><th>Label</th><th>Value</th><th class="n">All</th><th class="n">{c['min_days']}+ d</th><th class="n">{c['proven_days']}+ d</th><th class="n">Balanced lift</th><th>For Nowa</th></tr></thead>
<tbody>{''.join(rows_html)}</tbody></table></div></div></section>""")

    n = c["nowa"]
    parts.append(f"""<section><span class="eyebrow"><span class="cell"></span>Measured</span><h2>The shape of a long-running video vs Nowa's</h2><div class="vs">
<div class="card"><span class="v">{secs(t['long']['median_s'])}</span><span class="k">median length, {c['min_days']}+ days. Nowa: <b>{secs(n['video_median_s'])}</b>.</span></div>
<div class="card"><span class="v">{pct(t['long']['text_at_0'])}</span><span class="k">show the hook as on-screen text in the first second.</span></div>
<div class="card"><span class="v">{pct(1 - t['long']['speech'])}</span><span class="k">have no speech at all.</span></div>
<div class="card"><span class="v">{pct(t['long']['multi_version'])}</span><span class="k">run several versions of the same ad.</span></div>
<div class="card"><span class="v">${(n['video']['cpm'] or 0):.2f}</span><span class="k">Nowa video CPM vs <b>${(n['static']['cpm'] or 0):.2f}</b> static.</span></div></div></section>""")

    cards = []
    for a in c["top"]:
        note = ad_notes.get(lib(a["id"]))
        hk = a["hook"]
        beats = "".join(f'<li><span class="t">{s} s · {k}</span><span>{E(x)}</span></li>' for s, k, x in a["beats"])
        chips = (f'<span class="chip ink">{a["days_running"]} days</span><span class="chip">{secs(a["duration_s"])} · {"voice" if a["speech"] else "silent"}</span>'
                 f'<span class="chip">{E(pretty(a["labels"]["hook_type"]))} · {E(pretty(a["labels"]["pain_point"]))} · {E(pretty(a["labels"]["funnel_stage"]))}</span>'
                 + ("" if note else '<span class="chip amber">new since notes</span>'))
        hook_txt = " → ".join(hk["on_screen"]) or "(no readable on-screen text)"
        extra = ""
        if note:
            extra = "<div class='two'>" + "".join(f'<div class="box {cls}"><b>{lbl}</b>{E(note[k])}</div>' for k, lbl, cls in
                                                  (("copy", "Copy structure", ""), ("steal", "Steal", "steal"), ("avoid", "Careful", "avoid")) if note.get(k)) + "</div>"
        copy = E((a["body"] or "")[:260]) if a["body"] and "{{" not in a["body"] else "<span class='dim'>Templated / dynamic copy</span>"
        cards.append(f"""<article class="card"><div class="td-head"><h3>{E(a['brand_name'])} · {E(note.get('title') if note else (hk['on_screen'][0] if hk['on_screen'] else a['headline'] or 'video'))}</h3><div class="chips">{chips}</div></div>
<div class="hook"><small>Hook, 0–1 s on screen</small>{E(hook_txt)}{f"<small style='margin-top:6px'>Said, 0–3 s</small>{E(hk['said'])}" if hk['said'] else ''}</div>
<ul class="beats">{beats or '<li><span class="t">–</span><span>No readable text or speech extracted.</span></li>'}</ul>
<div class="box"><b>Primary text</b>{copy}</div>{extra}
<a class="note" href="{E(a['source_url'] or '')}" target="_blank" rel="noopener">Ad Library {E(lib(a['id']))}</a></article>""")
    parts.append(f"""<section><span class="eyebrow"><span class="cell"></span>Teardowns</span><h2>The longest-running videos, beat by beat</h2>
<p class="note">Top {c['n_long'] and len(c['top'])} distinct videos ({c['min_days']}+ days, up to a few per brand). Beats are pulled automatically from on-screen text (OCR) and speech (Whisper); brand names in speech are often misheard.
{f"<b>{len(new_since)} ad(s) have no editorial note yet</b> (marked “new since notes”): add them to report/video_notes.yaml." if new_since else ""}</p>
<div class="teardown">{''.join(cards)}</div></section>""")

    tpls = notes.get("templates") or []
    parts.append('<section><span class="eyebrow"><span class="cell"></span>Spine templates</span><h2>Repeatable structures</h2><div class="card"><div class="scroll"><table>'
                 '<thead><tr><th>Template</th><th>Seen in</th><th>Beats</th><th>Hook formula</th><th>Nowa translation</th></tr></thead><tbody>'
                 + "".join(f"<tr><td><b>{E(x['id'])} · {E(x['name'])}</b></td><td>{E(x.get('seen_in', ''))}</td><td>{E(x.get('beats', ''))}</td>"
                           f"<td>{E(x.get('hook_formula', ''))}</td><td>{E(x.get('nowa_note', ''))}</td></tr>" for x in tpls)
                 + "</tbody></table></div></div></section>")

    parts.append(_render_ideas(notes))
    rules = notes.get("translation_rules") or []
    if rules:
        parts.append('<section><span class="eyebrow"><span class="cell"></span>Rules for every idea</span><div class="card"><ul class="rules">'
                     + "".join(f"<li>{E(r)}</li>" for r in rules) + "</ul></div></section>")
    parts.append(f"""<footer><p><b>Method.</b> US competitor videos from the Meta Ad Library, labelled with taxonomy tx-1.0 from each ad's copy, on-screen text
(macOS Vision) and speech (Whisper base.en). Brands with fewer than {MIN_BRAND_VIDEOS} videos or none older than {MIN_BRAND_MAX_DAYS} days are excluded.
Nowa baselines are Meta-reported US lifetime numbers. Notes version {E(str(notes.get('version', '–')))} ({E(str(notes.get('updated', '–')))}).</p>
<p><b>Limits.</b> Longevity is a proxy. OCR and speech contain errors. Competitor content is for internal analysis only and is not reused in Nowa creative.</p></footer></main>""")
    return "\n".join(parts)


def _render_ideas(notes: dict) -> str:
    ideas, segs = notes.get("ideas") or [], notes.get("segments") or {}
    if not ideas:
        return ""
    tpl_names = {x["id"]: x["name"] for x in notes.get("templates") or []}
    filt = ('<div class="filters" role="group" aria-label="Filter ideas"><button class="f on" data-seg="">All</button>'
            + "".join(f'<button class="f" data-seg="{E(k)}">{E(v["label"])}</button>' for k, v in segs.items()) + "</div>")
    blocks = []
    for k, sg in segs.items():
        items = [i for i in ideas if i["segment"] == k]
        cards = []
        for i in items:
            beats = "".join(f'<li><span class="t">{E(str(b[0]))}</span><span>{E(b[1])}</span></li>' for b in i.get("beats") or [])
            cards.append(f"""<article class="card idea" data-seg="{E(k)}"><div class="chips"><span class="chip ink">{E(i['template'])} · {E(tpl_names.get(i['template'], ''))}</span><span class="chip">{E(i['id'])}</span></div>
<h3>{E(i['title'])}</h3><div class="hook"><small>On screen, 0–1 s</small>{E(i['hook'])}{f"<small style='margin-top:6px'>Voice</small>{E(i['vo'])}" if i.get('vo') else ''}</div>
<ul class="beats">{beats}</ul><div class="box"><b>Primary text</b>{E(i['primary_text'])}</div>
<p class="cta-line">Links to <span>{E(i.get('lp') or sg['lp'])}</span></p></article>""")
        blocks.append(f'<div class="segblock" data-seg="{E(k)}"><h3 class="seghead">{E(sg["label"])} <span class="dim">· {len(items)} ideas · {E(sg["lp"])}</span></h3>'
                      f'<p class="note">{E(sg.get("angle", ""))}</p><div class="grid">{"".join(cards)}</div></div>')
    script = """<script>
document.querySelectorAll('.filters .f').forEach(b=>b.addEventListener('click',()=>{
  document.querySelectorAll('.filters .f').forEach(x=>x.classList.toggle('on',x===b));
  document.querySelectorAll('.segblock').forEach(s=>s.hidden=!!b.dataset.seg&&s.dataset.seg!==b.dataset.seg);}));
</script>"""
    return (f'<section><span class="eyebrow"><span class="cell"></span>Nowa idea bank</span><h2>{len(ideas)} video ideas, by segment and template</h2>'
            f'<p class="note">{E(notes.get("ideas_intro", ""))}</p>{filt}{"".join(blocks)}{script}</section>')


def build(conn, out: Path | None = None, min_days: int = 30, per_brand: int = 4, log=print) -> dict:
    notes = yaml.safe_load(NOTES.read_text()) if NOTES.exists() else {}
    c = compute(conn, min_days=min_days, per_brand=per_brand)
    out = out or OUT
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(c, notes))
    (out.with_suffix(".json")).write_text(json.dumps(c, default=str, ensure_ascii=False))
    missing = [a["id"] for a in c["top"] if a["id"].split(":", 1)[-1] not in {str(k) for k in (notes.get("ad_notes") or {})}]
    log(f"  {c['n_videos']} videos · {c['n_long']} ran {min_days}+ days · top set {len(c['top'])} · "
        f"{len(notes.get('ideas') or [])} ideas · {len(missing)} top ads without notes → {out}")
    return {"out": str(out), "top": len(c["top"]), "missing_notes": missing}
