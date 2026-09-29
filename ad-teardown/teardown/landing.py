"""M2 landing pages: safe fetch → structured extraction → per-ad evidence (spec Step 5).

Safety:
- Only http(s). Every redirect hop is re-checked; hosts that resolve to private, loopback,
  link-local, reserved or multicast IPs are refused (SSRF guard). At most 3 redirects,
  10 s timeout, 2 MB HTML cap, 4 fetches at a time and at most 2 per domain.
- Only allowlisted domains are fetched: our own domains, competitor domains from
  brands.yaml, and app stores. Anything else becomes `unsupported`.
- robots.txt is respected for competitor domains. Our own domains are exempt (it's our site,
  and go.nowaplanet.com disallows all crawlers to stay out of search indexes). No forms are
  submitted and no cookie banners accepted.
- Our own pages are always rendered in a real browser, so JS-rendered offer elements
  (e.g. the "N of 500 left" counter) are captured. Analytics is blocked, as above.

Analytics hygiene: we fetch the canonical URL (UTMs stripped), never the ad's tracked URL,
so our own GA4 / Meta reports don't gain fake "meta / paid_social" sessions. The plain HTML
fetch runs no JavaScript. When a page is client-rendered and needs a real browser, every
analytics/pixel request is blocked before it leaves.

A blocked or failed page is `unverifiable` for M3, never a mismatch.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib import robotparser
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

from . import config, db

EXTRACTOR_VERSION = "landing/0.1"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
ROBOTS_AGENT = "NowaAdTeardown"
MAX_REDIRECTS, TIMEOUT, MAX_BYTES, TTL_S = 3, 10, 2 * 1024 * 1024, 24 * 3600
EXTRA_ALLOWED = ["apps.apple.com", "itunes.apple.com", "play.google.com"]
ANALYTICS_HOSTS = re.compile(
    r"(google-analytics|googletagmanager|analytics\.google|doubleclick|facebook\.(net|com)/tr|"
    r"connect\.facebook|clarity\.ms|hotjar|tiktok|snapchat|pinterest|klaviyo|segment|"
    r"cookiehub|monorail-edge\.shopifysvc|/api/collect|/\.well-known/shopify/monorail)", re.IGNORECASE)


class Refused(Exception):
    def __init__(self, status: str, msg: str):
        super().__init__(msg)
        self.status = status


# ---------- guards ----------

def allowlist(cfg: dict) -> list[str]:
    doms = [b.get("domain") for b in cfg["own"]] + [c.get("domain") for c in cfg.get("competitors") or []]
    out = set(EXTRA_ALLOWED)
    for d in filter(None, doms):
        d = d.lower().removeprefix("www.")
        out.add(d)
        parts = d.split(".")
        if len(parts) > 2:  # us.tonies.com -> tonies.com, so www/other subdomains pass too
            out.add(".".join(parts[-2:]))
    return sorted(out)


def host_allowed(host: str, allowed: list[str]) -> bool:
    host = host.lower().removeprefix("www.")
    return any(host == d or host.endswith("." + d) for d in allowed)


def check_public(url: str):
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise Refused("unsupported", f"non-http url {url[:80]}")
    try:
        infos = socket.getaddrinfo(parts.hostname, None)
    except socket.gaierror as e:
        raise Refused("error", f"dns: {e}") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified):
            raise Refused("unsupported", f"{parts.hostname} resolves to non-public {ip}")


_robots: dict[str, robotparser.RobotFileParser | None] = {}
_robots_lock = threading.Lock()


def robots_ok(url: str) -> bool:
    parts = urlsplit(url)
    base = f"{parts.scheme}://{parts.netloc}"
    with _robots_lock:
        rp = _robots.get(base, "missing")
    if rp == "missing":
        rp = robotparser.RobotFileParser()
        try:
            r = requests.get(base + "/robots.txt", timeout=TIMEOUT, headers={"User-Agent": UA})
            rp.parse(r.text.splitlines() if r.ok else [])
        except requests.RequestException:
            rp = None  # unreachable robots.txt: treat as no rules, like most crawlers
        with _robots_lock:
            _robots[base] = rp
    return rp is None or (rp.can_fetch(ROBOTS_AGENT, url) and rp.can_fetch("*", url))


# ---------- fetch ----------

def fetch(url: str, allowed: list[str], own: list[str] | None = None) -> dict:
    chain, current = [], url
    for _ in range(MAX_REDIRECTS + 1):
        host = urlsplit(current).hostname or ""
        if not host_allowed(host, allowed):
            raise Refused("unsupported", f"{host} not on the domain allowlist")
        check_public(current)
        if not host_allowed(host, own or []) and not robots_ok(current):
            raise Refused("blocked", "disallowed by robots.txt")
        try:
            r = requests.get(current, timeout=TIMEOUT, allow_redirects=False, stream=True,
                             headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
        except requests.Timeout as e:
            raise Refused("timeout", str(e)[:120]) from e
        except requests.RequestException as e:
            raise Refused("error", str(e)[:120]) from e
        chain.append({"url": current, "status": r.status_code})
        if r.is_redirect or r.status_code in (301, 302, 303, 307, 308):
            current = urljoin(current, r.headers.get("Location", ""))
            r.close()
            continue
        if r.status_code in (404, 410):
            raise Refused("missing", f"http {r.status_code}")
        if r.status_code in (401, 403, 429, 503):
            raise Refused("blocked", f"http {r.status_code}")
        if r.status_code >= 400:
            raise Refused("error", f"http {r.status_code}")
        ctype = r.headers.get("Content-Type", "")
        if "html" not in ctype:
            raise Refused("unsupported", f"content-type {ctype[:40]}")
        body = b""
        for chunk in r.iter_content(64 * 1024):
            body += chunk
            if len(body) > MAX_BYTES:
                break
        return {"final_url": current, "http_status": r.status_code, "chain": chain,
                "html": body[:MAX_BYTES].decode(r.encoding or "utf-8", errors="replace"),
                "truncated": len(body) > MAX_BYTES}
    raise Refused("error", f"more than {MAX_REDIRECTS} redirects")


def render(url: str) -> str:
    """Client-rendered page fallback: real browser with every analytics call blocked."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(user_agent=UA, locale="en-US", viewport={"width": 1280, "height": 900})
        ctx.route("**/*", lambda route: route.abort() if ANALYTICS_HOSTS.search(route.request.url)
                  else route.continue_())
        page = ctx.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(3000)
        html = page.content()
        browser.close()
    return html


