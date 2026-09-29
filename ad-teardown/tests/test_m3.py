"""M3 acceptance on synthetic fixtures with the mock provider (no model calls, no network)."""

import json

import pytest

from teardown import analyze, db, policy
from teardown.provider import MockProvider
from teardown.taxonomy import LABELS


def _ok_labels():
    return {k: "unknown" for k in LABELS if k != "cta_intent"}


def _result(ad_id, status="match", ad_ids=(), page_ids=(), severity=None, conf=0.9):
    return {"ad_id": ad_id, "labels": _ok_labels(),
            "offer_detail": {"price": None, "currency": None, "discount_percent": None, "trial_days": None,
                             "deadline": None, "later_price": None, "scarcity": None},
            "claims": [], "landing_comparison": {"status": status, "severity": severity, "reason": "x",
                                                 "ad_evidence_ids": list(ad_ids),
                                                 "page_evidence_ids": list(page_ids)},
            "confidence": {**{k: conf for k in LABELS if k != "cta_intent"}, "landing_comparison": conf},
            "review_flags": [], "model_notes": "n"}


IDS: dict[str, str] = {}


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    c = db.connect(":memory:")
    db.upsert_brand(c, id="nowa", name="Nowa", kind="own")
    run = db.create_run(c, {"stage": "fixture"})

    def ad(i, text, page_text=None, page_status="ok"):
        ad_id, _ = db.upsert_ad(c, {"brand_id": "nowa", "source": "meta_own", "source_ad_id": str(i),
                                    "lane": "own", "headline": text, "cta_text": "Shop Now",
                                    "format": "static_image", "raw_payload_key": "raw/x.json",
                                    "destination_url_canonical": f"https://nowaplanet.com/p{i}"})
        sid = f"s{i}"
        c.execute("INSERT INTO landing_snapshots (id, canonical_url, fetched_at, status) VALUES (?,?,?,?)",
                  (sid, f"https://nowaplanet.com/p{i}", db.now(), page_status))
        c.execute("INSERT INTO ad_landing_snapshots VALUES (?,?,?)", (ad_id, sid, run))
        c.execute("INSERT INTO evidence (id, ad_id, origin, text_value, created_at) VALUES (?,?,?,?,?)",
                  (f"{ad_id}:ad_headline", ad_id, "ad_headline", text, db.now()))
        if page_text:
            c.execute("INSERT INTO evidence (id, ad_id, origin, text_value, locator, created_at) VALUES (?,?,?,?,?,?)",
                      (f"{ad_id}:landing_dom:0", ad_id, "landing_dom", page_text,
                       json.dumps({"kind": "price"}), db.now()))
        return ad_id

    IDS.update({
        "conflict": ad(1, "$99 until Oct 15, then $149", "full package returns to $249"),
        "blocked": ad(2, "$99 until Oct 15, then $149", None, page_status="blocked"),
        "plain": ad(3, "Routines, minus the nagging", "Morning routines made fun"),
    })
    c.commit()
    return c


def responder_factory(bad_first=False):
    seen = set()

    def respond(model, payloads):
        out = []
        for p in payloads:
            ids = {e["o"]: e["id"] for e in p["evidence"]}
            aid = p["ad_id"]
            if aid.endswith(":1"):
                if bad_first and aid not in seen:
                    seen.add(aid)  # first answer: a mismatch without page evidence (invalid)
                    out.append(_result(aid, "mismatch", [ids["ad_headline"]], [], "high"))
                else:
                    out.append(_result(aid, "mismatch", [ids["ad_headline"]], [ids["landing_dom"]], "high"))
            elif aid.endswith(":2"):  # model wrongly claims a mismatch on a blocked page
                out.append(_result(aid, "mismatch", [ids["ad_headline"]], [], "high"))
            else:
                out.append(_result(aid, "match", [ids["ad_headline"]], [ids["landing_dom"]]))
        return out

    return respond


def _latest(conn, ad_id):
    return conn.execute("SELECT * FROM analyses WHERE ad_id=? AND status IN ('completed','needs_review') "
                        "ORDER BY created_at DESC LIMIT 1", (ad_id,)).fetchone()


def test_conflicting_offer_flagged_with_two_sided_evidence(conn):
    counts = analyze.run(conn, MockProvider(responder_factory()), log=lambda *_: None)
    row = _latest(conn, IDS["conflict"])
    lc = json.loads(row["landing_comparison"])
    assert lc["status"] == "mismatch" and lc["severity"] == "high"
    assert lc["ad_evidence_ids"] == [f"{IDS['conflict']}:ad_headline"]
    assert lc["page_evidence_ids"] == [f"{IDS['conflict']}:landing_dom:0"]
    assert row["status"] == "needs_review"  # high-severity mismatch goes to a human
    assert row["model_id"] == analyze.SONNET  # a mismatch always gets the stronger model
    assert counts["spent_usd"] > 0


def test_unreachable_page_is_never_a_mismatch(conn):
    analyze.run(conn, MockProvider(responder_factory()), log=lambda *_: None)
    lc = json.loads(_latest(conn, IDS["blocked"])["landing_comparison"])
    assert lc["status"] == "unverifiable" and lc["page_evidence_ids"] == []


def test_invalid_output_gets_one_repair(conn):
    provider = MockProvider(responder_factory(bad_first=True))
    analyze.run(conn, provider, log=lambda *_: None)
    single_calls = [ids for _m, ids in provider.calls if ids == [IDS["conflict"]]]
    assert single_calls, "repair should re-ask for just the failed ad"
    assert json.loads(_latest(conn, IDS["conflict"])["landing_comparison"])["status"] == "mismatch"


