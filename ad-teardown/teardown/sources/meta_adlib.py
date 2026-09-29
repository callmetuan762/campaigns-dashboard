"""Competitor lane: public Meta Ad Library, crawled page by page (no login).

Why the UI and not the API: the Ad Library API has no US commercial ads. Rights decision
(Amy, 2026-09-28): internal analysis only; competitor media is never reused in our creative.

What the Ad Library can and cannot tell us:
- CAN: copy, headline, CTA, landing URL, media, platforms, start date, active flag, and how
  many near-identical versions a brand runs (collation_count).
- CANNOT: spend, impressions, CTR, conversions. Longevity (days running) is the only
  "winner" proxy, and every output labels it as a proxy.

Extraction approach adapted from the brand-funnel-xray skill: Meta server-renders the first
batch of ads as JSON inside the HTML and loads more via /api/graphql on scroll. We read both
and never parse the rendered text. Scrolling is bounded (stall limit) because an unbounded
scroll loop hangs.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime

from .. import db
from ..normalize import canonical_url, clean_text

CONNECTOR_VERSION = "meta_adlib/0.1"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
FORMAT_MAP = {"IMAGE": "static_image", "VIDEO": "video", "CAROUSEL": "carousel",
              "MULTI_IMAGES": "carousel", "DCO": "mixed", "DPA": "mixed", "TEXT": "text"}


def library_url(page_id: str, country: str, active: str = "active") -> str:
    return ("https://www.facebook.com/ads/library/?active_status=" + active +
            f"&ad_type=all&country={country}&is_targeted_country=false&media_type=all"
            f"&search_type=page&view_all_page_id={page_id}")


def _embedded_nodes(html: str) -> list[dict]:
    """Brace-match every JSON object that holds "ad_archive_id" (string-aware)."""
    nodes, i, key = [], 0, '"ad_archive_id"'
    while (k := html.find(key, i)) != -1:
        start = html.rfind("{", 0, k)
        depth, j, in_str, esc = 0, start, False, False
        while j < len(html):
            c = html[j]
            if in_str:
                esc = (not esc) if c == "\\" else False
                if c == '"' and not esc:
                    in_str = False
            elif c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        try:
            nodes.append(json.loads(html[start:j + 1]))
        except ValueError:
            pass
        i = j + 1
    return nodes


def _walk(o, found: list):
    if isinstance(o, dict):
        if "ad_archive_id" in o and "snapshot" in o:
            found.append(o)
        for v in o.values():
            _walk(v, found)
    elif isinstance(o, list):
        for v in o:
            _walk(v, found)


def crawl(page_id: str, country: str = "US", max_scroll: int = 40, stall_limit: int = 6,
          headless: bool = True) -> dict:
    from playwright.sync_api import sync_playwright

    url = library_url(page_id, country)
    captured: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless,
                                    args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(user_agent=UA, locale="en-US",
                                  viewport={"width": 1400, "height": 2400})
        page = ctx.new_page()

        def on_resp(r):
            try:
                if "/api/graphql" in r.url:
                    t = r.text()
                    if "ad_archive_id" in t:
                        captured.append(t)
            except Exception:  # noqa: BLE001, S110 — a failed body read just means one fewer batch
                pass

        page.on("response", on_resp)
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(6000)
        text = page.inner_text("body")
        m = re.search(r"~?\s?[\d,]+\s+results?", text)
        reported = m.group(0).strip() if m else None
        last = stall = 0
        for _ in range(max_scroll):
            page.mouse.wheel(0, 4000)
            page.wait_for_timeout(1300)
            n = page.inner_text("body").count("Library ID")
            stall = stall + 1 if n <= last else 0
            last = max(last, n)
            if stall >= stall_limit:
                break
        html = page.content()
        browser.close()

    nodes = _embedded_nodes(html)
    for t in captured:
        for line in t.splitlines():
            try:
                _walk(json.loads(line), nodes)
            except ValueError:
                pass
    by_id: dict[str, dict] = {}
    for n in nodes:
        aid = str(n.get("ad_archive_id") or "")
        if aid and (aid not in by_id or len(json.dumps(n)) > len(json.dumps(by_id[aid]))):
            by_id[aid] = n
    return {"page_id": page_id, "country": country, "url": url, "reported_results": reported,
            "collected_at": db.now(), "connector_version": CONNECTOR_VERSION,
            "nodes": list(by_id.values())}


def _epoch(v) -> str | None:
    return datetime.fromtimestamp(v, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ") if v else None


def parse_node(n: dict) -> dict:
    snap = n.get("snapshot") or {}
    body = snap.get("body")
    body = body.get("text") if isinstance(body, dict) else body
    cards = snap.get("cards") or []
    media = []
    for im in snap.get("images") or []:
        media.append({"kind": "image",
                      "source_url": im.get("original_image_url") or im.get("resized_image_url")})
    for v in snap.get("videos") or []:
        media.append({"kind": "video", "source_url": v.get("video_hd_url") or v.get("video_sd_url"),
                      "thumbnail_url": v.get("video_preview_image_url")})
    for c in cards:
        if c.get("original_image_url") or c.get("resized_image_url"):
            media.append({"kind": "image",
                          "source_url": c.get("original_image_url") or c.get("resized_image_url")})
        if c.get("video_hd_url") or c.get("video_sd_url"):
            media.append({"kind": "video", "source_url": c.get("video_hd_url") or c.get("video_sd_url"),
                          "thumbnail_url": c.get("video_preview_image_url")})
    link = snap.get("link_url") or next((c.get("link_url") for c in cards if c.get("link_url")), None)
    fmt = FORMAT_MAP.get(snap.get("display_format") or "", "unknown")
    if fmt in ("mixed", "unknown") and media:
        # Dynamic creative (DCO/DPA): Meta rotates the cards as single ads, so the media
        # decide the format. Only a true image+video mix stays "mixed".
        kinds = {m["kind"] for m in media}
        fmt = "video" if kinds == {"video"} else "static_image" if kinds == {"image"} else "mixed"
    return {
        "body": clean_text(body) or clean_text(next((c.get("body") for c in cards if c.get("body")), None)),
        "headline": clean_text(snap.get("title")) or clean_text(
            next((c.get("title") for c in cards if c.get("title")), None)),
        "description": clean_text(snap.get("link_description")),
        "cta_text": snap.get("cta_text"),
        "link": link,
        "format": fmt,
        "media": media,
        "cards": [{"headline": c.get("title"), "body": c.get("body"), "link": c.get("link_url")}
                  for c in cards],
        "start": _epoch(n.get("start_date")),
        "end": _epoch(n.get("end_date")),
        "is_active": n.get("is_active"),
        "platforms": n.get("publisher_platform") or [],
        "versions": n.get("collation_count") or 1,
        "collation_id": n.get("collation_id"),
        "page_name": n.get("page_name") or snap.get("page_name"),
        "page_id": str(n.get("page_id") or snap.get("page_id") or ""),
    }


def ingest(conn, brand: dict, result: dict, run_id: str) -> dict:
    """Store one crawl. Ads whose page_id isn't the confirmed one are rejected, not merged —
    the Ad Library page view can include ads run by partner pages."""
    day = result["collected_at"][:10]
    raw_key = db.save_raw(f"meta_adlib/{brand['id']}/{day}.json", result)
    counters = {"nodes": len(result["nodes"]), "created": 0, "updated": 0, "rejected_page": 0}
    for n in result["nodes"]:
        a = parse_node(n)
        if a["page_id"] and a["page_id"] != str(brand["page_id"]):
            counters["rejected_page"] += 1
            continue
        observed = result["collected_at"]
        days_running = None
        if a["start"]:
            end = a["end"] if (a["end"] and not a["is_active"]) else observed
            days_running = (datetime.fromisoformat(end[:10]) - datetime.fromisoformat(a["start"][:10])).days
        lib_id = str(n["ad_archive_id"])
        ad_id, created = db.upsert_ad(conn, {
            "brand_id": brand["id"], "source": "meta_adlib", "source_ad_id": lib_id,
            "source_url": f"https://www.facebook.com/ads/library/?id={lib_id}",
            "lane": "competitor",
            "active_status": "active" if a["is_active"] else ("inactive" if a["is_active"] is False else "unknown"),
            "headline": a["headline"], "body": a["body"], "description": a["description"],
            "cta_text": a["cta_text"],
            "text_variants": {"cards": a["cards"]},
            "destination_url_original": a["link"],
            "destination_url_canonical": canonical_url(a["link"]),
            "format": a["format"],
            "country": result["country"],
            "source_created_at": a["start"],
            "raw_payload_key": raw_key,
            "creative_group_id": str(a["collation_id"]) if a["collation_id"] else None,
            "metadata": {"platforms": a["platforms"], "versions": a["versions"],
                         "days_running_at_observation": days_running,
                         "longevity_is_proxy": True, "end_date": a["end"],
                         "page_name": a["page_name"], "connector_version": CONNECTOR_VERSION,
                         "run_id": run_id},
        })
        counters["created" if created else "updated"] += 1
        conn.execute("DELETE FROM media_assets WHERE ad_id=? AND processing_status='pending'", (ad_id,))
        for i, m in enumerate(a["media"]):
            conn.execute(
                "INSERT OR IGNORE INTO media_assets (id, ad_id, kind, source_url, rights_status, metadata) "
                "VALUES (?,?,?,?,?,?)",
                (f"{ad_id}:m{i}", ad_id, m["kind"], m.get("source_url"), "internal_analysis_only",
                 db.dumps({"thumbnail_url": m.get("thumbnail_url")})))
    conn.commit()
    return counters