# ---------- extract ----------

PRICE = re.compile(r"(?:US)?\$\s?\d[\d,]*(?:\.\d{2})?")
TERMS = re.compile(r"(\d+\s?% off|save \$?\d+[\d,]*|free shipping|free trial|\d+[- ]day (?:free )?trial|"
                   r"money[- ]back|refund(?:able)?|guarantee[d]?|no subscription|subscription|"
                   r"cancel anytime|pre-?order|reserve|deposit|financing|klarna|afterpay|shop pay)", re.IGNORECASE)
DATES = re.compile(r"\b((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}(?:,\s*\d{4})?|"
                   r"(?:January|February|March|April|May|June|July|August|September|October|November|"
                   r"December)\s+\d{4}|\d{1,2}/\d{1,2}(?:/\d{2,4})?)\b")
# Page text is newline-joined per DOM node, so counters like "85 / of 500 left" span lines: \s+.
SCARCITY = re.compile(r"(\d[\d,]*\s+of\s+\d[\d,]*\s+(?:left|remaining|spots|reserved)|"
                      r"\bonly\s+\d+\s+left|^\d[\d,]*\s+left$|\d[\d,]*\s+(?:reservations|spots)\s+only|"
                      r"first\s+\d[\d,]*\s+(?:families|customers|orders|backers|bundles|units|spots)|"
                      r"limited\s+(?:time|edition|spots)|selling fast|almost gone|sold out)", re.IGNORECASE | re.MULTILINE)