def test_deterministic_fields_override_model(conn):
    analyze.run(conn, MockProvider(responder_factory()), log=lambda *_: None)
    labels = json.loads(_latest(conn, IDS["plain"])["labels"])
    assert labels["format"] == "static_image" and labels["cta_intent"] == "shop"


def test_budget_cap_pauses_instead_of_overspending(conn):
    counts = analyze.run(conn, MockProvider(responder_factory(), cost_per_call=0.01),
                         budget_usd=0.0001, batch_size=1, log=lambda *_: None)
    assert counts.get("budget_paused") == 3 and counts["spent_usd"] == 0
    assert conn.execute("SELECT COUNT(*) FROM analyses").fetchone()[0] == 0


def test_rerun_uses_cache(conn):
    analyze.run(conn, MockProvider(responder_factory()), log=lambda *_: None)
    provider = MockProvider(responder_factory())
    counts = analyze.run(conn, provider, log=lambda *_: None)
    assert provider.calls == [] and counts["cached"] == 3


def test_policy_check_verdicts():
    bundle = {"payload": {"evidence": [{"id": "e1", "o": "ocr", "t": "$99 until Oct 15. Then it's $149. Ships November 30"}]},
              "id_map": {"e1": "ad:ocr:0"}}
    facts = {"deposit_price_usd": {"value": 99, "status": "confirmed"},
             "price_after_deadline_usd": {"value": 249, "status": "confirmed"},
             "ship_date": {"value": "2026-11-30", "status": "confirmed"}}
    got = {c["fact"]: c["verdict"] for c in policy.check(bundle, facts)}
    assert got["deposit_price_usd"] == "consistent"
    assert got["price_after_deadline_usd"] == "conflict"
    assert got["deadline"] == "unconfirmed"
    assert got["ship_date"] == "consistent"


def test_duplicate_creatives_are_analyzed_once(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    c = db.connect(":memory:")
    db.upsert_brand(c, id="nowa", name="Nowa", kind="own")
    ids = []
    for i in (1, 2):  # same creative reused in two ad sets
        ad_id, _ = db.upsert_ad(c, {"brand_id": "nowa", "source": "meta_own", "source_ad_id": str(i),
                                    "lane": "own", "headline": "Same creative", "cta_text": "Learn More",
                                    "format": "static_image", "raw_payload_key": "raw/x.json"})
        c.execute("INSERT INTO evidence (id, ad_id, origin, text_value, created_at) VALUES (?,?,?,?,?)",
                  (f"{ad_id}:ad_headline", ad_id, "ad_headline", "Same creative", db.now()))
        ids.append(ad_id)
    c.commit()
    provider = MockProvider(lambda model, ps: [_result(p["ad_id"], "no_landing_page", ["e1"]) for p in ps])
    analyze.run(c, provider, log=lambda *_: None)
    assert len(provider.calls) == 1 and len(provider.calls[0][1]) == 1
    rows = c.execute("SELECT ad_id, claims, landing_comparison, metadata FROM analyses ORDER BY ad_id").fetchall()
    assert [r["ad_id"] for r in rows] == ids
    # each duplicate cites its OWN evidence ids, not the representative's
    assert json.loads(rows[1]["landing_comparison"])["ad_evidence_ids"] == [f"{ids[1]}:ad_headline"]
    assert json.loads(rows[1]["metadata"])["analyzed_as"] == ids[0]


def test_policy_result_with_yaml_date_serializes():
    import datetime

    bundle = {"payload": {"evidence": [{"id": "e1", "o": "ocr", "t": "Ships November 30"}]},
              "id_map": {"e1": "ad:ocr:0"}}
    # yaml.safe_load("value: 2026-11-30") yields a datetime.date, as in the real offer_facts.yaml
    checks = policy.check(bundle, {"ship_date": {"value": datetime.date(2026, 11, 30), "status": "confirmed"}})
    assert checks[0]["verdict"] == "consistent"
    assert '"policy_value": "2026-11-30"' in db.dumps(checks)


def test_malformed_model_output_is_an_error_not_a_crash():
    from teardown.validate import validate

    bundle = {"id_map": {"e1": "ad:ad_copy"}, "payload": {"evidence": [{"id": "e1", "o": "ad_copy", "t": "x"}]},
              "landing_status": "ok"}
    bad = _result("ad", "match", ["e1"])
    bad["claims"] = [{"field": "price", "value": "99", "evidence_id": ["e1"]}]  # list, seen in a real run
    clean, errs = validate(bad, bundle)
    assert clean is None and errs
    assert validate("not an object", bundle)[0] is None
    assert validate({**_result("ad"), "landing_comparison": "oops"}, bundle)[0] is None


def test_price_rule_is_deterministic_and_two_sided():
    ev = [{"id": "a1", "origin": "ocr", "t": None, "text": "$99 until Oct 15. Then it's $149, for the same bundle."},
          {"id": "p1", "origin": "landing_dom", "text": "Founding preorder · $99 85 of 500 left Full package returns to $249"},
          {"id": "p2", "origin": "landing_dom", "text": "$99 today · $149 at retail"}]
    r = policy.price_rule(ev)
    assert r["verdict"] == "conflict" and r["ad_later"] == ["149"] and r["page_later"] == ["249"]
    assert r["ad_evidence"] == "a1" and r["page_evidence"] == "p1"
    assert any("retail" in m for m in r["page_other_mentions"])
    same = [{**ev[0]}, {"id": "p1", "origin": "landing_dom", "text": "then $149 after launch"}]
    assert policy.price_rule(same)["verdict"] == "consistent"
    assert policy.price_rule([{"id": "a", "origin": "ad_copy", "text": "Big feelings?"}])["verdict"] == "no_claim"
