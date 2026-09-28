"""Own-lane source: our ads + real performance from the Meta Marketing API (read-only).

Why not the Ad Library API: it only covers political ads and ads delivered to the EU/UK.
Our ads are US commercial ads, so the Marketing API is the only route that also gives spend,
CTR and conversions.

Gotchas carried over from earlier analysis (don't "simplify" these away):
- The `effective_status` *filter parameter* returns a wrong, larger set (includes ads whose
  parent is paused). We list everything and read the returned field instead.
- Asking for the full creative field set on the paginated /ads call 500s. We list ids first,
  then fetch creative detail in chunks of 25 via /?ids=.
- The ad-set name is not the destination. The real link comes from the creative.
- Pawcast shares this ad account. Brand scoping is by campaign-name prefix, always.
"""

from __future__ import annotations

import json
import os
import random
import re
import time
from pathlib import Path

import requests

from .. import db
from ..normalize import (
    ad_code_from_name,
    canonical_url,
    clean_text,
    format_from_name,
    with_url_tags,
)

GRAPH = "https://graph.facebook.com/v24.0"
CONNECTOR_VERSION = "meta_own/0.1"

CREATIVE_FIELDS = (
    "creative{id,name,title,body,object_type,object_story_spec,asset_feed_spec,image_url,"
    "image_hash,thumbnail_url,video_id,url_tags,call_to_action_type,effective_object_story_id,"
    "link_url,instagram_permalink_url}"
)
INSIGHT_FIELDS = (
    "ad_id,ad_name,campaign_name,spend,impressions,reach,clicks,ctr,cpm,cpc,frequency,"
    "actions,date_start,date_stop"
)
WINDOWS = {"lifetime": "maximum", "last_30d": "last_30d", "last_7d": "last_7d"}


class GraphError(RuntimeError):
    pass


def load_token() -> str:
    """Env var first; else read the export line from ~/.zshrc (tools don't source it).
    The value is never printed or logged."""
    token = os.environ.get("META_ACCESS_TOKEN")
    if token:
        return token
    zshrc = Path.home() / ".zshrc"
    if zshrc.exists():
        m = re.search(r'^export META_ACCESS_TOKEN="?([^"\n]+)"?', zshrc.read_text(), re.MULTILINE)
        if m:
            return m.group(1)
    raise GraphError("META_ACCESS_TOKEN not found (env or ~/.zshrc)")


class Graph:
    def __init__(self, token: str | None = None, session: requests.Session | None = None):
        self.token = token or load_token()
        self.s = session or requests.Session()
        self.calls = 0

    def get(self, path: str, params: dict | None = None) -> dict:
        url = path if path.startswith("http") else f"{GRAPH}/{path.lstrip('/')}"
        params = dict(params or {})
        if "access_token=" not in url:
            params["access_token"] = self.token
        for attempt in range(4):
            self.calls += 1
            try:
                r = self.s.get(url, params=params, timeout=60)
            except requests.RequestException as e:
                err = f"network: {type(e).__name__}"
            else:
                if r.ok:
                    return r.json()
                body = _safe_json(r)
                e = body.get("error", {})
                code = e.get("code")
                err = f"{r.status_code} code={code} {e.get('message', '')[:200]}"
                # Auth / permission / bad request: retrying won't help.
                if r.status_code in (400, 401, 403) and code not in (4, 17, 32, 613, 80004):
                    raise GraphError(err)
                retry_after = r.headers.get("Retry-After")
                if retry_after and retry_after.isdigit():
                    time.sleep(min(int(retry_after), 120))
                    continue
            time.sleep(min(2 ** attempt * 2, 60) + random.uniform(0, 1.5))
        raise GraphError(err)

    def paginate(self, path: str, params: dict) -> list[dict]:
        out, data = [], self.get(path, params)
        while True:
            out.extend(data.get("data", []))
            nxt = data.get("paging", {}).get("next")
            if not nxt:
                return out
            data = self.get(nxt)


def _safe_json(r) -> dict:
    try:
        return r.json()
    except ValueError:
        return {}


def _cta_label(cta_type: str | None) -> str | None:
    return cta_type.replace("_", " ").title() if cta_type else None


