"""Duration-sized lyric sheets for Klipin Music (cheap text-only Gemini call).

HeartMuLa treats ``--max_audio_length_ms`` as a *ceiling*, never a target: the
song stops as soon as the lyric sheet runs out. That single fact explains the
"lagunya cuma 1:37 padahal minta 3:00" report:

  * reference song (``output/music_pop_jawa_full``): 38 sung lines across 9
    sections -> 180.1 s of audio for a 180 s request;
  * UI placeholder default: 4 sung lines -> 94.7 s of audio for a 180 s request;
  * offline draft seed: 8 sung lines -> 97.6 s of audio for a 210 s request.

So the lyric sheet must be sized to the requested duration and written in the
genre's locked vocal language. ``compose_lyrics`` writes a full sheet from an
idea, ``expand_lyrics`` keeps a user's own lines as the core and grows them to
the structure, and both share one sanitizer so a malformed answer can never
reach the model. Same cheap Gemini text path as ``hook_variants.py`` — no video
upload, so the cost stays ~free.
"""
from __future__ import annotations

import os
import re

MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")

# Calibrated on the 180 s reference song: 38 sung lines ≈ 4.7 s per sung line.
SECONDS_PER_LINE = 4.7
MIN_LINES = 6
MAX_LINES = 140
# A sheet may overshoot this much before the sanitizer trims it (keeps the song
# from running far past the duration the user asked and paid credits for).
OVERSHOOT = 1.25

# Seeds shipped in the UI / older builds. They must never be sung on the record:
# the model happily sings "Tuliskan lirikmu di sini" if it is passed as lyrics.
PLACEHOLDER_MARKERS = (
    "tuliskan lirikmu",
    "satu baris satu napas",
    "chorus yang mudah diingat",
    "biar semua ikut bernyanyi",
    "langkah kecil menuju cahaya",
    "kita bernyanyi, kita percaya",
    "nada lokal membawa cerita",
    "tulis lirik kamu",
    "di bawah langit yang sama",
)

# Suno Field Guide Section 13: AI Lyric Anti-Patterns to prevent generic "AI soup".
AI_CLICHE_AVOID_WORDS = (
    "neon", "shadows", "ashes", "embers", "echoes", "chains", "shackles",
    "abyss", "void", "throne", "crown", "crimson", "shattered",
    # Additional overused nouns from the field guide.
    "kingdom", "halo", "wings", "angels", "ghost", "armor", "sword",
    "canvas", "storm", "mirror", "demons", "ruins", "silence", "flames",
)
AI_BANNED_PHRASES = (
    "ghost in the machine", "rise from the ashes", "broken wings",
    "dance with the devil", "weight of the world", "lost in the dark",
    "walls come crashing down", "written in the stars", "chasing dreams",
    # Additional banned phrases from the field guide.
    "heart of gold", "find the light", "demons inside", "fire and ice",
    "against all odds", "eye of the storm", "through the fire",
)
# Adjective crutches that make a lyric read like a template.
AI_CLICHE_ADJECTIVES = (
    "endless", "eternal", "hollow", "broken", "fading", "crimson",
    "golden", "empty", "frozen",
)
# Lazy rhyme pairs: using more than one of them in a song is what makes the
# lyric sound auto-generated.
AI_LAZY_RHYME_PAIRS = (
    ("fire", "desire"), ("heart", "apart"), ("heart", "start"),
    ("night", "light"), ("night", "fight"), ("night", "sight"),
    ("pain", "rain"), ("pain", "vain"), ("soul", "whole"),
    ("soul", "control"), ("time", "mind"), ("time", "blind"),
    ("breath", "death"), ("tears", "fears"), ("tears", "years"),
    ("burn", "turn"), ("burn", "learn"), ("fall", "all"),
)


