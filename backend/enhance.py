"""AI Enhance — ClipperStudio-style "AI Enhance" for Klipin clips.

Kept free and deterministic: a curated FFmpeg enhancement chain (upscale to
1080p, denoise, unsharp, color vibrance) that runs locally. No paid upscaling
API. The "AI" part mirrors the rest of the stack: Gemini is NOT needed here —
the enhancement model is a fixed, safe filter chain, so the cost is $0 and the
result is predictable.
"""
import os

from gpu import encode_args
from video import get_duration, get_resolution, run

TARGET_W = 1080
TARGET_H = 1920


def _denoise_strength(strength: int) -> str:
    # hqdn3d luma/chroma strengths scale with the requested intensity.
    luma = 1.2 + strength * 0.6
    chroma = 1.5 + strength * 0.6
    return f"hqdn3d={luma:.1f}:0:{chroma:.1f}:0"


def apply_enhance(input_path: str, out_path: str, strength: int = 2,
                  log=None) -> str:
    """Enhance a clip: scale to 1080x1920, denoise, sharpen, add vibrance.

    strength 1..3 (default 2). Always outputs 1080p vertical regardless of the
    input resolution so a 640x360 source is delivered as a clean Full HD clip.
    """
    strength = max(1, min(3, int(strength)))
    w, h = get_resolution(input_path)
    dur = get_duration(input_path)
    if dur <= 0:
        dur = 5.0
    log and log(f"✨ AI Enhance (strength {strength}) — "
                f"{w}x{h} -> {TARGET_W}x{TARGET_H}")

    # Upscale with lanczos + light unsharp first (matches video.py quality
    # philosophy), then clean noise and finish with vibrance.
    unsharp = 4 + strength * 1.5
    vibrance = 0.06 + strength * 0.04
    vf = (
        f"scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=increase:flags=lanczos,"
        f"crop={TARGET_W}:{TARGET_H},setsar=1,"
        f"unsharp=5:5:{unsharp:.1f},"
        f"{_denoise_strength(strength)},"
        f"eq=saturation=1.12:vibrance={vibrance:.3f}"
    )
    run([
        "ffmpeg", "-y", "-i", input_path,
        "-vf", vf,
        *encode_args(log=log),
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        out_path,
    ], log)
    return out_path


def run_enhance(job_id: str, clip_idx: int, out_root: str = "",
                strength: int = 2, sink=None) -> dict:
    """Job entry: enhance a single rendered clip and swap it into the slot."""
    import db
    clips = db.get_clips(job_id)
    if clip_idx < 0 or clip_idx >= len(clips):
        raise RuntimeError(f"Clip index out of range: {clip_idx}")
    src = os.path.join(out_root, job_id, f"clip_{clip_idx + 1}.mp4")
    if not os.path.exists(src):
        raise RuntimeError("Clip file no longer available on disk")

    out = os.path.join(out_root, job_id, f"clip_{clip_idx + 1}_enh.mp4")
    apply_enhance(src, out, strength, sink)
    if not os.path.exists(out):
        raise RuntimeError("Enhance render produced no file")

    final = os.path.join(out_root, job_id, f"clip_{clip_idx + 1}.mp4")
    os.replace(out, final)
    rev = db.bump_clip_rev(job_id, clip_idx)
    size_mb = round(os.path.getsize(final) / 1e6, 1)
    db.update_clip(job_id, clip_idx, size_mb=size_mb, rev=rev)
    sink and sink("✅ AI Enhance applied")
    return {
        "clip_idx": clip_idx,
        "strength": strength,
        "rev": rev,
        "size_mb": size_mb,
        "video_url": f"/videos/{job_id}/clip_{clip_idx + 1}.mp4",
    }
