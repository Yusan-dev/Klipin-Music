"""Who is talking, from mouth movement plus audio energy.

The SPLIT layout shipped without this and it is the thing that decides whether
the format lands or grates: stacking is only worth half the frame when both
people actually take turns. Geometry alone will stack a scene where one person
sits silent for thirty seconds.

Full diarisation is not needed to answer "which of these two faces is moving its
mouth right now". This measures frame-to-frame change in the mouth region of
each known face box, and only trusts the comparison in windows where the audio
says somebody is speaking at all — otherwise a nod, a laugh or a chewed sweet
scores as speech.

The two faces come from split_layout's medians, so no per-frame face detection
runs here: the subjects of a two-shot are seated and the boxes hold.

Everything in this module is pure enough to unit-test except decode_activity(),
which needs ffmpeg.
"""
import os
import subprocess

import numpy as np

# Load .env if present (for container env vars)
try:
    from dotenv import load_dotenv
    load_dotenv("/app/.env")
except Exception:
    pass

# Seconds per decision window. Short enough to catch a one-word interjection,
# long enough that a single blurred frame cannot flip the answer.
WINDOW_SECONDS = 0.4

# On by default: before a two-shot is stacked, we check that both people
# actually talk (SPEAKER_SIGNAL=0 opts back out to the pure-geometry route).
# SPEAKER_CUT=1 additionally replaces the stack with hard cuts to whoever
# holds the floor.
ENABLED = os.environ.get("SPEAKER_SIGNAL", "1") == "1"
# Klipin is a Shorts/Reels editor: a verified two-person conversation should
# focus the person holding the floor by default.  Setting SPEAKER_CUT=0 still
# restores the wide two-shot for installations that explicitly prefer it.
CUT_MODE = os.environ.get("SPEAKER_CUT", "1") == "1"

# A window counts as speech only if its audio RMS clears this fraction of the
# scene's loudest window. Silence and room tone sit far below it.
AUDIO_FLOOR = 0.18

# The winner's mouth must be this much more active than the other's, relative to
# the pair's total, before the window is attributed. Below it the window is
# "unclear" and simply does not vote — which is the honest answer when both are
# still or both are moving.
MIN_MARGIN = 0.15

# Share of attributed windows each speaker needs before a two-shot counts as a
# conversation. At 0.2 a listener who chips in every fifth window still counts;
# a genuinely silent listener does not.
MIN_SHARE = float(os.environ.get("SPLIT_MIN_SHARE", "0.2"))

ANALYSIS_WIDTH = 480


def mouth_region(box, frame_w, frame_h, scale=1.0):
    """Pixel rect (x0, y0, x1, y1) around the mouth of a face box.

    ``box`` is (cx, cy, w, h) — CENTER + size, the convention produced by
    ``detector.face_boxes`` (MediaPipe/YOLO/Haar) and used everywhere else in
    this codebase. ``scale`` maps the box from its detection space down to the
    analysis frame (decode_activity re-samples to ANALYSIS_WIDTH before
    slicing, so the ROI must live in analysis coords). MediaPipe boxes cover
    brow to chin, so the mouth sits low and central. The region is deliberately
    wider than the lips: jaw movement carries as much signal as the lips
    themselves and survives a box that drifts a little.
    """
    cx, cy, w, h = [v * scale for v in box]
    mouth_y = cy + h * 0.22
    half_w = w * 0.36
    half_h = h * 0.22

    x0 = int(max(0, min(cx - half_w, frame_w - 1)))
    x1 = int(max(x0 + 1, min(cx + half_w, frame_w)))
    y0 = int(max(0, min(mouth_y - half_h, frame_h - 1)))
    y1 = int(max(y0 + 1, min(mouth_y + half_h, frame_h)))
    return x0, y0, x1, y1


def audio_envelope(video_path, start_s, duration_s, window_s=WINDOW_SECONDS):
    """Per-window RMS of the clip's audio, normalised to its own peak.

    Returns [] when the source has no audio track, which makes every window
    eligible rather than none: a silent source should fall back to mouth
    movement alone, not refuse to decide.
    """
    try:
        raw = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", f"{start_s:.4f}",
             "-t", f"{duration_s:.4f}", "-i", video_path,
             "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=300).stdout
    except (subprocess.SubprocessError, OSError):
        return []
    if not raw:
        return []

    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
    per_window = max(1, int(8000 * window_s))
    n = len(samples) // per_window
    if n < 1:
        return []

    windows = samples[:n * per_window].reshape(n, per_window)
    rms = np.sqrt(np.mean(windows ** 2, axis=1))
    peak = rms.max()
    return (rms / peak).tolist() if peak > 0 else [0.0] * n


