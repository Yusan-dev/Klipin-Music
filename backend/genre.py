"""Genre-aware reframe classifier.

Uses Gemini to classify a video's genre from sampled frames + transcript
snippets, then maps to a reframe strategy tuned for that genre.

Genres:
  podcast    — talking head / interview / panel (default)
  sports     — action footage: wider crop, keep field/court visible
  gaming     — screen recording / gameplay: preserve HUD, center crop
  vlog       — personal vlog: face track with extra headroom
  tutorial   — screencast / how-to: full-width, preserve screen
  news       — broadcast / news: handle tickers, wider crop
  music      — music video / performance: artistic framing
  documentary — narrated footage: wider context

The genre determines TWO reframe parameters:
  1. crop_style — how tight the crop window is (tight / normal / wide)
  2. track_mode — how aggressively face tracking follows (aggressive / relaxed / off)
"""
import json
import os

# ─── genre → reframe parameter map ─────────────────────────────────────

GENRE_PARAMS = {
    "podcast": {
        "crop_style": "tight",
        "track_mode": "aggressive",
        "description": "Talking head / interview — tight face crop, aggressive tracking",
    },
    "sports": {
        "crop_style": "wide",
        "track_mode": "off",
        "description": "Sports / action — wide crop to keep action visible, no face tracking",
    },
    "gaming": {
        "crop_style": "wide",
        "track_mode": "off",
        "description": "Gaming / screencast — preserve HUD, center crop, no face tracking",
    },
    "vlog": {
        "crop_style": "normal",
        "track_mode": "relaxed",
        "description": "Vlog — moderate crop with extra headroom, relaxed face tracking",
    },
    "tutorial": {
        "crop_style": "wide",
        "track_mode": "off",
        "description": "Tutorial / screencast — full-width, preserve screen content",
    },
    "news": {
        "crop_style": "normal",
        "track_mode": "relaxed",
        "description": "News / broadcast — moderate crop, handle tickers/graphics",
    },
    "music": {
        "crop_style": "normal",
        "track_mode": "relaxed",
        "description": "Music / performance — artistic framing, moderate tracking",
    },
    "documentary": {
        "crop_style": "wide",
        "track_mode": "relaxed",
        "description": "Documentary — wider context, relaxed tracking",
    },
}

# Default when genre detection is unavailable
DEFAULT_GENRE = "podcast"


def _extract_json(text: str) -> dict:
    """Pull a JSON object out of a model response (tolerant of fences)."""
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1].lstrip("json").strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    start, end = t.find("{"), t.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(t[start:end + 1])
        except json.JSONDecodeError:
            pass
    return {}


def detect_genre(video_path: str, work_dir: str, log=None,
                 transcript_snippet: str = "") -> dict:
    """Classify video genre from sampled frames + transcript.

    Returns {"genre": str, "reason": str, "params": dict}.
    Never raises — falls back to 'podcast' on any failure.
    """
    default = {
        "genre": DEFAULT_GENRE,
        "reason": "detection unavailable",
        "params": GENRE_PARAMS[DEFAULT_GENRE],
    }

    try:
        from google import genai
        key = os.environ.get("GEMINI_API_KEY", "")
        if not key:
            log and log("⚠️ Genre detection: GEMINI_API_KEY not set")
            return default
    except ImportError:
        return default

    # Sample frames (reuse analyze.py's sampler)
    try:
        from analyze import sample_frames
        frames, _duration = sample_frames(video_path, work_dir, n=6, log=log)
    except Exception as e:  # noqa: BLE001
        log and log(f"⚠️ Genre detection frame sampling failed: {e}")
        return default

    client = genai.Client(api_key=key)
    model = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")

    contents = [(
        "You are a video genre classifier for a video clipper tool.\n"
        "Look at these sampled frames from a video and classify its genre.\n"
        "\n"
        "Genres:\n"
        "- podcast: talking head, interview, panel discussion, two-person chat\n"
        "- sports: football, basketball, MMA, racing, any athletic competition\n"
        "- gaming: video game footage, screen recording, esports, Let's Play\n"
        "- vlog: personal vlog, travel, daily life, 'day in the life'\n"
        "- tutorial: how-to, cooking, DIY, screencast, software demo\n"
        "- news: news broadcast, press conference, political coverage\n"
        "- music: music video, concert, performance, dance\n"
        "- documentary: narrated story, nature, true crime, explainer\n"
        "\n"
        "Return STRICT JSON: {\"genre\": \"one of the above\", "
        "\"reason\": \"short reason (max 60 chars)\"}"
    )]

    # Add transcript snippet for better classification
    if transcript_snippet:
        contents.append(f"TRANSCRIPT SNIPPET (first ~500 chars):\n{transcript_snippet[:500]}")

    # Encode frames as Gemini inline images
    try:
        from google.genai import types
        for f in frames:
            try:
                with open(f, "rb") as fh:
                    data = fh.read()
                contents.append(types.Part(
                    inline_data=types.Blob(
                        mime_type="image/jpeg", data=data)))
            except OSError:
                continue
    except ImportError:
        pass

    log and log(f"🧠 Genre detection ({model})...")
    try:
        resp = client.models.generate_content(
            model=model, contents=contents,
            config={"response_mime_type": "application/json"},
        )
        data = _extract_json(resp.text)
    except Exception as e:  # noqa: BLE001
        log and log(f"⚠️ Genre detection failed: {e}")
        return default

    genre = str(data.get("genre", DEFAULT_GENRE)).strip().lower()
    reason = data.get("reason", "")

    if genre not in GENRE_PARAMS:
        log and log(f"⚠️ Unknown genre '{genre}' — falling back to podcast")
        genre = DEFAULT_GENRE

    params = GENRE_PARAMS[genre]
    log and log(f"🎬 Genre: {genre} — {params['description']}")
    return {"genre": genre, "reason": reason, "params": params}


def crop_style_height_ratio(genre: str) -> float:
    """Return the crop window height multiplier for a genre's crop_style.

    tight  = 0.75 — face fills frame (podcast default)
    normal = 0.85 — moderate headroom (vlog, news, music)
    wide   = 1.0  — full source height, keeps all context (sports, gaming, tutorial)
    """
    style = GENRE_PARAMS.get(genre, GENRE_PARAMS[DEFAULT_GENRE]).get(
        "crop_style", "tight")
    return {"tight": 0.75, "normal": 0.85, "wide": 1.0}.get(style, 0.85)


def track_mode_from_genre(genre: str) -> str:
    """Map genre to track_mode: aggressive / relaxed / off."""
    return GENRE_PARAMS.get(genre, GENRE_PARAMS[DEFAULT_GENRE]).get(
        "track_mode", "aggressive")
