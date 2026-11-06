"""Scene type classification for adaptive cropping strategy selection.

Detects scene context (talking-head, whiteboard, 2-person, crowd, etc.)
and recommends appropriate crop strategy (TRACK, FIT, SPLIT, etc.).
"""
import numpy as np
import cv2
from typing import Tuple
from enum import Enum


class SceneType(Enum):
    """Enumeration of detectable scene types."""
    TALKING_HEAD = "talking_head"           # Single face, neutral background
    TALKING_HEAD_BRIGHT = "talking_head_bright"  # Single face, bright (studio/text)
    WHITEBOARD = "whiteboard"               # Presenter + whiteboard/screen
    TWO_PERSON_CLOSE = "2_person_close"     # Shoulder-to-shoulder (duo)
    TWO_PERSON_SEPARATE = "2_person_separate"  # Debate/interview (distant)
    CROWD = "crowd"                         # 3+ people visible
    WIDE_SHOT_STATIC = "wide_shot_static"   # Landscape, no clear subject
    WIDE_SHOT_MOVING = "wide_shot_moving"   # B-roll, camera panning


class SceneClassifier:
    """Classify video scene type based on visual features."""
    
    def __init__(self, brightness_threshold: float = 180.0,
                 edge_density_threshold: float = 0.04):
        """
        brightness_threshold: above this = "bright" (studio, text heavy)
        edge_density_threshold: above this = high detail (whiteboard/text)
        """
        self.brightness_threshold = brightness_threshold
        self.edge_density_threshold = edge_density_threshold
    
    def extract_features(self, frame_bgr) -> dict:
        """Extract visual features from frame."""
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        
        # Brightness
        brightness = np.mean(gray)
        
        # Edge density (Canny edge detection)
        edges = cv2.Canny(gray, 60, 160)
        edge_density = np.sum(edges > 0) / edges.size
        
        # Motion (for static vs. moving detection, rough estimate)
        # In practice, compute optical flow; here use Laplacian variance (focus/blur)
        laplacian = cv2.Laplacian(gray, cv2.CV_64F)
        motion_score = np.var(laplacian)
        
        return {
            "brightness": brightness,
            "edge_density": edge_density,
            "motion_score": motion_score,
        }
    
    def classify(self, frame_bgr, face_count: int, 
                 face_boxes: list = None, motion: float = 0.0) -> SceneType:
        """
        Classify scene type.
        
        face_count: number of detected faces
        face_boxes: list of (cx, cy, w, h) face boxes (optional, for distance calc)
        motion: estimated motion score (0-1 scale, for wide_shot detection)
        
        Returns: SceneType enum
        """
        features = self.extract_features(frame_bgr)
        brightness = features["brightness"]
        edge_density = features["edge_density"]
        
        # Decision tree
        if face_count >= 3:
            return SceneType.CROWD
        
        elif face_count == 2:
            if face_boxes and len(face_boxes) >= 2:
                # Distance between two faces
                cx1, cy1, w1, h1 = face_boxes[0]
                cx2, cy2, w2, h2 = face_boxes[1]
                dist = ((cx1 - cx2) ** 2 + (cy1 - cy2) ** 2) ** 0.5
                frame_w = max(frame_bgr.shape[1], 1920)
                
                if dist < 0.2 * frame_w:
                    return SceneType.TWO_PERSON_CLOSE
                else:
                    return SceneType.TWO_PERSON_SEPARATE
            else:
                return SceneType.TWO_PERSON_SEPARATE  # Default
        
        elif face_count == 1:
            # Single face: talking-head vs. bright studio
            if brightness > self.brightness_threshold:
                return SceneType.TALKING_HEAD_BRIGHT
            else:
                # Check for whiteboard (high edge density)
                if edge_density > self.edge_density_threshold:
                    return SceneType.WHITEBOARD
                else:
                    return SceneType.TALKING_HEAD
        
        else:  # face_count == 0
            # No clear face: wide shot or B-roll
            if motion > 0.5:
                return SceneType.WIDE_SHOT_MOVING
            else:
                return SceneType.WIDE_SHOT_STATIC
    
    @staticmethod
    def get_recommended_strategy(scene_type: SceneType) -> str:
        """Map scene type to crop strategy."""
        mapping = {
            SceneType.TALKING_HEAD: "track",          # Follow face
            SceneType.TALKING_HEAD_BRIGHT: "track",   # Still follow face
            SceneType.WHITEBOARD: "fit",              # Fit full content
            SceneType.TWO_PERSON_CLOSE: "split",      # Stack vertically
            SceneType.TWO_PERSON_SEPARATE: "alternate",  # Hard cut between
            SceneType.CROWD: "general",               # Blur background
            SceneType.WIDE_SHOT_STATIC: "general",    # Blur background
            SceneType.WIDE_SHOT_MOVING: "center",     # Safe center crop
        }
        return mapping.get(scene_type, "general")


def classify_scene_type(frame_bgr, face_count: int = 0, 
                       face_boxes: list = None) -> Tuple[SceneType, str]:
    """
    Convenience function: classify scene and return (type, strategy).
    
    Args:
        frame_bgr: OpenCV frame (BGR)
        face_count: number of detected faces
        face_boxes: list of face boxes [(cx, cy, w, h), ...]
    
    Returns:
        (SceneType, strategy_string)
    """
    classifier = SceneClassifier()
    scene_type = classifier.classify(frame_bgr, face_count, face_boxes)
    strategy = SceneClassifier.get_recommended_strategy(scene_type)
    return scene_type, strategy
