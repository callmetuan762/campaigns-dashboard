"""M1 acceptance checks on synthetic fixtures (no network)."""

import pytest

from teardown import db
from teardown.normalize import ad_code_from_name, canonical_url, format_from_name
from teardown.sources import csv_import, meta_adlib
from teardown.sources.meta_own import parse_creative


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    c = db.connect(":memory:")
    db.upsert_brand(c, id="nowa", name="Nowa", kind="own")
    db.upsert_brand(c, id="yoto", name="Yoto", kind="competitor", page_id="111", parent_brand="nowa")
    return c


def test_canonical_url_strips_tracking_keeps_real_params():
    url = "https://www.NowaPlanet.com/preorder/?utm_source=meta&fbclid=x&variant=2"
    assert canonical_url(url) == "https://nowaplanet.com/preorder?variant=2"
    assert canonical_url("javascript:alert(1)") is None


@pytest.mark.parametrize("name,code,fmt", [
    ("Nowa | HOME-04 | broad | single_image | 20260715", "HOME-04", "static_image"),
    ("Nowa | ROUTINE-CAR-02 | broad | carousel | 20260715", "ROUTINE-CAR-02", "carousel"),
    # ad-set prefix mentions Carousel; the ad itself is static (no -CAR- in the code)
    ("Routine — Static+Carousel · ROUTINE-09", "ROUTINE-09", "static_image"),
    ("Home — Video · HOME-VID-03", "HOME-VID-03", "video"),
    ("some manual name", None, None),
])
def test_ad_code_and_format_from_name(name, code, fmt):
    assert ad_code_from_name(name) == code
    assert format_from_name(name) == fmt


def test_parse_creative_shapes():
    carousel = parse_creative({"object_story_spec": {"link_data": {
        "message": "Big feelings?", "link": "https://nowaplanet.com/for/big-feelings/",
        "call_to_action": {"type": "LEARN_MORE"},
        "child_attachments": [{"name": "Card 1", "image_hash": "h1"},
                              {"name": "Card 2", "image_hash": "h2"}]}}})
    assert carousel["format"] == "carousel" and len(carousel["media"]) == 2
    assert carousel["cta_text"] == "Learn More"

    video = parse_creative({"object_story_spec": {"video_data": {
        "message": "Watch", "title": "Nowa", "video_id": "v1",
        "call_to_action": {"type": "SHOP_NOW", "value": {"link": "https://nowaplanet.com/"}}}}})
    assert video["format"] == "video" and video["link"] == "https://nowaplanet.com/"

    dco = parse_creative({"asset_feed_spec": {
        "bodies": [{"text": "A"}, {"text": "B"}], "titles": [{"text": "T"}],
        "link_urls": [{"website_url": "https://nowaplanet.com/preorder"}],
        "images": [{"hash": "h"}], "call_to_action_types": ["SHOP_NOW"]}})
    assert dco["text_variants"]["bodies"] == ["A", "B"] and dco["format"] == "static_image"


def test_reimport_creates_no_duplicates_and_keeps_first_seen(conn):
    ad = {"brand_id": "nowa", "source": "meta_own", "source_ad_id": "1", "lane": "own",
          "headline": "v1", "raw_payload_key": "raw/x.json"}
    ad_id, created = db.upsert_ad(conn, ad)
    first = conn.execute("SELECT first_seen_at FROM ads WHERE id=?", (ad_id,)).fetchone()[0]
    _, created_again = db.upsert_ad(conn, {**ad, "headline": "v2"})
    rows = conn.execute("SELECT headline, first_seen_at FROM ads").fetchall()
    assert created and not created_again
    assert len(rows) == 1 and rows[0]["headline"] == "v2" and rows[0]["first_seen_at"] == first


def test_adlib_ingest_rejects_other_pages_and_is_idempotent(conn):
    brand = {"id": "yoto", "page_id": "111"}
    node = {"ad_archive_id": "9001", "page_id": "111", "is_active": True, "start_date": 1754000000,
            "collation_count": 3, "snapshot": {"body": {"text": "Screen-free stories"},
                                                "title": "Yoto", "display_format": "IMAGE",
                                                "link_url": "https://us.yotoplay.com/?utm_x=1",
                                                "images": [{"original_image_url": "https://x/i.jpg"}]}}
    partner = {**node, "ad_archive_id": "9002", "page_id": "222"}
    result = {"collected_at": "2026-09-28T00:00:00Z", "country": "US", "nodes": [node, partner]}
    c1 = meta_adlib.ingest(conn, brand, result, "run")
    c2 = meta_adlib.ingest(conn, brand, result, "run")
    assert c1 == {"nodes": 2, "created": 1, "updated": 0, "rejected_page": 1}
    assert c2["created"] == 0 and c2["updated"] == 1
    row = conn.execute("SELECT lane, format, metadata FROM ads").fetchone()
    assert row["lane"] == "competitor" and row["format"] == "static_image"
    assert '"longevity_is_proxy": true' in row["metadata"]
    assert conn.execute("SELECT COUNT(*) FROM ad_metrics").fetchone()[0] == 0  # never metrics


def test_csv_import_validates_and_writes_rejections(conn, tmp_path):
    f = tmp_path / "manual.csv"
    f.write_text(
        "source,source_ad_id,brand,headline,body,destination_url,format\n"
        "tiktok_cc,t1,yoto,Hello,,https://us.yotoplay.com/?utm_source=tt,video\n"
        "tiktok_cc,t2,nobody,Hi,,,video\n"
        "tiktok_cc,t3,yoto,,,,video\n"
        "meta_own,t4,nowa,Hi,,,video\n")
    c = csv_import.import_file(conn, f, "run")
    assert c["created"] == 1 and c["rejected"] == 3
    assert (tmp_path / "imports" / "manual.rejected.csv").exists()
    c2 = csv_import.import_file(conn, f, "run")
    assert c2["created"] == 0 and c2["updated"] == 1