def parse_creative(creative: dict) -> dict:
    """Flatten a Meta creative into ad fields + media refs. Handles the three shapes we use:
    link_data (static / carousel), video_data, and asset_feed_spec (dynamic creative)."""
    oss = creative.get("object_story_spec") or {}
    feed = creative.get("asset_feed_spec") or {}
    link = oss.get("link_data") or {}
    video = oss.get("video_data") or {}

    bodies, titles, descs, links, media, cards = [], [], [], [], [], []
    cta_type = creative.get("call_to_action_type")

    if link:
        bodies.append(link.get("message"))
        titles.append(link.get("name"))
        descs.append(link.get("description"))
        links.append(link.get("link") or (link.get("call_to_action") or {}).get("value", {}).get("link"))
        cta_type = (link.get("call_to_action") or {}).get("type") or cta_type
        for c in link.get("child_attachments") or []:
            cards.append({"headline": c.get("name"), "description": c.get("description"),
                          "link": c.get("link")})
            if c.get("image_hash") or c.get("picture"):
                media.append({"kind": "image", "source_ref": c.get("image_hash"),
                              "source_url": c.get("picture")})
            if c.get("video_id"):
                media.append({"kind": "video", "source_ref": c.get("video_id")})
        if not cards and (link.get("image_hash") or link.get("picture")):
            media.append({"kind": "image", "source_ref": link.get("image_hash"),
                          "source_url": link.get("picture")})
    if video:
        bodies.append(video.get("message"))
        titles.append(video.get("title"))
        descs.append(video.get("link_description"))
        cta = video.get("call_to_action") or {}
        links.append(cta.get("value", {}).get("link"))
        cta_type = cta.get("type") or cta_type
        media.append({"kind": "video", "source_ref": video.get("video_id"),
                      "thumbnail_url": video.get("image_url")})
    if feed:
        bodies += [b.get("text") for b in feed.get("bodies") or []]
        titles += [t.get("text") for t in feed.get("titles") or []]
        descs += [d.get("text") for d in feed.get("descriptions") or []]
        links += [u.get("website_url") for u in feed.get("link_urls") or []]
        cta_type = (feed.get("call_to_action_types") or [cta_type])[0]
        for im in feed.get("images") or []:
            media.append({"kind": "image", "source_ref": im.get("hash"), "source_url": im.get("url")})
        for v in feed.get("videos") or []:
            media.append({"kind": "video", "source_ref": v.get("video_id"),
                          "thumbnail_url": v.get("thumbnail_url")})

    bodies.append(creative.get("body"))
    titles.append(creative.get("title"))
    links.append(creative.get("link_url"))
    if not media and creative.get("video_id"):
        media.append({"kind": "video", "source_ref": creative["video_id"],
                      "thumbnail_url": creative.get("thumbnail_url")})
    if not media and (creative.get("image_hash") or creative.get("image_url")):
        media.append({"kind": "image", "source_ref": creative.get("image_hash"),
                      "source_url": creative.get("image_url")})

    bodies, titles, descs = (_uniq(clean_text(x) for x in xs) for xs in (bodies, titles, descs))
    links = _uniq(x for x in links if x)

    kinds = {m["kind"] for m in media}
    if cards or "CAROUSEL" in (feed.get("ad_formats") or []):
        fmt = "carousel"
    elif kinds == {"image", "video"}:
        fmt = "mixed"
    elif "video" in kinds:
        fmt = "video"
    elif "image" in kinds:
        fmt = "static_image"
    elif bodies or titles:
        fmt = "text"
    else:
        fmt = "unknown"

    return {
        "headline": titles[0] if titles else None,
        "body": bodies[0] if bodies else None,
        "description": descs[0] if descs else None,
        "cta_text": _cta_label(cta_type),
        "text_variants": {"bodies": bodies, "titles": titles, "descriptions": descs,
                          "links": links, "cards": cards},
        "link": links[0] if links else None,
        "format": fmt,
        "media": media,
    }


def _uniq(items) -> list:
    seen, out = set(), []
    for x in items:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _count(actions: list[dict] | None, *types: str) -> int | None:
    if not actions:
        return None
    for t in types:  # first matching type wins, so pixel + omni duplicates aren't summed
        for a in actions:
            if a.get("action_type") == t:
                return int(float(a.get("value", 0)))
    return 0


