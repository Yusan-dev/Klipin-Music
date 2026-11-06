"""Pose estimation for hand/arm detection — keeps hands visible in crop.

Phase 2 of Opus Clip parity (PRD_OPUS_CLIP_PARITY.md).

Uses MediaPipe Pose (lightweight) to detect upper body landmarks:
  - Shoulders, elbows, wrists — keeps gestures/hands in frame
  - Head center — alternative face position when face detection fails
  - Upper body bounding box — expands crop to include gesturing arms

Falls back gracefully when MediaPipe is unavailable.
"""
import os
import numpy as np
import cv2
from typing import List, Tuple, Optional, Dict


# MediaPipe Pose landmark indices (upper body only)
LEFT_SHOULDER = 11
RIGHT_SHOULDER = 12
LEFT_ELBOW = 13
RIGHT_ELBOW = 14
LEFT_WRIST = 15
RIGHT_WRIST = 16
NOSE = 0


class PoseDetector:
    """MediaPipe Pose-based upper body detection for crop expansion."""

    def __init__(self, min_detection_confidence: float = 0.5,
                 analysis_width: int = 640):
        self.min_detection_confidence = min_detection_confidence
        self.analysis_width = analysis_width
        self._pose = None
        self._failed = False

    def _get_pose(self):
        """Lazy-load MediaPipe Pose (thread-local)."""
        if self._failed:
            return None
        if self._pose is not None:
            return self._pose
        try:
            import mediapipe as mp
            self._pose = mp.solutions.pose.Pose(
                static_image_mode=False,
                model_complexity=0,  # lightweight
                min_detection_confidence=self.min_detection_confidence,
                min_tracking_confidence=0.5,
            )
            return self._pose
        except Exception:
            self._failed = True
            return None

    def detect_upper_body(self, frame_bgr) -> Optional[Dict]:
        """Detect upper body landmarks in a frame.

        Returns:
            {
                "head_center": (cx, cy),           # nose position
                "shoulders": [(lx, ly), (rx, ry)], # shoulder centers
                "hands": [(lx, ly), (rx, ry)],     # wrist positions
                "upper_body_bbox": (x0, y0, x1, y1),  # bounding box of all
                "confidence": float,
            }
            or None if no pose detected.
        """
        pose = self._get_pose()
        if pose is None:
            return None

        h, w = frame_bgr.shape[:2]
        scale = 1.0
        if w > self.analysis_width:
            scale = self.analysis_width / w
            small = cv2.resize(frame_bgr, (self.analysis_width, int(h * scale)))
        else:
            small = frame_bgr

        rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
        results = pose.process(rgb)

        if not results.pose_landmarks:
            return None

        landmarks = results.pose_landmarks.landmark
        sw, sh = small.shape[1], small.shape[0]

        def to_px(lm):
            return (lm.x * sw / scale, lm.y * sh / scale)

        # Extract key points
        nose = to_px(landmarks[NOSE])
        l_shoulder = to_px(landmarks[LEFT_SHOULDER])
        r_shoulder = to_px(landmarks[RIGHT_SHOULDER])
        l_wrist = to_px(landmarks[LEFT_WRIST])
        r_wrist = to_px(landmarks[RIGHT_WRIST])

        # Confidence: average visibility of key landmarks
        conf = np.mean([
            landmarks[NOSE].visibility,
            landmarks[LEFT_SHOULDER].visibility,
            landmarks[RIGHT_SHOULDER].visibility,
        ])

        if conf < 0.3:
            return None

        # Upper body bounding box
        all_pts = [nose, l_shoulder, r_shoulder, l_wrist, r_wrist]
        xs = [p[0] for p in all_pts]
        ys = [p[1] for p in all_pts]
        margin = 30  # px margin around landmarks
        bbox = (
            max(0, min(xs) - margin),
            max(0, min(ys) - margin),
            min(w, max(xs) + margin),
            min(h, max(ys) + margin),
        )

        return {
            "head_center": nose,
            "shoulders": [l_shoulder, r_shoulder],
            "hands": [l_wrist, r_wrist],
            "upper_body_bbox": bbox,
            "confidence": float(conf),
        }

    def expand_crop_for_hands(self, crop_x: int, crop_y: int,
                              crop_w: int, crop_h: int,
                              pose_data: Dict,
                              frame_w: int, frame_h: int,
                              max_expand: float = 0.15) -> Tuple[int, int, int, int]:
        """Expand crop window to include hands if they're outside.

        Args:
            crop_x, crop_y, crop_w, crop_h: current crop window
            pose_data: from detect_upper_body()
            frame_w, frame_h: source frame dimensions
            max_expand: max fraction of crop to expand

        Returns:
            (new_x, new_y, new_w, new_h) — expanded crop window
        """
        if not pose_data:
            return crop_x, crop_y, crop_w, crop_h

        hands = pose_data.get("hands", [])
        if not hands:
            return crop_x, crop_y, crop_w, crop_h

        # Check if any hand is outside the crop
        expand_x = 0
        expand_y = 0

        for hx, hy in hands:
            if hx < crop_x:
                expand_x = max(expand_x, crop_x - hx)
            elif hx > crop_x + crop_w:
                expand_x = max(expand_x, hx - (crop_x + crop_w))
            if hy < crop_y:
                expand_y = max(expand_y, crop_y - hy)
            elif hy > crop_y + crop_h:
                expand_y = max(expand_y, hy - (crop_y + crop_h))

        # Limit expansion
        max_px_x = int(crop_w * max_expand)
        max_px_y = int(crop_h * max_expand)
        expand_x = min(expand_x, max_px_x)
        expand_y = min(expand_y, max_px_y)

        if expand_x == 0 and expand_y == 0:
            return crop_x, crop_y, crop_w, crop_h

        # Apply expansion (center the crop on the expanded area)
        new_x = max(0, crop_x - expand_x // 2)
        new_y = max(0, crop_y - expand_y // 2)
        new_w = crop_w + expand_x
        new_h = crop_h + expand_y

        # Clamp to frame
        if new_x + new_w > frame_w:
            new_x = max(0, frame_w - new_w)
        if new_y + new_h > frame_h:
            new_y = max(0, frame_h - new_h)

        return new_x, new_y, new_w, new_h


def detect_pose_fallback(frame_bgr, face_box=None) -> Optional[Dict]:
    """Simple pose estimation without MediaPipe — uses face box + body heuristics.

    When MediaPipe is unavailable, estimate upper body from face detection:
    - Head center = face center
    - Shoulders estimated at 1.5x face width below face
    - Hands unknown (can't detect without model)
    """
    if face_box is None:
        return None

    cx, cy, fw, fh = face_box
    # Estimate shoulder positions (below face)
    shoulder_y = cy + fh * 1.2
    shoulder_half_w = fw * 0.8
    l_shoulder = (cx - shoulder_half_w, shoulder_y)
    r_shoulder = (cx + shoulder_half_w, shoulder_y)

    # Upper body bbox (face + estimated body)
    margin = 20
    bbox = (
        max(0, cx - fw - margin),
        max(0, cy - fh // 2 - margin),
        cx + fw + margin,
        shoulder_y + fh * 0.5 + margin,
    )

    return {
        "head_center": (cx, cy),
        "shoulders": [l_shoulder, r_shoulder],
        "hands": [],  # unknown without model
        "upper_body_bbox": bbox,
        "confidence": 0.5,  # estimate
    }
