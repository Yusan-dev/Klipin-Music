"""Arrangement intelligence ("layout for audio") for Klipin Music.

Suno-class products feel *smart* because the song is planned before it is
synthesized: every section has a job (verse lowers energy, chorus is the
payoff), there is one memorable hook, and the style is a deliberate tag set
rather than a free-text wish. Klipin already sizes the lyric sheet to the
requested duration (``lyrics_writer``); this module turns that sheet into an
explicit arrangement plan and feeds the result back into the engines:

* ``plan_from_sheet`` — deterministic section plan (role, energy, vocal,
  instrument focus, hook, start/end seconds) derived from the sheet;
* ``driver_tags`` — short arrangement tags appended to the provider tags so
  HeartMuLa / DiffRhythm / ACE-Step actually see "verse→chorus lift", "singalong
  chorus", etc.;
* ``format_pipe_header`` — Suno-style pipe-stacked section meta-tag
  (``[Chorus | Anthemic | Lead with harmony | Full band]``) with left-to-right
  priority and a 7-element ceiling;
* ``suggest_style_tags`` / ``hook_variants`` — optional *cheap* Gemini text
  passes (same path as ``hook_variants.py``), each with an offline fallback so
  no code path ever fails because a text quota ran out.

Nothing here spends GPU seconds: it only shapes the prompt/tags that the
existing workers already consume.
"""
from __future__ import annotations

import os
import re

import lyrics_writer as lw

DEFAULT_BPM = 96
MAX_SECTIONS = 24

# Header -> role. Order matters: "final chorus" and "pre chorus" must win
# before the generic "chorus" needle (a pre-chorus literally contains "chorus").
_ROLE_RULES = (
    ("final chorus", "final-chorus"),
    ("last chorus", "final-chorus"),
    ("pre chorus", "prechorus"),
    ("prechorus", "prechorus"),
    ("chorus", "chorus"),
    ("refrain", "chorus"),
    ("hook", "chorus"),
    ("bridge", "bridge"),
    ("middle 8", "bridge"),
    ("outro", "outro"),
    ("ending", "outro"),
    ("intro", "intro"),
    ("instrumental", "break"),
    ("interlude", "break"),
    ("breakdown", "break"),
    ("solo", "break"),
    ("verse", "verse"),
)

_ROLE_ENERGY = {
    "intro": 2, "verse": 2, "prechorus": 3, "chorus": 5, "final-chorus": 5,
    "bridge": 2, "break": 3, "outro": 1,
}
_ROLE_VOCAL = {
    "intro": "soft", "verse": "lead", "prechorus": "lead build",
    "chorus": "lead with harmony", "final-chorus": "lead with full harmony",
    "bridge": "intimate", "break": "instrumental", "outro": "fade out",
}
_ROLE_FOCUS = {
    "intro": "sparse texture, set the mood",
    "verse": "steady groove, leave room for the vocal",
    "prechorus": "rising tension, add layers",
    "chorus": "full band, layered harmony",
    "final-chorus": "biggest lift, everything in",
    "bridge": "stripped back, intimate",
    "break": "featured instrument",
    "outro": "thin out and resolve",
}

_HOOK_ROLES = ("chorus", "final-chorus")


def role_for(header: str) -> str:
    """Map a section header like ``[Pre-Chorus]`` or ``[Chorus | Anthemic]`` to a canonical role."""
    raw = str(header or "").strip("[] ")
    primary = raw.split("|", 1)[0].split(":", 1)[0]
    name = re.sub(r"[\s_\-]+", " ", primary.lower()).strip()
    for needle, role in _ROLE_RULES:
        if needle in name:
            return role
    return "verse"


