"""ASS subtitle generation for Klipin.

Default style: white (#FFFFFF) text, no highlight box — proven best for
readability on short videos. Default animation is `karaoke`: unspoken text
stays in the base colour (white) and each word sweeps to the highlight
colour (#FFE500 yellow) the moment it is spoken — the classic karaoke
word-by-word sweep. Both colours are customizable (`color` = base,
`karaoke_color` = highlight). Also supports emoji injection for
money/cash keywords (uang → 💰).

Caption styling (machine settings): font family, size, and one of
`solid` (outline + shadow), `highlight` (opaque box), `outline` (thick
stroke only). A watermark can be burned in via drawtext at burn time.

OpenShorts hook_v2: professional hook overlays with multiple styles.
"""
import os
import re
import tempfile

from gpu import encode_args
from hooks import build_hook_v2_text
from video import output_fps_args

# Non-speech tags the ASR/caption source can emit ([musik], [tertawa], …).
# Reject whole segments whose text is ONLY such a tag so they never become
# caption blocks.
_HOOK_BURN_NEEDS_BF0 = False
_NOISE_TAG_RE = re.compile(r"^(\s*\[[^\]]*\]\s*)+$")


def reconcile_segment_words(segment):
    """Make a segment's word list cover its full text.

    faster-whisper's word-level timestamps occasionally drop tokens at
    segment boundaries, so a segment can end up with FEWER words than the
    text it describes. Subtitle text is built from the word list, not from
    the segment text, so those drops silently vanish from burned captions.
    When the word list is shorter than the text's own token count, rebuild
    it from the text with evenly-spaced timestamps over the segment span.
    Complete word lists are never touched. (Ported from QuClippers.)
    """
    words = segment.get("words") or []
    text = (segment.get("text") or "").strip()
    if not text or not words:
        return segment
    text_words = text.split()
    if len(words) >= len(text_words):
        return segment
    start = float(segment.get("start", 0) or 0)
    end = float(segment.get("end", start) or start)
    span = max(0.05, end - start)
    step = span / max(1, len(text_words))
    segment["words"] = [
        {
            "word": (" " + w) if i else w,
            "start": round(start + i * step, 3),
            "end": round(start + (i + 1) * step, 3),
        }
        for i, w in enumerate(text_words)
    ]
    return segment


def merge_continuation_words(words):
    """Merge faster-whisper continuation fragments into their base word.

    faster-whisper marks a word boundary with a LEADING SPACE on each token.
    Compound-word fragments (e.g. "-Kanal.", ".200") arrive WITHOUT a leading
    space and belong to the preceding word. We concatenate such fragments onto
    the previous word and extend its end time. Normal words keep their leading
    space, so real word boundaries are never glued together.

    Groq words carry NO leading space, so this only runs when the leading-space
    convention is actually present (i.e. the local faster-whisper path).
    (Ported from QuClippers.)
    """
    merged = []
    for word in words:
        text = str(word.get("word", "") or "")
        if merged and isinstance(text, str) and text and not text.startswith(" "):
            prev = merged[-1]
            prev["word"] = f"{prev.get('word', '')}{text}"
            if word.get("end") is not None:
                prev["end"] = word["end"]
        else:
            merged.append(dict(word))
    return merged


def _sanitize_caption_text(text: str) -> str:
    """Normalize typographic characters that libass can't render into tofu.

    Smart quotes / dashes / ellipses from LLM output (or Whisper) often have
    no glyph in Liberation Sans; map them to plain ASCII so captions never
    show a □ box. Emoji are intentionally left untouched — they need a
    monochrome fallback font (fonts-symbola) instead of a text glyph.

    Also strips characters that have NO glyph in ANY caption font and would
    therefore always render as a tofu box:
      - U+FFFD  replacement char — Whisper/LLM emit this for garbled audio
      - U+00AD  soft hyphen — invisible in text editors, tofu in libass
      - U+200B  zero-width space — same tofu problem
      - U+FEFF  zero-width no-break space / BOM — same
      - U+2011  non-breaking hyphen — maps to a plain hyphen (has a glyph)
    """
    if not text:
        return text
    return (text
            .replace("\ufffd", "")      # replacement char → drop (tofu)
            .replace("\u00ad", "")      # soft hyphen → drop (tofu)
            .replace("\u200b", "")      # zero-width space → drop (tofu)
            .replace("\ufeff", "")      # BOM / ZWNBSP → drop (tofu)
            .replace("\u2011", "-")     # non-breaking hyphen → hyphen
            .replace("\u2018", "'").replace("\u2019", "'")
            .replace("\u201c", '"').replace("\u201d", '"')
            .replace("\u2013", "-").replace("\u2014", "-")
            .replace("\u2026", "...")
            .replace("\u00a0", " "))


def sanitize_caption_text(text: str) -> str:
    """Public wrapper: safe for any caption text before storing/rendering.

    Used by the caption editor, AI autofix and edit-storage paths so tofu
    characters never reach the DB — burn-time `_ass_escape` stays as the
    final safety net.
    """
    return _sanitize_caption_text(text)


def normalize_subtitle_word(value):
    """Clean a transcript word before it reaches a subtitle.

    Strips Whisper/YouTube non-speech tags ([musik], [tertawa], …) and stray
    punctuation-only tokens — they read as broken caption text, especially
    after translation. Numbers and quoted speech are kept verbatim.
    """
    raw = " ".join(str(value or "").split())
    cleaned = re.sub(r"\[[^\]]*\]", "", raw).strip()
    cleaned = re.sub(r"^[\W_]+$", "", cleaned)
    return _sanitize_caption_text(cleaned)


# Fonts must exist on the system running ffmpeg/libass.
# DejaVu Sans ships with ffmpeg Docker images; Inter falls back to it.
FONT_ALIASES = {
    # Liberation fonts ship in the base image and look like Arial/Times.
    "Inter": "Liberation Sans",
    "Arial": "Liberation Sans",
    "Georgia": "Liberation Serif",
    "Impact": "Anton",
    "Courier New": "Liberation Mono",
    # Installed from Google Fonts in the Dockerfile.
    "Anton": "Anton",
    "Bebas Neue": "Bebas Neue",
    "Poppins": "Poppins",
    "Montserrat": "Montserrat",
    "Roboto": "Roboto",
    "Roboto Slab": "Roboto Slab",
}

STYLE_SPECS = {
    # Every preset keeps a black outline/shadow so white or highlighted text
    # remains legible over faces, slides, gameplay, and other busy footage.
    # BorderStyle 1 = outline + shadow, 3 = opaque box (highlight).
    "solid": dict(borderstyle=1, outline=4, shadow=3, margin=120),
    "highlight": dict(borderstyle=3, outline=4, shadow=3, margin=160),
    "outline": dict(borderstyle=1, outline=5, shadow=3, margin=120),
    # KlipAja-style gaming: thick outline + bold shadow, high margin so the
    # caption sits above the gameplay area.
    "gaming": dict(borderstyle=1, outline=6, shadow=3, margin=200),
    # Clean opaque box plus a black drop shadow for separation from footage.
    "box": dict(borderstyle=3, outline=4, shadow=3, margin=140),
}