def _has_lazy_rhyme_pair(lines: list[str]) -> bool:
    """True when two or more lazy rhyme pairs are used in the same sheet."""
    used = 0
    words = {w.lower().strip(".,!?") for line in lines for w in line.split()}
    for a, b in AI_LAZY_RHYME_PAIRS:
        if a in words and b in words:
            used += 1
            if used >= 2:
                return True
    return False


def _is_cliche_line(text: str) -> bool:
    """True when a line is built from the field guide's banned vocabulary."""
    low = f" {re.sub(r'[^a-z0-9 ]+', ' ', text.lower())} "
    for phrase in AI_BANNED_PHRASES:
        if f" {phrase} " in low:
            return True
    for word in AI_CLICHE_AVOID_WORDS:
        if f" {word} " in low:
            return True
    for adj in AI_CLICHE_ADJECTIVES:
        if f" {adj} " in low:
            return True
    return False
# Full-song arc used for anything longer than ~20 lines; weights are scaled to
# the line budget so a 2-minute and a 5-minute song share the same shape.
_FULL_BLOCKS = (
    ("Verse", 4), ("Prechorus", 2), ("Chorus", 4), ("Verse", 4),
    ("Prechorus", 2), ("Chorus", 4), ("Bridge", 3),
    ("Final Chorus", 4), ("Outro", 2),
)

_SONG_PROMPT = """You are a professional lyricist writing for an AI singing model that stops the music the moment the lyric sheet runs out.

Write a COMPLETE lyric sheet that a singer can perform for {duration} seconds — that means {total} sung lines, no more and no less.

VOCAL LANGUAGE (mandatory): {language}. If this describes two languages, follow its section plan exactly and keep each line in the assigned language. Use the natural script of that language; for regional Indonesian languages (Jawa, Sunda, Bali) write Latin romanisation exactly as native songwriters do. Never fall back to English or Indonesian filler.
GENRE: {genre}
VOCAL: {vocal}
TITLE: "{title}"
STORY / IDEA: {prompt}

SECTION STRUCTURE — follow this order and these line counts exactly:
{structure}

HARD RULES:
- Exactly {total} sung lines. Section headers go on their own line inside square brackets, like [Chorus], and are not counted as sung lines.
- 6-11 words per line, one breath per line, singable, with natural end-rhyme or assonance.
- One memorable chorus, repeated verbatim at every chorus section, the way real pop songs do.
- Every line must carry story or emotion with concrete sensory images and lived-in details taken from the STORY / IDEA above.
- Use a HYPHEN "-" for a lyrical break, never an em dash. Use at most one ellipsis in the whole song.
- Vary the line lengths. Never make every line the same shape.
- Avoid AI lyric clichés entirely. Do not use these words: neon, shadows, ashes, embers, echoes, chains, shackles, abyss, void, throne, crown, crimson, shattered, kingdom, halo, wings, angels, ghost, armor, sword, canvas, storm, mirror, demons, ruins, silence, flames, endless, eternal, hollow, broken, fading, golden, empty, frozen.
- Do not use these phrases: "ghost in the machine", "rise from the ashes", "broken wings", "dance with the devil", "weight of the world", "lost in the dark", "walls come crashing down", "written in the stars", "chasing dreams", "heart of gold", "find the light", "demons inside", "fire and ice", "against all odds", "eye of the storm", "through the fire".
- Do not build the song on lazy rhyme pairs. At most ONE of these pairs may appear: fire/desire, heart/apart, night/light, pain/rain, soul/whole, time/mind, breath/death, tears/fears, burn/turn, fall/all.
- Prefer concrete sensory detail over abstract emotion. Write what a camera could film: a specific object, a time of day, one small unglamorous detail.
- Never output placeholder text, lorem ipsum, stage directions, notes about the song, the requested duration, translations, numbering, or markdown fences.
- Do not invent facts about real people, brands, or events.

Return ONLY the lyric sheet: section header lines plus lyric lines, nothing else."""

