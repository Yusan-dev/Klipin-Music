"""Video Editor with Gemini File API (ported from OpenShorts).

Uploads video to Gemini, asks for edit decisions, builds deterministic FFmpeg filters.
This gives TRUE video understanding - not just 12 frames but the whole video.
"""
import os
import json
import time
from typing import List, Optional
from google import genai
from google.genai import types
from pydantic import BaseModel

from edit_builder import build_filter_string


class EditDecision(BaseModel):
    type: str
    start: float
    end: float
    strength: float = 0.0
    reason: str = ""


class EditPlan(BaseModel):
    edits: List[EditDecision]


class VideoEditor:
    def __init__(self, api_key: str):
        self.client = genai.Client(api_key=api_key)
        self.model_name = (
            os.environ.get("GEMINI_MODEL_EDITOR")
            or os.environ.get("GEMINI_MODEL")
            or "gemini-3.5-flash"
        )

    def upload_video(self, video_path: str):
        """Uploads video to Gemini File API."""
        print(f"📤 Uploading {video_path} to Gemini...")

        # Ensure we are passing a path that exists
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"Video file not found: {video_path}")

        # Using 'file' keyword instead of 'path'
        try:
            file_upload = self.client.files.upload(file=video_path)
        except Exception as e:
            print(f"❌ Gemini Upload Error: {e}")
            raise e

        # Wait for processing
        print("⏳ Waiting for video processing by Gemini...")
        deadline = time.time() + 120
        while True:
            file_info = self.client.files.get(name=file_upload.name)
            state = getattr(file_info, "state", file_info)
            state_name = str(getattr(state, "name", state)).upper()
            if state_name == "ACTIVE":
                print("✅ Video processed and ready.")
                return file_upload
            if state_name == "FAILED":
                raise Exception("Video processing failed by Gemini.")
            if time.time() > deadline:
                raise TimeoutError("Gemini file processing timed out after 120s.")
            time.sleep(2)

    def get_ffmpeg_filter(self, video_file_obj, duration: float, fps: int = 30,
                          width: Optional[int] = None, height: Optional[int] = None,
                          transcript: Optional[List] = None, has_captions: bool = False):
        """Asks Gemini for an edit decision list, then builds the FFmpeg filter
        deterministically (edit_builder) so syntax and zoom limits are always safe.
        """
        if width is None or height is None:
            # Keep prompt usable even if caller didn't pass dimensions.
            width, height = 1080, 1920

        transcript_text = json.dumps(transcript) if transcript else "Not available."

        caption_rule = ""
        if has_captions:
            caption_rule = (
                "\n        CRITICAL: This video already has burned-in captions and/or a hook text. "
                "Any zoom would crop or shift them off screen. Do NOT use zoom_in, punch_in or zoom_pulse — "
                "only color_pop, bw_moment, flash and vignette are allowed.\n"
            )

        prompt = f"""
        You are a viral short-form video editor (TikTok / Reels / Shorts). You receive a video and its transcript.
        Decide WHERE a small set of tasteful, high-impact effects belongs. You do NOT write FFmpeg —
        you return an edit decision list, and a deterministic renderer applies it safely.

        Video duration: {duration:.1f} seconds. Resolution: {width}x{height}.

        Available effect types:
        - "zoom_in": slow push-in across the segment. Builds tension or focus on a key statement. strength 0.06-0.15, segment 1.5-6s.
        - "punch_in": instant tighter framing for the whole segment. Emphasis, punchlines, "listen to this" moments. strength 0.06-0.15, segment 0.8-5s.
        - "zoom_pulse": quick in-and-out pulse peaking mid-segment. Beat drops, single impactful words. strength 0.04-0.10, segment 0.4-1.2s.
        - "color_pop": richer saturation/contrast. Energy, excitement, product/visual highlights. strength 0.2-1.0.
        - "bw_moment": black & white. Drama, serious quotes, flashbacks.
        - "flash": brief bright flash at segment start. Hard-cut energy, reveals. Use at most 2.
        - "vignette": darkened edges. Focus, intimacy, storytelling moments.

        Rules:
        1. Match effects to the CONTENT: place them on the exact words or moments they emphasize, using the transcript timestamps.
        2. Less is more: 2-6 edits per 30 seconds of video. A random effect is worse than no effect.
        3. Never overlap two zoom-type edits (zoom_in/punch_in/zoom_pulse).
        4. Calm, serious delivery => few or no motion effects. High-energy content => more punch.
        {caption_rule}
        TRANSCRIPT (with timestamps, the context of what is being said):
        {transcript_text}

        Return JSON only:
        {{"edits": [{{"type": "punch_in", "start": 3.2, "end": 5.4, "strength": 0.1, "reason": "punchline"}}]}}
        If no effects genuinely improve the video, return {{"edits": []}}.
        """

        print("🤖 Asking Gemini for an edit decision list...")
        # Low media resolution: Gemini samples at 1 FPS and normalizes frames to
        # fixed token sizes, so "low" (~70 tokens/frame) cuts video-input cost
        # without hurting motion/scene understanding. Fall back to defaults if
        # the SDK or model rejects the option.
        try:
            config = types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=EditPlan,
                media_resolution=types.MediaResolution.MEDIA_RESOLUTION_LOW,
            )
        except Exception:
            config = types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=EditPlan,
            )
        try:
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=[video_file_obj, prompt],
                config=config,
            )
        except Exception as e:
            if getattr(config, "media_resolution", None) is None:
                raise
            print(f"⚠️ media_resolution=low rejected ({e}); retrying with defaults...")
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=[video_file_obj, prompt],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=EditPlan,
                ),
            )

        raw_edits = self._extract_edits(response)
        if raw_edits is None:
            return None

        filter_string, applied = build_filter_string(
            raw_edits, duration=duration, fps=fps, width=width, height=height,
            has_captions=has_captions,
        )
        if not filter_string:
            print("ℹ️ Gemini suggested no (valid) edits — keeping the clip untouched.")
            return {"filter_string": None, "edits": []}
        print(f"🎯 Applying {len(applied)} edits: " + ", ".join(f"{e['type']}@{e['start']:.1f}s" for e in applied))
        return {"filter_string": filter_string, "edits": applied}

    @staticmethod
    def _extract_edits(response):
        """Pull the edit list out of a Gemini response (parsed schema preferred,
        raw-JSON fallback). Returns a list of dicts, or None on hard failure.
        """
        parsed = getattr(response, "parsed", None)
        if parsed is not None:
            try:
                return [edit.model_dump() for edit in parsed.edits]
            except Exception:
                pass

        text = (getattr(response, "text", None) or "").strip()
        print(f"🔍 DEBUG: Gemini Raw Response:\n{text}")
        if text.startswith("```json"):
            text = text[7:]
        elif text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
        start_idx = text.find('{')
        end_idx = text.rfind('}')
        if start_idx != -1 and end_idx != -1:
            text = text[start_idx:end_idx + 1]
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            print(f"❌ Failed to parse edit plan JSON: {text[:500]}")
            return None
        edits = data.get("edits") if isinstance(data, dict) else None
        return edits if isinstance(edits, list) else []