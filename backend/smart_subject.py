"""Multi-subject intelligent scoring and selection.

Determines which subject(s) should be in the crop frame based on:
  - Mouth motion (who is speaking?)
  - Face size (who is prominent?)
  - Centering bias (prefer already-centered faces)
  - Distance grouping (are they related? conversational pair?)
  
Output: Subject scores per-frame + recommended crop strategy.
"""
import numpy as np
import cv2
from typing import Dict, List, Tuple, Optional
from detector import face_boxes


class MouthMotionDetector:
    """Frame-to-frame mouth activity measurement for multiple subjects."""
    
    def __init__(self, analysis_width: int = 480, window_seconds: float = 0.4):
        """
        analysis_width: downscale to this width for analysis (speed)
        window_seconds: aggregate mouth motion over this many seconds
        """
        self.analysis_width = analysis_width
        self.window_seconds = window_seconds
        self.prev_frames = {}  # subject_id -> prev_gray_roi
    
    def mouth_region(self, box: Tuple, frame_w: int, frame_h: int, 
                     scale: float = 1.0) -> Tuple[int, int, int, int]:
        """
        Extract mouth region coords from face box.
        box: (cx, cy, w, h) in center form
        Returns: (x0, y0, x1, y1) in pixel coords
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
    
    def compute_frame_activity(self, frame_bgr, tracked_subjects: Dict,
                               frame_w: int, frame_h: int) -> Dict[int, float]:
        """
        Compute mouth motion score for each tracked subject in this frame.
        
        tracked_subjects: {subject_id: (cx, cy), ...}
        Returns: {subject_id: motion_score (0-255 scale), ...}
        """
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        scale = self.analysis_width / frame_w

        if frame_w > self.analysis_width:
            small_h = int(frame_h * scale)
            gray = cv2.resize(gray, (self.analysis_width, small_h))
            # subject coords are in ORIGINAL frame space; scale them down to
            # match the resized gray map so the mouth ROI lands on the actual face
            scaled_subjects = {sid: (cx * scale, cy * scale)
                              for sid, (cx, cy) in tracked_subjects.items()}
            subj_w, subj_h = self.analysis_width, small_h
        else:
            scaled_subjects = tracked_subjects
            subj_w, subj_h = frame_w, frame_h

        activity = {}
        for subject_id, (cx, cy) in scaled_subjects.items():
            x0, y0, x1, y1 = self.mouth_region((cx, cy, 100, 100),
                                               subj_w, subj_h,
                                               scale=1.0)
            
            roi = gray[y0:y1, x0:x1]
            
            if roi.size == 0:
                activity[subject_id] = 0.0
                continue
            
            if subject_id in self.prev_frames:
                prev_roi = self.prev_frames[subject_id]
                if prev_roi.shape == roi.shape:
                    diff = cv2.absdiff(roi, prev_roi)
                    score = float(np.mean(diff))
                else:
                    score = 0.0
            else:
                score = 0.0
            
            self.prev_frames[subject_id] = roi.copy()
            activity[subject_id] = score
        
        return activity


class SubjectScorer:
    """Score importance of each subject (who to crop around?)."""
    
    def __init__(self, mouth_detector: Optional[MouthMotionDetector] = None):
        self.mouth_detector = mouth_detector or MouthMotionDetector()
        self.mouth_history = {}  # subject_id -> [scores per frame]
    
    def score_subjects(self, frame_bgr, tracked_subjects: Dict, 
                      w: int, h: int,
                      mouth_scores: Optional[Dict] = None) -> Dict[int, float]:
        """
        Score each subject 0-1 by importance.
        
        tracked_subjects: {id: (cx, cy), ...}
        mouth_scores: {id: motion_score (0-255)}, or None to compute
        
        Returns: {id: importance_score (0-1), ...}
        
        Scoring formula:
            score = 0.4 * mouth_activity + 0.3 * face_size + 0.2 * centering + 0.1 * is_centered
        """
        if not tracked_subjects:
            return {}
        
        # 1. Compute mouth motion if not provided
        if mouth_scores is None:
            mouth_scores = self.mouth_detector.compute_frame_activity(
                frame_bgr, tracked_subjects, w, h)
        
        # Normalize mouth scores to 0-1
        mouth_values = list(mouth_scores.values())
        if mouth_values:
            mouth_max = max(mouth_values) if max(mouth_values) > 0 else 1.0
            mouth_norm = {sid: score / mouth_max for sid, score in mouth_scores.items()}
        else:
            mouth_norm = {}
        
        # 2. Face size scoring (larger = more prominent)
        # Estimate size from tracking stability (proxy: recent detection consistency)
        # For now, use rough heuristic based on position range
        face_size_scores = {}
        for sid in tracked_subjects:
            # Rough assumption: well-tracked subjects are mid-frame
            # (crude proxy for face size; better to store size from detector)
            face_size_scores[sid] = 0.7  # Default moderate size
        
        # 3. Centering bias (prefer faces already centered)
        center_x, center_y = w / 2, h / 2
        centering_scores = {}
        for sid, (cx, cy) in tracked_subjects.items():
            dist_from_center = ((cx - center_x) ** 2 + (cy - center_y) ** 2) ** 0.5
            max_dist = ((w/2) ** 2 + (h/2) ** 2) ** 0.5
            # Closer to center = higher score (inverted distance)
            centering_scores[sid] = 1.0 - (dist_from_center / max_dist)
        
        # 4. Combine scores
        scores = {}
        for sid in tracked_subjects:
            score = (
                0.4 * mouth_norm.get(sid, 0.0) +
                0.3 * face_size_scores.get(sid, 0.7) +
                0.2 * centering_scores.get(sid, 0.5) +
                0.1 * (1.0 if centering_scores.get(sid, 0.0) > 0.7 else 0.0)
            )
            scores[sid] = min(1.0, max(0.0, score))
        
        return scores


def adaptive_crop_window(tracked_subjects: Dict, subject_scores: Dict,
                        w: int, h: int, tw: int, th: int,
                        strategy: str = "center_dominant") -> Tuple[int, int, int, int]:
    """
    Generate crop window (x, y, ww, wh) based on tracked subjects and scores.
    
    Strategies:
      - "center_dominant": Crop around single highest-scored subject
      - "group_nearby": Include dominant + nearby subjects in bounding box
      - "expand": Start dominant, expand if motion detected
    
    Returns: (x, y, crop_width, crop_height) in source coords
    """
    from video import _aspect_window
    
    if not tracked_subjects:
        # Fallback: center crop
        ww, wh = _aspect_window(w, h, tw, th)
        x = max(0, min((w - ww) // 2, w - ww))
        y = max(0, min((h - wh) // 2, h - wh))
        return x, y, ww, wh
    
    # Find dominant subject
    dominant_id = max(subject_scores.keys(), key=lambda sid: subject_scores.get(sid, 0))
    # adaptive_crop_window: handle KeyError for ID mismatch
    if dominant_id not in tracked_subjects:
        # Fallback to first available or center
        if tracked_subjects:
            dominant_id = list(tracked_subjects.keys())[0]
        else:
            return (0, 0, w, h) # Should not happen
    dominant_pos = tracked_subjects[dominant_id]
    
    if strategy == "center_dominant":
        # Crop around single subject
        ww, wh = _aspect_window(w, h, tw, th)
        cx, cy = dominant_pos
        x = max(0, min(int(cx - ww / 2), w - ww))
        y = max(0, min(int(cy - wh / 2), h - wh))
        return x, y, ww, wh
    
    elif strategy == "group_nearby":
        # Include all subjects within ~20% frame width distance
        nearby_threshold = 0.20 * w
        nearby = [dominant_id]
        
        for sid, (pos_x, pos_y) in tracked_subjects.items():
            if sid == dominant_id:
                continue
            dx = abs(pos_x - dominant_pos[0])
            dy = abs(pos_y - dominant_pos[1])
            dist = (dx ** 2 + dy ** 2) ** 0.5
            
            if dist < nearby_threshold:
                nearby.append(sid)
        
        # Bounding box of all nearby
        xs = [tracked_subjects[sid][0] for sid in nearby]
        ys = [tracked_subjects[sid][1] for sid in nearby]
        
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        
        # Center of bounding box with margin
        center_x = (min_x + max_x) / 2
        center_y = (min_y + max_y) / 2
        margin_x = max(50, (max_x - min_x) * 0.15)
        margin_y = max(50, (max_y - min_y) * 0.15)
        
        ww, wh = _aspect_window(w, h, tw, th)
        x = max(0, min(int(center_x - ww / 2), w - ww))
        y = max(0, min(int(center_y - wh / 2), h - wh))
        return x, y, ww, wh
    
    else:  # expand
        # Start with dominant, could expand based on motion
        ww, wh = _aspect_window(w, h, tw, th)
        cx, cy = dominant_pos
        x = max(0, min(int(cx - ww / 2), w - ww))
        y = max(0, min(int(cy - wh / 2), h - wh))
        return x, y, ww, wh


def group_nearby_subjects(tracked_subjects: Dict, threshold_px: float = 0.25) -> List[List[int]]:
    """
    Cluster subjects into conversational groups (e.g., two-person interview).
    
    threshold_px: fraction of frame width
    
    Returns: [[id1, id2], [id3], ...] (groups of subject IDs)
    """
    if not tracked_subjects:
        return []
    
    positions = {sid: pos for sid, pos in tracked_subjects.items()}
    # Rough frame width estimate from position range
    xs = [pos[0] for pos in positions.values()]
    frame_w_estimate = max(xs) - min(xs) if xs else 1920
    
    threshold = threshold_px * frame_w_estimate
    
    # Simple clustering: greedy grouping by distance
    groups = []
    ungrouped = set(positions.keys())
    
    while ungrouped:
        seed = ungrouped.pop()
        group = [seed]
        
        for other_id in list(ungrouped):
            dist = ((positions[seed][0] - positions[other_id][0]) ** 2 +
                   (positions[seed][1] - positions[other_id][1]) ** 2) ** 0.5
            
            if dist < threshold:
                group.append(other_id)
                ungrouped.remove(other_id)
        
        groups.append(group)
    
    return groups
