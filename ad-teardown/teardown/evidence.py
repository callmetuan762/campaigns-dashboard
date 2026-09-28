"""M2 evidence: turn every ad into citable evidence rows (spec Step 4 + §3 `Evidence`).

Evidence IDs are stable (`<ad_id>:<origin>:<n>`), so M3 can cite them and a rebuild
produces the same IDs. Origins:
  ad_headline / ad_copy / ad_cta / ad_description — text the source reports
  ocr   — text read from the image or video frames (macOS Vision, local), with bbox + confidence
  asr   — speech transcript segments (faster-whisper, local), with start/end ms

Missing signals are recorded as `unavailable` in ads.metadata.signals and never skipped
silently. OCR and ASR results are cached per file checksum, so media shared by many ads are
read once.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from . import db

ROOT = Path(__file__).resolve().parent.parent
OCR_BIN = db.DATA_DIR / "bin" / "ocr"
CACHE = db.DATA_DIR / "cache"
OCR_VERSION = "apple-vision-accurate/1"
ASR_MODEL = "base.en"
ASR_VERSION = f"faster-whisper-{ASR_MODEL}/1"
MIN_OCR_CONF = 0.30


def _norm(s: str) -> str:
    return re.sub(r"\W+", " ", s.lower()).strip()


# ---------- OCR ----------

def ensure_ocr_bin() -> Path:
    if not OCR_BIN.exists():
        OCR_BIN.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["swiftc", "-O", str(ROOT / "tools" / "ocr.swift"), "-o", str(OCR_BIN)],
                       check=True, capture_output=True)
    return OCR_BIN


def ocr_many(keys: list[str]) -> dict[str, dict]:
    """OCR artifact keys (paths under data/). Cached per key, since keys embed the sha256."""
    out, todo = {}, []
    for k in keys:
        c = CACHE / "ocr" / (k.replace("/", "__") + ".json")
        if c.exists():
            out[k] = json.loads(c.read_text())
        elif (db.DATA_DIR / k).exists():
            todo.append(k)
        else:
            out[k] = {"lines": [], "error": "artifact missing"}
    if todo:
        proc = subprocess.run([str(ensure_ocr_bin())], input="\n".join(str(db.DATA_DIR / k) for k in todo),
                              capture_output=True, text=True, timeout=3600, check=False)
        by_path = {}
        for line in proc.stdout.splitlines():
            try:
                d = json.loads(line)
                by_path[d["path"]] = d
            except ValueError:
                continue
        for k in todo:
            d = by_path.get(str(db.DATA_DIR / k), {"lines": [], "error": "no ocr output"})
            c = CACHE / "ocr" / (k.replace("/", "__") + ".json")
            c.parent.mkdir(parents=True, exist_ok=True)
            c.write_text(json.dumps(d))
            out[k] = d
    return out


# ---------- ASR ----------

_model = None


def _whisper():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel

        _model = WhisperModel(ASR_MODEL, device="cpu", compute_type="int8")
    return _model


def asr(audio_key: str) -> dict:
    c = CACHE / "asr" / (audio_key.replace("/", "__") + ".json")
    if c.exists():
        return json.loads(c.read_text())
    path = db.DATA_DIR / audio_key
    if not path.exists():
        return {"segments": [], "error": "audio missing"}
    try:
        segments, info = _whisper().transcribe(str(path), vad_filter=True, beam_size=1)
        res = {"language": info.language, "language_probability": info.language_probability,
               "segments": [{"start_ms": int(s.start * 1000), "end_ms": int(s.end * 1000),
                             "text": s.text.strip(), "avg_logprob": s.avg_logprob,
                             "no_speech_prob": s.no_speech_prob} for s in segments]}
    except Exception as e:  # noqa: BLE001 — failed ASR marks the signal unavailable
        res = {"segments": [], "error": str(e)[:200]}
    c.parent.mkdir(parents=True, exist_ok=True)
    c.write_text(json.dumps(res))
    return res


# ---------- build ----------

def _ins(conn, ev_id, ad_id, origin, text, locator=None, conf=None, artifact=None, version=None):
    conn.execute(
        """INSERT OR REPLACE INTO evidence (id, ad_id, origin, text_value, artifact_key, locator,
             confidence, extractor_version, created_at) VALUES (?,?,?,?,?,?,?,?,?)""",
        (ev_id, ad_id, origin, text, artifact, db.dumps(locator) if locator else None, conf,
         version, db.now()))


def build(conn, brand_ids: list[str] | None = None, with_asr: bool = True, log=print) -> dict:
    q = "SELECT * FROM ads"
    params: list = []
    if brand_ids:
        q += f" WHERE brand_id IN ({','.join('?' * len(brand_ids))})"
        params = brand_ids
    ads = [dict(r) for r in conn.execute(q, params)]
    media = {}
    for m in conn.execute("SELECT * FROM media_assets"):
        media.setdefault(m["ad_id"], []).append(dict(m))

    # OCR everything up front in one batch (one process, fast).
    keys = []
    for a in ads:
        for m in media.get(a["id"], []):
            if m["processing_status"] == "done":
                keys += [f["key"] for f in json.loads(m["metadata"]).get("frames") or []]
    log(f"  OCR {len(set(keys))} distinct images/frames …")
    ocr_res = ocr_many(sorted(set(keys)))

    counters = {"ads": len(ads), "copy": 0, "ocr": 0, "asr": 0, "asr_files": 0}
    for i, a in enumerate(ads, start=1):
        ad_id = a["id"]
        conn.execute("DELETE FROM evidence WHERE ad_id=?", (ad_id,))
        n = 0
        for origin, field in (("ad_headline", "headline"), ("ad_copy", "body"),
                              ("ad_description", "description"), ("ad_cta", "cta_text")):
            if a[field]:
                _ins(conn, f"{ad_id}:{origin}", ad_id, origin, a[field], version="source")
                n += 1
        variants = json.loads(a["text_variants"] or "{}")
        for kind, origin in (("bodies", "ad_copy"), ("titles", "ad_headline")):
            for j, t in enumerate(variants.get(kind) or []):
                if j and t:  # index 0 is already the primary field
                    _ins(conn, f"{ad_id}:{origin}:v{j}", ad_id, origin, t, {"variant": j},
                         version="source")
                    n += 1
        for j, card in enumerate(variants.get("cards") or []):
            for fld in ("headline", "body", "description"):
                if card.get(fld):
                    _ins(conn, f"{ad_id}:ad_copy:card{j}:{fld}", ad_id, "ad_copy", card[fld],
                         {"card": j, "field": fld}, version="source")
                    n += 1
        counters["copy"] += n

        assets = media.get(ad_id, [])
        # No video asset = nothing to transcribe. That's not a gap, so it isn't "unavailable".
        has_video = any(m["kind"] == "video" for m in assets)
        signals = {"ocr": "unavailable", "asr": "unavailable" if has_video or not assets else "not_applicable",
                   "media": "none"}
        if assets:
            states = {m["processing_status"] for m in assets}
            signals["media"] = "ok" if "done" in states else "media_unavailable"
        seen, k = set(), 0
        copy_norm = _norm(" ".join(filter(None, [a["headline"], a["body"]])))
        for m in assets:
            if m["processing_status"] != "done":
                continue
            meta = json.loads(m["metadata"])
            for f in meta.get("frames") or []:
                r = ocr_res.get(f["key"]) or {}
                if r.get("error") is None:
                    signals["ocr"] = "none_found" if signals["ocr"] != "ok" else "ok"
                for line in r.get("lines") or []:
                    t, key = line["text"].strip(), _norm(line["text"])
                    if len(key) < 2 or line["confidence"] < MIN_OCR_CONF or key in seen:
                        continue  # repeated across frames, or noise
                    seen.add(key)
                    _ins(conn, f"{ad_id}:ocr:{k}", ad_id, "ocr", t,
                         {"asset_id": m["id"], "frame_t_ms": f.get("t_ms"), "bbox": line["bbox"],
                          "also_in_copy": key in copy_norm},
                         line["confidence"], f["key"], OCR_VERSION)
                    k += 1
                    signals["ocr"] = "ok"
            if with_asr and m["kind"] == "video":
                if not meta.get("has_audio"):
                    signals["asr"] = "no_audio" if signals["asr"] != "ok" else "ok"
                    continue
                if not meta.get("audio_key"):
                    continue
                res = asr(meta["audio_key"])
                counters["asr_files"] += 1
                if res.get("error"):
                    continue
                speech = [s for s in res["segments"] if s["no_speech_prob"] < 0.6 and s["text"]]
                signals["asr"] = "ok" if speech else ("none_found" if signals["asr"] != "ok" else "ok")
                for j, s in enumerate(speech):
                    _ins(conn, f"{ad_id}:asr:{m['id'].rsplit(':', 1)[-1]}:{j}", ad_id, "asr", s["text"],
                         {"asset_id": m["id"], "start_ms": s["start_ms"], "end_ms": s["end_ms"],
                          "language": res.get("language")},
                         round(min(1.0, max(0.0, 1 + s["avg_logprob"])), 3), meta["audio_key"],
                         ASR_VERSION)
                    counters["asr"] += 1
        counters["ocr"] += k
        conn.execute("UPDATE ads SET metadata=json_set(metadata, '$.signals', json(?)) WHERE id=?",
                     (db.dumps(signals), ad_id))
        if i % 100 == 0:
            conn.commit()
            log(f"  … {i}/{len(ads)} ads")
    conn.commit()
    return counters
