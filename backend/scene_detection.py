"""Scene detection for Klipin (module parity with the original engine).

Implementation lives in reframe.py — this module keeps the scene-aware API
(`detect_scenes`) importable under the same name the QuClippers codebase used.
"""
from reframe import SCENE_THRESHOLD, detect_scenes, scene_strategies

__all__ = ["detect_scenes", "scene_strategies", "SCENE_THRESHOLD"]
