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
