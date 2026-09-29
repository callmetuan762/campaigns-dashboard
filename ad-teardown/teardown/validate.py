"""Validate one model result against its bundle before anything is stored (spec §3–4).

Hard errors (they trigger the single repair attempt, then `analysis_failed`):
  unknown label values, cited ids that aren't in the bundle, a mismatch without evidence on
  BOTH sides, partial/mismatch without a severity.
Deterministic corrections (never errors, always flagged):
  a page that wasn't fetched OK can't produce any comparison but unverifiable/no_landing_page.
  model_notes are cut to 240 chars.
"""

from __future__ import annotations

import copy

from .taxonomy import COMPARISON_STATUS, LABELS, REVIEW_FLAGS, SEVERITY


def validate(result, bundle: dict) -> tuple[dict | None, list[str]]:
    """Never raises on model output. A wrong shape (a list where an id belongs, a string where
    an object belongs) is a validation error like any other, so it gets the one repair."""
    if not isinstance(result, dict):
        return None, [f"result is {type(result).__name__}, expected an object"]
    try:
        return _validate(result, bundle)
    except (TypeError, AttributeError, KeyError, ValueError) as e:
        return None, [f"malformed output ({type(e).__name__}: {str(e)[:120]})"]


def _validate(result: dict, bundle: dict) -> tuple[dict | None, list[str]]:
    errs: list[str] = []
    r = copy.deepcopy(result)
    ids = bundle["id_map"]
    origin = {i["id"]: i["o"] for i in bundle["payload"]["evidence"]}

    labels = r.get("labels") or {}
    for k, allowed in LABELS.items():
        if k == "cta_intent":
            continue
        if labels.get(k) not in allowed:
            errs.append(f"labels.{k}={labels.get(k)!r} not allowed")
    extra = set(labels) - set(LABELS)
    if extra:
        errs.append(f"unexpected labels {sorted(extra)}")

    for c in r.get("claims") or []:
        if not isinstance(c.get("evidence_id"), str) or c["evidence_id"] not in ids:
            errs.append(f"claim {c.get('field')} cites unknown evidence {c.get('evidence_id')!r}")

    lc = r.get("landing_comparison") or {}
    flags = [f for f in r.get("review_flags") or [] if f in REVIEW_FLAGS]
    status = lc.get("status")
    if status not in COMPARISON_STATUS:
        errs.append(f"landing status {status!r} not allowed")
    for side in ("ad_evidence_ids", "page_evidence_ids"):
        bad = [x for x in lc.get(side) or [] if not isinstance(x, str) or x not in ids]
        if bad:
            errs.append(f"{side} has unknown ids {bad}")
    page_ok = bundle["landing_status"] == "ok"
    if not page_ok and status in ("match", "partial", "mismatch"):
        # Never a mismatch against a page we couldn't read — correct it, don't fail it.
        lc.update(status="no_landing_page" if bundle["landing_status"] == "no_landing_page" else "unverifiable",
                  severity=None, page_evidence_ids=[],
                  reason=f"Landing page {bundle['landing_status']}; comparison not possible.")
        flags.append("landing_unavailable")
        status = lc["status"]
    if status == "mismatch":
        ad_side = [x for x in lc.get("ad_evidence_ids") or [] if origin.get(x) not in (None, "landing_dom")]
        page_side = [x for x in lc.get("page_evidence_ids") or [] if origin.get(x) == "landing_dom"]
        if not ad_side or not page_side:
            errs.append("mismatch needs >=1 ad evidence id and >=1 landing_dom evidence id")
    if status in ("partial", "mismatch") and lc.get("severity") not in SEVERITY:
        errs.append(f"{status} needs a severity")
    if status not in ("partial", "mismatch"):
        lc["severity"] = None

    if errs:
        return None, errs

    # Map short ids back to stored evidence ids.
    r["claims"] = [{**c, "evidence_id": ids[c["evidence_id"]]} for c in r.get("claims") or []]
    lc["ad_evidence_ids"] = [ids[x] for x in lc.get("ad_evidence_ids") or []]
    lc["page_evidence_ids"] = [ids[x] for x in lc.get("page_evidence_ids") or []]
    r["landing_comparison"] = lc
    r["review_flags"] = sorted(set(flags))
    r["model_notes"] = (r.get("model_notes") or "")[:240]
    return r, []