def format_pipe_header(name: str, role: str, vocal: str = "", focus: str = "") -> str:
    """Build a Suno-style pipe-stacked section meta-tag (max 7 elements, left-to-right priority)."""
    base_name = str(name or "Verse").split("|", 1)[0].strip("[] ") or "Verse"
    energy = _ROLE_ENERGY.get(role, 3)
    mood_tag = (
        "Explosive Anthemic" if energy >= 5 else
        "Building Intensity" if energy == 4 or role == "prechorus" else
        "Dynamic Groove" if energy == 3 else
        "Intimate Stripped Back" if role == "bridge" else
        "Fading Resolution" if role == "outro" else
        "Warm Melodic"
    )
    parts = [base_name, mood_tag]
    if vocal:
        parts.append(vocal)
    if focus:
        parts.append(focus.split(",", 1)[0].strip())
    return "[" + " | ".join(p for p in parts[:7] if p) + "]"


def _sections_from_sheet(lyrics: str, duration: float = 180) -> list[dict]:
    """Sections from a real sheet, or a structural fallback plan."""
    sections = lw.split_sections(lyrics)
    if sections:
        return sections
    # No sheet yet: mirror the arc the writer will use for this duration so the
    # plan is stable from the first preview.
    total = lw.target_lines(duration)
    blocks: list[dict] = []
    current = "Verse"
    for raw in lw._structure(total).splitlines():  # noqa: SLF001 - shared arc
        line = raw.strip()
        if line.startswith("["):
            current = line.strip("[]")
        elif line.endswith("lines"):
            try:
                count = int(line.split()[0])
            except ValueError:
                count = 0
            blocks.append({"header": f"[{current}]", "lines": [""] * max(1, count)})
    return blocks or [{"header": "[Verse]", "lines": [""] * 4}]


def plan_from_sheet(lyrics: str, duration: float, bpm: int = 0) -> dict:
    """Deterministic arrangement plan for ``lyrics`` at ``duration`` seconds."""
    sections_in = [
        {"header": s.get("header") or "[Verse]",
         "lines": [ln for ln in (s.get("lines") or []) if str(ln).strip()]}
        for s in _sections_from_sheet(lyrics, duration)
    ][:MAX_SECTIONS]
    weights = [max(1, len(s["lines"])) for s in sections_in]
    total_weight = float(sum(weights)) or 1.0
    seconds = max(8.0, float(duration or 180))

    sections: list[dict] = []
    cursor = 0.0
    for index, (sec, weight) in enumerate(zip(sections_in, weights)):
        header = sec["header"]
        role = role_for(header)
        span = seconds * weight / total_weight
        if index == len(sections_in) - 1:
            end = seconds  # absorb rounding drift on the final block
        else:
            end = round(cursor + span, 2)
        vocal_desc = _ROLE_VOCAL.get(role, "lead")
        focus_desc = _ROLE_FOCUS.get(role, "")
        clean_name = header.strip("[]").split("|", 1)[0].strip() or "Verse"
        sections.append({
            "index": index,
            "header": header,
            "pipe_header": format_pipe_header(clean_name, role, vocal_desc, focus_desc),
            "name": clean_name,
            "role": role,
            "lines": sec["lines"],
            "line_count": len(sec["lines"]),
            "start_sec": round(cursor, 2),
            "end_sec": end,
            "duration_sec": round(max(0.0, end - cursor), 2),
            "energy": _ROLE_ENERGY.get(role, 3),
            "vocal": vocal_desc,
            "focus": focus_desc,
            "hook": role in _HOOK_ROLES,
        })
        cursor = end

    hook = _hook_from_sections(sections)
    plan = {
        "version": 1,
        "duration_sec": round(seconds, 2),
        "bpm": int(bpm or 0),
        "sections": sections,
        "energy_curve": [s["energy"] for s in sections],
        "has_chorus": any(s["hook"] for s in sections),
        "hook": hook,
        "driver_tags": driver_tags(sections),
        "style_tags": "",
        "source": "offline",
    }
    plan["style_tags"] = offline_style_tags({"bpm": bpm, "genre": ""}, plan)
    return plan


def _hook_from_sections(sections: list[dict]) -> dict:
    """The single line listeners should remember: first line of a chorus."""
    for sec in sections:
        if sec["role"] in _HOOK_ROLES and sec["lines"]:
            return {"line": str(sec["lines"][0])[:140], "section": sec["name"],
                    "index": sec["index"], "variants": []}
    for sec in sections:
        if sec["lines"]:
            return {"line": str(sec["lines"][0])[:140], "section": sec["name"],
                    "index": sec["index"], "variants": []}
    return {"line": "", "section": "", "index": -1, "variants": []}