_EXPAND_PROMPT = _SONG_PROMPT + """

The user already wrote this partial lyric sheet. Keep their strongest lines and
their chorus wording, keep their language, and grow it into the full structure
above. Reuse their imagery rather than inventing a new subject.

USER'S PARTIAL LYRICS:
{existing}"""

_REFINE_PROMPT = """You are a professional song doctor polishing a lyric sheet for an AI singing model.
VOCAL LANGUAGE (mandatory): {language}. Keep every sung line in this language.
GENRE: {genre}
TITLE: "{title}"

TASK: fix the sheet below WITHOUT changing its story, structure, or line count
({total} sung lines). Repair weak rhymes, clunky syllables, repeated words,
and lines that are too long to sing (aim 6-11 words). Keep section headers
exactly as they are. Never add placeholder text, translations, numbering, or
markdown fences. Never invent facts about real people or brands.

CURRENT SHEET:
{existing}

Return ONLY the polished lyric sheet: section header lines plus lyric lines."""

_TITLE_PROMPT = """You are a music-title specialist for {genre} songs in {language}.
STORY / IDEA: {prompt}
LYRICS SNIPPET: {snippet}

Write exactly 3 short song-title candidates (2-5 words each, no quotes, no
numbering, one per line). Memorable, singable, genre-fitting, no clickbait."""

_SECTION_PROMPT = """You are a professional lyricist editing ONE section of a song lyric sheet.
VOCAL LANGUAGE (mandatory): {language}. Keep every sung line in this language.
GENRE: {genre}
TITLE: "{title}"

TASK: rewrite ONLY the [{section}] section below according to this instruction:
"{instruction}"

HARD RULES:
- Return exactly {lines} sung lines, one per line, nothing else.
- Do NOT output the section header or any other section.
- Keep it singable (6-11 words per line) with natural rhyme, matching the song's story.
- Never output placeholder text, translations, numbering, notes, or markdown fences.
- Never invent facts about real people, brands, or events.

CURRENT [{section}] LINES:
{body}

Return ONLY the {lines} rewritten lyric lines."""

_TITLE_PLACEHOLDER = ("tuliskan lirikmu", "judul", "tanpajudul", "untitled",
                      "my song", "lagu baru")


def _is_section(raw: str) -> bool:
    text = (raw or "").strip()
    return text.startswith("[") and text.endswith("]") and len(text) <= 96


def _is_sound_cue(raw: str) -> bool:
    """True for Suno-style non-sung environmental cues like ``*crowd cheering*``."""
    text = (raw or "").strip()
    return len(text) >= 3 and text.startswith("*") and text.endswith("*") and len(text) <= 60


def line_count(lyrics: str) -> int:
    """Number of actually sung lines (section headers excluded)."""
    return sum(1 for raw in (lyrics or "").splitlines()
               if raw.strip() and not _is_section(raw) and not _is_sound_cue(raw))


def is_placeholder(lyrics: str) -> bool:
    """True when the "lyrics" are really the UI/offline seed text."""
    low = (lyrics or "").lower()
    return any(marker in low for marker in PLACEHOLDER_MARKERS)


def target_lines(duration: float, density: float | None = None) -> int:
    """Sung lines needed to fill ``duration`` seconds of audio."""
    if density is None:
        density = float(os.environ.get("KLIPIN_MUSIC_LYRICS_DENSITY", "1") or 1)
    seconds = max(8.0, float(duration or 180))
    lines = seconds / SECONDS_PER_LINE * max(0.5, min(2.0, density))
    return max(MIN_LINES, min(MAX_LINES, int(round(lines))))


def _structure(total: int) -> str:
    """Scale the full-song arc to a ``total`` line budget."""
    if total <= 12:
        return "[Verse]\n4 lines\n[Chorus]\n4 lines\n[Outro]\n2 lines"
    weight = sum(w for _, w in _FULL_BLOCKS)
    out, used = [], 0
    for name, w in _FULL_BLOCKS:
        n = max(2, round(total * w / weight)) if total > 20 else w
        out.append(f"[{name}]\n{n} lines")
        used += n
    # Fix rounding drift on the outro block.
    if used != total:
        drift = total - used
        last = out[-1].splitlines()
        n = max(1, int(last[1].split()[0]) + drift)
        out[-1] = f"{last[0]}\n{n} lines"
    return "\n".join(out)