def normalise_activity(activity):
    """Rescale each speaker's mouth activity onto its own quiet-to-loud range.

    Raw frame-difference magnitude is not comparable between two faces: it
    scales with the local contrast, the lighting and the size of the box, so
    whichever speaker happens to be better lit wins every window. Measured on a
    real two-shot, the raw signal handed one speaker 90-100% of the scene.

    Subtracting each speaker's own 20th percentile (their resting state) and
    dividing by their own 20-80 spread compares "how active is this mouth for
    this person" instead. Windows where nobody speaks are already dropped by the
    audio gate, so a silent listener's amplified noise never gets a vote.
    """
    if not activity:
        return []
    columns = np.asarray(activity, dtype=float)
    if columns.ndim != 2 or columns.shape[1] < 2:
        return activity

    low = np.percentile(columns, 20, axis=0)
    high = np.percentile(columns, 80, axis=0)
    spread = np.where(high - low > 1e-6, high - low, 1.0)
    return np.clip((columns - low) / spread, 0.0, None).tolist()


def attribute_windows(activity, loudness, min_margin=MIN_MARGIN):
    """Which speaker owns each window, or None when it is not clear.

    ``activity`` is [[a0, a1], ...], one mouth-movement score per speaker per
    window; ``loudness`` is the normalised audio envelope (or [] for none).
    """
    verdicts = []
    for i, pair in enumerate(activity):
        if loudness and i < len(loudness) and loudness[i] < AUDIO_FLOOR:
            verdicts.append(None)          # nobody is talking here
            continue
        a, b = pair
        total = a + b
        if total <= 0:
            verdicts.append(None)
            continue
        margin = abs(a - b) / total
        verdicts.append(None if margin < min_margin else (0 if a > b else 1))
    return verdicts


def shares(verdicts):
    """Fraction of attributed windows held by each speaker: (share0, share1).

    Returns (0.0, 0.0) when nothing was attributed, so callers see "no evidence"
    rather than a spurious 50/50.
    """
    counted = [v for v in verdicts if v is not None]
    if not counted:
        return 0.0, 0.0
    n = float(len(counted))
    return counted.count(0) / n, counted.count(1) / n


def is_conversation(verdicts, min_share=None):
    """True when both speakers hold enough of the attributed windows."""
    min_share = MIN_SHARE if min_share is None else min_share
    a, b = shares(verdicts)
    return min(a, b) >= min_share


def hold(verdicts, min_windows=3):
    """Smooth the per-window verdicts so the answer cannot flap.

    A cut back and forth every 0.4s is unwatchable, and a listener's single
    "yeah" should not steal the frame. Unclear windows inherit the current
    speaker, and a new speaker must hold ``min_windows`` in a row to take over.
    """
    out = []
    current = None
    pending = None
    run = 0
    for v in verdicts:
        if v is None or v == current:
            if v == current:
                pending, run = None, 0
            out.append(current)
            continue
        if v == pending:
            run += 1
        else:
            pending, run = v, 1
        if current is None or run >= min_windows:
            current, pending, run = v, None, 0
        out.append(current)

    # Nothing was ever confident enough to start: leave it to the caller.
    if all(v is None for v in out):
        return out

    # Backfill the lead-in with whoever spoke first.
    first = next(v for v in out if v is not None)
    return [first if v is None else v for v in out]


