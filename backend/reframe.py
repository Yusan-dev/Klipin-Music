"""Scene-aware reframing for Klipin: dynamic face tracking (TRACK) vs
blurred-background fit (GENERAL), with a 1080p delivery floor.

Ported ideas from the original Klipin (QuClippers) reframe engine:
  - analysis runs at <=640px to keep CPU/time cheap
  - TRACK follows the speaker's face with a smoothed dynamic crop (sendcmd)
  - a big camera move must persist JUMP_CONFIRM samples before the crop
    follows it (kills the jitter a noisy detector produces)
  - GENERAL falls back to the blurred-background treatment
"""
import json
import os
import subprocess

import numpy as np
import cv2

from active_speaker import (
    ENABLED as SPEAKER_ENABLED,
    CUT_MODE as SPEAKER_CUT_MODE,
    AUDIO_FLOOR as _AUDIO_FLOOR,
    MIN_MARGIN as _MIN_MARGIN,
    decode_activity,
    normalise_activity,
    attribute_windows,
    shares,
    is_conversation,
    hold,
    audio_envelope,
    WINDOW_SECONDS,
    MIN_SHARE as SPLIT_MIN_SHARE,
)

from detector import face_boxes, verified_face_boxes
from gpu import encode_args
from video import (TARGET_FRAMES, _aspect_window, _ffmpeg_executable,
                   get_duration, get_fps, get_resolution, run, UPSCALE,
                   _coexisting_pair)

ANALYSIS_MAX_WIDTH = 640
DELIVERY_MIN_WIDTH = 1080
TRACK_FPS = 12.0         # face-sampling rate for the crop path (doubled from 6 for responsive tracking)
JUMP_CONFIRM = 3         # OpenShorts parity: ~250 ms confirmation at 12 Hz
BIG_MOVE_FRAC = 0.10     # move larger than 10% of window = "big"
DEAD_ZONE_FRAC = 0.65    # wider dead zone → camera HOLDS still, less wobble
PAN_SPEED = 0.8          # px/frame @30fps — very slow, steady linear pan (anti-getar)
FAST_PAN_SPEED = 10.0    # px/frame @30fps — reach a newly speaking face promptly

# Faces narrower than this share of the frame width are background extras, an
# audience, or a photo on the wall — NOT speakers. OpenShorts (split_layout.py
# MIN_FACE_WIDTH=0.045) filters them out before counting. Without it a panel
# show or crowd shot raises the median face count to 3+ and the whole scene
# falls to GENERAL (full-frame), exactly the "banyak orang -> no crop" bug.
# A talking head close-up keeps counting as ONE face even with a row of small
# faces behind it, so it stays TRACK and crops to the speaker.
MIN_FACE_WIDTH_FRAC = 0.045

SCENE_THRESHOLD = 27.0    # PySceneDetect ContentDetector threshold (27.0 = OpenShorts default)
SCENE_MIN_DURATION = 3.0  # skip cuts shorter than 3s (stops ticker/graphic flicker)
MAX_SCENES_LIMIT = 50     # cap total scenes per clip so 3-hour podcasts never OOM/crash


class FacePath(list):
    """List-compatible face path carrying verified OpenShorts routing data."""

    def __init__(self, values=(), speaker_segments=None, scene_bounds=None):
        super().__init__(values)
        self.speaker_segments = speaker_segments or []
        self.scene_bounds = scene_bounds or []


def detect_scenes(path: str, threshold: float = SCENE_THRESHOLD,
                  log=None, min_duration: float | None = None) -> list:
    """Scene boundaries via PySceneDetect (ContentDetector).

    Returns [(start, end), ...] covering [0, duration] with cuts detected
    by the content-aware detector. PySceneDetect uses histogram diff +
    optional edge detection, which is more accurate than ffmpeg's scdet
    for real content changes (vs. graphics/ticker flicker).

    Falls back to ffmpeg scdet if PySceneDetect is unavailable.
    """
    try:
        dur = get_duration(path)
    except Exception:
        log and log("scene detect: cannot read source, using single scene")
        return [(0.0, 600.0)]
    if dur <= 0:
        return [(0.0, 600.0)]
    log and log("detecting scene cuts (PySceneDetect)...")
    try:
        from scenedetect import detect as psd_detect, ContentDetector
        detector_options = {"threshold": threshold}
        if min_duration is not None and min_duration < 1.0:
            detector_options["min_scene_len"] = 1
        scene_list = psd_detect(
            path, ContentDetector(**detector_options),
            start_time=0, end_time=dur,
        )
        cuts = []
        for scene in scene_list:
            start = scene[0].get_seconds()
            end = scene[1].get_seconds()
            if end - start >= 0.1:  # skip sub-100ms glitches
                cuts.append(round(end, 2))  # cut point is at scene end
        cuts = sorted(set(cuts))
    except ImportError:
        log and log("⚠️ PySceneDetect not available, falling back to ffmpeg scdet")
        # ffmpeg scdet uses a 0-1 score scale (different from PySceneDetect's
        # ContentDetector threshold). 0.85 kills most ticker/graphics cuts.
        FFMPEG_SCD_THRESHOLD = 0.85
        r = subprocess.run(
            [_ffmpeg_executable(), "-hide_banner", "-nostats", "-i", path,
             "-vf", f"scdet=threshold={FFMPEG_SCD_THRESHOLD}",
             "-an", "-f", "null", "-"],
            capture_output=True, text=True,
        )
        cuts = []
        for line in r.stderr.splitlines():
            if "scdet" not in line:
                continue
            try:
                if "lavfi.scd.score" in line:
                    score = float(line.split("lavfi.scd.score:")[1]
                                  .split(",")[0].strip())
                    ts = float(line.split("lavfi.scd.time:")[1].strip())
                else:
                    ts = float(line.split("pts_time:")[1].split()[0])
                    score = float(line.split("score:")[1].strip())
            except (IndexError, ValueError):
                continue
            if score >= FFMPEG_SCD_THRESHOLD and 0.0 < ts < dur:
                cuts.append(round(ts, 2))
        cuts = sorted(set(cuts))
    except Exception as e:
        log and log(f"⚠️ PySceneDetect failed: {e}")
        cuts = []
    min_dur = (float(min_duration) if min_duration is not None else
               float(os.environ.get("SCENE_MIN_DURATION", SCENE_MIN_DURATION)))
    scenes = []
    prev = 0.0
    for c in cuts:
        if c - prev >= min_dur:
            scenes.append((prev, c))
            prev = c
    if dur - prev >= min_dur:
        scenes.append((prev, dur))
    if not scenes:
        scenes = [(0.0, dur)]
    # Cap total scenes to prevent OOM on long podcasts
    max_scenes = int(os.environ.get("MAX_SCENES_LIMIT", MAX_SCENES_LIMIT))
    if len(scenes) > max_scenes:
        # Too many scenes (e.g., news with ticker/graphics) — fallback to single GENERAL scene
        # This avoids OOM and wrong merge logic; single scene will use GENERAL mode (full frame + blur)
        log and log(f"⚠️ scene count {len(scenes)} > {max_scenes}, falling back to single scene")
        scenes = [(0.0, dur)]
    log and log(f"🎬 {len(scenes)} scenes")
    return scenes


def _frame(path: str, t: float, max_w: int = ANALYSIS_MAX_WIDTH):
    """Read one frame as BGR (scaled to max_w), or None."""
    cap = cv2.VideoCapture(path)
    cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
    ok, frame = cap.read()
    cap.release()
    if not ok or frame is None:
        return None
    h, w = frame.shape[:2]
    if w > max_w:
        scale = max_w / w
        frame = cv2.resize(frame, (max_w, int(h * scale)),
                           interpolation=cv2.INTER_AREA)
    return frame


def _count_people_in_frame(frame) -> int:
    """Count people in frame — combines face detection + YOLO person detection.

    MediaPipe only counts faces facing the camera. In a 2-person debate where
    one person faces away, face count drops to 1 → TRACK crops to one person
    even though BOTH are visible. By also checking YOLO person detection
    (which finds bodies regardless of face angle), we get the real count.

    Returns max(face_count, person_count) — the more inclusive count.
    """
    if frame is None or frame.size == 0:
        return 0
    # Face count (MediaPipe/Haar — only counts faces facing camera)
    boxes = face_boxes(frame)
    fw = float(frame.shape[1])
    face_count = 0
    if fw > 0:
        face_count = len([b for b in boxes if b[2] >= MIN_FACE_WIDTH_FRAC * fw])
    # YOLO person count (finds bodies regardless of face angle)
    try:
        from detector import _yolo_persons
        persons = _yolo_persons(frame)
        # Filter small persons (background extras, audience)
        persons = [p for p in persons if p[2] >= MIN_FACE_WIDTH_FRAC * fw] if fw > 0 else []
        person_count = len(persons)
    except Exception:
        person_count = 0
    # Use the more inclusive count — if YOLO finds 2 bodies but MediaPipe
    # only sees 1 face (person facing away), we still know there are 2 people.
    return max(face_count, person_count)


def _faces_in_frame(frame, cascade=None) -> list:
    """Detected faces as [(cx, cy, w, h)] in frame coords.

    `cascade` is kept for backwards compatibility but is ignored: face boxes
    now come from detector.face_boxes (MediaPipe -> YOLO -> Haar fallback).

    Faces narrower than MIN_FACE_WIDTH_FRAC of the frame width are dropped:
    they are background extras / audience / photos, not speakers (OpenShorts
    split_layout.MIN_FACE_WIDTH behaviour). Keeps a wide panel shot from
    inflating the face count to 3+ (-> GENERAL) while the actual speaker is
    a close-up; a talking head + a row of tiny audience faces still counts
    as ONE face and stays TRACK.
    """
    boxes = face_boxes(frame)
    if frame is not None and frame.size:
        fw = float(frame.shape[1])
        if fw > 0:
            boxes = [b for b in boxes if b[2] >= MIN_FACE_WIDTH_FRAC * fw]
    return boxes


def _strategy_for_faces(med: int, src_w: int = 1920, src_h: int = 1080,
                        genre: str = "",
                        face_sizes: list | None = None) -> str:
    """Map a median face count to a reframe strategy.

    OpenShorts parity (split_layout.py ENABLED=False by default):
      - 1 face -> TRACK: crop tightly to the single talking head.
      - 0, 2, or 3+ faces (crowd, panel debate, TV broadcast with 2 speakers /
        split-screen built-in) -> GENERAL: fit the WHOLE video in the center
        with a blurred backdrop so everyone stays visible and nobody gets
        cropped in half (e.g. ck3/ck4/ck5 where a TV split was cut weirdly).
      - SPLIT / PANEL is only used if explicitly enabled via SPLIT_LAYOUT=1.

    Genre overrides:
      - sports/gaming/tutorial: track_mode=off → never use TRACK, always
        CENTER or GENERAL so the full action/screen stays visible.
      - vlog/news: track_mode=relaxed → use TRACK for 1 face but with
        wider crop (handled by crop_style in reframe_clip).

    face_sizes: optional list of (w, h) tuples for each detected face.
      Used to detect a dominant speaker — one face significantly larger
      than others → TRACK to that face. Otherwise GENERAL (show all).
    """
    # Genre-based overrides: sports/gaming/tutorial should never crop to
    # a single face — the action/screen content is more important.
    if genre in ("sports", "gaming", "tutorial"):
        if med == 1:
            return "center"  # keep full frame, no face zoom
        return "general"     # 0/2/3+ faces → blurred fit

    if med == 1:
        return "track"
    if med == 2:
        # AUTO follows the OpenShorts shot-edit pattern demonstrated by os4:
        # a multi-person/group shot preserves its composition (GENERAL), while
        # a camera cut to one visible person becomes FACE/TRACK. Active-speaker
        # cuts for a persistent two-shot belong to the explicit FACE mode;
        # applying them globally made AUTO unexpectedly discard reactions and
        # other people in the shot.
        return "general"
    # 3+ faces (crowd, panel, TV broadcast): ALWAYS GENERAL.
    # Show the WHOLE video in the center with a blurred backdrop so everyone
    # stays visible — never TRACK to a dominant face. Tracking one face in a
    # 3+ person shot zooms in (crops others out) and the follow-cam jitters
    # when the "main" speaker shifts, which is exactly the getar users hate.
    # Matches the reference panels (iNews Rakyat Bersuara): full-frame fit +
    # blurred top/bottom, all subjects intact.
    if med >= 3:
        return "general"


