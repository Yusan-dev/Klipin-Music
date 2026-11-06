"""A/B hook generator — 3 hook variants per clip with predicted scores.

Beats "one viral score" tools by giving the creator three concrete hooks to
choose from, each in a different psychological style (curiosity gap, bold
claim, emotional), each with its own predicted score + reasoning. Uses the
cheap Gemini text model — no video upload, so the cost stays ~free.

  generate_hook_variants(title, hook, transcript, log)
      -> {"variants": [{"style", "text", "predicted_score", "why"}, ...]}
"""
import json
import os

MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")

_VARIANTS_PROMPT = """You are a Shorts/TikTok hook specialist.
Write in the SAME language as the clip transcript (default Indonesian).

CLIP TITLE: "{title}"
CURRENT HOOK: "{hook}"
TRANSCRIPT SNIPPET: "{snippet}"

Generate exactly 3 alternative hooks for this clip, each in a different style:
1. "curiosity"  — curiosity gap / open question that forces the viewer to wait for the payoff.
2. "bold"       — bold claim or controversial statement (still honest, no lies).
3. "emotional"  — emotional / relatable angle that hits the target viewer directly.

Rules:
- Max 12 words per hook, ALL-CAPS forbidden, no emoji.
- Each hook must be grounded in the actual clip content — never invent facts.
- Score each hook 0-100 for short-form retention (predicted_score) and give a
  one-sentence reason (why) referencing what makes it work.

Return STRICT JSON only, no markdown:
{{"variants": [
  {{"style": "curiosity", "text": "...", "predicted_score": 87, "why": "..."}},
  {{"style": "bold", "text": "...", "predicted_score": 82, "why": "..."}},
  {{"style": "emotional", "text": "...", "predicted_score": 79, "why": "..."}}
]}}
"""


def _json_or_none(text: str):
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def generate_hook_variants(title: str, hook: str,
                           transcript: dict | None = None, log=None) -> dict:
    """Generate 3 scored hook variants via Gemini. Returns {"variants": [...]}."""
    from google import genai

    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        raise RuntimeError("GEMINI_API_KEY not set (A/B hooks need it)")

    segs = (transcript or {}).get("segments") or []
    snippet = " ".join(s.get("text", "").strip() for s in segs[:60])[:1200]

    prompt = _VARIANTS_PROMPT.format(
        title=(title or "Clip")[:120],
        hook=(hook or "")[:200],
        snippet=snippet or "(no transcript available)",
    )

    client = genai.Client(api_key=key)
    log and log(f"🅰️ Generating 3 A/B hook variants ({MODEL})...")
    resp = client.models.generate_content(model=MODEL, contents=[prompt])
    data = _json_or_none(resp.text) or {}

    seen_styles = set()
    variants = []
    for v in (data.get("variants") or [])[:3]:
        text = str(v.get("text") or "").strip()[:200]
        if not text:
            continue
        # Enforce prompt contract: max 12 words, no ALL-CAPS, no emoji.
        words = text.split()
        if len(words) > 12:
            text = " ".join(words[:12])
        if text.isupper():
            text = text.title()
        # Strip emoji / non-text symbols that break hook safe-zone rendering.
        text = "".join(ch for ch in text if ch.isprintable()).strip()
        if not text:
            continue
        try:
            score = max(0, min(100, int(float(v.get("predicted_score", 0)))))
        except (TypeError, ValueError):
            score = 0
        style = str(v.get("style") or "hook").strip().lower()[:20]
        if style not in ("curiosity", "bold", "emotional"):
            # Map unknown/duplicate styles to the first missing canonical slot.
            for canonical in ("curiosity", "bold", "emotional"):
                if canonical not in seen_styles:
                    style = canonical
                    break
        if style in seen_styles:
            continue
        seen_styles.add(style)
        variants.append({
            "style": style,
            "text": text,
            "predicted_score": score,
            "why": str(v.get("why") or "").strip()[:200],
        })
    if not variants:
        raise RuntimeError("LLM returned no usable hook variants")
    # Guarantee exactly 3: pad missing styles from the original hook so the
    # frontend A/B promise (curiosity/bold/emotional) always holds.
    if len(variants) < 3:
        fallback_base = (hook or title or "Tonton sampai habis").strip()[:180]
        for canonical in ("curiosity", "bold", "emotional"):
            if len(variants) >= 3:
                break
            if canonical not in seen_styles:
                variants.append({
                    "style": canonical,
                    "text": fallback_base,
                    "predicted_score": 60,
                    "why": "Fallback dari hook asli — LLM tidak mengembalikan varian ini.",
                })
                seen_styles.add(canonical)
    return {"variants": variants[:3]}
