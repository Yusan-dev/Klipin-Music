"""Subject detection for Klipin reframing.

Follows the Opus-Clip-style stack recommended in bug-report-crop-clipper.md:

  - MediaPipe Face Detection -> precise face boxes (primary)
  - YOLOv8 person detection  -> robust fallback when the face is occluded,
                                turned away, or out of frame (largest person
                                becomes the crop target)
  - Haar cascade             -> last resort so reframing still works on hosts
                                without mediapipe / ultralytics installed

Detectors are lazy AND thread-local: the async job queue runs several renders
concurrently, and MediaPipe/YOLO objects are stateful and must not be shared
across threads. Loading is deferred until the first frame so importing this
module never requires the heavy model packages.
"""
import os
import threading

import cv2
import numpy as np

YOLO_MODEL = os.environ.get("YOLO_MODEL_PATH", "yolov8n.pt")
YOLO_CONF = 0.35
FACE_MIN_CONF = 0.5

_local = threading.local()


def _mp_face_detector():
    if getattr(_local, "mp_face_failed", False):
        return None
    det = getattr(_local, "mp_face", None)
    if det is None:
        try:
            import mediapipe as mp
        except Exception:
            _local.mp_face_failed = True
            return None
        det = mp.solutions.face_detection.FaceDetection(
            model_selection=1, min_detection_confidence=FACE_MIN_CONF)
        _local.mp_face = det
    return det


def _yolo_detector():
    if getattr(_local, "yolo_failed", False):
        return None
    det = getattr(_local, "yolo", None)
    if det is None:
        try:
            from ultralytics import YOLO
        except Exception:
            _local.yolo_failed = True
            return None
        det = YOLO(YOLO_MODEL)
        _local.yolo = det
    return det


def _haar_cascade():
    det = getattr(_local, "haar", None)
    if det is None:
        candidates = []
        if hasattr(cv2, "data") and getattr(cv2.data, "haarcascades", ""):
            candidates.append(os.path.join(
                cv2.data.haarcascades,
                "haarcascade_frontalface_default.xml"))
        candidates.append(os.path.join(
            os.path.dirname(cv2.__file__), "data",
            "haarcascade_frontalface_default.xml"))
        path = next((p for p in candidates if os.path.exists(p)), None)
        det = cv2.CascadeClassifier(path) if path else None
        _local.haar = det
    return det


def _mediapipe_faces(frame_bgr):
    """MediaPipe face boxes [(cx, cy, w, h)] in pixel coords."""
    detector = _mp_face_detector()
    if detector is None:
        return []
    rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    try:
        detections = detector.process(rgb).detections
    except Exception:
        return []
    if not detections:
        return []
    h, w = frame_bgr.shape[:2]
    out = []
    for d in detections:
        bbox = d.location_data.relative_bounding_box
        x = bbox.xmin * w
        y = bbox.ymin * h
        bw = bbox.width * w
        bh = bbox.height * h
        if bw <= 0 or bh <= 0:
            continue
        out.append((x + bw / 2, y + bh / 2, bw, bh))
    return out


def _yolo_persons(frame_bgr):
    """YOLO person boxes [(cx, cy, w, h)] in pixel coords.

    Deduplicated (one box per person, biggest wins): YOLO's own NMS lets
    overlapping whole/half-body boxes through on stacked portrait shots,
    and every downstream headcount (pair coexistence, seat clustering)
    decides by count — duplicates inflate it and fragment seat votes so
    no seat reaches the stability bar.
    """
    model = _yolo_detector()
    if model is None:
        return []
    try:
        results = model(frame_bgr, classes=[0], conf=YOLO_CONF, verbose=False)
    except Exception:
        return []
    out = []
    for r in results:
        if r.boxes is None:
            continue
        xyxy = r.boxes.xyxy.detach().cpu().numpy()
        for x1, y1, x2, y2 in xyxy:
            out.append(((x1 + x2) / 2, (y1 + y2) / 2, x2 - x1, y2 - y1))
    kept = []
    for box in sorted(out, key=lambda b: b[2] * b[3], reverse=True):
        duplicate = False
        for other in kept:
            ix = max(0.0, min(box[0] + box[2] / 2, other[0] + other[2] / 2)
                     - max(box[0] - box[2] / 2, other[0] - other[2] / 2))
            iy = max(0.0, min(box[1] + box[3] / 2, other[1] + other[3] / 2)
                     - max(box[1] - box[3] / 2, other[1] - other[3] / 2))
            inter = ix * iy
            union = box[2] * box[3] + other[2] * other[3] - inter
            if union > 0 and inter / union > 0.30:
                duplicate = True
                break
        if not duplicate:
            kept.append(box)
    return kept


def _haar_faces(frame_bgr):
    cascade = _haar_cascade()
    if cascade is None or cascade.empty():
        return []
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    gray = cv2.equalizeHist(gray)
    faces = cascade.detectMultiScale(
        gray, scaleFactor=1.1, minNeighbors=5,
        minSize=(max(40, gray.shape[1] // 12),) * 2)
    return [(x + w / 2, y + h / 2, w, h) for x, y, w, h in faces]


def _person_to_face(person):
    """Approximate the face region from a YOLO person box.

    YOLO gives a full-body box; the face sits in the upper ~20% of it. This is
    only a fallback when MediaPipe found no face, so a rough-but-stable region
    is enough to keep the crop anchored to the speaker instead of freezing.
    """
    cx, cy, pw, ph = person
    fh = ph * 0.22
    fw = pw * 0.40
    face_cy = (cy - ph / 2) + ph * 0.15
    return (cx, face_cy, fw, fh)


def face_boxes(frame_bgr):
    """Return face boxes [(cx, cy, w, h)] in frame pixel coordinates.

    Priority: MediaPipe (precise face) -> YOLO (largest person, face region
    estimated) -> Haar. An empty list means no subject was found.
    """
    if frame_bgr is None or frame_bgr.size == 0:
        return []
    faces = _mediapipe_faces(frame_bgr)
    if faces:
        return faces
    persons = _yolo_persons(frame_bgr)
    if persons:
        # Return ALL persons as face regions, not just the largest. Keeping
        # only the biggest box made the TRACK lock onto a large centre-frame
        # subject (e.g. someone holding money) whenever MediaPipe missed the
        # small/profile speaker face at the frame edge — the active-speaker
        # mouth-motion logic then never saw the real speaker to switch to.
        return sorted((_person_to_face(p) for p in persons),
                      key=lambda b: b[2] * b[3], reverse=True)
    return _haar_faces(frame_bgr)


def verified_face_boxes(frame_bgr):
    """Actual face detections only, never an estimated YOLO body region.

    Active-speaker routing must score mouth pixels belonging to a real face.
    A body-derived pseudo-face is useful as a last-resort framing anchor, but
    its ROI can land on a wall/TV when someone leans or is partly occluded.
    Merge MediaPipe and Haar results and suppress duplicate reports.
    """
    if frame_bgr is None or frame_bgr.size == 0:
        return []
    candidates = list(_mediapipe_faces(frame_bgr))
    candidates.extend(_haar_faces(frame_bgr))
    kept = []
    for box in sorted(candidates, key=lambda b: b[2] * b[3], reverse=True):
        cx, cy, bw, bh = box
        duplicate = False
        for kx, ky, kw, kh in kept:
            radius = 0.35 * max(bw, bh, kw, kh, 1.0)
            if (cx - kx) ** 2 + (cy - ky) ** 2 <= radius ** 2:
                duplicate = True
                break
        if not duplicate:
            kept.append(box)
    return kept
