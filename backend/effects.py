"""AI video effects — Submagic/QuClippers-style emphasis effects, free.

QuClippers asked Gemini to emit raw FFmpeg filter graphs (error-prone).
Klipin's approach: Gemini picks ONE effect + intensity from a curated,
locally-implemented set, then a safe, pre-tested FFmpeg filter runs. No
arbitrary filter code from the model ever reaches the command line.

Effects:
  glitch     — RGB-split + noise bursts at emphasis moments
  zoom       — punch-in zoom pulses at emphasis moments
  pulse      — brightness/contrast pulse at emphasis moments
  shake      — quick horizontal shake at emphasis moments
  vignette   — cinematic dark vignette + slight contrast (whole clip)
  color_grade— teal-and-orange look (whole clip)
  b_w        — black & white with boosted contrast (whole clip)
"""

import json
import os
import subprocess

from gpu import encode_args
from video import get_duration, run

EFFECTS = ("glitch", "zoom", "pulse", "shake", "vignette", "color_grade", "b_w")

EFFECT_LABELS = {
    "glitch": "Glitch / RGB split",
    "zoom": "Punch-in zoom",
    "pulse": "Brightness pulse",
    "shake": "Camera shake",
    "vignette": "Cinematic vignette",
    "color_grade": "Teal & orange grade",
    "b_w": "Black & white",
}


def _extract_json(text: str) -> dict:
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1].lstrip("json").strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    start, end = t.find("{"), t.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(t[start:end + 1])
        except json.JSONDecodeError:
            pass
    return {}


def suggest_effect(title: str, hook: str, transcript_text: str,
                   log=None) -> dict:
    """Ask Gemini which effect fits the clip. Returns {effect, intensity}
    with intensity in 1..3 (default 2). Never fails: falls back to 'zoom'."""
    from google import genai

    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        return {"effect": "zoom", "intensity": 2}
    client = genai.Client(api_key=key)
    model = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")
    prompt = (
        "Pick the single best video EFFECT for this short vertical clip.\n"
        f"Available: {', '.join(EFFECTS)}.\n"
        "- glitch/zoom/pulse/shake = emphasis effects for energetic clips\n"
        "- vignette/color_grade/b_w = cinematic looks for calm/serious clips\n"
        "Return STRICT JSON: {\"effect\": \"one of the names\", "
        "\"intensity\": 1|2|3} (3 = strongest).\n\n"
        f"TITLE: {title[:120]}\nHOOK: {hook[:200]}\n"
        f"TRANSCRIPT SNIPPET: {transcript_text[:800]}"
    )
    try:
        resp = client.models.generate_content(
            model=model, contents=prompt,
            config={"response_mime_type": "application/json"},
        )
        data = _extract_json(resp.text)
    except Exception as e:  # noqa: BLE001
        log and log(f"⚠️ effect suggestion failed ({e}) — using zoom")
        return {"effect": "zoom", "intensity": 2}
    eff = data.get("effect", "zoom")
    if eff not in EFFECTS:
        eff = "zoom"
    try:
        intensity = max(1, min(3, int(data.get("intensity", 2))))
    except (TypeError, ValueError):
        intensity = 2
    return {"effect": eff, "intensity": intensity}


def _emphasis_moments(transcript_text: str, clip_dur: float,
                      count: int = 3) -> list:
    """Approximate emphasis timestamps (numbers, exclamations) spread over
    the clip — same heuristic family as sfx.py, so effects land on the
    energetic beats without needing word-level timestamps."""
    import re

    beats = []
    for m in re.finditer(r"\d+|[!?]|wow|luar biasa|gila|amazing|whoa|stop|yes",
                         transcript_text, re.IGNORECASE):
        beats.append(m.start())
    if not beats:
        beats = [0]
    # Map character offsets to a time in [0, clip_dur).
    total_len = max(1, len(transcript_text))
    times = sorted((b / total_len) * clip_dur for b in beats)
    # De-duplicate (min 0.8s apart) and cap the count.
    out = []
    for t in times:
        if not out or t - out[-1] >= 0.8:
            out.append(t)
        if len(out) >= count:
            break
    return out or [clip_dur * 0.3]


def apply_effect(input_path: str, out_path: str, effect: str = "zoom",
                 intensity: int = 2, transcript_text: str = "",
                 log=None) -> str:
    """Apply `effect` to a clip. Returns out_path."""
    if effect not in EFFECTS:
        effect = "zoom"
    intensity = max(1, min(3, int(intensity)))
    dur = get_duration(input_path)
    if dur <= 0:
        dur = 5.0

    if effect == "vignette":
        vf = ("vignette=PI/4.5,eq=contrast=1.12:brightness=-0.02"
              if intensity <= 2 else
              "vignette=PI/3.5,eq=contrast=1.2:brightness=-0.04")
    elif effect == "color_grade":
        vf = ("colorbalance=rs=-0.15:bs=0.12:rm=-0.08:bm=0.08,"
              "eq=saturation=1.15:contrast=1.08")
    elif effect == "b_w":
        vf = "hue=s=0,eq=contrast=1.25:brightness=0.01"
    elif effect == "zoom":
        moments = _emphasis_moments(transcript_text, dur)
        vf = _zoom_pulse_vf(moments, dur, intensity)
    elif effect == "pulse":
        moments = _emphasis_moments(transcript_text, dur)
        vf = _pulse_vf(moments, intensity)
    elif effect == "shake":
        moments = _emphasis_moments(transcript_text, dur)
        vf = _shake_vf(moments, intensity)
    else:  # glitch
        moments = _emphasis_moments(transcript_text, dur)
        vf = _glitch_vf(moments, intensity)

    log and log(f"✨ Applying effect: {effect} (intensity {intensity})")
    run([
        "ffmpeg", "-y", "-i", input_path,
        "-vf", vf,
        *encode_args(log=log),
        "-c:a", "copy", "-movflags", "+faststart",
        out_path,
    ], log)
    return out_path


