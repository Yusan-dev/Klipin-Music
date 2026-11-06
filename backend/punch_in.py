"""Punch-in zoom: a short push toward the subject on the beats that carry the
clip (ported from OpenShorts punch_in.py).

A static crop for 45 seconds is the most "unedited" thing a vertical clip can
do, but a constant linear zoom (the old `apply_auto_zoom` ramp) reads as
drift rather than intent. This module finds the audio emphasis beats and
builds an asymmetric fast-in / hold / slow-out punch on each one — how a
human operator does it.

Pure helpers here stay unit-testable without FFmpeg or the video files.
"""

import numpy as np

# Peak zoom. 1.12 crops ~11% off each dimension: visible as intent, still
# short of cutting into a head framed by the TRACK crop.
MAX_ZOOM = 1.12

# Envelope, in seconds: snap in, sit there, drift out.
RISE_SECONDS = 0.25
HOLD_SECONDS = 1.30
FALL_SECONDS = 0.55

# Audio-envelope resolution used to find beats.
BEAT_WINDOW = 0.2

# A beat must exceed the clip's median loudness by this much of the gap to
# its peak, and two punches this close together are merged into one — a beat
# per 18s of clip is emphasis, a beat every 7s is a twitch (measured on the
# OpenShorts corpus: 18.0/0.60 gives the tightest punch-per-minute spread).
MIN_GAP_SECONDS = 18.0
BEAT_PROMINENCE = 0.60


def _ease(t):
    """Smoothstep on [0, 1]. Linear ramps look mechanical at this duration."""
    t = min(max(t, 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


def emphasis_times(video_path, duration, envelope=None, window=BEAT_WINDOW,
                   min_gap=MIN_GAP_SECONDS, prominence=BEAT_PROMINENCE):
    """Seconds where the audio leaps above its own baseline.

    ``envelope`` may be supplied directly (the per-window normalised RMS from
    active_speaker.audio_envelope) for hermetic unit tests; otherwise it is
    decoded from ``video_path``. Returns [] for silent or unreadable audio,
    which simply means no punches.
    """
    if envelope is None:
        from active_speaker import audio_envelope
        envelope = audio_envelope(video_path, 0.0, duration, window_s=window)
    if not envelope:
        return []

    values = np.asarray(envelope, dtype=float)
    baseline = float(np.median(values))
    peak = float(values.max())
    if peak - baseline <= 1e-6:
        return []

    threshold = baseline + (peak - baseline) * prominence
    times = []
    last = -1e9
    for i, v in enumerate(values):
        t = i * window
        if v >= threshold and t - last >= min_gap:
            times.append(round(t, 3))
            last = t
    return times


def zoom_curve(n_frames, fps, emphasis_times, max_zoom=None,
               start_offset=0.0):
    """Per-frame zoom factor (1.0 = untouched) for one scene.

    ``emphasis_times`` are absolute seconds; ``start_offset`` is where this
    scene begins, so callers can pass clip-level beats unchanged.
    """
    max_zoom = MAX_ZOOM if max_zoom is None else max_zoom
    zooms = [1.0] * max(0, n_frames)
    if not zooms or max_zoom <= 1.0:
        return zooms

    span = RISE_SECONDS + HOLD_SECONDS + FALL_SECONDS
    for t in emphasis_times:
        local = t - start_offset
        if local <= -span or local >= n_frames / fps:
            continue
        for f in range(max(0, int(local * fps)),
                       min(n_frames, int((local + span) * fps) + 1)):
            dt = f / fps - local
            if dt < 0:
                continue
            if dt < RISE_SECONDS:
                amount = _ease(dt / RISE_SECONDS)
            elif dt < RISE_SECONDS + HOLD_SECONDS:
                amount = 1.0
            elif dt < span:
                amount = 1.0 - _ease(
                    (dt - RISE_SECONDS - HOLD_SECONDS) / FALL_SECONDS)
            else:
                continue
            # Overlapping pushes take the strongest rather than compounding.
            zooms[f] = max(zooms[f], 1.0 + (max_zoom - 1.0) * amount)
    return zooms


def punch_expression(emphasis_times, max_zoom=MAX_ZOOM, fps=30.0):
    """FFmpeg zoompan `z` expression for a punch-in on each beat time.

    Each punch is a trapezoid (fast rise / hold / slow fall) smoothed with
    smoothstep and clamped with `clip`; overlapping beats take the strongest
    push rather than compounding. `on` is the zoompan output-frame counter,
    so `on/fps` is time in seconds. Returns None when there is nothing to
    punch (no beats or no zoom), so the caller can skip the re-encode.
    """
    if not emphasis_times or max_zoom <= 1.0:
        return None
    amp = max_zoom - 1.0
    terms = []
    for t0 in emphasis_times:
        t2 = t0 + RISE_SECONDS + HOLD_SECONDS
        p = (f"min(clip((on/{fps:g}-{t0:.3f})/{RISE_SECONDS:g},0,1),"
             f"1-clip((on/{fps:g}-{t2:.3f})/{FALL_SECONDS:g},0,1))")
        terms.append(f"({p})*({p})*(3-2*({p}))")
    # ffmpeg's max() is BINARY (exactly two args) — a single term needs no
    # wrapper and several are nested, otherwise the filter fails to parse.
    if len(terms) == 1:
        base = terms[0]
    else:
        base = terms[0]
        for term in terms[1:]:
            base = f"max({base},{term})"
    return f"1+{amp:.4f}*{base}"