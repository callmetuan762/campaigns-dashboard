"""M2 checks: media sniffing, SSRF guard, landing extraction, evidence IDs (no network)."""

import json
import socket

import pytest

from teardown import db, evidence, landing, media


def test_sniff_uses_bytes_not_extension():
    assert media.sniff(b"\xff\xd8\xff\xe0" + b"\0" * 12) == "image/jpeg"
    assert media.sniff(b"\0\0\0\x18ftypmp42" + b"\0" * 4) == "video/mp4"
    assert media.sniff(b"<html><body>") is None  # an HTML error page saved as .jpg is rejected


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.5", "169.254.169.254", "192.168.1.1", "::1"])
def test_ssrf_guard_refuses_non_public_ips(monkeypatch, ip):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(None, None, None, None, (ip, 0))])
    with pytest.raises(landing.Refused) as e:
        landing.check_public("https://nowaplanet.com/")
    assert e.value.status == "unsupported"


def test_allowlist_matches_subdomains_only_of_listed_domains():
    allowed = landing.allowlist({"own": [{"domain": "nowaplanet.com"}],
                                 "competitors": [{"domain": "us.tonies.com"}]})
    assert landing.host_allowed("go.nowaplanet.com", allowed)
    assert landing.host_allowed("www.tonies.com", allowed)
    assert not landing.host_allowed("nowaplanet.com.evil.io", allowed)
    assert not landing.host_allowed("bit.ly", allowed)


PREORDER_FIXTURE = """<html><head><title>Reserve your Nowa: $99 pre-order (was $149)</title>
<meta name="description" content="Founding preorder"></head><body><main>
<h1>Reserve your Nowa</h1><div>Founding preorder · $99</div><div>85</div><div>of 500 left</div>
<div>Full package returns to $249</div><p>The first 500 bundles are $99.</p>
<p>Ships October 2026 · Fully refundable until it ships</p>
<p>If your Nowa has not shipped by November 30, 2026 you get a full refund.</p>
<a class="btn" href="/cart">Reserve your Nowa for $99 →</a></main></body></html>"""


def test_extract_reads_offer_facts_across_dom_nodes():
    ex = landing.extract(PREORDER_FIXTURE)
    assert ex["h1"] == ["Reserve your Nowa"]
    assert {"$99", "$249"} <= {p["match"] for p in ex["prices"]}
    scarcity = {s["match"].replace("\n", " ") for s in ex["scarcity"]}
    assert "85 of 500 left" in scarcity and "first 500 bundles" in scarcity
    assert {"October 2026", "November 30, 2026"} <= {d["match"] for d in ex["dates"]}
    assert "Reserve your Nowa for $99 →" in ex["primary_ctas"]
    assert not any("Oct 15" in d["match"] for d in ex["dates"])  # the page never says Oct 15


def test_evidence_ids_are_stable_and_signals_explicit(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(evidence, "CACHE", tmp_path / "cache")
    conn = db.connect(":memory:")
    db.upsert_brand(conn, id="nowa", name="Nowa", kind="own")
    db.upsert_ad(conn, {"brand_id": "nowa", "source": "meta_own", "source_ad_id": "1", "lane": "own",
                        "headline": "$99. Until Oct 15.", "body": "Reserve now", "cta_text": "Shop Now",
                        "text_variants": {"bodies": ["Reserve now", "Second body"]},
                        "raw_payload_key": "raw/x.json"})
    evidence.build(conn, log=lambda *_: None)
    ids1 = sorted(r[0] for r in conn.execute("SELECT id FROM evidence"))
    evidence.build(conn, log=lambda *_: None)
    ids2 = sorted(r[0] for r in conn.execute("SELECT id FROM evidence"))
    assert ids1 == ids2 == ["meta_own:1:ad_copy", "meta_own:1:ad_copy:v1",
                            "meta_own:1:ad_cta", "meta_own:1:ad_headline"]
    sig = json.loads(conn.execute("SELECT json_extract(metadata,'$.signals') FROM ads").fetchone()[0])
    assert sig == {"media": "none", "ocr": "unavailable", "asr": "unavailable"}