# Smart emoji map (R9): keyword -> emoji, multi-language. Matching is
# word-based with Indonesian/English affix stripping (uangnya -> uang,
# winning -> win), so "keuangan" does NOT match "uang" the way substring
# matching did. A line can gain up to MAX_EMOJI_PER_LINE emojis.
EMOJI_MAP = {
    # ── Money / finance (id + en) ───────────────────────────────────────
    "uang": "💰", "duit": "💰", "dana": "💰", "money": "💰", "cash": "💰",
    "gaji": "💰", "harga": "💰", "price": "💰", "cost": "💰",
    "bayar": "💳", "pay": "💳", "tagihan": "🧾", "bill": "🧾",
    "untung": "📈", "profit": "📈", "gain": "📈", "naik": "📈", "up": "📈",
    "rugi": "📉", "loss": "📉", "turun": "📉", "down": "📉",
    "kaya": "🤑", "rich": "🤑", "bonus": "🎁", "gift": "🎁",
    # ── Emotion / reactions ─────────────────────────────────────────────
    "api": "🔥", "fire": "🔥", "panas": "🔥", "hot": "🔥", "lit": "🔥",
    "gila": "🤯", "crazy": "🤯", "wow": "🤯", "mindblowing": "🤯",
    "hati": "❤️", "cinta": "❤️", "sayang": "❤️", "love": "❤️",
    "happy": "😄", "senang": "😄", "bahagia": "😄", "bahagiaa": "😄",
    "sedih": "😢", "sad": "😢", "nangis": "😢", "cry": "😢",
    "terharu": "🥹", "touched": "🥹", "lucu": "😂", "funny": "😂",
    "ketawa": "😂", "laugh": "😂", "kaget": "😱", "shock": "😱",
    "terkejut": "😱", "surprise": "😱", "takut": "😨", "scared": "😨",
    "marah": "😡", "angry": "😡", "kesal": "😤", "menang": "🏆",
    "win": "🏆", "juara": "🏆", "champion": "🏆", "gagal": "😞",
    "fail": "😞", "salah": "❌", "wrong": "❌", "benar": "✅", "right": "✅",
    # ── Other popular categories ────────────────────────────────────────
    "makan": "🍽️", "eat": "🍽️", "food": "🍽️", "enak": "😋", "yummy": "😋",
    "sehat": "💪", "healthy": "💪", "kuat": "💪", "strong": "💪",
    "sakit": "🤒", "sick": "🤒", "belajar": "📚", "learn": "📚",
    "study": "📚", "pintar": "🧠", "smart": "🧠", "kerja": "💼", "work": "💼",
    "bisnis": "💼", "business": "💼", "liburan": "✈️", "holiday": "✈️",
    "travel": "✈️", "jalan": "🚶", "walk": "🚶", "mobil": "🚗", "car": "🚗",
    "waktu": "⏰", "time": "⏰", "jam": "🕐", "clock": "🕐",
    "rahasia": "🤫", "secret": "🤫", "ide": "💡", "idea": "💡",
    "coba": "🧪", "try": "🧪", "temukan": "🔍", "find": "🔍",
    "penting": "❗", "important": "❗", "hati-hati": "⚠️", "careful": "⚠️",
    "bahaya": "☠️", "danger": "☠️", "stop": "🛑", "mulai": "▶️", "start": "▶️",
    "selesai": "🏁", "done": "🏁", "dunia": "🌍", "world": "🌍",
    "bintang": "⭐", "star": "⭐", "super": "⭐", "keren": "😎", "cool": "😎",
    "hebat": "👏", "great": "👏", "bagus": "👍", "good": "👍",
    "jelek": "👎", "bad": "👎", "mimpi": "🌙", "dream": "🌙",
    "cahaya": "✨", "light": "✨", "sukses": "🚀", "success": "🚀",
}

# Per-language bonus keywords (R9: "lebih cerdas dengan per-language").
# Keyed by the same codes as LANG_NAMES in translate.py; inject_emoji picks
# the right map via `lang` (falls back to the universal map above).
EMOJI_LANG_MAPS = {
    "id": {
        "duit": "💰", "gocap": "💰", "receh": "💰", "cuan": "💰",
        "jualan": "💼", "dagang": "💼", "belanja": "🛒", "borong": "🛒",
        "murah": "🏷️", "mahal": "💎", "promo": "🎉", "diskon": "🏷️",
        "gaul": "😎", "kece": "😎", "gokil": "🤪", "ngakak": "😂",
        "gabut": "😴", "capek": "😮💨", "lelah": "😮💨", "penasaran": "🤔",
        "insaf": "😇", "syukur": "🙏", "berkah": "🙏", "amin": "🙏",
        "hati-hati": "⚠️", "awas": "⚠️", "waspada": "⚠️",
        "mantap": "👍", "top": "👌", "gacor": "📈", "viral": "📈",
        "viral": "📈", "fyp": "📈", "trending": "📈",
        "seru": "🎢", "asik": "🎉", "rame": "🎉", "ramai": "🎉",
        "sunyi": "🤫", "sepi": "🤫", "nyaman": "😌", "enak": "😋",
        "kenyang": "😋", "lupa": "😵", "lupa-lupa": "😵",
        "bangun": "🌅", "tidur": "😴", "ngantuk": "🥱", "mengantuk": "🥱",
    },
    "ms": {
        "duit": "💰", "wang": "💰", "harga": "💰", "jual": "💼",
        "cinta": "❤️", "sayang": "❤️", "gembira": "😄", "sedih": "😢",
        "marah": "😡", "takut": "😨", "menang": "🏆", "kalah": "😞",
        "makan": "🍽️", "sedap": "😋", "belajar": "📚", "kerja": "💼",
        "hebat": "👏", "bagus": "👍", "cantik": "✨", "tampan": "😎",
    },
    "en": {
        "money": "💰", "cash": "💰", "rich": "🤑", "profit": "📈",
        "loss": "📉", "fire": "🔥", "love": "❤️", "happy": "😄",
        "sad": "😢", "funny": "😂", "shock": "😱", "scared": "😨",
        "angry": "😡", "win": "🏆", "fail": "😞", "wrong": "❌",
        "right": "✅", "food": "🍽️", "yummy": "😋", "healthy": "💪",
        "sick": "🤒", "learn": "📚", "smart": "🧠", "work": "💼",
        "business": "💼", "holiday": "✈️", "travel": "✈️", "car": "🚗",
        "time": "⏰", "secret": "🤫", "idea": "💡", "try": "🧪",
        "find": "🔍", "important": "❗", "careful": "⚠️", "danger": "☠️",
        "stop": "🛑", "start": "▶️", "done": "🏁", "world": "🌍",
        "star": "⭐", "super": "⭐", "cool": "😎", "great": "👏",
        "good": "👍", "bad": "👎", "dream": "🌙", "light": "✨",
        "success": "🚀",
    },
    "zh": {
        "钱": "💰", "金": "💰", "富": "🤑", "爱": "❤️", "心": "❤️",
        "火": "🔥", "好": "👍", "坏": "👎", "笑": "😂", "哭": "😢",
        "怕": "😨", "怒": "😡", "胜": "🏆", "吃": "🍽️", "学": "📚",
        "工": "💼", "梦": "🌙", "光": "✨", "成功": "🚀", "星": "⭐",
    },
    "ja": {
        "お金": "💰", "金": "💰", "愛": "❤️", "恋": "❤️", "火": "🔥",
        "笑": "😂", "泣": "😢", "怖": "😨", "怒": "😡", "勝": "🏆",
        "食": "🍽️", "学": "📚", "仕事": "💼", "夢": "🌙", "光": "✨",
        "成功": "🚀", "星": "⭐",
    },
    "ko": {
        "돈": "💰", "돈": "💰", "사랑": "❤️", "불": "🔥", "웃음": "😂",
        "울음": "😢", "무서": "😨", "화": "😡", "승리": "🏆", "음식": "🍽️",
        "공부": "📚", "일": "💼", "꿈": "🌙", "빛": "✨", "성공": "🚀", "별": "⭐",
    },
    "es": {
        "dinero": "💰", "plata": "💰", "rico": "🤑", "amor": "❤️",
        "fuego": "🔥", "risa": "😂", "llorar": "😢", "miedo": "😨",
        "ganar": "🏆", "comer": "🍽️", "estudiar": "📚", "trabajo": "💼",
        "sueño": "🌙", "luz": "✨", "éxito": "🚀", "estrella": "⭐",
    },
    "ar": {
        "مال": "💰", "حب": "❤️", "نار": "🔥", "ضحك": "😂", "بكاء": "😢",
        "خوف": "😨", "غضب": "😡", "فوز": "🏆", "أكل": "🍽️", "عمل": "💼",
        "حلم": "🌙", "ضوء": "✨", "نجاح": "🚀", "نجم": "⭐",
    },
}

