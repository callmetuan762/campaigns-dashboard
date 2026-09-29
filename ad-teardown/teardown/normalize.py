"""Pure normalization helpers (no network, no DB) — the easiest part to unit-test."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Query params that identify the click, not the page. Stripped from the canonical URL;
# the original URL is always kept alongside so attribution isn't lost.
TRACKING_PARAMS = re.compile(
    r"^(utm_\w+|fbclid|gclid|ttclid|msclkid|mc_[a-z]+|_hs\w+|ref|igshid|campaign_id|ad_id|adset_id|"
    r"placement|site_source_name|h_ad_id)$",
    re.IGNORECASE,
)


def clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    value = unicodedata.normalize("NFKC", value)
    value = re.sub(r"[ \t ]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip() or None


def canonical_url(url: str | None) -> str | None:
    if not url:
        return None
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https"):
        return None
    host = (parts.hostname or "").lower()
    host = host.removeprefix("www.")
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not TRACKING_PARAMS.match(k)]
    path = parts.path or "/"
    if len(path) > 1:
        path = path.rstrip("/") or "/"
    return urlunsplit(("https", host, path, urlencode(sorted(query)), ""))


def with_url_tags(link: str | None, url_tags: str | None) -> str | None:
    """Meta appends creative `url_tags` to the link at click time — rebuild what a user lands on."""
    if not link or not url_tags:
        return link
    sep = "&" if "?" in link else "?"
    return f"{link}{sep}{url_tags.lstrip('?&')}"


def ad_code_from_name(name: str | None) -> str | None:
    """Two naming conventions coexist on the Nowa account:
    old  'Nowa | CODE | broad | single_image | 20260715'  -> 2nd pipe field
    new  '<adset prefix> · CODE'                           -> text after the last ' · '
    """
    if not name:
        return None
    if " · " in name:
        return name.rsplit(" · ", 1)[1].strip() or None
    fields = [f.strip() for f in name.split("|")]
    if len(fields) >= 2 and fields[0].lower() in ("nowa", "pawcast"):
        return fields[1] or None
    return None


def format_from_name(name: str | None) -> str | None:
    """Cross-check only — media facts win. Old names carry an explicit token; new names
    encode format inside the ad code (-CAR- carousel, -VID- video)."""
    if not name:
        return None
    if " · " not in name:
        # Only the old pipe convention has a format token. New names carry the ad-set prefix
        # ("Static+Carousel"), which describes the ad set, not this ad.
        low = name.lower()
        for token, fmt in (("carousel", "carousel"), ("single_video", "video"),
                           ("single_image", "static_image")):
            if token in low:
                return fmt
    code = (ad_code_from_name(name) or "").upper()
    if not code:
        return None
    if "-CAR-" in f"-{code}-":
        return "carousel"
    if "-VID-" in f"-{code}-":
        return "video"
    return "static_image"


def text_fingerprint(*parts: str | None) -> str:
    joined = " ".join(p.lower() for p in parts if p)
    joined = re.sub(r"\W+", " ", joined).strip()
    return hashlib.sha256(joined.encode()).hexdigest()[:16]
