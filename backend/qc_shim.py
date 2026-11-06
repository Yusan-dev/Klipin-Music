"""qc_shim.py — Bridge Klipin API calls to the authentic QuClippers engine.

Klipin's pipeline calls `reframe_clip_v2(src, out_path, aspect=...)`.
QuClippers' renderer is `render(input_video, final_output_video, aspect_ratio=...)`.

This shim adapts the QuClippers engine so Klipin's existing `reframe_clip_v2`
call site transparently uses the real QuClippers renderer — no changes needed
to pipeline.py or app.py.

QuClippers handles layout selection (STACK/TALKING_HEAD, SPLIT, PANEL,
SCREENCAST, GENERAL) internally via scene classification + speaker count, so
the Klipin `stack` parameter is respected as an override:
  stack=True  → set FORCE_STACK=1 (force 3-band layout)
  stack=False → clear FORCE_STACK  (let QuClippers auto-pick)
"""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))

from reframe_v2_qc import render  # noqa: F401

__all__ = ["reframe_clip_v2"]


def reframe_clip_v2(src: str, out_path: str, aspect: str = "9:16",
                    log=None, adaptive_size: bool = False) -> str:
    """Drop-in for Klipin's `reframe_clip_v2` → QuClippers `render()`.

    Output is always Full HD (1080x1920 for 9:16) with zero stretch.
    Honors FORCE_STACK env override (set/cleared by the wrapper).

    Note: QuClippers' render() does not accept a `log` kwarg, so we route
    logging through its print() calls instead.
    """
    # QuClippers uses print() for logging; adapt log callback if provided
    import builtins
    orig_print = builtins.print
    if log:
        builtins.print = lambda *a, **k: log(' '.join(str(x) for x in a))
    try:
        return render(input_video=src, final_output_video=out_path,
                      aspect_ratio=aspect)
    finally:
        builtins.print = orig_print
