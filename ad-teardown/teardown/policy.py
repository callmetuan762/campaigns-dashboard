"""Ad ↔ policy check for our own ads (plan §4 Step 7, third side of the comparison).

Deterministic on purpose: offer facts are legal/brand-risk claims, so they're matched by
explicit patterns against config/offer_facts.yaml, which only Amy edits. Verdicts:
  consistent   — the ad's claim equals a confirmed fact
  conflict     — the ad's claim differs from a confirmed fact      -> analysis needs_review
  unconfirmed  — the ad makes a claim whose fact isn't confirmed yet -> Amy's review queue
Never picks a winner, never edits copy.
"""

from __future__ import annotations

import re

import yaml

from .config import CONFIG_DIR

MONTHS = "jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec"
PATTERNS = {
    "deposit_price_usd": re.compile(r"\$\s?(99)\b"),
    "price_after_deadline_usd": re.compile(
        r"(?:then(?: it'?s)?|after [^$\n]{0,30}?|returns? to|goes (?:up )?to|jumps to|→)\s*\$\s?(\d{2,4})", re.IGNORECASE),
    "deadline": re.compile(rf"(?:until|through|thru|by|ends?|after|before)\s+((?:{MONTHS})[a-z]*\.?\s+\d{{1,2}})\b", re.IGNORECASE),
    "scarcity": re.compile(r"(?:first|only)\s+(\d{2,5})\b|\b(\d{2,5})\s+(?:reservations|spots|families|bundles|units)\b",
                           re.IGNORECASE),
    "ship_date": re.compile(rf"ships?\s+(?:in\s+|by\s+)?((?:{MONTHS})[a-z]*(?:\s+\d{{1,2}})?(?:,?\s+\d{{4}})?)", re.IGNORECASE),
    "refundable": re.compile(r"\b(fully refundable|refundable|money[- ]back)\b", re.IGNORECASE),
}


def load_facts() -> dict:
    path = CONFIG_DIR / "offer_facts.yaml"
    return yaml.safe_load(path.read_text())["facts"] if path.exists() else {}


def _norm_date(s: str) -> str:
    s = re.sub(r"\s+", " ", s.lower().replace(".", "")).strip()
    return s[:3] + s[s.find(" "):] if " " in s else s[:3]


def _same(fact: str, ad_value: str, policy_value) -> bool:
    if policy_value is None:
        return False
    if fact.endswith("_usd"):
        return float(ad_value) == float(policy_value)
    if fact in ("deadline", "ship_date"):
        pv = str(policy_value)
        if re.match(r"\d{4}-\d{2}-\d{2}", pv):  # 2026-11-30 -> "nov 30"
            months = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
            _y, m, d = pv.split("-")
            pv = f"{months[int(m) - 1]} {int(d)}"
        return _norm_date(ad_value).startswith(_norm_date(pv)[:6])
    if fact == "scarcity":
        return ad_value in str(policy_value)
    return True  # refundable: presence claim only


def check(bundle: dict, facts: dict | None = None) -> list[dict]:
    facts = facts if facts is not None else load_facts()
    out, seen = [], set()
    for item in bundle["payload"]["evidence"]:
        if item["o"] == "landing_dom":
            continue
        for fact, rx in PATTERNS.items():
            for m in rx.finditer(item["t"]):
                value = next(g for g in m.groups() if g) if m.groups() else m.group(0)
                key = (fact, value.lower())
                if key in seen:
                    continue
                seen.add(key)
                f = facts.get(fact) or {}
                if f.get("status") != "confirmed":
                    verdict = "unconfirmed"
                elif _same(fact, value, f.get("value")):
                    verdict = "consistent"
                else:
                    verdict = "conflict"
                out.append({"fact": fact, "ad_value": value, "evidence_id": bundle["id_map"][item["id"]],
                            "policy_value": f.get("value"), "policy_status": f.get("status", "missing"),
                            "verdict": verdict})
    return out


# ---------- rule-based ad ↔ page price check ----------
# Why: on 2026-09-29 the model split 15 near-identical Last Call ads into 5 mismatch / 5 partial
# / 5 match, because /pages/preorder carries two "later" prices ($249 "full package returns to"
# and $149 "at retail" / "was $149"). A price comparison must give the same verdict for the
# same claim, so it is done by rule, and the model's verdict is kept as a second opinion.
PAGE_LATER = re.compile(r"(?:returns? to|goes (?:up )?to|jumps to|then(?: it'?s)?|after [^$\n]{0,40}?)\s*\$\s?(\d{2,4})",
                        re.IGNORECASE)
PAGE_PRICE = re.compile(r"\$\s?(\d{2,4})(?:\.\d{2})?")


def price_rule(evidence: list[dict]) -> dict:
    """evidence: [{"id", "origin", "text"}] for one ad (ad side + landing_dom).
    verdict: conflict | consistent | no_claim | no_page_price."""
    ad_later, page_later, page_mentions = {}, {}, {}
    for e in evidence:
        text = e["text"] or ""
        if e["origin"] == "landing_dom":
            for m in PAGE_LATER.finditer(text):
                page_later.setdefault(m.group(1), e["id"])
            for m in PAGE_PRICE.finditer(text):
                ctx = text[max(0, m.start() - 30): m.end() + 30].replace("\n", " ").strip()
                page_mentions.setdefault(m.group(1), [])
                if ctx not in page_mentions[m.group(1)] and len(page_mentions[m.group(1)]) < 3:
                    page_mentions[m.group(1)].append(ctx)
        else:
            for m in PATTERNS["price_after_deadline_usd"].finditer(text):
                ad_later.setdefault(m.group(1), e["id"])
    if not ad_later:
        return {"verdict": "no_claim"}
    if not page_later:
        return {"verdict": "no_page_price", "ad_later": sorted(ad_later)}
    missing = [p for p in ad_later if p not in page_later]
    res = {"ad_later": sorted(ad_later), "page_later": sorted(page_later),
           "verdict": "conflict" if missing else "consistent"}
    if missing:
        p = missing[0]
        res.update(ad_evidence=ad_later[p], page_evidence=next(iter(page_later.values())),
                   # the page may still show the ad's number in another role (retail, "was")
                   page_other_mentions=page_mentions.get(p, []))
    return res
