"""M3: classify every ad with a cheap-first cascade, under a hard dollar cap (spec Step 6–7, §4, §9).

  1. Deterministic first: format (media facts), cta_intent (button text), policy check.
  2. Claude Haiku 4.5, 15 ads per call, text-only evidence bundle.
  3. Escalate to Claude Sonnet 5 when a critical label is < 0.65 confident, the landing
     comparison is a mismatch / high severity / < 0.80 confident, or Haiku's output stays
     invalid after one repair.
  4. needs_review (human) for a high-severity mismatch, a policy conflict, or confidence
     still low after Sonnet. Nothing high-risk is published silently.

Budget: before each call the estimated cost is reserved against the run's cap. If a call
would exceed it, that batch and everything after it becomes `budget_paused`, and what was
completed is kept. Actual cost (from the CLI) replaces the estimate after each call.

Cache: an ad whose evidence_hash + taxonomy + prompt version already has a finished analysis
is skipped, so weekly re-runs only pay for new or changed ads.

Dedup: many ads reuse one creative across ad sets (1,465 ads → 971 distinct bundles). Each
distinct bundle is sent once. Its result is then validated separately against every
duplicate's own evidence ids and stored per ad with metadata.analyzed_as.
"""

from __future__ import annotations

import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor

from . import bundle as B
from . import db, policy, prompts
from .provider import PRICING_VERSION, estimate_cost
from .taxonomy import CRITICAL, TAXONOMY_VERSION, cta_intent, output_schema
from .validate import validate

HAIKU, SONNET = "claude-haiku-4-5", "claude-sonnet-5"
FINAL = ("completed", "needs_review")


class Budget:
    def __init__(self, cap: float):
        self.cap, self.spent, self.reserved = cap, 0.0, 0.0
        self.lock = threading.Lock()

    def reserve(self, est: float) -> bool:
        with self.lock:
            if self.spent + self.reserved + est > self.cap:
                return False
            self.reserved += est
            return True

    def settle(self, est: float, actual: float):
        with self.lock:
            self.reserved -= est
            self.spent += actual


def _needs_escalation(r: dict, lane: str) -> list[str]:
    why = [f"low_conf:{k}" for k in CRITICAL if (r["confidence"].get(k) or 0) < 0.65]
    lc = r["landing_comparison"]
    if lc["status"] == "mismatch" or lc.get("severity") == "high":
        why.append("mismatch")
    elif (lane == "own" and lc["status"] in ("match", "partial")
          and (r["confidence"].get("landing_comparison") or 0) < 0.80):
        # High impact only for our own ads (it's our offer and our spend). A shaky
        # "partial" on a competitor page is not worth a stronger model.
        why.append("low_conf:landing")
    return why


