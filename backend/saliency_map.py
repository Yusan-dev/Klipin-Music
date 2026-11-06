"""Saliency map computation for visual importance scoring.

Phase 3 of Opus Clip parity (PRD_OPUS_CLIP_PARITY.md).

Computes pixel-level visual importance to guide crop window placement:
  - High-saliency regions (faces, text, motion) should stay in frame
  - Low-saliency regions (background, sky) can be cropped

Uses OpenCV's built-in saliency detection (Spectral Residual) as a
lightweight alternative to deep learning saliency models.
"""
import numpy as np
import cv2
from typing import Tuple, Optional


def compute_saliency_map(frame_bgr) -> np.ndarray:
    """Compute visual saliency map using Spectral Residual method.

    Returns:
        saliency: float32 array (0-1) same size as input frame
                  Higher = more visually important
    """
    # Use OpenCV's built-in saliency (Spectral Residual)
    try:
        saliency_detector = cv2.saliency.StaticSaliencySpectralResidual_create()
        ok, saliency = saliency_detector.computeSaliency(frame_bgr)
        if ok and saliency is not None:
            return saliency.astype(np.float32)
    except Exception:
        pass

    # Fallback: simple saliency based on gradient magnitude + contrast
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)

    # Gradient magnitude (edge strength)
    grad_x = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    grad_y = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    gradient = np.sqrt(grad_x ** 2 + grad_y ** 2)

    # Local contrast (difference from local mean)
    blurred = cv2.GaussianBlur(gray, (21, 21), 0)
    contrast = np.abs(gray.astype(np.float32) - blurred.astype(np.float32))

    # Combine gradient + contrast
    saliency = 0.5 * (gradient / (gradient.max() + 1e-6)) + \
               0.5 * (contrast / (contrast.max() + 1e-6))

    return saliency.astype(np.float32)


def saliency_center_of_mass(saliency: np.ndarray) -> Tuple[float, float]:
    """Compute center of mass of saliency map.

    Returns:
        (cx, cy) — weighted center of visual importance
    """
    h, w = saliency.shape
    total = saliency.sum()
    if total <= 0:
        return w / 2, h / 2

    # Weighted average of coordinates
    y_coords, x_coords = np.mgrid[0:h, 0:w]
    cx = (x_coords * saliency).sum() / total
    cy = (y_coords * saliency).sum() / total

    return float(cx), float(cy)


def saliency_bounding_box(saliency: np.ndarray,
                          threshold: float = 0.3,
                          margin: int = 30) -> Tuple[int, int, int, int]:
    """Find bounding box of high-saliency region.

    Args:
        threshold: saliency value above which = "important"
        margin: extra pixels around the bounding box

    Returns:
        (x0, y0, x1, y1) — bounding box of important region
    """
    h, w = saliency.shape
    mask = (saliency > threshold).astype(np.uint8)

    # Find contours of high-saliency regions
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)

    if not contours:
        # Fallback: center of frame
        return margin, margin, w - margin, h - margin

    # Get bounding box of all contours combined
    all_points = np.vstack(contours)
    x, y, bw, bh = cv2.boundingRect(all_points)

    # Apply margin
    x0 = max(0, x - margin)
    y0 = max(0, y - margin)
    x1 = min(w, x + bw + margin)
    y1 = min(h, y + bh + margin)

    return x0, y0, x1, y1


def compute_region_saliency(saliency: np.ndarray,
                            x0: int, y0: int,
                            x1: int, y1: int) -> float:
    """Compute average saliency in a rectangular region.

    Returns:
        float (0-1) — average importance in the region
    """
    h, w = saliency.shape
    x0 = max(0, min(x0, w))
    y0 = max(0, min(y0, h))
    x1 = max(0, min(x1, w))
    y1 = max(0, min(y1, h))

    if x1 <= x0 or y1 <= y0:
        return 0.0

    region = saliency[y0:y1, x0:x1]
    return float(region.mean())


def crop_saliency_score(crop_x: int, crop_y: int,
                        crop_w: int, crop_h: int,
                        saliency: np.ndarray) -> float:
    """Score how much visual importance a crop window captures.

    Returns:
        float (0-1) — fraction of total saliency captured by crop
    """
    total_saliency = saliency.sum()
    if total_saliency <= 0:
        return 0.0

    crop_saliency = compute_region_saliency(
        saliency, crop_x, crop_y,
        crop_x + crop_w, crop_y + crop_h
    )
    crop_area = crop_w * crop_h
    total_area = saliency.shape[1] * saliency.shape[0]

    # Normalize by area ratio
    area_ratio = crop_area / total_area if total_area > 0 else 0.0
    if area_ratio <= 0:
        return 0.0

    # Score = (saliency density in crop) / (average saliency density)
    crop_density = crop_saliency / area_ratio if area_ratio > 0 else 0
    avg_density = total_saliency / total_area if total_area > 0 else 0

    if avg_density <= 0:
        return 0.0

    return min(1.0, crop_density / avg_density)


def find_best_crop_by_saliency(saliency: np.ndarray,
                               tw: int, th: int,
                               frame_w: int, frame_h: int,
                               n_candidates: int = 20) -> Tuple[int, int, int, int]:
    """Find the crop window that captures the most visual importance.

    Tries n_candidates random positions and picks the one with highest
    saliency score.

    Args:
        tw, th: target crop dimensions (aspect ratio)
        frame_w, frame_h: source frame dimensions
        n_candidates: number of random positions to try

    Returns:
        (x, y, w, h) — best crop window
    """
    # Compute aspect ratio
    aspect = tw / th if th > 0 else 9 / 16

    # Compute crop dimensions that fit in frame
    max_h = frame_h
    max_w = int(max_h * aspect)
    if max_w > frame_w:
        max_w = frame_w
        max_h = int(max_w / aspect)

    best_score = -1
    best_crop = (0, 0, max_w, max_h)

    # Try center first
    cx = (frame_w - max_w) // 2
    cy = (frame_h - max_h) // 2
    best_score = crop_saliency_score(cx, cy, max_w, max_h, saliency)
    best_crop = (cx, cy, max_w, max_h)

    # Random candidates
    rng = np.random.RandomState(42)
    for _ in range(n_candidates):
        x = rng.randint(0, max(1, frame_w - max_w))
        y = rng.randint(0, max(1, frame_h - max_h))
        score = crop_saliency_score(x, y, max_w, max_h, saliency)

        if score > best_score:
            best_score = score
            best_crop = (x, y, max_w, max_h)

    return best_crop
