"""Auto Sound Effects (SFX) — Submagic-style emphasis sounds on a clip.

Ported from QuClippers `auto_sfx.py`. QuClippers shipped an empty assets/sfx/
folder, so we SYNTHESIZE the SFX files with ffmpeg on first use (free, no
assets to ship):

  swoosh/whoosh — filtered noise sweep (transitions)
  pop           — short click (emphasis / exclamation)
  ting          — bright ding (numbers, facts, reveals)
  boom          — low thump (dramatic moment)

Moment detection uses the same estimated word timestamps as filler.py
(Klipin transcripts have no word-level times), plus exclamation and
number heuristics. Cap: 8 SFX per clip, min 3s apart.
"""
import os
import shutil
import subprocess

SFX_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "assets", "sfx")

SFX_TYPES = {
    "swoosh": "swoosh.mp3",
    "pop": "pop.mp3",
    "ting": "ting.mp3",
    "boom": "boom.mp3",
    "whoosh": "whoosh.mp3",
}

# ffmpeg aevalsrc recipes (44100 Hz mono)
_SYNTH = {
    "ting": "aevalsrc='sin(2*PI*1568*t)*exp(-9*t)':d=0.5:s=44100",
    "pop": "aevalsrc='sin(2*PI*420*t)*exp(-28*t)':d=0.18:s=44100",
    "boom": "aevalsrc='sin(2*PI*55*t)*exp(-4*t)':d=1.0:s=44100",
    "swoosh": ("aevalsrc='(random(0)-0.5)*exp(-3*(t-0.25)*(t-0.25)*6)':"
               "d=0.6:s=44100"),
    "whoosh": ("aevalsrc='(random(0)-0.5)*exp(-6*(t-0.15)*(t-0.15)*6)':"
               "d=0.35:s=44100"),
}


def ensure_sfx(log=None) -> None:
    """Synthesize any missing SFX files with ffmpeg."""
    log = log or (lambda m: None)
    os.makedirs(SFX_DIR, exist_ok=True)
    for name, filename in SFX_TYPES.items():
        path = os.path.join(SFX_DIR, filename)
        if os.path.exists(path) and os.path.getsize(path) > 0:
            continue
        log(f"🎵 Synthesizing SFX: {filename}...")
        r = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
             _SYNTH[name], "-af", "afade=t=in:st=0:d=0.02",
             "-c:a", "libmp3lame", "-b:a", "192k", path],
            capture_output=True, text=True,
        )
        if r.returncode != 0 or not os.path.exists(path):
            raise RuntimeError(f"SFX synthesis failed for {filename}: "
                               f"{r.stderr[-300:]}")


def get_available_sfx() -> list:
    return [t for t, f in SFX_TYPES.items()
            if os.path.exists(os.path.join(SFX_DIR, f))]


def _word_timestamps(segments: list) -> list:
    out = []
    for seg in segments:
        start = seg.get("start", 0)
        end = seg.get("end", 0)
        if end <= start:
            continue
        words = (seg.get("text") or "").split()
        if not words:
            continue
        step = (end - start) / len(words)
        for i, w in enumerate(words):
            w0 = start + i * step
            out.append({"word": w, "start": w0, "end": w0 + step})
    return out


def detect_emphasis_moments(segments: list) -> list:
    """Numbers → ting, exclamations → pop, pauses > 0.8s → swoosh."""
    words = _word_timestamps(segments or [])
    moments = []
    for i, word in enumerate(words):
        w = word.get("word", "").strip()
        t = word.get("start", 0)
        if any(c.isdigit() for c in w) and len(w) > 1:
            moments.append({"time": t, "type": "ting",
                            "reason": f"number: {w}"})
            continue
        if w.endswith("!"):
            moments.append({"time": t, "type": "pop", "reason": "exclamation"})
            continue
        if i > 0 and t - words[i - 1].get("end", t) > 0.8:
            moments.append({"time": max(0.0, t - 0.1), "type": "swoosh",
                            "reason": "pause transition"})
    # Deduplicate: min 3s apart, cap at 8
    filtered = []
    for m in moments:
        if not filtered or m["time"] - filtered[-1]["time"] > 3.0:
            filtered.append(m)
    return filtered[:8]


def inject_sfx(input_video: str, output_path: str, moments: list,
               volume: float = 0.3, log=None) -> str:
    """Delay-blend SFX onto the clip's original audio at each moment."""
    log = log or (lambda m: None)
    ensure_sfx(log)
    valid = [m for m in moments
             if os.path.exists(os.path.join(SFX_DIR,
                                            SFX_TYPES.get(m["type"], "")))]
    if not valid:
        shutil.copy2(input_video, output_path)
        log("🎧 No SFX to inject — clip copied unchanged")
        return output_path

    cmd = ["ffmpeg", "-y", "-i", input_video]
    for m in valid:
        cmd.extend(["-i", os.path.join(SFX_DIR, SFX_TYPES[m["type"]])])

    n = len(valid)
    parts = []
    for i, m in enumerate(valid):
        delay_ms = int(max(0.0, m["time"]) * 1000)
        parts.append(f"[{i + 1}:a]adelay={delay_ms}|{delay_ms},"
                     f"volume={volume}[sfx{i}]")
    mix_inputs = "[0:a]" + "".join(f"[sfx{i}]" for i in range(n))
    parts.append(f"{mix_inputs}amix=inputs={n + 1}:duration=first:"
                 f"dropout_transition=2:normalize=0[aout]")

    cmd.extend(["-filter_complex", ";".join(parts),
                "-map", "0:v", "-map", "[aout]",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart", "-shortest", output_path])
    log(f"🎧 Injecting {len(valid)} SFX...")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"SFX injection failed: {r.stderr[-500:]}")
    log("✅ SFX injected")
    return output_path


def run_sfx(job_id: str, clip_idx: int, out_root: str = "",
            volume: float = 0.3, sink=None) -> dict:
    """Job entrypoint: inject auto-SFX into one clip of a project."""
    import db

    project = db.get_project(job_id)
    if not project:
        raise RuntimeError("Project not found")
    clips = project.get("clips") or []
    if clip_idx < 0 or clip_idx >= len(clips):
        raise RuntimeError(f"Clip index out of range: {clip_idx}")

    log = sink or (lambda m: None)
    src = os.path.join(out_root, job_id, f"clip_{clip_idx + 1}.mp4")
    if not os.path.exists(src):
        raise RuntimeError("Clip video no longer available on disk")
    out = os.path.join(out_root, job_id, f"clip_{clip_idx + 1}_sfx.mp4")
    segments = (project.get("transcript") or {}).get("segments") or []

    c0, c1 = clips[clip_idx].get("start", 0), clips[clip_idx].get("end", 60)
    moments = [m for m in detect_emphasis_moments(segments)
               if c0 <= m["time"] <= c1]
    # The clip timeline starts at 0 (extracted from source c0) — shift each
    # moment so delays land at the right position inside the clip.
    for m in moments:
        m["time"] -= c0
    log(f"🎧 {len(moments)} emphasis moment(s) in clip range")
    inject_sfx(src, out, moments, volume=volume, log=log)
    return {
        "clip_idx": clip_idx,
        "video_url": f"/videos/{job_id}/clip_{clip_idx + 1}_sfx.mp4",
        "size_mb": round(os.path.getsize(out) / 1e6, 1),
    }
