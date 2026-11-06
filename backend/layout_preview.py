"""Cached layout proofs from the same full-clip dispatcher as re-render.

Preview never overwrites the saved clip and never substitutes CSS cropping
of a previously rendered portrait. Caption/hook controls remain overlays in
the editor; the generated video is explicitly a layout-only proof.
"""
import hashlib
import json
from pathlib import Path
import threading

_render_lock = threading.Lock()
REVISION = "layout-proof-20260905-v1"
LAYOUT_KEYS = ("layout", "crop", "subject_lock", "layout_plan_override",
               "gameplay_face_position", "gameplay_ratio", "_detected_genre",
               "genre_detect", "layout_picker")


def render_preview(source, output_dir, clip, options):
    from ops import _extract_clip_with_segments, _render_crop, _portrait_guardian
    source, output_dir = Path(source), Path(output_dir)
    if not source.is_file():
        raise FileNotFoundError("Source video is unavailable for layout preview")
    start, end = float(clip["start"]), float(clip["end"])
    import math
    if not all(map(math.isfinite, (start, end))) or not 0 < end - start <= 180:
        raise ValueError("Layout preview requires a clip of at most 180 seconds")
    layout_options = {key: options[key] for key in LAYOUT_KEYS if key in options}
    # Use deterministic local scene evidence for preview and cached results;
    # no transcript or paid generation calls are needed to inspect a layout.
    layout_options.update(genre_detect=False, layout_picker=False)
    identity = json.dumps([REVISION, source.stat().st_size, source.stat().st_mtime_ns,
                           clip, layout_options], sort_keys=True, default=str)
    key = hashlib.sha256(identity.encode()).hexdigest()[:24]
    folder = output_dir / "layout_previews" / key
    target = folder / "preview.mp4"
    with _render_lock:
        if target.is_file():
            return target
        folder.mkdir(parents=True, exist_ok=True)
        raw = folder / "raw.mp4"
        pending = folder / "pending.mp4"
        try:
            _extract_clip_with_segments(str(source), clip, str(raw), None, str(folder))
            _render_crop(str(raw), str(pending), layout_options, "9:16", None, str(folder))
            _portrait_guardian(str(pending), str(folder), "preview", "9:16")
            pending.replace(target)
        finally:
            raw.unlink(missing_ok=True)
            pending.unlink(missing_ok=True)
    return target