def _call_batch(provider, model, bundles, budget, system, conn_lock, run_id, conn, extra_note=""):
    payloads = [b["payload"] for b in bundles]
    user = prompts.user_prompt(payloads) + extra_note
    est = estimate_cost(model, len(system) + len(user), len(bundles))
    if not budget.reserve(est):
        return None, "budget_paused"
    res = provider.call(model, system, user, output_schema([b["payload"]["ad_id"] for b in bundles]))
    if not res.ok and res.error in ("timeout",):  # one retry for transient failures
        res = provider.call(model, system, user, output_schema([b["payload"]["ad_id"] for b in bundles]))
    budget.settle(est, res.cost_usd)
    with conn_lock:
        conn.execute(
            """INSERT INTO model_calls (id, run_id, ad_id, stage, provider, model_id, prompt_version,
                 input_tokens, output_tokens, cost_usd, latency_ms, status, error_code, created_at, detail)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (db.new_id(), run_id, bundles[0]["payload"]["ad_id"] if len(bundles) == 1 else None,
             "classify", provider.name, res.model_id, prompts.PROMPT_VERSION, res.input_tokens,
             res.output_tokens, res.cost_usd, res.latency_ms, "ok" if res.ok else "error",
             (res.error or "")[:200] or None, db.now(), db.dumps(res.raw) if res.raw else None))
    if not res.ok:
        return None, res.error
    return {r.get("ad_id"): r for r in res.output.get("results") or []}, None


def _finalize(ad: dict, bundle: dict, r: dict, facts: dict) -> tuple[dict, str]:
    """Deterministic fields + policy check + final status."""
    r["labels"]["format"] = ad["format"]
    r["labels"]["cta_intent"] = cta_intent(ad["cta_text"]) or ("other" if ad["cta_text"] else "unknown")
    checks = policy.check(bundle, facts) if ad["lane"] == "own" else []
    r["landing_comparison"]["policy"] = checks
    flags = set(r["review_flags"])
    signals = (bundle["payload"]["facts"].get("signals") or {})
    if signals.get("ocr") in ("unavailable", "none_found") and ad["format"] in ("static_image", "carousel", "video"):
        flags.add("visual_not_analyzed")
    status = "completed"
    lc = r["landing_comparison"]
    if lc["status"] == "mismatch" and lc.get("severity") == "high":
        status = "needs_review"
    if any(c["verdict"] == "conflict" for c in checks):
        status = "needs_review"
        flags.add("policy_sensitive_claim")
    r["review_flags"] = sorted(flags)
    return r, status


def _content_key(bundle: dict) -> str:
    p = dict(bundle["payload"])
    p.pop("ad_id")
    p["evidence"] = [{"o": e["o"], "t": e["t"]} for e in p["evidence"]]
    return hashlib.sha256(json.dumps(p, sort_keys=True).encode()).hexdigest()


def _store(conn, run_id, ad, bundle, r, model_id, status, analyzed_as=None):
    conn.execute(
        """INSERT OR REPLACE INTO analyses (id, run_id, ad_id, taxonomy_version, prompt_version, model_id,
             evidence_hash, status, labels, offer_detail, claims, landing_comparison, confidence,
             review_flags, model_notes, created_at, metadata) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (db.new_id(), run_id, ad["id"], TAXONOMY_VERSION, prompts.PROMPT_VERSION, model_id,
         bundle["evidence_hash"], status, db.dumps(r["labels"]), db.dumps(r.get("offer_detail")),
         db.dumps(r.get("claims")), db.dumps(r.get("landing_comparison")), db.dumps(r.get("confidence")),
         db.dumps(r.get("review_flags")), r.get("model_notes")[:240] if r.get("model_notes") else None,
         db.now(), db.dumps({"analyzed_as": analyzed_as}) if analyzed_as else None))


def run(conn, provider, brand_ids=None, lane=None, budget_usd=10.0, limit=None, batch_size=15,
        escalate_batch=8, workers=4, dry_run=False, force=False, campaign=None, log=print) -> dict:
    q = "SELECT * FROM ads WHERE 1=1"
    params: list = []
    if campaign:
        q += " AND campaign_name LIKE ?"
        params.append(f"%{campaign}%")
    if brand_ids:
        q += f" AND brand_id IN ({','.join('?' * len(brand_ids))})"
        params += brand_ids
    if lane:
        q += " AND lane=?"
        params.append(lane)
    q += " ORDER BY lane DESC, brand_id, id"  # own lane first: if the cap hits, our ads are done
    ads = [dict(r) for r in conn.execute(q, params)]
    if limit:
        ads = ads[:limit]
    facts = policy.load_facts()
    system = prompts.system_prompt()

    bundles, cached = {}, 0
    for a in ads:
        b = B.build(conn, a)
        hit = None if force else conn.execute(
            """SELECT 1 FROM analyses WHERE ad_id=? AND evidence_hash=? AND taxonomy_version=? AND
               prompt_version=? AND status IN ('completed','needs_review')""",
            (a["id"], b["evidence_hash"], TAXONOMY_VERSION, prompts.PROMPT_VERSION)).fetchone()
        if hit:
            cached += 1
        else:
            bundles[a["id"]] = b
    todo = [a for a in ads if a["id"] in bundles]
    groups: dict[str, list[str]] = {}   # representative ad id -> every ad sharing that bundle
    rep_of: dict[str, str] = {}
    for a in todo:
        k = _content_key(bundles[a["id"]])
        rep_of.setdefault(k, a["id"])
        groups.setdefault(rep_of[k], []).append(a["id"])
    reps = [a for a in todo if a["id"] in groups]
    batches = [reps[i:i + batch_size] for i in range(0, len(reps), batch_size)]
    est_total = sum(estimate_cost(HAIKU, len(system) + len(prompts.user_prompt([bundles[a["id"]]["payload"] for a in bt])),
                                  len(bt)) for bt in batches)
    log(f"  {len(ads)} ads · {cached} cached · {len(todo)} to analyze ({len(reps)} distinct) in "
        f"{len(batches)} Haiku calls · "
        f"est. Haiku ${est_total:.2f} (+ Sonnet for escalations) · cap ${budget_usd:.2f}")
    if dry_run:
        return {"ads": len(ads), "cached": cached, "to_analyze": len(todo), "distinct": len(reps),
                "haiku_calls": len(batches),
                "est_haiku_usd": round(est_total, 2)}

    run_id = db.create_run(conn, {"stage": "analyze", "brand_ids": brand_ids, "lane": lane,
                                  "budget_usd": budget_usd, "models": [HAIKU, SONNET],
                                  "pricing_version": PRICING_VERSION, "operator": "cli"})
    conn.execute("UPDATE runs SET status='processing' WHERE id=?", (run_id,))
    for a in ads:
        conn.execute("INSERT OR IGNORE INTO run_ads (run_id, ad_id, state) VALUES (?,?,?)",
                     (run_id, a["id"], "queued" if a["id"] in bundles else "cached"))
    conn.commit()

    try:
        return _run_stages(conn, provider, run_id, ads, bundles, cached, batches, groups, facts,
                           system, budget_usd, escalate_batch, workers, log)
    except BaseException as e:
        # Don't leave a run stuck in 'processing'. Finished analyses are already committed
        # and will come back from the cache on the next run.
        conn.rollback()
        db.finish_run(conn, run_id, "failed", {"error": f"{type(e).__name__}: {e}"[:300]})
        conn.commit()
        raise