TRUST = re.compile(r"(\d[\d,.]*\+? (?:reviews|ratings|families|parents|kids|customers)|"
                   r"\d(?:\.\d)? ?(?:out of 5|stars?|★)|as seen (?:in|on)|award[- ]winning|"
                   r"trusted by [^.]{0,40}|kidSAFE|COPPA|FCC|certified|clinically|expert[- ]designed|"
                   r"designed with [^.]{0,40}(?:psychologists|experts|therapists))", re.IGNORECASE)


def _snips(text: str, rx: re.Pattern, kind: str, limit: int = 12) -> list[dict]:
    out, seen = [], set()
    for m in rx.finditer(text):
        s, e = max(0, m.start() - 70), min(len(text), m.end() + 70)
        ctx = text[s:e].replace("\n", " ").strip()
        key = m.group(0).lower()
        if (key, ctx[:40]) in seen:
            continue
        seen.add((key, ctx[:40]))
        out.append({"kind": kind, "match": m.group(0), "context": ctx, "offset": m.start()})
        if len(out) >= limit:
            break
    return out


def extract(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    meta = lambda **kw: (soup.find("meta", attrs=kw) or {}).get("content")
    for t in soup(["script", "style", "noscript", "svg", "template", "iframe"]):
        t.decompose()
    h1 = [x.get_text(" ", strip=True) for x in soup.find_all("h1")][:3]
    h2 = [x.get_text(" ", strip=True) for x in soup.find_all("h2")][:10]
    ctas = []
    for el in soup.select("button, a[class*=btn], a[class*=button], a[class*=cta], input[type=submit]"):
        t = (el.get_text(" ", strip=True) or el.get("value") or "").strip()
        if t and 1 < len(t) < 60 and t.lower() not in {c.lower() for c in ctas}:
            ctas.append(t)
    main = soup.find("main") or soup.body or soup
    for t in main.find_all(["nav", "footer", "header"]):
        t.decompose()
    text = re.sub(r"[ \t]+", " ", main.get_text("\n", strip=True))
    text = re.sub(r"\n{2,}", "\n", text)
    return {
        "title": soup.title.get_text(strip=True) if soup.title else None,
        "meta_description": meta(name="description") or meta(property="og:description"),
        "product_name": meta(property="og:title"),
        "site_name": meta(property="og:site_name"),
        "h1": h1, "h2": h2,
        "hero_text": text[:900],
        "primary_ctas": ctas[:10],
        "prices": _snips(text, PRICE, "price", 20),
        "terms": _snips(text, TERMS, "term", 20),
        "dates": _snips(text, DATES, "date", 10),
        "scarcity": _snips(text, SCARCITY, "scarcity", 10),
        "trust": _snips(text, TRUST, "trust", 12),
        "visible_chars": len(text),
        "text": text[:20000],
    }


# ---------- orchestrate ----------

_domain_locks: dict[str, threading.Semaphore] = {}
_dl_lock = threading.Lock()


def _snapshot(url: str, allowed: list[str], own: list[str]) -> dict:
    host = urlsplit(url).hostname or ""
    with _dl_lock:
        sem = _domain_locks.setdefault(host, threading.Semaphore(2))
    with sem:
        snap = {"id": db.new_id(), "canonical_url": url, "fetched_at": db.now()}
        try:
            got = fetch(url, allowed, own)
            data = extract(got["html"])
            rendered = False
            is_own = host_allowed(urlsplit(got["final_url"]).hostname or "", own)
            if is_own or data["visible_chars"] < 400:  # own page, or mostly client-rendered
                try:
                    data = extract(render(got["final_url"]))
                    rendered = True
                except Exception as e:  # noqa: BLE001
                    data["render_error"] = str(e)[:120]
            data["rendered"] = rendered
            data["truncated"] = got["truncated"]
            snap.update(status="ok", final_url=got["final_url"], http_status=got["http_status"],
                        redirect_chain=got["chain"], title=data["title"], extracted=data,
                        content_hash=hashlib.sha256(data["text"].encode()).hexdigest())
        except Refused as e:
            snap.update(status=e.status, error_code=str(e)[:200])
        time.sleep(0.5)
        return snap


def run(conn, run_id: str, brand_ids: list[str] | None = None, force: bool = False, log=print) -> dict:
    cfg = config.load()
    allowed = allowlist(cfg)
    own = [b["domain"] for b in cfg["own"] if b.get("domain")]
    q = "SELECT id, destination_url_canonical u FROM ads WHERE destination_url_canonical IS NOT NULL"
    params: list = []
    if brand_ids:
        q += f" AND brand_id IN ({','.join('?' * len(brand_ids))})"
        params = brand_ids
    ads = [dict(r) for r in conn.execute(q, params)]
    urls = sorted({a["u"] for a in ads})

    # 24 h cache per canonical URL.
    cached, todo = {}, []
    for u in urls:
        r = conn.execute("SELECT * FROM landing_snapshots WHERE canonical_url=? ORDER BY fetched_at DESC LIMIT 1",
                         (u,)).fetchone()
        fresh = r and (time.time() - time.mktime(time.strptime(r["fetched_at"], "%Y-%m-%dT%H:%M:%SZ"))
                       + time.timezone) < TTL_S
        if r and fresh and not force:
            cached[u] = r["id"]
        else:
            todo.append(u)
    log(f"  {len(urls)} distinct landing URLs ({len(cached)} cached, {len(todo)} to fetch)")

    counters: dict[str, int] = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        for snap in pool.map(lambda u: _snapshot(u, allowed, own), todo):
            counters[snap["status"]] = counters.get(snap["status"], 0) + 1
            conn.execute(
                """INSERT INTO landing_snapshots (id, canonical_url, final_url, fetched_at, status,
                     http_status, content_hash, title, extracted, redirect_chain, error_code)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (snap["id"], snap["canonical_url"], snap.get("final_url"), snap["fetched_at"],
                 snap["status"], snap.get("http_status"), snap.get("content_hash"), snap.get("title"),
                 db.dumps(snap.get("extracted")) if snap.get("extracted") else None,
                 db.dumps(snap.get("redirect_chain")) if snap.get("redirect_chain") else None,
                 snap.get("error_code")))
            cached[snap["canonical_url"]] = snap["id"]
    conn.commit()

    # Link ads → snapshot, and write landing_dom evidence the offer check can cite.
    snaps = {r["id"]: dict(r) for r in conn.execute(
        f"SELECT * FROM landing_snapshots WHERE id IN ({','.join('?' * len(cached))})", list(cached.values()))}
    for a in ads:
        sid = cached.get(a["u"])
        if not sid:
            continue
        conn.execute("INSERT OR IGNORE INTO ad_landing_snapshots (ad_id, snapshot_id, run_id) VALUES (?,?,?)",
                     (a["id"], sid, run_id))
        conn.execute("DELETE FROM evidence WHERE ad_id=? AND origin='landing_dom'", (a["id"],))
        s = snaps[sid]
        if s["status"] != "ok":
            continue
        ex = json.loads(s["extracted"])
        n = 0
        items = ([("title", ex.get("title"))] + [("h1", h) for h in ex.get("h1") or []] +
                 [("h2", h) for h in (ex.get("h2") or [])[:6]] + [("cta", c) for c in (ex.get("primary_ctas") or [])[:5]] +
                 [(sn["kind"], sn["context"]) for k in ("prices", "terms", "dates", "scarcity", "trust")
                  for sn in ex.get(k) or []])
        for kind, text in items:
            if text:
                conn.execute(
                    """INSERT OR REPLACE INTO evidence (id, ad_id, origin, text_value, artifact_key,
                         locator, confidence, extractor_version, created_at) VALUES (?,?,?,?,?,?,?,?,?)""",
                    (f"{a['id']}:landing_dom:{n}", a["id"], "landing_dom", text, sid,
                     db.dumps({"snapshot_id": sid, "kind": kind, "final_url": s["final_url"],
                               "fetched_at": s["fetched_at"]}), None, EXTRACTOR_VERSION, db.now()))
                n += 1
    conn.commit()
    return counters
