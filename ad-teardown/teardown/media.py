"""M2 media: download → check → derive analysis artifacts (spec Step 4).

- Images: sniffed by magic bytes (not by URL/extension), capped at 20 MB, checksummed,
  resized to a 1280 px analysis copy, perceptual-hashed for creative grouping.
- Videos: capped at 100 MB, analysed over the first 120 s only. We extract the poster, the
  first 3 s at 1 s steps and scene-change frames (8 frames max in total), plus 16 kHz mono
  audio for ASR.
- Rights: our own videos are kept. For competitor videos the full file is **deleted** after
  derivation. Only the frames, audio and checksum stay (internal analysis only, Amy
  2026-09-28).
- Identical files (same sha256) are processed once and shared across ads.

Competitor media URLs are signed fbcdn links that expire within hours to days, so run this
right after a crawl. An expired link becomes `media_unavailable`, not a crash.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

from . import db

MEDIA_DIR = db.DATA_DIR / "media"
EXTRACTOR_VERSION = "media/0.1"
MAX_IMAGE = 20 * 1024 * 1024
MAX_VIDEO = 100 * 1024 * 1024
MAX_VIDEO_S = 120
MAX_FRAMES = 8
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126 Safari/537.36"


def sniff(head: bytes) -> str | None:
    if head[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if head[4:8] == b"ftyp":
        return "video/mp4"
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return "video/webm"
    return None


def download(url: str, kind: str, dest_dir: Path) -> dict:
    """Stream to disk with a hard size cap; the MIME type comes from the bytes."""
    cap = MAX_IMAGE if kind == "image" else MAX_VIDEO
    dest_dir.mkdir(parents=True, exist_ok=True)
    tmp = dest_dir / f".part-{hashlib.md5(url.encode()).hexdigest()}"
    h, size = hashlib.sha256(), 0
    try:
        with requests.get(url, stream=True, timeout=(10, 60), headers={"User-Agent": UA}) as r:
            if r.status_code in (403, 404, 410):
                return {"status": "media_unavailable", "error": f"http {r.status_code} (expired link?)"}
            r.raise_for_status()
            with tmp.open("wb") as f:
                head = b""
                for chunk in r.iter_content(256 * 1024):
                    if not head:
                        head = chunk[:16]
                    size += len(chunk)
                    if size > cap:
                        raise ValueError(f"over size cap {cap // 1024 // 1024} MB")
                    h.update(chunk)
                    f.write(chunk)
    except (requests.RequestException, ValueError) as e:
        tmp.unlink(missing_ok=True)
        return {"status": "error", "error": str(e)[:200]}
    mime = sniff(head)
    if not mime or not mime.startswith(kind):
        tmp.unlink(missing_ok=True)
        return {"status": "error", "error": f"unexpected content {mime or 'unknown'} for {kind}"}
    sha = h.hexdigest()
    ext = mime.split("/")[1].replace("jpeg", "jpg")
    final = dest_dir / f"{sha}.{ext}"
    if final.exists():
        tmp.unlink()
    else:
        tmp.rename(final)
    return {"status": "ok", "path": final, "sha256": sha, "mime": mime, "bytes": size}


def derive_image(path: Path) -> dict:
    import imagehash
    from PIL import Image

    out = path.with_name(path.stem + ".analysis.jpg")
    with Image.open(path) as im:
        im = im.convert("RGB")
        w, h = im.size
        if not out.exists():
            copy = im.copy()
            copy.thumbnail((1280, 1280))
            copy.save(out, "JPEG", quality=88)
        return {"width": w, "height": h, "phash": str(imagehash.phash(im)),
                "frames": [{"t_ms": None, "key": _key(out)}]}


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=300, check=False)


def derive_video(path: Path) -> dict:
    probe = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type",
                  "-of", "json", str(path)])
    info = json.loads(probe.stdout or "{}")
    duration = float((info.get("format") or {}).get("duration") or 0)
    has_audio = any(s.get("codec_type") == "audio" for s in info.get("streams") or [])
    fdir = path.with_name(path.stem + ".frames")
    fdir.mkdir(exist_ok=True)
    window = min(duration, MAX_VIDEO_S) if duration else MAX_VIDEO_S

    frames = []
    for t in (0, 1, 2):  # poster + first 3 seconds at 1 s steps
        if duration and t >= duration:
            break
        out = fdir / f"t{t * 1000:06d}.jpg"
        if not out.exists():
            _run(["ffmpeg", "-v", "error", "-y", "-ss", str(t), "-i", str(path), "-frames:v", "1",
                  "-vf", "scale='min(720,iw)':-2", str(out)])
        if out.exists():
            frames.append({"t_ms": t * 1000, "key": _key(out), "why": "poster" if t == 0 else "opening"})

    # Scene changes after the opening, until the 8-frame cap.
    budget = MAX_FRAMES - len(frames)
    if budget > 0 and window > 3:
        res = _run(["ffmpeg", "-v", "info", "-ss", "3", "-t", str(window - 3), "-i", str(path),
                    "-vf", "select='gt(scene,0.30)',showinfo,scale='min(720,iw)':-2",
                    "-vsync", "vfr", "-frames:v", str(budget), str(fdir / "scene%02d.jpg")])
        times = [float(x) for x in re.findall(r"pts_time:([\d.]+)", res.stderr)][:budget]
        for i, t in enumerate(times, start=1):
            out = fdir / f"scene{i:02d}.jpg"
            if out.exists():
                ms = int((t + 3) * 1000)
                final = fdir / f"t{ms:06d}.jpg"
                out.rename(final)
                frames.append({"t_ms": ms, "key": _key(final), "why": "scene_change"})

    audio = None
    if has_audio:
        wav = path.with_name(path.stem + ".wav")
        if not wav.exists():
            _run(["ffmpeg", "-v", "error", "-y", "-t", str(MAX_VIDEO_S), "-i", str(path), "-vn",
                  "-ac", "1", "-ar", "16000", str(wav)])
        audio = _key(wav) if wav.exists() else None
    return {"duration_ms": int(duration * 1000) if duration else None, "has_audio": has_audio,
            "truncated_to_s": MAX_VIDEO_S if duration > MAX_VIDEO_S else None,
            "frames": frames, "audio_key": audio}


def _key(p: Path) -> str:
    return str(p.relative_to(db.DATA_DIR))


def _process_one(row: dict) -> dict:
    kind, lane = row["kind"], row["lane"]
    url = row["source_url"]
    if not url:
        return {"id": row["id"], "status": "media_unavailable", "error": "no url from source"}
    got = download(url, kind, MEDIA_DIR / row["brand_id"])
    if got["status"] != "ok":
        return {"id": row["id"], **got}
    try:
        derived = derive_image(got["path"]) if kind == "image" else derive_video(got["path"])
    except Exception as e:  # noqa: BLE001 — a corrupt file marks this asset, not the run
        return {"id": row["id"], "status": "error", "error": f"derive: {str(e)[:160]}",
                "sha256": got["sha256"]}
    stored = _key(got["path"])
    if kind == "video" and lane == "competitor":
        got["path"].unlink(missing_ok=True)  # rights: keep derived artifacts only
        stored = None
    return {"id": row["id"], "status": "done", "sha256": got["sha256"], "mime": got["mime"],
            "storage_key": stored, "derived": derived}


def process(conn, brand_ids: list[str] | None = None, lane: str | None = None,
            workers: int = 8, retry_errors: bool = False, log=print) -> dict:
    states = ("pending", "error") if retry_errors else ("pending",)
    q = (f"SELECT m.id, m.kind, m.source_url, a.brand_id, a.lane FROM media_assets m "
         f"JOIN ads a ON a.id = m.ad_id WHERE m.processing_status IN ({','.join('?' * len(states))})")
    params: list = list(states)
    if brand_ids:
        q += f" AND a.brand_id IN ({','.join('?' * len(brand_ids))})"
        params += brand_ids
    if lane:
        q += " AND a.lane = ?"
        params.append(lane)
    rows = [dict(r) for r in conn.execute(q, params)]

    # One download per distinct URL; results are fanned back out to every asset row using it.
    by_url: dict[str, list[dict]] = {}
    for r in rows:
        by_url.setdefault(r["source_url"] or f"none:{r['id']}", []).append(r)
    log(f"  {len(rows)} assets, {len(by_url)} distinct urls")

    counters = {"done": 0, "media_unavailable": 0, "error": 0}
    groups = list(by_url.values())
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for n, (group, res) in enumerate(zip(groups, pool.map(_process_one, [g[0] for g in groups]),
                                             strict=True), start=1):
            for r in group:
                _store(conn, r["id"], res)
                counters[res["status"]] += 1
            if n % 100 == 0:
                conn.commit()
                log(f"  … {n}/{len(groups)} urls")
    conn.commit()
    return counters


def _store(conn, asset_id: str, res: dict):
    d = res.get("derived") or {}
    meta_patch = {k: v for k, v in {
        "error": res.get("error"), "frames": d.get("frames"), "audio_key": d.get("audio_key"),
        "has_audio": d.get("has_audio"), "truncated_to_s": d.get("truncated_to_s"),
        "width": d.get("width"), "height": d.get("height"),
        "extractor_version": EXTRACTOR_VERSION}.items() if v is not None}
    conn.execute(
        """UPDATE media_assets SET processing_status=?, sha256=coalesce(?, sha256),
             mime_type=coalesce(?, mime_type), storage_key=?, phash=coalesce(?, phash),
             duration_ms=coalesce(?, duration_ms),
             metadata=json_patch(metadata, ?) WHERE id=?""",
        (res["status"], res.get("sha256"), res.get("mime"), res.get("storage_key"),
         d.get("phash"), d.get("duration_ms"), json.dumps(meta_patch), asset_id))
