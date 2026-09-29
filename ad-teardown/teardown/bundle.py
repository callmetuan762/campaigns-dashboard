"""EvidenceBundle (spec Step 4): the compact, citable package one ad is judged on.

Each item gets a short local id (e1, e2, …) so the model cites cheaply and accurately. The
bundle keeps an `id_map` back to the stored evidence ids. Truncation is per field and
recorded in `truncated`, so an ad is never silently dropped.

evidence_hash = sha256 of the bundle as sent. Same hash + same prompt/taxonomy/model = the
cached analysis is reused.
"""

from __future__ import annotations

import hashlib
import json

LIMITS = {"ad_copy": 1200, "ad_headline": 200, "ad_description": 300, "ad_cta": 40,
          "ocr_total": 700, "asr_total": 900, "landing_item": 160}
MAX_VARIANTS = 3
LANDING_KINDS = {"title": 1, "h1": 2, "h2": 3, "cta": 3, "price": 6, "term": 4, "date": 4,
                 "scarcity": 4, "trust": 2}


def build(conn, ad: dict) -> dict:
    ev = [dict(r) for r in conn.execute(
        "SELECT id, origin, text_value, locator, confidence FROM evidence WHERE ad_id=? ORDER BY id",
        (ad["id"],))]
    items, id_map, truncated = [], {}, []

    def add(e, text, limit):
        if not text:
            return 0
        if len(text) > limit:
            truncated.append(e["id"])
            text = text[:limit] + "…"
        sid = f"e{len(items) + 1}"
        items.append({"id": sid, "o": e["origin"], "t": text})
        id_map[sid] = e["id"]
        return len(text)

    by_origin: dict[str, list[dict]] = {}
    for e in ev:
        by_origin.setdefault(e["origin"], []).append(e)

    for origin in ("ad_headline", "ad_copy", "ad_description", "ad_cta"):
        rows = by_origin.get(origin, [])
        primary = [e for e in rows if e["id"].endswith(f":{origin}")]
        variants = [e for e in rows if e not in primary][:MAX_VARIANTS]
        for e in primary + variants:
            add(e, e["text_value"], LIMITS[origin] if e in primary else 300)

    for origin, cap in (("ocr", LIMITS["ocr_total"]), ("asr", LIMITS["asr_total"])):
        used = 0
        rows = sorted(by_origin.get(origin, []),
                      key=lambda e: (json.loads(e["locator"] or "{}").get("also_in_copy", False),
                                     -(e["confidence"] or 0)))
        if origin == "asr":
            rows = sorted(by_origin.get(origin, []), key=lambda e: json.loads(e["locator"] or "{}").get("start_ms", 0))
        for e in rows:
            if used >= cap:
                truncated.append(f"{origin}:rest")
                break
            used += add(e, e["text_value"], cap - used)

    counts: dict[str, int] = {}
    landing_meta = None
    for e in by_origin.get("landing_dom", []):
        loc = json.loads(e["locator"] or "{}")
        kind = loc.get("kind", "other")
        landing_meta = landing_meta or {"final_url": loc.get("final_url"), "fetched_at": loc.get("fetched_at")}
        if counts.get(kind, 0) >= LANDING_KINDS.get(kind, 1):
            continue
        counts[kind] = counts.get(kind, 0) + 1
        add(e, e["text_value"], LIMITS["landing_item"])

    snap = conn.execute(
        """SELECT s.status, s.final_url, s.error_code FROM ad_landing_snapshots l
           JOIN landing_snapshots s ON s.id = l.snapshot_id WHERE l.ad_id=?
           ORDER BY s.fetched_at DESC LIMIT 1""", (ad["id"],)).fetchone()
    meta = json.loads(ad["metadata"] or "{}")
    landing_status = (snap["status"] if snap else
                      ("no_landing_page" if not ad["destination_url_canonical"] else "not_fetched"))
    payload = {
        "ad_id": ad["id"],
        "facts": {"brand": ad["brand_id"], "lane": ad["lane"], "format": ad["format"],
                  "cta_button": ad["cta_text"], "destination": ad["destination_url_canonical"],
                  "landing_status": landing_status,
                  "landing_final_url": snap["final_url"] if snap else None,
                  "signals": meta.get("signals")},
        "evidence": items,
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return {"payload": payload, "id_map": id_map, "truncated": truncated, "evidence_hash": digest,
            "landing_status": landing_status, "landing_meta": landing_meta}