def _strategy_for_requested_layout(requested: str, observed: str, faces: int,
                                   text_scene: bool = False) -> str:
    """Return a scene-safe strategy for a user-selected layout.

    A layout selection is a preference, not permission to fabricate subjects.
    Two-subject layouts are only valid while exactly two people coexist in the
    sampled shot.  This prevents a solo camera cut inside a debate from being
    duplicated/cropped into SPLIT or PANEL.  Conversely, FACE must not zoom
    into an empty slide or a group shot.
    """
    requested = str(requested or "").strip().lower()
    aliases = {"screencast": "gameplay", "face": "track"}
    requested = aliases.get(requested, requested)
    if requested in ("", "auto", "none", "user"):
        return observed

    if requested in ("split", "panel", "alternate"):
        if faces == 2:
            return requested
        if faces == 0:
            if requested == "split":
                # An explicit split request must stay on the speaker-follow
                # renderer. If face detection misses a frame, TRACK holds a
                # stable portrait position; GENERAL is never used as the
                # split fallback.
                return "track"
            return "gameplay" if observed == "gameplay" else "general"
        if faces == 1:
            # Explicit SPLIT's only safe fallback is the mature face/tracker
            # path. GENERAL creates the old fitted landscape band and is not
            # acceptable for a split request.
            return "track"
        if faces >= 3:
            # SPLIT is a two-person composition. When a split request sees a
            # larger group, keep the portrait output on the active speaker
            # tracker rather than falling back to GENERAL (which loses the
            # speaker and recreates the unwanted fitted landscape band).
            return "track" if requested == "split" else "general"
        return "track"

    if requested == "track":
        if faces in (1, 2):
            return "track"
        if faces >= 3:
            return "general"
        return "gameplay" if text_scene else "general"

    if requested == "gameplay":
        if text_scene or faces == 0:
            return "gameplay"
        if faces == 1:
            return "track"
        return "general"

    # CENTER and GENERAL are composition-safe for any subject count. Unknown
    # historical layout values degrade to the observed AUTO decision.
    if requested in ("center", "general"):
        return requested
    return observed


def _looks_text_scene(frame) -> bool:
    """Lightweight whiteboard/slide heuristic (OpenCV only, no OCR model).

    A text-dense scene (whiteboard, slide, screencast) is bright and full of
    sharp high-contrast edges. Cropping such a frame to 9:16 cuts off the
    board's right side, so we flag it and fit the FULL frame instead. Kept
    conservative (bright-area ratio AND edge density must both be high) to
    avoid misclassifying a bright talking-head background as text. The
    product threshold was 0.010 but a studio news ticker + bright graphics
    (bright~0.30 * edges~0.05) exceeded it on speaker shots, flipping 11/33
    scenes to 'general' and making the crop flicker between track and
    fit-frame. Raised to 0.018 so only genuine slide/whiteboard frames trip.
    """
    if frame is None or frame.size == 0:
        return False
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    # A slide / whiteboard / screencast fills the UPPER part of the frame. A
    # TV news ticker (bright + dense edges) sits at the very bottom, so it
    # alone must NOT trip this check — analyse only the top 65% of the frame
    # to ignore lower-thirds and tickers. Without this, a studio news clip
    # (bright graphic ~0.30 * edges ~0.10 = 0.030) flipped 11/33 scenes to
    # 'general', making the crop flicker between track and fit-frame.
    h = gray.shape[0]
    region = gray[:int(h * 0.65)]
    if region.size == 0:
        return False
    bright = (region > 205).mean()
    if bright < 0.18:
        return False
    edges = cv2.Canny(region, 60, 160)
    edge_density = (edges > 0).mean()
    return bright * edge_density > 0.018


def _filter_sampled_faces(path, samples, frame_w, start=0.0, end=None, log=None):
    """Apply source-coordinate poster evidence to detector-sized samples."""
    from video import _drop_static_boxes
    source_w, _ = get_resolution(path)
    if source_w <= 0 or frame_w <= 0:
        return samples
    scale = source_w / float(frame_w)
    source_boxes = [[tuple(v * scale for v in box) for box in faces]
                    for faces in samples]
    filtered = _drop_static_boxes(path, source_boxes, log=log, start=start, end=end)
    return [[tuple(v / scale for v in box) for box in faces] for faces in filtered]


def scene_strategies(path: str, scenes: list, log=None, genre: str = "",
                     layout_prior: str = "",
                     requested_layout: str = "") -> list:
    """Per-scene reframe strategy from face count + text-scene heuristic + active speaker."""
    out = []
    for (s, e) in scenes:
        if e - s < 0.1:
            continue
        mid = (s + e) / 2
        counts = []
        text_votes = 0
        face_boxes_list = []
        analysis_w = 0
        for dt in (-0.3, 0.0, 0.3):
            # Sampling short shots must never read the following camera angle.
            frame = _frame(path, min(e - min(0.04, (e - s) / 4),
                                     max(s, mid + dt)))
            # Use _count_people_in_frame for strategy — combines face + YOLO
            # person detection so a person facing away still counts.
            counts.append(_count_people_in_frame(frame))
            # Keep _faces_in_frame for active speaker (needs face boxes)
            # Speaker candidates must be real face detections. YOLO body boxes
            # are allowed only when every face detector fails, as a framing
            # anchor—not as a trustworthy mouth/speaker measurement.
            faces = verified_face_boxes(frame)
            if faces:
                faces = [b for b in faces
                         if b[2] >= MIN_FACE_WIDTH_FRAC * frame.shape[1]]
            else:
                faces = _faces_in_frame(frame)
            text_votes += int(_looks_text_scene(frame))
            face_boxes_list.append(faces)
            if frame is not None and frame.size:
                analysis_w = frame.shape[1]
        filtered = _filter_sampled_faces(path, face_boxes_list, analysis_w, s, e, log)
        # Remove rejected printed faces from the inclusive body/face count as
        # well; YOLO detecting a printed torso must not add the poster back.
        counts = [max(0, count - (len(before) - len(after)))
                  for count, before, after in zip(counts, face_boxes_list, filtered)]
        face_boxes_list = filtered
        med = int(np.median(counts))
        w, h = get_resolution(path)

        # Strategy: a detected face wins over the text heuristic. A talking
        # head in a news studio sits in front of a bright graphic wall, so the
        # whiteboard/slide check fires on the backdrop and would force 'general'
        # (fit-frame) — which both drops the speaker crop AND, when it flickers
        # against 'track' scene-to-scene, makes the whole clip shake. OpenShorts
        # keeps the face tracked in exactly this case. So: 1+ face -> always
        # track/panel/split; only 0 faces (true no-speaker/slide) -> 'general'.
        # Collect face sizes for dominant speaker detection (3+ faces)
        all_face_sizes = []
        for boxes in face_boxes_list:
            for b in boxes:
                all_face_sizes.append((b[2], b[3]))  # (w, h) per face
        if med >= 1:
            strat = _strategy_for_faces(med, w, h, genre=genre,
                                        face_sizes=all_face_sizes or None)
        else:
            # Readable slides/screen recordings need the dedicated full-width
            # layout. Ordinary no-face/wide shots retain the safe fitted frame.
            strat = "gameplay" if (text_votes >= 2 or layout_prior == "screencast") else "general"

        text_scene = text_votes >= 2
        strat = _strategy_for_requested_layout(
            requested_layout, strat, med, text_scene=text_scene)

        # AI layout priors cannot turn an observed group shot into a crop.
        # Explicit modes are preferences filtered through the per-scene
        # compatibility matrix above.
        # Attach genre-derived crop_style & track_mode so reframe_clip can
        # use them without re-querying the genre detection.
        from genre import crop_style_height_ratio, track_mode_from_genre
        entry = {"start": s, "end": e, "strategy": strat,
                 "faces": med, "text": text_scene,
                 "crop_ratio": crop_style_height_ratio(genre),
                 "track_mode": track_mode_from_genre(genre)}
        out.append(entry)

    # Scene hysteresis (OpenShorts parity): short scenes (<2s) whose
    # neighbors agree on the same strategy flip to match, preventing
    # flicker from a brief misclassification (e.g. a 1.5s crowd shot
    # between two TRACK scenes flickers to GENERAL then back).
    HYSTERESIS_MAX_DUR = 2.0  # seconds — scenes shorter than this get hysteresis
    for i in range(1, len(out) - 1):
        dur_i = out[i]["end"] - out[i]["start"]
        if dur_i >= HYSTERESIS_MAX_DUR:
            continue
        prev_s = out[i - 1]["strategy"]
        next_s = out[i + 1]["strategy"]
        if prev_s == next_s and out[i]["strategy"] != prev_s:
            # Never let visual hysteresis undo a compatibility fallback. A
            # short solo cut between two debate shots must remain FACE, not
            # become SPLIT merely because both neighboring scenes are split.
            # Never let visual hysteresis undo a compatibility fallback. Two-subject
            # layouts (split/panel/alternate) require evidence of a real two-shot; a
            # brief solo cut (FACE/GENERAL fallback) among debate shots must stay
            # FACE, not be force-promoted to SPLIT just because both neighbours split.This
            # holds in every mode (incl. AUTO), where requested_primary is empty.Continue
            two_subject = {"split", "panel", "alternate"}
            fallback_safe = {"track", "gameplay", "general", "center"}
            if prev_s in two_subject and out[i]["strategy"] in fallback_safe:
                continue
            requested_primary = {
                "face": "track", "screencast": "gameplay",
            }.get(str(requested_layout or "").lower(),
                  str(requested_layout or "").lower())
            if requested_primary and requested_primary not in ("auto", "none", "user"):
                if out[i]["strategy"] != requested_primary:
                    continue
            log and log(f"🔄 Hysteresis: scene {out[i]['start']:.0f}-"
                        f"{out[i]['end']:.0f} ({dur_i:.1f}s) {out[i]['strategy']}"
                        f" → {prev_s} (matches neighbors)")
            out[i]["strategy"] = prev_s

    log and log("🎥 " + ", ".join(
        f"[{s['start']:.0f}-{s['end']:.0f}]{s['strategy'][0].upper()}"
        for s in out))
    return out


def _two_face_count(src: str) -> int:
    """Count verified coexisting faces in src (OpenShorts two-shot check).

    Returns 2 when both speakers genuinely share the frame (same sampling
    routine as the active-speaker router below, so the two can never
    disagree), 1 when at least one face is seen, else 0 — so the
    speaker-cut path knows it has someone to cut TO instead of fabricating
    a second face. Cheap: sampled frames only.
    """
    try:
        return max(_two_face_count_impl(src, 8), 0)
    except Exception:
        return 0


def _coexisting_pair_samples(path: str, start: float, end: float,
                             samples: int = 8):
    """Per-sample verified-pair hits for one time range.

    Single sampling routine behind BOTH the tripod/continuous gate
    (_two_face_count) and the active-speaker router
    (_openshorts_speaker_segments): each sample contributes its median pair
    halves when exactly the same two separated subjects share the frame
    (canonical single-frame _coexisting_pair: size-filtered, deduped,
    horizontally separated). Returns (readable, lefts, rights, any_face).
    """
    lefts, rights = [], []
    readable = 0
    any_face = False
    sampled = []
    frame_w = 0
    for i in range(max(1, samples)):
        t = start + (end - start) * (i + 1) / (samples + 1)
        frame = _frame(path, t)
        if frame is None or not frame.size:
            continue
        readable += 1
        faces = _faces_in_frame(frame)
        sampled.append(faces)
        frame_w = float(frame.shape[1])
    for faces in _filter_sampled_faces(path, sampled, frame_w, start, end):
        if faces:
            any_face = True
        pair = _coexisting_pair(faces, frame_w)
        if pair:
            lefts.append(pair[0])
            rights.append(pair[1])
    return readable, lefts, rights, any_face