# Indonesian suffixes stripped before keyword lookup (uangnya -> uang).
_ID_SUFFIXES = ("nya", "ku", "mu", "kah", "lah", "pun", "an", "kan")
# English suffixes (winning -> win, prices -> price).
_EN_SUFFIXES = ("ing", "ed", "es", "s")

MAX_EMOJI_PER_LINE = 2


def _strip_suffix(word: str, suffixes) -> str:
    """Strip one trailing suffix; if it leaves a doubled consonant (winning
    -> winn), collapse it to the single-letter stem (win)."""
    for suf in suffixes:
        if len(word) - len(suf) >= 3 and word.endswith(suf):
            stem = word[:-len(suf)]
            if len(stem) >= 3 and stem[-1] == stem[-2]:
                stem = stem[:-1]
            return stem
    return word


def _emoji_for_word(word: str, emoji_map: dict) -> str | None:
    """Map a single word to an emoji, trying exact then affix-stripped stem."""
    w = word.strip().lower()
    if not w:
        return None
    if w in emoji_map:
        return emoji_map[w]
    # Indonesian: uangnya -> uang, gajiin -> gaji
    id_stem = _strip_suffix(w, _ID_SUFFIXES)
    if id_stem != w and id_stem in emoji_map:
        return emoji_map[id_stem]
    # English: winning -> win, prices -> price
    en_stem = _strip_suffix(w, _EN_SUFFIXES)
    if en_stem != w and en_stem in emoji_map:
        return emoji_map[en_stem]
    return None


# CJK / Thai etc. don't use spaces, so per-word matching can't see keywords.
# For those languages fall back to substring matching (keywords are single
# ideographs, so false positives are rare).
_NO_SPACE_LANGS = {"zh", "ja", "ko", "th"}


def inject_emoji(text: str, lang: str = "") -> str:
    """Append up to MAX_EMOJI_PER_LINE relevant emojis for the line's keywords.

    Word-based (no substring false positives) with Indonesian/English affix
    stripping. `lang` ("id", "en", "zh", …) adds language-specific keywords;
    empty uses only the universal map. CJK langs fall back to substring match
    since they don't use spaces.
    """
    emoji_map = dict(EMOJI_MAP)
    if lang and lang in EMOJI_LANG_MAPS:
        emoji_map.update(EMOJI_LANG_MAPS[lang])
    found = []
    lowered = text.lower()
    if lang in _NO_SPACE_LANGS:
        for keyword, emoji in emoji_map.items():
            if keyword in lowered and emoji not in found:
                found.append(emoji)
            if len(found) >= MAX_EMOJI_PER_LINE:
                break
    else:
        for word in text.split():
            e = _emoji_for_word(word, emoji_map)
            if e and e not in found:
                found.append(e)
            if len(found) >= MAX_EMOJI_PER_LINE:
                break
    if not found:
        return text
    return f"{text} {' '.join(found)}"

ANIMATION_TAGS = {
    "fade": r"{\fad(100,0)}",
    "grow": r"{\fscx78\fscy78\t(0,200,\fscx110\fscy110)}",
    "drop": r"{\fscx115\fscy40\t(0,220,\fscx100\fscy100)}",
    "slide-left": r"{\fscx40\fscy110\t(0,220,\fscx100\fscy100)}",
    "slide-right": r"{\fscx40\fscy110\t(0,220,\fscx100\fscy100)}",
    "pop": r"{\fscx120\fscy120\t(0,150,\fscx100\fscy100)}",
    "bounce": r"{\fscy60\t(0,120,\fscy115)\t(120,200,\fscy95)\t(200,260,\fscy105)\t(260,300,\fscy100)}",
    "shake": r"{\frz-3\t(0,50,\frz3)\t(50,100,\frz-2)\t(100,150,\frz2)\t(150,200,\frz-1)\t(200,250,\frz0)}",
    "glow": r"{\1a&H00&\3a&H00&\t(0,150,\1a&H40&\3a&H40&)}",
}


def default_color() -> str:
    """Base caption colour (unspoken text). Defaults to white — the classic
    readable subtitle look; the spoken word highlights via karaoke colour."""
    return os.environ.get("CAPTION_COLOR", "#FFFFFF").lstrip("#")


def default_karaoke_color() -> str:
    """Highlight colour the spoken word sweeps to in karaoke mode.

    Defaults to amber (#FFE500): white text -> yellow on the spoken word.
    """
    return os.environ.get("KARAOKE_COLOR", "#FFE500").lstrip("#")


