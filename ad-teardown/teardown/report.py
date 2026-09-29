"""M4: one row per ad → CSV/JSON export + dashboard (spec Step 8, §8).

Single source of truth: `rows()` builds every ad's row from the database. The export writes
those rows, and the dashboard computes every chart from the same embedded rows, so export
counts equal dashboard counts by construction. `aggregate()` is the Python twin of the
page's JavaScript and is what the tests reconcile against.

Rules kept from the spec:
- Latest finished analysis per ad (completed / needs_review) under the current prompt and
  taxonomy. Ads without one are counted as "analysis pending", never dropped.
- `unknown` stays in every denominator.
- Observed facts (format, CTA, destination, spend) are kept apart from inferred labels.
- Competitor ads carry no performance numbers. Days running is a proxy, labelled as such.
- Own-lane performance is US-only (insights broken down by country) and Meta-reported.
  Meta purchases are not Shopify orders.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from . import db, policy, prompts
from .taxonomy import LABELS, TAXONOMY_VERSION

REPORT_DIR = db.DATA_DIR / "report"
TEMPLATE = Path(__file__).resolve().parent.parent / "report" / "dashboard.template.html"
DIMENSIONS = ["format", "hook_type", "offer_type", "cta_intent", "awareness_stage", "driver",
              "funnel_stage", "pain_point", "proof_type"]
DIRECTIONAL_SPEND, DIRECTIONAL_IMPR = 50.0, 1000
LONG_RUNNING_DAYS = 60


def _q(ev: dict, ids: list[str], limit=220) -> list[dict]:
    out = []
    for i in ids or []:
        e = ev.get(i)
        if e:
            out.append({"id": i.split(":", 2)[-1], "origin": e["origin"], "text": (e["text_value"] or "")[:limit]})
    return out


def rows(conn) -> list[dict]:
    latest = {}
    for a in conn.execute(
            """SELECT * FROM analyses WHERE status IN ('completed','needs_review')
               AND prompt_version=? AND taxonomy_version=? ORDER BY created_at""",
            (prompts.PROMPT_VERSION, TAXONOMY_VERSION)):
        latest[a["ad_id"]] = dict(a)  # later rows win (e.g. Sonnet over Haiku)
    metrics = {}
    for m in conn.execute("SELECT * FROM ad_metrics WHERE country='US' AND window IN ('lifetime','last_30d')"):
        metrics.setdefault(m["ad_id"], {})[m["window"]] = dict(m)
    snaps = {r["ad_id"]: dict(r) for r in conn.execute(
        """SELECT l.ad_id, s.status, s.final_url, s.fetched_at FROM ad_landing_snapshots l
           JOIN landing_snapshots s ON s.id=l.snapshot_id ORDER BY s.fetched_at""")}
    brands = {b["id"]: b["name"] for b in conn.execute("SELECT id, name FROM brands")}

    out = []
    for ad in conn.execute("SELECT * FROM ads ORDER BY lane DESC, brand_id, campaign_name, ad_name"):
        ad = dict(ad)
        meta = json.loads(ad["metadata"] or "{}")
        an = latest.get(ad["id"])
        ev = {}
        if an:
            ev = {e["id"]: dict(e) for e in conn.execute(
                "SELECT id, origin, text_value FROM evidence WHERE ad_id=?", (ad["id"],))}
        labels = json.loads(an["labels"]) if an else {}
        lc = json.loads(an["landing_comparison"]) if an else {}
        snap = snaps.get(ad["id"]) or {}
        row = {
            "id": ad["id"], "brand": ad["brand_id"], "brand_name": brands.get(ad["brand_id"], ad["brand_id"]),
            "lane": ad["lane"], "source_url": ad["source_url"],
            "campaign": ad["campaign_name"], "adset": ad["adset_name"], "ad_name": ad["ad_name"],
            "code": ad["ad_code"], "active": ad["active_status"],
            "started": (ad["source_created_at"] or "")[:10] or None,
            "headline": ad["headline"], "body": (ad["body"] or "")[:400] or None, "cta": ad["cta_text"],
            "destination": ad["destination_url_canonical"],
            "landing_status": snap.get("status") or ("no_landing_page" if not ad["destination_url_canonical"] else "not_fetched"),
            "landing_final_url": snap.get("final_url"), "landing_fetched_at": snap.get("fetched_at"),
            "days_running": meta.get("days_running_at_observation"), "versions": meta.get("versions"),
            "platforms": meta.get("platforms"), "signals": meta.get("signals"),
            "observed_at": ad["last_seen_at"],
            "analysis": an["status"] if an else "pending",
            "model": an["model_id"] if an else None,
            "analyzed_as": (json.loads(an["metadata"] or "{}") or {}).get("analyzed_as") if an else None,
            "labels": {d: (ad["format"] if d == "format" else labels.get(d, "unknown" if an else None))
                       for d in DIMENSIONS},
            "confidence": json.loads(an["confidence"] or "{}") if an else {},
            "offer": json.loads(an["offer_detail"] or "{}") if an else {},
            "compare": {"status": lc.get("status"), "severity": lc.get("severity"), "reason": lc.get("reason"),
                        "ad_quotes": _q(ev, lc.get("ad_evidence_ids")),
                        "page_quotes": _q(ev, lc.get("page_evidence_ids"))} if an else None,
            "policy": [{"fact": c["fact"], "ad_value": c["ad_value"], "verdict": c["verdict"],
                        "policy_value": c.get("policy_value"), "quote": (_q(ev, [c["evidence_id"]]) or [{}])[0].get("text")}
                       for c in lc.get("policy") or []],
            "flags": json.loads(an["review_flags"] or "[]") if an else [],
            "notes": an["model_notes"] if an else None,
        }
        if ad["lane"] == "own":
            ev_all = [{"id": e["id"], "origin": e["origin"], "text": e["text_value"]}
                      for e in conn.execute("SELECT id, origin, text_value FROM evidence WHERE ad_id=?", (ad["id"],))]
            rule = policy.price_rule(ev_all)
            if rule.get("ad_evidence"):
                texts = {e["id"]: e["text"] for e in ev_all}
                rule["ad_quote"] = (texts.get(rule["ad_evidence"]) or "")[:220]
                rule["page_quote"] = (texts.get(rule["page_evidence"]) or "")[:220]
            row["price_rule"] = rule
            for win, key in (("lifetime", "perf"), ("last_30d", "perf_30d")):
                m = (metrics.get(ad["id"]) or {}).get(win)
                row[key] = None if not m else {
                    "spend": round(m["spend"] or 0, 2), "impressions": m["impressions"] or 0,
                    "link_clicks": m["link_clicks"] or 0, "lpv": m["meta_landing_page_views"] or 0,
                    "atc": m["meta_add_to_cart"] or 0, "ic": m["meta_initiate_checkout"] or 0,
                    "purchases": m["meta_purchases"] or 0, "leads": m["meta_leads"] or 0,
                    "date_start": m["date_start"], "date_stop": m["date_stop"]}
        out.append(row)
    return out


def aggregate(rs: list[dict]) -> dict:
    """Counts by brand × dimension value, unknown included; mismatch rate per brand."""
    agg: dict = {"n_ads": len(rs), "by_brand": {}, "dims": {}, "compare": {}}
    for r in rs:
        agg["by_brand"][r["brand"]] = agg["by_brand"].get(r["brand"], 0) + 1
    analyzed = [r for r in rs if r["analysis"] != "pending"]
    agg["n_analyzed"] = len(analyzed)
    for d in DIMENSIONS:
        cell: dict = {}
        for r in analyzed:
            v = r["labels"].get(d) or "unknown"
            cell.setdefault(r["brand"], {}).setdefault(v, 0)
            cell[r["brand"]][v] += 1
        agg["dims"][d] = cell
    for r in analyzed:
        c = agg["compare"].setdefault(r["brand"], {})
        s = (r["compare"] or {}).get("status") or "unknown"
        c[s] = c.get(s, 0) + 1
    agg["price_rule"] = {}
    for r in rs:
        if r.get("price_rule"):
            v = r["price_rule"]["verdict"]
            agg["price_rule"][v] = agg["price_rule"].get(v, 0) + 1
    for c in agg["compare"].values():
        verifiable = sum(c.get(s, 0) for s in ("match", "partial", "mismatch"))
        c["mismatch_rate_of_verifiable"] = round(c.get("mismatch", 0) / verifiable, 4) if verifiable else None
    return agg


def build(conn, out_dir: Path | None = None, log=print) -> dict:
    out_dir = out_dir or REPORT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    rs = rows(conn)
    agg = aggregate(rs)
    runs = [dict(r) for r in conn.execute(
        "SELECT id, status, created_at, spent_usd FROM runs WHERE request LIKE '%analyze%' ORDER BY created_at")]
    provenance = {
        "generated_at": db.now(), "taxonomy_version": TAXONOMY_VERSION, "prompt_version": prompts.PROMPT_VERSION,
        "labels": LABELS, "dimensions": DIMENSIONS, "analysis_runs": runs,
        "model_cost_usd": round(conn.execute("SELECT COALESCE(SUM(cost_usd),0) FROM model_calls").fetchone()[0], 2),
        "observed_from": min((r["observed_at"] for r in rs), default=None),
        "observed_to": max((r["observed_at"] for r in rs), default=None),
        "country": "US", "directional_spend": DIRECTIONAL_SPEND, "directional_impressions": DIRECTIONAL_IMPR,
        "long_running_days": LONG_RUNNING_DAYS,
        "notes": ["Competitor ads have no performance data; days running is a proxy for what a brand keeps paying for.",
                  "Own-ad performance is US-only and Meta-reported. Meta purchases are not Shopify orders.",
                  "Labels are model inferences from the ad's own evidence, not platform facts."],
    }
    payload = {"provenance": provenance, "aggregates": agg, "rows": rs}
    (out_dir / "report.json").write_text(json.dumps(payload, ensure_ascii=False, default=str))
    with (out_dir / "ads.csv").open("w", newline="") as f:
        w = csv.writer(f)
        head = ["id", "brand", "lane", "campaign", "ad_name", "code", "active", "started", "format", *DIMENSIONS[1:],
                "compare_status", "compare_severity", "compare_reason", "price_rule", "analysis", "model", "flags",
                "spend_us_lifetime", "impressions", "link_clicks", "lpv", "atc", "purchases_meta", "leads_meta",
                "days_running_proxy", "destination", "landing_final_url", "source_url"]
        w.writerow(head)
        for r in rs:
            p = r.get("perf") or {}
            c = r["compare"] or {}
            w.writerow([r["id"], r["brand"], r["lane"], r["campaign"], r["ad_name"], r["code"], r["active"],
                        r["started"], r["labels"]["format"], *[r["labels"].get(d) for d in DIMENSIONS[1:]],
                        c.get("status"), c.get("severity"), c.get("reason"),
                        (r.get("price_rule") or {}).get("verdict"), r["analysis"], r["model"],
                        "|".join(r["flags"]), p.get("spend"), p.get("impressions"), p.get("link_clicks"),
                        p.get("lpv"), p.get("atc"), p.get("purchases"), p.get("leads"), r["days_running"],
                        r["destination"], r["landing_final_url"], r["source_url"]])
    html = TEMPLATE.read_text().replace("/*__DATA__*/null", json.dumps(payload, ensure_ascii=False, default=str)
                                        .replace("</", "<\\/"))
    (out_dir / "dashboard.html").write_text(html)
    log(f"  {len(rs)} rows ({agg['n_analyzed']} analyzed) → {out_dir}/report.json, ads.csv, dashboard.html "
        f"({(out_dir / 'dashboard.html').stat().st_size / 1e6:.1f} MB)")
    return {"rows": len(rs), "analyzed": agg["n_analyzed"], "dir": str(out_dir)}
