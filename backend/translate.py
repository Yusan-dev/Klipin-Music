"""Subtitle translation — Gemini batch-translates transcript segments.

Ported conceptually from QuClippers `translate.py`, but instead of the paid
ElevenLabs Dubbing API we use Gemini (already in the stack): one call
translates all segments at once, aligned by index. Cost stays tiny
(≈ a few thousand tokens) and fits the "cheap compute" architecture.
"""
import json
import os

# Gemini can answer in any language natively, but asking it to name the
# target keeps output consistent for the subtitle stylesheet.
LANG_NAMES = {
    "id": "Indonesian", "en": "English", "ms": "Malay",
    "zh": "Chinese", "ja": "Japanese", "ko": "Korean",
    "es": "Spanish", "fr": "French", "de": "German",
    "ar": "Arabic", "hi": "Hindi", "th": "Thai", "vi": "Vietnamese",
    "pt": "Portuguese", "ru": "Russian", "tr": "Turkish",
}

PROMPT = """You are a subtitle translator. Translate each transcript segment
below into {lang}. Rules:
- Keep it natural and SHORT — subtitles must be readable at a glance.
- Keep proper nouns, names, brands, and numbers unchanged.
- Preserve the tone (casual stays casual, emotional stays emotional).
- Return STRICT JSON only: an array of strings, one per input segment,
  same order and same count. No markdown, no extra keys."""


def translate_segments(transcript: dict, target: str, log=None) -> dict:
    """Return a copy of `transcript` with each segment's `text` translated.

    Falls back to the original text for any segment that fails to map,
    so a bad translation never breaks the whole job.
    """
    from google import genai

    lang = LANG_NAMES.get(target, target)
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        raise RuntimeError("GEMINI_API_KEY not set (translate needs it)")

    segs = transcript.get("segments") or []
    if not segs:
        return transcript

    lines = "\n".join(f"{i}\t{s.get('text', '').strip()}" for i, s in enumerate(segs[:400]))
    client = genai.Client(api_key=key)
    model = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")
    log and log(f"🌐 Translating {len(segs[:400])} segments to {lang} ({model})...")

    resp = client.models.generate_content(
        model=model,
        contents=[PROMPT.format(lang=lang), f"SEGMENTS:\n{lines}"],
    )
    text = (resp.text or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        translated = json.loads(text)
    except json.JSONDecodeError:
        log and log("⚠️ Translation response wasn't valid JSON — keeping original text")
        return transcript
    if not isinstance(translated, list):
        return transcript

    out = dict(transcript)
    new_segments = []
    for i, s in enumerate(segs):
        tr = translated[i].strip() if i < len(translated) and isinstance(translated[i], str) and translated[i].strip() else ""
        if not tr:
            # Fall back to original text so a bad slot never empties a caption.
            tr = s.get("text", "")
        ns = {**s, "text": tr}
        # Karaoke captions are built from the segment's `words`, not its text.
        # A translation is never word-time-aligned to the audio, so we rebuild
        # the word list from the translated text with even distribution over the
        # segment span — this guarantees the burned caption text exactly equals
        # the translation (instead of leaking source-language words).
        tr_tokens = tr.split()
        if tr_tokens:
            seg_start = float(s.get("start", 0) or 0)
            seg_end = float(s.get("end", seg_start) or seg_start)
            span = max(0.05, seg_end - seg_start)
            step = span / len(tr_tokens)
            ns["words"] = [
                {"word": w, "start": round(seg_start + j * step, 3),
                 "end": round(seg_start + (j + 1) * step, 3)}
                for j, w in enumerate(tr_tokens)
            ]
        new_segments.append(ns)
    out["segments"] = new_segments
    log and log(f"🌐 Translated {len(out['segments'])} segments → {lang}")
    return out
