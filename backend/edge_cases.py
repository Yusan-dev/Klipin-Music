"""Edge case handling for crop safety — watermarks, logos, black bars.

Phase 3 of Opus Clip parity (PRD_OPUS_CLIP_PARITY.md).

Detects and handles common video edge cases that can break crop quality:
  - Watermarks/logos (fixed position, should stay in frame)
  - Black bars / letterbox (should be cropped out)
  - Color bars / test patterns (should be skipped)
  - Very dark / very bright frames (should handle gracefully)
"""
import numpy as np
import cv2
from typing import Tuple, Optional, Dict


def detect_watermark(frame_bgr,
                     corner: str = "top_right",
                     corner_fraction: float = 0.15) -> Optional[Dict]:
    """Detect watermark/logo in frame corner.

    Watermarks are usually semi-transparent logos in a corner.
    We detect them by finding high-contrast regions in corners that
    persist across frames.

    Args:
        corner: "top_left" | "top_right" | "bottom_left" | "bottom_right"
        corner_fraction: fraction of frame to search in each dimension

    Returns:
        {"bbox": (x0, y0, x1, y1), "confidence": float} or None
    """
    h, w = frame_bgr.shape[:2]
    cw = int(w * corner_fraction)
    ch = int(h * corner_fraction)

    # Determine corner region
    if corner == "top_left":
        roi = frame_bgr[0:ch, 0:cw]
        offset = (0, 0)
    elif corner == "top_right":
        roi = frame_bgr[0:ch, w-cw:w]
        offset = (w - cw, 0)
    elif corner == "bottom_left":
        roi = frame_bgr[h-ch:h, 0:cw]
        offset = (0, h - ch)
    else:  # bottom_right
        roi = frame_bgr[h-ch:h, w-cw:w]
        offset = (w - cw, h - ch)

    if roi.size == 0:
        return None

    # Convert to grayscale
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)

    # Watermarks are usually brighter/darker than background
    # Check if there's a distinct region
    mean_val = gray.mean()
    std_val = gray.std()

    if std_val < 15:
        # Low contrast — probably no watermark
        return None

    # Find the most "logo-like" region (high contrast, small area)
    # Use adaptive threshold to find bright/dark spots
    _, thresh = cv2.threshold(gray, 0, 255,
                              cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    # Find contours
    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)

    if not contours:
        return None

    # Find the largest contour (likely the logo)
    largest = max(contours, key=cv2.contourArea)
    x, y, cw_r, ch_r = cv2.boundingRect(largest)

    # Must be small (watermark, not the whole corner)
    area_ratio = (cw_r * ch_r) / (roi.shape[0] * roi.shape[1])
    if area_ratio > 0.5 or area_ratio < 0.01:
        return None

    # Convert to full frame coordinates
    x0 = x + offset[0]
    y0 = y + offset[1]
    x1 = x0 + cw_r
    y1 = y0 + ch_r

    # Confidence based on contrast
    confidence = min(1.0, std_val / 50.0)

    return {
        "bbox": (x0, y0, x1, y1),
        "confidence": confidence,
    }


def detect_black_bars(frame_bgr,
                      threshold: int = 15,
                      min_bar_fraction: float = 0.03) -> Dict:
    """Detect black bars (letterbox/pillarbox) in frame.

    Returns:
        {
            "top": bar_height,
            "bottom": bar_height,
            "left": bar_width,
            "right": bar_width,
            "has_bars": bool,
        }
    """
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape

    min_bar_h = int(h * min_bar_fraction)
    min_bar_w = int(w * min_bar_fraction)

    # Check top bar
    top_bar = 0
    for y in range(min(h, int(h * 0.2))):
        row_mean = gray[y, :].mean()
        if row_mean < threshold:
            top_bar = y + 1
        else:
            break

    # Check bottom bar
    bottom_bar = 0
    for y in range(h - 1, max(0, h - int(h * 0.2)), -1):
        row_mean = gray[y, :].mean()
        if row_mean < threshold:
            bottom_bar = h - y
        else:
            break

    # Check left bar
    left_bar = 0
    for x in range(min(w, int(w * 0.2))):
        col_mean = gray[:, x].mean()
        if col_mean < threshold:
            left_bar = x + 1
        else:
            break

    # Check right bar
    right_bar = 0
    for x in range(w - 1, max(0, w - int(w * 0.2)), -1):
        col_mean = gray[:, x].mean()
        if col_mean < threshold:
            right_bar = w - x
        else:
            break

    has_bars = (top_bar >= min_bar_h or bottom_bar >= min_bar_h or
                left_bar >= min_bar_w or right_bar >= min_bar_w)

    return {
        "top": top_bar,
        "bottom": bottom_bar,
        "left": left_bar,
        "right": right_bar,
        "has_bars": has_bars,
    }