def driver_tags(sections: list[dict]) -> list[str]:
    """Short arrangement tags the engines actually react to."""
    tags = ["clear verse to chorus lift", "dynamic arrangement"]
    if any(s["role"] in _HOOK_ROLES for s in sections):
        tags.append("anthemic singalong chorus")
        tags.append("memorable hook")
    if any(s["role"] == "bridge" for s in sections):
        tags.append("emotional bridge section")
    if any(s["role"] == "break" for s in sections):
        tags.append("featured instrumental break")
    if any(s["role"] == "outro" for s in sections):
        tags.append("resolved outro")
    return tags


def offline_style_tags(spec: dict, plan: dict) -> str:
    """Deterministic style tags when Gemini is unavailable or not requested."""
    parts: list[str] = []
    try:
        genre = str(spec.get("genre") or "").strip()
    except AttributeError:
        genre = ""
    if genre:
        parts.append(genre.replace("-", " "))
    gender = str(spec.get("vocal_gender") or "auto").strip().lower()
    if spec.get("instrumental"):
        parts.append("instrumental")
    elif gender == "female":
        parts.append("female lead vocal")
    elif gender == "male":
        parts.append("male lead vocal")
    elif gender == "duet":
        parts.append("male and female duet")
    try:
        bpm = int(spec.get("bpm") or 0)
    except (TypeError, ValueError):
        bpm = 0
    if 40 <= bpm <= 220:
        parts.append("tight rhythmic pocket")
    parts.extend(["radio-ready mix", "clean wide production"])
    parts.extend(driver_tags(plan.get("sections") or [])[:3])
    return ", ".join(dict.fromkeys(p for p in parts if p))


def _clean_tags(raw: str) -> str:
    text = re.sub(r"^(tags?|style)\s*[:\-]\s*", "", str(raw or ""), flags=re.I)
    items = [re.sub(r"\s+", " ", seg).strip(" .;\"'`") for seg in text.split(",")]
    items = [seg for seg in items if 2 <= len(seg) <= 48]
    return ", ".join(dict.fromkeys(items[:12]))


def _gemini(prompt: str, log=None) -> str:
    return lw._call_gemini(prompt, log=log)  # noqa: SLF001 - one shared text path


_STYLE_PROMPT = """You write music-production style tags for an AI song generator.
GENRE: {genre}
VOCAL: {vocal}
LANGUAGE: {language}
BPM HINT: {bpm}
ARRANGEMENT: {arrangement}
STORY / IDEA: {prompt}

Write 8-12 comma-separated, concrete production tags covering instrumentation,
mood, groove, and vocal treatment. Lowercase, English, no genre name repetition,
no numbers, no sentences, no quotes. Example shape:
"live drums, warm bassline, plucked guitar, airy pads, uplifting mood, duet harmony"
Return ONLY the tag list."""


def suggest_style_tags(spec: dict, plan: dict, log=None) -> str:
    """Cheap Gemini pass for richer style tags; offline fallback on any error."""
    fallback = offline_style_tags(spec, plan)
    if not os.environ.get("GEMINI_API_KEY", "").strip():
        return fallback
    arrangement = " -> ".join(
        f"{s['name']} (energy {s['energy']})" for s in (plan.get("sections") or [])[:10])
    try:
        raw = _gemini(_STYLE_PROMPT.format(
            genre=spec.get("genre") or "pop",
            vocal=("instrumental" if spec.get("instrumental")
                   else spec.get("vocal_gender") or "auto"),
            language=spec.get("language") or "id",
            bpm=spec.get("bpm") or "-",
            arrangement=arrangement or "-",
            prompt=(spec.get("prompt") or "-")[:400]), log=log)
        return _clean_tags(raw) or fallback
    except Exception as exc:  # noqa: BLE001 - tags must never break a job
        log and log(f"⚠️ Saran style gagal ({exc}); pakai tag offline")
        return fallback