def verdicts_for_scene(video_path, start_f, end_f, fps, centres):
    """Held per-window speaker verdicts for one scene (frames start_f..end_f).

    ``centres`` are (cx, cy, w, h) face boxes in source coords for the
    candidate speakers (first two used). Returns held verdicts (0/1/None per
    0.4 s window), or [] when nothing is decidable. Same measurement chain
    as the V1 router (mouth activity + audio gate + hold), so shares() and
    is_conversation() apply directly.
    """
    fps = float(fps or 0.0)
    if fps <= 0 or not centres or len(centres) < 2:
        return []
    start = float(start_f) / fps
    dur = max(0.0, (float(end_f) - float(start_f)) / fps)
    if dur < 1.2:
        return []
    try:
        boxes = [tuple(float(v) for v in c[:4]) for c in centres[:2]]
        activity = decode_activity(video_path, start, dur, boxes)
    except Exception:
        return []
    if not activity:
        return []
    try:
        loudness = audio_envelope(video_path, start, dur)
    except Exception:
        loudness = []
    try:
        verdicts = hold(attribute_windows(
            normalise_activity(activity), loudness))
    except Exception:
        return []
    if not verdicts or all(v is None for v in verdicts):
        return []
    return verdicts


def speaker_xs(held, centres, crop_w, frame_w, frame_count, fps):
    """Stable per-frame crop x coordinates for active-speaker hard cuts."""
    frame_count = max(0, int(frame_count or 0))
    fps = float(fps or 30.0)
    usable = list(centres or [])[:2]
    if not usable:
        return [max(0.0, (float(frame_w) - float(crop_w)) / 2.0)] * frame_count
    max_x = max(0.0, float(frame_w) - float(crop_w))
    verdicts = list(held or [])
    default = next((v for v in verdicts if v is not None), 0)
    result = []
    for frame in range(frame_count):
        wi = min(len(verdicts) - 1,
                 int((frame / fps) / WINDOW_SECONDS)) if verdicts else -1
        speaker = verdicts[wi] if wi >= 0 and verdicts[wi] is not None else default
        idx = min(max(int(speaker), 0), len(usable) - 1)
        x = float(usable[idx][0]) - float(crop_w) / 2.0
        result.append(max(0.0, min(max_x, x)))
    return result


def decode_activity(video_path, start_s, duration_s, boxes):
    """Decode video and compute per-frame mouth activity for each face box.

    Returns a list of [speaker0_activity, speaker1_activity] per window.
    """
    import cv2

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return []

    fps = cap.get(cv2.CAP_PROP_FPS)
    if fps <= 0:
        fps = 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    start_frame = int(start_s * fps)
    end_frame = min(total_frames, int((start_s + duration_s) * fps))
    n_frames = end_frame - start_frame

    if n_frames <= 0:
        cap.release()
        return []

    frame_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    frame_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    scale = ANALYSIS_WIDTH / frame_w
    small_h = int(frame_h * scale)

    # Precompute mouth regions at analysis resolution. The ROI must be clamped
    # to the DOWNSAMPLED frame (ANALYSIS_WIDTH x small_h), not the source
    # resolution — otherwise a face near the right edge yields an x1 past the
    # slice boundary and the ROI comes back empty (crash on np.mean).
    regions = []
    for box in boxes:
        x0, y0, x1, y1 = mouth_region(box, ANALYSIS_WIDTH, small_h, scale)
        regions.append((x0, y0, x1, y1))

    cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)

    activity = []  # [[s0, s1], ...] per frame
    # Liveness checks also call this with one face or a group of candidates.
    # A fixed two-element buffer crashed on candidate #3 and silently disabled
    # the caller's poster filter; truncation also lost all solo measurements.
    prev_gray = [None] * len(regions)

    for _ in range(n_frames):
        ret, frame = cap.read()
        if not ret:
            break

        small = cv2.resize(frame, (ANALYSIS_WIDTH, small_h))
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)

        frame_activity = []
        for i, (x0, y0, x1, y1) in enumerate(regions):
            roi = gray[y0:y1, x0:x1]
            if prev_gray[i] is not None:
                diff = cv2.absdiff(roi, prev_gray[i])
                score = float(np.mean(diff))
            else:
                score = 0.0
            frame_activity.append(score)
            prev_gray[i] = roi.copy()

        if frame_activity:
            activity.append(frame_activity)

    cap.release()

    # Aggregate per-window (WINDOW_SECONDS)
    window_frames = max(1, int(WINDOW_SECONDS * fps))
    windowed = []
    for i in range(0, len(activity), window_frames):
        chunk = activity[i:i + window_frames]
        if chunk:
            # Average per speaker
            speaker_activity = [sum(c[j] for c in chunk) / len(chunk)
                                  for j in range(len(regions))]
            windowed.append(speaker_activity)

    return windowed
