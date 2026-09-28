"""Taxonomy (spec §3) + Nowa extensions. Versioned: any change here bumps TAXONOMY_VERSION,
which makes every ad re-analyze on the next run (old analyses are kept, never overwritten).

Every inferred field has `unknown`. The model may not invent labels: validate() rejects them.
"""

TAXONOMY_VERSION = "tx-1.0"

LABELS: dict[str, list[str]] = {
    "hook_type": ["question", "problem", "benefit", "curiosity", "social_proof", "urgency",
                  "comparison", "demonstration", "story", "authority", "other", "unknown"],
    "offer_type": ["discount", "free_trial", "free_resource", "bundle", "financing", "shipping",
                   "preorder_deposit", "none_explicit", "other", "unknown"],
    "cta_intent": ["learn", "shop", "sign_up", "download", "book", "contact", "subscribe",
                   "other", "unknown"],
    "awareness_stage": ["unaware", "problem_aware", "solution_aware", "product_aware",
                        "most_aware", "unknown"],
    "driver": ["save_money", "save_time", "convenience", "status", "safety", "health",
               "belonging", "novelty", "performance", "other", "unknown"],
    "funnel_stage": ["top", "middle", "bottom", "unknown"],
    # Nowa extensions (plan §4 Step 6)
    "pain_point": ["big_feelings", "routines", "screen_time", "independence", "learning", "sleep",
                   "connection", "none_explicit", "other", "unknown"],
    "proof_type": ["testimonial", "stat", "expert", "ugc", "demo", "press", "none", "unknown"],
}

DEFINITIONS = {
    "hook_type": "What the first line / first 3 seconds use to stop the scroll.",
    "offer_type": "The explicit offer. preorder_deposit = pay a deposit now for a product that ships later.",
    "cta_intent": "What the button asks the viewer to do.",
    "awareness_stage": "Schwartz: how much the copy assumes the viewer already knows (problem, solution, this product).",
    "driver": "The main motive the ad appeals to.",
    "funnel_stage": "Inferred from message + CTA only, never from targeting.",
    "pain_point": "The parent/child problem the ad names. none_explicit if it names none.",
    "proof_type": "Main form of evidence shown. ugc = creator/parent talking to camera.",
}

CRITICAL = ("hook_type", "offer_type", "awareness_stage", "funnel_stage")
COMPARISON_STATUS = ["match", "partial", "mismatch", "unverifiable", "no_landing_page"]
SEVERITY = ["low", "medium", "high"]
REVIEW_FLAGS = ["landing_unavailable", "visual_not_analyzed", "claim_not_on_page",
                "price_conflict", "date_conflict", "scarcity_conflict", "product_conflict",
                "policy_sensitive_claim", "low_confidence", "other"]

# CTA button text -> intent. Deterministic: the model never labels what the source states.
CTA_INTENT = {
    "shop now": "shop", "order now": "shop", "buy now": "shop", "get offer": "shop",
    "see menu": "shop", "shop": "shop", "pre-order": "shop", "preorder now": "shop",
    "learn more": "learn", "see more": "learn", "watch more": "learn", "read more": "learn",
    "sign up": "sign_up", "apply now": "sign_up", "get started": "sign_up", "join": "sign_up",
    "download": "download", "install now": "download", "install": "download", "use app": "download",
    "play game": "download", "get app": "download",
    "book now": "book", "book travel": "book", "get quote": "contact",
    "contact us": "contact", "send message": "contact", "call now": "contact",
    "whatsapp": "contact", "send whatsapp message": "contact",
    "subscribe": "subscribe", "get showtimes": "other", "listen now": "learn",
}


def cta_intent(cta_text: str | None) -> str | None:
    return CTA_INTENT.get((cta_text or "").strip().lower()) if cta_text else None


def output_schema(ad_ids: list[str]) -> dict:
    """JSON schema for one batch. Enums are hard constraints; ad_id is pinned to the batch."""
    label_props = {k: {"type": "string", "enum": v} for k, v in LABELS.items() if k != "cta_intent"}
    conf_props = {k: {"type": "number", "minimum": 0, "maximum": 1} for k in [*label_props, "landing_comparison"]}
    ev_list = {"type": "array", "items": {"type": "string"}}
    result = {
        "type": "object",
        "additionalProperties": False,
        "required": ["ad_id", "labels", "offer_detail", "claims", "landing_comparison",
                     "confidence", "review_flags", "model_notes"],
        "properties": {
            "ad_id": {"type": "string", "enum": ad_ids},
            "labels": {"type": "object", "additionalProperties": False,
                       "required": list(label_props), "properties": label_props},
            "offer_detail": {"type": "object", "additionalProperties": False,
                             "required": ["price", "currency", "discount_percent", "trial_days",
                                          "deadline", "later_price", "scarcity"],
                             "properties": {
                                 "price": {"type": ["number", "null"]},
                                 "currency": {"type": ["string", "null"]},
                                 "discount_percent": {"type": ["number", "null"]},
                                 "trial_days": {"type": ["integer", "null"]},
                                 "deadline": {"type": ["string", "null"]},
                                 "later_price": {"type": ["number", "null"]},
                                 "scarcity": {"type": ["string", "null"]}}},
            "claims": {"type": "array", "items": {
                "type": "object", "additionalProperties": False,
                "required": ["field", "value", "evidence_id"],
                "properties": {"field": {"type": "string"}, "value": {"type": "string"},
                               "evidence_id": {"type": "string"}}}},
            "landing_comparison": {
                "type": "object", "additionalProperties": False,
                "required": ["status", "severity", "reason", "ad_evidence_ids", "page_evidence_ids"],
                "properties": {"status": {"type": "string", "enum": COMPARISON_STATUS},
                               "severity": {"type": ["string", "null"], "enum": [*SEVERITY, None]},
                               "reason": {"type": "string"},
                               "ad_evidence_ids": ev_list, "page_evidence_ids": ev_list}},
            "confidence": {"type": "object", "additionalProperties": False,
                           "required": list(conf_props), "properties": conf_props},
            "review_flags": {"type": "array", "items": {"type": "string", "enum": REVIEW_FLAGS}},
            "model_notes": {"type": "string", "maxLength": 240},
        },
    }
    return {"type": "object", "additionalProperties": False, "required": ["results"],
            "properties": {"results": {"type": "array", "items": result}}}