def _ass_color(rgb: str) -> str:
    """Convert a #RRGGBB hex to ASS's &HAABBGGRR& (alpha 00 = opaque).

    ASS stores colour as AABBGGRR; Klipin's hex is RRGGBB. Writing the hex
    verbatim (e.g. `&HFFE500&`) makes alpha = FF = FULLY TRANSPARENT, which
    is why burned captions could look "missing". Ported from QuClippers'
    `hex_to_ass_color`. Malformed colours fall back to opaque white.
    """
    c = (rgb or "").lstrip("#")
    if len(c) != 6 or any(ch not in "0123456789abcdefABCDEF" for ch in c):
        return "&H00FFFFFF&"
    return f"&H00{c[4:6]}{c[2:4]}{c[0:2]}&"


def _phrase_blocks(clip_words: list, max_words: int = 8,
                   pause: float = 0.5) -> list:
    """Group consecutive clip words into sentence/phrase blocks (1 block = 1 line).

    Blocks break at:
      1. Speaker changes (speaker-aware boundary cut for debate/multi-speaker)
      2. Long pauses (> `pause`s — natural sentence boundaries)
      3. Terminal punctuation (?, !, ., ...) for clean conversational pacing
      4. `max_words` cap to prevent crowded on-screen lines

    Timing stays word-locked: a block starts at its first word and ends at
    its last word (no artificial padding or delay), so captions match exactly
    what is spoken — accurate, professional subtitle sync.
    """
    blocks = []
    cur = []
    prev_end = None
    prev_speaker = None
    # ASR providers occasionally return a word whose end overlaps the next
    # word. Sort once so phrase ordering is deterministic; event boundaries
    # are made non-overlapping in `_karaoke_block_events` below.
    sorted_words = sorted(clip_words, key=lambda item: (
        float(item.get("start", 0) or 0), float(item.get("end", 0) or 0)))

    for w in sorted_words:
        s = float(w.get("start", 0) or 0)
        spk = w.get("speaker")
        
        # 1. Break on speaker change (debate / rapid turn-taking)
        speaker_changed = (
            cur and spk is not None and prev_speaker is not None and spk != prev_speaker
        )
        # 2. Break on acoustic pause
        pause_exceeded = (
            cur and prev_end is not None and (s - prev_end) > pause
        )
        # 3. Break on terminal punctuation of previous word
        punct_break = False
        if cur:
            prev_w_text = str(cur[-1].get("word", "")).strip()
            if prev_w_text.endswith(("?", "!", ".", "…", "...")):
                punct_break = True

        if speaker_changed or pause_exceeded or punct_break:
            blocks.append(cur)
            cur = []

        cur.append(w)
        e = float(w.get("end", s) or s)
        prev_end = e if prev_end is None else max(prev_end, e)
        prev_speaker = spk

        if len(cur) >= max_words:
            blocks.append(cur)
            cur = []
            prev_end = None

    if cur:
        blocks.append(cur)
    return blocks


def _ass_escape(text: str) -> str:
    text = _sanitize_caption_text(text)
    return text.replace("{", "(").replace("}", ")").replace("\n", " ")