def _call_gemini(prompt: str, log=None) -> str:
    from google import genai
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("GEMINI_API_KEY not set (lyrics need it)")
    client = genai.Client(api_key=key)
    log and log(f"✍️ Menulis lirik AI ({MODEL})…")
    resp = client.models.generate_content(model=MODEL, contents=[prompt])
    return (getattr(resp, "text", "") or "").strip()


def sanitize_sheet(text: str, total: int) -> str:
    """Force a model answer into exactly ``total`` sung lines.

    Keeps section headers and Suno-style sound cues (``*crowd cheering*``),
    drops stage directions/numbering and the field guide's AI-cliche
    vocabulary, trims overshoot and pads shortfalls by repeating the chorus
    (pop-song behaviour).  Sound cues are kept but never counted as sung
    lines, so they cannot eat into the duration-sized line budget.
    """
    sections: list[tuple[str, list[str], list[str]]] = []
    current = "Verse"
    buf: list[str] = []
    cues: list[str] = []

    def flush():
        if buf or cues:
            sections.append((current, list(buf), list(cues)))
            buf.clear()
            cues.clear()

    for raw in (text or "").splitlines():
        t = raw.strip().strip("`").strip()
        if not t or t.startswith("```"):
            continue
        if _is_section(t):
            flush()
            current = t[1:-1].strip() or current
            continue
        # Suno-style sound cue (*crowd cheering*) must be checked BEFORE the
        # bullet-stripping regex: the old code stripped the leading "*" first,
        # so the cue became the literal text "crowd cheering*" and the model
        # SANG the stage direction instead of treating it as a cue.
        if _is_sound_cue(t):
            cues.append(t[:140])
            continue
        # Drop numbering / bullets / stage directions / notes.
        t = re.sub(r"^(\d+[\).:\-]\s*|[-*\u2022]\s*)", "", t).strip()
        if not t or len(t.split()) > 18:
            continue
        low = t.lower()
        if low.startswith(("note:", "catatan:", "translation:", "terjemahan:",
                           "intro (", "stage direction")):
            continue
        if is_placeholder(t):
            continue
        # Field guide section 13: drop the AI-cliche vocabulary for real.
        if _is_cliche_line(t):
            continue
        buf.append(t[:140])
    flush()
    if not sections:
        return ""
    # Flatten preserving section order, then fit the SUNG lines to budget.
    flat: list[tuple[str, str]] = []
    for name, lines, _cue in sections:
        for line in lines:
            flat.append((name, line))
    # Find chorus lines for padding / verbatim repeat.
    chorus = [ln for sec, ln in flat if "chorus" in sec.lower()]
    if len(flat) > int(total * OVERSHOOT):
        flat = flat[:total]
    while len(flat) < total and chorus:
        # Repeat chorus verbatim, inserted before outro when possible.
        flat.insert(max(0, len(flat) - 2), ("Chorus", chorus[len(flat) % len(chorus)]))
    # Rebuild with headers (chorus repeated verbatim) + the original cues.
    out: list[str] = []
    last_sec = None
    for sec, ln in flat[:total]:
        if sec != last_sec:
            out.append(f"[{sec}]")
            last_sec = sec
        out.append(ln)
    # Sound cues are re-attached to the end of their own section, in order.
    for name, _lines, cue_lines in sections:
        if not cue_lines:
            continue
        if name != last_sec:
            out.append(f"[{name}]")
            last_sec = name
        out.extend(cue_lines)
    return "\n".join(out).strip()