def _pair_is_stable(lefts: list, rights: list, frame_w: float) -> bool:
    """True when the pair is the SAME two seated people, not passing noise.

    A real debate pair holds its seats: both halves' centres must stay
    within 15% of the frame width across samples. A bystander photobomb or
    detector false positive jumps around and fails this, so lowering the
    coexistence bar for profile-heavy footage cannot admit a phantom pair.
    """
    if not lefts or frame_w <= 0:
        return False
    try:
        import numpy as _np
        sx = float(_np.std([p[0] for p in lefts]))
        dx = float(_np.std([p[0] for p in rights]))
    except (TypeError, ValueError, IndexError):
        return False
    return sx <= 0.15 * frame_w and dx <= 0.15 * frame_w


def _two_face_count_impl(src: str, samples: int = 8) -> int:
    dur = get_duration(src)
    if dur <= 0:
        return 0
    readable, lefts, _rights, any_face = _coexisting_pair_samples(
        src, 0.0, dur, samples)
    if readable and len(lefts) >= max(2, (readable + 1) // 2):
        return 2
    return 1 if any_face else 0


def clip_strategy(path: str, log=None, genre: str = "") -> str:
    """Pick the reframe strategy for a single already-extracted clip from the
    median face count sampled across it (used by re-renders that don't have
    a full-video scene plan)."""
    dur = get_duration(path)
    if dur <= 0:
        return "general"
    counts = []
    text_votes = 0
    face_sizes = []  # (w, h) per detected face for dominant speaker check
    for t in (0.3, 0.5, 0.7):
        frame = _frame(path, max(0.2, dur * t))
        counts.append(_count_people_in_frame(frame))
        text_votes += int(_looks_text_scene(frame))
        # Collect face sizes for dominant speaker detection
        boxes = _faces_in_frame(frame)
        for b in boxes:
            face_sizes.append((b[2], b[3]))
    med = int(np.median(counts)) if counts else 0
    w, h = get_resolution(path)
    strat = "general" if text_votes >= 2 else _strategy_for_faces(
        med, w, h, genre=genre, face_sizes=face_sizes or None)
    log and log(f"🎥 clip strategy: {strat} ({med} faces, "
                f"{'text' if text_votes >= 2 else 'talk'}, genre={genre or 'none'})")
    return strat


def strategy_at(strategies: list, t: float) -> str:
    """Strategy active at time `t` (seconds), default 'general' (fit frame + blur bg)."""
    for s in strategies:
        if s["start"] <= t < s["end"]:
            return s["strategy"]
    return "general"


def scene_at(strategies: list, t: float) -> dict:
    """Full scene entry active at time `t`, with strategy + genre params."""
    for s in strategies:
        if s["start"] <= t < s["end"]:
            return s
    return {"strategy": "general", "crop_ratio": 1.0, "track_mode": "aggressive"}


def _audio_gate(path: str, dur: float, n_samples: int,
                floor: float = 0.18) -> list:
    """Per-window normalised audio RMS (peak = 1.0) to gate mouth motion.

    Ported from QuClippers active_speaker.audio_envelope: a window only
    counts as speech when its RMS clears this fraction of the scene's
    loudest window, so a nod/laugh never scores as talking. Returns bools
    (True = speech) aligned 1:1 with the _face_path sample windows.
    """
    if n_samples < 2 or dur <= 0:
        return [True] * n_samples
    try:
        raw = subprocess.run(
            [_ffmpeg_executable(), "-v", "error", "-i", path, "-vn", "-ac", "1",
             "-ar", "8000", "-f", "s16le", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=300).stdout
    except (subprocess.SubprocessError, OSError):
        return [True] * n_samples  # no audio track -> trust mouth only
    if not raw:
        return [True] * n_samples
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
    win = max(1, len(samples) // n_samples)
    n = len(samples) // win
    if n < 1:
        return [True] * n_samples
    rms = np.sqrt(np.mean(samples[:n * win].reshape(n, win) ** 2, axis=1))
    peak = rms.max()
    if peak <= 0:
        return [True] * n_samples
    # Stretch the per-window envelope to n_samples (windows may differ in
    # count after ffmpeg decode drift).
    idx = np.linspace(0, n - 1, n_samples).astype(int)
    return (rms[idx] / peak >= floor).tolist()


def _face_path(path: str, log=None) -> list:
    """Sample the ACTIVE-SPEAKER face center across the clip at TRACK_FPS.

    CPU-only proxy for the QuClippers active-speaker cut (no diarization
    model): faces are tracked frame-to-frame by temporal proximity, and each
    one scores "mouth motion" (mean abs diff of the lower-face ROI between
    consecutive samples). The face that is talking moves its mouth most, so a
    head-to-head / podcast switches between the two people as each speaks
    instead of pinning a single face. A small hysteresis stops rapid flicker.

    The raw sample path is then smoothed with a short median filter so
    detector wobble doesn't reach the camera — OpenShorts delivers a stable
    tripod look, and unfiltered cx/cy (range 254-557 in the test clip) made
    the crop chase the face and shake. Returns [(t, cx, cy)] in SOURCE coords.
    """
    # For a verified two-shot, OpenShorts' mature active-speaker decision is
    # authoritative.  The generic tracker above remains useful for a single
    # talking head, camera cuts and detector misses, but its rolling scores are
    # not comparable between two differently lit/sized faces.  OpenShorts
    # normalises each mouth against that person's own resting range, applies
    # the audio gate, then requires three consecutive 0.4 s verdicts before a
    # cut.  Overlay those held decisions per scene onto the generic path.
    face_scenes = detect_scenes(path, log=log, min_duration=0.60)
    speaker_segments = _openshorts_speaker_segments(
        path, log, scenes=face_scenes)
    dur = get_duration(path)
    # Avoid the expensive 12 fps generic face/YOLO pass when OpenShorts has
    # already produced authoritative coverage for the clip. This used to
    # decode/detect the same 60 seconds twice on Modal.
    covered = sum(max(0.0, end - start)
                  for start, end, _centres, _verdicts in speaker_segments)
    if speaker_segments and dur > 0 and covered >= dur * 0.95:
        step = 1.0 / TRACK_FPS
        pts = []
        t = 0.0
        while t <= dur + step:
            chosen = None
            for start, end, centres, verdicts in speaker_segments:
                if start <= min(t, dur) <= end and verdicts:
                    wi = min(len(verdicts) - 1,
                             max(0, int((min(t, dur) - start) /
                                        WINDOW_SECONDS)))
                    who = verdicts[wi]
                    if who is not None:
                        chosen = centres[min(int(who), 1)]
                    break
            if chosen:
                pts.append((min(t, dur), float(chosen[0]), float(chosen[1])))
            t += step
    else:
        pts = _face_path_impl(path, log, scenes=face_scenes)
    if speaker_segments and pts:
        routed = []
        for t, cx, cy in pts:
            replacement = None
            for start, end, centres, verdicts in speaker_segments:
                if start <= t < end and verdicts:
                    wi = min(len(verdicts) - 1,
                             max(0, int((t - start) / WINDOW_SECONDS)))
                    who = verdicts[wi]
                    if who is not None:
                        replacement = centres[min(int(who), 1)]
                    break
            routed.append((t, float(replacement[0]), float(replacement[1]))
                          if replacement else (t, cx, cy))
        pts = routed
    if len(pts) < 3:
        return FacePath(pts, speaker_segments, face_scenes)
    ts = [p[0] for p in pts]
    xs = np.array([p[1] for p in pts], dtype=float)
    ys = np.array([p[2] for p in pts], dtype=float)
    k = 5 if len(pts) >= 5 else (3 if len(pts) >= 3 else 1)
    # pad the edges so every sample keeps a full window (median preserves the
    # big deliberate pans while killing 1-2 sample spikes).
    xp = np.pad(xs, (k // 2, k // 2), mode="edge")
    yp = np.pad(ys, (k // 2, k // 2), mode="edge")
    xs = np.array([np.median(xp[i:i + k]) for i in range(len(pts))])
    ys = np.array([np.median(yp[i:i + k]) for i in range(len(pts))])
    return FacePath(
        [(ts[i], float(xs[i]), float(ys[i])) for i in range(len(pts))],
        speaker_segments, face_scenes,
    )


def _has_verified_two_shot(src: str) -> bool:
    """True when two speakers genuinely coexist in the same frames.

    Tripod rule: continuous follow-cam (``_face_path`` + ``_held_speaker_track``)
    is only worth its detector noise when there is someone to cut TO. A single
    talking head stays on a static median lock — diem, no getar.
    Cheap: reuses the OpenShorts coexistence check (5 samples, median boxes).
    """
    try:
        return _two_face_count(src) >= 2
    except Exception:
        return False


# Turn-based camera-switch (pattern from opensource-clipping's
# --camera-switch: switch-hold 2.0s, blend 0 = instant snap). Between turns the
# crop is piecewise-CONSTANT — zero glide, zero chase. A turn only changes
# hands when the challenger holds the floor for the full hold window, so a
# laugh/nod/interjection never steals the frame ("tidak ke yang bicara").
# Hold is 4.0s (measured on clip1 "Negara Bukan Kampung", a heated 2-speaker
# debate: 2.0s gave 13 cuts with 4 ping-pong turns under 4s; 4.0s gives 7 calm
# turns that still follow the active speaker; 5.0s overshot and pinned the
# wrong speaker for 44s straight). Prioritise the active speaker, never dizzy.
SPEAKER_MIN_HOLD_SEC = 4.0
SPEAKER_SNAP_FRAC = 0.15  # target windows closer than this => no cut


def _compress_turns(verdicts: list, hold_windows: int) -> list:
    """Compress per-window verdicts into committed speaker turns.

    Returns [(speaker, n_windows), ...]. ``None`` windows extend the current
    turn (silence keeps the last speaker on screen). A challenger must persist
    for ``hold_windows`` consecutive windows before the turn flips — brief
    verdict flicker between two faces therefore never reaches the camera. The
    flip is backdated to the challenger's first window, so debouncing adds
    zero cut latency (unlike stacking another hold on top of ``hold()``).
    """
    starts: list = []  # [speaker, start_window_idx]
    committed = None
    challenger = None
    run = 0
    for i, v in enumerate(verdicts):
        if committed is None:
            committed = v if v is not None else 0
            starts.append([committed, 0])
        if v is None or v == committed:
            challenger, run = None, 0
        else:
            challenger, run = (v, 1) if v != challenger else (v, run + 1)
            if run >= hold_windows:
                committed = challenger
                challenger, run = None, 0
                starts.append([committed, i - hold_windows + 1])
    out = []
    for k, (s, st) in enumerate(starts):
        nx = starts[k + 1][1] if k + 1 < len(starts) else len(verdicts)
        if nx > st:
            out.append((int(s), int(nx - st)))
    return out


def _held_speaker_track(pts: list, w: int, h: int, ww: int, wh: int,
                        fps: float, duration: float) -> list:
    """Convert held OpenShorts verdicts to stable per-frame hard cuts.

    Turn-based camera-switch: each speaker owns ONE static window per turn and
    the crop cuts (never glides) between them, with a minimum hold of
    ``SPEAKER_MIN_HOLD_SEC``. Between verified two-shots, retain the smoothed
    generic trajectory for single-speaker/fallback scenes.

    Shoulder-to-shoulder guard: when the two verified centres sit closer than
    15% of the crop width, a hard snap only chases detector wobble between two
    near-identical windows — keep the smoothed base instead so the camera
    holds still.
    """
    scene_bounds = getattr(pts, "scene_bounds", [])
    if scene_bounds:
        base = []
        last_position = (max(0, round((w - ww) / 2)),
                         max(0, round((h - wh) / 2)))
        for start, end in scene_bounds:
            count = max(1, int(round((end - start) * fps)))
            local = [(max(0.0, t - start), cx, cy)
                     for t, cx, cy in pts if start <= t < end]
            if local:
                # Tripod talking-head footage should feel locked. Active
                # speaker selection may change, but the crop must pan slowly
                # and ignore detector jitter between confirmed switches.
                shot = _smooth_track(local, w, h, ww, wh, fps=fps,
                                     track_mode="relaxed",
                                     snap_big_moves=False)
                shot = (shot + [shot[-1]] * count)[:count]
                last_position = shot[-1]
            else:
                shot = [last_position] * count
            base.extend(shot)
    else:
        base = _smooth_track(pts, w, h, ww, wh, fps=fps,
                             track_mode="relaxed", snap_big_moves=False)
    segments = getattr(pts, "speaker_segments", [])
    if not segments:
        return base
    total = max(len(base), int(round(duration * fps)) + 1)
    if len(base) < total:
        base.extend([base[-1]] * (total - len(base)))
    for start, end, centres, verdicts in segments:
        # Shoulder-to-shoulder pairs produce near-identical windows; snapping
        # between them only renders detector wobble as camera motion.
        try:
            separation = abs(float(centres[0][0]) - float(centres[1][0]))
        except (IndexError, TypeError, ValueError):
            separation = float(ww)
        if separation < ww * SPEAKER_SNAP_FRAC:
            continue
        # One static window per speaker: precompute both, then cut between
        # them turn-by-turn (no per-frame recompute, no glide).
        windows = []
        for cx, cy in (centres[0], centres[1]):
            windows.append((
                max(0, min(round(float(cx) - ww / 2), w - ww)),
                max(0, min(round(float(cy) - wh * 0.38), h - wh)),
            ))
        from active_speaker import WINDOW_SECONDS as _WS
        hold_n = max(2, int(round(SPEAKER_MIN_HOLD_SEC / _WS)))
        # Walk turns in window units, converting to frame ranges.
        cursor = max(0, int(round(start * fps)))
        seg_last = min(total, int(round(end * fps)))
        for speaker, n_win in _compress_turns(list(verdicts), hold_n):
            f1 = min(seg_last, cursor + int(round(n_win * _WS * fps)))
            if f1 > cursor:
                base[cursor:f1] = [windows[min(int(speaker), 1)]] * (f1 - cursor)
            cursor = f1
            if cursor >= seg_last:
                break
    return base


def _openshorts_speaker_segments(path: str, log=None,
                                 scenes: list | None = None) -> list:
    """OpenShorts active-speaker routing for verified two-shots, per scene.

    Returns ``[(start, end, centres, held_verdicts), ...]``.  Detection uses
    the same coexistence, face-size and horizontal-separation rules as
    OpenShorts ``split_layout``. YOLO is deliberately absent here: a body box
    has no trustworthy mouth ROI and therefore cannot decide who is speaking.
    """
    dur = get_duration(path)
    w, h = get_resolution(path)
    if dur <= 0 or w <= 0 or h <= 0:
        return []
    routed = []
    for start, end in (scenes or detect_scenes(
            path, log=log, min_duration=0.60)):
        scene_dur = end - start
        if scene_dur < 1.2:
            continue
        sample_count = max(8, min(24, int(np.ceil(scene_dur / 1.5))))
        # Shared sampler with the tripod/continuous gate: profile-heavy
        # two-shots (listener turned sideways) coexist in fewer frames
        # than the strict 50% bar, so require 30% PLUS seat stability —
        # the same two separated people holding still, never a drifting
        # false positive. A plano/contraplano edit still scores ~0%.
        readable, lefts, rights, _any = _coexisting_pair_samples(
            path, start, end, sample_count)
        if not readable or len(lefts) < 0.3 * readable:
            continue
        if not _pair_is_stable(lefts, rights, float(w)):
            continue
        # Median boxes are important: decode_activity expects a stable
        # mouth ROI and OpenShorts explicitly avoids per-frame redetection.
        # (Sampler boxes are already in source coords — no rescale.)
        left = tuple(float(np.median([p[j] for p in lefts]))
                     for j in range(4))
        right = tuple(float(np.median([p[j] for p in rights]))
                      for j in range(4))
        boxes = [left, right]
        activity = decode_activity(path, start, scene_dur, boxes)
        if not activity:
            continue
        verdicts = hold(attribute_windows(
            normalise_activity(activity),
            audio_envelope(path, start, scene_dur),
        ))
        if not verdicts or all(v is None for v in verdicts):
            continue
        centres = [(left[0], left[1]), (right[0], right[1])]
        routed.append((start, end, centres, verdicts))
        log and log(
            f"🎙️ Klipin active-speaker cut {start:.1f}-{end:.1f}s "
            f"({len(verdicts)} windows, hold=3)"
        )
    return routed


def _face_path_impl(path: str, log=None, audio_gate: bool = True,
                    scenes: list | None = None) -> list:
    w, h = get_resolution(path)
    dur = get_duration(path)
    if dur <= 0:
        return []
    scale = 1.0
    fr_w, fr_h = w, h
    if w > ANALYSIS_MAX_WIDTH:
        scale = ANALYSIS_MAX_WIDTH / w
        fr_w, fr_h = ANALYSIS_MAX_WIDTH, int(h * scale)
    step = 1.0 / TRACK_FPS
    cap = cv2.VideoCapture(path)
    cap.set(cv2.CAP_PROP_POS_MSEC, 0)
    pts = []
    prev_faces = []      # [(id, cx, cy, w, h)] from the previous sample
    prev_mouth = {}      # id -> mouth ROI grayscale (for motion)
    motion = {}          # id -> running mouth-motion score
    active_id = None
    switch_run = 0
    t = 0.0
    # Audio gate (ported from QuClippers): only trust mouth motion while the
    # audio says somebody is actually talking. Without it, a nod, a laugh or
    # a chewed sweet scores as speech and flips the crop to the wrong person.
    n_samples = max(1, int(dur / step))
    audio_flags = _audio_gate(path, dur, n_samples) if audio_gate else \
        [True] * n_samples
    audio_i = 0
    scene_starts = [float(s) for s, _e in (scenes or []) if float(s) > 0]
    scene_i = 0
    probe_faces = []
    for at in np.linspace(0.1, max(0.1, dur - 0.1), 8):
        probe = _frame(path, float(at), max_w=fr_w)
        probe_faces.append(verified_face_boxes(probe) if probe is not None else [])
    live_probes = _filter_sampled_faces(path, probe_faces, fr_w, 0.0, dur, log)
    rejected = [box for before, after in zip(probe_faces, live_probes)
                for box in before if not any(np.allclose(box, b) for b in after)]
    while t <= dur + step:
        if scene_i < len(scene_starts) and t >= scene_starts[scene_i] - step / 2:
            prev_faces = []
            prev_mouth = {}
            motion = {}
            active_id = None
            switch_run = 0
            scene_i += 1
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, frame = cap.read()
        if ok and frame is not None:
            if frame.shape[1] > ANALYSIS_MAX_WIDTH:
                frame = cv2.resize(frame, (fr_w, fr_h),
                                   interpolation=cv2.INTER_AREA)
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            # FACE is a strict face-targeting contract. Never substitute a
            # YOLO body box (or another pseudo-face) here: its centre can sit
            # on a torso, table or empty gap between speakers. On a temporary
            # detector miss we retain the last verified face below instead.
            faces = verified_face_boxes(frame)
            if faces:
                faces = [b for b in faces
                         if b[2] >= MIN_FACE_WIDTH_FRAC * frame.shape[1]]
                faces = [b for b in faces if not any(
                    (b[0] - p[0]) ** 2 + (b[1] - p[1]) ** 2
                    <= (max(fr_w, fr_h) * 0.06) ** 2 for p in rejected)]
            talking = audio_flags[min(audio_i, len(audio_flags) - 1)] \
                if audio_flags else True
            cur = []
            if faces:
                ids = [None] * len(faces)
                for fi, f in enumerate(faces):
                    best, bd = None, float("inf")
                    for pi, p in enumerate(prev_faces):
                        if ids.count(p[0]):
                            continue
                        d = (f[0] - p[1]) ** 2 + (f[1] - p[2]) ** 2
                        if d < bd:
                            bd, best = d, p[0]
                    if best is None or bd > (0.38 * max(f[3], 24)) ** 2:
                        best = max([p[0] for p in prev_faces], default=-1) + 1
                    ids[fi] = best
                for fi, f in enumerate(faces):
                    cid = ids[fi]
                    cx, cy, fw, fh = f
                    from active_speaker import mouth_region
                    x0, y0, x1, y1 = mouth_region(f, gray.shape[1], gray.shape[0])
                    roi = gray[y0:y1, x0:x1]
                    old = prev_mouth.get(cid)
                    if old is not None and roi.size and old.shape == roi.shape:
                        motion[cid] = (0.85 * motion.get(cid, 0.0) +
                                       0.15 * float(np.abs(roi.astype(np.float32) -
                                                           old.astype(np.float32)).mean()))
                    else:
                        motion[cid] = motion.get(cid, 0.0) * 0.85
                    prev_mouth[cid] = roi
                    cur.append((cid, cx, cy, fw, fh))
            # When face detection briefly misses (head turn/occlusion), hold
            # the last verified face. Never jump to frame centre or a body box.
            if not cur and prev_faces and active_id is not None:
                last = next((p for p in prev_faces if p[0] == active_id), None)
                if last:
                    cur = [last]  # carry forward — camera holds position
            prev_faces = cur
            if cur:
                if active_id is None or active_id not in motion:
                    # first sample / lost track: pick the face whose MOUTH MOVES
                    # most (pure motion). Size was used here before and it made
                    # the camera lock onto a large static object (money/logo)
                    # instead of the small but actually-speaking face at the
                    # frame edge (35% width). Only when NO face has accumulated
                    # any motion yet (the very first frame) fall back to the
                    # largest face — a wrong first pick is corrected as soon
                    # as motion accumulates.
                    if max(motion.get(c[0], 0.0) for c in cur) <= 0:
                        active_id = max(cur, key=lambda c: c[3] * c[4])[0]
                    else:
                        active_id = max(cur,
                                        key=lambda c: motion.get(c[0], 0.0))[0]
                else:
                    am = motion.get(active_id, 0.0)
                    challenger = max(cur, key=lambda c: motion.get(c[0], 0.0))
                    cm = motion.get(challenger[0], 0.0)
                    # Only switch while somebody is actually speaking: the
                    # mouth-motion gate keeps a silent nod/laugh from stealing
                    # the frame (audio_envelope floor 0.18).
                    # Turn-taking pattern (opensource-clipping MIN_HOLD): a
                    # challenger must DOMINATE mouth motion (~2x, not 1.45x) for
                    # 4 consecutive samples (~0.33s @12Hz) while audio confirms
                    # speech. Anything less is a nod/laugh/chew stealing the
                    # frame — the exact "tidak ke yang bicara + gerak-gerak".
                    if talking and (challenger[0] != active_id
                                    and cm > am * 2.0 + 0.50):
                        switch_run += 1
                        if switch_run >= 4:
                            active_id = challenger[0]
                            switch_run = 0
                    else:
                        switch_run = 0
                act = next((c for c in cur if c[0] == active_id), None)
                if act is None:
                    act = max(cur, key=lambda c: c[3] * c[4])
                pts.append((t, float(act[1] / scale), float(act[2] / scale)))
        t += step
        audio_i += 1
    cap.release()
    return pts


def _smooth_track(pts: list, w: int, h: int, ww: int, wh: int,
                  fps: float = 30.0, track_mode: str = "aggressive",
                  snap_big_moves: bool = False) -> list:
    """Turn sampled face centers into per-frame smoothed crop positions.

    "Heavy tripod" camera (the model QuClippers proved stable):
      - a candidate center further than BIG_MOVE_FRAC of the window must
        persist JUMP_CONFIRM consecutive samples before the camera commits
        to it (kills false-positive whip-pans);
      - while the committed target stays inside DEAD_ZONE_FRAC of the crop
        window, the camera holds STILL — detector wobble never moves it;
      - once the target leaves the dead zone, the camera pans at a CONSTANT
        linear speed (slow, or fast for a re-frame) and STOPS exactly on
        target — no asymptotic easing that keeps moving every frame.

    The old exponential ease (alpha=0.08 toward every raw sample) made the
    camera chase the face's sub-pixel wobble forever — the constant
    micro-movement that reads as "getar" (shaking) on delivered clips.
    """
    n = int(round((pts[-1][0] if pts else 0) * fps)) + 1
    out = []
    if pts:
        px, py = pts[0][1], pts[0][2]
    else:
        px, py = w / 2.0, h / 2.0
    tx, ty = px, py          # committed camera target (follows confirmed samples)
    confirm = 0
    pending = None
    last_confirm_idx = -1    # sample idx of the last counted confirmation
    idx = 0
    big = BIG_MOVE_FRAC * ww
    # Dead zone: the camera holds STILL while the target stays inside it.
    # Size it as a fraction of the window itself, clamped to the pan room
    # on each axis so a near-full-frame window (tiny room) still has a
    # non-zero zone. The old `min(ww,wh,pan_room_x,pan_room_y)` collapsed
    # to 0 when pan_room_y=0 (window=source height on 640x360), which made
    # the zone ~0px and the camera chased every sub-pixel wobble — the
    # constant micro-movement users see as "getar" (shaking).
    pan_room_x = max(0, w - ww)
    pan_room_y = max(0, h - wh)
    dz_x = min(DEAD_ZONE_FRAC * ww, pan_room_x * 0.4) if pan_room_x > 0 else 0
    dz_y = min(DEAD_ZONE_FRAC * wh, pan_room_y * 0.4) if pan_room_y > 0 else 0
    dz = max(dz_x, dz_y)
    # Genre-aware track_mode: relaxed uses wider dead zone and slower pan
    # so the camera doesn't chase every micro-movement during casual vlogs.
    if track_mode == "relaxed":
        dz *= 1.2             # 20% wider dead zone (tadi 50% terlalu lebar)
    speed_k = fps / 30.0     # pan speeds are defined @30fps
    pan_speed = PAN_SPEED * (0.7 if track_mode == "relaxed" else 1.0)
    fast_pan_speed = FAST_PAN_SPEED * (0.7 if track_mode == "relaxed" else 1.0)
    for f in range(n):
        t = f / fps
        # advance sample pointer
        while idx < len(pts) - 1 and pts[idx + 1][0] <= t:
            idx += 1
        if idx < len(pts):
            _t, sx, sy = pts[idx]
            if abs(sx - tx) > big or abs(sy - ty) > big:
                # A jump far from the committed target must repeat across
                # DISTINCT detections before the camera follows it;
                # contradicting outliers reset it. Confirmations count samples,
                # not frames — at 6Hz sampling a single-sample glitch is seen
                # by ~5 frames, so counting frames would commit one-off
                # detector errors as if they were real moves.
                if pending is None or abs(sx - pending[0]) > big or \
                        abs(sy - pending[1]) > big:
                    pending = (sx, sy)
                    confirm = 1
                    last_confirm_idx = idx
                else:
                    if idx != last_confirm_idx:
                        confirm += 1
                        last_confirm_idx = idx
                    if confirm >= JUMP_CONFIRM:
                        tx, ty = sx, sy
                        if snap_big_moves:
                            px, py = sx, sy
                        pending = None
                        confirm = 0
            else:
                pending = None
                confirm = 0
                tx, ty = sx, sy
        # Heavy-tripod pan: hold inside the dead zone, otherwise move toward
        # the target at constant speed and stop exactly on arrival.
        dx, dy = tx - px, ty - py
        dist = (dx * dx + dy * dy) ** 0.5
        if dist > dz:
            speed = (fast_pan_speed if dist > ww * 0.5 else pan_speed) * speed_k
            step = min(dist, speed)
            px += dx / dist * step
            py += dy / dist * step
            # snap only sub-pixel leftovers so the camera actually rests
            if abs(tx - px) < 0.5:
                px = tx
            if abs(ty - py) < 0.5:
                py = ty
        # clamp window so it stays inside the frame
        x0 = max(0, min(px - ww / 2, w - ww))
        y0 = max(0, min(py - wh / 2, h - wh))
        out.append((round(x0), round(y0)))
    return out


def _locked_track(pts: list, w: int, h: int, ww: int, wh: int,
                   fps: float, duration: float | None = None,
                   face_wh: tuple | None = None) -> list:
    """One stable face-centred crop for a single-speaker scene.

    Scene detection already splits camera cuts before TRACK is rendered.  A
    median lock inside each resulting shot is therefore both more accurate
    and calmer than following detector noise frame by frame.  In particular,
    it avoids the old ``center_all`` failure where a disappeared speaker track
    remained alive and the camera aimed at the empty midpoint between the old
    and new faces.

    Whole-face guarantee: the lock window is shifted (never resized) just
    enough that the full face box — median face w/h plus a ~12% margin and
    headroom — stays inside the crop. The camera itself never moves: as long
    as the whole head remains in frame, the position is identical every frame
    (tripod rule, like the reference Short).
    """
    if not pts:
        return [(max(0, round((w - ww) / 2)),
                 max(0, round((h - wh) / 2)))]
    cx = float(np.median([p[1] for p in pts]))
    cy = float(np.median([p[2] for p in pts]))
    x = max(0, min(round(cx - ww / 2), w - ww))
    # A portrait crop from a landscape shot normally consumes the full source
    # height.  Where vertical room exists, place the face at ~38% from the top
    # instead of dead-centre to retain natural headroom and shoulders.
    y = max(0, min(round(cy - wh * 0.38), h - wh))
    if face_wh is not None:
        try:
            fw, fh = float(face_wh[0]), float(face_wh[1])
            if fw > 0 and fh > 0:
                margin_x = fw * 0.12
                margin_y = fh * 0.12
                # Face extents in source coords (head top .. chin bottom).
                fx0, fx1 = cx - fw / 2 - margin_x, cx + fw / 2 + margin_x
                fy0, fy1 = cy - fh / 2 - margin_y, cy + fh / 2 + margin_y
                # Shift the static window only if the face would clip.
                if fx0 < x:
                    x = max(0, min(round(fx0), w - ww))
                elif fx1 > x + ww:
                    x = max(0, min(round(fx1 - ww), w - ww))
                if fy0 < y:
                    y = max(0, min(round(fy0), h - wh))
                elif fy1 > y + wh:
                    y = max(0, min(round(fy1 - wh), h - wh))
        except (TypeError, ValueError, IndexError):
            pass
    end_t = float(duration if duration is not None else pts[-1][0])
    count = max(1, int(round(end_t * fps)) + 1)
    return [(x, y)] * count


def _write_commands(positions: list, fps: float, path: str) -> None:
    """Write a sendcmd command file setting crop x/y, deduped to change-points.

    The TARGET must be the exact crop filter instance name (`crop@cam`); a
    bare `cam` target does not match any filter, so the commands were dropped
    and the crop never moved. Only frames where the crop actually CHANGES get
    a command — steady frames emit nothing, so a static camera writes ~3
    commands instead of one per frame (less to parse, no redundant sets).
    """
    lines = []
    prev = None
    for f, (x, y) in enumerate(positions):
        if (x, y) != prev:
            t = f / fps
            lines.append(f"{t:.4f} [enter] crop@cam x {x}, "
                         f"[enter] crop@cam y {y}")
            prev = (x, y)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("; ".join(lines))
    return path


def _ffmpeg_filter_path(path: str) -> str:
    """Quote a filesystem path for use as an ffmpeg filter value.

    sendcmd filename is parsed as filtergraph syntax: a bare Windows path
    (``C:\\dir\\file``) breaks parsing on the drive colon and backslashes,
    and spaces split the value. Prefer a cwd-relative forward-slash path;
    otherwise use the absolute path with forward slashes, an escaped drive
    colon and single quotes (the documented ``'C\\:/...'`` form) so any
    drive, any work dir and spaces in names all survive.
    """
    abs_path = os.path.abspath(path).replace(os.sep, "/")
    try:
        rel = os.path.relpath(abs_path, os.getcwd()).replace(os.sep, "/")
        if not rel.startswith(".."):
            abs_path = rel
    except ValueError:
        pass  # different drive — keep the absolute form below
    return "'" + abs_path.replace(":", "\\:") + "'"


def clip_scene_plan(path: str, log=None, genre: str = "",
                    layout_prior: str = "",
                    requested_layout: str = "") -> list:
    """Per-scene reframe strategies for an ALREADY-EXTRACTED clip (times in
    clip coords). Cheap: scdet on the short clip + 3-frame strategy sampling
    per scene. Used by re-renders (retrim/cut) that have no stored plan."""
    # A brief reaction shot still changes speaker coordinates. Do not apply
    # the generic three-second cut suppression to subject-specific layouts.
    if requested_layout in {"split", "panel", "alternate"}:
        scenes = detect_scenes(path, log=log, min_duration=0.1)
    else:
        scenes = detect_scenes(path, log=log)
    return scene_strategies(path, scenes, log=log, genre=genre,
                            layout_prior=layout_prior,
                            requested_layout=requested_layout)


def _merge_static_layout_segments(segs: list) -> list:
    """Merge adjacent identical non-FACE layouts into one render pass.

    TRACK segments deliberately remain separate: each camera cut needs a fresh
    median face lock, which is how the OpenShorts os4-style speaker changes are
    preserved. SPLIT/PANEL also need fresh subject positions after each cut;
    identical layout names do not imply identical source crop windows.
    """
    merged = []
    for seg in segs:
        if (merged and seg[2] not in {"track", "split", "panel", "alternate"}
                and merged[-1][2:] == seg[2:]
                and abs(merged[-1][1] - seg[0]) <= 0.04):
            prev = merged[-1]
            merged[-1] = (prev[0], seg[1], *prev[2:])
        else:
            merged.append(seg)
    return merged


def compatible_edited_plan(raw, plan, log=None):
    """Keep edited timing/layout preferences; validate subjects without an LLM."""
    import math
    duration = get_duration(raw)
    normalized = []
    for scene in plan:
        start, end = float(scene["start"]), float(scene["end"])
        if not math.isfinite(start) or not math.isfinite(end) or end <= start:
            raise ValueError("Invalid edited scene bounds")
        start, end = max(0.0, start), min(duration, end)
        mode = scene.get("strategy") or scene.get("layout") or "track"
        if mode not in {"face", "track", "general", "center", "split", "panel",
                        "alternate", "gameplay", "screencast", "auto"}:
            raise ValueError(f"Invalid edited layout: {mode}")
        if end - start >= 0.1:
            normalized.append((start, end, mode))
    normalized.sort()
    if not normalized or any(a[1] > b[0] for a, b in zip(normalized, normalized[1:])):
        raise ValueError("Edited plan is empty or overlaps")
    result = []
    for start, end, mode in normalized:
        result.extend(scene_strategies(raw, [(start, end)], log=log,
                                       requested_layout=mode))
    return result


def reframe_scenes(raw: str, out_path: str, aspect: str = "9:16",
                   plan: list | None = None, clip_start: float = 0.0,
                   clip_end: float | None = None, log=None,
                   work_dir: str | None = None,
                   punch_zoom: float = 0.0,
                   subject_lock: tuple | None = None,
                   explicit_face: bool = False,
                   gameplay_ratio: float = 0.60) -> str:
    """Reframe `raw` scene-by-scene and concat.

    A clip that crosses a scene-type change (talking head -> whiteboard/slide)
    is split at the scene boundaries and EACH part is reframed with its own
    strategy, so the board's text is never cropped by the talking-head window
    (and the talking head is never shrunk to fit a slide). Camera-cut scene
    boundaries are preserved even when adjacent scenes share TRACK: a new
    shot must start with a fresh face lock instead of panning from the face in
    the previous shot.

    `plan` holds strategies in the SOURCE coordinate space (as produced by
    `scene_strategies`); `[clip_start, clip_end]` selects the window that
    belongs to this clip. When every segment shares one strategy (or the plan
    is empty) this degrades to a single `reframe_clip` — no extra cost.
    """
    if clip_end is None:
        clip_end = get_duration(raw)
    segs = []
    for s in (plan or []):
        a = max(s["start"], clip_start)
        b = min(s["end"], clip_end)
        if b - a >= 0.1:
            segs.append((a - clip_start, b - clip_start, s["strategy"],
                         s.get("crop_ratio", 1.0), s.get("track_mode", "aggressive")))
    # Scene detectors can omit a short tail (the 60s proof ended at 58.68s),
    # which made AUTO physically shorter than every explicit layout. Preserve
    # the entire requested timeline by filling uncovered gaps with the nearest
    # known strategy; the portrait guardian still validates the final frame.
    if segs:
        covered = []
        cursor = 0.0
        total = max(0.0, clip_end - clip_start)
        for seg in sorted(segs, key=lambda item: item[0]):
            if seg[0] > cursor + 0.04:
                template = covered[-1] if covered else seg
                covered.append((cursor, seg[0], template[2],
                                template[3], template[4]))
            covered.append(seg)
            cursor = max(cursor, seg[1])
        if cursor < total - 0.04:
            template = covered[-1]
            covered.append((cursor, total, template[2],
                            template[3], template[4]))
        segs = covered
        segs = _merge_static_layout_segments(segs)
    if not segs:
        segs = [(0.0, clip_end - clip_start, "track", 1.0, "aggressive")]
    if len(segs) == 1:
        return reframe_clip(raw, out_path, aspect, segs[0][2], log, work_dir,
                            crop_ratio=segs[0][3], track_mode=segs[0][4],
                            punch_zoom=punch_zoom,
                            subject_lock=subject_lock,
                            # A solo tracked speaker gets the sharp full-frame
                            # portrait treatment; blur-fill is reserved for
                            # general/fit layouts and no-face fallbacks.
                            explicit_face=(explicit_face or segs[0][2] == "track"),
                            gameplay_ratio=gameplay_ratio)

    from video import concat_clips, extract_clip
    work_dir = work_dir or os.path.dirname(out_path)
    os.makedirs(work_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(out_path))[0]
    parts = []
    part_durations = []
    # Explicit FACE chain: the last face window travels across segments so a
    # brief no-face gap holds the crop (anchor_xy) instead of flickering to a
    # fallback layout. AUTO never threads an anchor — its fallbacks stay.
    anchor = None
    sink: list = []
    for i, (s, e, st, cr, tm) in enumerate(segs):
        seg_raw = os.path.join(work_dir, f"sc_{base}_{i}_raw.mp4")
        seg_out = os.path.join(work_dir, f"sc_{base}_{i}.mp4")
        try:
            try:
                _sig_fast = True
                try:
                    import inspect as _insp
                    _sig_fast = "fast" in _insp.signature(extract_clip).parameters
                except Exception:
                    _sig_fast = True
                if _sig_fast:
                    extract_clip(raw, s, e, seg_raw, log, fast=True)
                else:
                    extract_clip(raw, s, e, seg_raw, log)
            except TypeError:
                # Legacy/test doubles of extract_clip without fast kw.
                extract_clip(raw, s, e, seg_raw, log)
            reframe_clip(seg_raw, seg_out, aspect, st, log, work_dir,
                         crop_ratio=cr, track_mode=tm,
                         punch_zoom=punch_zoom,
                         subject_lock=subject_lock,
                         explicit_face=(explicit_face or st == "track"),
                         gameplay_ratio=gameplay_ratio,
                         anchor_xy=(anchor if (explicit_face and st == "track")
                                    else None),
                         anchor_sink=(sink if (explicit_face and st == "track")
                                      else None))
            if explicit_face and st == "track" and sink:
                anchor = sink[-1]
            parts.append(seg_out)
            part_durations.append(e - s)
        except Exception as ex:
            # Never silently discard seconds (and desynchronise captions).
            raise RuntimeError(f"Scene {i} ({s:.2f}-{e:.2f}s, {st}) failed") from ex
    if not parts:
        return reframe_clip(raw, out_path, aspect, "track", log, work_dir)
    if len(parts) == 1:
        os.replace(parts[0], out_path)
    else:
        concat_clips(parts, out_path, log, durations=part_durations)
    # Clean up intermediate segment files to avoid disk accumulation.
    for i in range(len(segs)):
        for tag in ("_raw", ""):
            try:
                os.remove(os.path.join(work_dir, f"sc_{base}_{i}{tag}.mp4"))
            except OSError:
                pass
    return out_path


def reframe_face(raw: str, out_path: str, aspect: str = "9:16", log=None,
                 work_dir: str | None = None,
                 subject_lock: tuple | None = None,
                 punch_zoom: float = 0.0,
                 gameplay_ratio: float = 0.60) -> str:
    """Explicit talking-head render that stays correct across camera cuts.

    A single whole-clip TRACK lock aims at the median face position, so on a
    multi-shot clip (debate cut suit-man -> white-shirt-man -> wide studio)
    every shot inherits one static window: faces end up half-cropped or fully
    out of frame, and the camera shows whoever happens to be inside instead
    of the active speaker. This builds a per-scene FACE plan instead (each
    scene through the requested_layout compatibility matrix) and renders it
    with explicit_face so every shot gets a fresh centred lock, brief no-face
    gaps hold the last window via anchor threading, and verified two-shot
    scenes cut to the active speaker. Single-shot clips degrade to the plain
    face crop — no extra cost.
    """
    plan = []
    try:
        plan = clip_scene_plan(raw, log=log, requested_layout="face")
    except Exception as exc:  # noqa: BLE001 — fall through to single lock
        log and log(f"⚠️ FACE scene plan skipped: {exc}")
    if not plan or (len(plan) == 1 and plan[0]["strategy"] == "track"):
        from video import crop_video
        return crop_video(raw, out_path, mode="face", aspect=aspect, log=log,
                          subject_lock=subject_lock)
    if log:
        log("🎛️ FACE per-scene: " + ", ".join(
            f"{s['start']:.1f}-{s['end']:.1f}s={s['strategy']}"
            for s in plan))
    return reframe_scenes(raw, out_path, aspect=aspect, plan=plan,
                          clip_start=0.0, clip_end=get_duration(raw),
                          log=log, work_dir=work_dir, explicit_face=True,
                          subject_lock=subject_lock, punch_zoom=punch_zoom,
                          gameplay_ratio=gameplay_ratio)


def _attribute_windows_n(activity, loudness, min_margin=None):
    """Which speaker owns each window (N speakers), or None when unclear.

    Same contract as active_speaker.attribute_windows, generalised past two
    speakers: the winner needs a relative margin over the runner-up, and
    quiet windows stay unattributed. active_speaker itself is untouched —
    every existing two-speaker caller keeps its exact behaviour.
    """
    margin = _MIN_MARGIN if min_margin is None else min_margin
    verdicts = []
    for i, scores in enumerate(activity):
        if loudness and i < len(loudness) and loudness[i] < _AUDIO_FLOOR:
            verdicts.append(None)          # nobody is talking here
            continue
        try:
            ordered = sorted(range(len(scores)), key=lambda k: scores[k],
                             reverse=True)
        except (TypeError, IndexError):
            verdicts.append(None)
            continue
        if not ordered:
            verdicts.append(None)
            continue
        top = ordered[0]
        total = sum(max(0.0, float(s)) for s in scores)
        if total <= 0:
            verdicts.append(None)
            continue
        runner = ordered[1] if len(ordered) > 1 else None
        gap = ((float(scores[top]) - float(scores[runner])) / total
               if runner is not None else 1.0)
        verdicts.append(None if gap < margin else top)
    return verdicts


def _portrait_speaker_spotlight(src: str, out_path: str, log=None,
                                work_dir: str | None = None):
    """Explicit-FACE spotlight for already-portrait sources: hard cuts to
    whoever is talking (single-pass sendcmd, N speakers).

    A 9:16 source delivered to 9:16 has zero crop slack, so the generic
    TRACK path returns a flat upscale and the camera never moves — on a
    crowd/stage clip FACE then shows a static wide frame instead of the
    speaker. The spotlight instead frames each live seat as a close-up and
    cuts between them turn-by-turn (same hold/debounce contract as the
    two-speaker cut engine). Returns out_path, or None when there is
    nobody to cut to (caller keeps the flat upscale).
    """
    from video import _stable_body_seats, _speaker_activity_boxes
    w, h = get_resolution(src)
    if w <= 0 or h <= 0 or h < w:
        return None  # landscape handled by the generic TRACK machinery
    try:
        seats = _stable_body_seats(src, samples=10)
    except Exception:
        return None
    if len(seats) < 2:
        return None
    # Bound the activity measurement: the six largest live seats.
    seats = sorted(seats, key=lambda b: b[2] * b[3], reverse=True)[:6]
    try:
        boxes = _speaker_activity_boxes(
            src, [(s[0], s[1]) for s in seats], samples=10)
    except Exception:
        return None
    if len(boxes) < 2:
        return None
    # One close-up window per seat, anchored on its (real, matched) face.
    tw, th = TARGET_FRAMES.get("9:16", (1080, 1920))
    wh = max(4, int(h * 0.55))
    ww = max(4, int(wh * tw / th))
    if ww > w:
        ww = w
        wh = max(4, int(ww * th / tw))
    windows = []
    for cx, cy, _bw, _bh in boxes:
        x = max(0, min(int(cx - ww / 2), w - ww))
        y = max(0, min(int(cy - wh * 0.38), h - wh))
        windows.append((x, y))
    # Same subject twice (duplicate seats) must not ping-pong the camera.
    for i in range(len(windows)):
        for j in range(i + 1, len(windows)):
            if (abs(windows[i][0] - windows[j][0]) < ww * SPEAKER_SNAP_FRAC
                    and abs(windows[i][1] - windows[j][1]) < wh * SPEAKER_SNAP_FRAC):
                log and log("🎤 portrait spotlight: seats overlap — static frame")
                return None
    dur = get_duration(src)
    if dur <= 0:
        return None
    try:
        activity = decode_activity(src, 0, dur, boxes)
    except Exception:
        return None
    if not activity:
        return None
    normed = normalise_activity(activity)
    try:
        loudness = audio_envelope(src, 0, dur)
    except Exception:
        loudness = []
    verdicts = hold(_attribute_windows_n(normed, loudness))
    if not verdicts or all(v is None for v in verdicts):
        return None
    hold_n = max(2, int(round(SPEAKER_MIN_HOLD_SEC / WINDOW_SECONDS)))
    turns = _compress_turns(list(verdicts), hold_n)
    if not turns:
        return None
    fps = get_fps(src) or 30.0
    n_frames = int(round(dur * fps)) + 1
    positions = []
    cursor = 0.0
    for speaker, n_win in turns:
        f1 = min(n_frames, int(round(cursor + n_win * WINDOW_SECONDS * fps)))
        win = windows[min(int(speaker), len(windows) - 1)]
        f0 = int(round(cursor))
        if f1 > f0:
            positions.extend([win] * (f1 - f0))
        cursor += n_win * WINDOW_SECONDS * fps
    while len(positions) < n_frames:
        positions.append(positions[-1] if positions else windows[0])
    positions = positions[:n_frames]
    if len(set(positions)) < 2:
        # One speaker owns the whole clip: a static close-up still spotlights
        # them better than a flat full-frame upscale.
        x, y = positions[0]
        log and log("🎤 portrait spotlight: single speaker close-up")
        from video import _crop_window
        _crop_window(src, out_path, x, y, ww, wh, tw, th,
                     blurred=False, log=log)
        return out_path
    work_dir = work_dir or os.path.dirname(out_path) or "."
    os.makedirs(work_dir, exist_ok=True)
    cmds_raw = os.path.join(work_dir, "spotlight_cmds.txt")
    _write_commands(positions, fps, cmds_raw)
    cmds = _ffmpeg_filter_path(cmds_raw)
    init_x, init_y = positions[0]
    vf = (
        f"[0:v]sendcmd=filename={cmds},"
        f"crop@cam={ww}:{wh}:{init_x}:{init_y},"
        f"scale={tw}:{th}:flags=lanczos,setsar=1,unsharp=5:5:0.35:5:5:0.0"
    )
    log and log(f"🎤 portrait spotlight: {len(seats)} seats, "
                f"{len(turns)} turns")
    run([
        "ffmpeg", "-y", "-i", src,
        "-filter_complex", vf,
        *encode_args(log=log),
        "-c:a", "aac", "-b:a", "128k",
        out_path,
    ], log)
    try:
        os.remove(cmds_raw)
    except OSError:
        pass
    return out_path


def _speaker_cut_clip(src: str, out_path: str, aspect: str = "9:16",
                      log=None, work_dir: str | None = None,
                      fallback_face: bool = False) -> str:
    """Hard cuts to active speaker — single-pass sendcmd (OpenShorts parity).

    Instead of pre-rendering 2 close-ups + concat (2 ffmpeg passes + disk I/O),
    this builds a crop-x trajectory that jumps between the two speaker positions
    and renders through the TRACK sendcmd path in ONE ffmpeg pass.
    """
    from video import (crop_video, _speaker_positions, _speaker_activity_boxes,
                       _stable_face_center, _crop_window)
    w, h = get_resolution(src)
    tw, th = TARGET_FRAMES.get(aspect, TARGET_FRAMES["9:16"])

    def fallback():
        if not fallback_face:
            return crop_video(src, out_path, mode="general", aspect=aspect, log=log)
        center = _stable_face_center(src, log)
        cx, cy = center if center else (w / 2, h / 2)
        ww, wh = _aspect_window(w, h, tw, th)
        x = max(0, min(int(cx - ww / 2), w - ww))
        y = max(0, min(int(cy - wh / 2), h - wh))
        _crop_window(src, out_path, x, y, ww, wh, tw, th,
                     blurred=False, log=log)
        return out_path

    found = _speaker_positions(src, samples=12)
    if len(found) < 2:
        log and log("⚠️ <2 speakers for SPEAKER_CUT — falling back to general")
        return fallback()

    spk1, spk2 = found[0], found[1]
    ww, wh = _aspect_window(w, h, tw, th)
    work_dir = work_dir or os.path.dirname(out_path)
    os.makedirs(work_dir, exist_ok=True)

    # Tripod + whole-face rule (reference-Short behaviour): the camera holds
    # ONE static window per speaker turn and never glides. A cut only happens
    # when the challenger holds the floor for SPEAKER_MIN_HOLD_SEC.
    # Shoulder-to-shoulder pairs (windows nearer than SPEAKER_SNAP_FRAC of the
    # crop width) keep a single midpoint lock — snapping there only renders
    # detector wobble as camera motion.
    # Speaker crop-x positions (centered on each face)
    x1 = max(0, min(int(spk1[0] - ww / 2), w - ww))
    x2 = max(0, min(int(spk2[0] - ww / 2), w - ww))
    if abs(x1 - x2) < ww * SPEAKER_SNAP_FRAC:
        mid_x = max(0, min(int((spk1[0] + spk2[0]) / 2 - ww / 2), w - ww))
        y_mid = max(0, min(int((spk1[1] + spk2[1]) / 2 - wh * 0.38), h - wh))
        _crop_window(src, out_path, mid_x, y_mid, ww, wh, tw, th,
                     blurred=False, log=log)
        log and log("🎤 SPEAKER_CUT: pair too close — static midpoint lock")
        return out_path
    # Use the average y (both speakers at roughly same height in a 2-shot).
    # Face at ~38% from the top keeps the whole head + shoulders in frame;
    # dead-centre (wh/2) clipped foreheads on close two-shots.
    y_center = max(0, min(int((spk1[1] + spk2[1]) / 2 - wh * 0.38), h - wh))

    dur = get_duration(src)
    if dur <= 0:
        return fallback()

    boxes = _speaker_activity_boxes(src, found)
    if len(boxes) < 2:
        return fallback()
    activity = decode_activity(src, 0, dur, boxes)
    if not activity:
        return fallback()

    normed = normalise_activity(activity)
    loudness = audio_envelope(src, 0, dur)
    verdicts = attribute_windows(normed, loudness)
    verdicts = hold(verdicts, min_windows=3)
    if not verdicts:
        return fallback()

    # Turn-based cuts (same contract as _held_speaker_track): compress the
    # held verdicts into committed turns with a SPEAKER_MIN_HOLD_SEC hold.
    # Brief interjections / detector flicker between two faces never reach
    # the camera; the flip is backdated so debouncing adds zero cut latency.
    hold_n = max(2, int(round(SPEAKER_MIN_HOLD_SEC / WINDOW_SECONDS)))
    turns = _compress_turns(list(verdicts), hold_n)
    if not turns:
        return fallback()

    # Build per-frame crop positions from committed turns (piecewise-constant)
    fps = get_fps(src) or 30.0
    n_frames = int(round(dur * fps)) + 1
    positions = []
    cursor = 0.0
    for speaker, n_win in turns:
        f1 = min(n_frames, int(round(cursor + n_win * WINDOW_SECONDS * fps)))
        cx = x1 if int(speaker) == 0 else x2
        f0 = int(round(cursor))
        if f1 > f0:
            positions.extend([(cx, y_center)] * (f1 - f0))
        cursor += n_win * WINDOW_SECONDS * fps
    while len(positions) < n_frames:
        positions.append(positions[-1] if positions else (x1, y_center))
    positions = positions[:n_frames]

    # Write sendcmd file (deduped to change-points). Keep the raw path
    # for cleanup: cmds becomes the quoted filter value below.
    cmds_raw = os.path.join(work_dir, "speaker_cut_cmds.txt")
    _write_commands(positions, fps, cmds_raw)
    cmds = _ffmpeg_filter_path(cmds_raw)

    # Initial crop position must match the first trajectory frame to
    # avoid a 1-frame flash at the start.
    init_x = positions[0][0] if positions else x1
    vf = (
        f"[0:v]sendcmd=filename={cmds},"
        f"crop@cam={ww}:{wh}:{init_x}:{y_center},"
        f"scale={tw}:{th}:flags=lanczos,setsar=1,unsharp=5:5:0.35:5:5:0.0"
    )
    log and log(f"🎤 SPEAKER_CUT: single-pass ({len(verdicts)} windows)")
    run([
        "ffmpeg", "-y", "-i", src,
        "-filter_complex", vf,
        *encode_args(log=log),
        "-c:a", "aac", "-b:a", "128k",
        out_path,
    ], log)
    try:
        os.remove(cmds_raw)
    except OSError:
        pass

    return out_path


def reframe_clip(src: str, out_path: str, aspect: str = "9:16",
                 strategy: str = "track", log=None,
                 work_dir: str | None = None,
                 crop_ratio: float = 1.0,
                 track_mode: str = "aggressive",
                 punch_zoom: float = 0.0,
                 explicit_face: bool = False,
                 subject_lock: tuple | None = None,
                 gameplay_ratio: float = 0.60,
                 anchor_xy: tuple | None = None,
                 anchor_sink: list | None = None) -> str:
    """Reframe `src` to `aspect` with the chosen strategy. Returns out_path.

    crop_ratio: height multiplier for the crop window (1.0=default,
        0.75=tight/podcast, 0.85=normal/vlog, 1.0=wide/sports).
    track_mode: aggressive (default) or relaxed (wider dead zone, slower pan).
    punch_zoom: >0 adds audio-beat punch-in composited with track (single pass).
    subject_lock: normalized (x, y) in 0..1 — manual subject lock. The face
        nearest this point is followed for the whole clip, overriding the
        largest-face / active-speaker heuristic (user clicked the subject).
    anchor_xy: (x, y) source-coord crop origin to HOLD when TRACK finds no
        face in this segment. Threaded by reframe_scenes for explicit FACE so
        a brief no-face gap keeps the last face window (and keeps searching
        for the active speaker) instead of flickering to CENTER. None keeps
        the legacy CENTER fallback (AUTO behaviour).
    anchor_sink: optional list receiving the final track window (x, y) of
        this render, so reframe_scenes can thread it as the next segment's
        anchor_xy. Ignored for non-track strategies.
    """
    w, h = get_resolution(src)
    tw, th = TARGET_FRAMES.get(aspect, TARGET_FRAMES["9:16"])
    ww, wh = _aspect_window(w, h, tw, th)
    # TRACK already has to discard the sides of a landscape frame to produce
    # true 9:16.  Tight genre presets (podcast used 0.75) cropped another 25%
    # from both dimensions, turning a medium shot into a soft extreme closeup
    # (for example 111x199 -> 1080x1920).  Keep the full source height for
    # tracked portrait shots so head, shoulders and torso remain visible.
    if strategy == "track" and aspect == "9:16":
        crop_ratio = max(1.0, crop_ratio)
    # Genre-aware crop_ratio: adjust window height while preserving aspect.
    if crop_ratio != 1.0:
        wh = max(4, int(wh * crop_ratio))
        ww = max(4, int(wh * tw / th))
        # Clamp to source bounds so the crop window never exceeds the frame.
        if ww > w:
            ww = w
            wh = max(4, int(ww * th / tw))
        if wh > h:
            wh = h
            ww = max(4, int(wh * tw / th))

    if ww >= w - 1 and wh >= h - 1:
        # Source already matches the target aspect — scale up to the delivery
        # floor (1080 wide) with lanczos + sharpening. BUT still run the TRACK
        # strategy: a vertical 9:16 source (YouTube Shorts / TikTok) usually
        # has a talking head that a static scale leaves untouched — no smooth
        # camera follow, no anti-jitter crop. So 'track' on an already-9:16
        # source uses the dynamic face-follow window instead of a flat upscale.
        # Keep an already-portrait source locked. Re-running face-follow on a
        # tripod/podcast clip makes detector jitter move an otherwise safe
        # frame. Source edits/cuts already provide the camera movement.
        #
        # Explicit FACE is the exception: the user asked to spotlight the
        # speaker, and a flat upscale never moves the camera — on a crowd or
        # stage clip that reads as "face saja". The portrait spotlight cuts
        # to whoever holds the floor; anything undecidable keeps this flat
        # upscale below (AUTO behaviour untouched).
        if explicit_face:
            try:
                spot = _portrait_speaker_spotlight(
                    src, out_path, log=log, work_dir=work_dir)
                if spot:
                    return spot
            except Exception as exc:  # noqa: BLE001 — flat upscale below
                log and log(f"⚠️ portrait spotlight skipped ({exc})")
        sw = max(DELIVERY_MIN_WIDTH, tw)
        run([
            "ffmpeg", "-y", "-i", src,
            "-vf", f"scale={sw}:-2:flags=lanczos,unsharp=5:5:0.6:5:5:0.0",
            *encode_args(log=log),
            "-c:a", "aac", "-b:a", "128k",
            out_path,
        ], log)
        return out_path

    if strategy != "track":
        # GENERAL / CENTER / SPLIT / PANEL — delegate to video.crop_video so
        # debates show both people (split = stacked, panel = side-by-side) and
        # no-face/crowd shots fill the frame (center) instead of blurring.
        from video import crop_video
        # OpenShorts: SPEAKER_CUT mode — hard cuts to active speaker instead of
        # panel/split/general. Applies to 2-person scenes (debate) so the
        # frame lands full-screen on whoever is actually talking, not two
        # tiny people in a stacked/split layout or a blurred fit.
        # SPLIT is already a scene-verified composition. Do not replace the
        # requested top/bottom panels with the global active-speaker default.
        if SPEAKER_CUT_MODE and strategy in ("panel", "general"):
            if _two_face_count(src) >= 2:
                log and log("🎤 SPEAKER_CUT mode: hard cuts to active speaker")
                return _speaker_cut_clip(src, out_path, aspect, log, work_dir)
        mode = strategy if strategy in (
            "face", "split", "panel", "alternate", "center", "gameplay"
        ) else "general"
        return crop_video(src, out_path, mode=mode, aspect=aspect, log=log,
                          gameplay_ratio=gameplay_ratio,
                          subject_lock=subject_lock)

    # TRACK on a 9:16 source that must be delivered in another aspect (16:9 /
    # 1:1) can't use the vertical face-follow window — it would crop to 9:16.
    # Delegate to the general fit (blur bg + sharp cutout) instead.
    if aspect != "9:16":
        from video import crop_video
        return crop_video(src, out_path, mode="general", aspect=aspect, log=log)

    # V2 pipeline (Opus Clip parity): env-gated, safe fallback to V1.
    if os.environ.get("KLIPIN_REFRAME_V2", "0") == "1":
        try:
            from reframe_v2 import reframe_clip_v2
            log and log("🚀 Using Reframe V2 (Opus Clip parity pipeline)")
            return reframe_clip_v2(src, out_path, aspect, log)
        except Exception as e:
            log and log(f"⚠️ V2 failed ({e}) — falling back to V1 TRACK")

    # ── TRACK: dynamic crop following the smoothed face center (V1) ──────
    work_dir = work_dir or os.path.dirname(out_path)
    os.makedirs(work_dir, exist_ok=True)
    # Use the CLIP's real frame rate for the sendcmd grid: commands written
    # at a hardcoded 30Hz on a 25/29.97/60fps clip land between frames, so the
    # the crop repeats then jumps — another source of visible stutter.
    fps = get_fps(src) or 30.0
    # A strict 9:16 crop of a landscape source is often only ~23% of its
    # width.  Use a 4:5 medium-shot foreground inside the 9:16 canvas instead:
    # it retains shoulders/torso, needs less upscaling, and the blurred canvas
    # still guarantees a true portrait delivery. Punch zoom keeps the strict
    # window because that mode explicitly asks for a close-up effect.
    medium_fit = punch_zoom <= 0 and not explicit_face
    if medium_fit:
        ww = min(w, max(ww, int(round(wh * 4 / 5))))

    # Tripod rule: a single speaker gets ONE static median lock per scene
    # (diem, like the reference Short). Continuous follow-cam (12fps face path
    # + held speaker cuts) only runs when two speakers genuinely coexist —
    # otherwise it just chases detector wobble and reads as getar. Scene cuts
    # are already rendered separately by reframe_scenes, so per-scene median
    # locks preserve every real camera/speaker change without micro-motion.
    use_continuous = bool(explicit_face) and subject_lock is None
    if use_continuous and not _has_verified_two_shot(src):
        log and log("🎥 TRACK tripod-lock: single speaker → static median crop")
        use_continuous = False
    face_wh = None
    if use_continuous:
        # Explicit FACE on a verified two-shot is an active-speaker contract:
        # sample continuously so camera cuts, profile faces and speaker turns
        # can all change the selected subject.
        pts = _face_path(src, log)
        duration = get_duration(src)
        if pts and duration > pts[-1][0] + (1.0 / max(TRACK_FPS, 1.0)):
            pts.append((duration, pts[-1][1], pts[-1][2]))
    else:
        from video import _face_centers
        # AUTO scenes are already isolated at camera cuts; a median lock keeps
        # a stationary talking head calm and cheap. With a manual subject_lock
        # the face nearest the user's click wins every sample instead.
        if subject_lock is not None:
            lock_samples = max(8, min(24, int(os.environ.get(
                "TRACK_LOCK_SAMPLES", "8"))) * 2)
            log and log(f"🔒 Subject lock: following face nearest "
                        f"({subject_lock[0]:.2f}, {subject_lock[1]:.2f})")
        else:
            lock_samples = max(4, min(18, int(os.environ.get(
                "TRACK_LOCK_SAMPLES", "8"))))
        # Keep the legacy call shape when no lock is active. Besides avoiding
        # needless coupling, this preserves compatibility with alternate face
        # providers and test doubles that only accept ``samples``.
        detected = (_face_centers(src, samples=lock_samples,
                                   subject_lock=subject_lock)
                     if subject_lock is not None else
                     _face_centers(src, samples=lock_samples))
        pts = [(float(i), float(face[0]), float(face[1]))
               for i, face in enumerate(detected)]
        # Median face size across samples: used by _locked_track to guarantee
        # the WHOLE head (plus margin) stays inside the static window instead
        # of just centring a point that can clip forehead/chin on close-ups.
        face_wh = None
        try:
            _ws = [float(f[2]) for f in detected if len(f) > 3]
            _hs = [float(f[3]) for f in detected if len(f) > 3]
            if _ws and _hs:
                import statistics as _st
                face_wh = (_st.median(_ws), _st.median(_hs))
        except Exception:
            face_wh = None
    if not pts:
        if anchor_xy is not None:
            # Explicit FACE hold: no face in this gap segment, so freeze the
            # last face window. Detection keeps running on later segments and
            # normal tracking resumes the moment a face returns.
            try:
                ax = max(0, min(int(round(float(anchor_xy[0]))), w - ww))
                ay = max(0, min(int(round(float(anchor_xy[1]))), h - wh))
            except (TypeError, ValueError, IndexError):
                ax, ay = max(0, (w - ww) // 2), max(0, (h - wh) // 2)
            fps = get_fps(src) or 30.0
            count = max(1, int(round(get_duration(src) * fps)) + 1)
            positions = [(ax, ay)] * count
            log and log(f"🎥 FACE hold: no face here — keeping crop ({ax},{ay})")
            if anchor_sink is not None:
                anchor_sink.append((ax, ay))
            from video import _crop_window as _hold_crop_window
            _hold_crop_window(src, out_path, ax, ay, ww, wh, tw, th,
                              blurred=False, log=log)
            return out_path
        log and log("🎥 No faces detected — using CENTER reframe (full-frame crop)")
        return reframe_clip(src, out_path, aspect, "center", log, work_dir)
    positions = (_locked_track(pts, w, h, ww, wh, fps=fps,
                               duration=get_duration(src), face_wh=face_wh)
                 if subject_lock is not None else
                 (_held_speaker_track(pts, w, h, ww, wh, fps=fps,
                                      duration=get_duration(src))
                  if explicit_face else
                  _locked_track(pts, w, h, ww, wh, fps=fps,
                                duration=get_duration(src), face_wh=face_wh)))
    if anchor_sink is not None and positions:
        anchor_sink.append(positions[-1])
    # Initial crop position must match the first trajectory frame to avoid a
    # 1-frame flash at the start of the clip.
    init_x = positions[0][0] if positions else max(0, (w - ww) // 2)
    y_center = positions[0][1] if positions else max(0, (h - wh) // 2)
    cmds = os.path.join(work_dir, "track_cmds.txt")
    _write_commands(positions, fps, cmds)
    # sendcmd filename is parsed as a FILTER value: quote it via
    # _ffmpeg_filter_path so drive-letter colons, backslashes and spaces
    # (Windows) never break the graph, whatever the work dir is.
    cmds = _ffmpeg_filter_path(cmds)

    # FACE/TRACK is a portrait spotlight contract. The old camera-inset/PiP
    # path was removed entirely because it duplicated the source as a tiny
    # blurred person in the corner and was not part of the requested layout.
    # sendcmd MUST precede crop@cam: sendcmd forwards per-frame x/y commands to
    # the downstream crop filter. Placing it after crop left the crop window
    # frozen at the initial (center) position — the reported "static crop".
    # When punch_zoom > 0, compose the zoom into the same filter graph
    # (single ffmpeg pass) instead of a separate apply_punch_zoom pass.
    if punch_zoom > 0:
        import punch_in as _pi
        dur = get_duration(src)
        beats = _pi.emphasis_times(src, dur if dur > 0 else 5.0)
        zoom_expr = _pi.punch_expression(beats, max_zoom=punch_zoom, fps=fps)
        if zoom_expr:
            zoom_expr = zoom_expr.replace(",", r"\,")
            vf = (
                f"[0:v]sendcmd=filename={cmds},"
                f"crop@cam={ww}:{wh}:{init_x}:{y_center},"
                f"scale={tw}:{th}:flags=lanczos,setsar=1,unsharp=5:5:0.35:5:5:0.0,"
                f"zoompan=z='{zoom_expr}':d=1:"
                f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                f"s={tw}x{th}:fps={fps},setsar=1"
            )
            log and log(f"🎥 TRACK + punch-in ({len(beats)} beats)")
        else:
            vf = (
                f"[0:v]sendcmd=filename={cmds},"
                f"crop@cam={ww}:{wh}:{init_x}:{y_center},"
                f"scale={tw}:{th}:flags=lanczos,setsar=1,unsharp=5:5:0.35:5:5:0.0"
            )
    else:
        if medium_fit:
            vf = (
                f"[0:v]scale={tw}:{th},setsar=1,boxblur=20:5[bg];"
                f"[0:v]sendcmd=filename={cmds},"
                f"crop@cam={ww}:{wh}:{init_x}:{y_center},"
                f"scale={tw}:-2:flags=lanczos,setsar=1[fg];"
                f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,"
                f"unsharp=5:5:0.25:5:5:0.0"
            )
        else:
            vf = (
                f"[0:v]sendcmd=filename={cmds},"
                f"crop@cam={ww}:{wh}:{init_x}:{y_center},"
                f"scale={tw}:{th}:flags=lanczos,setsar=1,"
                f"unsharp=5:5:0.35:5:5:0.0"
            )
    log and log("🎥 TRACK face-lock medium 4:5" if medium_fit else
                "🎥 TRACK face-lock")
    run([
        "ffmpeg", "-y", "-i", src,
        "-filter_complex", vf,
        *encode_args(log=log),
        "-c:a", "aac", "-b:a", "128k",
        out_path,
    ], log)
    try:
        os.remove(cmds)
    except OSError:
        pass
    return out_path


def _track_9x16(src: str, out_path: str, aspect: str = "9:16", log=None,
                work_dir: str | None = None,
                track_mode: str = "aggressive") -> str:
    """Face-follow TRACK for an already-9:16 source (Shorts/TikTok vertical).

    A flat `scale` on a vertical source leaves the talking head untouched —
    no camera movement, no anti-jitter. Here the crop window takes the
    full width and ~92% of the height, then the smoothed face track (same
    `_smooth_track` / sendcmd path as the landscape TRACK) keeps the face
    centered. The 78% window over-zoomed a 360p source (3× upscale) and the
    delivered clip read soft/blurry and cropped — 92% of the height lands at
    ~1.08×. The width is derived from the height at the target aspect (9:16)
    so the crop window never distorts: a 360px-wide source crops 330×588
    (0.561 ≈ 9:16 exactly) and keeps 30px of horizontal room for face pan.
    """
    w, h = get_resolution(src)
    tw, th = TARGET_FRAMES.get(aspect, TARGET_FRAMES["9:16"])
    # Window aspect MUST match the delivery frame: derive width from height at
    # the target ratio. Using the full source width (360×588 = 0.612) and then
    # scaling to 1080×1920 (0.5625) stretched the frame ~8.8% horizontally —
    # the reported "ngelebar" widening on vertical sources.
    wh = int(h * 0.92)
    ww = max(2, int(wh * tw / th))
    if ww > w:
        ww = w
        wh = max(2, int(ww * th / tw))
    if wh < 2 or ww < 2:
        # Degenerate tiny source — plain upscale instead.
        sw = max(DELIVERY_MIN_WIDTH, tw)
        run([
            "ffmpeg", "-y", "-i", src,
            "-vf", f"scale={sw}:-2:flags=lanczos,unsharp=5:5:0.6:5:5:0.0",
            *encode_args(log=log),
            "-c:a", "aac", "-b:a", "128k",
            out_path,
        ], log)
        return out_path
    fps = get_fps(src) or 30.0
    pts = _face_path(src, log)
    if not pts:
        log and log("🎥 No faces detected — CENTER upscale for vertical source")
        sw = max(DELIVERY_MIN_WIDTH, tw)
        run([
            "ffmpeg", "-y", "-i", src,
            "-vf", f"scale={sw}:-2:flags=lanczos,unsharp=5:5:0.6:5:5:0.0",
            *encode_args(log=log),
            "-c:a", "aac", "-b:a", "128k",
            out_path,
        ], log)
        return out_path
    positions = _smooth_track(pts, w, h, ww, wh, fps=fps, track_mode=track_mode)
    cmds = os.path.join(work_dir or os.path.dirname(out_path), "track_9x16_cmds.txt")
    _write_commands(positions, fps, cmds)
    try:
        rel = os.path.relpath(cmds, os.getcwd())
        if not rel.startswith(".."):
            cmds = rel.replace(os.sep, "/")
    except ValueError:
        pass
    init_x, init_y = positions[0]
    vf = (
        f"[0:v]sendcmd=filename={cmds},"
        f"crop@cam={ww}:{wh}:{init_x}:{init_y},"
        f"scale={tw}:{th}:flags=lanczos,setsar=1,"
        f"hqdn3d=1.5:1.5:4:4,eq=contrast=1.06:saturation=1.12,"
        f"unsharp=5:5:0.35:5:5:0.0"
    )
    log and log(f"🎥 TRACK reframe (vertical face-follow) — "
                f"hard-lock 9:16 @ {fps:g}fps")
    run([
        "ffmpeg", "-y", "-i", src,
        "-filter_complex", vf,
        *encode_args(log=log),
        "-c:a", "aac", "-b:a", "128k",
        out_path,
    ], log)
    try:
        os.remove(cmds)
    except OSError:
        pass
    return out_path
