"""Safe single-pass renderer for the cheapest portrait layout."""
from __future__ import annotations

import os

from gpu import encode_args
from video import (_aspect_window, _ffmpeg_executable,
                   _sharpen_for_upscale, get_resolution, output_fps_args,
                   run)


def eligible(crop_mode: str, aspect: str, options: dict,
             *, watermark: str = "", title_overlay: str = "",
             hook_overlay: bool = False, hook_v2: object = None,
             narrated_opening: bool = False, bgm: str = "") -> bool:
    """Return true only when fusion cannot change visual/audio semantics."""
    if os.environ.get("KLIPIN_FUSED_RENDER", "0") != "1":
        return False
    if crop_mode != "center" or aspect != "9:16":
        return False
    if watermark.strip() or title_overlay.strip() or hook_overlay or hook_v2:
        return False
    if narrated_opening or (bgm and bgm != "none"):
        return False
    if options.get("auto_effects") or options.get("auto_zoom"):
        return False
    return True


def render(source: str, start: float, end: float, ass_path: str,
           output: str, log=None) -> str:
    """Encode source -> portrait crop -> captions in one FFmpeg process."""
    width, height = get_resolution(source)
    target_w, target_h = 1080, 1920
    crop_w, crop_h = _aspect_window(width, height, target_w, target_h)
    x, y = max(0, (width - crop_w) // 2), max(0, (height - crop_h) // 2)
    fps_args = output_fps_args(source)
    fps = fps_args[fps_args.index("-r") + 1]
    filters = [
        f"crop={crop_w}:{crop_h}:{x}:{y}",
        f"scale={target_w}:{target_h}{_sharpen_for_upscale(crop_w, crop_h, target_w, target_h)}",
        f"setpts=N/({fps}*TB)", "setsar=1",
        f"ass={os.path.basename(ass_path)}",
    ]
    duration = max(0.01, float(end) - float(start))
    log and log("Fused render: source -> crop -> captions -> NVENC (single pass)")
    run([
        _ffmpeg_executable(), "-y", "-ss", f"{max(0.0, float(start)):.3f}",
        "-i", source, "-t", f"{duration:.3f}", "-vf", ",".join(filters),
        "-map", "0:v:0", "-map", "0:a?", *fps_args, *encode_args(log=log),
        "-af", "aresample=48000:async=1:first_pts=0,asetpts=N/SR/TB",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-movflags", "+faststart", output,
    ], log)
    return output