def pull(conn, brand: dict, account_id: str, run_id: str, graph: Graph | None = None,
         log=print) -> dict:
    """Acquire every ad of one own brand (active or not) + insights for 3 windows."""
    g = graph or Graph()
    prefix = brand["campaign_prefix"]
    counters = {"listed": 0, "in_brand": 0, "created": 0, "updated": 0, "metrics_rows": 0,
                "post_fallbacks": 0, "errors": []}

    ads = g.paginate(f"{account_id}/ads", {
        "fields": "id,name,effective_status,configured_status,created_time,"
                  "campaign{id,name},adset{id,name}",
        "limit": 200,
    })
    counters["listed"] = len(ads)
    ads = [a for a in ads if (a.get("campaign") or {}).get("name", "").startswith(prefix)]
    counters["in_brand"] = len(ads)
    log(f"  {len(ads)} ads under campaigns starting '{prefix}' (of {counters['listed']} listed)")

    adsets = {a["id"]: a for a in g.paginate(f"{account_id}/adsets", {
        "fields": "id,name,targeting{geo_locations,age_min,age_max}", "limit": 200})}

    details: dict[str, dict] = {}
    ids = [a["id"] for a in ads]
    for i in range(0, len(ids), 25):
        chunk = ids[i:i + 25]
        details.update(g.get("", {"ids": ",".join(chunk), "fields": CREATIVE_FIELDS}))

    for a in ads:
        creative = (details.get(a["id"]) or {}).get("creative") or {}
        parsed = parse_creative(creative)
        if not (parsed["body"] or parsed["headline"]) and creative.get("effective_object_story_id"):
            parsed = _post_fallback(g, creative["effective_object_story_id"], parsed, counters)

        adset = adsets.get((a.get("adset") or {}).get("id"), {})
        countries = ((adset.get("targeting") or {}).get("geo_locations") or {}).get("countries")
        link = with_url_tags(parsed["link"], creative.get("url_tags"))
        raw_key = db.save_raw(f"meta_own/{a['id']}.json",
                              {"ad": a, "creative": creative, "adset": adset,
                               "connector_version": CONNECTOR_VERSION, "run_id": run_id,
                               "collected_at": db.now()})
        ad_id, created = db.upsert_ad(conn, {
            "brand_id": brand["id"], "source": "meta_own", "source_ad_id": a["id"],
            "source_url": f"https://www.facebook.com/adsmanager/manage/ads?selected_ad_ids={a['id']}",
            "lane": "own",
            "campaign_id": a["campaign"]["id"], "campaign_name": a["campaign"]["name"],
            "adset_id": (a.get("adset") or {}).get("id"),
            "adset_name": (a.get("adset") or {}).get("name"),
            "ad_name": a.get("name"),
            "active_status": (a.get("effective_status") or "unknown").lower(),
            "headline": parsed["headline"], "body": parsed["body"],
            "description": parsed["description"], "cta_text": parsed["cta_text"],
            "text_variants": parsed["text_variants"],
            "destination_url_original": link,
            "destination_url_canonical": canonical_url(link),
            "format": parsed["format"],
            "format_from_name": format_from_name(a.get("name")),
            "ad_code": ad_code_from_name(a.get("name")),
            "country": ",".join(countries) if countries else None,
            "source_created_at": a.get("created_time"),
            "raw_payload_key": raw_key,
            "metadata": {"creative_id": creative.get("id"),
                         "instagram_permalink_url": creative.get("instagram_permalink_url"),
                         "targeting_age": [adset.get("targeting", {}).get("age_min"),
                                           adset.get("targeting", {}).get("age_max")],
                         "connector_version": CONNECTOR_VERSION},
        })
        counters["created" if created else "updated"] += 1
        _replace_media(conn, ad_id, parsed["media"])
    conn.commit()

    _resolve_media_urls(conn, g, account_id, log)

    for window, preset in WINDOWS.items():
        rows = g.paginate(f"{account_id}/insights", {
            "level": "ad", "fields": INSIGHT_FIELDS, "breakdowns": "country",
            "date_preset": preset, "limit": 500,
            "filtering": json.dumps([{"field": "campaign.name", "operator": "CONTAIN",
                                      "value": prefix.split(" ")[0]}]),
        })
        rows = [r for r in rows if (r.get("campaign_name") or "").startswith(prefix)]
        for r in rows:
            _store_metrics(conn, r, window)
        counters["metrics_rows"] += len(rows)
        log(f"  insights {window}: {len(rows)} ad×country rows")
    conn.commit()
    counters["graph_calls"] = g.calls
    return counters


def _post_fallback(g: Graph, post_id: str, parsed: dict, counters: dict) -> dict:
    """Ads built from an existing page post carry no copy in the creative spec."""
    try:
        post = g.get(post_id, {"fields": "message,attachments{media_type,title,description,"
                                          "unshimmed_url,media,subattachments}"})
    except GraphError as e:
        counters["errors"].append(f"post {post_id}: {e}")
        return parsed
    counters["post_fallbacks"] += 1
    att = ((post.get("attachments") or {}).get("data") or [{}])[0]
    parsed["body"] = clean_text(post.get("message")) or parsed["body"]
    parsed["headline"] = clean_text(att.get("title")) or parsed["headline"]
    parsed["link"] = parsed["link"] or att.get("unshimmed_url")
    return parsed