def seconds_to_ass(t: float) -> str:
    # a whole second (e.g. 1.999 -> 2.00) can never emit an invalid
    # 3-digit centisecond field like ".100" in the ASS timestamp.
    t = max(0.0, t)
    total_cs = int(round(t * 100))
    h, rem = divmod(total_cs, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _caption_sync_offset() -> float:
    """Optional calibration applied equally to caption entry and karaoke.

    Word timestamps are already aligned to the source audio. The historical
    +80 ms default made every caption visibly late after accurate re-encoding,
    so production now preserves the original timestamp unless an operator has
    measured a provider-specific offset and sets it explicitly.
    """
    try:
        return max(-0.5, min(0.5, float(os.environ.get(
            "CAPTION_SYNC_OFFSET", "0"))))
    except (TypeError, ValueError):
        return 0.0


def _word_anim_tag(anim: str, dur_ms: int) -> str:
    """Per-word entry animation (CapCut-style pop/fade) as ASS inline tags.

    Applied to the FIRST word of each line so the new line "pops" in,
    exactly like CapCut's per-word pop: each new word scales/fades in as
    it is spoken. The karaoke sweep ({\\k}) is preserved on every word, so
    coverage is never reduced — only the visual entry of the line changes.
    Returns "" when the animation is line-level only (none/karaoke).
    """
    if anim == "pop":
        return r"{\fscx118\fscy118\t(0,%d,\fscx100\fscy100)}" % min(160, max(60, dur_ms // 2))
    if anim == "fade":
        return r"{\fad(%d,0)}" % min(200, max(80, dur_ms))
    if anim == "grow":
        return r"{\fscx80\fscy80\t(0,%d,\fscx100\fscy100)}" % min(200, max(80, dur_ms))
    if anim == "bounce":
        t1, t2, t3, t4 = min(100, dur_ms // 4), min(160, dur_ms // 2), min(220, dur_ms * 3 // 4), min(280, dur_ms)
        return r"{\fscy60\t(0,%d,\fscy115)\t(%d,%d,\fscy95)\t(%d,%d,\fscy105)\t(%d,%d,\fscy100)}" % (t1, t1, t2, t2, t3, t3, t4)
    if anim == "shake":
        t1, t2, t3, t4 = min(50, dur_ms // 5), min(100, dur_ms // 2), min(150, dur_ms * 3 // 5), min(200, dur_ms * 4 // 5)
        return r"{\frz-3\t(0,%d,\frz3)\t(%d,%d,\frz-2)\t(%d,%d,\frz2)\t(%d,%d,\frz-1)\t(%d,%d,\frz0)}" % (t1, t1, t2, t2, t3, t3, t4, t4, dur_ms)
    if anim == "glow":
        return r"{\1a&H00&\3a&H00&\t(0,%d,\1a&H40&\3a&H40&)}" % min(200, max(80, dur_ms))
    if anim == "drop":
        return r"{\fscx115\fscy40\t(0,%d,\fscx100\fscy100)}" % min(220, max(80, dur_ms))
    if anim == "slide-left":
        return r"{\fscx40\fscy110\t(0,%d,\fscx100\fscy100)}" % min(220, max(80, dur_ms))
    return ""


def _karaoke_block_events(blocks: list, cs: float, ce: float, emoji: bool,
                          lang: str = "", edits: dict | None = None,
                          anim: str = "", position_y: int = 73):
    """Build ASS karaoke events for pre-grouped phrase blocks.

    Each block becomes ONE Dialogue that starts at its first word and ends at
    its last (word-locked). Every word carries a `{\\kNN}` tag so libass
    sweeps PrimaryColour over the spoken word while the rest of the line
    stays dim (SecondaryColour) — the classic karaoke sweep INSIDE a natural
    sentence/phrase line. `anim` is an optional line-level entry effect
    (fade/pop/grow/…) applied in front of the sweep.

    A block whose SOURCE start matches an `edits` key is rendered as a plain
    (non-karaoke) line with the edited text — typo fixes replace the sweep.
    """
    events = []
    try:
        position_y = max(10, min(90, int(position_y)))
    except (TypeError, ValueError):
        position_y = 73
    sync_offset = _caption_sync_offset()
    # Two independent subtitle lanes keep a debate readable. WebVTT permits
    # overlapping cues; ASS can render the same timing safely on distinct
    # layers/positions. The old single global end cursor delayed the second
    # speaker until its cue had already ended, silently dropping the line.
    lane_ends = [cs, cs]
    for block_index, block in enumerate(blocks):
        if not block:
            continue
        raw_start = float(block[0].get("start", cs))
        desired_start = max(cs, raw_start + sync_offset)
        line_end = min(ce, float(block[-1].get(
            "end", raw_start + 0.5)) + sync_offset)
        free_lanes = [index for index, lane_end in enumerate(lane_ends)
                      if lane_end <= desired_start + 0.02]
        lane = free_lanes[0] if free_lanes else min(
            range(len(lane_ends)), key=lane_ends.__getitem__)
        line_start = max(desired_start, lane_ends[lane])
        if line_end <= line_start:
            continue
        lane_y = position_y if lane == 0 else max(10, position_y - 12)
        position_tag = (
            f"{{\\an2\\pos(540,{round(1920 * lane_y / 100)})}}")
        edited = (edits or {}).get(f"{raw_start:.2f}")
        if edited:
            text = _ass_escape(edited)
            if emoji:
                text = inject_emoji(text, lang)
            events.append(
                f"Dialogue: {lane},{seconds_to_ass(line_start - cs)},"
                f"{seconds_to_ass(line_end - cs)},"
                f"Caption,,0,0,0,,{position_tag}{anim}{text}"
            )
            lane_ends[lane] = line_end
            continue
        parts = []
        for j, w in enumerate(block):
            raw_word_start = float(w.get("start", raw_start))
            raw_word_end = float(w.get("end", raw_word_start + 0.2))
            w_start = raw_word_start + sync_offset
            w_end = raw_word_end + sync_offset
            # ASS karaoke is cumulative: the next word starts only after all
            # previous \k durations. Therefore each duration must reach the
            # NEXT word's actual start, including natural inter-word silence.
            # Using only (word.end - word.start) made every following highlight
            # progressively earlier than the speech.
            if j + 1 < len(block):
                next_word_start = float(block[j + 1].get(
                    "start", w_end - sync_offset)) + sync_offset
                karaoke_end = max(w_start + 0.01, next_word_start)
            else:
                karaoke_end = max(w_start + 0.01, line_end)
            k_dur_cs = max(1, int(round((karaoke_end - w_start) * 100)))
            text = _ass_escape(str(w.get("word", "")))
            if emoji:
                text = inject_emoji(text, lang)
            sep = " " if j > 0 else ""
            entry = _word_anim_tag(anim, k_dur_cs) if j == 0 else ""
            parts.append(f"{sep}{entry}{{\\k{k_dur_cs}}}{text}")
        events.append(
            f"Dialogue: {lane},{seconds_to_ass(line_start - cs)},"
            f"{seconds_to_ass(line_end - cs)},"
            f"Caption,,0,0,0,,{position_tag}{''.join(parts)}"
        )
        lane_ends[lane] = line_end
    return events


def _back_colour(bg: str, bg_color: str = "") -> str:
    """ASS BackColour (alpha BBGGRR) for the caption background box/shadow.

    ASS alpha: 00 = opaque, FF = fully transparent.
    `bg_color` (hex like "#FFE500") overrides the alpha channel of the
    background so users can pick any colour for the subtitle background box.
    Presets: `solid` = opaque black, `glass` = ~50% translucent black,
    `none` = fully transparent (no box, stroke/shadow only).
    """
    if bg_color:
        col = _ass_color(bg_color.lstrip("#"))
        # col is BBGGRR hex; prepend a 50% alpha so the box is visible.
        return "&H80" + col[2:] if col.startswith("&H") else col
    if bg == "glass":
        return "&H80000000&"
    if bg == "none":
        return "&HFF000000&"
    return "&H00000000&"


def _all_words(transcript: dict) -> list:
    """Flatten every transcript segment into one word list with global timing.

    Mirrors the exact word-prep logic build_ass used to do inline: reconcile
    segment word lists, honor the faster-whisper leading-space convention, and
    fall back to even word distribution when the ASR returns no word times.
    """
    lines = transcript.get("segments") or []
    # Only merge continuation fragments when the ORIGINAL words carry the
    # faster-whisper leading-space convention (local whisper path); Groq
    # words carry no leading space and must never be glued together.
    has_leading_space = any(
        str(w.get("word", "") or "").startswith(" ")
        for seg in lines
        for w in (seg.get("words") or [])
    )
    # Rebuild segments whose word lists are shorter than their text so every
    # spoken word reaches the caption (Ported from QuClippers).
    for seg in lines:
        reconcile_segment_words(seg)
    # Build word list with global timing
    words = []
    for seg in lines:
        for w in seg.get("words") or []:
            words.append(w)
    if has_leading_space:
        words = merge_continuation_words(words)
    if not words:
        # Groq returns no per-word times — estimate by distributing each
        # segment's words evenly across it (smooth word-level karaoke).
        for seg in lines:
            s0, s1 = seg.get("start", 0), seg.get("end", 0)
            if s1 <= s0:
                continue
            seg_words = (seg.get("text") or "").split()
            if not seg_words:
                continue
            step = (s1 - s0) / len(seg_words)
            for i, w in enumerate(seg_words):
                words.append({"word": w, "start": s0 + i * step,
                              "end": s0 + (i + 1) * step})
        if not words:
            words = [
                {"word": seg.get("text", "").strip(),
                 "start": seg.get("start", 0),
                 "end": seg.get("end", seg.get("start", 0) + 2)}
                for seg in lines
            ]
    return words


def _clip_words(transcript: dict, clip: dict) -> list:
    """Words that fall inside a clip's window, normalized for captions."""
    cs, ce = clip["start"], clip["end"]
    try:
        lead = float(os.environ.get("CAPTION_LEAD", "0.0"))
    except Exception:
        lead = 0.0
    clip_words = []
    for w in _all_words(transcript):
        # Include a word that is already being spoken when the clip starts;
        # start-only filtering dropped that first word.  Require real time
        # overlap (with a small operator-configurable lead) so a preceding
        # word that ended at the boundary cannot leak into the clip.
        ws = float(w.get("start", 0) or 0)
        we = float(w.get("end", ws) or ws)
        if we < cs - lead or ws > ce:
            continue
        word = normalize_subtitle_word(w.get("word", ""))
        if not word:
            continue
        cw = dict(w)
        # Clamp a crossing word to the clip window so karaoke begins at 0,
        # while preserving its original end for accurate duration.
        cw["start"] = max(ws, cs)
        cw["end"] = min(max(we, ws + 0.01), ce)
        cw["word"] = word
        clip_words.append(cw)
    return clip_words


def clip_caption_blocks(transcript: dict, clip: dict, emoji: bool = True,
                        lang: str = "", words_per_line: int = 3) -> list:
    """Compute the exact caption phrase-blocks that WILL be burned for a clip.

    Returns [{start, end, text}] in SOURCE time — the same block list
    build_ass renders into .ass, so the editor and the burner always agree.
    """
    cs, ce = clip["start"], clip["end"]
    wpl = max(1, int(words_per_line or 3))
    blocks = _phrase_blocks(_clip_words(transcript, clip), max_words=wpl,
                            pause=0.5)
    out = []
    lane_ends = [cs, cs]
    for block in blocks:
        raw_b_start = float(block[0].get("start", cs))
        sync_offset = _caption_sync_offset()
        desired_start = max(cs, raw_b_start + sync_offset)
        b_end = min(ce, float(block[-1].get(
            "end", raw_b_start + 0.4)) + sync_offset)
        free_lanes = [index for index, lane_end in enumerate(lane_ends)
                      if lane_end <= desired_start + 0.02]
        lane = free_lanes[0] if free_lanes else min(
            range(len(lane_ends)), key=lane_ends.__getitem__)
        b_start = max(desired_start, lane_ends[lane])
        if b_end <= b_start:
            b_end = min(ce, b_start + 0.4)
        text = " ".join(str(w.get("word", "")) for w in block).strip()
        if not text:
            continue
        if emoji:
            text = inject_emoji(text, lang)
        out.append({"start": b_start, "end": b_end, "text": text,
                    "lane": lane})
        lane_ends[lane] = b_end
    return out


def build_ass(transcript: dict, clips: list, work_dir: str,
              animation: str = "none", color: str | None = None,
              karaoke_color: str | None = None,
              emoji: bool = True, font: str = "Montserrat", size: int = 84,
              style: str = "solid", bg: str = "solid", bg_color: str | None = None,
              log=None, emoji_lang: str = "", edits: dict | None = None,
              words_per_line: int = 3, anim: str = "",
              enabled: bool = True, position_y: int = 73) -> dict:
    """Generate one .ass per clip, word-level timed. Returns {clip_index: path}.

    `bg` controls the caption background box/shadow: 'solid' | 'glass' | 'none'.
    `bg_color` (optional) overrides the background color.
    `words_per_line` controls how many words are grouped into one caption
    line.
    """
    color = (color or default_color()).lstrip("#")
    karaoke_color = (karaoke_color or default_karaoke_color()).lstrip("#")
    anim = animation if not anim else anim
    font = FONT_ALIASES.get(font, "Montserrat")
    spec = STYLE_SPECS.get(style, STYLE_SPECS["solid"])
    back = _back_colour(bg, bg_color or "")

    # Karaoke: unspoken words stay in the BASE colour (white); the word
    # being spoken sweeps to the HIGHLIGHT colour (default yellow) — the
    # classic white->yellow karaoke word highlight. {\k} sweeps
    # SecondaryColour -> PrimaryColour, so Secondary = base, Primary =
    # highlight. Both colours are customizable.
    # RULE: a transcript with only 1-2 words total is too short to show a
    # sweep; render static (PrimaryColour).
    # NOTE: transcripts from Groq/Deepgram carry words inside segments[]
    # (transcript["segments"][i]["words"]), NOT a top-level "words" key —
    # counting transcript.get("words") always yielded 0, which forced
    # primary=secondary and silently disabled ALL karaoke highlighting.
    primary = _ass_color(karaoke_color)
    secondary = _ass_color(color)
    if len(_all_words(transcript)) <= 2:
        primary = secondary  # Static: no highlight effect, just base color.

    ass = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n\n"
        "[V4+ Styles]\n"
        f"Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        f"OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        f"ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        f"Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Caption, {font}, {size}, {primary}, {secondary}, &H000000&, "
        f"{back}, -1, 0, 0, 0, 100, 100, -0.4, 0, "
        f"{spec['borderstyle']}, {spec['outline']}, {spec['shadow']}, 2, 60, 60, "
        f"180, 1\n\n[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text\n"
    )

    results = {}
    for idx, clip in enumerate(clips):
        cs, ce = clip["start"], clip["end"]
        clip_words = _clip_words(transcript, clip)
        # edits: {clip_index: {block_source_start: text}} for multi-clip
        # renders, or a flat {block_source_start: text} dict (single clip).
        if isinstance(edits, dict) and any(isinstance(k, int) for k in edits):
            clip_edits = edits.get(idx) or {}
        else:
            clip_edits = edits or {}
        # Group words into lines of `words_per_line`; pauses still break the
        # line early so a sentence boundary is never glued into the next line.
        wpl = max(1, int(words_per_line or 3))
        blocks = _phrase_blocks(clip_words, max_words=wpl, pause=0.5)
        events = [] if not enabled else _karaoke_block_events(
            blocks, cs, ce, emoji, lang=emoji_lang, edits=clip_edits,
            anim=anim, position_y=position_y)
        meta = [
            (max(cs, float(block[0].get("start", cs))),
             min(ce, float(block[-1].get("end",
                   max(cs, float(block[0].get("start", cs))) + 0.5))),
             clip_edits.get(
                 f"{max(cs, float(block[0].get('start', cs))):.2f}",
                 " ".join(str(w.get("word", "")) for w in block)))
            for block in blocks
        ]
        path = os.path.join(work_dir, f"clip_{idx + 1}.ass")
        with open(path, "w", encoding="utf-8") as f:
            f.write(ass + "\n".join(events))
        results[idx] = path
        if log:
            log(f"📝 CLIP {idx + 1}: {len(meta) if enabled else 0} caption blocks "
                f"({'karaoke' if enabled else 'subtitle nonaktif'} · "
                f"{wpl} kata/baris · {animation or 'none'} entry)")
            if enabled:
                for s, e, t in meta[:10]:
                    log(f"   ⏱️ [{s:.2f}s → {e:.2f}s] {t[:90]}")
                if len(meta) > 10:
                    log(f"   … +{len(meta) - 10} blok lagi")
    log and log(f"✅ {len(results)} subtitle files generated (karaoke · "
                f"{animation or 'none'} entry animation)")
    return results


def _drawtext_escape(text: str) -> str:
    """Escape a string for ffmpeg drawtext filter syntax.

    The text value is wrapped in single quotes (text='...'). Two layers parse
    the string and each needs its own escape:
      1. filterchain parser splits options on ':' even INSIDE single quotes,
         so a literal colon must be written '\\:' — an unescaped ':' in the
         middle of a hook line ("GAGAL: PELATIH") made the whole graph fail
         with "No option name near ...".
      2. FFmpeg single-quoted strings do NOT honour backslash escapes for the
         quote itself — embed a literal apostrophe by DOUBLING it ('').
    """
    return text.replace(":", "\\:").replace("'", "''")


def _safe_hex(value: str, default: str = "#FFFFFF") -> str:
    """Return a drawtext-safe six-digit colour supplied by the editor."""
    raw = str(value or "").strip()
    if re.fullmatch(r"#[0-9a-fA-F]{6}", raw):
        return raw.upper()
    return default


def safe_caption_position(video_path: str, requested: int = 73) -> int:
    """Choose a Reels-safe subtitle band that avoids detected faces."""
    import cv2
    from detector import face_boxes
    try:
        requested = max(18, min(82, int(requested)))
    except (TypeError, ValueError):
        requested = 73
    cap = cv2.VideoCapture(video_path)
    count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    face_bands = []
    for frac in (0.18, 0.34, 0.50, 0.66, 0.82):
        if count > 1:
            cap.set(cv2.CAP_PROP_POS_FRAMES, int((count - 1) * frac))
        ok, frame = cap.read()
        if not ok or frame is None:
            continue
        h, w = frame.shape[:2]
        scale = min(1.0, 640.0 / max(1, w))
        probe = cv2.resize(frame, (int(w * scale), int(h * scale))) \
            if scale < 1 else frame
        for _cx, cy, _fw, fh in face_boxes(probe):
            ph = max(1, probe.shape[0])
            face_bands.append(((cy - fh * 0.65) / ph * 100,
                               (cy + fh * 0.75) / ph * 100))
    cap.release()
    if not face_bands:
        return requested
    candidates = list(dict.fromkeys([requested, 73, 62, 54, 80]))

    def penalty(y):
        band_top, band_bottom = y - 5.5, y + 5.5
        overlap = sum(max(0.0, min(band_bottom, bottom) -
                          max(band_top, top)) for top, bottom in face_bands)
        return overlap * 100 + abs(y - requested)

    return min(candidates, key=penalty)


def apply_safe_caption_position(ass_path: str, video_path: str, log=None) -> int:
    """Rewrite ASS position tags so downloaded subtitles match the burn."""
    if os.environ.get("SAFE_ZONE_AUTO", "1") != "1":
        return -1
    with open(ass_path, "r", encoding="utf-8-sig") as fh:
        body = fh.read()
    match = re.search(r"\\pos\(540,(\d+)\)", body)
    if not match:
        return -1
    requested = round(int(match.group(1)) / 1920 * 100)
    safe = safe_caption_position(video_path, requested)
    if safe != requested:
        delta_px = round(1920 * (safe - requested) / 100)
        def shift_lane(match):
            shifted = max(192, min(1728, int(match.group(1)) + delta_px))
            return rf"\pos(540,{shifted})"
        body = re.sub(r"\\pos\(540,(\d+)\)", shift_lane, body)
        with open(ass_path, "w", encoding="utf-8") as fh:
            fh.write(body)
        log and log(f"🛡️ Safe-zone: subtitle moved {requested}% → {safe}%")
    return safe


def burn_subtitles(video_path: str, ass_path: str, out_path: str, log=None,
                   watermark: str = "", title_overlay: str = "",
                   title_color: str = "#FFFFFF", title_position_y: int = 14,
                   hook_overlay: str = "", hook_v2: dict = None,
                   free_tier: bool = False) -> str:
    """Burn an ASS file into a video via libass, optionally adding a watermark,
    a short title overlay (fades in, holds ~2s, fades out) at the top, and a
    prominent HOOK overlay (big uppercase text, fades in/out — the QuClippers
    `hooks.py` behaviour, free via drawtext).

    OpenShorts hook_v2: If provided, renders a professional hook overlay image
    with multiple styles (classic/dark/yellow/red/outline/outline_yellow),
    emoji support, and pixel-perfect wrapping.

    When free_tier=True, a semi-transparent 'Klip-in' branding watermark is
    burned in the CENTER of the video (for free / Mulai plan users).

    The ass filter runs with cwd set to the subtitle's directory and receives
    only the bare filename — embedding an absolute path in the filter string
    breaks on Windows (backslashes are filter escapes, drive-letter colons
    split options), which is a known ffmpeg pitfall.
    """
    import subprocess
    import os
    import tempfile
    from video import _ffmpeg_executable, get_resolution
    log and log("Burning subtitles...")
    drawtext_font = "font='Montserrat'"
    for font_path in (
        "/usr/local/share/fonts/klipin/Montserrat-Bold.ttf",
        "/usr/local/share/fonts/openshorts/Montserrat-Bold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "/mnt/c/Windows/Fonts/arialbd.ttf",
    ):
        if os.path.isfile(font_path):
            # FFmpeg filter paths require an escaped drive-letter colon even
            # when argv itself is passed without a shell.
            escaped_font = font_path.replace("\\", "/").replace(":", "\\:")
            drawtext_font = f"fontfile='{escaped_font}'"
            break
    apply_safe_caption_position(ass_path, video_path, log=log)
    # Do not trust the source stream's sample-aspect metadata.  A broken
    # upstream pass produced 720x1280 pixels with SAR 256:81, which players
    # correctly displayed as 16:9.  Captions are the final mandatory video
    # pass, so square the pixels here as well as in download/export scaling.
    fps_args = output_fps_args(video_path)
    fps_rate = fps_args[fps_args.index("-r") + 1]
    # Scene-aware clips are joined losslessly before this final encode. MP4
    # segments may carry encoder-delay/discontinuous timestamps even when all
    # pixels are valid. Rebuild video PTS from the CFR frame count so a bad
    # concat boundary cannot reach movenc as an unsupported PTS/DTS pair.
    # Audio gets the equivalent sample-count clock below; both start at zero.
    filters = [f"setpts=N/(({fps_rate})*TB)", "setsar=1",
               f"ass={os.path.basename(ass_path)}"]
    # Free-tier center watermark: semi-transparent 'Klip-in' branding
    if free_tier:
        log and log("Watermark: Klip-in (free tier)")
        filters.append(
            f"drawtext=text='Klip-in':"
            f"fontsize=42:fontcolor=white@0.25:borderw=2:bordercolor=black@0.3:"
            f"x=(w-tw)/2:y=(h-th)/2"
        )
    if hook_overlay.strip():
        log and log(f"🎣 Hook overlay: {hook_overlay.strip()[:60]}")
        hook = hook_overlay.strip().upper()[:70]
        filters.append(
            f"drawtext=text='{_drawtext_escape(hook)}':"
            f"{drawtext_font}:fontsize=54:fontcolor=#FFE500@0.98:borderw=5:"
            f"bordercolor=black@0.9:shadowcolor=black@0.6:shadowx=3:shadowy=3:"
            f"x=(w-text_w)/2:y=(h*0.13):"
            f"alpha='if(lt(t,0.7),t/0.7,if(lt(t,3.4),1,if(lt(t,4.2),(4.2-t)/0.8,0)))'"
        )
    if title_overlay.strip():
        log and log(f"🏷️ Title overlay: {title_overlay.strip()[:60]}")
        try:
            title_position_y = max(8, min(85, int(title_position_y)))
        except (TypeError, ValueError):
            title_position_y = 14
        # Hook occupies the upper headline band. Keep a simultaneous title in
        # a smaller eyebrow band instead of stacking both around 10-14%.
        if hook_overlay.strip() and title_position_y <= 18:
            title_position_y = 7
        title_color = _safe_hex(title_color)
        filters.append(
            f"drawtext=text='{_drawtext_escape(title_overlay.strip())}':"
            f"{drawtext_font}:fontsize=42:fontcolor={title_color}@0.96:"
            f"borderw=3:bordercolor=black@0.85:"
            f"x=(w-text_w)/2:y=(h*{title_position_y}/100)-(text_h/2):"
            f"alpha='if(lt(t,0.8),t/0.8,if(lt(t,3),1,if(lt(t,3.8),(3.8-t)/0.8,0)))'"
        )
    if watermark.strip():
        log and log(f"🏷️ Watermark: {watermark.strip()}")
        filters.append(
            f"drawtext=text='{_drawtext_escape(watermark.strip())}':"
            f"fontsize=30:fontcolor=white@0.35:borderw=1:bordercolor=black@0.4:"
            f"x=w-tw-36:y=h-th-36"
        )
    # OpenShorts hook_v2: professional image overlay. Mirrors OpenShorts
    # `add_hook_to_video`: the overlay PNG is passed as a SECOND `-i` input and
    # composited with `[0:v][1:v]overlay=x:y` — NOT via a `movie=` source inside
    # the filter graph (that requires a complex graph and glues an extra input
    # into `-vf`, which ffmpeg rejects with "expected 1 input/1 output").
    hook_img = None
    overlay_body = None
    if hook_v2:
        hook_text = build_hook_v2_text(hook_v2)
        hook_style = hook_v2.get("style", "classic")
        try:
            hook_duration = max(0.5, float(hook_v2.get("duration", 3.0)))
        except (TypeError, ValueError):
            hook_duration = 3.0
        if hook_text:
            # Keep the intro hook readable without covering the speaker.  AI
            # responses can occasionally exceed the advertised word limit;
            # enforce it at the render boundary as a final safe-zone guard.
            hook_words = hook_text.split()
            if len(hook_words) > 10:
                hook_text = " ".join(hook_words[:10]).rstrip(" .,;:!?…") + "…"
                log and log("⚠️ Hook V2 truncated to 10 words for safe-zone")
            log and log(f"🎣 Hook V2 overlay: {hook_text[:60]} (style: {hook_style})")
            try:
                from hooks import create_hook_image
                video_width, video_height = get_resolution(video_path)
                target_box_width = int(video_width * 0.84)
                hook_img = os.path.join(tempfile.gettempdir(), f"hook_v2_{os.path.basename(out_path)}.png")
                _, box_w, box_h = create_hook_image(hook_text, target_box_width, hook_img, font_scale=1.0, style=hook_style)
                overlay_x = (video_width - box_w) // 2
                overlay_y = int(video_height * 0.20)
                overlay_body = f"{overlay_x}:{overlay_y}:enable='between(t,0,{hook_duration})'"
            except Exception as e:
                log and log(f"⚠️ Hook V2 failed, falling back to drawtext: {e}")
                hook_img = None
                hook = hook_text.upper()[:70]
                filters.append(
                    f"drawtext=text='{_drawtext_escape(hook)}':"
                    f"{drawtext_font}:fontsize=54:fontcolor=#FFE500@0.98:borderw=5:"
                    f"bordercolor=black@0.9:shadowcolor=black@0.6:shadowx=3:shadowy=3:"
                    f"x=(w-text_w)/2:y=(h*0.13):"
                    f"alpha='if(lt(t,0.7),t/0.7,if(lt(t,3.4),1,if(lt(t,4.2),(4.2-t)/0.8,0)))'"
                )
    audio_filters = (
        "aresample=48000:async=1:first_pts=0,asetpts=N/SR/TB")
    if overlay_body:
        # Complex graph only for the hook overlay: chain the simple
        # ass/drawtext filters on [0:v] -> [main], then composite the hook
        # image ([1:v], the second -i input) on top with the prebuilt x:y:enable.
        chain = ",".join(filters)
        filter_complex = f"[0:v]{chain}[main];[main][1:v]overlay={overlay_body}[vout]"
        cmd = [
            _ffmpeg_executable(), "-y",
            "-i", video_path,
            "-i", hook_img,
            "-filter_complex", filter_complex,
            "-map", "[vout]",
            "-map", "0:a?",
            *fps_args,
            *encode_args(log=log),
            "-af", audio_filters,
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-movflags", "+faststart",
            out_path,
        ]
    else:
        cmd = [
            _ffmpeg_executable(), "-y", "-i", video_path,
            "-vf", ",".join(filters),
            *fps_args,
            *encode_args(log=log),
            "-af", audio_filters,
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-movflags", "+faststart",
            out_path,
        ]
    global _HOOK_BURN_NEEDS_BF0
    if _HOOK_BURN_NEEDS_BF0:
        # A previous burn in this worker hit the muxer rejection below; skip
        # straight to the known-good no-B-frame encode (same output quality).
        cmd = [*cmd[:-1], "-bf", "0", cmd[-1]]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       cwd=os.path.dirname(ass_path))
    if r.returncode != 0 and "pts/dts pair unsupported" in r.stderr:
        # FFmpeg 5.1's MP4 muxer can reject NVENC's reordered timestamps after
        # the Hook V2 two-input overlay, even after the decoded frame/sample
        # clocks have been rebuilt above. Retry only this final caption pass
        # without B-frame reordering; the expensive scene render remains
        # cached and every other encode keeps the production p4/CQ settings.
        log and log(
            "⚠️ MP4 timestamp reorder rejected — retrying caption encode "
            "without B-frames")
        try:
            if os.path.exists(out_path):
                os.remove(out_path)
        except OSError:
            pass
        _HOOK_BURN_NEEDS_BF0 = True
        retry_cmd = [*cmd[:-1], "-bf", "0", cmd[-1]]
        r = subprocess.run(retry_cmd, capture_output=True, text=True,
                           cwd=os.path.dirname(ass_path))
    if r.returncode != 0:
        raise RuntimeError(f"subtitle burn failed: {r.stderr[-500:]}")
    # Cleanup hook image
    if hook_img is not None and os.path.exists(hook_img):
        try:
            os.remove(hook_img)
        except OSError:
            pass
    return out_path