def remove_black_bars(frame_bgr, bars: Dict = None) -> Tuple[np.ndarray, Tuple[int, int, int, int]]:
    """Remove black bars from frame and return cropped frame + crop coords.

    Returns:
        (cropped_frame, (x0, y0, x1, y1))
    """
    if bars is None:
        bars = detect_black_bars(frame_bgr)

    if not bars["has_bars"]:
        return frame_bgr, (0, 0, frame_bgr.shape[1], frame_bgr.shape[0])

    h, w = frame_bgr.shape[:2]
    y0 = bars["top"]
    y1 = h - bars["bottom"]
    x0 = bars["left"]
    x1 = w - bars["right"]

    # Ensure valid crop
    if y1 <= y0 or x1 <= x0:
        return frame_bgr, (0, 0, w, h)

    cropped = frame_bgr[y0:y1, x0:x1]
    return cropped, (x0, y0, x1, y1)


def detect_color_bars(frame_bgr,
                      n_bars: int = 7,
                      min_fraction: float = 0.1) -> bool:
    """Detect SMPTE color bars or test patterns.

    Color bars are uniform vertical stripes of different colors.
    They indicate the video is a test pattern, not real content.
    """
    h, w = frame_bgr.shape[:2]
    # Sample middle row
    mid_row = frame_bgr[h // 2, :]

    # Count color transitions
    transitions = 0
    prev_color = mid_row[0]
    for pixel in mid_row[::10]:  # sample every 10 pixels
        diff = np.abs(pixel.astype(int) - prev_color.astype(int)).mean()
        if diff > 30:
            transitions += 1
        prev_color = pixel

    # Color bars have many transitions
    expected_transitions = n_bars - 1
    return transitions >= expected_transitions * 0.7


def is_valid_frame(frame_bgr,
                   min_brightness: int = 5,
                   max_brightness: int = 250,
                   min_variance: float = 10.0) -> Tuple[bool, str]:
    """Check if frame is valid for cropping (not black, not frozen, not test pattern).

    Returns:
        (is_valid, reason)
    """
    if frame_bgr is None or frame_bgr.size == 0:
        return False, "empty_frame"

    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    mean_val = gray.mean()
    var_val = gray.var()

    # Too dark (black frame)
    if mean_val < min_brightness:
        return False, "too_dark"

    # Too bright (white frame)
    if mean_val > max_brightness:
        return False, "too_bright"

    # No variance (frozen/blank frame)
    if var_val < min_variance:
        return False, "frozen"

    # Color bars
    if detect_color_bars(frame_bgr):
        return False, "color_bars"

    return True, "ok"


def adjust_crop_for_watermarks(crop_x: int, crop_y: int,
                               crop_w: int, crop_h: int,
                               watermarks: list,
                               frame_w: int, frame_h: int,
                               margin: int = 10) -> Tuple[int, int, int, int]:
    """Adjust crop to keep watermarks visible if they overlap the crop edge.

    If a watermark is partially cut by the crop boundary, shift the crop
    to include it (watermarks are usually important for branding).
    """
    new_x, new_y = crop_x, crop_y

    for wm in watermarks:
        bbox = wm.get("bbox", (0, 0, 0, 0))
        conf = wm.get("confidence", 0)
        if conf < 0.3:
            continue

        wm_x0, wm_y0, wm_x1, wm_y1 = bbox

        # Check if watermark is cut by crop boundary
        # Left edge
        if wm_x0 < crop_x and wm_x1 > crop_x:
            shift = crop_x - wm_x0 + margin
            new_x = max(0, new_x - shift)

        # Right edge
        crop_right = crop_x + crop_w
        if wm_x1 > crop_right and wm_x0 < crop_right:
            shift = wm_x1 - crop_right + margin
            new_x = min(frame_w - crop_w, new_x + shift)

        # Top edge
        if wm_y0 < crop_y and wm_y1 > crop_y:
            shift = crop_y - wm_y0 + margin
            new_y = max(0, new_y - shift)

        # Bottom edge
        crop_bottom = crop_y + crop_h
        if wm_y1 > crop_bottom and wm_y0 < crop_bottom:
            shift = wm_y1 - crop_bottom + margin
            new_y = min(frame_h - crop_h, new_y + shift)

    # Clamp
    new_x = max(0, min(new_x, frame_w - crop_w))
    new_y = max(0, min(new_y, frame_h - crop_h))

    return new_x, new_y, crop_w, crop_h