def _replace_media(conn, ad_id: str, media: list[dict]):
    """Media refs are re-derived from the creative on every pull (they're facts of the
    creative, not analysis), but a row that was already processed keeps its artifacts."""
    kept = {r["source_ref"] for r in conn.execute(
        "SELECT source_ref FROM media_assets WHERE ad_id=? AND processing_status!='pending'", (ad_id,))}
    conn.execute("DELETE FROM media_assets WHERE ad_id=? AND processing_status='pending'", (ad_id,))
    for i, m in enumerate(media):
        if m.get("source_ref") in kept:
            continue
        conn.execute(
            """INSERT INTO media_assets (id, ad_id, kind, source_ref, source_url, rights_status,
                                         metadata) VALUES (?,?,?,?,?,?,?)""",
            (f"{ad_id}:m{i}", ad_id, m["kind"], m.get("source_ref"), m.get("source_url"), "own",
             db.dumps({"thumbnail_url": m.get("thumbnail_url")})),
        )


def _resolve_media_urls(conn, g: Graph, account_id: str, log):
    """Image hashes -> downloadable URLs (adimages); video ids -> source + thumbnail."""
    hashes = [r[0] for r in conn.execute(
        "SELECT DISTINCT source_ref FROM media_assets m JOIN ads a ON a.id=m.ad_id "
        "WHERE m.kind='image' AND m.source_ref IS NOT NULL AND a.source='meta_own'")]
    urls = {}
    for i in range(0, len(hashes), 50):
        try:
            rows = g.paginate(f"{account_id}/adimages", {
                "hashes": json.dumps(hashes[i:i + 50]), "fields": "hash,url,width,height"})
        except GraphError as e:
            log(f"  adimages lookup failed: {e}")
            continue
        urls.update({r["hash"]: r for r in rows})
    for h, r in urls.items():
        conn.execute("UPDATE media_assets SET source_url=?, metadata=json_set(metadata,'$.width',?,"
                     "'$.height',?) WHERE kind='image' AND source_ref=?",
                     (r.get("url"), r.get("width"), r.get("height"), h))

    vids = [r[0] for r in conn.execute(
        "SELECT DISTINCT source_ref FROM media_assets WHERE kind='video' AND source_ref IS NOT NULL")]
    for i in range(0, len(vids), 25):
        try:
            data = g.get("", {"ids": ",".join(vids[i:i + 25]),
                              "fields": "source,picture,length,permalink_url"})
        except GraphError as e:
            log(f"  video lookup failed: {e}")
            continue
        for vid, v in data.items():
            conn.execute(
                "UPDATE media_assets SET source_url=?, duration_ms=?, "
                "metadata=json_set(metadata,'$.thumbnail_url',coalesce(?,json_extract(metadata,'$.thumbnail_url')),"
                "'$.permalink_url',?) WHERE kind='video' AND source_ref=?",
                (v.get("source"), int(float(v["length"]) * 1000) if v.get("length") else None,
                 v.get("picture"), v.get("permalink_url"), vid))
    log(f"  media urls: {len(urls)}/{len(hashes)} images, {len(vids)} videos looked up")


def _store_metrics(conn, r: dict, window: str):
    ad_id = f"meta_own:{r['ad_id']}"
    if not conn.execute("SELECT 1 FROM ads WHERE id=?", (ad_id,)).fetchone():
        return  # insight for a deleted/archived ad we didn't list — skip, don't invent a row
    acts = r.get("actions") or []
    num = lambda k: float(r[k]) if r.get(k) not in (None, "") else None
    conn.execute(
        """INSERT OR REPLACE INTO ad_metrics (ad_id, window, country, date_start, date_stop,
             spend, impressions, reach, clicks, link_clicks, ctr, cpm, cpc, frequency,
             meta_landing_page_views, meta_add_to_cart, meta_initiate_checkout,
             meta_purchases, meta_leads, actions, fetched_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (ad_id, window, r.get("country") or "unknown", r.get("date_start"), r.get("date_stop"),
         num("spend"), int(num("impressions") or 0), int(num("reach") or 0),
         int(num("clicks") or 0), _count(acts, "link_click"),
         num("ctr"), num("cpm"), num("cpc"), num("frequency"),
         _count(acts, "landing_page_view", "omni_landing_page_view"),
         _count(acts, "add_to_cart", "omni_add_to_cart", "offsite_conversion.fb_pixel_add_to_cart"),
         _count(acts, "initiate_checkout", "omni_initiated_checkout",
                "offsite_conversion.fb_pixel_initiate_checkout"),
         _count(acts, "purchase", "omni_purchase", "offsite_conversion.fb_pixel_purchase"),
         _count(acts, "lead", "offsite_conversion.fb_pixel_lead", "onsite_conversion.lead_grouped"),
         db.dumps(acts), db.now()),
    )
