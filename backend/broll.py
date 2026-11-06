"""Auto B-roll (R7) — replace talking-head stretches with stock footage.

For a rendered clip:
  1. Find "talking-head" stretches from the transcript: runs of segments
     with small gaps (>= ~4s of continuous speech). Those are the moments
     a creator would normally cut away to b-roll.
  2. Build a Pexels search query — Gemini suggests it from the clip's
     transcript (falls back to top keywords locally when no API key).
  3. Download the best portrait stock video (Pexels API, free tier).
  4. Overlay the stock footage full-frame over the talking-head windows,
     with a quick fade in/out; the original audio keeps playing underneath
     (classic b-roll: voice continues, visuals cut away).

Kept in a tiny module — requests is only imported when b-roll runs.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile

from gpu import encode_args

PEXELS_SEARCH = "https://api.pexels.com/videos/search"
# Pexels blocks requests without a User-Agent (403).
_PEXELS_UA = {"User-Agent": "Klipin-Broll/1.0"}
MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")

# Short, low-information words filtered from the local keyword fallback.
_STOP = set("""
a an and are as at be by for from has have he her his i in is it its me my
of on or our she so that the their them they this to was we were will with you
your ini itu dan yang di ke dari untuk dengan pada adalah akan tidak juga
saya kamu kita mereka dia kami anda aku nya ku mu lah kah pun saja semua ada
bisa harus sudah masih lebih sangat begitu seperti karena tetapi tapi atau
kalau jika ketika setelah sebelum tentang
""".split())

# Max seconds of stock per window (keeps renders fast & cheap).
MAX_BROLL_SECS = 5.0
MIN_STRETCH_SECS = 4.0  # a "talking head" stretch must be at least this long
MAX_WINDOWS = 2         # default number of b-roll inserts


def _stretches(segments: list, c0: float, c1: float) -> list:
    """Runs of in-clip segments with gap <= 1.5s -> [(start, end), ...]."""
    segs = [s for s in segments
            if s.get("end", 0) > c0 and s.get("start", 0) < c1]
    segs.sort(key=lambda s: s.get("start", 0))
    runs = []
    for s in segs:
        a = max(c0, float(s.get("start", 0)))
        b = min(c1, float(s.get("end", 0)))
        if not runs or a - runs[-1][1] > 1.5:
            runs.append([a, b])
        else:
            runs[-1][1] = max(runs[-1][1], b)
    return [(a, b) for a, b in runs if b - a >= MIN_STRETCH_SECS]


def _pick_windows(clip_dur: float, stretches: list, count: int) -> list:
    """Pick up to `count` overlay windows: longest stretches first, clamped
    to MAX_BROLL_SECS and inset slightly from the stretch edges."""
    picks = []
    for a, b in sorted(stretches, key=lambda r: r[1] - r[0], reverse=True):
        b = max(a, b)  # never inverted
        dur = min(b - a, MAX_BROLL_SECS)
        a2 = max(0.0, a + 0.3)
        b2 = a2 + dur
        if b2 > b - 0.3:
            b2 = min(b - 0.3, a2 + dur)
            a2 = max(0.0, b2 - dur)
        a2 = max(0.0, min(a2, b2))
        b2 = min(clip_dur, max(a2, b2))
        if b2 - a2 >= 1.0:
            picks.append((a2, b2))
        if len(picks) >= count:
            break
    return picks


def _suggest_query(transcript_text: str, hook: str, title: str, log=None) -> str:
    """Pexels search query: Gemini first, local keyword fallback otherwise."""
    key = os.environ.get("GEMINI_API_KEY", "")
    if key and transcript_text.strip():
        try:
            from google import genai
            prompt = (
                "You pick ONE stock-video search query for Pexels (English, "
                "2-5 words, concrete visuals, no brands/names) that best "
                "illustrates this clip's topic. Return STRICT JSON only: "
                '{"query": "..."}'
            )
            client = genai.Client(api_key=key)
            resp = client.models.generate_content(
                model=MODEL,
                contents=[prompt, f"TITLE: {title[:120]}\nHOOK: {hook[:200]}"
                          f"\nTRANSCRIPT SNIPPET:\n{transcript_text[:1200]}"],
            )
            text = (resp.text or "").strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            q = json.loads(text).get("query", "")
            if q and len(q) < 60:
                log and log(f"🎬 Query (Gemini): {q}")
                return q
        except Exception as e:  # noqa: BLE001 — fall back to local
            log and log(f"⚠️ Gemini query failed ({e}), using local keywords")

    # Local fallback: most frequent non-stopword tokens, ties broken by
    # word length (longer tokens are usually more specific search terms).
    words = re.findall(r"[A-Za-zÀ-ž]{4,}", transcript_text.lower())
    freq: dict = {}
    for w in words:
        if w not in _STOP and not w.isdigit():
            freq[w] = freq.get(w, 0) + 1
    top = sorted(freq.items(), key=lambda kv: (-kv[1], -len(kv[0])))
    q = " ".join(w for w, _ in top[:3])
    if q.strip():
        log and log(f"🎬 Query (local keywords): {q}")
        return q
    return "business meeting"


def _fetch_stock(query: str, out_path: str, log=None) -> str:
    """Search Pexels and download the best portrait mp4 to `out_path`."""
    import requests

    key = os.environ.get("PEXELS_API_KEY", "")
    if not key:
        raise RuntimeError(
            "PEXELS_API_KEY not set — add it to backend/.env to use b-roll"
        )
    r = requests.get(
        PEXELS_SEARCH,
        params={"query": query, "orientation": "portrait",
                "per_page": 4, "size": "medium"},
        headers={"Authorization": key, **_PEXELS_UA},
        timeout=20,
    )
    if r.status_code != 200:
        raise RuntimeError(f"Pexels search failed ({r.status_code}): {r.text[:200]}")
    videos = (r.json().get("videos") or [])
    if not videos:
        raise RuntimeError(f"No stock footage found for query: {query}")

    # Prefer an mp4 >= 720p with portrait-ish dimensions.
    best = None
    for v in videos:
        files = v.get("video_files") or []
        for f in sorted(files, key=lambda f: -(f.get("width") or 0)):
            if (f.get("file_type") == "video/mp4"
                    and (f.get("width") or 0) >= 640):
                best = f
                break
        if best:
            break
    if not best:
        raise RuntimeError("Pexels returned no usable mp4 files")
    url = best.get("link")
    if not url:
        raise RuntimeError("Pexels video entry missing file link")
    log and log(f"🎬 Downloading stock: {url}")
    dl = requests.get(url, headers=_PEXELS_UA, timeout=60, stream=True)
    dl.raise_for_status()
    with open(out_path, "wb") as fh:
        for chunk in dl.iter_content(1 << 16):
            fh.write(chunk)
    if os.path.getsize(out_path) == 0:
        raise RuntimeError("Downloaded stock video is empty")
    return out_path


def _overlay(src: str, stock: str, out_path: str, windows: list,
             log=None) -> str:
    """Overlay `stock` full-frame during each [start, end] window of `src`.
    Original audio continues untouched underneath."""
    w, h = 1080, 1920  # rendered clips are 9:16; keep it simple
    parts = []
    for i, (a, b) in enumerate(windows):
        dur = b - a
        parts.append(
            f"[{i + 1}:v]trim=duration={dur:.2f},setpts=PTS-STARTPTS,"
            f"scale={w}:{h}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h},fps=30,format=yuv420p,"
            f"fade=in:st=0:d=0.25,fade=out:st={max(0.0, dur - 0.25):.2f}:d=0.25,"
            f"setpts=PTS-STARTPTS+{a:.2f}/TB[br{i}]"
        )
    overlay_expr = "[0:v]"
    for i, _ in enumerate(windows):
        overlay_expr += f"[br{i}]overlay=0:0:enable='between(t,{windows[i][0]:.2f},{windows[i][1]:.2f})'"
    overlay_expr += "[v]"

    cmd = ["ffmpeg", "-y", "-i", src]
    for _ in windows:
        cmd += ["-i", stock]
    cmd += ["-filter_complex", ";".join(parts + [overlay_expr]),
            "-map", "[v]", "-map", "0:a",
            "-vsync", "vfr",
            *encode_args(crf=23, preset="veryfast", log=None),
            "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
            out_path]
    log and log(f"🎬 Overlaying {len(windows)} b-roll window(s)...")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"B-roll render failed: {r.stderr[-500:]}")
    return out_path


def run_broll(job_id: str, clip_idx: int, out_root: str = "",
              count: int = MAX_WINDOWS, sink=None) -> dict:
    """Job entrypoint: add auto b-roll to one rendered clip."""
    import db

    count = max(1, min(int(count or MAX_WINDOWS), 4))
    project = db.get_project(job_id)
    if not project:
        raise RuntimeError("Project not found")
    clips = db.get_clips(job_id)
    if clip_idx < 0 or clip_idx >= len(clips):
        raise RuntimeError(f"Clip index out of range: {clip_idx}")

    log = sink or (lambda m: None)
    src = os.path.join(out_root, job_id, f"clip_{clip_idx + 1}.mp4")
    if not os.path.exists(src):
        raise RuntimeError("Clip video no longer available on disk")
    out = os.path.join(out_root, job_id, f"clip_{clip_idx + 1}_broll.mp4")

    clip = clips[clip_idx]
    c0 = float(clip.get("start", 0))
    c1 = float(clip.get("end", c0 + 60))
    clip_dur = c1 - c0
    segs = (project.get("transcript") or {}).get("segments") or []
    stretches = _stretches(segs, c0, c1)
    # stretches are absolute (source timeline); the rendered clip is 0-based,
    # so convert to clip-relative before picking overlay windows.
    rel_stretches = [(max(0.0, a - c0), min(clip_dur, b - c0))
                     for a, b in stretches]
    windows = _pick_windows(clip_dur, rel_stretches, count)

    if not windows:
        # fall back to a middle window so the effect still does something
        if clip_dur >= 6:
            mid = clip_dur * 0.4
            end = min(clip_dur - 0.5, mid + MAX_BROLL_SECS)
            if end > mid:
                windows = [(mid, end)]
    if not windows:
        raise RuntimeError("Clip too short for b-roll (need >= ~6s of speech)")

    # Build a search query from the in-clip transcript.
    in_segs = [s for s in segs if s.get("end", 0) > c0 and s.get("start", 0) < c1]
    text = " ".join(s.get("text", "") for s in in_segs[:40])
    query = _suggest_query(text, clip.get("hook", ""), clip.get("title", ""), log)

    with tempfile.TemporaryDirectory() as tmp:
        stock = os.path.join(tmp, "stock.mp4")
        log(f"🔎 Searching Pexels: \"{query}\"")
        _fetch_stock(query, stock, log)
        _overlay(src, stock, out, windows, log)
        inserts = os.path.join(out_root, job_id, "inserts")
        os.makedirs(inserts, exist_ok=True)
        stock_saved = os.path.join(inserts, f"auto_broll_{clip_idx + 1}.mp4")
        shutil.copy2(stock, stock_saved)

    # Keep Studio's professional timeline in sync with the footage that was
    # actually generated. Existing manual layers are preserved; only slots
    # from an earlier auto B-roll run are replaced.
    existing = [layer for layer in (clip.get("layer_plan") or [])
                if not str(layer.get("id", "")).startswith("auto-broll-")]
    video_url = f"/videos/{job_id}/clip_{clip_idx + 1}_broll.mp4"
    stock_url = f"/videos/{job_id}/inserts/auto_broll_{clip_idx + 1}.mp4"
    auto_layers = [{
        "id": f"auto-broll-{clip_idx}-{i}",
        "kind": "broll",
        "label": f"Auto B-roll · {query}",
        "text": "",
        "start": round(a, 2), "end": round(b, 2),
        "x": 0.5, "y": 0.5, "width": 1.0, "opacity": 1.0,
        "visible": True, "locked": False, "color": "#FFFFFF",
        "asset_url": stock_url, "media_path": stock_saved,
    } for i, (a, b) in enumerate(windows)]
    db.update_clip(job_id, clip_idx,
                   layer_plan=json.dumps(existing + auto_layers))

    log(f"✅ B-roll added to clip {clip_idx + 1} "
        f"({len(windows)} window(s), query \"{query}\")")
    return {
        "clip_idx": clip_idx,
        "windows": [[round(a, 2), round(b, 2)] for a, b in windows],
        "query": query,
        "video_url": video_url,
        "layers": auto_layers,
        "size_mb": round(os.path.getsize(out) / 1e6, 1),
    }
