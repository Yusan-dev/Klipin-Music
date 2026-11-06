"""Repurpose-to-text — turn a video's transcript into written content.

Ported conceptually from QuClippers `RepurposeModal` (X thread, LinkedIn,
blog, Instagram long caption), but free: one Gemini text call per format
(Gemini is already in the stack), cost ≈ a few thousand tokens.

Formats:
  x_thread   — a thread of short punchy posts (with numbers)
  linkedin   — a professional long-form post
  blog       — a blog article with headings
  ig_long    — a long Instagram caption with emoji + hashtags
"""

import json
import os

FORMATS = ("x_thread", "linkedin", "blog", "ig_long")

FORMAT_LABELS = {
    "x_thread": "X / Twitter thread",
    "linkedin": "LinkedIn post",
    "blog": "Blog article",
    "ig_long": "Instagram long caption",
}

_PROMPTS = {
    "x_thread": (
        "Write a viral X/Twitter THREAD from this transcript. Rules:\n"
        "- 6-10 numbered posts, each 1-3 short sentences, punchy hooks.\n"
        "- The first post must be a scroll-stopping hook.\n"
        "- End with a CTA post.\n"
        "- Return STRICT JSON: {{\"thread\": [\"post 1\", \"post 2\", ...]}}"
    ),
    "linkedin": (
        "Write a professional LinkedIn post from this transcript. Rules:\n"
        "- Hook line first, then insight, then a takeaway + soft CTA.\n"
        "- 150-250 words, first-person, no hashtag spam (max 3).\n"
        "- Return STRICT JSON: {{\"post\": \"...\"}}"
    ),
    "blog": (
        "Write a blog article from this transcript. Rules:\n"
        "- Title + 3-6 sections with ## headings, 250-450 words total.\n"
        "- Keep the speaker's key points and examples.\n"
        "- Return STRICT JSON: {{\"title\": \"...\", \"sections\": "
        "[{{\"heading\": \"...\", \"body\": \"...\"}}]}}"
    ),
    "ig_long": (
        "Write a long Instagram caption from this transcript. Rules:\n"
        "- Hook first, story middle, CTA last.\n"
        "- Line breaks between short paragraphs, 3-6 relevant emoji,\n"
        "  5-8 hashtags at the end (mix of broad + niche).\n"
        "- Return STRICT JSON: {{\"caption\": \"...\"}}"
    ),
}


def _compact_transcript(transcript: dict, limit: int = 250) -> str:
    segs = transcript.get("segments") or []
    return " ".join((s.get("text") or "").strip() for s in segs[:limit])


def _extract_json(text: str) -> dict:
    """Pull a JSON object out of a model response (tolerant of fences)."""
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1]
        t = t.lstrip("json").strip()
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


def repurpose(transcript: dict, fmt: str, title: str = "", hook: str = "",
              log=None) -> dict:
    """Generate written content for `fmt` from a transcript. Returns
    {format, content} where content is the format's natural shape:
      x_thread -> {"thread": [...]}
      linkedin -> {"post": "..."}
      blog     -> {"title": ..., "sections": [...]}
      ig_long  -> {"caption": "..."}
    """
    if fmt not in FORMATS:
        raise ValueError(f"Unknown format: {fmt}")
    from google import genai

    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        raise RuntimeError("GEMINI_API_KEY not set")

    text = _compact_transcript(transcript)
    if not text.strip():
        raise RuntimeError("Transcript is empty — nothing to repurpose")

    log and log(f"✍️ Repurposing transcript → {fmt}...")
    client = genai.Client(api_key=key)
    model = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")
    context = ""
    if title.strip():
        context += f"VIDEO TITLE: {title.strip()[:120]}\n"
    if hook.strip():
        context += f"HOOK: {hook.strip()[:200]}\n"
    prompt = _PROMPTS[fmt] + (
        "\n\nKeep the facts, numbers and names exactly as in the transcript. "
        "Do not invent statistics.\n\n"
        f"{context}TRANSCRIPT:\n{text[:8000]}"
    )
    try:
        resp = client.models.generate_content(
            model=model, contents=prompt,
            config={"response_mime_type": "application/json"},
        )
        data = _extract_json(resp.text)
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"Repurpose failed: {e}") from e

    if not data:
        raise RuntimeError("Gemini returned no usable content")
    log and log(f"✅ {fmt} generated")
    return {"format": fmt, "content": data}
