"""AI typo auto-fix for subtitle lines (Gemini text model).

Suggestion-only: returns corrected lines for the user to review in the
caption editor before saving. No DB writes, no re-render here.
"""

import json
import os


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


def fix_caption_lines(lines: list, log=None) -> list:
    """Correct obvious typos in caption lines; keep meaning, numbers, emoji."""
    clean = [str(x).strip() for x in lines if str(x).strip()]
    if not clean:
        raise RuntimeError("No caption lines to fix")

    from google import genai

    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        raise RuntimeError("GEMINI_API_KEY not set")

    log and log(f"✨ AI auto-fix: sending {len(clean)} caption line(s) to Gemini...")
    client = genai.Client(api_key=key)
    model = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")
    prompt = (
        "You fix typos and obvious grammar mistakes in subtitle lines.\n"
        "Rules:\n"
        "- Correct ONLY clear typos / obvious errors; keep the speaker's slang\n"
        "  (e.g. 'nggak', 'gitu') as-is.\n"
        "- Never reword, summarize, translate, or add content.\n"
        "- Keep emoji, numbers, currency, names exactly.\n"
        "- Return EXACTLY the same number of lines, in the same order.\n"
        "- Return STRICT JSON: {\"lines\": [\"...\", \"...\"]}\n\n"
        f"LINES:\n{json.dumps(clean, ensure_ascii=False)}"
    )
    try:
        resp = client.models.generate_content(
            model=model, contents=prompt,
            config={"response_mime_type": "application/json"},
        )
        data = _extract_json(resp.text)
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"AI auto-fix failed: {e}") from e

    fixed = data.get("lines")
    if not isinstance(fixed, list) or not fixed:
        raise RuntimeError("Gemini returned no corrected lines")

    fixed = fixed[:len(clean)]
    while len(fixed) < len(clean):
        fixed.append(clean[len(fixed)])
    log and log(f"✅ AI auto-fix: {len(clean)} line(s) corrected")
    return fixed