def _compose_via_model(kind: str, title: str, prompt: str, genre: str,
                       language: str, vocal: str, duration: float,
                       existing: str = "", log=None) -> str:
    total = target_lines(duration)
    structure = _structure(total)
    base = _SONG_PROMPT.format(duration=f"{duration:.0f}", total=total,
                               language=language, genre=genre, vocal=vocal,
                               title=(title or "Tanpa Judul")[:80],
                               prompt=(prompt or "cerita hangat")[:600],
                               structure=structure)
    if kind == "expand":
        base = _EXPAND_PROMPT.format(
            duration=f"{duration:.0f}", total=total, language=language,
            genre=genre, vocal=vocal, title=(title or "Tanpa Judul")[:80],
            prompt=(prompt or "cerita hangat")[:600], structure=structure,
            existing=(existing or "")[:2000])
    raw = _call_gemini(base, log=log)
    clean = sanitize_sheet(raw, total)
    if line_count(clean) < max(MIN_LINES, int(total * 0.7)):
        raise RuntimeError("LLM returned too few usable lyric lines")
    return clean


def compose_lyrics(title: str, prompt: str, genre: str = "pop",
                   language: str = "id", vocal: str = "auto",
                   duration: float = 180, log=None) -> dict:
    """Write a full duration-sized sheet from an idea. Returns dict."""
    total = target_lines(duration)
    try:
        sheet = _compose_via_model("compose", title, prompt, genre,
                                   language, vocal, duration, log=log)
        return {"lyrics": sheet, "lines": line_count(sheet),
                "target": total, "source": "gemini"}
    except Exception as exc:  # noqa: BLE001 — offline-sized fallback below
        log and log(f"⚠️ Lirik AI gagal ({exc}); pakai draft offline")
        sheet = offline_sheet(prompt, total)
        return {"lyrics": sheet, "lines": line_count(sheet),
                "target": total, "source": "offline", "error": str(exc)[:300]}


def expand_lyrics(existing: str, title: str, prompt: str, genre: str = "pop",
                  language: str = "id", vocal: str = "auto",
                  duration: float = 180, log=None) -> dict:
    """Grow the user's partial lyrics into the full structure."""
    total = target_lines(duration)
    if not (existing or "").strip() or is_placeholder(existing):
        return compose_lyrics(title, prompt, genre, language, vocal, duration, log=log)
    try:
        sheet = _compose_via_model("expand", title, prompt, genre,
                                   language, vocal, duration,
                                   existing=existing, log=log)
        return {"lyrics": sheet, "lines": line_count(sheet),
                "target": total, "source": "gemini-expand"}
    except Exception as exc:  # noqa: BLE001
        log and log(f"⚠️ Perluas lirik gagal ({exc}); pakai draft offline")
        sheet = offline_sheet((existing or "") + "\n" + (prompt or ""), total)
        return {"lyrics": sheet, "lines": line_count(sheet),
                "target": total, "source": "offline", "error": str(exc)[:300]}


def refine_lyrics(existing: str, title: str, genre: str = "pop",
                  language: str = "id", log=None) -> dict:
    """Polish the user's sheet in place: same story/structure/count, better craft."""
    total = line_count(existing)
    if total < 2:
        raise RuntimeError("Lirik terlalu pendek untuk diperbaiki (min. 2 baris)")
    try:
        raw = _call_gemini(_REFINE_PROMPT.format(
            language=language, genre=genre, title=(title or "Tanpa Judul")[:80],
            total=total, existing=(existing or "")[:3000]), log=log)
        clean = sanitize_sheet(raw, total)
        if line_count(clean) < max(2, int(total * 0.8)):
            raise RuntimeError("LLM returned too few usable lines")
        return {"lyrics": clean, "lines": line_count(clean),
                "target": total, "source": "gemini-refine"}
    except Exception as exc:  # noqa: BLE001
        log and log(f"⚠️ Refine gagal ({exc})")
        raise