_HOOK_PROMPT = """You write hooks for a {genre} song ({language}).
TITLE: {title}
CURRENT CHORUS HOOK: {hook}
STORY / IDEA: {prompt}

Write exactly 3 alternative hook lines a singer can repeat. One per line, in
this pipe format, no extra text:
<score 0-100>|<angle>|<hook line>

angle is one of: curiosity, bold, emotional. 6-11 words per line, singable,
no quotes, no numbering, no placeholder text."""


def _offline_hook_variants(spec: dict, plan: dict) -> list[dict]:
    hook = ((plan.get("hook") or {}).get("line") or "").strip()
    idea = " ".join(str(spec.get("prompt") or "cerita kita").split())[:48]
    lines = [
        (hook or f"{idea}, kita bersinar lagi"),
        f"tentang {idea} yang tak mudah dilupakan",
        f"{idea} — kita bernyanyi sampai pagi",
    ]
    angles = ("curiosity", "bold", "emotional")
    scores = (72, 66, 61)
    out = []
    for line, angle, score in zip(lines, angles, scores):
        out.append({"line": line[:140], "angle": angle, "score": score,
                    "reason": "Fallback offline: bisa langsung dinyanyikan di chorus.",
                    "source": "offline"})
    return out


def hook_variants(spec: dict, plan: dict, log=None) -> list[dict]:
    """3 scored hook alternatives; deterministic fallback when Gemini is off."""
    fallback = _offline_hook_variants(spec, plan)
    if not os.environ.get("GEMINI_API_KEY", "").strip():
        return fallback
    try:
        raw = _gemini(_HOOK_PROMPT.format(
            genre=spec.get("genre") or "pop",
            language=spec.get("language") or "id",
            title=(spec.get("title") or "Tanpa Judul")[:80],
            hook=((plan.get("hook") or {}).get("line") or "-")[:140],
            prompt=(spec.get("prompt") or "-")[:400]), log=log)
    except Exception as exc:  # noqa: BLE001
        log and log(f"⚠️ Varian hook gagal ({exc}); pakai fallback")
        return fallback
    out: list[dict] = []
    for raw_line in (raw or "").splitlines():
        parts = [p.strip() for p in raw_line.split("|")]
        if len(parts) < 3:
            continue
        try:
            score = int(re.sub(r"\D", "", parts[0])[:3] or 0)
        except ValueError:
            score = 0
        angle = parts[1].lower()
        line = re.sub(r"\s+", " ", parts[2]).strip(" .\"'`")
        if len(line) < 10 or len(line.split()) > 18:
            continue
        if angle not in ("curiosity", "bold", "emotional"):
            angle = "emotional"
        out.append({"line": line[:140], "angle": angle,
                    "score": max(0, min(100, score)),
                    "reason": "Varian AI: frasa hook untuk chorus.",
                    "source": "gemini"})
        if len(out) == 3:
            break
    return out or fallback


def build_plan(spec: dict, ai: bool = False, log=None) -> dict:
    """Full plan for a music spec. ``ai`` adds tag/hook suggestions (cheap)."""
    try:
        bpm = int(spec.get("bpm") or 0)
    except (TypeError, ValueError):
        bpm = 0
    plan = plan_from_sheet(str(spec.get("lyrics") or ""),
                           float(spec.get("duration") or 180), bpm)
    plan["style_tags"] = (suggest_style_tags(spec, plan, log=log) if ai
                          else offline_style_tags(spec, plan))
    plan["hook"]["variants"] = hook_variants(spec, plan, log=log) if ai else []
    plan["source"] = "gemini" if (ai and os.environ.get("GEMINI_API_KEY", "").strip()) \
        else "offline"
    if plan["source"] == "offline":
        plan["hook"]["variants"] = _offline_hook_variants(spec, plan)
    return plan


def summary(plan: dict) -> str:
    """One-line human summary for logs/UI."""
    sections = plan.get("sections") or []
    arc = " -> ".join(
        f"{s['name']}({s['energy']})" for s in sections[:12])
    hook = (plan.get("hook") or {}).get("line") or "-"
    return f"{len(sections)} sections: {arc} | hook: {hook}"
