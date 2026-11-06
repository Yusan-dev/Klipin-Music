"""Optical flow analysis for camera movement detection.

Phase 2 of Opus Clip parity (PRD_OPUS_CLIP_PARITY.md).

Uses Lucas-Kanade sparse optical flow to detect:
  - Pan speed and direction (horizontal/vertical camera movement)
  - Zoom in/out (frame size changes)
  - Camera shake vs intentional movement

Output: per-frame camera motion vector (dx, dy, dz) that the crop
window can follow for smooth camera-aware reframing.
"""
import numpy as np
import cv2
from typing import List, Tuple, Optional


# Lucas-Kanade parameters
LK_PARAMS = dict(
    winSize=(21, 21),
    maxLevel=3,
    criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01),
)

# Shi-Tomasi corner detection parameters
FEATURE_PARAMS = dict(
    maxCorners=200,
    qualityLevel=0.3,
    minDistance=7,
    blockSize=7,
)


def detect_features(gray_frame) -> np.ndarray:
    """Detect good features to track (Shi-Tomasi corners)."""
    points = cv2.goodFeaturesToTrack(gray_frame, mask=None, **FEATURE_PARAMS)
    return points


def compute_flow(prev_gray, curr_gray, prev_points) -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
    """Compute Lucas-Kanade optical flow between two frames.

    Returns:
        (next_points, status) — status[i] == 1 if point i was tracked
    """
    if prev_points is None or len(prev_points) == 0:
        return None, None

    next_points, status, error = cv2.calcOpticalFlowPyrLK(
        prev_gray, curr_gray, prev_points, None, **LK_PARAMS
    )
    return next_points, status


def estimate_camera_motion(flow_points: np.ndarray, status: np.ndarray,
                           frame_w: int, frame_h: int) -> Tuple[float, float, float]:
    """Estimate camera motion from tracked points.

    Returns:
        (dx, dy, zoom) — per-frame camera motion
        dx: horizontal pan (positive = camera pans right)
        dy: vertical pan (positive = camera pans down)
        zoom: zoom factor (>1 = zoom in, <1 = zoom out, 1.0 = no change)
    """
    if flow_points is None or status is None:
        return 0.0, 0.0, 1.0

    # Filter to only successfully tracked points
    good_mask = status.flatten() == 1
    if good_mask.sum() < 5:
        return 0.0, 0.0, 1.0

    # Compute median displacement from center (robust to outliers)
    center_x, center_y = frame_w / 2, frame_h / 2
    good_pts = flow_points[good_mask, 0]

    # Estimate motion as median displacement of tracked points
    # Points moving left = camera panning right (dx positive)
    dx = float(np.median(good_pts[:, 0] - center_x)) * 0.01
    dy = float(np.median(good_pts[:, 1] - center_y)) * 0.01

    # Zoom estimate from point spread change
    dists = np.sqrt((good_pts[:, 0] - center_x) ** 2 +
                    (good_pts[:, 1] - center_y) ** 2)
    avg_dist = np.mean(dists)
    # Normalize by frame diagonal for rough zoom
    diag = (frame_w ** 2 + frame_h ** 2) ** 0.5
    zoom = 1.0 + (avg_dist / diag - 0.3) * 0.1  # rough approximation
    zoom = max(0.9, min(1.1, zoom))  # clamp to reasonable range

    return dx, dy, zoom


def smooth_camera_motion(motions: List[Tuple[float, float, float]],
                         window: int = 5) -> List[Tuple[float, float, float]]:
    """Smooth camera motion vectors with a median filter.

    Removes jitter while preserving intentional camera movements.
    """
    if len(motions) <= 1:
        return motions

    dxs = [m[0] for m in motions]
    dys = [m[1] for m in motions]
    zooms = [m[2] for m in motions]

    def median_smooth(arr, w):
        result = []
        for i in range(len(arr)):
            lo = max(0, i - w // 2)
            hi = min(len(arr), i + w // 2 + 1)
            result.append(float(np.median(arr[lo:hi])))
        return result

    dxs_smooth = median_smooth(dxs, window)
    dys_smooth = median_smooth(dys, window)
    zooms_smooth = median_smooth(zooms, window)

    return list(zip(dxs_smooth, dys_smooth, zooms_smooth))


def motion_to_crop_offsets(motions: List[Tuple[float, float, float]],
                           crop_w: int, crop_h: int,
                           frame_w: int, frame_h: int,
                           gain: float = 0.5) -> List[Tuple[int, int]]:
    """Convert camera motion vectors to crop window offset adjustments.

    When the camera pans right (dx > 0), the crop window should shift right
    to keep the subject centered (inverse motion).

    Args:
        gain: how aggressively to follow camera motion (0=ignore, 1=full follow)

    Returns:
        [(offset_x, offset_y), ...] — per-frame crop offsets
    """
    offsets = []
    acc_x, acc_y = 0.0, 0.0

    for dx, dy, zoom in motions:
        # Inverse camera motion → crop offset
        acc_x += dx * gain
        acc_y += dy * gain

        # Clamp so crop stays within frame
        max_x = max(0, (frame_w - crop_w) / 2)
        max_y = max(0, (frame_h - crop_h) / 2)
        acc_x = np.clip(acc_x, -max_x, max_x)
        acc_y = np.clip(acc_y, -max_y, max_y)

        offsets.append((int(round(acc_x)), int(round(acc_y))))

    return offsets


def analyze_video_motion(video_path: str, sample_fps: float = 10.0,
                         log=None) -> List[Tuple[float, float, float]]:
    """Analyze camera motion across an entire video.

    Args:
        video_path: path to video file
        sample_fps: analysis frame rate (lower = faster, less precise)

    Returns:
        List of (dx, dy, zoom) per sampled frame
    """
    import cv2

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return []

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    step = max(1, int(fps / sample_fps))

    log and log(f"🔍 Analyzing camera motion @ {sample_fps:.0f}fps ({total_frames // step} frames)...")

    prev_gray = None
    prev_points = None
    motions = []
    frame_idx = 0

    while True:
        ok, frame = cap.read()
        if not ok:
            break

        if frame_idx % step != 0:
            frame_idx += 1
            continue

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape

        if prev_gray is None:
            prev_gray = gray
            prev_points = detect_features(gray)
            motions.append((0.0, 0.0, 1.0))
            frame_idx += 1
            continue

        # Compute flow
        next_points, status = compute_flow(prev_gray, gray, prev_points)

        if next_points is not None and status is not None:
            dx, dy, zoom = estimate_camera_motion(next_points, status, w, h)
            motions.append((dx, dy, zoom))

            # Re-detect features periodically (every 30 frames)
            if frame_idx % (step * 30) == 0:
                prev_points = detect_features(gray)
            else:
                # Keep only good points for next iteration
                good_mask = status.flatten() == 1
                prev_points = next_points[good_mask].reshape(-1, 1, 2)
        else:
            motions.append((0.0, 0.0, 1.0))
            prev_points = detect_features(gray)

        prev_gray = gray
        frame_idx += 1

    cap.release()

    # Smooth the motion vectors
    motions = smooth_camera_motion(motions)

    log and log(f"✅ Camera motion analysis: {len(motions)} frames")
    return motions