def _run_stages(conn, provider, run_id, ads, bundles, cached, batches, groups, facts, system,
                budget_usd, escalate_batch, workers, log) -> dict:
    budget, lock = Budget(budget_usd), threading.Lock()
    by_id = {a["id"]: a for a in ads}
    state: dict[str, str] = {}
    escalate: list[tuple[str, list[str]]] = []
    repair: list[tuple[str, list[str], str]] = []

    def handle(model, results, rep_ids, allow_repair=True):
        for aid in rep_ids:
            r = results.get(aid) if results else None
            clean, errs = (validate(r, bundles[aid]) if r else (None, ["missing from output"]))
            if not clean:
                if allow_repair:
                    repair.append((aid, errs, model))
                else:
                    for m in groups[aid]:
                        state[m] = "analysis_failed"
                    if model == HAIKU:
                        escalate.append((aid, ["invalid_after_repair"]))
                continue
            why = _needs_escalation(clean, by_id[aid]["lane"]) if model == HAIKU else []
            still_low = model == SONNET and any((clean["confidence"].get(k) or 0) < 0.65 for k in CRITICAL)
            for m in groups[aid]:
                mine, _errs = (clean, []) if m == aid else validate(r, bundles[m])
                if not mine:
                    state[m] = "analysis_failed"
                    continue
                final, status = _finalize(by_id[m], bundles[m], mine, facts)
                if still_low and status == "completed":
                    status = "needs_review"
                with lock:
                    _store(conn, run_id, by_id[m], bundles[m], final, model,
                           "escalated" if why else status, analyzed_as=aid if m != aid else None)
                state[m] = "escalated" if why else status
            if why:
                escalate.append((aid, why))

    def stage(model, call_groups, note_fn=None):
        paused = []

        def one(group):
            ids = [a["id"] for a in group]
            results, err = _call_batch(provider, model, [bundles[i] for i in ids], budget, system,
                                       lock, run_id, conn, note_fn(group) if note_fn else "")
            return ids, results, err

        with ThreadPoolExecutor(max_workers=workers) as pool:
            for n, (ids, results, err) in enumerate(pool.map(one, call_groups), start=1):
                if err == "budget_paused":
                    paused += ids
                    continue
                if err:
                    log(f"    call failed ({err[:80]}) for {len(ids)} ads")
                handle(model, results, ids, allow_repair=note_fn is None)
                with lock:
                    conn.commit()
                if n % 10 == 0:
                    log(f"    {model}: {n}/{len(call_groups)} calls · spent ${budget.spent:.2f}")
        for i in paused:
            for m in groups[i]:
                state[m] = "budget_paused"
        return paused

    log(f"  stage 1 · {HAIKU}")
    stage(HAIKU, batches)

    if repair:
        log(f"  repair · {len(repair)} invalid results, one retry each")
        items = list(repair)
        repair.clear()
        for model in (HAIKU, SONNET):
            group = [(aid, errs) for aid, errs, m in items if m == model]
            if group:
                notes = {aid: errs for aid, errs in group}
                stage(model, [[by_id[aid]] for aid, _ in group],
                      note_fn=lambda g, notes=notes: (
                          "\nYour previous output for this ad failed validation: "
                          + "; ".join(notes[g[0]["id"]]) + ". Return a corrected result."))
        for aid, _errs, _m in repair:  # repairs that failed validation again
            state[aid] = "analysis_failed"

    if escalate:
        ids = list(dict.fromkeys(aid for aid, _ in escalate))
        log(f"  stage 2 · {SONNET} · {len(ids)} escalated ads")
        stage(SONNET, [[by_id[i] for i in ids[k:k + escalate_batch]] for k in range(0, len(ids), escalate_batch)])

    # Escalations that didn't finish (Sonnet invalid twice, or the cap hit mid-stage-2) fall back
    # to the Haiku result, marked for a human, so the ad is never silently missing.
    sonnet_invalid = {aid for aid, _e, m in repair if m == SONNET}
    for aid in bundles:
        if state.get(aid) in ("escalated", "budget_paused") or aid in sonnet_invalid:
            row = conn.execute("SELECT id, review_flags FROM analyses WHERE run_id=? AND ad_id=? AND "
                               "status='escalated'", (run_id, aid)).fetchone()
            if row:
                flags = sorted(set(json.loads(row["review_flags"] or "[]")) | {"low_confidence"})
                conn.execute("UPDATE analyses SET status='needs_review', review_flags=? WHERE id=?",
                             (db.dumps(flags), row["id"]))
                state[aid] = "needs_review"
            elif aid in sonnet_invalid:
                state[aid] = "analysis_failed"

    counts: dict[str, int] = {}
    for aid in bundles:
        s = state.get(aid, "analysis_failed")
        counts[s] = counts.get(s, 0) + 1
        conn.execute("UPDATE run_ads SET state=? WHERE run_id=? AND ad_id=?", (s, run_id, aid))
    counts["cached"] = cached
    counts["escalated_total"] = len({aid for aid, _ in escalate})
    counts["spent_usd"] = round(budget.spent, 4)
    status = "partial" if counts.get("budget_paused") or counts.get("analysis_failed") else "completed"
    conn.execute("UPDATE runs SET spent_usd=? WHERE id=?", (budget.spent, run_id))
    db.finish_run(conn, run_id, status, counts)
    conn.commit()
    counts["run_id"] = run_id
    return counts


