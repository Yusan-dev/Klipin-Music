"""Background music (BGM) — Submagic-style music bed for clips.

Like SFX, we SYNTHESIZE the tracks locally with ffmpeg (no audio assets, no
licensing, no cost). Each mood is a short chord loop: sine oscillators for a
clean synth-pad feel plus a low bass, faded in/out to avoid clicks. The loop
is then mixed UNDER the clip's original audio (default 15 % volume) so the
voice stays clear — the classic "music bed".

Moods:
  upbeat — bright major progression (C–Am–F–G), faster pulse
  chill  — laid-back pad (Am–F–C–G), slower
  epic   — low cinematic pads with a slow swell
"""

import os
import subprocess
import tempfile

BGM_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "assets", "bgm")

# (bass_note, chord_notes, bars, bpm) — one chord per bar.
_MOODS = {
    "upbeat": {
        "chords": [
            (130.81, [261.63, 329.63, 392.00]),   # C
            (110.00, [220.00, 261.63, 329.63]),   # Am
            (87.31, [174.61, 220.00, 261.63]),    # F
            (98.00, [196.00, 246.94, 293.66]),    # G
        ],
        "bpm": 112, "gain": 0.16,
    },
    "chill": {
        "chords": [
            (110.00, [220.00, 261.63, 329.63]),   # Am
            (87.31, [174.61, 220.00, 261.63]),    # F
            (130.81, [261.63, 329.63, 392.00]),   # C
            (98.00, [196.00, 246.94, 293.66]),    # G
        ],
        "bpm": 80, "gain": 0.14,
    },
    "epic": {
        "chords": [
            (65.41, [196.00, 261.63, 329.63]),    # Cm? use C
            (77.78, [233.08, 293.66, 349.23]),    # Bb
            (87.31, [174.61, 220.00, 261.63]),    # F
            (98.00, [196.00, 246.94, 293.66]),    # G
        ],
        "bpm": 60, "gain": 0.20,
    },
}


def _chord_expr(notes: list, start: float, dur: float, gain: float,
                bpm: float) -> str:
    """Sine-sum expression for one chord, gated to [start, start+dur)."""
    # attack/release 60 ms to avoid clicks at bar boundaries
    env = f"min(1,(t-{start:.3f})/0.06)*min(1,({start + dur:.3f}-t)/0.06)"
    parts = [f"({gain}*sin(2*PI*{f:.2f}*t))" for f in notes]
    return f"({'+'.join(parts)})*max(0,{env})"


def _loop_expr(mood: dict, loop_sec: float) -> str:
    """Full aevalsrc expression for `loop_sec` seconds of the mood loop."""
    bpm = mood["bpm"]
    bar = 4 * 60.0 / bpm
    gain = mood["gain"]
    exprs = []
    t = 0.0
    chords = mood["chords"]
    i = 0
    while t < loop_sec:
        bass, notes = chords[i % len(chords)]
        exprs.append(_chord_expr([bass * 2], t, bar, gain * 0.5, bpm))  # bass
        exprs.append(_chord_expr(notes, t, bar, gain, bpm))            # pad
        t += bar
        i += 1
    return "+".join(exprs)


def render_bgm(mood: str, out_path: str, duration: float, log=None) -> str:
    """Synthesize a `duration`-long music bed for `mood` to `out_path` (m4a).

    Build only one musical loop and stream-loop it to the requested duration.
    Expanding a 1–3 minute aevalsrc expression in one FFmpeg process can make
    libavfilter allocate excessive memory (especially on Windows/Docker).
    """
    log = log or (lambda m: None)
    mood_cfg = _MOODS.get(mood)
    if not mood_cfg:
        raise RuntimeError(f"Unknown BGM mood: {mood} (use {list(_MOODS)})")
    os.makedirs(BGM_DIR, exist_ok=True)
    duration = max(3.0, float(duration))
    bar = 4 * 60.0 / mood_cfg["bpm"]
    loop_duration = bar * len(mood_cfg["chords"])
    expr = _loop_expr(mood_cfg, loop_duration)
    log(f"🎵 Rendering BGM ({mood}, {duration:.0f}s)...")
    loop_path = None
    try:
        fd, loop_path = tempfile.mkstemp(prefix="klipin_bgm_loop_", suffix=".m4a")
        os.close(fd)
        r = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
             "-i", f"aevalsrc='{expr}':d={loop_duration:.3f}:s=44100:c=mono",
             "-af", "lowpass=f=2400,afade=t=in:st=0:d=0.8,afade=t=out:st={}:d=0.8".format(max(0.0, loop_duration - 0.8)),
             "-c:a", "aac", "-b:a", "128k", loop_path],
            capture_output=True, text=True,
        )
        if r.returncode != 0 or not os.path.exists(loop_path):
            raise RuntimeError(f"BGM synthesis failed ({mood}): {r.stderr[-400:]}")
        r = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-stream_loop", "-1",
             "-i", loop_path, "-t", f"{duration:.3f}",
             "-af", "afade=t=in:st=0:d=0.8,afade=t=out:st={}:d=1.0".format(max(0.0, duration - 1.0)),
             "-c:a", "aac", "-b:a", "128k", out_path],
            capture_output=True, text=True,
        )
        if r.returncode != 0 or not os.path.exists(out_path):
            raise RuntimeError(f"BGM loop expansion failed ({mood}): {r.stderr[-400:]}")
    finally:
        if loop_path:
            try:
                os.remove(loop_path)
            except OSError:
                pass
    log(f"✅ BGM ready: {out_path}")
    return out_path


def mix_bgm(video_path: str, bgm_path: str, out_path: str,
            volume: float = 0.15, log=None) -> str:
    """Mix the music bed UNDER the video's audio at `volume`."""
    log = log or (lambda m: None)
    log(f"🎚️ Mixing BGM at {volume:.0%} volume...")
    r = subprocess.run([
        "ffmpeg", "-y", "-i", video_path, "-i", bgm_path,
        "-filter_complex",
        f"[0:a]volume=1.0[a0];[1:a]volume={volume:.3f},aloop=loop=-1:size=2e9[a1];"
        "[a0][a1]amix=inputs=2:duration=first:normalize=0[a]",
        "-map", "0:v", "-map", "[a]",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart", out_path,
    ], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"BGM mix failed: {r.stderr[-500:]}")
    log("✅ BGM mixed")
    return out_path


def available_moods() -> list:
    return list(_MOODS.keys())