def _zoom_pulse_vf(moments: list, dur: float, intensity: int) -> str:
    """zoompan pulses: zoom to ~1.12-1.3 for 0.5s around each moment."""
    fps = 30
    n = max(1, int(dur * fps))
    # Build an expression zooming per frame index.
    z = "1"
    for t in moments:
        f0 = max(0, int(t * fps) - 4)
        f1 = min(n, int(t * fps) + int(0.5 * fps))
        zmax = 1.10 + 0.07 * intensity
        z = f"if(between(on,{f0},{f1}),{zmax},{z})"
    vf = (
        f"scale=iw*1.4:ih*1.4,"
        f"zoompan=z='{z}':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"s=iw/1.4*2:ih/1.4*2:fps={fps}"
    )
    return vf


def _pulse_vf(moments: list, intensity: int) -> str:
    """Brightness pulse (eq brightness) with enable=between on the moments."""
    boost = 0.18 + 0.08 * intensity
    expr = []
    for t in moments:
        expr.append(f"between(t,{t - 0.05:.2f},{t + 0.35:.2f})")
    cond = "+".join(expr) if expr else "0"
    return f"eq=brightness='if({cond},{boost},0)':eval=frame"


def _shake_vf(moments: list, intensity: int) -> str:
    """Quick horizontal jitter at moments (crop + x offset)."""
    amp = 12 + 8 * intensity
    expr = []
    for t in moments:
        expr.append(f"between(t,{t - 0.05:.2f},{t + 0.3:.2f})")
    cond = "+".join(expr) if expr else "0"
    return (
        f"crop=iw-{amp * 2}:ih:x='(iw-iw+{amp}*2)*sin(t*90)*0.5*"
        f"if({cond},1,0)':y=0,scale={1920}:{1920}"
    )


def _glitch_vf(moments: list, intensity: int) -> str:
    """RGB-split glitch bursts: temporary red/blue channel offset."""
    off = 8 + 6 * intensity
    expr = []
    for t in moments:
        expr.append(f"between(t,{t - 0.05:.2f},{t + 0.28:.2f})")
    cond = "+".join(expr) if expr else "0"
    return (
        f"split=3[r][g][b];"
        f"[r]lutrgb=r='val+{off}':g='val':b='val',"
        f"crop=iw-{off * 2}:ih:{off}:0[r2];"
        f"[b]lutrgb=r='val':g='val':b='val+{off}',"
        f"crop=iw-{off * 2}:ih:0:0[b2];"
        f"[g]crop=iw-{off * 2}:ih:{off // 2}:0[g2];"
        f"[r2][g2][b2]blend=all_mode=addition,"
        f"eq=brightness='if({cond},0.12,0)':eval=frame"
    )


def run_effect(job_id: str, clip_idx: int, effect: str | None = None,
               out_root: str = "", sink=None) -> dict:
    """Job entry: apply an AI-suggested (or user-picked) effect to a clip."""
    import db
    clips = db.get_clips(job_id)
    if clip_idx < 0 or clip_idx >= len(clips):
        raise RuntimeError(f"Clip index out of range: {clip_idx}")
    clip = clips[clip_idx]
    src = os.path.join(out_root, job_id, f"clip_{clip_idx + 1}.mp4")
    if not os.path.exists(src):
        raise RuntimeError("Clip file no longer available on disk")

    project = db.get_project(job_id) or {}
    transcript = project.get("transcript") or {}
    text = " ".join((s.get("text") or "") for s in
                    (transcript.get("segments") or []))[:2000]

    if not effect:
        suggested = suggest_effect(clip.get("title", ""),
                                   clip.get("hook", ""), text, sink)
        effect = suggested["effect"]
        intensity = suggested["intensity"]
    else:
        intensity = 2

    out = os.path.join(out_root, job_id, f"clip_{clip_idx + 1}_fx.mp4")
    apply_effect(src, out, effect, intensity, text, sink)
    if not os.path.exists(out):
        raise RuntimeError("Effect render produced no file")

    # Swap the rendered file into the clip slot (keep the same URL).
    final = os.path.join(out_root, job_id, f"clip_{clip_idx + 1}.mp4")
    os.replace(out, final)
    rev = db.bump_clip_rev(job_id, clip_idx)
    db.update_clip(job_id, clip_idx, size_mb=round(os.path.getsize(final) / 1e6, 1),
                   rev=rev)
    sink and sink("✅ Effect applied")
    # Include video_url so the status-poll response gets signed by the media
    # middleware (same path as the existing clip — effect overwrites in place).
    video_url = f"/videos/{job_id}/clip_{clip_idx + 1}.mp4"
    return {"clip_idx": clip_idx, "effect": effect, "intensity": intensity,
            "rev": rev, "video_url": video_url}
