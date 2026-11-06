"""Speaker diarization proxy for Klipin — determines split-screen vs camera-switch.

Borrowed conceptually from opensource-clipping's diarization gate: a clip with
two (or more) active speakers within the same window should render as split-
screen (side-by-side) rather than a single face-tracked frame, otherwise the
viewer loses half the conversation and subtitles can drift ahead of the wrong
mouth.

We use pyannote.audio's pretrained diarization pipeline (CPU, ~30 s / minute of
audio on the Klipin container). The result is reduced to a small list of
[{speaker, start, end}] segments so the layout picker and subtitle builder
can reason about speaker changes without holding the full annotation.
"""
import os
from typing import List

_CACHE = {}


def diarize(source_path: str, start: float, end: float,
            cache_dir: str = "/app/data/.diar",
            min_speakers: int = 1) -> List[dict]:
    """Return speaker segments overlapping [start, end].

    Each item: {"speaker": "SPEAKER_00", "start": float, "end": float}.
    Cached on disk per (source, start, end) so re-renders are instant.
    """
    os.makedirs(cache_dir, exist_ok=True)
    key = f"{os.path.basename(source_path)}_{start:.1f}_{end:.1f}_{min_speakers}"
    cache_file = os.path.join(cache_dir, f"{key}.json")
    if os.path.exists(cache_file):
        import json
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass  # recompute on any corruption

    try:
        from pyannote.audio import Pipeline
    except Exception:
        # pony: pyannote unavailable in this env. Return a single-speaker
        # fallback so the caller degrades to face-track (no split).
        segs = [{"speaker": "SPEAKER_00", "start": start, "end": end}]
        return segs

    if "pipeline" not in _CACHE:
        # 9router / container has network to HF for model download on first run.
        _CACHE["pipeline"] = Pipeline.from_pretrained(
            "pyannote/speaker-diarization",
            use_auth_token=os.environ.get("HF_TOKEN", None),
        )

    pipeline = _CACHE["pipeline"]
    diar = pipeline(source_path)

    segs = []
    for turn, _, speaker in diar.itertracks(label="speaker"):
        # Only keep segments that overlap [start, end]
        if turn.end <= start or turn.start >= end:
            continue
        segs.append({
            "speaker": str(speaker),
            "start": round(max(turn.start, start), 2),
            "end": round(min(turn.end, end), 2),
        })

    import json
    try:
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(segs, f)
    except Exception:
        pass
    return segs


def speaker_count(segs: List[dict]) -> int:
    """Distinct speakers in a diarization result."""
    return len({s["speaker"] for s in segs}) or 1
