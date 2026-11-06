"""AI publish ideas — platform-specific caption + hashtags per clip.

When a user publishes a clip, they can hit "✨ AI" to get a ready-to-post
caption and hashtags tuned for the target platform (YouTube Shorts / TikTok /
Instagram Reels). Uses the cheap Gemini text model — no video upload, so the
cost stays ~free.

  suggest_post(platform, title, hook, transcript, log)
      -> {"caption": "...", "hashtags": ["#...", ...], "title": "..."}

The clip already carries a title + hook from the pipeline, so we only send
text; transcript segments are trimmed to a small snippet for context.
"""
import json
import os

MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")

# Platform personality: each platform has its own caption conventions.
_PLATFORM_GUIDE = {
    "youtube": (
        "YouTube Shorts. Give a SHORT punchy title (max 100 chars, curiosity "
        "gap, no clickbait lies) and a 1-2 line description that sets up the "
        "payoff. Keep hashtags to 3-5."
    ),
    "tiktok": (
        "TikTok. Write a conversational, hook-first caption in Indonesian "
        "(max 2200 chars, but keep it tight — 1-3 short sentences). Include "
        "3-5 hashtags: 1-2 trending/general + 1-2 niche + 1 platform tag."
    ),
    "instagram": (
        "Instagram Reels. Write a slightly longer storytelling caption in "
        "Indonesian with 1-2 emojis and a light call-to-action. Include 5-8 "
        "hashtags: broad + niche mix."
    ),
}

_PROMPT = """You write social media captions for an Indonesian creator.
Write in Indonesian unless the clip content is clearly another language.

TARGET PLATFORM: {platform}
PLATFORM STYLE: {guide}

CLIP TITLE: "{title}"
HOOK (emotional promise): "{hook}"
{source}

Return STRICT JSON only, no markdown:
{{
  "title": "platform title (youtube only, else reuse hook)",
  "caption": "ready-to-post caption WITHOUT the hashtags below",
  "hashtags": ["#tag1", "#tag2", ...]
}}

Rules:
- The caption must not already contain the hashtags (they are appended separately).
- Do not invent facts not present in the clip.
- Keep the tone energetic but honest — matching the clip's actual emotion.
"""


def _json_or_none(text: str):
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def suggest_post(platform: str, title: str, hook: str,
                 transcript: dict | None = None, log=None) -> dict:
    """Generate {title, caption, hashtags} for one platform via Gemini."""
    from google import genai

    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        raise RuntimeError("GEMINI_API_KEY not set (publish ideas need it)")

    segs = (transcript or {}).get("segments") or []
    snippet = " ".join(s.get("text", "").strip() for s in segs[:60])[:1000]
    source = f'SOURCE SNIPPET: "{snippet}"' if snippet else ""
    guide = _PLATFORM_GUIDE.get(platform, _PLATFORM_GUIDE["tiktok"])

    prompt = _PROMPT.format(
        platform=platform,
        guide=guide,
        title=(title or f"Clip")[:120],
        hook=(hook or "")[:200],
        source=source,
    )

    client = genai.Client(api_key=key)
    log and log(f"✨ Generating {platform} publish ideas ({MODEL})...")
    resp = client.models.generate_content(model=MODEL, contents=[prompt])
    data = _json_or_none(resp.text) or {}

    caption = str(data.get("caption") or hook or "").strip()[:2200]
    hashtags = []
    for t in data.get("hashtags") or []:
        t = str(t).strip().strip("#")
        if t and t not in hashtags:
            hashtags.append(f"#{t[:30]}")
    ideas_title = str(data.get("title") or title or "").strip()[:100]
    return {"title": ideas_title, "caption": caption, "hashtags": hashtags}
