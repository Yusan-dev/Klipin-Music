"""Klipin adapter module so the QuClippers-derived reframe_v2_qc engine
(reframe_v2_qc.py) can run inside Klipin.

reframe_v2_qc.py was ported from OpenShorts/QuClippers, where the entry point
module is `main.py`. Klipin keeps the same logic spread across smaller modules
(reframe.py, detector.py, video.py, ...). This file re-exposes the exact
symbols reframe_v2_qc.py imports from `main` so the engine runs unchanged:

  - DETECT_STRIDE, YOLO_FALLBACK_STRIDE
  - detect_face_candidates, detect_person_yolo
  - SmoothedCameraman, SpeakerTracker
  - detect_scenes, get_video_resolution
  - analyze_scenes_strategy

The detector/scene helpers re-use Klipin's own (more robust) implementations.
SmoothedCameraman / SpeakerTracker are copied verbatim from OpenShorts
(they are self-contained, no heavy imports) so the camera behaviour matches
the reference clips exactly.
"""
import os
import threading

import cv2
import numpy as np

import reframe
import detector
import video


# --- strides (env-overridable, parity with OpenShorts) --------------------
DETECT_STRIDE = max(int(os.environ.get("DETECT_STRIDE", "4")), 1)
YOLO_FALLBACK_STRIDE = DETECT_STRIDE * 2


# --- detectors --------------------------------------------------------------
# reframe_v2_qc expects candidates as {'box': [x, y, w, h], 'score': w*h}
def detect_face_candidates(frame):
    """All detected faces as [{'box':[x,y,w,h], 'score': w*h}] (orig coords)."""
    if frame is None or frame.size == 0:
        return []
    boxes = detector.face_boxes(frame)
    return [{"box": [int(x), int(y), int(w), int(h)],
             "score": int(w) * int(h)} for (x, y, w, h) in boxes]


def detect_person_yolo(frame):
    """Largest person's upper-body box [x, y, w, h] (orig coords), or None."""
    if frame is None or frame.size == 0:
        return None
    persons = detector._yolo_persons(frame)
    if not persons:
        return None
    # largest by area; focus on top 40% (head/chest) for framing
    best = max(persons, key=lambda b: b[2] * b[3])
    cx, cy, w, h = best
    x, y = int(cx - w / 2), int(cy - h / 2)
    w, h = int(w), int(h)
    face_h = int(h * 0.4)
    return [x, y, w, face_h]


# --- scene analysis ---------------------------------------------------------
def detect_scenes(video_path):
    scenes = reframe.detect_scenes(video_path)
    # Fetch fps properly (must return (scenes, fps) tuple for reframe_v2_qc render())
    import cv2
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    cap.release()
    return scenes, fps


def get_video_resolution(video_path):
    return video.get_resolution(video_path)