def split_sections(lyrics: str) -> list[dict]:
    """Parse a sheet into ordered ``{header, lines}`` sections.

    Lines before the first header are kept as a header-less opening section so a
    round-trip through :func:`join_sections` never loses the user's text.
    """
    text = (lyrics or "").replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip():
        return []
    sections: list[dict] = []
    current: dict = {"header": "", "lines": []}
    for raw in text.split("\n"):
        stripped = raw.strip()
        if _is_section(stripped):
            if current["header"] or current["lines"]:
                sections.append(current)
            current = {"header": stripped, "lines": []}
        elif stripped:
            current["lines"].append(raw.rstrip())
    if current["header"] or current["lines"]:
        sections.append(current)
    return sections


def join_sections(sections: list[dict]) -> str:
    """Rebuild a sheet from :func:`split_sections` output."""
    out: list[str] = []
    for sec in sections or []:
        if sec.get("header"):
            out.append(str(sec["header"]).strip())
        out.extend(line for line in sec.get("lines", []) if str(line).strip())
    return "\n".join(out).strip()


def _clean_section_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw in (text or "").splitlines():
        t = raw.strip().strip("`").strip()
        if not t or t.startswith("```") or _is_section(t):
            continue
        # Keep sound cues (*crowd cheering*) intact - check before stripping.
        if _is_sound_cue(t):
            lines.append(t[:140])
            continue
        t = re.sub(r"^(\d+[\).\:\-]\s*|[-*•]\s*)", "", t).strip()
        if not t or len(t.split()) > 18:
            continue
        low = t.lower()
        if low.startswith(("note:", "catatan:", "translation:", "terjemahan:",
                           "section:", "bagian:")):
            continue
        if is_placeholder(t):
            continue
        if _is_cliche_line(t):
            continue
        lines.append(t[:140])
    return lines


def rewrite_section(existing: str, index: int, instruction: str, title: str = "",
                    genre: str = "pop", language: str = "id", log=None) -> dict:
    """AI-edit a single section, keeping every other section byte-identical.

    The section keeps its original line count so the sung duration does not
    drift after an edit.
    """
    sections = split_sections(existing)
    if not sections:
        raise RuntimeError("Lirik masih kosong — tulis atau buat lirik dulu.")
    try:
        idx = int(index)
    except (TypeError, ValueError):
        raise RuntimeError("Bagian lirik tidak valid.") from None
    if not 0 <= idx < len(sections):
        raise RuntimeError("Bagian lirik tidak ditemukan.")
    instruction = (instruction or "").strip()
    if len(instruction) < 3:
        raise RuntimeError("Tulis instruksi perubahan (mis. 'jadikan chorus lebih megah').")
    target = sections[idx]
    body = "\n".join(target.get("lines", [])).strip()
    if not body:
        raise RuntimeError("Bagian lirik yang dipilih masih kosong.")
    total = len(target.get("lines", []))
    name = (target.get("header") or "Verse").strip("[]") or "Verse"
    raw = _call_gemini(_SECTION_PROMPT.format(
        language=language, genre=genre, title=(title or "Tanpa Judul")[:80],
        instruction=instruction[:300], section=name, lines=total,
        body=body[:1200]), log=log)
    lines = _clean_section_lines(raw)
    if not lines:
        raise RuntimeError("LLM tidak menghasilkan lirik untuk bagian ini.")
    if len(lines) > total:
        lines = lines[:total]
    else:
        for original in target.get("lines", []):
            if len(lines) >= total:
                break
            lines.append(original)
    sections[idx]["lines"] = lines
    return {"lyrics": join_sections(sections), "section": name, "index": idx,
            "lines": len(lines), "target": total, "source": "gemini-section"}