def repolicy(conn, log=print) -> dict:
    """Re-run the deterministic policy check on stored analyses after offer_facts.yaml changes.
    No model calls. Status is recomputed from the stored model verdict + the new policy result."""
    facts = policy.load_facts()
    counts: dict[str, int] = {}
    rows = conn.execute(
        """SELECT an.id, an.ad_id, an.status, an.landing_comparison, an.review_flags, a.lane
           FROM analyses an JOIN ads a ON a.id = an.ad_id
           WHERE an.status IN ('completed','needs_review') AND an.prompt_version=? AND an.taxonomy_version=?""",
        (prompts.PROMPT_VERSION, TAXONOMY_VERSION)).fetchall()
    for r in rows:
        if r["lane"] != "own":
            continue
        ad = dict(conn.execute("SELECT * FROM ads WHERE id=?", (r["ad_id"],)).fetchone())
        checks = policy.check(B.build(conn, ad), facts)
        lc = json.loads(r["landing_comparison"])
        lc["policy"] = checks
        flags = set(json.loads(r["review_flags"] or "[]")) - {"policy_sensitive_claim"}
        if any(c["verdict"] in ("conflict", "risk") for c in checks):
            flags.add("policy_sensitive_claim")
        needs = ((lc.get("status") == "mismatch" and lc.get("severity") == "high")
                 or "policy_sensitive_claim" in flags or "low_confidence" in flags)
        status = "needs_review" if needs else "completed"
        conn.execute("UPDATE analyses SET landing_comparison=?, review_flags=?, status=? WHERE id=?",
                     (db.dumps(lc), db.dumps(sorted(flags)), status, r["id"]))
        key = f"{r['status']}->{status}"
        counts[key] = counts.get(key, 0) + 1
    conn.commit()
    log(f"  re-checked {sum(counts.values())} own-ad analyses against offer_facts.yaml")
    return counts