def analyze_scenes_strategy(video_path, scenes):
    """Per-scene TRACK vs GENERAL (OpenShorts logic).

    `scenes` is a list of (start_sec, end_sec) tuples (Klipin format, from
    reframe.detect_scenes). Returns a list of 'TRACK'/'GENERAL' aligned 1:1
    with `scenes`:

      0 faces            -> GENERAL  (B-roll / landscape)
      1 face  (avg<=1.2) -> TRACK    (crop to the talking head)
      >=2 people         -> GENERAL  (group / debate -> 3-stack blur / SPLIT / PANEL)

    ponytail: person counting uses YOLO (bodies) merged with face detection so
    a 2-person shot where one faces away (face count 1) still counts as 2 and
    routes to GENERAL instead of cropping to a single head.
    """
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return ['TRACK'] * len(scenes)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frame_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    MIN_FACE_WIDTH_FRAC = 0.045
    strategies = []

    for start, end in scenes:
        s_f, e_f = int(start * fps), int(end * fps)
        margin = min(2, max(0, (e_f - s_f - 1) // 2))
        if e_f - 1 - margin <= s_f + margin:
            frames_to_check = [max(0, (s_f + e_f) // 2)]
        else:
            frames_to_check = sorted(set(
                int(round(f)) for f in np.linspace(s_f + margin, e_f - 1 - margin, 5)
            ))
        face_counts = []
        person_counts = []
        for f_idx in frames_to_check:
            cap.set(cv2.CAP_PROP_POS_FRAMES, f_idx)
            ret, frame = cap.read()
            if not ret or frame is None:
                continue
            if frame.mean() < 16:  # cut-to-black / fades carry no faces
                continue
            faces = detect_face_candidates(frame)
            fw = float(frame.shape[1]) if frame.size else 0.0
            face_n = len([b for b in faces if b['box'][2] >= MIN_FACE_WIDTH_FRAC * fw]) if fw else len(faces)
            face_counts.append(face_n)
            # YOLO person count (bodies facing away still detected).
            try:
                persons = detector._yolo_persons(frame)
                person_n = len([p for p in persons if p[2] >= MIN_FACE_WIDTH_FRAC * fw]) if fw else len(persons)
            except Exception:
                person_n = 0
            person_counts.append(person_n)

        avg_faces = (sum(face_counts) / len(face_counts)) if face_counts else 0
        avg_persons = (sum(person_counts) / len(person_counts)) if person_counts else 0
        # Use the most inclusive count: if YOLO sees 2 bodies but faces see 1
        # (someone looking away), still treat it as >=2 people -> GENERAL.
        subjects = max(avg_faces, avg_persons)
        strategies.append('GENERAL' if (subjects > 1.2 or subjects < 0.5) else 'TRACK')
    cap.release()

    # Hysteresis: short scene flanked by the same opposite strategy flips to it.
    max_flip_frames = int(2.0 * fps)
    for i in range(1, len(strategies) - 1):
        dur = (scenes[i][1] - scenes[i][0]) * fps
        if (dur < max_flip_frames
                and strategies[i - 1] == strategies[i + 1] != strategies[i]):
            strategies[i] = strategies[i - 1]
    return strategies


# --- camera state machines (verbatim from OpenShorts) -----------------------
JUMP_CONFIRM_FRAMES = max(int(os.environ.get("JUMP_CONFIRM_FRAMES", "3")), 1)


class SmoothedCameraman:
    """Heavy-tripod camera: only moves when the subject leaves the safe zone.

    Slow, linear pans. Matches the reference OpenShorts footage behaviour.
    """
    def __init__(self, output_width, output_height, video_width, video_height,
                 aspect_ratio=9 / 16):
        self.output_width = output_width
        self.output_height = output_height
        self.video_width = video_width
        self.video_height = video_height
        self.aspect_ratio = aspect_ratio

        self.current_center_x = video_width / 2
        self.target_center_x = video_width / 2

        self.crop_height = video_height
        self.crop_width = int(self.crop_height * aspect_ratio)
        if self.crop_width > video_width:
            self.crop_width = video_width
            self.crop_height = int(self.crop_width / aspect_ratio)

        self.safe_zone_radius = self.crop_width * 0.25
        self.jump_confirm_frames = JUMP_CONFIRM_FRAMES
        self._pending_target = None
        self._pending_count = 0

    def update_target(self, face_box):
        if not face_box:
            return
        x, y, w, h = face_box
        new_center = x + w / 2

        if abs(new_center - self.target_center_x) > self.safe_zone_radius:
            if (self._pending_target is not None
                    and abs(new_center - self._pending_target) <= self.safe_zone_radius):
                self._pending_count += 1
            else:
                self._pending_target = new_center
                self._pending_count = 1
            if self._pending_count < self.jump_confirm_frames:
                return

        self._pending_target = None
        self._pending_count = 0
        self.target_center_x = new_center

    def get_crop_box(self, force_snap=False):
        if force_snap:
            self.current_center_x = self.target_center_x
        else:
            diff = self.target_center_x - self.current_center_x
            if abs(diff) > self.safe_zone_radius:
                direction = 1 if diff > 0 else -1
                if abs(diff) > self.crop_width * 0.5:
                    speed = 15.0
                else:
                    speed = 3.0
                self.current_center_x += direction * speed
                new_diff = self.target_center_x - self.current_center_x
                if (direction == 1 and new_diff < 0) or (direction == -1 and new_diff > 0):
                    self.current_center_x = self.target_center_x

        half_crop = self.crop_width / 2
        if self.current_center_x - half_crop < 0:
            self.current_center_x = half_crop
        if self.current_center_x + half_crop > self.video_width:
            self.current_center_x = self.video_width - half_crop

        x1 = int(self.current_center_x - half_crop)
        x2 = int(self.current_center_x + half_crop)
        x1 = max(0, x1)
        x2 = min(self.video_width, x2)
        return x1, 0, x2, self.video_height


class SpeakerTracker:
    """Sticky speaker tracking: hysteresis + cooldown prevent flicker."""
    def __init__(self, stabilization_frames=15, cooldown_frames=30):
        self.active_speaker_id = None
        self.speaker_scores = {}
        self.last_seen = {}
        self.locked_counter = 0
        self.stabilization_threshold = stabilization_frames
        self.switch_cooldown = cooldown_frames
        self.last_switch_frame = -1000
        self.next_id = 0
        self.known_faces = []

    def get_target(self, face_candidates, frame_number, width):
        current_candidates = []
        for face in face_candidates:
            x, y, w, h = face['box']
            center_x = x + w / 2
            best_match_id = -1
            min_dist = width * 0.15
            for kf in self.known_faces:
                if frame_number - kf['last_frame'] > 30:
                    continue
                dist = abs(center_x - kf['center'])
                if dist < min_dist:
                    min_dist = dist
                    best_match_id = kf['id']
            if best_match_id == -1:
                best_match_id = self.next_id
                self.next_id += 1
            self.known_faces = [kf for kf in self.known_faces
                                if kf['id'] != best_match_id]
            self.known_faces.append({'id': best_match_id, 'center': center_x,
                                     'last_frame': frame_number})
            current_candidates.append({'id': best_match_id, 'box': face['box'],
                                       'score': face['score']})

        for pid in list(self.speaker_scores.keys()):
            self.speaker_scores[pid] *= 0.85
            if self.speaker_scores[pid] < 0.1:
                del self.speaker_scores[pid]

        for cand in current_candidates:
            pid = cand['id']
            raw_score = cand['score'] / (width * width * 0.05)
            self.speaker_scores[pid] = self.speaker_scores.get(pid, 0) + raw_score

        if not current_candidates:
            return None

        best_candidate = None
        max_score = -1
        for cand in current_candidates:
            pid = cand['id']
            total_score = self.speaker_scores.get(pid, 0)
            if pid == self.active_speaker_id:
                total_score *= 3.0
            if total_score > max_score:
                max_score = total_score
                best_candidate = cand

        if best_candidate:
            target_id = best_candidate['id']
            if target_id == self.active_speaker_id:
                self.locked_counter += 1
                return best_candidate['box']
            if frame_number - self.last_switch_frame < self.switch_cooldown:
                old_cand = next((c for c in current_candidates
                                 if c['id'] == self.active_speaker_id), None)
                return old_cand['box'] if old_cand else None
            self.active_speaker_id = target_id
            self.last_switch_frame = frame_number
            self.locked_counter = 0
            return best_candidate['box']
        return None