def suggest_titles(title: str, prompt: str, lyrics: str, genre: str = "pop",
                   language: str = "id", log=None) -> dict:
    """3 title candidates from the idea/lyrics. Offline fallback included."""
    seed = (prompt or "") + "\n" + (lyrics or "")
    try:
        raw = _call_gemini(_TITLE_PROMPT.format(
            genre=genre, language=language, prompt=(prompt or title)[:400],
            snippet="\n".join((lyrics or "").splitlines()[:8])[:600] or "-"), log=log)
        titles = [t.strip().strip("\"'“”‘’-•*").strip()
                  for t in raw.splitlines() if t.strip()]
        titles = [t for t in titles if 1 <= len(t) <= 60][:3]
        if not titles:
            raise RuntimeError("LLM returned no titles")
        return {"titles": titles, "source": "gemini"}
    except Exception:  # noqa: BLE001 — fallback deterministik
        words = [w for w in seed.split() if len(w) > 3][:4]
        base = " ".join(words).title()[:40] or "Lagu Baru"
        low = base.lower()
        if any(m in low for m in _TITLE_PLACEHOLDER) or len(base) < 4:
            base = f"{genre.title()} Baru"
        return {"titles": [base, f"{base} (Reprise)", f"Cerita {base}"],
                "source": "offline"}


def offline_sheet(seed: str, total: int) -> str:
    """Deterministic duration-sized fallback (never placeholder text)."""
    topic = " ".join((seed or "cerita hari ini").split())[:48] or "cerita hari ini"
    structure = _structure(total)
    # Parse section sizes from the structure string.
    blocks: list[tuple[str, int]] = []
    cur = "Verse"
    for line in structure.splitlines():
        line = line.strip()
        if line.startswith("["):
            cur = line.strip("[]")
        elif line.endswith("lines"):
            try:
                blocks.append((cur, int(line.split()[0])))
            except ValueError:
                pass
    pool = [f"{topic}, langkah kita berlanjut",
            "Cahaya pagi menuntun pulang",
            "Nada wengi ngancani ati",
            "Nada ini membawa cerita",
            "Ing ngisor lintang kang padha",
            "Kita pulang membawa makna",
            "Angin membawa kabar baik",
            "Hati tenang melangkah pasti"]
    out: list[str] = []
    i = 0
    for sec, n in blocks:
        out.append(f"[{sec}]")
        for _ in range(n):
            out.append(pool[i % len(pool)])
            i += 1
    # Exact fit.
    sung = [ln for ln in out if not _is_section(ln)][:total]
    rebuilt: list[str] = []
    idx = 0
    for ln in out:
        if _is_section(ln):
            rebuilt.append(ln)
        elif idx < len(sung):
            rebuilt.append(sung[idx])
            idx += 1
    return "\n".join(rebuilt).strip()


def ensure_sheet(spec: dict, log=None) -> tuple[str, dict]:
    """Return ``(lyrics, info)`` sized to ``spec['duration']``.

    Uses the user's lyrics when long enough, expands them with Gemini when
    short, or composes a fresh sheet when empty/placeholder. Never returns
    placeholder seed text.
    """
    duration = float(spec.get("duration") or 180)
    language = spec.get("_language_prompt") or spec.get("language", "id")
    total = target_lines(duration)
    existing = (spec.get("lyrics") or "").strip()
    if existing and not is_placeholder(existing) and line_count(existing) >= int(total * 0.7):
        return existing, {"source": "user", "lines": line_count(existing), "target": total}
    has_key = bool(os.environ.get("GEMINI_API_KEY", "").strip())
    if existing and not is_placeholder(existing) and has_key:
        res = expand_lyrics(existing, spec.get("title", ""), spec.get("prompt", ""),
                            spec.get("genre", "pop"), language,
                            spec.get("vocal_gender", "auto"), duration, log=log)
        return res["lyrics"], res
    if has_key:
        res = compose_lyrics(spec.get("title", ""), spec.get("prompt", ""),
                             spec.get("genre", "pop"), language,
                             spec.get("vocal_gender", "auto"), duration, log=log)
        return res["lyrics"], res
    sheet = offline_sheet(f"{spec.get('prompt', '')}\n{existing}", total)
    return sheet, {"source": "offline", "lines": line_count(sheet), "target": total}
