"""reframe_v2.py — Klipin wrapper that delegates to the QuClippers engine.

Historically this file held a from-scratch V2 pipeline. We now delegate to
the authentic QuClippers/OpenShorts engine (reframe_v2_qc.py + qc_shim.py) so
Klipin ships identical crop/smooth quality to QuClippers with Full HD output.

This module preserves the `reframe_clip_v2` entry-point and the `stack` kwarg
(used by pipeline.py for backwards compat) so nothing upstream breaks.

The `stack` parameter maps to QuClippers' FORCE_STACK env override:
  stack=True  → force 3-stack layout (sharp middle + blur top/bottom)
  stack=False → clear override, let QuClippers auto-pick layout
"""
from qc_shim import reframe_clip_v2 as _qc_reframe_clip_v2

__all__ = ["reframe_clip_v2", "render", "delivery_size", "ANALYSIS_MAX_WIDTH"]


def reframe_clip_v2(src: str, out_path: str, aspect: str = "9:16",
                    log=None, adaptive_size: bool = False,
                    stack: bool = True) -> str:
    """Klipin entry point → QuClippers renderer.

    Args:
        stack: True = force 3-stack layout (sharp middle + blur top/bottom);
               False = let QuClippers auto-select layout.
    """
    if stack:
        import os as _os
        _os.environ["FORCE_STACK"] = "1"
    else:
        import os as _os
        _os.environ.pop("FORCE_STACK", None)
    # adaptive_size is QuClippers default (min 1080px); no-op here.
    return _qc_reframe_clip_v2(src, out_path, aspect, log=log,
                               adaptive_size=adaptive_size)


# Re-export QuClippers internals for tests / diagnostics.
from reframe_v2_qc import (  # noqa: E402
    render,
    delivery_size,
    ANALYSIS_MAX_WIDTH,
)
