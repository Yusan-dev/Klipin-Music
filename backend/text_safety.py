"""OCR + text detection for crop safety — keeps captions visible in frame.

Phase 3 of Opus Clip parity (PRD_OPUS_CLIP_PARITY.md).

Detects text regions (subtitles, captions, lower-thirds, news tickers)
and adjusts the crop window to avoid cutting them off.

Uses:
  - EasyOCR (lightweight, CPU-friendly) for text detection
  - OpenCV morphological operations as fallback
  - Heuristic rules for common text positions (bottom 20% = subtitles)
"""
import os
import numpy as np
import cv2
from typing import List, Tuple, Optional, Dict


# Common text regions in videos
SUBTITLE_REGION = "bottom"      # subtitles are usually at bottom 20%
TICKER_REGION = "bottom"        # news tickers at very bottom
LOWER_THIRD = "lower_third"     # lower 1/3 of frame
UPPER_THIRD = "upper_third"     # upper 1/3 (titles, logos)


class TextDetector:
    """Detect text regions in video frames for crop safety."""

    def __init__(self, analysis_width: int = 640):
        self.analysis_width = analysis_width
        self._ocr = None
        self._failed = False

    def _get_ocr(self):
        """Lazy-load EasyOCR."""
        if self._failed:
            return None
        if self._ocr is not None:
            return self._ocr
        try:
            import easyocr
            self._ocr = easyocr.Reader(['en'], gpu=False)
            return self._ocr
        except Exception:
            self._failed = True
            return None

    def detect_text_regions(self, frame_bgr) -> List[Dict]:
        """Detect text regions in frame.

        Returns list of:
            {
                "bbox": (x0, y0, x1, y1),
                "text": str,
                "confidence": float,
                "region": "subtitle" | "ticker" | "lower_third" | "upper_third" | "other",
            }
        """
        h, w = frame_bgr.shape[:2]
        regions = []

        # Try EasyOCR first
        ocr = self._get_ocr()
        if ocr is not None:
            try:
                # Downscale for speed
                scale = 1.0
                if w > self.analysis_width:
                    scale = self.analysis_width / w
                    small = cv2.resize(frame_bgr, (self.analysis_width, int(h * scale)))
                else:
                    small = frame_bgr

                results = ocr.readtext(small)

                for (bbox, text, conf) in results:
                    if conf < 0.3:
                        continue
                    # Scale back to original coords
                    x0 = int(bbox[0][0] / scale)
                    y0 = int(bbox[0][1] / scale)
                    x1 = int(bbox[2][0] / scale)
                    y1 = int(bbox[2][1] / scale)

                    # Classify region
                    region = self._classify_region(y0, y1, h)

                    regions.append({
                        "bbox": (x0, y0, x1, y1),
                        "text": text,
                        "confidence": float(conf),
                        "region": region,
                    })
            except Exception:
                pass

        # Fallback: morphological text detection (no OCR)
        if not regions:
            regions = self._morphological_detect(frame_bgr)

        return regions

    def _classify_region(self, y0: int, y1: int, frame_h: int) -> str:
        """Classify text position in frame."""
        cy = (y0 + y1) / 2
        ratio = cy / frame_h

        if ratio > 0.85:
            return "ticker"
        elif ratio > 0.75:
            return "subtitle"
        elif ratio > 0.5:
            return "lower_third"
        elif ratio < 0.25:
            return "upper_third"
        return "other"

    def _morphological_detect(self, frame_bgr) -> List[Dict]:
        """Fallback text detection using morphological operations.

        Detects high-contrast text-like regions without OCR.
        """
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape

        # Focus on bottom 30% (subtitles/tickers) and top 15% (titles)
        regions = []

        for (y_start, y_end, region_name) in [
            (int(h * 0.7), h, "subtitle"),
            (0, int(h * 0.15), "upper_third"),
        ]:
            roi = gray[y_start:y_end, :]
            if roi.size == 0:
                continue

            # Threshold to find bright text on dark background
            _, thresh = cv2.threshold(roi, 200, 255, cv2.THRESH_BINARY)

            # Morphological operations to connect text characters
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (15, 5))
            dilated = cv2.dilate(thresh, kernel, iterations=2)

            # Find contours
            contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL,
                                           cv2.CHAIN_APPROX_SIMPLE)

            for cnt in contours:
                x, y, cw, ch = cv2.boundingRect(cnt)
                area = cw * ch
                if area < 500 or cw < 50 or ch < 10:
                    continue

                # Check aspect ratio (text is usually wide and short)
                aspect = cw / max(ch, 1)
                if aspect < 2.0:
                    continue

                regions.append({
                    "bbox": (x, y + y_start, x + cw, y + y_start + ch),
                    "text": "",  # no OCR in morphological mode
                    "confidence": 0.5,
                    "region": region_name,
                })

        return regions


def adjust_crop_for_text(crop_x: int, crop_y: int,
                         crop_w: int, crop_h: int,
                         text_regions: List[Dict],
                         frame_w: int, frame_h: int,
                         safety_margin: int = 20) -> Tuple[int, int, int, int]:
    """Adjust crop window to avoid cutting off important text.

    Rules:
      - Subtitles (bottom 20%): if crop cuts through subtitle region,
        expand crop downward to include it
      - Tickers (very bottom): if crop cuts through ticker, expand down
      - Upper third titles: if crop cuts through, expand up

    Returns:
        (new_x, new_y, new_w, new_h) — adjusted crop window
    """
    if not text_regions:
        return crop_x, crop_y, crop_w, crop_h

    new_x, new_y = crop_x, crop_y
    new_w, new_h = crop_w, crop_h

    crop_bottom = crop_y + crop_h
    crop_top = crop_y

    for tr in text_regions:
        bbox = tr["bbox"]
        region = tr["region"]
        text_y0, text_y1 = bbox[1], bbox[3]

        if region in ("subtitle", "ticker"):
            # Text is at bottom — if crop cuts through it, expand down
            if crop_bottom > text_y0 and crop_bottom < text_y1:
                # Crop bottom is inside text region — expand to include text
                expand = text_y1 - crop_bottom + safety_margin
                new_h += expand
                # Shift up to maintain aspect if needed
            elif crop_bottom > text_y1:
                # Crop already covers the text — good
                pass
            elif crop_y < text_y0:
                # Crop is entirely above text — no issue
                pass

        elif region == "upper_third":
            # Text is at top — if crop cuts through it, expand up
            if crop_top < text_y1 and crop_top > text_y0:
                expand = text_y1 - crop_top + safety_margin
                new_y = max(0, new_y - expand)
                new_h += expand

    # Clamp to frame
    new_y = max(0, min(new_y, frame_h - new_h))
    new_x = max(0, min(new_x, frame_w - new_w))
    if new_y + new_h > frame_h:
        new_h = frame_h - new_y
    if new_x + new_w > frame_w:
        new_w = frame_w - new_x

    return new_x, new_y, new_w, new_h


def has_text_in_crop(frame_bgr, crop_x: int, crop_y: int,
                     crop_w: int, crop_h: int,
                     detector: TextDetector = None) -> bool:
    """Quick check if there's text inside the crop region."""
    if detector is None:
        detector = TextDetector()

    # Extract crop region
    h, w = frame_bgr.shape[:2]
    x0 = max(0, crop_x)
    y0 = max(0, crop_y)
    x1 = min(w, crop_x + crop_w)
    y1 = min(h, crop_y + crop_h)

    crop_frame = frame_bgr[y0:y1, x0:x1]
    if crop_frame.size == 0:
        return False

    regions = detector.detect_text_regions(crop_frame)
    return len(regions) > 0
