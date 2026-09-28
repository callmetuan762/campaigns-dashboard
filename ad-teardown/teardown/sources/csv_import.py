"""Manual lane: CSV or JSON import (spec Step 2.1 — the dependable fallback path).

Use it for ads spotted by hand (TikTok Creative Center, a screenshot someone sent, an
Ads Manager export). Every row is validated; rejected rows go to a `.rejected.csv` next to
the database with the reason, so nothing disappears silently.

Columns (header names, case-insensitive):
  required: source, source_ad_id, brand, and headline or body
  optional: source_url, lane, cta_text, destination_url, status, start_date, captured_at,
            format, media_urls (separated by |), country
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from .. import db
from ..normalize import canonical_url, clean_text

FORMATS = {"static_image", "carousel", "video", "text", "mixed", "unknown"}


def _rows(path: Path) -> list[dict]:
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text())
        return data if isinstance(data, list) else data.get("ads", [])
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def validate(row: dict, brands: dict[str, str]) -> tuple[dict | None, str | None]:
    r = {str(k).strip().lower(): (v.strip() if isinstance(v, str) else v) for k, v in row.items()}
    for key in ("source", "source_ad_id", "brand"):
        if not r.get(key):
            return None, f"missing {key}"
    if r["brand"] not in brands:
        return None, f"unknown brand '{r['brand']}' (add it to config/brands.yaml)"
    if r["source"] in ("meta_own", "meta_adlib"):
        return None, "source name reserved for the API/crawler lanes — use e.g. 'manual' or 'tiktok_cc'"
    if not (r.get("headline") or r.get("body")):
        return None, "needs headline or body"
    fmt = (r.get("format") or "unknown").lower()
    if fmt not in FORMATS:
        return None, f"format '{fmt}' not in {sorted(FORMATS)}"
    url = r.get("destination_url")
    if url and not canonical_url(url):
        return None, f"destination_url is not http(s): {url[:60]}"
    return r, None


def import_file(conn, path: Path | str, run_id: str) -> dict:
    path = Path(path)
    brands = {b["id"]: b["kind"] for b in conn.execute("SELECT id, kind FROM brands")}
    rows = _rows(path)
    raw_key = db.save_raw(f"imports/{path.stem}-{db.now()[:10]}.json", rows)
    counters, rejected = {"rows": len(rows), "created": 0, "updated": 0, "rejected": 0}, []
    for i, row in enumerate(rows, start=2):  # row 1 is the header
        r, err = validate(row, brands)
        if err:
            rejected.append({"row": i, "reason": err, **row})
            continue
        lane = r.get("lane") or ("own" if brands[r["brand"]] == "own" else "competitor")
        ad_id, created = db.upsert_ad(conn, {
            "brand_id": r["brand"], "source": r["source"], "source_ad_id": r["source_ad_id"],
            "source_url": r.get("source_url"), "lane": lane,
            "active_status": (r.get("status") or "unknown").lower(),
            "headline": clean_text(r.get("headline")), "body": clean_text(r.get("body")),
            "cta_text": clean_text(r.get("cta_text")),
            "destination_url_original": r.get("destination_url"),
            "destination_url_canonical": canonical_url(r.get("destination_url")),
            "format": (r.get("format") or "unknown").lower(),
            "country": r.get("country") or "US",
            "source_created_at": r.get("start_date"),
            "raw_payload_key": raw_key,
            "metadata": {"captured_at": r.get("captured_at"), "import_file": path.name,
                         "run_id": run_id},
        })
        counters["created" if created else "updated"] += 1
        for j, u in enumerate(x for x in (r.get("media_urls") or "").split("|") if x.strip()):
            conn.execute(
                "INSERT OR IGNORE INTO media_assets (id, ad_id, kind, source_url, rights_status) "
                "VALUES (?,?,?,?,?)",
                (f"{ad_id}:m{j}", ad_id, "video" if u.lower().endswith((".mp4", ".mov")) else "image",
                 u.strip(), "internal_analysis_only"))
    conn.commit()
    counters["rejected"] = len(rejected)
    if rejected:
        out = db.DATA_DIR / "imports" / f"{path.stem}.rejected.csv"
        out.parent.mkdir(parents=True, exist_ok=True)
        keys = list(dict.fromkeys(k for r in rejected for k in r))
        with out.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            w.writerows(rejected)
        counters["rejected_file"] = str(out)
    return counters
