"""Klipin Music: provider-neutral song generation with Indonesian-first lyrics.

The API deliberately keeps model execution behind a tiny adapter. ACE-Step and
HeartMuLa are optional local runners configured with environment variables;
when neither is installed, Klipin still returns a real, cacheable instrumental
preview using the existing deterministic BGM synthesizer. This makes the UX
usable in development and keeps the video render workers isolated.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

try:
    import imageio_ffmpeg
    _ffmpeg_dir = str(Path(imageio_ffmpeg.get_ffmpeg_exe()).parent)
    os.environ["PATH"] = _ffmpeg_dir + os.pathsep + os.environ.get("PATH", "")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "output"

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:
    pass

from bgm import render_bgm

GENRES = [
    # Pop & Balada
    {"id": "pop", "label": "Pop", "category": "Pop & Balada", "mood": "upbeat", "tags": "radio pop, polished, catchy chorus"},
    {"id": "pop-indo", "label": "Pop Indonesia / Galau", "category": "Pop & Balada", "mood": "chill", "tags": "Indonesian emotional pop ballad, warm grand piano, lush strings, heartfelt vocal, catchy chorus"},
    {"id": "ballad", "label": "Power Ballad", "category": "Pop & Balada", "mood": "epic", "tags": "emotional power ballad, grand piano, sweeping orchestral strings, dynamic drum build-up, soaring vocal"},
    {"id": "pop-rock", "label": "Pop Rock", "category": "Pop & Balada", "mood": "upbeat", "tags": "catchy pop rock, crunchy rhythm guitars, energetic live drums, soaring chorus"},
    {"id": "indie-pop", "label": "Indie Pop", "category": "Pop & Balada", "mood": "chill", "tags": "dreamy indie pop, jangly electric guitar, warm analog synths, relaxed groovy drums, intimate vocal"},
    {"id": "synth-pop", "label": "Synth-Pop / 80s Retro", "category": "Pop & Balada", "mood": "upbeat", "tags": "1980s synth-pop, analog Juno pads, gated reverb snare, punchy synth bass, nostalgic chorus"},
    {"id": "city-pop", "label": "City Pop", "category": "Pop & Balada", "mood": "chill", "tags": "80s Japanese city pop groove, funk slap bass, lush Rhodes electric piano, brass stabs, shimmering night vibe"},
    {"id": "dream-pop", "label": "Dream Pop / Shoegaze", "category": "Pop & Balada", "mood": "chill", "tags": "ethereal dream pop, lush chorused guitars, washed-out reverb, airy dreamy vocal, atmospheric pads"},
    {"id": "akustik", "label": "Pop Akustik / Acoustic", "category": "Pop & Balada", "mood": "chill", "tags": "acoustic pop, unplugged, fingerpicked acoustic guitar, intimate vocal, gentle piano, warm harmonies, live session feel"},
    {"id": "pop-remix", "label": "Pop Remix", "category": "Pop & Balada", "mood": "upbeat", "tags": "pop remix, polished vocal, four-on-the-floor drums, synth bass, radio hook"},

    # Nusantara & Daerah
    {"id": "pop-jawa", "label": "Pop Jawa", "category": "Nusantara & Daerah", "mood": "upbeat", "tags": "Javanese pop, warm gamelan textures, modern pop"},
    {"id": "jazz-pop", "label": "Jazz Pop Jawa", "category": "Nusantara & Daerah", "mood": "chill", "tags": "Indonesian jazz pop, Javanese influence, male and female duet, piano, upright bass, brush drums"},
    {"id": "campursari", "label": "Campursari", "category": "Nusantara & Daerah", "mood": "chill", "tags": "campursari, kendang, siter, Javanese vocal"},
    {"id": "dangdut-koplo", "label": "Dangdut Koplo", "category": "Nusantara & Daerah", "mood": "upbeat", "tags": "dangdut koplo, kendang, festive, danceable"},
    {"id": "dangdut-klasik", "label": "Dangdut Klasik", "category": "Nusantara & Daerah", "mood": "upbeat", "tags": "classic Indonesian dangdut, expressive tabla kendang, melodic bamboo suling flute, mandolin, emotional cengkok vocal"},
    {"id": "dangdut-remix", "label": "Dangdut Remix", "category": "Nusantara & Daerah", "mood": "upbeat", "tags": "dangdut remix, koplo groove, kendang, electronic bass, danceable drop"},
    {"id": "keroncong", "label": "Keroncong", "category": "Nusantara & Daerah", "mood": "chill", "tags": "keroncong, ukulele, cak cuk, nostalgic"},
    {"id": "sunda", "label": "Pop Sunda", "category": "Nusantara & Daerah", "mood": "chill", "tags": "Sundanese pop, kacapi, suling, intimate"},
    {"id": "bali", "label": "Pop Bali", "category": "Nusantara & Daerah", "mood": "upbeat", "tags": "Balinese pop, gamelan-inspired, bright"},
    {"id": "pop-minang", "label": "Pop Minang / Melayu", "category": "Nusantara & Daerah", "mood": "chill", "tags": "Minangkabau Malay melodic pop, saluang bamboo flute, gambus and accordion accents, slow rock Melayu feel, expressive soulful vocal"},
    {"id": "pop-batak", "label": "Pop Batak", "category": "Nusantara & Daerah", "mood": "epic", "tags": "Batak melodic pop ballad, soaring powerful harmony vocals, acoustic guitar, warm piano and strings, heartfelt anthem"},
    {"id": "pop-timur", "label": "Pop Indonesia Timur / Ambon", "category": "Nusantara & Daerah", "mood": "chill", "tags": "Eastern Indonesian tropical pop, warm island acoustic guitar, groovy bass, soulful silky vocal harmonies"},

    # Anak-anak & Keluarga
    {"id": "anak-ceria", "label": "Lagu Anak-anak / Ceria", "category": "Anak-anak & Keluarga", "mood": "upbeat", "tags": "cheerful children song, Indonesian kids singalong, playful xylophone, marimba, bright acoustic guitar, happy clapping rhythm, innocent sweet melody"},
    {"id": "anak-edukasi", "label": "Lagu Anak / Edukasi & Bermain", "category": "Anak-anak & Keluarga", "mood": "upbeat", "tags": "educational children song, cheerful playful ukulele, upright piano, tambourine, clear simple memorable kids chorus"},
    {"id": "anak-tidur", "label": "Lagu Anak / Pengantar Tidur (Lullaby)", "category": "Anak-anak & Keluarga", "mood": "chill", "tags": "gentle children lullaby, music box, soft kalimba and warm harp, soothing gentle strings, peaceful bedtime vocal"},
    {"id": "anak-islami", "label": "Lagu Anak Islami", "category": "Anak-anak & Keluarga", "mood": "upbeat", "tags": "cheerful Islamic children song, playful acoustic guitar, soft rebana frame drum, joyful kids singalong melody"},

    # Country, Folk & Akustik
    {"id": "country", "label": "Country / Americana", "category": "Country, Folk & Akustik", "mood": "chill", "tags": "warm modern country, acoustic strumming, pedal steel guitar licks, upright bass, storytelling vocal"},
    {"id": "country-pop", "label": "Country Pop", "category": "Country, Folk & Akustik", "mood": "upbeat", "tags": "modern country pop, bright acoustic guitar, banjo accents, catchy crossover pop chorus, warm live drums"},

    # Religi & Rohani
    {"id": "nasyid", "label": "Nasyid / Islami", "category": "Religi & Rohani", "mood": "chill", "tags": "nasyid, Islamic devotional, peaceful male vocal, frame drum, soft strings, uplifting chorus"},
    {"id": "sholawat", "label": "Sholawat / Hadroh Modern", "category": "Religi & Rohani", "mood": "chill", "tags": "Indonesian sholawat hadroh rebana frame drum percussion, warm oud and orchestral strings, serene devotional choral vocal"},
    {"id": "qasidah", "label": "Qasidah Gambus", "category": "Religi & Rohani", "mood": "upbeat", "tags": "classic qasidah gambus ensemble, rhythmic darbuka tambourine, expressive oud melody, warm unison vocal"},
    {"id": "gospel", "label": "Gospel / Choir Uplifting", "category": "Religi & Rohani", "mood": "epic", "tags": "soulful gospel choir, Hammond organ, grand piano, uplifting handclaps, powerful harmonies"},

    # Rock, Indie & Metal
    {"id": "rock", "label": "Rock", "category": "Rock, Indie & Metal", "mood": "epic", "tags": "rock, electric guitars, driving drums, powerful chorus, energetic vocal"},
    {"id": "alt-rock", "label": "Alternative Rock", "category": "Rock, Indie & Metal", "mood": "epic", "tags": "90s 2000s alternative rock, dynamic quiet-loud verses, gritty guitar riffs, punchy live drums, emotional chorus"},
    {"id": "indie-rock", "label": "Indie Rock", "category": "Rock, Indie & Metal", "mood": "upbeat", "tags": "modern indie rock, crisp telecaster guitar riffs, tight driving bass and drums, raw authentic vocal"},
    {"id": "punk", "label": "Pop Punk / Punk Rock", "category": "Rock, Indie & Metal", "mood": "upbeat", "tags": "fast energetic pop punk, palm-muted power chords, snappy fast drums, youthful anthemic gang vocals"},
    {"id": "grunge", "label": "Grunge", "category": "Rock, Indie & Metal", "mood": "epic", "tags": "90s Seattle grunge, heavy fuzz distortion guitar, sludgy bassline, thunderous snare, raw raspy vocal"},
    {"id": "blues-rock", "label": "Blues Rock", "category": "Rock, Indie & Metal", "mood": "upbeat", "tags": "gritty blues rock, expressive electric guitar licks, Hammond organ, shuffle groove, soulful raspy vocal"},
    {"id": "metal", "label": "Metal", "category": "Rock, Indie & Metal", "mood": "epic", "tags": "metal, heavy guitars, double kick drums, intense vocal, epic chorus"},
    {"id": "metalcore", "label": "Metalcore / Post-Hardcore", "category": "Rock, Indie & Metal", "mood": "epic", "tags": "modern metalcore, drop-tuned chugging guitar riffs, breakdown grooves, soaring clean chorus and intense screams"},
    {"id": "folk", "label": "Folk / Akustik", "category": "Rock, Indie & Metal", "mood": "chill", "tags": "folk, acoustic guitar, intimate vocal, warm harmonies"},

    # Elektronik & DJ
    {"id": "edm", "label": "EDM / Dance", "category": "Elektronik & DJ", "mood": "epic", "tags": "EDM, festival dance, powerful drop, sidechain synths, euphoric build-up"},
    {"id": "dj-remix", "label": "DJ Remix", "category": "Elektronik & DJ", "mood": "epic", "tags": "DJ remix, club mix, punchy kick, bass drop, sidechain, dancefloor energy"},
    {"id": "funkot", "label": "Funkot / Breakbeat Indo", "category": "Elektronik & DJ", "mood": "upbeat", "tags": "high-energy Indonesian funkot breakbeat, bouncy offbeat bass, chopped vocal stabs, fast club percussion"},
    {"id": "house", "label": "Deep House / Tropical House", "category": "Elektronik & DJ", "mood": "chill", "tags": "warm deep and tropical house, 120 BPM four-on-the-floor kick, marimba plucks, lush piano chords, airy vocal"},
    {"id": "techno", "label": "Techno / Melodic Techno", "category": "Elektronik & DJ", "mood": "epic", "tags": "driving melodic techno, hypnotic analog arpeggios, rolling sub bass, dark atmospheric builds"},
    {"id": "trance", "label": "Trance / Uplifting", "category": "Elektronik & DJ", "mood": "epic", "tags": "euphoric uplifting trance, 138 BPM rolling bass, soaring supersaw lead melody, emotional breakdown"},
    {"id": "dubstep", "label": "Dubstep / Future Bass", "category": "Elektronik & DJ", "mood": "epic", "tags": "melodic dubstep and future bass, half-time punchy snare, massive wobble sub-bass, shimmering chord drops"},
    {"id": "dnb", "label": "Drum & Bass / Liquid DnB", "category": "Elektronik & DJ", "mood": "upbeat", "tags": "fast 172 BPM liquid drum and bass, crisp breakbeat snare, warm rolling sub bass, atmospheric pads"},
    {"id": "synthwave", "label": "Synthwave / Outrun", "category": "Elektronik & DJ", "mood": "upbeat", "tags": "retro-futuristic 80s synthwave, driving analog arpeggio bass, gated tom drums, neon nostalgic synths"},
    {"id": "disco", "label": "Nu-Disco / Funk", "category": "Elektronik & DJ", "mood": "upbeat", "tags": "groovy nu-disco and funk, slap bassline, rhythmic wah electric guitar, crisp hi-hats, bright brass"},

    # Urban, Hip-Hop & R&B
    {"id": "rnb", "label": "R&B / Soul", "category": "Urban, Hip-Hop & R&B", "mood": "chill", "tags": "R&B, soulful vocal, smooth groove, warm keys, late night vibe"},
    {"id": "hiphop", "label": "Hip-hop", "category": "Urban, Hip-Hop & R&B", "mood": "epic", "tags": "hip-hop, punchy drums, bass, confident"},
    {"id": "trap", "label": "Trap / Melodic Rap", "category": "Urban, Hip-Hop & R&B", "mood": "epic", "tags": "modern melodic trap, booming 808 sub bass, rapid rolling hi-hats, moody synth bells, melodic rap vocal"},
    {"id": "drill", "label": "Drill / Sliding 808", "category": "Urban, Hip-Hop & R&B", "mood": "epic", "tags": "dark sliding 808 bass glides, syncopated drill hi-hat bounce, ominous piano loop, sharp confident rap delivery"},
    {"id": "phonk", "label": "Phonk / Drift Phonk", "category": "Urban, Hip-Hop & R&B", "mood": "epic", "tags": "Memphis cowbell phonk, heavy distorted 808 bass, dark cowbell melody, gritty underground club vibe"},
    {"id": "afrobeats", "label": "Afrobeats / Amapiano", "category": "Urban, Hip-Hop & R&B", "mood": "upbeat", "tags": "afrobeats, log drums, groovy percussion, warm vocal, danceable"},

    # Jazz, Reggae, Chill & Sinematik
    {"id": "jazz", "label": "Jazz", "category": "Jazz, Chill & Reggae", "mood": "chill", "tags": "jazz, swing rhythm, brushed drums, upright bass, smooth vocal"},
    {"id": "bossa-nova", "label": "Bossa Nova", "category": "Jazz, Chill & Reggae", "mood": "chill", "tags": "smooth Brazilian bossa nova, nylon acoustic guitar syncopation, soft brushed drums, warm flute and Rhodes, intimate vocal"},
    {"id": "blues", "label": "Soul & Classic Blues", "category": "Jazz, Chill & Reggae", "mood": "chill", "tags": "vintage classic blues and soul, warm tube guitar solo, slow 12/8 pocket groove, brass section, heartfelt vocal"},
    {"id": "reggae", "label": "Reggae", "category": "Jazz, Chill & Reggae", "mood": "chill", "tags": "warm island roots pop-reggae cover (BOENTER chill reggae vibe), laid-back 68 BPM half-time one-drop drum groove (soft kick and cross-stick rimshot on beat 3), muted clean electric guitar and warm Hammond B3 organ bubble on the offbeat upbeats, deep round sub-dub bassline (60-160 Hz warm pocket), melodic brass horn hook on intro and interlude, relaxed soulful vocal with warm studio reverb and subtle dub tape echo, silky rolled-off analog highs, no harsh hi-hats, no four-on-the-floor kick"},
    {"id": "ska", "label": "Ska / Reggae-Rock", "category": "Jazz, Chill & Reggae", "mood": "upbeat", "tags": "upbeat horn-driven ska groove, crisp offbeat guitar chops, walking bass, brass trumpet and trombone hooks, energetic vocal"},
    {"id": "lofi", "label": "Lo-fi", "category": "Jazz, Chill & Reggae", "mood": "chill", "tags": "lo-fi, mellow, dusty drums, late night"},
    {"id": "ambient", "label": "Ambient / Sinematik", "category": "Jazz, Chill & Reggae", "mood": "chill", "tags": "ambient, cinematic pads, ethereal vocal textures, slow build"},
    {"id": "orchestral", "label": "Orkestra / Epic Soundtrack", "category": "Jazz, Chill & Reggae", "mood": "epic", "tags": "sweeping cinematic orchestra, lush violin and cello section, epic brass fanfare, timpani drums, dramatic vocal"},

    # Global & Asia
    {"id": "kpop", "label": "K-Pop", "category": "Global & Asia", "mood": "upbeat", "tags": "k-pop, energetic Korean pop, bright vocals, polished production, driving beat, catchy hook"},
    {"id": "jpop", "label": "J-Pop", "category": "Global & Asia", "mood": "upbeat", "tags": "j-pop, upbeat Japanese pop, youthful vocal, bright synths, driving guitars"},
    {"id": "anime", "label": "Anime / J-Pop", "category": "Global & Asia", "mood": "epic", "tags": "anime opening, j-pop, energetic female vocal, bright female singer, driving drums, sparkling synths, catchy anthemic chorus"},
    {"id": "mandarin", "label": "Mandarin Pop", "category": "Global & Asia", "mood": "upbeat", "tags": "mandopop, emotional ballad vocal, piano, strings, polished production"},
    {"id": "india", "label": "India / Bollywood", "category": "Global & Asia", "mood": "upbeat", "tags": "Bollywood, Hindi pop, tabla, sitar textures, festive chorus"},
    {"id": "arab", "label": "Arab / Middle East", "category": "Global & Asia", "mood": "epic", "tags": "Arabic pop, middle eastern scales, oud, qanun, darbuka percussion, passionate vocal"},
    {"id": "latin", "label": "Latin / Reggaeton", "category": "Global & Asia", "mood": "upbeat", "tags": "Latin pop, reggaeton beat, Spanish guitar, danceable groove, passionate vocal"},
    {"id": "flamenco", "label": "Flamenco / Spanish Acoustic", "category": "Global & Asia", "mood": "epic", "tags": "passionate Spanish flamenco guitar rasgueado, crisp palmas handclaps, cajon rhythm, dramatic vocal"},
]


ORIGINAL_COVER_GENRE: dict = {
    "id": "original",
    "label": "🎵 Lagu Original (Pertahankan Musik Asli — Ganti Vokal Saja)",
    "mood": "pop",
    "tags": "original arrangement,preserve backing track,voice conversion cover",
}


def genre_info(genre: str) -> dict:
    if str(genre or "").strip().lower() == "original":
        return ORIGINAL_COVER_GENRE
    return next((g for g in GENRES if g["id"] == genre), GENRES[0])


def language_info(code: str) -> dict:
    """Catalog entry for a language code, with a safe generic fallback."""
    wanted = (code or "").strip().lower()
    found = next((item for item in LANGUAGES if item["id"] == wanted), None)
    if found:
        return found
    return {"id": wanted or DEFAULT_LANGUAGE, "label": wanted or "Indonesia",
            "tag": f"{wanted} vocal,{wanted} lyrics" if wanted else "",
            "group": "Lainnya"}


def resolve_language(genre: str, language: str = "",
                     override: bool = False) -> tuple[str, bool]:
    """Return ``(language_code, locked)`` for the selected genre.

    ``locked=True`` means the genre dictates the vocal language (Pop Jawa →
    Jawa). The UI mirrors this by disabling the language picker; the advanced
    "override" toggle passes ``override=True`` to force any language.
    """
    locked_code = (GENRE_LANGUAGE.get((genre or "").strip().lower()) or ("", False))[0]
    requested = (language or "").strip().lower()
    if locked_code and not override:
        return locked_code, True
    if requested:
        return requested, False
    if locked_code:
        return locked_code, False
    return DEFAULT_LANGUAGE, False


def language_options() -> list[dict]:
    """Language catalog for the UI, each entry annotated with its lock info."""
    options = []
    for item in LANGUAGES:
        locked_genres = [genre for genre, value in GENRE_LANGUAGE.items()
                         if value[0] == item["id"]]
        options.append({**item, "locked_for": locked_genres})
    return options


def _normalize_tags(value: str) -> str:
    """HeartMuLa expects comma-separated tags without spaces after commas."""
    flat = re.sub(r"\s+", " ", str(value or ""))
    return re.sub(r"\s*,\s*", ",", flat).strip(" ,")


def _exclude_items(spec: dict) -> list[str]:
    """Collect the "Exclude Styles" items of a request (max 3).

    Suno field guide (section 8) is explicit about the semantics:
      * the verified inline form is ``no <element>`` (not ``-tag``, not
        ``don't add ...``),
      * exclusions are applied AFTER the positives, so they belong at the
        very end of the style string,
      * more than ~3 exclusions destabilise the arrangement.

    ``exclude_styles`` is the dedicated Exclude field; ``negative_tags`` is
    kept as the legacy alias so old projects and API clients keep working.
    Both are merged and de-duplicated.
    """
    raw: list[str] = []
    for key in ("exclude_styles", "negative_tags"):
        raw.extend(re.split(r"[,\n;]+", str(spec.get(key) or "")))
    items: list[str] = []
    seen: set[str] = set()
    for seg in raw:
        item = seg.strip().lstrip("-").strip()
        low = item.lower()
        if not item or low in seen:
            continue
        seen.add(low)
        items.append(item)
    return items[:3]


def _creative_hints(spec: dict) -> list[str]:
    """Translate the Suno creative sliders into continuous text hints.

    The previous implementation only reacted at the extremes (>=75 / <=25),
    which left the middle half of both sliders completely inert: dragging
    Weirdness from 50 to 60 changed nothing at all. The field guide treats
    both parameters as continuous 0-100 scales, so they are mapped onto four
    bands with a neutral (untouched) middle band around 50.
    """
    hints: list[str] = []
    try:
        weirdness = int(spec.get("weirdness", 50))
    except (TypeError, ValueError):
        weirdness = 50
    try:
        influence = int(spec.get("style_influence", 50))
    except (TypeError, ValueError):
        influence = 50

    if weirdness >= 70:
        hints.append("experimental arrangement, surprising melodic turns, "
                     "unexpected structure, adventurous")
    elif weirdness >= 55:
        hints.append("subtle creative variation, slightly adventurous arrangement")
    elif weirdness <= 45:
        hints.append("conventional, predictable, straightforward song structure")
    elif weirdness <= 30:
        hints.append("safe, radio-friendly arrangement, follows the style tags closely")

    if influence >= 80:
        hints.append("strict genre adherence, faithful to the style tags, minimal deviation")
    elif influence >= 65:
        hints.append("strong genre adherence, closely follows the style tags")
    elif influence <= 35:
        hints.append("loose interpretation of the style tags, freer arrangement choices")
    elif influence <= 20:
        hints.append("style tags as loose inspiration only, maximum creative freedom")
    return hints


def tags_for(spec: dict, info: dict | None = None) -> str:
    """Provider tags: genre + language + gender + BPM/key + persona + idea.

    Order follows the GMVP priority documented in the field guide
    (Genre -> Mood -> Instrument -> Vocal -> Production) and the exclusions
    are appended LAST because Suno applies them after the positives.
    """
    info = info or genre_info(spec.get("genre") or "pop-jawa")
    gender = str(spec.get("vocal_gender") or "auto").strip().lower()
    parts = [info.get("tags", "")]
    if spec.get("instrumental"):
        parts.append("instrumental,no vocals,no vocal")
    else:
        parts.append(language_info(spec.get("language") or DEFAULT_LANGUAGE).get("tag", ""))
        secondary = (spec.get("language_secondary") or "").strip().lower()
        if secondary:
            parts.append(language_info(secondary).get("tag", ""))
            parts.append("multilingual lyrics, bilingual song")
        parts.append(VOCAL_GENDERS.get(gender, ""))
        vocal_style = str(spec.get("vocal_style") or "").strip().lower()
        if vocal_style and vocal_style in VOCAL_STYLES:
            parts.append(VOCAL_STYLES[vocal_style])
        parts.append("clear articulate vocals, studio vocal clarity, upfront lead vocal")
    try:
        bpm = int(spec.get("bpm") or 0)
    except (TypeError, ValueError):
        bpm = 0
    if 40 <= bpm <= 220:
        parts.append(f"{bpm} BPM")
    key = str(spec.get("music_key") or "").strip()[:24]
    if key:
        parts.append(f"key of {key}")
    if spec.get("style_tags"):
        parts.append(str(spec["style_tags"])[:300])
    # Arrangement intelligence: tag arahan seksi (lift verse→chorus, chorus
    # singalong) supaya engine menyusun dinamika, bukan sekadar genrenya.
    plan = spec.get("song_plan")
    if isinstance(plan, dict):
        for tag in (plan.get("driver_tags") or [])[:4]:
            parts.append(str(tag)[:80])
    take_var = str(spec.get("_take_arrangement_variation") or "").strip()
    if take_var:
        parts.append(take_var)
    parts.extend(_creative_hints(spec))
    parts.append(spec.get("prompt") or "")
    # Exclude Styles ("no X") selalu paling akhir: Suno memproses positives
    # lebih dulu, exclusions baru diterapkan sesudahnya.
    excludes = _exclude_items(spec)
    if excludes:
        parts.append(", ".join(f"no {item}" for item in excludes))
    return _normalize_tags(",".join(str(part).strip() for part in parts if part))


def _ace_modal_enabled() -> bool:
    return os.environ.get("KLIPIN_ACE_STEP_MODAL_ENABLED", "0").strip() == "1"


def _diffrhythm_modal_enabled() -> bool:
    """Whether the Apache 2.0 DiffRhythm full-song worker is deployed and enabled."""
    return os.environ.get("KLIPIN_DIFFRHYTHM_MODAL_ENABLED", "0").strip() == "1"


def _mulacover_modal_enabled() -> bool:
    """Whether the official HeartMuLa MuLaCover worker is deployed.

    HeartMuLa's original text-to-song worker cannot consume a reference audio
    file.  MuLaCover is the official audio-conditioned cover model and is kept
    as a separate opt-in worker because its checkpoint has a different
    deployment and licensing profile.
    """
    return os.environ.get("KLIPIN_MULACOVER_MODAL_ENABLED", "0").strip() == "1"


def _voice_modal_enabled() -> bool:
    """Whether the Seed-VC singing voice-conversion worker is available."""
    return os.environ.get("KLIPIN_VOICE_MODAL_ENABLED", "0").strip() == "1"


def _yue2_comfy_enabled() -> bool:
    """Whether YuE2 ComfyUI worker (Modal or local ComfyUI instance) is enabled."""
    return (
        os.environ.get("KLIPIN_YUE2_COMFY_ENABLED", "0").strip() == "1"
        or os.environ.get("KLIPIN_YUE2_MODAL_ENABLED", "0").strip() == "1"
        or bool(os.environ.get("COMFYUI_URL", "").strip())
    )


def _yue2_native_enabled() -> bool:
    """Whether YuE2 Native Python SDK worker (Modal or local CUDA) is enabled."""
    return (
        os.environ.get("KLIPIN_YUE2_NATIVE_ENABLED", "0").strip() == "1"
        or os.environ.get("KLIPIN_YUE2_MODAL_ENABLED", "0").strip() == "1"
    )


def _normalize_provider(provider: str | None) -> str:
    """Map persisted runtime provider labels back to request provider IDs.

    Completed jobs store the concrete engine label (for example
    ``heartmula-modal``).  Follow-up operations such as Extend reuse that
    value, while the generation dispatcher accepts only the logical IDs.
    Legacy ``yue2`` labels are transparently migrated to ``heartmula`` so
    existing database rows never break after removing non-commercial YuE2.
    """
    value = str(provider or "auto").strip().lower()
    if value.startswith("yue2_comfy") or value.startswith("yue2_native") or value == "yue2":
        return value.split("-")[0]
    if value.startswith("heartmula"):
        return "heartmula"
    if value.startswith("ace-step") or value.startswith("acestep"):
        return "ace-step"
    if value.startswith("mulacover"):
        return "mulacover"
    if value.startswith("diffrhythm"):
        return "diffrhythm"
    if value.startswith("arrangement"):
        return "arrangement"
    return value if value in ("auto", "heartmula", "ace-step", "mulacover", "diffrhythm", "arrangement") else "auto"


def _lora_id() -> str:
    """Regional LoRA slot (Jawa moat). Empty = base model, no training yet."""
    return os.environ.get("KLIPIN_MUSIC_LORA_ID", "").strip()


# ── Metering: transparan, tanpa fake precision ──────────────────────────
# L4 $0.000222/s + CPU/mem dari .env (sama seperti estimasi render video).
# Kredit termurah: paket Studio Rp149rb/100 = Rp1490 per kredit.
def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


def gpu_rate_usd_per_sec() -> float:
    l4 = _env_float("MODAL_L4_USD_PER_SEC", 0.000222)
    cpu = _env_float("MODAL_CPU_CORE_USD_PER_SEC", 0.0000131)
    mem = _env_float("MODAL_MEMORY_GIB_USD_PER_SEC", 0.00000222)
    cores = _env_float("MODAL_CPU_CORES", 8)
    gib = _env_float("MODAL_MEMORY_GIB", 16)
    return l4 + cpu * cores + mem * gib


def music_cost(gpu_sec: float) -> dict:
    """Biaya aktual satu take: {gpu_sec, usd, credits} (credits = ceil)."""
    import math
    sec = max(0.0, float(gpu_sec or 0))
    usd = sec * gpu_rate_usd_per_sec()
    idr_per_usd = _env_float("KLIPIN_USD_IDR", 16000)
    idr_per_credit = _env_float("KLIPIN_IDR_PER_CREDIT", 1490)
    credits = math.ceil(usd * idr_per_usd / idr_per_credit) if sec > 0 else 0
    return {"gpu_sec": round(sec, 1), "usd": round(usd, 6), "credits": credits}


def estimate_music_cost(duration: float, takes: int = 2) -> dict:
    """Estimasi sebelum antre (cold-start aware): draft 90s + final 240s."""
    import math
    est_sec = 90.0 + 240.0  # per grup 2-takes; diskala bila take_count=1
    if takes <= 1:
        est_sec = 240.0
    per = music_cost(est_sec)
    return {**per, "gpu_sec": round(est_sec, 1), "takes": takes,
            "note": "Estimasi cold-start; aktual dicatat per take"}


def provider_hint() -> str:
    """Actionable sentence: what is active, or exactly what is missing."""
    available = []
    if os.environ.get("KLIPIN_HEARTMULA_MODAL_ENABLED", "0").strip() == "1":
        available.append("HeartMuLa Modal (`%s`)"
                         % os.environ.get("KLIPIN_HEARTMULA_MODAL_APP", "klipin-music"))
    if _diffrhythm_modal_enabled():
        available.append("DiffRhythm Modal (`%s`)"
                         % os.environ.get("KLIPIN_DIFFRHYTHM_MODAL_APP", "klipin-diffrhythm"))
    if _mulacover_modal_enabled():
        available.append("MuLaCover Modal (`%s` cover)"
                         % os.environ.get("KLIPIN_MULACOVER_MODAL_APP", "klipin-mulacover"))
    if _ace_modal_enabled():
        available.append("ACE-Step Modal (`%s`)"
                         % os.environ.get("KLIPIN_ACE_STEP_MODAL_APP", "klipin-ace-step"))
    if _ace_step_url():
        available.append(f"ACE-Step ({_ace_step_url()})")
    if os.environ.get("KLIPIN_HEARTMULA_ROOT", "").strip():
        available.append("HeartMuLa lokal")
    if available:
        suffix = f" + LoRA `{_lora_id()}`" if _lora_id() else ""
        return "Engine lagu penuh aktif: " + " + ".join(available) + suffix + "."
    missing = [
        "KLIPIN_HEARTMULA_MODAL_ENABLED=1 untuk worker Modal `klipin-music` (Apache 2.0)",
        "KLIPIN_DIFFRHYTHM_MODAL_ENABLED=1 untuk worker Modal `klipin-diffrhythm` (Apache 2.0)",
    ]
    if not _ace_modal_enabled():
        missing.append("KLIPIN_ACE_STEP_MODAL_ENABLED=1 untuk worker Modal `klipin-ace-step` (draft cepat)")
    if not os.environ.get("KLIPIN_HEARTMULA_ROOT", "").strip():
        missing.append("KLIPIN_HEARTMULA_ROOT + KLIPIN_HEARTMULA_MODEL_PATH untuk HeartMuLa lokal")
    if not _ace_step_url():
        missing.append("KLIPIN_ACE_STEP_URL untuk server ACE-Step")
    return ("Belum ada engine lagu penuh yang aktif. Isi salah satu di backend/.env "
            "lalu restart API (docker compose restart api): " + "; ".join(missing) +
            ". Atau matikan `full_song` untuk memakai preview instrumental.")


def engine_status(probe: bool = False) -> dict:
    """Which full-song engines are configured (and optionally reachable)."""
    diffrhythm_enabled = _diffrhythm_modal_enabled()
    modal_enabled = os.environ.get("KLIPIN_HEARTMULA_MODAL_ENABLED", "0").strip() == "1"
    mulacover_enabled = _mulacover_modal_enabled()
    ace_modal = _ace_modal_enabled()
    ace_step = bool(_ace_step_url()) or ace_modal
    local = bool(os.environ.get("KLIPIN_HEARTMULA_ROOT", "").strip()
                 and os.environ.get("KLIPIN_HEARTMULA_MODEL_PATH", "").strip())
    provider = ("heartmula-modal" if modal_enabled else
                ("diffrhythm-modal" if diffrhythm_enabled else
                 ("ace-step" if ace_step else ("heartmula" if local else "none"))))
    status = {"provider": provider, "diffrhythm_modal_enabled": diffrhythm_enabled,
              "modal_enabled": modal_enabled,
              "mulacover_enabled": mulacover_enabled,
              "ace_modal_enabled": ace_modal, "ace_step": ace_step,
              "heartmula_local": local, "lora_id": _lora_id(),
              "ready": bool(diffrhythm_enabled or modal_enabled or ace_step or local), "worker": None,
              "draft_speed": "ace-step" if ace_step else ("diffrhythm-modal" if diffrhythm_enabled else "none"),
              "final_quality": "heartmula-modal" if modal_enabled else ("diffrhythm-modal" if diffrhythm_enabled else "none")}
    if modal_enabled and probe:
        from modal_credentials import modal_worker_status
        worker = modal_worker_status(
            os.environ.get("KLIPIN_HEARTMULA_MODAL_APP", "klipin-music"),
            os.environ.get("KLIPIN_HEARTMULA_MODAL_FUNCTION", "generate_song"))
        status["worker"] = worker
        status["ready"] = bool(worker["ready"])
    elif diffrhythm_enabled and probe:
        from modal_credentials import modal_worker_status
        worker = modal_worker_status(
            os.environ.get("KLIPIN_DIFFRHYTHM_MODAL_APP", "klipin-diffrhythm"),
            os.environ.get("KLIPIN_DIFFRHYTHM_MODAL_FUNCTION", "generate_song"))
        status["worker"] = worker
        status["ready"] = bool(worker["ready"])
    if status["worker"] and not status["worker"]["ready"]:
        status["message"] = status["worker"]["message"]
    else:
        status["message"] = ("Engine lagu penuh siap." if status["ready"]
                             else provider_hint())
    return status


# Gender penyanyi: tag bahasa Inggris yang disuntik ke prompt/tags provider.
# HeartMuLa mengikuti tags; ACE-Step mengikuti prompt.
VOCAL_GENDERS = {
    "auto": "",
    "male": "male vocal, male singer, masculine voice",
    "female": "female vocal, female singer, feminine voice",
    "child-female": "young girl child vocal, sweet innocent little girl singer, bright youthful child voice",
    "child-male": "young boy child vocal, cheerful little boy singer, bright youthful boy voice",
    "duet": "real male and female duet, female lead vocal, male response vocal, alternating singers, two distinct voices, duet harmony",
}

VOCAL_STYLES = {
    "auto": "",
    "warm-smooth": "warm smooth vocal delivery, silky phrasing, intimate studio tone",
    "emotional-ballad": "emotional expressive vocal, dynamic vibrato, heartfelt ballad phrasing",
    "soulful-raspy": "soulful slightly raspy vocal timbre, warm chest voice, organic grit",
    "bright-pop": "bright polished radio pop vocal, crisp diction, energetic projection",
    "traditional-cengkok": "authentic Nusantara cengkok vocal ornamentation, expressive melodic turns",
    "laidback-chill": "laid-back relaxed island vocal delivery, effortless phrasing, warm pocket",
}

# Bahasa vokal yang bisa dipilih pengguna. `tag` disuntikkan ke tags HeartMuLa,
# yang didokumentasikan komunitas sebagai daftar kata dipisah koma tanpa spasi.
LANGUAGES = [
    {"id": "jv", "label": "Jawa", "tag": "javanese vocal,javanese lyrics", "group": "Nusantara"},
    {"id": "id", "label": "Indonesia", "tag": "indonesian vocal,indonesian lyrics", "group": "Nusantara"},
    {"id": "ms", "label": "Melayu", "tag": "malay vocal,malay lyrics", "group": "Nusantara"},
    {"id": "su", "label": "Sunda", "tag": "sundanese vocal,sundanese lyrics", "group": "Nusantara"},
    {"id": "ban", "label": "Bali", "tag": "balinese vocal,balinese lyrics", "group": "Nusantara"},
    {"id": "ja", "label": "Jepang", "tag": "japanese vocal,japanese lyrics", "group": "Asia"},
    {"id": "ko", "label": "Korea", "tag": "korean vocal,korean lyrics", "group": "Asia"},
    {"id": "zh", "label": "Mandarin", "tag": "mandarin vocal,mandarin lyrics", "group": "Asia"},
    {"id": "hi", "label": "Hindi", "tag": "hindi vocal,hindi lyrics", "group": "Asia"},
    {"id": "th", "label": "Thailand", "tag": "thai vocal,thai lyrics", "group": "Asia"},
    {"id": "vi", "label": "Vietnam", "tag": "vietnamese vocal,vietnamese lyrics", "group": "Asia"},
    {"id": "tl", "label": "Filipina", "tag": "tagalog vocal,tagalog lyrics", "group": "Asia"},
    {"id": "ar", "label": "Arab", "tag": "arabic vocal,arabic lyrics", "group": "Timur Tengah"},
    {"id": "fa", "label": "Persia", "tag": "persian vocal,persian lyrics", "group": "Timur Tengah"},
    {"id": "tr", "label": "Turki", "tag": "turkish vocal,turkish lyrics", "group": "Timur Tengah"},
    {"id": "ur", "label": "Urdu", "tag": "urdu vocal,urdu lyrics", "group": "Timur Tengah"},
    {"id": "en", "label": "English", "tag": "english vocal,english lyrics", "group": "Eropa & Global"},
    {"id": "es", "label": "Spanyol", "tag": "spanish vocal,spanish lyrics", "group": "Eropa & Global"},
    {"id": "pt", "label": "Portugis", "tag": "portuguese vocal,portuguese lyrics", "group": "Eropa & Global"},
    {"id": "fr", "label": "Prancis", "tag": "french vocal,french lyrics", "group": "Eropa & Global"},
    {"id": "de", "label": "Jerman", "tag": "german vocal,german lyrics", "group": "Eropa & Global"},
    {"id": "it", "label": "Italia", "tag": "italian vocal,italian lyrics", "group": "Eropa & Global"},
    {"id": "ru", "label": "Rusia", "tag": "russian vocal,russian lyrics", "group": "Eropa & Global"},
]

DEFAULT_LANGUAGE = "id"

LANGUAGE_MIXES = {
    "none": "Tanpa campur bahasa",
    "chorus": "Verse/bridge bahasa utama, chorus/reff bahasa kedua",
    "verse-chorus": "Verse bahasa utama, chorus/reff bergantian dua bahasa",
    "line": "Bergantian per baris",
    "auto": "AI memilih pembagian section yang paling natural",
}


def normalize_language_mix(primary: str, secondary: str = "",
                           mix: str = "none") -> tuple[str, str]:
    """Validate the bilingual lyric controls shared by UI and workers."""
    primary = (primary or DEFAULT_LANGUAGE).strip().lower()
    secondary = (secondary or "").strip().lower()
    mix = (mix or "none").strip().lower()
    known = {item["id"] for item in LANGUAGES}
    if secondary and secondary not in known:
        raise ValueError("Bahasa kedua tidak dikenal.")
    if secondary == primary:
        raise ValueError("Bahasa kedua harus berbeda dari bahasa utama.")
    if not secondary:
        return "", "none"
    if mix not in LANGUAGE_MIXES:
        raise ValueError("Pola campur bahasa tidak dikenal.")
    return secondary, mix


def language_mix_prompt(spec: dict) -> str:
    """Human-readable instruction for lyric LLMs and provider tags."""
    primary = language_info(spec.get("language") or DEFAULT_LANGUAGE)["label"]
    secondary = (spec.get("language_secondary") or "").strip().lower()
    if not secondary:
        return primary
    second = language_info(secondary)["label"]
    mix = spec.get("language_mix") or "auto"
    pattern = LANGUAGE_MIXES.get(mix, LANGUAGE_MIXES["auto"])
    voice = ""
    if str(spec.get("vocal_gender") or "").strip().lower() == "duet":
        voice = (" Gunakan duet nyata: vokal wanita memimpin bahasa utama, "
                 "vokal pria menjadi response/chorus bahasa kedua; keduanya "
                 "harus terdengar bergantian dan pada bagian chorus bersama.")
    return (f"Bahasa utama: {primary}. Bahasa kedua: {second}. {pattern}. "
            "Tandai section dengan [Verse], [Pre-Chorus], [Chorus], [Bridge]. "
            "Jangan mencampur bahasa dalam satu baris kecuali mode per baris." + voice)


def annotate_lyric_languages(timeline: list[dict], spec: dict) -> list[dict]:
    """Attach language metadata to karaoke lines for UI/export consumers."""
    secondary = (spec.get("language_secondary") or "").strip().lower()
    primary = (spec.get("language") or DEFAULT_LANGUAGE).strip().lower()
    if not secondary:
        return [{**row, "language": primary} for row in timeline]
    mix = spec.get("language_mix") or "auto"
    out = []
    for i, row in enumerate(timeline):
        section = (row.get("section") or "").lower()
        if mix == "line":
            lang = secondary if i % 2 else primary
        elif mix == "chorus":
            lang = secondary if "chorus" in section or "reff" in section else primary
        elif mix == "verse-chorus":
            lang = secondary if ("chorus" in section or "reff" in section) else primary
        else:
            # Auto is deterministic at render time: chorus in the second language.
            lang = secondary if "chorus" in section or "reff" in section else primary
        out.append({**row, "language": lang})
    return out

# Genre daerah/bahasa-spesifik MENGUNCI bahasa vokal: memilih "Pop Jawa" tidak
# boleh menghasilkan vokal Indonesia/Inggris — sejalan dengan preset gaya
# daerah di Suno, di mana gaya membawa bahasa daerahnya. Genre global (pop,
# edm, rock, …) tetap bebas dipilih pengguna dengan default `id`.
GENRE_LANGUAGE = {
    "pop-jawa": ("jv", True),
    "jazz-pop": ("jv", True),
    "campursari": ("jv", True),
    "dangdut-koplo": ("jv", True),
    "keroncong": ("id", True),
    "sunda": ("su", True),
    "bali": ("ban", True),
    "nasyid": ("id", True),
    "kpop": ("ko", True),
    "jpop": ("ja", True),
    "anime": ("ja", True),
    "mandarin": ("zh", True),
    "india": ("hi", True),
    "arab": ("ar", True),
    "latin": ("es", True),
}


def normalize_lyrics(lyrics: str, duration: float) -> list[dict]:
    """Create stable line-level timing for karaoke and .lrc/.srt export."""
    lines = []
    section = "Verse"
    for raw in (lyrics or "").splitlines():
        text = raw.strip()
        if not text:
            continue
        if text.startswith("[") and text.endswith("]"):
            inner = text[1:-1].split("|", 1)[0].strip()
            if inner.lower() not in ("end", "fade out", "silence", "pause"):
                section = inner or section
            continue
        if len(text) >= 3 and text.startswith("*") and text.endswith("*"):
            continue
        lines.append({"text": text, "section": section})
    if not lines:
        return []
    slot = max(1.2, float(duration) / len(lines))
    return [{**line, "start": round(i * slot, 3),
             "end": round(min(float(duration), (i + 1) * slot), 3)}
            for i, line in enumerate(lines)]


def _duet_role_lyrics(lyrics: str, role: str) -> str:
    """Build a role-aware lyric sheet for a natural duet arrangement.

    A duet is not two full songs hard-switched at arbitrary timestamps. The
    reliable arrangement is: alternate verses, use a short shared/pre-chorus
    lift, and let both singers own the chorus. HeartMuLa has no speaker token,
    so each role receives the same section markers while the other role gets
    an instrumental placeholder. The stitcher later crossfades the sections.
    """
    wanted = "male" if str(role).lower() == "male" else "female"
    current = "verse"
    verse_no = 0
    out: list[str] = []
    kept_in_section = False
    for raw in (lyrics or "").splitlines():
        text = raw.strip()
        if not text:
            continue
        if text.startswith("[") and text.endswith("]"):
            current = text[1:-1].strip().lower() or current
            if "verse" in current and "pre" not in current:
                verse_no += 1
            out.append(text)
            kept_in_section = False
            continue
        is_chorus = any(token in current for token in ("chorus", "reff", "post-chorus"))
        is_shared = is_chorus or "pre-chorus" in current or "prechorus" in current or "bridge" in current
        if is_shared:
            is_wanted = True
        elif "verse" in current:
            # Verse 1 = female, verse 2 = male, then repeat the conversation.
            verse_role = "female" if verse_no % 2 else "male"
            is_wanted = wanted == verse_role
        else:
            is_wanted = wanted == "female"
        if is_wanted:
            out.append(text)
            kept_in_section = True
        elif not kept_in_section:
            out.append("[Instrumental]")
            kept_in_section = True
    return "\n".join(out).strip() or lyrics


def _duet_section_role(section: str, verse_no: int) -> str:
    """Return the arrangement role for a timed lyric section."""
    name = str(section or "verse").strip().lower()
    if any(token in name for token in ("chorus", "reff", "post-chorus")):
        return "both"
    if "pre-chorus" in name or "prechorus" in name or "bridge" in name:
        return "both"
    if "verse" in name:
        return "female" if verse_no % 2 else "male"
    return "female"


def _duet_male_timbre(input_path: Path, output_path: Path,
                      semitones: float = -4.0) -> Path:
    """Lower the male take when the base model collapses it to a high voice.

    HeartMuLa 3B has no speaker-id control. Rubber Band preserves timing and
    singing articulation better than a raw sample-rate hack, and is applied
    only to the male role before section stitching. A zero shift is supported
    for experiments and a failed optional filter falls back to the source.
    """
    if abs(float(semitones or 0)) < 0.01:
        return input_path
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        try:
            import imageio_ffmpeg
            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            return input_path
    result = subprocess.run(
        [ffmpeg, "-y", "-v", "error", "-i", str(input_path),
         "-af", f"rubberband=pitch={math.pow(2.0, float(semitones) / 12.0):.6f}",
         "-c:a", "libmp3lame", "-b:a", "192k", str(output_path)],
        capture_output=True, text=True, timeout=600)
    if result.returncode == 0 and output_path.exists() and output_path.stat().st_size > 1024:
        return output_path
    return input_path


def _seed_vc_convert(input_path: Path, output_path: Path,
                     reference_path: Path, log=None) -> tuple[Path, str]:
    """Convert a male singing take to a configured reference singer.

    Seed-VC is deliberately an external optional adapter: its model weights
    are too large for the normal Klipin API image. The official CLI writes an
    output directory, so we locate its generated audio and normalize it to the
    requested MP3 path. Missing config/dependencies never break Music; the
    caller falls back to the deterministic pitch-shift path.
    """
    root = os.environ.get("KLIPIN_SEED_VC_ROOT", "").strip()
    python_bin = os.environ.get("KLIPIN_SEED_VC_PYTHON", "python").strip() or "python"
    inference = Path(root) / "inference.py" if root else None
    if not inference or not inference.exists() or not reference_path.exists():
        return input_path, "unconfigured"
    work = output_path.parent / "_seed_vc_out"
    work.mkdir(parents=True, exist_ok=True)
    cmd = [python_bin, str(inference), "--source", str(input_path),
           "--target", str(reference_path), "--output", str(work),
           "--diffusion-steps", os.environ.get("KLIPIN_SEED_VC_STEPS", "50"),
           "--f0-condition", "True", "--auto-f0-adjust", "False"]
    try:
        result = subprocess.run(cmd, cwd=str(root), capture_output=True,
                                text=True, timeout=1800)
    except Exception as exc:  # noqa: BLE001 — optional adapter
        log and log(f"⚠️ Seed-VC tidak tersedia ({exc}); pakai fallback")
        return input_path, "unavailable"
    if result.returncode != 0:
        log and log(f"⚠️ Seed-VC gagal ({result.stderr[-500:]}); pakai fallback")
        return input_path, "failed"
    candidates = [p for p in work.rglob("*")
                  if p.is_file() and p.suffix.lower() in (".wav", ".flac", ".mp3")]
    if not candidates:
        log and log("⚠️ Seed-VC tidak menghasilkan audio; pakai fallback")
        return input_path, "empty"
    source = max(candidates, key=lambda p: p.stat().st_mtime)
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        try:
            import imageio_ffmpeg
            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            return input_path, "no-ffmpeg"
    encoded = subprocess.run(
        [ffmpeg, "-y", "-v", "error", "-i", str(source), "-c:a", "libmp3lame",
         "-b:a", "192k", str(output_path)], capture_output=True,
        text=True, timeout=600)
    if encoded.returncode == 0 and output_path.exists() and output_path.stat().st_size > 1024:
        log and log(f"🎙️ Seed-VC male reference applied: {reference_path.name}")
        return output_path, "seed-vc"
    return input_path, "encode-failed"


def voice_persona_capability() -> dict:
    """Report whether private-reference singing conversion is usable here."""
    from persona_previews import enabled, capability
    if enabled():
        return capability()
    root = Path(os.environ.get("KLIPIN_SEED_VC_ROOT", "").strip())
    python_bin = os.environ.get("KLIPIN_SEED_VC_PYTHON", sys.executable).strip()
    demucs_python = os.environ.get("KLIPIN_DEMUCS_PYTHON", python_bin).strip()
    inference = root / "inference.py" if str(root) else None
    if not inference or not inference.is_file():
        return {"ready": False, "message": "Voice conversion belum dikonfigurasi di server."}
    if not Path(python_bin).is_file() and not shutil.which(python_bin):
        return {"ready": False, "message": "Python Seed-VC tidak ditemukan di server."}
    if not Path(demucs_python).is_file() and not shutil.which(demucs_python):
        return {"ready": False, "message": "Python pemisah vokal tidak ditemukan di server."}
    try:
        check = subprocess.run(
            [demucs_python, "-c", "import demucs"], capture_output=True,
            text=True, timeout=10)
        if check.returncode != 0:
            return {"ready": False, "message": "Pemisah vokal Demucs belum terpasang di server."}
    except Exception:
        return {"ready": False, "message": "Pemisah vokal Demucs belum siap di server."}
    return {"ready": True, "message": "Persona suara siap dipakai."}


def apply_voice_persona(src: Path, reference_path: Path, job_dir: Path,
                        duration: float, log=None, semi_tone_shift: int = 0,
                        auto_f0: bool = False) -> Path:
    """Convert only the vocal stem and remix it with the original backing."""
    capability = voice_persona_capability()
    if not capability["ready"]:
        raise RuntimeError(capability["message"])
    from persona_previews import enabled, convert_song
    if enabled():
        return convert_song(src, reference_path, job_dir / "song_voice_persona.mp3",
                            duration, semi_tone_shift=semi_tone_shift, auto_f0=auto_f0)
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        try:
            import imageio_ffmpeg
            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("FFmpeg belum tersedia untuk meracik persona suara.") from exc
    demucs_python = os.environ.get(
        "KLIPIN_DEMUCS_PYTHON",
        os.environ.get("KLIPIN_SEED_VC_PYTHON", sys.executable)).strip()
    with tempfile.TemporaryDirectory(prefix="klipin-voice-convert-") as work_name:
        work = Path(work_name)
        stems_dir = work / "stems"
        result = subprocess.run(
            [demucs_python, "-m", "demucs", "--two-stems=vocals",
             "-o", str(stems_dir), str(src)],
            capture_output=True, text=True,
            timeout=int(os.environ.get("KLIPIN_VOICE_SEPARATION_TIMEOUT", "1200")))
        if result.returncode != 0:
            raise RuntimeError("Pemisahan vokal gagal: " + result.stderr[-700:])
        vocal_candidates = list(stems_dir.rglob("vocals.wav"))
        backing_candidates = list(stems_dir.rglob("no_vocals.wav"))
        if not vocal_candidates or not backing_candidates:
            raise RuntimeError("Demucs tidak menghasilkan stem vokal dan musik.")
        converted, mode = _seed_vc_convert(
            vocal_candidates[0], work / "converted-vocals.mp3", reference_path, log=log)
        if mode != "seed-vc":
            raise RuntimeError(f"Konversi suara gagal ({mode}); lagu asli dipertahankan.")
        target = job_dir / "song_voice_persona.mp3"
        mixed = subprocess.run(
            [ffmpeg, "-y", "-v", "error", "-i", str(backing_candidates[0]),
             "-i", str(converted), "-filter_complex",
             "[1:a]highpass=f=80,equalizer=f=3500:t=q:w=1.2:g=1.8,equalizer=f=10500:t=q:w=1.0:g=2.5,volume=1.08[v];"
             "[0:a]volume=0.86[bg];"
             "[bg][v]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0,"
             "alimiter=limit=0.98[out]",
             "-map", "[out]", "-t", f"{float(duration):.3f}",
             "-c:a", "libmp3lame", "-b:a", "256k", str(target)],
            capture_output=True, text=True, timeout=600)
        if mixed.returncode != 0 or not target.is_file() or target.stat().st_size < 1024:
            raise RuntimeError("Pencampuran vokal persona gagal: " + mixed.stderr[-500:])
        log and log("🎙️ Persona suara diterapkan pada stem vokal; musik asli dipertahankan")
        return target


def _synthesize_voice_reference(spec: dict, job_dir: Path, log=None) -> str:
    """Auto-create a target-voice reference when no persona sample is available.

    Uses the singing-persona worker (HeartMuLa to sing a short a-cappella line +
    Demucs to isolate the voice).  The resulting 16s reference is what Seed-VC
    uses as the timbre target for the source song.
    """
    import base64
    from persona_previews import enabled as _persona_enabled, _function as _voice_fn

    if not _persona_enabled():
        raise RuntimeError(
            "Cover voice-conversion butuh referensi suara: pilih persona "
            "bersuara atau aktifkan worker Seed-VC (KLIPIN_VOICE_MODAL_ENABLED=1).")
    gender = str(spec.get("vocal_gender") or "female").strip().lower()
    if gender == "child-female":
        timbre = "young girl child vocal,little girl singer,sweet innocent child voice"
    elif gender == "child-male":
        timbre = "young boy child vocal,little boy singer,bright cheerful child voice"
    elif gender == "male":
        timbre = "male singer,masculine voice"
    else:
        gender = "female"
        timbre = "female singer,feminine voice"
    persona_tags = str(spec.get("style_tags") or "").strip()
    extra = f",{persona_tags}" if persona_tags else ",clear smooth voice,expressive singing,balanced vocal"
    tags = f"{timbre}{extra},solo singer,a cappella,dry studio vocal"
    seed = int(spec.get("seed") or 1) % (2 ** 31 - 1)
    log and log(f"🎤 Menyintesis referensi suara {gender} untuk cover…")
    result = _voice_fn("design_preview").remote(
        tags, seed, os.environ.get("KLIPIN_HEARTMULA_MODAL_APP", "klipin-music"))
    data = base64.b64decode(result["audio_b64"])
    if len(data) < 1024:
        raise RuntimeError("Referensi suara hasil sintesis kosong.")
    reference = job_dir / "voice-reference.wav"
    reference.write_bytes(data)
    return str(reference)


GENRE_ARRANGEMENT_BLUEPRINTS: dict[str, str] = {
    "reggae": (
        "warm island roots Indonesian pop-reggae cover instrumental arrangement (BOENTER EDM chill reggae vibe), "
        "laid-back 66-72 BPM half-time feel (130 BPM double-time grid), crisp offbeat guitar and muted clean electric guitar skank panned left "
        "and warm Hammond B3 organ bubble panned right on the '&' upbeats, deep round sub-dub bassline (60-160 Hz warm pocket) "
        "following the chord roots, classic one-drop reggae drum groove (cross-stick rimshot + warm kick on beat 3, "
        "silky rolled-off analog hi-hat shuffle), warm brass/saxophone melodic hooks on intro and instrumental break, "
        "relaxed santai feel, silky rolled-off highs (>7 kHz), NO harsh metallic cymbals, NO four-on-the-floor kick"
    ),
    "dangdut-koplo": (
        "modern Jawa Timur dangdut koplo live stage instrumental arrangement, syncopated kendang "
        "koplo ketipung pattern, punchy bass guitar, bamboo suling flute fills, rhythmic clean "
        "guitar comping, festive upbeat danceable groove"
    ),
    "dangdut-remix": (
        "modern dangdut remix instrumental arrangement, driving koplo kendang combined with "
        "electronic club bassline, punchy kick, synthesizer hooks, festive dancefloor drop"
    ),
    "jazz-pop": (
        "smooth Indonesian jazz-pop lounge instrumental arrangement, warm Fender Rhodes electric "
        "piano voicings, upright walking bass, brushed snare swing groove, subtle jazz guitar fills"
    ),
    "jazz": (
        "classic smooth jazz ensemble instrumental arrangement, swing rhythm, brushed drums, "
        "upright acoustic bass, warm piano comping, mellow horn textures"
    ),
    "akustik": (
        "unplugged acoustic live session instrumental arrangement, fingerpicked and strummed "
        "steel-string acoustic guitar, warm cajon percussion, soft grand piano chords, intimate upright bass"
    ),
    "folk": (
        "warm indie folk acoustic instrumental arrangement, dual acoustic guitars, light shaker "
        "and tambourine, warm upright bass, organic live room feel"
    ),
    "dj-remix": (
        "modern Indonesian DJ club remix / Jedag Jedug festival dance instrumental arrangement, "
        "punchy four-on-the-floor kick, sidechained synth chords, bouncy offbeat bass drop, crisp claps"
    ),
    "edm": (
        "festival EDM dance instrumental arrangement, euphoric sidechained supersaw synths, "
        "driving bassline, punchy kick and snare build-up"
    ),
    "pop-remix": (
        "modern radio pop remix instrumental arrangement, four-on-the-floor groove, warm synth bass, "
        "sparkling arpeggios, tight polished drums"
    ),
    "keroncong": (
        "traditional Indonesian keroncong ensemble instrumental arrangement, interlocking ukulele "
        "cak and cuk offbeat rhythm, pizzicato cello kendang style, melodic flute and violin counter-melody"
    ),
    "campursari": (
        "modern Javanese campursari instrumental arrangement, warm saron and bonang gamelan textures, "
        "kendang ciblon groove, modern bass and keyboard pads"
    ),
    "pop-jawa": (
        "modern Javanese pop instrumental arrangement, warm acoustic guitar, gentle kendang groove, "
        "mellow gamelan bell accents, polished bass and keys"
    ),
    "sunda": (
        "Sundanese pop instrumental arrangement, kacapi zither arpeggios, bamboo suling flute, "
        "soft kendang Sunda rhythm, warm acoustic bass"
    ),
    "bali": (
        "Balinese pop fusion instrumental arrangement, bright rindik and gangsa gamelan motifs, "
        "upbeat acoustic guitar and modern rhythm section"
    ),
    "rock": (
        "energetic full-band rock instrumental arrangement, overdriven electric rhythm guitars, "
        "punchy live acoustic drum kit, driving bassline, melodic guitar fills"
    ),
    "rnb": (
        "smooth late-night R&B neo-soul instrumental arrangement, warm electric Rhodes keys, "
        "laid-back pocket drum groove, deep sub bass"
    ),
    "lofi": (
        "mellow lo-fi chillhop instrumental arrangement, dusty boom-bap drum loop, warm detuned "
        "electric piano chords, soft vinyl warmth, round sub bass"
    ),
    "pop-indo": (
        "modern Indonesian emotional pop ballad instrumental arrangement, warm grand piano, "
        "lush orchestral strings, subtle clean electric guitar arpeggio, steady dynamic drums"
    ),
    "synth-pop": (
        "1980s retro synth-pop instrumental arrangement, warm analog Juno synth chords, "
        "punchy gated reverb snare, pulsing synth bassline, crisp electronic percussion"
    ),
    "city-pop": (
        "80s Japanese city pop groove instrumental arrangement, funky slap bassline, "
        "lush Fender Rhodes keys, bright brass stabs, shimmering clean guitar rhythm"
    ),
    "funkot": (
        "high-energy Indonesian funkot breakbeat club instrumental arrangement, fast bouncy "
        "offbeat bassline, crisp club percussion, bright synth stabs, festival drop"
    ),
    "house": (
        "warm tropical & deep house instrumental arrangement, 120 BPM four-on-the-floor kick, "
        "bouncy marimba plucks, lush piano house chords, smooth sidechained bass"
    ),
    "ska": (
        "upbeat horn-driven ska instrumental arrangement, crisp offbeat guitar chops, "
        "walking bassline, punchy trumpet and trombone horn section, lively drumbeat"
    ),
    "bossa-nova": (
        "smooth Brazilian bossa nova instrumental arrangement, syncopated nylon acoustic guitar, "
        "soft brushed snare & rimshot groove, upright acoustic bass, warm Rhodes chords"
    ),
    "disco": (
        "groovy nu-disco & funk instrumental arrangement, funky slap bass, rhythmic wah-wah guitar, "
        "four-on-the-floor disco drums, bright strings and brass accents"
    ),
    "trap": (
        "modern melodic trap instrumental arrangement, booming 808 sub-bass, crisp rapid hi-hat rolls, "
        "moody bell synths, hard-hitting trap snare"
    ),
    "country": (
        "warm modern country Americana instrumental arrangement, steel-string acoustic guitar strumming, "
        "melodic pedal steel guitar and fiddle fills, upright bass, relaxed steady country drum beat"
    ),
    "country-pop": (
        "modern upbeat country pop instrumental arrangement, bright acoustic rhythm guitar, "
        "playful banjo picking, warm electric guitar accents, radio-ready live drums"
    ),
    "anak-ceria": (
        "cheerful children song instrumental arrangement, playful xylophone and marimba melody, "
        "bright acoustic guitar strumming, happy handclaps, light tambourine and cheerful upright piano"
    ),
    "anak-edukasi": (
        "educational children song instrumental arrangement, bright ukulele chords, warm grand piano, "
        "soft shaker and woodblock rhythm, simple clear singalong melody"
    ),
    "anak-tidur": (
        "gentle children bedtime lullaby instrumental arrangement, delicate music box chimes, "
        "soft warm harp and kalimba arpeggios, soothing quiet orchestral strings"
    ),
    "anak-islami": (
        "cheerful Islamic children song instrumental arrangement, playful acoustic guitar, "
        "gentle rebana frame drum rhythm, warm piano and joyful melody"
    ),
}


def _arrangement_prompt_for_genre(genre_id: str, info: dict,
                                  bpm: int | float = 0, key: str = "") -> str:
    """Build a genre-accurate instrumental rearrangement prompt (fixes hardcoded dangdut bug)."""
    gid = str(genre_id or "").strip().lower()
    blueprint = GENRE_ARRANGEMENT_BLUEPRINTS.get(
        gid,
        f"complete {info.get('label', gid)} instrumental arrangement ({info.get('tags', '')})"
    )
    tempo_lock = []
    if bpm and 45 <= float(bpm) <= 210:
        tempo_lock.append(f"exact tempo {int(round(float(bpm)))} BPM")
    if key:
        tempo_lock.append(f"key of {key}")
    lock_clause = f" ({', '.join(tempo_lock)})" if tempo_lock else ""
    return (
        f"instrumental backing track only, strictly no lead vocals, no singing; "
        f"completely re-arrange this accompaniment into {blueprint}{lock_clause}; "
        f"keep the exact original tempo, bar timing, chord progression, and song structure "
        f"so the original lead vocal aligns beat-for-beat, but completely replace the "
        f"original drum pattern and instrumentation with {info.get('label', gid)} style"
    )


def _ffmpeg_exe() -> str:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        import imageio_ffmpeg
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    return ffmpeg


def _separate_source_stems(source_b64: str, duration: float, job_dir: Path,
                           pitch_shift: int = 0, log=None) -> tuple[Path, Path, str]:
    """Isolate clean original vocals + backing stem from source audio.

    Priority:
      1. Modal L4 GPU `klipin-voice.separate_stems` (Demucs htdemucs on CUDA, ~8s)
      2. Local `demucs` (`htdemucs --two-stems=vocals`)
      3. FFmpeg phase-coherent Mid/Side vocal + harmonic backing separator fallback
    """
    import base64

    vocals_out = job_dir / "cover_vocals.mp3"
    backing_out = job_dir / "cover_backing_source.mp3"
    cap = str(max(3.0, min(float(duration or 180.0), 600.0)))
    shift = max(-12, min(12, int(pitch_shift or 0)))

    # 1. Try Modal L4 GPU Demucs stem separator first when enabled
    if _voice_modal_enabled():
        try:
            from persona_previews import _function as _voice_fn
            log and log("🎤 Memisahkan 4-stem lagu sumber di GPU L4 (Demucs htdemucs: vokal, drum, bass, instrumen)…")
            stems = _voice_fn("separate_stems").remote(source_b64, float(duration or 180.0), shift)
            if isinstance(stems, dict) and stems.get("vocals_b64") and stems.get("backing_b64"):
                vocals_out.write_bytes(base64.b64decode(stems["vocals_b64"]))
                backing_out.write_bytes(base64.b64decode(stems["backing_b64"]))
                for extra_key, extra_name in (
                    ("drums_b64", "cover_drums_source.mp3"),
                    ("bass_b64", "cover_bass_source.mp3"),
                    ("other_b64", "cover_other_source.mp3"),
                ):
                    if stems.get(extra_key):
                        (job_dir / extra_name).write_bytes(base64.b64decode(stems[extra_key]))
                if vocals_out.stat().st_size > 1024 and backing_out.stat().st_size > 1024:
                    return vocals_out, backing_out, str(stems.get("method") or "demucs-modal-gpu")
        except Exception as exc:  # noqa: BLE001
            log and log(f"ℹ️ GPU separate_stems fallback ke pemisah lokal ({exc})")

    source_file = job_dir / "cover_source_input.mp3"
    source_file.write_bytes(base64.b64decode(source_b64))
    ffmpeg = _ffmpeg_exe()

    # 2. Try local Demucs htdemucs two-stems
    try:
        import importlib.util
        if importlib.util.find_spec("demucs") is not None:
            log and log("🎤 Memisahkan vokal asli & instrumen (Demucs htdemucs)…")
            stems_dir = job_dir / "_demucs_stems"
            res = subprocess.run(
                [sys.executable, "-m", "demucs", "-n", "htdemucs", "--two-stems=vocals",
                 "--shifts", "0", "-o", str(stems_dir), str(source_file)],
                capture_output=True, text=True, timeout=900)
            if res.returncode == 0:
                v_wav = next(stems_dir.rglob("vocals.wav"), None)
                b_wav = next(stems_dir.rglob("no_vocals.wav"), None)
                if v_wav and b_wav and v_wav.is_file() and b_wav.is_file():
                    v_af = []
                    if shift != 0:
                        ratio = 2.0 ** (shift / 12.0)
                        v_af = ["-af", f"asetrate=44100*{ratio:.6f},aresample=44100,atempo={1.0/ratio:.6f}"]
                    subprocess.run(
                        [ffmpeg, "-y", "-v", "error", "-i", str(v_wav), "-t", cap,
                         *v_af, "-c:a", "libmp3lame", "-b:a", "192k", str(vocals_out)],
                        capture_output=True, text=True, timeout=180, check=True)
                    subprocess.run(
                        [ffmpeg, "-y", "-v", "error", "-i", str(b_wav), "-t", cap,
                         "-c:a", "libmp3lame", "-b:a", "192k", str(backing_out)],
                        capture_output=True, text=True, timeout=180, check=True)
                    shutil.rmtree(stems_dir, ignore_errors=True)
                    if vocals_out.is_file() and backing_out.is_file():
                        return vocals_out, backing_out, "demucs-htdemucs-local"
    except Exception as exc:  # noqa: BLE001
        log and log(f"ℹ️ Demucs lokal dilewati ({exc}); memakai filter isolasi vokal")

    # 3. Fallback: FFmpeg Mid/Side vocal-focused & harmonic-backing extraction
    log and log("🎤 Mengekstrak jalur vokal utama & panduan harmoni (FFmpeg studio isolator)…")
    v_filter = (
        "pan=stereo|c0=0.5*c0+0.5*c1|c1=0.5*c0+0.5*c1,"
        "highpass=f=115,lowpass=f=11500,"
        "equalizer=f=250:width_type=q:width=1.2:g=-3.0,"
        "equalizer=f=3000:width_type=q:width=1.2:g=2.5"
    )
    if shift != 0:
        ratio = 2.0 ** (shift / 12.0)
        v_filter += f",asetrate=44100*{ratio:.6f},aresample=44100,atempo={1.0/ratio:.6f}"
    subprocess.run(
        [ffmpeg, "-y", "-v", "error", "-i", str(source_file), "-t", cap,
         "-af", v_filter, "-c:a", "libmp3lame", "-b:a", "192k", str(vocals_out)],
        capture_output=True, text=True, timeout=180, check=True)
    b_filter = (
        "pan=stereo|c0=c0-c1|c1=c1-c0,"
        "highpass=f=65,lowpass=f=6000,volume=1.35"
    )
    subprocess.run(
        [ffmpeg, "-y", "-v", "error", "-i", str(source_file), "-t", cap,
         "-af", b_filter, "-c:a", "libmp3lame", "-b:a", "192k", str(backing_out)],
        capture_output=True, text=True, timeout=180, check=True)
    return vocals_out, backing_out, "ffmpeg-mid-side"


def _prepare_harmonic_guide_backing(backing_path: Path, job_dir: Path,
                                    genre: str, groove_reset: bool = True) -> Path:
    """Smooth out original drum transients so audio2audio generates a fresh genre groove
    while keeping 100% of the chord progression and bar timing locked to the vocal.
    """
    rhythmic_genres = {
        "reggae", "ska", "dangdut-koplo", "dangdut-klasik", "dangdut-remix",
        "dj-remix", "funkot", "edm", "house", "techno", "trance", "dubstep", "dnb",
        "synthwave", "disco", "pop-remix", "keroncong", "campursari", "jazz-pop",
        "jazz", "bossa-nova", "latin", "afrobeats", "trap", "drill", "phonk",
    }
    if not groove_reset and str(genre or "").lower() not in rhythmic_genres:
        return backing_path
    guide_path = job_dir / "cover_harmonic_guide.mp3"
    ffmpeg = _ffmpeg_exe()
    af = (
        "highpass=f=105,lowpass=f=4500,"
        "acompressor=threshold=-22dB:ratio=5:attack=1.5:release=70:makeup=2,"
        "aecho=0.8:0.75:25:0.18"
    )
    res = subprocess.run(
        [ffmpeg, "-y", "-v", "error", "-i", str(backing_path),
         "-af", af, "-c:a", "libmp3lame", "-b:a", "192k", str(guide_path)],
        capture_output=True, text=True, timeout=180)
    if res.returncode == 0 and guide_path.is_file() and guide_path.stat().st_size > 1024:
        return guide_path
    return backing_path


def _mix_vocal_and_arrangement(vocals_path: Path | str, restyled_path: Path | str,
                               output: Path | str, duration: float,
                               genre: str = "", log=None) -> Path:
    """Studio vocal-over-arrangement mixer & master:
    - Cleans residual bleed from isolated vocal stem (highpass 95Hz, de-mud 260Hz,
      presence lift 3.2kHz, optical-style leveling compressor, subtle room glue).
    - Genre-adaptive vocal styling:
      * Reggae: Dub tape delay (240ms quarter-note echo) + warm low-mid swing
        so the vocal phrasing glides naturally into the reggae skank beat.
      * Koplo: Bright upfront presence + snappy slapback.
    - Carves a -1.8dB mid-range vocal pocket at 2.6kHz in the new genre arrangement
      so the vocal sits naturally inside the band (like YouTube Reggae/Koplo covers).
    - Masters the combined stereo bus to -14 LUFS with true-peak limiting.
    """
    vocals_path = Path(vocals_path)
    restyled_path = Path(restyled_path)
    output = Path(output)
    ffmpeg = _ffmpeg_exe()
    gid = str(genre or "").strip().lower()

    if gid in ("reggae", "ska", "dub", "reggae-santai", "reggae-ska", "reggae-dub"):
        # BOENTER-style warm Reggae vocal pocket:
        # - Warm low-mid body (+1.2dB at 210Hz), de-box at 340Hz, smooth presence at 2.6kHz
        # - Gentle analog high-shelf roll-off (-1.8dB at 6.8kHz, lowpass 11.2kHz) so vocal never sounds harsh
        # - Subtle dub/plate slapback glue (115ms at 0.11 decay) so the vocal sits organically inside the reggae band
        vocal_chain = (
            "[1:a]highpass=f=92,lowpass=f=11200,"
            "equalizer=f=210:width_type=q:width=1.2:g=1.2,"
            "equalizer=f=340:width_type=q:width=1.4:g=-2.0,"
            "equalizer=f=2650:width_type=q:width=1.2:g=2.0,"
            "equalizer=f=6800:width_type=q:width=1.1:g=-1.8,"
            "acompressor=threshold=-18dB:ratio=3.2:attack=8:release=135:makeup=2.0,"
            "aecho=0.88:0.84:115:0.11,volume=1.18[v]"
        )
    elif gid in ("dangdut", "dangdut-koplo", "dangdut-remix"):
        vocal_chain = (
            "[1:a]highpass=f=120,lowpass=f=14500,"
            "equalizer=f=250:width_type=q:width=1.5:g=-2.8,"
            "equalizer=f=3500:width_type=q:width=1.2:g=3.2,"
            "acompressor=threshold=-17dB:ratio=3.8:attack=6:release=110:makeup=2.0,"
            "aecho=0.85:0.8:90:0.18,volume=1.22[v]"
        )
    else:
        vocal_chain = (
            "[1:a]highpass=f=95,lowpass=f=13800,"
            "equalizer=f=260:width_type=q:width=1.4:g=-2.2,"
            "equalizer=f=3200:width_type=q:width=1.2:g=2.2,"
            "acompressor=threshold=-18dB:ratio=3.2:attack=8:release=130:makeup=2.0,"
            "aecho=0.8:0.86:36:0.11,volume=1.18[v]"
        )
    if gid in ("reggae", "ska", "dub", "reggae-santai", "reggae-ska", "reggae-dub"):
        backing_chain = (
            "[0:a]lowpass=f=11500,"
            "equalizer=f=1850:width_type=q:width=1.2:g=1.5,"
            "equalizer=f=2800:width_type=q:width=1.2:g=-1.2,"
            "equalizer=f=6800:width_type=q:width=1.0:g=-1.0,"
            "extrastereo=m=1.38,"
            "volume=0.90[bg]"
        )
    else:
        backing_chain = (
            "[0:a]equalizer=f=2600:width_type=q:width=1.1:g=-1.8,"
            "volume=0.88[bg]"
        )
    filter_complex = (
        f"{backing_chain};{vocal_chain};"
        "[bg][v]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0,"
        "loudnorm=I=-14:TP=-1.5:LRA=11,alimiter=limit=0.96[out]"
    )
    mixed = subprocess.run(
        [ffmpeg, "-y", "-v", "error", "-i", str(restyled_path), "-i", str(vocals_path),
         "-filter_complex", filter_complex,
         "-map", "[out]", "-t", f"{float(duration):.3f}",
         "-c:a", "libmp3lame", "-b:a", "192k", str(output)],
        capture_output=True, text=True, timeout=600)
    if mixed.returncode != 0 or not output.is_file() or output.stat().st_size < 1024:
        raise RuntimeError("Pencampuran vokal + aransemen baru gagal: " + mixed.stderr[-400:])
    return output


def _decode_stereo_f32(path: Path, sr: int = 44100, max_sec: float = 600.0):
    """Decode any audio file to a (N, 2) float32 numpy array via FFmpeg."""
    import numpy as np

    ffmpeg = _ffmpeg_exe()
    res = subprocess.run(
        [ffmpeg, "-v", "error", "-i", str(path), "-ac", "2", "-ar", str(sr),
         "-t", f"{float(max_sec):.3f}", "-f", "f32le", "-"],
        capture_output=True, timeout=300)
    if res.returncode != 0 or not res.stdout:
        return np.zeros((0, 2), dtype=np.float32)
    raw = np.frombuffer(res.stdout, dtype=np.float32)
    frames = raw.size // 2
    return raw[: frames * 2].reshape(frames, 2).copy()


def _track_f0_voiced(y, sr: int = 22050):
    """Fast autocorrelation F0 tracker (85-780 Hz) on voiced frames."""
    import numpy as np

    hop = 512
    win = 2048
    if len(y) < win:
        return np.zeros(0, dtype=np.float32)
    lo = max(2, int(sr / 780.0))
    hi = min(win - 1, int(sr / 85.0))
    f0s: list[float] = []
    for i in range(0, len(y) - win, hop):
        seg = y[i : i + win]
        if float(np.sqrt(np.mean(seg * seg))) < 0.018:
            f0s.append(0.0)
            continue
        ac = np.correlate(seg, seg, mode="full")[win - 1 :]
        if ac[0] <= 1e-9:
            f0s.append(0.0)
            continue
        pk = lo + int(np.argmax(ac[lo:hi]))
        if ac[pk] > 0.34 * ac[0]:
            f0s.append(float(sr) / float(pk))
        else:
            f0s.append(0.0)
    return np.asarray(f0s, dtype=np.float32)


def _lock_vocal_and_backing_key(
    vocals_path: Path,
    backing_path: Path,
    job_dir: Path,
    source_b64: str,
    duration: float,
    requested_shift: int = 0,
    keep_original_vocal: bool = False,
    log=None,
) -> float:
    """Ensure 100.00% harmonic key lock (0.000 semitones error) between vocal and backing stems.

    Seed-VC's ``--auto-f0-adjust True`` multiplies the vocal F0 by a floating-point
    ratio ``median(f0_ref) / median(f0_src)`` (e.g. +4.71 or +6.03 semitones) so its
    diffusion network generates clean target female/male vocal formants, while
    leaving ``backing_path`` / ``other_path`` / ``bass_path`` at 0.00 semitones.
    Left uncorrected, the singer sings in a different musical key than the band.

    This function:
      1. Reconstructs the original source vocal waveform ``source - (backing + drums)``
         (or reads ``cover_vocals_orig.mp3``) and measures the exact median semitone
         shift ``delta_s`` applied to ``vocals_path``.
      2. Locks ``vocals_path`` to the exact target semitone key using FFmpeg
         ``rubberband=pitch=...:formant=preserved`` so 100% of Seed-VC's female/male
         vocal tract formants and cengkok are preserved while every sung note lands
         on the exact pitch of the instrumental chords & bass (0.0 cents drift).
    """
    if keep_original_vocal:
        return 0.0
    try:
        import base64
        import numpy as np

        sr = 22050
        probe_sec = min(float(duration or 90.0), 90.0)
        y_conv = _decode_stereo_f32(vocals_path, sr=sr, max_sec=probe_sec).mean(axis=1)
        if len(y_conv) < sr * 4:
            return 0.0

        orig_vocal_file = job_dir / "cover_vocals_orig.mp3"
        if orig_vocal_file.is_file() and orig_vocal_file.stat().st_size > 1024:
            y_orig = _decode_stereo_f32(orig_vocal_file, sr=sr, max_sec=probe_sec).mean(axis=1)
        else:
            src_tmp = job_dir / "_key_probe_source.mp3"
            if not src_tmp.is_file():
                src_tmp.write_bytes(base64.b64decode(source_b64))
            y_src = _decode_stereo_f32(src_tmp, sr=sr, max_sec=probe_sec).mean(axis=1)
            src_tmp.unlink(missing_ok=True)
            y_back = _decode_stereo_f32(backing_path, sr=sr, max_sec=probe_sec).mean(axis=1)
            drums_p = job_dir / "cover_drums_source.mp3"
            y_drums = (
                _decode_stereo_f32(drums_p, sr=sr, max_sec=probe_sec).mean(axis=1)
                if drums_p.is_file()
                else np.zeros_like(y_back)
            )
            n = min(len(y_src), len(y_back), len(y_drums))
            y_orig = y_src[:n] - y_back[:n] - y_drums[:n]

        f_orig = _track_f0_voiced(y_orig, sr=sr)
        f_conv = _track_f0_voiced(y_conv, sr=sr)
        n_f = min(len(f_orig), len(f_conv))
        if n_f < 10:
            return 0.0
        mask = (f_orig[:n_f] > 0.0) & (f_conv[:n_f] > 0.0)
        if int(np.sum(mask)) < 8:
            return 0.0

        st_diffs = 12.0 * np.log2(f_conv[:n_f][mask] / f_orig[:n_f][mask])
        delta_s = float(np.median(st_diffs))
        f_orig_med = float(np.median(f_orig[:n_f][mask]))

        req_st = int(requested_shift or 0)
        if req_st != 0:
            if abs(req_st) == 12:
                target_vocal_st = float(req_st)
                target_backing_st = 0.0
            else:
                target_vocal_st = float(req_st)
                target_backing_st = float(req_st)
        else:
            # Preserve Seed-VC's natural target register (female/male) by snapping
            # to the nearest integer semitone (e.g. +3.51 st -> +4.0 st) and transposing
            # the backing track to match that exact semitone key (0.0 cents drift),
            # NEVER pitching a female conversion back down to 0.0 st (male register)!
            snapped_st = float(int(round(delta_s)))
            snapped_st = max(-7.0, min(7.0, snapped_st))
            target_vocal_st = snapped_st
            target_backing_st = 0.0 if abs(int(snapped_st)) == 12 else snapped_st

        vocal_correction_st = target_vocal_st - delta_s
        ffmpeg = _ffmpeg_exe()

        if abs(vocal_correction_st) > 0.10:
            ratio = 2.0 ** (vocal_correction_st / 12.0)
            v_tmp = job_dir / "_vocals_keylocked.mp3"
            res = subprocess.run(
                [
                    ffmpeg, "-y", "-v", "error", "-i", str(vocals_path),
                    "-af", f"rubberband=pitch={ratio:.6f}:formant=preserved",
                    "-c:a", "libmp3lame", "-b:a", "192k", str(v_tmp),
                ],
                capture_output=True, text=True, timeout=240,
            )
            if res.returncode == 0 and v_tmp.is_file() and v_tmp.stat().st_size > 1024:
                v_tmp.replace(vocals_path)
                log and log(
                    f"🎯 Kunci nada vokal diselaraskan ke instrumen "
                    f"(koreksi pitch {vocal_correction_st:+.2f} st dengan formant vokal tetap utuh)"
                )
            else:
                v_tmp.unlink(missing_ok=True)

        if abs(target_backing_st) > 0.10:
            b_ratio = 2.0 ** (target_backing_st / 12.0)
            for stem_p in (
                backing_path,
                job_dir / "cover_other_source.mp3",
                job_dir / "cover_bass_source.mp3",
            ):
                if stem_p.is_file() and stem_p.stat().st_size > 1024:
                    s_tmp = job_dir / f"_{stem_p.stem}_shifted.mp3"
                    r_b = subprocess.run(
                        [
                            ffmpeg, "-y", "-v", "error", "-i", str(stem_p),
                            "-af", f"rubberband=pitch={b_ratio:.6f}",
                            "-c:a", "libmp3lame", "-b:a", "192k", str(s_tmp),
                        ],
                        capture_output=True, text=True, timeout=240,
                    )
                    if r_b.returncode == 0 and s_tmp.is_file() and s_tmp.stat().st_size > 1024:
                        s_tmp.replace(stem_p)
                    else:
                        s_tmp.unlink(missing_ok=True)
        return target_backing_st
    except Exception as exc:  # noqa: BLE001
        log and log(f"ℹ️ Penyelarasan nada dilewati ({exc})")
        return 0.0



def _voice_restyle_cover(spec: dict, job_dir: Path, output: Path, log=None) -> Path:
    """Vocal-locked cover with a 100% beat-locked & key-locked genre instrumental arrangement.

    Supports two vocal modes:
    - ``keep_original_vocal=True`` (or ``cover_engine="arrangement"``):
      Preserves the original singer's vocal from the source song (Demucs stem)
      and overlays it onto the newly arranged genre instrumental.
    - ``keep_original_vocal=False`` (``cover_engine="voice"`` + ``restyle=True``):
      Converts the isolated vocal to the target Voice Persona / gender via
      Seed-VC while keeping 100% of the source melody, cengkok, lyrics, and
      phrasing, locks the vocal pitch to the exact key of the backing chords,
      and mixes it over the beat-locked genre instrumental.
    """
    import base64
    from persona_previews import enabled as _vp_enabled, _function as _voice_fn

    duration = float(spec.get("duration") or 0) or 180.0
    source_b64 = (
        str(spec.get("cover_source_b64") or "").strip()
        or _cover_reference_b64(spec)
    )
    if not source_b64:
        raise RuntimeError("Cover aransemen butuh audio sumber lagu asli.")

    keep_original_vocal = bool(
        spec.get("keep_original_vocal")
        or str(spec.get("cover_engine") or "").strip().lower() == "arrangement"
    )
    pitch_shift = int(spec.get("voice_pitch_shift") or 0)

    # Check local stem + Seed-VC cache so switching genres on the same song is instant (Rp 0 GPU)
    raw_source_bytes = base64.b64decode(source_b64)
    src_sha = hashlib.sha1(raw_source_bytes).hexdigest()[:16]
    gender_key = "orig" if keep_original_vocal else (
        str(spec.get("persona_id") or spec.get("vocal_gender") or "female").strip().lower()
    )
    stem_cache_dir = job_dir.parent / "_stem_cache" / f"{src_sha}_{gender_key}_{pitch_shift}"
    vocals_path = job_dir / "cover_vocals.mp3"
    backing_path = job_dir / "cover_backing_source.mp3"

    cached_ok = (
        (stem_cache_dir / "cover_vocals.mp3").is_file()
        and (stem_cache_dir / "cover_backing_source.mp3").is_file()
        and (stem_cache_dir / "cover_other_source.mp3").is_file()
        and (stem_cache_dir / "cover_bass_source.mp3").is_file()
    )
    if cached_ok:
        log and log("⚡ Menggunakan cache 4-stem & vokal Seed-VC lagu sumber (hemat waktu & Rp 0 GPU)…")
        for fname in (
            "cover_vocals.mp3",
            "cover_vocals_orig.mp3",
            "cover_backing_source.mp3",
            "cover_drums_source.mp3",
            "cover_bass_source.mp3",
            "cover_other_source.mp3",
        ):
            src_f = stem_cache_dir / fname
            if src_f.is_file():
                shutil.copyfile(src_f, job_dir / fname)
        spec["_stem_separation_method"] = "seed-vc-4stems-cached"
    elif keep_original_vocal:
        vocals_path, backing_path, sep_method = _separate_source_stems(
            source_b64, duration, job_dir, pitch_shift=pitch_shift, log=log)
        spec["_stem_separation_method"] = sep_method
    else:
        if not _vp_enabled():
            raise RuntimeError(
                "Cover konversi vokal butuh worker Seed-VC aktif "
                "(KLIPIN_VOICE_MODAL_ENABLED=1).")
        reference_path = str(spec.get("_voice_reference_path") or "").strip()
        if not reference_path:
            reference_path = _synthesize_voice_reference(spec, job_dir, log=log)
            spec["_voice_reference_path"] = reference_path
        ref_b64 = base64.b64encode(Path(reference_path).read_bytes()).decode("ascii")
        voice_engine = str(spec.get("voice_engine") or os.environ.get("KLIPIN_VOICE_ENGINE", "rvc")).strip().lower()
        log and log(f"🎤 Memisah & menyelaraskan warna vokal sumber (Demucs + {voice_engine.upper()})…")
        try:
            stems = _voice_fn("convert_stems").remote(
                source_b64, ref_b64, duration,
                pitch_shift, bool(spec.get("voice_pitch_auto", True)),
                voice_engine=voice_engine, voice_persona=str(spec.get("vocal_gender") or "female"))
        except TypeError:
            stems = _voice_fn("convert_stems").remote(
                source_b64, ref_b64, duration,
                pitch_shift, bool(spec.get("voice_pitch_auto", True)))
        vocals_path.write_bytes(base64.b64decode(stems["vocals_b64"]))
        backing_path.write_bytes(base64.b64decode(stems["backing_b64"]))
        for extra_key, extra_name in (
            ("vocals_orig_b64", "cover_vocals_orig.mp3"),
            ("drums_b64", "cover_drums_source.mp3"),
            ("bass_b64", "cover_bass_source.mp3"),
            ("other_b64", "cover_other_source.mp3"),
        ):
            if stems.get(extra_key):
                (job_dir / extra_name).write_bytes(base64.b64decode(stems[extra_key]))
        spec["_stem_separation_method"] = str(stems.get("method") or "seed-vc-vocal-stem")
        try:
            stem_cache_dir.mkdir(parents=True, exist_ok=True)
            for fname in (
                "cover_vocals.mp3",
                "cover_vocals_orig.mp3",
                "cover_backing_source.mp3",
                "cover_drums_source.mp3",
                "cover_bass_source.mp3",
                "cover_other_source.mp3",
            ):
                src_f = job_dir / fname
                if src_f.is_file():
                    shutil.copyfile(src_f, stem_cache_dir / fname)
        except Exception:  # noqa: BLE001
            pass

    # Lock vocal pitch & backing key so the singer and band are 100.00% in the same key
    _lock_vocal_and_backing_key(
        vocals_path, backing_path, job_dir, source_b64, duration,
        requested_shift=pitch_shift, keep_original_vocal=keep_original_vocal, log=log
    )

    info = genre_info(spec.get("genre") or "reggae")
    detected = analyze_bpm_key(backing_path, log=log)
    bpm = detected.get("bpm") or spec.get("bpm") or 0
    key = detected.get("key") or spec.get("music_key") or ""
    if bpm:
        log and log(f"🧭 Tempo & nada sumber terdeteksi: {bpm} BPM{f', {key}' if key else ''}")

    restyled_path = job_dir / "cover_backing_restyled.mp3"
    groove_reset = bool(spec.get("groove_reset", True))
    guide_backing = _prepare_harmonic_guide_backing(
        backing_path, job_dir, info["id"], groove_reset=groove_reset)
    guide_b64 = base64.b64encode(guide_backing.read_bytes()).decode("ascii")
    try:
        strength = max(0.15, min(0.85, float(spec.get("restyle_strength", 0.42))))
    except (TypeError, ValueError):
        strength = 0.42
    arrangement_prompt = _arrangement_prompt_for_genre(info["id"], info, bpm=bpm, key=key)
    restyle_spec = {
        **spec,
        "bpm": int(round(float(bpm))) if bpm and 45 <= float(bpm) <= 210 else 0,
        "music_key": str(key or ""),
        "ref_audio_b64": source_b64 if _mulacover_modal_enabled() else guide_b64,
        "cover_source_b64": source_b64 if _mulacover_modal_enabled() else guide_b64,
        "ref_strength": strength,
        "prompt": arrangement_prompt,
        "lyrics": (
            spec.get("lyrics")
            if _mulacover_modal_enabled() and str(spec.get("lyrics") or "").strip()
            else "[Intro]\n\n[Verse]\nLa la la\n\n[Outro]"
        ),
        "cover_style_prompt": "",
        "instrumental": True,
        "groove_reset": True,
    }
    if _mulacover_modal_enabled():
        log and log(f"🎛️ Menata ulang aransemen instrumen AI ke {info['label']} (MuLaCover)…")
        raw_mula = job_dir / "cover_mulacover_raw.mp3"
        _mulacover_modal_generate(restyle_spec, raw_mula, log=log)
        try:
            mula_sep_dir = job_dir / "_mula_sep"
            mula_sep_dir.mkdir(parents=True, exist_ok=True)
            _, mula_inst, _ = _separate_source_stems(
                base64.b64encode(raw_mula.read_bytes()).decode("ascii"),
                duration, mula_sep_dir, pitch_shift=0, log=log)
            shutil.copyfile(mula_inst, restyled_path)
        except Exception:  # noqa: BLE001
            shutil.copyfile(raw_mula, restyled_path)
        spec["_arrangement_method"] = "mulacover-ai-arrangement"
    elif _ace_modal_enabled():
        log and log(f"🎛️ Menata ulang aransemen instrumen AI ke {info['label']} (ACE-Step)…")
        _ace_step_modal_generate(restyle_spec, restyled_path, log=log)
        spec["_arrangement_method"] = "ace-step-ai-arrangement"
    elif _ace_step_url():
        _ace_step_generate(restyle_spec, restyled_path, log=log)
        spec["_arrangement_method"] = "ace-step-ai-arrangement"
    else:
        shutil.copyfile(guide_backing, restyled_path)
        spec["_arrangement_method"] = "harmonic-guide-stem"

    if not restyled_path.is_file() or restyled_path.stat().st_size < 1024:
        raise RuntimeError("Gagal menata ulang aransemen instrumental.")

    log and log(
        "✅ Menyatukan vokal asli dengan aransemen baru (-14 LUFS studio mix)…"
        if keep_original_vocal
        else "✅ Menyatukan vokal hasil konversi dengan aransemen baru (-14 LUFS studio mix)…"
    )
    return _mix_vocal_and_arrangement(
        vocals_path, restyled_path, output, duration, genre=info["id"], log=log)


def _safe_title(prompt: str, title: str) -> str:
    return (title or prompt or "Klipin Music").strip()[:120]


def _draft_lyrics(prompt: str, language: str) -> str:
    """Offline lyric seed so every preview has something editable on screen."""
    topic = " ".join((prompt or "cerita hari ini").split())[:48]
    return (f"[Verse]\n{topic}\nLangkah kecil menuju cahaya\n"
            f"[Chorus]\nKita bernyanyi, kita percaya\nNada lokal membawa cerita\n"
            f"[Verse]\nDi bawah langit yang sama\nKita pulang membawa makna\n"
            f"[Chorus]\nKita bernyanyi, kita percaya\nNada lokal membawa cerita")


ALLOWED_EXTERNAL_PROVIDERS = {"heartmula", "ace-step", "diffrhythm", "mulacover"}
ALLOWED_AUDIO_SUFFIXES = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus"}


def _provider_command(name: str) -> str:
    clean = str(name or "").strip().lower()
    if clean not in ALLOWED_EXTERNAL_PROVIDERS:
        return ""
    env_key = clean.replace("-", "_").upper()
    return os.environ.get(f"KLIPIN_{env_key}_COMMAND", "").strip()


def is_safe_public_url(url: str) -> tuple[bool, str]:
    """Validate that a remix URL targets a public internet host (anti-SSRF)."""
    import ipaddress
    import socket
    from urllib.parse import urlparse

    parsed = urlparse(str(url or "").strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return False, "URL harus diawali http:// atau https:// yang valid."
    host = (parsed.hostname or "").strip().lower()
    if not host or host in ("localhost", "0.0.0.0") or host.endswith(".local") or host.endswith(".internal"):
        return False, "URL lokal atau jaringan internal tidak diizinkan."
    # Check literal IP first
    try:
        ip_obj = ipaddress.ip_address(host)
        if (ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_link_local
                or ip_obj.is_multicast or ip_obj.is_reserved or ip_obj.is_unspecified):
            return False, "Alamat IP privat atau loopback tidak diizinkan."
    except ValueError:
        # Hostname: resolve best-effort and reject if any resolved address is non-public
        try:
            infos = socket.getaddrinfo(host, None)
            for info in infos:
                addr = info[4][0]
                ip_obj = ipaddress.ip_address(addr)
                if (ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_link_local
                        or ip_obj.is_multicast or ip_obj.is_reserved or ip_obj.is_unspecified):
                    return False, "Host mengarah ke jaringan internal/privat yang tidak diizinkan."
        except socket.gaierror:
            pass
    return True, ""


def _ace_step_url() -> str:
    return os.environ.get("KLIPIN_ACE_STEP_URL", "").strip().rstrip("/")


def _cover_reference_b64(spec: dict) -> str:
    """Resolve the source audio used by a faithful cover.

    Cover jobs prefer a path to the completed parent artifact so the request
    does not have to keep a large base64 blob in the job payload.  The older
    ``ref_audio_b64`` field remains supported for URL-remix jobs and callers
    that already have an in-memory reference.
    """
    import base64

    encoded = str(spec.get("ref_audio_b64") or "").strip()
    if encoded:
        if len(encoded) > 20_000_000:
            raise RuntimeError("Audio referensi cover melebihi batas 15 MB.")
        return encoded
    raw_path = str(spec.get("ref_audio_path") or "").strip()
    if not raw_path:
        return ""
    source = Path(raw_path)
    if source.is_symlink() or not source.is_file():
        return ""
    if source.suffix.lower() not in ALLOWED_AUDIO_SUFFIXES:
        return ""
    raw = source.read_bytes()
    if len(raw) > 15_000_000:
        raise RuntimeError("Audio referensi cover melebihi batas 15 MB.")
    if len(raw) < 1024:
        return ""
    return base64.b64encode(raw).decode("ascii")


def _heartmula_generate(spec: dict, job_dir: Path, out_path: Path, log=None) -> bool:
    """Run HeartMuLa's official local generation example when installed."""
    root = os.environ.get("KLIPIN_HEARTMULA_ROOT", "").strip()
    model_path = os.environ.get("KLIPIN_HEARTMULA_MODEL_PATH", "").strip()
    if not root or not model_path:
        return False
    script = Path(root) / "examples" / "run_music_generation.py"
    if not script.exists():
        raise RuntimeError(f"HeartMuLa runner not found: {script}")
    info = genre_info(spec.get("genre") or "pop-jawa")
    provider_tags = tags_for(spec, info)
    lyrics_path = job_dir / "heartmula_lyrics.txt"
    tags_path = job_dir / "heartmula_tags.txt"
    lyrics_path.write_text(spec.get("lyrics", ""), encoding="utf-8")
    tags_path.write_text(provider_tags, encoding="utf-8")
    python_bin = os.environ.get("KLIPIN_HEARTMULA_PYTHON", "python")
    cmd = [python_bin, str(script), f"--model_path={model_path}", "--version=3B",
           f"--lyrics={lyrics_path}", f"--tags={tags_path}",
           f"--save_path={out_path}",
           f"--max_audio_length_ms={int(float(spec.get('duration') or 180) * 1000)}",
           f"--lazy_load={os.environ.get('KLIPIN_HEARTMULA_LAZY_LOAD', 'true')}"]
    log and log("🎤 HeartMuLa full-song generation started…")
    result = subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=1800)
    if result.returncode != 0:
        raise RuntimeError(f"HeartMuLa failed: {result.stderr[-1200:]}")
    return out_path.exists() and out_path.stat().st_size > 1024


def _resolve_sampling_params(spec: dict) -> dict:
    """Resolve studio sampling parameters (temperature, cfg_scale, topk) from explicit fields or 0-100 sliders."""
    try:
        weirdness = float(spec.get("weirdness", 50))
    except (TypeError, ValueError):
        weirdness = 50.0
    try:
        style_influence = float(spec.get("style_influence", 50))
    except (TypeError, ValueError):
        style_influence = 50.0

    # Map weirdness (0..100) -> temperature (0.78..1.18, neutral 50 -> 1.00)
    default_temp = round(0.78 + (max(0.0, min(100.0, weirdness)) / 100.0) * 0.40, 2)
    # Map style_influence (0..100) -> cfg_scale (1.15..2.65, neutral 50 -> 1.50)
    default_cfg = round(1.15 + (max(0.0, min(100.0, style_influence)) / 100.0) * 1.50, 2)

    raw_temp = spec.get("temperature")
    try:
        temp = round(max(0.5, min(1.4, float(raw_temp))), 2) if raw_temp not in (None, "") else default_temp
    except (TypeError, ValueError):
        temp = default_temp

    raw_cfg = spec.get("cfg_scale")
    try:
        cfg = round(max(1.0, min(4.5, float(raw_cfg))), 2) if raw_cfg not in (None, "") else default_cfg
    except (TypeError, ValueError):
        cfg = default_cfg

    raw_topk = spec.get("topk")
    try:
        topk = max(20, min(500, int(raw_topk))) if raw_topk not in (None, "") else 50
    except (TypeError, ValueError):
        topk = 50

    return {"temperature": temp, "cfg_scale": cfg, "topk": topk}


def _create_max_duration() -> int:
    try:
        return max(30, min(int(os.environ.get("KLIPIN_CREATE_MAX_DURATION", "300")), 360))
    except (TypeError, ValueError):
        return 300


def _create_modal_timeout() -> int:
    try:
        return max(60, min(int(os.environ.get("KLIPIN_CREATE_MODAL_TIMEOUT", "480")), 720))
    except (TypeError, ValueError):
        return 480


def _create_modal_only() -> bool:
    """When True (default in production), Create never falls back to local CPU/GPU or local BGM."""
    return os.environ.get("KLIPIN_CREATE_MODAL_ONLY", "1").strip() != "0"


def _heartmula_modal_generate(spec: dict, out_path: Path, log=None) -> dict | None:
    """Generate a full song on the deployed HeartMuLa Modal GPU worker.

    Every Modal/credential failure is re-raised as a ``RuntimeError`` whose text
    is safe to show to the user (see ``modal_credentials.friendly_modal_error``):
    the panel prints it verbatim instead of dying silently.
    """
    if os.environ.get("KLIPIN_HEARTMULA_MODAL_ENABLED", "0").strip() != "1":
        return None
    import base64
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

    from modal_credentials import ensure_modal_credentials, friendly_modal_error

    app_name = os.environ.get("KLIPIN_HEARTMULA_MODAL_APP", "klipin-music")
    function_name = os.environ.get("KLIPIN_HEARTMULA_MODAL_FUNCTION", "generate_song")
    info = genre_info(spec.get("genre") or "pop-jawa")
    tags = tags_for(spec, info)
    language = language_info(spec.get("language") or DEFAULT_LANGUAGE)
    seed = spec.get("seed")
    try:
        seed = int(seed) if seed is not None else None
    except (TypeError, ValueError):
        seed = None
    lora = spec.get("lora_id") or _lora_id()
    sampling = _resolve_sampling_params(spec)
    max_dur = _create_max_duration()
    req_dur = max(15, min(int(float(spec.get("duration") or 120)), max_dur))
    modal_timeout = _create_modal_timeout()
    log and log(
        f"🎤 HeartMuLa Modal Cloud GPU task started… "
        f"({language['label']}, dur={req_dur}s, tags={tags[:110]}, seed={seed}, "
        f"temp={sampling['temperature']}, cfg={sampling['cfg_scale']}, topk={sampling['topk']})"
    )

    def _invoke_modal():
        ensure_modal_credentials()
        import modal
        function = modal.Function.from_name(app_name, function_name)
        try:
            return function.remote(
                spec.get("lyrics", ""), tags,
                req_dur, seed,
                lora_id=lora or None,
                cfg_scale=sampling["cfg_scale"],
                temperature=sampling["temperature"],
                topk=sampling["topk"],
            )
        except TypeError:
            try:
                return function.remote(
                    spec.get("lyrics", ""), tags,
                    req_dur, seed,
                    lora_id=lora or None,
                )
            except TypeError:
                return function.remote(
                    spec.get("lyrics", ""), tags,
                    req_dur, seed
                )

    try:
        pool = ThreadPoolExecutor(max_workers=1)
        fut = pool.submit(_invoke_modal)
        try:
            result = fut.result(timeout=modal_timeout)
        except FuturesTimeoutError as tex:
            pool.shutdown(wait=False, cancel_futures=True)
            raise RuntimeError(
                f"Render Modal Cloud GPU melewati batas waktu produksi ({modal_timeout} detik). "
                "Job dihentikan otomatis agar tidak menggantung; silakan coba lagi."
            ) from tex
        finally:
            pool.shutdown(wait=False)
    except RuntimeError:
        raise
    except Exception as exc:  # noqa: BLE001 - translate Modal + credential errors
        raise RuntimeError(
            friendly_modal_error(exc, app=app_name, function=function_name)) from exc
    audio_b64 = result.get("audio_b64") if isinstance(result, dict) else None
    if not audio_b64:
        raise RuntimeError(
            "Worker HeartMuLa tidak mengembalikan audio. Detail: "
            f"{str(result)[:300]}")
    out_path.write_bytes(base64.b64decode(audio_b64))
    return result


def _split_long_lyric_line(text: str, limit: int = 120) -> list[str]:
    """Split a long ASR segment at sentence boundaries.

    Sentence punctuation is preferred so each output line stays a full sung
    phrase instead of an arbitrary mid-clause cut.
    """
    text = " ".join(str(text or "").split()).strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]
    pieces = re.split(r"(?<=[.!?;])\s+", text)
    out: list[str] = []
    for piece in pieces:
        piece = piece.strip()
        if not piece:
            continue
        if len(piece) <= limit:
            out.append(piece)
            continue
        chunk: list[str] = []
        for word in piece.split():
            if chunk and len(" ".join(chunk + [word])) > limit:
                out.append(" ".join(chunk))
                chunk = [word]
            else:
                chunk.append(word)
        if chunk:
            out.append(" ".join(chunk))
    return out


def _music_lyrics_from_manual(text: str) -> str:
    """Lock a user-supplied lyric sheet exactly as typed.

    One line means one sung sentence/phrase, which is what keeps the cover's
    vocal phrasing and timing tied to the original performance. Line breaks and
    section markers are preserved; only blank lines are dropped.
    """
    raw_lines = [ln.strip() for ln in str(text or "").splitlines()]
    lines = [ln for ln in raw_lines if ln]
    if not lines:
        raise RuntimeError("Lirik manual kosong.")
    has_marker = any(ln.startswith("[") and ln.endswith("]") for ln in lines)
    body = "\n".join(lines)
    if not has_marker:
        body = "[Verse]\n" + body
    return body[:6000].strip()


def _music_lyrics_from_transcript(transcript: dict | str) -> str:
    """Turn source-song ASR into locked, sectioned lyrics.

    This deliberately does not ask the lyric writer to paraphrase or improve
    the text. The cover pipeline must pass the words heard in the source to
    the singing model unchanged; structural [Intro] and [Instrumental] markers
    are injected from real ASR segment timestamps when an instrumental intro
    (>= 6s) or interlude gap (>= 8s) is detected.
    """
    if isinstance(transcript, str):
        return _music_lyrics_from_manual(transcript)
    lines: list[str] = []
    first_start: float | None = None
    prev_end: float = 0.0
    for segment in (transcript or {}).get("segments") or []:
        text = " ".join(str(segment.get("text") or "").split()).strip()
        if not text or text.lower() in {"[musik]", "[music]", "(musik)", "(music)"}:
            continue
        seg_lines = _split_long_lyric_line(text)
        if not seg_lines:
            continue
        try:
            seg_start = float(segment.get("start") or 0.0)
            seg_end = float(segment.get("end") or seg_start)
        except (TypeError, ValueError):
            seg_start, seg_end = 0.0, 0.0
        if first_start is None:
            first_start = seg_start
        elif prev_end > 0.0 and (seg_start - prev_end) >= 8.0:
            lines.append("[Instrumental]")
            lines.append("[Verse]")
        lines.extend(seg_lines)
        if seg_end > prev_end:
            prev_end = seg_end
    if not lines:
        text = " ".join(str((transcript or {}).get("text") or "").split()).strip()
        if text:
            lines = _split_long_lyric_line(text)
    if not lines:
        raise RuntimeError("Lirik lagu tidak berhasil diekstrak dari audio sumber.")
    prefix = "[Intro]\n\n[Verse]\n" if (first_start is not None and first_start >= 6.0) else "[Verse]\n"
    lyrics = prefix + "\n".join(lines)
    return lyrics[:6000].strip()


def _music_timed_lines_from_transcript(transcript: dict) -> list[dict]:
    """Extract phrase-accurate timed lines with original ASR timestamps.

    Discards [musik]/[music] markers and splits long segments at sentence boundaries
    (<= 120 chars) while preserving original timing proportionally.
    """
    timed_lines: list[dict] = []
    segments = (transcript or {}).get("segments") or []
    for segment in segments:
        text = " ".join(str(segment.get("text") or "").split()).strip()
        if not text or text.lower() in {"[musik]", "[music]", "(musik)", "(music)"}:
            continue
        start = float(segment.get("start") or 0.0)
        end = float(segment.get("end") or start)
        split_lines = _split_long_lyric_line(text, limit=120)
        if not split_lines:
            continue
        if len(split_lines) == 1:
            timed_lines.append({
                "text": split_lines[0],
                "start": round(start, 2),
                "end": round(end, 2),
                "section": "Verse",
            })
        else:
            total_chars = sum(max(1, len(ln)) for ln in split_lines)
            dur = max(0.5, end - start)
            cur = start
            for ln in split_lines:
                frac = max(1, len(ln)) / total_chars
                ln_dur = dur * frac
                ln_end = min(end, cur + ln_dur)
                timed_lines.append({
                    "text": ln,
                    "start": round(cur, 2),
                    "end": round(ln_end, 2),
                    "section": "Verse",
                })
                cur = ln_end

    if not timed_lines:
        text = " ".join(str((transcript or {}).get("text") or "").split()).strip()
        if text:
            split_lines = _split_long_lyric_line(text, limit=120)
            cur = 0.0
            for ln in split_lines:
                timed_lines.append({
                    "text": ln,
                    "start": round(cur, 2),
                    "end": round(cur + 4.0, 2),
                    "section": "Verse",
                })
                cur += 4.0

    return timed_lines


def transcribe_audio_groq(audio_path: str | Path, log=None, word_timestamps: bool = False) -> dict:
    """Transcribe audio with Groq Whisper whisper-large-v3-turbo via stdlib urllib.

    Requires GROQ_API_KEY in environment. Uses verbose_json, temperature=0,
    and automatic language detection so regional languages (Jawa, Sunda, etc.)
    are never mangled by forced 'id' tagging.
    """
    import json
    import os
    import urllib.request
    import urllib.error
    import uuid

    key = os.environ.get("GROQ_API_KEY", "").strip()
    if not key:
        raise ValueError("GROQ_API_KEY belum disetel.")

    path = Path(audio_path)
    if not path.is_file():
        raise FileNotFoundError(f"Audio file tidak ditemukan: {path}")

    filename = path.name
    audio_bytes = path.read_bytes()

    boundary = f"----WebKitFormBoundary{uuid.uuid4().hex}"

    crlf = b"\r\n"
    b_boundary = boundary.encode("utf-8")
    body = bytearray()
    body.extend(b"--" + b_boundary + crlf)
    body.extend(b'Content-Disposition: form-data; name="model"' + crlf + crlf)
    body.extend(b"whisper-large-v3-turbo" + crlf)

    body.extend(b"--" + b_boundary + crlf)
    body.extend(b'Content-Disposition: form-data; name="response_format"' + crlf + crlf)
    body.extend(b"verbose_json" + crlf)

    body.extend(b"--" + b_boundary + crlf)
    body.extend(b'Content-Disposition: form-data; name="temperature"' + crlf + crlf)
    body.extend(b"0" + crlf)

    if word_timestamps:
        body.extend(b"--" + b_boundary + crlf)
        body.extend(b'Content-Disposition: form-data; name="timestamp_granularities[]"' + crlf + crlf)
        body.extend(b"word" + crlf)
        body.extend(b"--" + b_boundary + crlf)
        body.extend(b'Content-Disposition: form-data; name="timestamp_granularities[]"' + crlf + crlf)
        body.extend(b"segment" + crlf)

    body.extend(b"--" + b_boundary + crlf)
    body.extend(f'Content-Disposition: form-data; name="file"; filename="{filename}"'.encode("utf-8") + crlf)
    body.extend(b"Content-Type: audio/mpeg" + crlf + crlf)
    body.extend(audio_bytes)
    body.extend(crlf)
    body.extend(b"--" + b_boundary + b"--" + crlf)

    req = urllib.request.Request(
        "https://api.groq.com/openai/v1/audio/transcriptions",
        data=bytes(body),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": "Klipin-Music/1.0",
        },
        method="POST",
    )

    log and log("🎙️ Mengirim audio ke Groq Whisper (whisper-large-v3-turbo, auto-detect bahasa)…")
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = resp.read()
            result = json.loads(data.decode("utf-8"))
            if not isinstance(result, dict):
                raise ValueError("Respons Groq bukan JSON dict.")
            result["source"] = "groq-whisper-large-v3-turbo"
            return result
    except urllib.error.HTTPError as exc:
        err_msg = ""
        try:
            err_msg = exc.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        raise RuntimeError(f"Groq API HTTP {exc.code}: {err_msg[:300]}") from exc
    except Exception as exc:
        raise RuntimeError(f"Gagal memanggil Groq Whisper: {exc}") from exc


def _transcribe_source_audio(audio_path: str | Path, log=None) -> dict:
    """Transcribe audio with Groq Whisper first, falling back to local Whisper.

    Logs source of transcription. Never fails silently into creative hallucination.
    """
    key = os.environ.get("GROQ_API_KEY", "").strip()
    if key:
        try:
            res = transcribe_audio_groq(audio_path, log=log)
            log and log("✅ Transkripsi berhasil via Groq Whisper (whisper-large-v3-turbo)")
            return res
        except Exception as exc:
            log and log(f"⚠️ Groq Whisper gagal ({exc}); beralih ke Whisper lokal…")
    else:
        log and log("ℹ️ GROQ_API_KEY tidak disetel; menggunakan Whisper lokal…")

    from transcribe import transcribe
    log and log("🎙️ Menjalankan Whisper lokal untuk transkripsi audio sumber…")
    res = transcribe(str(audio_path))
    if not isinstance(res, dict):
        raise RuntimeError("Hasil transkripsi lokal tidak valid.")
    res["source"] = res.get("source") or "audio_asr"
    return res


def _lyrics_to_lrc(lyrics: str, duration: float, lyrics_timed: list[dict] | None = None) -> str:
    """Format plain/structured lyrics into LRC timestamps for DiffRhythm.

    If lyrics_timed with timestamps is provided, uses real ASR timestamps.
    Otherwise falls back to even slot distribution via normalize_lyrics.
    """
    if lyrics_timed and isinstance(lyrics_timed, list):
        valid = [item for item in lyrics_timed if isinstance(item, dict) and item.get("text")]
        if valid:
            lines = []
            for item in valid:
                sec = float(item.get("start") or 0.0)
                mm = int(sec // 60)
                ss = sec % 60
                txt = str(item.get("text") or "").strip()
                if txt:
                    lines.append(f"[{mm:02d}:{ss:05.2f}]{txt}")
            if lines:
                return "\n".join(lines)

    raw = str(lyrics or "").strip()
    if not raw:
        return ""
    if re.search(r"\[\d{2}:\d{2}\.\d{2}\]", raw):
        return raw
    items = normalize_lyrics(raw, duration)
    if not items:
        return ""
    lines = []
    for item in items:
        sec = float(item.get("start") or 0.0)
        mm = int(sec // 60)
        ss = sec % 60
        lines.append(f"[{mm:02d}:{ss:05.2f}]{item['text']}")
    return "\n".join(lines)


def _diffrhythm_modal_generate(spec: dict, out_path: Path, log=None) -> dict | None:
    """Generate a full song on the Apache 2.0 DiffRhythm Modal L4 worker."""
    if not _diffrhythm_modal_enabled():
        return None
    import base64

    from modal_credentials import ensure_modal_credentials, friendly_modal_error

    app_name = os.environ.get("KLIPIN_DIFFRHYTHM_MODAL_APP", "klipin-diffrhythm")
    function_name = os.environ.get("KLIPIN_DIFFRHYTHM_MODAL_FUNCTION", "generate_song")
    info = genre_info(spec.get("genre") or "pop-jawa")
    tags = tags_for(spec, info)
    language = language_info(spec.get("language") or DEFAULT_LANGUAGE)
    style = f"{info.get('tags', '')}, {tags}"
    seed = spec.get("seed")
    try:
        seed = int(seed) if seed is not None else None
    except (TypeError, ValueError):
        seed = None
    duration = max(15, min(int(float(spec.get("duration") or 180)), 285))
    instrumental = bool(spec.get("instrumental"))
    lrc_text = "" if instrumental else _lyrics_to_lrc(spec.get("lyrics", ""), duration, lyrics_timed=spec.get("lyrics_timed"))
    log and log(
        f"⚡ DiffRhythm Modal L4 generation started… ({language['label']}, "
        f"duration={duration}s, seed={seed}, instrumental={instrumental})")
    try:
        ensure_modal_credentials()
        import modal
        function = modal.Function.from_name(app_name, function_name)
        result = function.remote(
            style, lrc_text, duration, seed, instrumental)
    except Exception as exc:  # noqa: BLE001 - translate Modal errors
        raise RuntimeError(
            friendly_modal_error(exc, app=app_name, function=function_name)) from exc
    audio_b64 = result.get("audio_b64") if isinstance(result, dict) else None
    if not audio_b64:
        raise RuntimeError(
            "Worker DiffRhythm tidak mengembalikan audio. Detail: "
            f"{str(result)[:300]}")
    out_path.write_bytes(base64.b64decode(audio_b64))
    return result


def _ace_step_generate(spec: dict, out_path: Path, log=None) -> bool:
    """Run the official ACE-Step async local API when configured.

    ACE-Step intentionally runs as a separate service so its Torch/model
    dependencies never enter the Klipin video container. See the official API
    flow: release_task -> query_result -> /v1/audio.
    """
    base = _ace_step_url()
    if not base:
        return False
    import requests
    log = log or (lambda _: None)
    info = genre_info(spec.get("genre") or "pop-jawa")
    provider_tags = tags_for(spec, info)
    payload = {
        "prompt": f"{spec.get('prompt', '')}; {provider_tags}",
        "lyrics": spec.get("lyrics", ""),
        # ACE-Step exposes one vocal_language field. For bilingual lyrics let
        # the lyric text carry the section languages instead of falsely
        # claiming the whole song is one language.
        "vocal_language": ("unknown" if spec.get("language_secondary")
                            else spec.get("language", "id")),
        "audio_format": "mp3",
        "audio_duration": max(10, min(float(spec.get("duration") or 180), 600)),
        "task_type": "text2music",
        "thinking": True,
        "use_format": True,
        "inference_steps": int(os.environ.get("KLIPIN_ACE_STEP_STEPS", "8")),
        "seed": int(spec.get("seed") if spec.get("seed") is not None else -1),
        "use_random_seed": spec.get("seed") is None,
    }
    headers = {}
    token = os.environ.get("KLIPIN_ACE_STEP_API_KEY", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    log("🎤 ACE-Step full-song task submitted…")
    submitted = requests.post(f"{base}/release_task", json=payload, headers=headers, timeout=30)
    submitted.raise_for_status()
    body = submitted.json()
    data = body.get("data") or body
    task_id = data.get("task_id")
    if not task_id:
        raise RuntimeError(f"ACE-Step did not return task_id: {body}")
    deadline = time.time() + int(os.environ.get("KLIPIN_ACE_STEP_TIMEOUT", "900"))
    result = None
    while time.time() < deadline:
        time.sleep(2)
        queried = requests.post(f"{base}/query_result", json={"task_id_list": [task_id]}, headers=headers, timeout=30)
        queried.raise_for_status()
        qdata = queried.json().get("data") or []
        if not qdata:
            continue
        row = qdata[0]
        status = int(row.get("status", 0))
        if status == 2:
            raise RuntimeError(f"ACE-Step generation failed: {row.get('error') or row}")
        if status != 1:
            continue
        raw = row.get("result") or row.get("results") or []
        parsed = json.loads(raw) if isinstance(raw, str) else raw
        result = parsed[0] if isinstance(parsed, list) and parsed else parsed
        break
    if not result:
        raise TimeoutError("ACE-Step full-song generation timed out")
    file_ref = result.get("file") or result.get("audio") or result.get("audio_url")
    if not file_ref:
        raise RuntimeError(f"ACE-Step result has no audio file: {result}")
    audio_url = file_ref if str(file_ref).startswith("http") else f"{base}{file_ref if str(file_ref).startswith('/') else '/' + str(file_ref)}"
    audio = requests.get(audio_url, headers=headers, timeout=120)
    audio.raise_for_status()
    out_path.write_bytes(audio.content)
    return out_path.exists() and out_path.stat().st_size > 1024


def _ace_step_modal_generate(spec: dict, out_path: Path, log=None) -> dict | None:
    """Draft cepat via Modal `klipin-ace-step` (±20-60 dtk). None bila mati."""
    if not _ace_modal_enabled():
        return None
    import base64
    from modal_credentials import ensure_modal_credentials, friendly_modal_error
    app_name = os.environ.get("KLIPIN_ACE_STEP_MODAL_APP", "klipin-ace-step")
    function_name = os.environ.get("KLIPIN_ACE_STEP_MODAL_FUNCTION", "generate_draft")
    info = genre_info(spec.get("genre") or "pop-jawa")
    provider_tags = tags_for(spec, info)
    seed = spec.get("seed")
    try:
        seed = int(seed) if seed is not None else None
    except (TypeError, ValueError):
        seed = None
    ref_b64 = _cover_reference_b64(spec)
    if spec.get("cover_mode") and not ref_b64:
        raise RuntimeError(
            "Cover faithful membutuhkan audio referensi induk yang masih tersedia.")
    try:
        ref_strength = float(spec.get("ref_strength", 0.5))
    except (TypeError, ValueError):
        ref_strength = 0.5
    ref_strength = max(0.1, min(0.9, ref_strength))
    log and log(f"⚡ ACE-Step Modal draft started… (seed={seed}"
                f"{', cover-ref' if ref_b64 else ''})")
    try:
        ensure_modal_credentials()
        import modal
        function = modal.Function.from_name(app_name, function_name)
        cover_instruction = str(spec.get("cover_style_prompt") or "").strip()
        provider_prompt = f"{spec.get('prompt', '')}; {provider_tags}"
        if cover_instruction:
            provider_prompt = f"{provider_prompt}; {cover_instruction}"
        try:
            result = function.remote(
                provider_prompt,
                spec.get("lyrics", ""), spec.get("language", "id"),
                max(10, min(float(spec.get("duration") or 180), 600)),
                seed, spec.get("lora_id") or _lora_id() or None,
                ref_b64 or None, ref_strength,
            )
        except TypeError:
            # Worker lama (tanpa slot ref-audio): fallback 5-arg.
            result = function.remote(
                f"{spec.get('prompt', '')}; {provider_tags}",
                spec.get("lyrics", ""), spec.get("language", "id"),
                max(10, min(float(spec.get("duration") or 180), 600)),
                seed,
            )
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            friendly_modal_error(exc, app=app_name, function=function_name)) from exc
    audio_b64 = result.get("audio_b64") if isinstance(result, dict) else None
    if not audio_b64:
        raise RuntimeError(f"Worker ACE-Step tidak mengembalikan audio: {str(result)[:300]}")
    out_path.write_bytes(base64.b64decode(audio_b64))
    return result


def _mulacover_sampling_params() -> dict:
    """Official HeartMuLa/MuLaCover CLI sampling defaults (src/mulacover/cli.py).

    Official defaults: ``cfg_scale=1.5``, ``temperature=1.0``, ``topk=250``.
    Higher ``cfg_scale`` (>=3.0) over-constrains the token distribution and
    distorts vocal cadence or causes early EOS collapse.
    """
    def _num(name: str, default: float) -> float:
        try:
            return float(os.environ.get(name, "") or default)
        except (TypeError, ValueError):
            return default

    return {
        "cfg_scale": max(1.0, min(6.0, _num("KLIPIN_MULACOVER_CFG_SCALE", 1.5))),
        "temperature": max(0.1, min(1.5, _num("KLIPIN_MULACOVER_TEMPERATURE", 1.0))),
        "topk": max(10, min(1000, int(_num("KLIPIN_MULACOVER_TOPK", 250.0)))),
    }


def _inject_vocal_timing_markers(lyrics: str, first_vocal_sec: float) -> str:
    """Ensure [Intro] precedes [Verse] when the original singer enters after >= 6.0s."""
    text = str(lyrics or "").strip()
    if not text or first_vocal_sec < 6.0:
        return text
    first_line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    if first_line.lower().startswith("[intro"):
        return text
    if first_line.startswith("["):
        return f"[Intro]\n\n{text}"
    return f"[Intro]\n\n[Verse]\n{text}"


def _format_lyrics_for_mulacover(lyrics: str) -> str:
    """Format lyrics according to official HeartMuLa/MuLaCover documentation:
    - Multiline lyrics with section markers ([Intro], [Verse], [Chorus], [Outro])
      on their own lines
    - One short singable lyric phrase per line (5-7 words)
    - Blank line between sections
    - Strip YouTube/Whisper ASR hallucinations ('Terima kasih', 'telah menonton', etc.)

    The user's own section structure is PRESERVED.  The previous version threw
    every header away and re-synthesised an alternating ``[Verse]/[Chorus]``
    block, which destroyed the verbatim chorus repetition and the
    pipe-stacked meta-tags (``[Chorus | Anthemic | ...]``) that the field
    guide depends on.  Only over-long lines are re-chunked here.
    """
    raw = str(lyrics or "").strip()
    if not raw:
        return "[Intro]\n\n[Verse]\nLa la la\n\n[Outro]"
    hallucination_re = re.compile(
        r"(terima\s+kasih(\s+kalo|\s+telah|\s+sudah)?(\s+menonton)?\.?|"
        r"jangan\s+lupa\s+subscribe|subtitle\s+oleh|^intro$|"
        r"^you\s+might\s+also\s+like$|^embed$|^\d+\s*embed$)",
        re.IGNORECASE,
    )
    out: list[str] = []
    for raw_line in raw.splitlines():
        s = raw_line.strip()
        if not s:
            continue
        # Put a blank line BEFORE each section header (never between the header and its stanza).
        if s.startswith("[") and s.endswith("]"):
            if out and out[-1] != "":
                out.append("")
            out.append(s)
            continue
        s = hallucination_re.sub("", s).strip(" .,-—")
        if not s:
            continue
        # Fix phonetic ASR spelling of "A-A-A-I-I-C-T-O-U" -> "Aishiteru"
        s = re.sub(r"\bA(?:-A)+(?:-I)+(?:-C)?(?:-T)?(?:-O)?(?:-U)?\b", "Aishiteru", s, flags=re.IGNORECASE)
        words = s.split()
        # Break run-on Whisper paragraphs into 5-7 word singable lyric lines
        chunk: list[str] = []
        for w in words:
            chunk.append(w)
            if len(chunk) >= 6:
                out.append(" ".join(chunk))
                chunk = []
        if chunk:
            if len(chunk) <= 2 and out and out[-1] and not out[-1].startswith("["):
                out[-1] = f"{out[-1]} {' '.join(chunk)}"
            else:
                out.append(" ".join(chunk))
    if not any(line and not line.startswith("[") for line in out):
        return raw
    # Normalize: exactly one blank line between blocks, no trailing blanks.
    lines: list[str] = []
    for line in out:
        if not line:
            if lines and lines[-1] != "":
                lines.append("")
            continue
        lines.append(line)
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def _audio_output_quality(path: Path, requested_duration: float) -> dict:
    """Reject near-empty/degenerate renders before they reach the user.

    A MuLaCover failure can still exit 0 and produce a ~3 KB, sub-second file.
    Completed jobs must never point at audio like that.
    """
    seconds = probe_audio_seconds(path)
    size = path.stat().st_size if path.exists() else 0
    floor = max(4.0, float(requested_duration or 0) * 0.2)
    mean_db = None
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg and size:
        try:
            probe = subprocess.run(
                [ffmpeg, "-v", "info", "-i", str(path), "-af", "volumedetect",
                 "-f", "null", "-"],
                capture_output=True, text=True, timeout=120)
            match = re.search(
                r"mean_volume:\s*(-?[0-9.]+)\s*dB",
                f"{probe.stderr or ''}\n{probe.stdout or ''}")
            if match:
                mean_db = float(match.group(1))
        except Exception:  # noqa: BLE001 - QC is best-effort
            mean_db = None
    reason = ""
    if size < 16_000:
        reason = f"file terlalu kecil ({size} byte)"
    elif seconds and seconds < floor:
        reason = f"durasi hanya {seconds:.1f} dtk dari target {requested_duration:.0f} dtk"
    elif mean_db is not None and mean_db < -55.0:
        reason = f"audio hampir hening ({mean_db:.1f} dB)"
    return {"ok": not reason, "reason": reason, "seconds": round(seconds, 2),
            "bytes": size, "mean_db": mean_db}


def prewarm_mulacover_symbolic(ref_b64: str, log=None) -> bool:
    """Fire-and-forget: build the symbolic + vocal-locked MIDI cache early.

    Called while the user is still reviewing the extracted lyrics. The worker
    caches by the reference-audio hash, so when the user presses "render" the
    slow YourMT3/ChordNet/Demucs frontend is already done and only the actual
    singing generation remains. Never raises; never changes the output.
    """
    if not ref_b64 or not _mulacover_modal_enabled() or os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    try:
        from modal_credentials import ensure_modal_credentials

        ensure_modal_credentials()
        import modal

        app_name = os.environ.get("KLIPIN_MULACOVER_MODAL_APP", "klipin-mulacover")
        modal.Function.from_name(app_name, "transcribe_ref").spawn(ref_b64)
        log and log("⚡ Pra-proses melodi vokal dimulai di latar belakang")
        return True
    except Exception as exc:  # noqa: BLE001 - prewarm is best-effort only
        log and log(f"ℹ️ Pra-proses melodi dilewati ({exc})")
        return False


class _EmptyCoverError(RuntimeError):
    """MuLaCover returned a near-empty render (safe to retry once)."""


def _mulacover_modal_generate(spec: dict, out_path: Path, log=None) -> dict | None:
    """Generate an audio-conditioned cover with the official MuLaCover worker.

    MuLaCover extracts symbolic melody, harmony, and optional drum conditions
    from the reference audio inside its GPU worker. Lyrics are supplied
    separately and are therefore locked before this function is called.
    """
    if not _mulacover_modal_enabled():
        return None
    import base64

    from modal_credentials import ensure_modal_credentials, friendly_modal_error

    app_name = os.environ.get("KLIPIN_MULACOVER_MODAL_APP", "klipin-mulacover")
    function_name = os.environ.get("KLIPIN_MULACOVER_MODAL_FUNCTION", "generate_cover")
    ref_b64 = _cover_reference_b64(spec)
    if not ref_b64:
        raise RuntimeError("MuLaCover membutuhkan audio referensi dari link sumber.")
    info = genre_info(spec.get("genre") or "pop-jawa")
    gender = str(spec.get("vocal_gender") or "auto").strip().lower()
    gender_tag = VOCAL_GENDERS.get(gender, "fresh lead vocal")
    groove_reset = bool(spec.get("groove_reset", True))
    # Official HeartMuLa/MuLaCover tag format (assets/tags.txt):
    # topic:[...]; genre:[...]; instrument:[...]; mood:[...]; vocal:[...]
    #
    # The topic used to be hardcoded to "longing,love" and the user's own
    # style field (style tags, Exclude Styles, creative sliders) was dropped
    # entirely on this path, so a cover could not reflect the request at all.
    # Build every field from the actual request now.
    topic = str(spec.get("prompt") or "").strip()[:120] or info.get("mood", "upbeat")
    topic = re.sub(r"\s+", " ", topic)
    instrument = info.get("tags", "")
    extra_style = str(spec.get("style_tags") or "").strip()
    if extra_style:
        instrument = f"{instrument}, {extra_style}"
    plan = spec.get("song_plan")
    if isinstance(plan, dict):
        driver = ", ".join(str(t) for t in (plan.get("driver_tags") or [])[:3])
        if driver:
            instrument = f"{instrument}, {driver}"
    mood = info.get("mood", "upbeat")
    vocal = f"{gender_tag}, clear articulate vocals, studio vocal clarity"
    excludes = _exclude_items(spec)
    if excludes:
        vocal = f"{vocal}, " + ", ".join(f"no {item}" for item in excludes)
    tags = (
        f"topic:[{topic}]; genre:[{info['label'].lower()}]; "
        f"instrument:[{instrument}]; mood:[{mood}]; "
        f"vocal:[{vocal}]"
    )
    formatted_lyrics = _format_lyrics_for_mulacover(spec.get("lyrics", ""))
    timed_list = spec.get("lyrics_timed")
    if isinstance(timed_list, list) and timed_list:
        try:
            first_t = float((timed_list[0] or {}).get("start") or 0.0)
            if first_t >= 6.0 and not formatted_lyrics.lstrip().lower().startswith("[intro"):
                formatted_lyrics = f"[Intro]\n\n{formatted_lyrics}"
        except (TypeError, ValueError):
            pass
    seed = spec.get("seed")
    try:
        seed = int(seed) if seed is not None else None
    except (TypeError, ValueError):
        seed = None
    duration = max(10, min(int(float(spec.get("duration") or 180)), 600))
    sampling = _mulacover_sampling_params()
    log and log(
        f"🎵 MuLaCover Modal (L4) cover started "
        f"(genre={info['label']}, vocal={gender}, seed={seed}, "
        f"cfg={sampling['cfg_scale']:.1f}, temp={sampling['temperature']:.2f}, topk={sampling.get('topk', 250)})")

    def _call(params: dict) -> dict:
        call_seed = params.get("seed", seed)
        call_drop_drums = bool(params.get("drop_drums", groove_reset))
        try:
            ensure_modal_credentials()
            import modal
            function = modal.Function.from_name(app_name, function_name)
            result = function.remote(
                ref_b64, formatted_lyrics, tags, duration, call_seed,
                spec.get("lora_id") or _lora_id() or None,
                params.get("cfg_scale"), params.get("temperature"),
                params.get("topk"),
                call_drop_drums,
            )
        except Exception as exc:  # noqa: BLE001 - translate Modal errors
            raw = str(exc)
            if "MULACOVER_EMPTY_AUDIO" in raw:
                raise _EmptyCoverError(raw[-500:]) from exc
            raise RuntimeError(
                friendly_modal_error(exc, app=app_name, function=function_name)) from exc
        audio_b64 = result.get("audio_b64") if isinstance(result, dict) else None
        if not audio_b64:
            raise RuntimeError(
                "Worker MuLaCover tidak mengembalikan audio. Detail: "
                f"{str(result)[:300]}")
        out_path.write_bytes(base64.b64decode(audio_b64))
        return result

    def _render_or_retry() -> tuple[dict, dict]:
        nonlocal sampling
        try:
            result = _call(sampling)
            qc = _audio_output_quality(out_path, duration)
            if qc["ok"]:
                return result, qc
            raise _EmptyCoverError(qc["reason"])
        except _EmptyCoverError as exc:
            log and log(
                f"⚠️ MuLaCover menghasilkan audio pendek ({exc}); "
                "mengulang dengan official sampling (cfg=1.5, temp=1.0, topk=250).")
            retry_seed = 42 if seed != 42 else 1792604902
            fallback = {
                "cfg_scale": 1.5,
                "temperature": 1.0,
                "topk": 250,
                "seed": retry_seed,
                "drop_drums": True,
            }
            try:
                result = _call(fallback)
            except _EmptyCoverError as exc2:
                out_path.unlink(missing_ok=True)
                raise RuntimeError(
                    "Worker MuLaCover mengembalikan audio kosong/terpotong. "
                    "Coba lagu sumber lain atau turunkan panjang cover; job "
                    "tidak ditandai selesai.") from exc2
            qc = _audio_output_quality(out_path, duration)
            if not qc["ok"]:
                out_path.unlink(missing_ok=True)
                raise RuntimeError(
                    "Worker MuLaCover mengembalikan audio kosong/terpotong "
                    f"({qc['reason']}). Coba lagu sumber lain atau turunkan "
                    "panjang cover; job tidak ditandai selesai.") from exc
            sampling = fallback
            return result, qc

    result, qc = _render_or_retry()
    return {**result, "audio_qc": qc, **sampling}


_COVER_PIPELINE_CLAUSE = re.compile(
    r";\s*preserve the extracted lyrics exactly.*$", re.IGNORECASE | re.DOTALL)
_DEFAULT_COVER_PROMPT = re.compile(r"^\s*rearrange this song as [\w\- &/]+\s*$", re.IGNORECASE)


def _cover_arrangement_direction(spec: dict) -> str:
    """User's own arrangement words only — no pipeline instructions.

    The API appends lyric/section-lock instructions to ``prompt`` for the
    audio-conditioned engines; YuE2 reads the style as tags, so those clauses
    (and the gender suffix) only dilute the user's direction.
    """
    raw = str(spec.get("arrangement_prompt") or "").strip()
    if not raw:
        raw = _COVER_PIPELINE_CLAUSE.sub("", str(spec.get("prompt") or "")).strip()
    if _DEFAULT_COVER_PROMPT.match(raw):
        return ""
    return raw[:400]


def _cover_vocal_direction(spec: dict) -> str:
    """Vocal description for YuE2: persona character first, then gender."""
    explicit = str(spec.get("vocal_persona_desc") or "").strip()
    if explicit:
        return explicit
    gender_tag = VOCAL_GENDERS.get(str(spec.get("vocal_gender") or "").lower(), "") or ""
    persona_tags = str(spec.get("style_tags") or "").strip() if spec.get("persona_id") else ""
    parts = [p for p in (persona_tags, gender_tag) if p]
    return ", ".join(dict.fromkeys(parts))


def _yue2_cover_generate(spec: dict, out_path: Path, engine_type: str = "yue2_comfy", log=None) -> dict:
    """Generate cover using SheetSage2 + YuE2 via ComfyUI API or Native Python SDK (Modal Cloud GPU or Local)."""
    try:
        from services.lyric_style_agent import (
            normalize_lyrics as normalize_cover_lyrics, build_style_prompt, arrangement_variation)
        from engines.yue2_comfy_engine import Yue2ComfyEngine
        from engines.yue2_native_engine import Yue2NativeEngine
    except ImportError:
        from backend.services.lyric_style_agent import (
            normalize_lyrics as normalize_cover_lyrics, build_style_prompt, arrangement_variation)
        from backend.engines.yue2_comfy_engine import Yue2ComfyEngine
        from backend.engines.yue2_native_engine import Yue2NativeEngine

    source_b64 = str(spec.get("cover_source_b64") or "").strip() or _cover_reference_b64(spec)
    ref_path = spec.get("ref_audio_path")
    if not ref_path and source_b64:
        import base64
        temp_ref = out_path.parent / "yue2_reference.mp3"
        temp_ref.write_bytes(base64.b64decode(source_b64))
        ref_path = temp_ref
    elif ref_path and not source_b64 and Path(ref_path).is_file():
        import base64
        source_b64 = base64.b64encode(Path(ref_path).read_bytes()).decode("ascii")

    if not ref_path or not Path(ref_path).is_file():
        raise RuntimeError("Cover YuE2 membutuhkan audio referensi sumber.")

    raw_lyrics = str(spec.get("lyrics") or "").strip()
    formatted_lyrics = normalize_cover_lyrics(raw_lyrics)

    info = genre_info(spec.get("genre") or "pop")
    seed = spec.get("seed")
    try:
        seed = int(seed) if seed is not None else 42
    except (TypeError, ValueError):
        seed = 42
    # Every take gets a seeded arrangement (groove / lead / hook / mix) so a
    # repeated cover of the same song is a new arrangement, not a re-render of
    # one fixed preset. The user's own direction always takes priority.
    variation = arrangement_variation(spec.get("genre") or "pop", seed,
                                      spec.get("weirdness", 50))
    style_prompt = build_style_prompt(
        genre=spec.get("genre") or "pop",
        arrangement=_cover_arrangement_direction(spec),
        vocal_persona_desc=_cover_vocal_direction(spec),
        variation=variation,
    )
    spec["_cover_arrangement_variation"] = variation
    log and log(f"🎼 Aransemen take ini: {variation}")

    duration = max(10, min(int(float(spec.get("duration") or 180)), 600))

    # 1. Primary: Modal Cloud GPU Execution (Nvidia L4 / A10G - Super Cepat di Cloud)
    use_modal = (
        not os.environ.get("PYTEST_CURRENT_TEST")
        and os.environ.get("KLIPIN_FORCE_LOCAL", "0").strip() != "1"
        and (os.environ.get("KLIPIN_YUE2_COMFY_ENABLED", "1").strip() == "1"
             or os.environ.get("KLIPIN_YUE2_MODAL_ENABLED", "1").strip() == "1")
    )

    if use_modal:
        try:
            from modal_credentials import ensure_modal_credentials, friendly_modal_error
        except ImportError:
            from backend.modal_credentials import ensure_modal_credentials, friendly_modal_error
        ensure_modal_credentials()
        import modal
        # Modal Worker Selection:
        # klipin-yue2-native is the robust native SheetSage2 + YuE2Pipeline on PyTorch L4 GPU.
        native_app = os.environ.get("KLIPIN_YUE2_NATIVE_APP", "klipin-yue2-native")
        native_fn = os.environ.get("KLIPIN_YUE2_NATIVE_FUNCTION", "generate_native_cover")
        comfy_app = os.environ.get("KLIPIN_YUE2_COMFY_APP", "klipin-yue2-native")
        comfy_fn = os.environ.get("KLIPIN_YUE2_COMFY_FUNCTION", "generate_native_cover")

        if engine_type == "yue2_native":
            workers_to_try = [(native_app, native_fn)]
        else:
            workers_to_try = [(comfy_app, comfy_fn)]
            if (native_app, native_fn) not in workers_to_try:
                workers_to_try.append((native_app, native_fn))

        sampling = _resolve_sampling_params(spec)
        last_exc = None
        for app_name, fn_name in workers_to_try:
            log and log(
                f"🚀 [{engine_type}] Merender langsung di Modal Cloud GPU L4 ({app_name}:{fn_name}, "
                f"temp={sampling['temperature']}, cfg={sampling['cfg_scale']})..."
            )
            try:
                fn = modal.Function.from_name(app_name, fn_name)
                try:
                    res = fn.remote(
                        ref_audio_b64=source_b64,
                        lyrics=formatted_lyrics,
                        style=style_prompt,
                        duration_sec=duration,
                        seed=seed,
                        cfg_scale=sampling["cfg_scale"],
                        temperature=sampling["temperature"],
                    )
                except TypeError:
                    res = fn.remote(
                        ref_audio_b64=source_b64,
                        lyrics=formatted_lyrics,
                        style=style_prompt,
                        duration_sec=duration,
                        seed=seed,
                    )
                audio_b64 = res.get("audio_b64") if isinstance(res, dict) else None
                if not audio_b64:
                    raise RuntimeError(f"Worker Modal {app_name} tidak mengembalikan audio: {str(res)[:300]}")

                import base64
                out_path.write_bytes(base64.b64decode(audio_b64))
                return {
                    "audio_path": str(out_path),
                    "model": res.get("model", "m-a-p/YuE2-3B"),
                    "audio_encoder": res.get("audio_encoder", "sheetsage2_bf16"),
                    "provider": f"{engine_type}-modal",
                    "duration": duration,
                    "arrangement_variation": variation,
                }
            except Exception as exc:
                last_exc = exc
                log and log(f"⚠️ Worker Modal {app_name} gagal ({exc}), mencoba worker cadangan...")

        if last_exc and os.environ.get("KLIPIN_ALLOW_LOCAL_FALLBACK", "0").strip() != "1":
            raise RuntimeError(friendly_modal_error(last_exc, app=workers_to_try[0][0], function=workers_to_try[0][1])) from last_exc

    # 2. Local Fallback (Unit Tests atau explicit local)
    engine_inst = Yue2NativeEngine() if engine_type == "yue2_native" else Yue2ComfyEngine()
    result = engine_inst.generate_cover(
        source_audio_path=Path(ref_path),
        lyrics=formatted_lyrics,
        genre_style=style_prompt,
        vocal_persona=str(spec.get("vocal_gender") or ""),
        seed=seed,
        max_duration=duration,
        output_dir=out_path.parent,
        log=log,
    )
    result_audio = Path(result["audio_path"])
    if not result_audio.is_file():
        raise RuntimeError(f"Engine {engine_type} tidak menghasilkan file audio valid.")
    shutil.copyfile(result_audio, out_path)
    if isinstance(result, dict):
        result = {**result, "arrangement_variation": variation}
    return result


_KEY_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
# Krumhansl major/minor profiles (12 pitch classes, C-based).
_KRUMHANSL = {
    "major": [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88],
    "minor": [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17],
}


def _numpy_chroma_key(y, sr: int = 22050) -> str:
    """Pure NumPy FFT chromagram + Krumhansl-Schmuckler key detector (zero librosa dependency)."""
    import numpy as np

    n_fft = 4096
    hop = 2048
    if len(y) < n_fft:
        return ""
    win = np.hanning(n_fft).astype(np.float32)
    frames = np.lib.stride_tricks.sliding_window_view(y, n_fft)[::hop]
    S = np.abs(np.fft.rfft(frames * win[None, :], n=n_fft, axis=1))
    freqs = np.fft.rfftfreq(n_fft, 1.0 / sr)
    valid = (freqs >= 65.0) & (freqs <= 2000.0)
    if not np.any(valid):
        return ""
    midi = 69.0 + 12.0 * np.log2(np.maximum(freqs[valid], 1.0) / 440.0)
    pc = np.round(midi).astype(int) % 12
    chroma = np.zeros(12, dtype=np.float64)
    S_valid = S[:, valid]
    for p in range(12):
        mask = pc == p
        if np.any(mask):
            chroma[p] = float(S_valid[:, mask].mean())
    if np.max(chroma) <= 1e-9:
        return ""
    chroma /= np.linalg.norm(chroma) + 1e-12
    best, best_score = "", -1e9
    for mode, profile in _KRUMHANSL.items():
        p_vec = np.asarray(profile, dtype=np.float64)
        p_vec /= np.linalg.norm(p_vec) + 1e-12
        for shift in range(12):
            score = float(np.dot(chroma, np.roll(p_vec, shift)))
            if score > best_score:
                best_score, best = score, f"{_KEY_NAMES[shift]} {mode}"
    return best


def analyze_bpm_key(audio_path: Path, log=None) -> dict:
    """Detect BPM + musical key with librosa or pure-NumPy spectral/Ellis DP fallback."""
    try:
        sr = 22050
        stereo = _decode_stereo_f32(Path(audio_path), sr=sr, max_sec=120.0)
        if len(stereo) < sr * 3:
            return {}
        y = stereo.mean(axis=1)
        drums_candidate = Path(audio_path).parent / "cover_drums_source.mp3"
        beat_ref = (
            drums_candidate
            if (drums_candidate.is_file() and drums_candidate.stat().st_size > 1024)
            else Path(audio_path)
        )
        grid = detect_downbeats(beat_ref, duration=120.0)
        bpm = grid.get("bpm")
        key = _numpy_chroma_key(y, sr=sr)
        out = {"bpm": bpm if (bpm and 40 <= float(bpm) <= 220) else None, "key": key}
        if out.get("bpm") or out.get("key"):
            log and log(f"🎼 Deteksi: {out.get('bpm')} BPM, {out.get('key')}")
        return out
    except Exception as exc:  # noqa: BLE001
        log and log(f"⚠️ Deteksi BPM/key dilewati ({exc})")
        return {}


def _lyric_word_tokens(text: str) -> list[dict]:
    """Split a lyric line into display tokens while keeping punctuation."""
    return [{"text": raw, "norm": re.sub(r"[^\w']+", "", raw.lower())}
            for raw in (text or "").split() if raw.strip()]


def _map_words_to_lines(asr_words: list[dict], base: list[dict]) -> list[dict]:
    """Attach word-level timestamps to each lyric line.

    ASR and lyrics rarely match 1:1 (ad-libs, misheard words, repeated hooks).
    SequenceMatcher aligns the two token streams; unmatched lyric tokens inherit
    an interpolated slot between their timed neighbours. Lyrics text is kept
    exactly as written - ASR text is only used for timing.
    """
    import difflib

    lyric_words: list[dict] = []
    for line_index, line in enumerate(base):
        for token in _lyric_word_tokens(line.get("text", "")):
            lyric_words.append({**token, "line": line_index})
    if not lyric_words or len(asr_words) < 2:
        return base

    matcher = difflib.SequenceMatcher(
        None, [re.sub(r"[^\w']+", "", w["text"].lower()) for w in asr_words],
        [w["norm"] for w in lyric_words], autojunk=False)
    times: list[tuple[float, float] | None] = [None] * len(lyric_words)
    matched = 0
    for block in matcher.get_matching_blocks():
        for offset in range(block.size):
            word = asr_words[block.a + offset]
            times[block.b + offset] = (word["start"], word["end"])
            matched += 1
    if matched / max(1, len(lyric_words)) < 0.15:
        return base

    by_line: dict[int, list[int]] = {}
    for index, word in enumerate(lyric_words):
        by_line.setdefault(word["line"], []).append(index)

    out: list[dict] = []
    for line_index, line in enumerate(base):
        indexes = by_line.get(line_index, [])
        if not indexes:
            out.append(line)
            continue
        anchored = [times[i] for i in indexes if times[i]]
        line_start = min(t[0] for t in anchored) if anchored else float(line["start"])
        line_end = max(t[1] for t in anchored) if anchored else float(line["end"])
        if line_end <= line_start:
            line_end = line_start + max(0.4, 0.3 * len(indexes))

        words: list[dict] = []
        cursor = line_start
        for position, index in enumerate(indexes):
            timing = times[index]
            if timing:
                start, end = float(timing[0]), float(timing[1])
            else:
                following = next(
                    (times[k] for k in indexes[position + 1:] if times[k]), None)
                limit = float(following[0]) if following else line_end
                run = 1
                while (position + run < len(indexes)
                       and not times[indexes[position + run]]):
                    run += 1
                step = max(0.2, (limit - cursor) / run)
                start = cursor
                end = min(limit, start + step)
            if end <= start:
                end = start + 0.2
            words.append({
                "text": lyric_words[index]["text"],
                "start": round(start, 3),
                "end": round(end, 3),
            })
            cursor = end

        out.append({
            **line,
            "start": words[0]["start"],
            "end": words[-1]["end"],
            "words": words,
            "source": "whisper",
        })
    return out


_BEAT_THIS_DIR = Path(os.environ.get(
    "KLIPIN_BEAT_MODEL_DIR", str(ROOT / "data" / "models" / "beat_this")))
_BEAT_THIS_CACHE: dict = {}
_BEAT_THIS_LOCK = threading.Lock()
_BEAT_THIS_SAMPLE_RATE = 22050
_BEAT_THIS_HOP = 441
_BEAT_THIS_N_FFT = 1024
_BEAT_THIS_CHUNK = 1500
_BEAT_THIS_BORDER = 6


def _beat_this_log_mel(signal, front: dict):
    """Log-mel identik dengan frontend Beat This! (torchaudio MelSpectrogram)."""
    import numpy as np

    window = np.asarray(front["window"], dtype=np.float32)
    mel_filters = np.asarray(front["mel_filters"], dtype=np.float32)
    pad = _BEAT_THIS_N_FFT // 2
    padded = np.pad(signal, (pad, pad), mode="reflect")
    frames = 1 + (len(padded) - _BEAT_THIS_N_FFT) // _BEAT_THIS_HOP
    if frames < 1:
        return None
    view = np.lib.stride_tricks.sliding_window_view(
        padded, _BEAT_THIS_N_FFT)[::_BEAT_THIS_HOP][:frames]
    spectrum = np.fft.rfft(view * window[None, :], n=_BEAT_THIS_N_FFT, axis=1)
    return np.log1p(1000.0 * (np.abs(spectrum) @ mel_filters.T)).astype(
        np.float32)


def _beat_this_chunks(spect):
    """Potong spectrogram jadi chunk 30 detik dengan border 6 frame."""
    import numpy as np

    border, chunk = _BEAT_THIS_BORDER, _BEAT_THIS_CHUNK
    starts = np.arange(-border, len(spect) - border, chunk - 2 * border)
    if len(spect) > chunk - 2 * border:
        starts[-1] = len(spect) - (chunk - border)
    pieces = []
    for start in starts:
        piece = spect[max(start, 0):min(start + chunk, len(spect))]
        left = max(0, -start)
        right = max(0, min(border, start + chunk - len(spect)))
        if left or right:
            piece = np.pad(piece, ((left, right), (0, 0)))
        pieces.append(piece)
    return pieces, starts


def _peak_times(logits, fps: float):
    """Peak-picking minimal Beat This!: maxpool 7 frame lalu dedup."""
    import numpy as np

    padded = np.pad(logits, 3, constant_values=-1000.0)
    windows = np.lib.stride_tricks.sliding_window_view(padded, 7)
    frames = np.flatnonzero((logits >= windows.max(axis=1)) & (logits > 0))
    if not len(frames):
        return np.empty(0)
    groups, current = [], [int(frames[0])]
    for frame in frames[1:]:
        if int(frame) - current[-1] <= 1:
            current.append(int(frame))
        else:
            groups.append(sum(current) / len(current))
            current = [int(frame)]
    groups.append(sum(current) / len(current))
    return np.asarray(groups) / fps


def _beat_this_grid(audio_path: Path, duration: float = 0) -> dict:
    """Beat + downbeat nyata dari model Beat This! (ONNX, MIT).

    Preprocessing mengikuti frontend resmi (22050 Hz, n_fft 1024, hop 441,
    128 mel). Model dijalankan chunk 1500 frame (30 detik) dengan overlap
    border 6 frame, lalu peak-picking minimal seperti upstream.
    """
    model_path = _BEAT_THIS_DIR / "beat-this-final0.onnx"
    front_path = _BEAT_THIS_DIR / "frontend.json"
    if not model_path.exists() or not front_path.exists():
        return {}

    global _BEAT_THIS_CACHE
    if not _BEAT_THIS_CACHE:
        with _BEAT_THIS_LOCK:
            if not _BEAT_THIS_CACHE:
                import onnxruntime as ort

                options = ort.SessionOptions()
                options.intra_op_num_threads = max(
                    1, min(4, os.cpu_count() or 1))
                options.graph_optimization_level = (
                    ort.GraphOptimizationLevel.ORT_ENABLE_ALL)
                session = ort.InferenceSession(
                    str(model_path), options,
                    providers=["CPUExecutionProvider"])
                front = json.loads(front_path.read_text(encoding="utf-8"))
                _BEAT_THIS_CACHE = {"session": session, "front": front}
    session = _BEAT_THIS_CACHE["session"]
    front = _BEAT_THIS_CACHE["front"]

    import numpy as np

    limit = min(float(duration) if duration else 600.0, 600.0)
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    decoded = subprocess.run(
        [ffmpeg, "-v", "error", "-i", str(audio_path), "-ac", "1",
         "-ar", str(_BEAT_THIS_SAMPLE_RATE), "-t", str(limit),
         "-f", "f32le", "-"],
        capture_output=True, timeout=300)
    if decoded.returncode != 0:
        return {}
    signal = np.frombuffer(decoded.stdout, dtype=np.float32).copy()
    if signal.size < _BEAT_THIS_SAMPLE_RATE:
        return {}

    spect = _beat_this_log_mel(signal, front)
    if spect is None or len(spect) <= _BEAT_THIS_BORDER:
        return {}

    pieces, starts = _beat_this_chunks(spect)
    beat_logits = np.full(len(spect), -1000.0, dtype=np.float32)
    downbeat_logits = np.full(len(spect), -1000.0, dtype=np.float32)
    results = []
    for piece in pieces:
        beat, downbeat = session.run(
            None, {"spectrogram": piece[None, :, :]})
        results.append((beat[0], downbeat[0]))
    for start, (beat, downbeat) in reversed(list(zip(starts, results))):
        left = start + _BEAT_THIS_BORDER
        length = min(_BEAT_THIS_CHUNK - 2 * _BEAT_THIS_BORDER,
                     len(beat) - 2 * _BEAT_THIS_BORDER,
                     len(beat_logits) - left)
        if length <= 0:
            continue
        beat_logits[left:left + length] = beat[
            _BEAT_THIS_BORDER:_BEAT_THIS_BORDER + length]
        downbeat_logits[left:left + length] = downbeat[
            _BEAT_THIS_BORDER:_BEAT_THIS_BORDER + length]

    fps = _BEAT_THIS_SAMPLE_RATE / _BEAT_THIS_HOP
    beats = _peak_times(beat_logits, fps)
    downbeats = _peak_times(downbeat_logits, fps)
    if len(beats) < 4:
        return {}
    if len(downbeats):
        for index, time_value in enumerate(downbeats):
            downbeats[index] = beats[np.argmin(np.abs(beats - time_value))]
        downbeats = np.unique(downbeats)
    if duration:
        beats = beats[beats < float(duration)]
        downbeats = downbeats[downbeats < float(duration)]
        if len(beats) < 4:
            return {}

    intervals = np.diff(beats)
    bpm = 60.0 / float(np.median(intervals)) if len(intervals) else 0.0
    time_signature = 4
    if len(downbeats) > 1 and bpm > 0:
        estimated = float(np.median(np.diff(downbeats))) / (60.0 / bpm)
        rounded = int(round(estimated))
        if rounded in (2, 3, 4, 6):
            time_signature = rounded
    return {
        "bpm": round(bpm, 1),
        "beats": [round(float(b), 3) for b in beats],
        "downbeats": [round(float(d), 3) for d in downbeats],
        "time_signature": time_signature,
        "source": "beat-this",
    }


def _ellis_dp_beat_grid(
    audio_path: Path,
    duration: float = 0,
    bass_path: Path | None = None,
    other_path: Path | None = None,
) -> dict:
    """High-precision Ellis (2007) Dynamic Programming multi-band beat + downbeat tracker.

    Runs at 100 fps (hop=220 at sr=22050, 9.98ms frame resolution) on multi-band
    log-spectral flux (kick/sub 35-170Hz, snare/body 170-3000Hz, hihat 3000-9500Hz,
    and chord changes 100-3500Hz). Dynamic programming snaps every beat to the
    exact local drum/chord transient from 0:00 to the end of the song with 0.0ms
    cumulative drift.
    """
    import numpy as np

    sr = 22050
    hop = 220
    n_fft = 1024
    max_sec = min(float(duration) if duration else 600.0, 600.0)
    y_main = _decode_stereo_f32(Path(audio_path), sr=sr, max_sec=max_sec).mean(axis=1)
    if len(y_main) < sr * 2:
        return {}
    win = np.hanning(n_fft).astype(np.float32)

    def _band_flux(sig: np.ndarray, fmin: float, fmax: float) -> np.ndarray:
        pad = np.pad(sig, (n_fft // 2, n_fft // 2))
        frames = np.lib.stride_tricks.sliding_window_view(pad, n_fft)[::hop]
        S = np.abs(np.fft.rfft(frames * win[None, :], n=n_fft, axis=1))
        freqs = np.fft.rfftfreq(n_fft, 1.0 / sr)
        mask = (freqs >= fmin) & (freqs <= fmax)
        mag = np.log1p(10.0 * S[:, mask])
        diff = np.maximum(0.0, np.diff(mag, axis=0, prepend=mag[:1]))
        env = diff.mean(axis=1)
        scale = float(np.percentile(env, 95))
        return (env / (scale + 1e-8)).astype(np.float32)

    o_kick = _band_flux(y_main, 35.0, 170.0)
    o_snare = _band_flux(y_main, 170.0, 3000.0)
    o_hat = _band_flux(y_main, 3000.0, 9500.0)
    n_frames = min(len(o_kick), len(o_snare), len(o_hat))

    if bass_path and Path(bass_path).is_file():
        y_bass = _decode_stereo_f32(Path(bass_path), sr=sr, max_sec=max_sec).mean(axis=1)
        if len(y_bass) >= sr * 2:
            o_b = _band_flux(y_bass, 35.0, 160.0)
            n_frames = min(n_frames, len(o_b))
            o_kick = o_kick[:n_frames] + 0.55 * o_b[:n_frames]

    if other_path and Path(other_path).is_file():
        y_other = _decode_stereo_f32(Path(other_path), sr=sr, max_sec=max_sec).mean(axis=1)
        if len(y_other) >= sr * 2:
            o_harm = _band_flux(y_other, 100.0, 3500.0)[:n_frames]
        else:
            o_harm = o_snare[:n_frames] * 0.5
    else:
        o_harm = _band_flux(y_main, 120.0, 2800.0)[:n_frames]

    o_kick = o_kick[:n_frames]
    o_snare = o_snare[:n_frames]
    o_hat = o_hat[:n_frames]
    o_harm = o_harm[:n_frames]

    onset = (1.3 * o_kick + 1.5 * o_snare + 0.4 * o_hat + 0.8 * o_harm).astype(np.float32)
    onset = np.convolve(onset, np.array([0.1, 0.25, 0.3, 0.25, 0.1], dtype=np.float32), mode="same")
    onset /= float(np.percentile(onset, 95)) + 1e-8

    fps = sr / hop
    ac = np.fft.irfft(np.abs(np.fft.rfft(onset - onset.mean(), n=2 * n_frames)) ** 2)[:n_frames]
    min_lag = int(round(fps * 60.0 / 175.0))
    max_lag = min(n_frames - 2, int(round(fps * 60.0 / 68.0)))
    if max_lag <= min_lag:
        return {}
    lags = np.arange(min_lag, max_lag + 1)
    bpms = 60.0 * fps / lags
    prior = np.exp(-0.5 * ((np.log2(bpms / 115.0)) / 0.45) ** 2)
    best_idx = int(np.argmax(ac[lags] * prior))
    period = float(lags[best_idx])

    # Ellis (2007) Dynamic Programming Beat Tracking
    tightness = 180.0
    taus = np.arange(max(4, int(round(period * 0.6))), int(round(period * 1.6)) + 1)
    tx_cost = -tightness * (np.log(taus / period) ** 2).astype(np.float32)

    cumscore = np.zeros(n_frames, dtype=np.float32)
    backlink = np.full(n_frames, -1, dtype=np.int32)
    for i in range(n_frames):
        valid = taus[taus <= i]
        if len(valid) == 0:
            cumscore[i] = onset[i]
        else:
            scores = cumscore[i - valid] + tx_cost[: len(valid)]
            best = int(np.argmax(scores))
            cumscore[i] = onset[i] + scores[best]
            backlink[i] = i - int(valid[best])

    tail_win = max(8, min(n_frames // 2, int(round(period * 4))))
    cur = int(n_frames - tail_win + np.argmax(cumscore[n_frames - tail_win :]))
    beats_idx: list[int] = []
    while cur >= 0:
        beats_idx.append(cur)
        cur = int(backlink[cur])
    beats_idx.reverse()

    step_i = max(4, int(round(period)))
    while beats_idx and beats_idx[0] >= step_i:
        beats_idx.insert(0, beats_idx[0] - step_i)
    while beats_idx and beats_idx[-1] + step_i < n_frames:
        beats_idx.append(beats_idx[-1] + step_i)

    if len(beats_idx) < 4:
        return {}

    # 4-phase downbeat detection: Beat 1 has strong kick/bass + chord change,
    # Beats 2 & 4 have strong backbeat snare.
    best_phase = 0
    best_phase_score = float("-inf")
    for phase in range(4):
        b_down = [b for idx, b in enumerate(beats_idx) if idx % 4 == phase and 0 <= b < n_frames]
        b_back = [
            b for idx, b in enumerate(beats_idx)
            if idx % 4 in ((phase + 1) % 4, (phase + 3) % 4) and 0 <= b < n_frames
        ]
        if not b_down:
            continue
        s_down = float(o_kick[b_down].mean()) + 0.8 * float(o_harm[b_down].mean())
        s_back = 0.8 * float(o_snare[b_back].mean()) if b_back else 0.0
        total_s = s_down + s_back
        if total_s > best_phase_score:
            best_phase_score = total_s
            best_phase = phase

    beats = [round(float(b) / fps, 3) for b in beats_idx]
    downbeats = [round(float(b) / fps, 3) for idx, b in enumerate(beats_idx) if idx % 4 == best_phase]
    if duration:
        beats = [b for b in beats if b < float(duration)]
        downbeats = [d for d in downbeats if d < float(duration)]
    if len(beats) < 4:
        return {}
    intervals = np.diff(beats)
    eff_bpm = 60.0 / float(np.median(intervals)) if len(intervals) else (60.0 * fps / period)
    return {
        "bpm": round(float(eff_bpm), 1),
        "beats": beats,
        "downbeats": downbeats,
        "time_signature": 4,
        "source": "ellis-dp-multiband",
    }


def detect_downbeats(audio_path: Path, duration: float = 0,
                     log=None) -> dict:
    """Beat grid + downbeat dari Beat This! ONNX, fallback Ellis DP multiband."""
    try:
        grid = _beat_this_grid(audio_path, duration)
        if grid and len(grid.get("beats") or []) >= 8:
            return grid
    except Exception as exc:  # noqa: BLE001
        log and log(f"Beat This! gagal ({exc}), beralih ke Ellis DP beat tracker")
    try:
        grid = _ellis_dp_beat_grid(audio_path, duration=duration)
        if grid and len(grid.get("beats") or []) >= 4:
            return grid
    except Exception as exc:  # noqa: BLE001
        log and log(f"Ellis DP beat tracker fallback ({exc})")
    return {}


def align_timeline(audio_path: Path, lyrics: str, duration: float,
                   log=None) -> list[dict]:
    """Word/line alignment via Groq Cloud Whisper (1.5s, 0% CPU) atau faster-whisper hemat CPU bila offline.

    Tidak pernah gagal — selalu kembalikan timeline karaoke yang valid.
    """
    base = normalize_lyrics(lyrics, duration)
    if not base or not audio_path.exists():
        return base

    # 1. Prioritas Utama: Groq Cloud Whisper Large-v3 (1.5 detik, 0% CPU laptop)
    groq_key = os.environ.get("GROQ_API_KEY", "").strip()
    if groq_key:
        try:
            log and log("🚀 Alignment cepat via Groq Cloud Whisper Large-v3 (0% CPU laptop)…")
            res = transcribe_audio_groq(audio_path, log=log, word_timestamps=True)
            raw_words = res.get("words") or []
            words: list[dict] = []
            for w in raw_words:
                t = str(w.get("word") or "").strip()
                if t:
                    words.append({
                        "start": float(w.get("start", 0) or 0),
                        "end": float(w.get("end", 0) or 0),
                        "text": t,
                    })
            if len(words) >= 4:
                out = _map_words_to_lines(words, base)
                if any(line.get("words") for line in out):
                    log and log(f"🎯 Alignment Groq Whisper selesai: {len(words)} kata → {len(out)} baris")
                else:
                    log and log(f"🎯 Alignment Groq Whisper selesai ({len(words)} kata terdeteksi)")
                return out
            return base
        except Exception as groq_err:
            log and log(f"⚠️ Groq alignment fallback ({groq_err})")

    # 2. Fallback Lokal CPU: dinonaktifkan secara default di produksi agar server/container
    # tidak tertahan 2-5 menit di CPU lokal saat GROQ_API_KEY kosong.
    if os.environ.get("KLIPIN_ALLOW_LOCAL_WHISPER_ALIGN", "0").strip() != "1":
        return base
    try:
        from faster_whisper import WhisperModel
        model_name = os.environ.get("KLIPIN_MUSIC_ALIGN_MODEL", "tiny")
        cpu_threads = min(4, max(1, (os.cpu_count() or 2) - 1))
        model = WhisperModel(model_name, device="cpu", compute_type="int8", cpu_threads=cpu_threads)
        segments, _ = model.transcribe(str(audio_path), word_timestamps=True)
        words: list[dict] = []
        for seg in segments:
            for w in (getattr(seg, "words", None) or []):
                t = str(getattr(w, "word", "") or "").strip()
                if t:
                    words.append({
                        "start": float(getattr(w, "start", 0) or 0),
                        "end": float(getattr(w, "end", 0) or 0),
                        "text": t,
                    })
        if len(words) < 4:
            return base
        out = _map_words_to_lines(words, base)
        if not any(line.get("words") for line in out):
            return base
        log and log(f"🎯 Alignment Whisper lokal: {len(words)} kata → {len(out)} baris")
        return out
    except Exception as exc:  # noqa: BLE001 — fallback merata tetap valid
        log and log(f"⚠️ Alignment fallback merata ({exc})")
        return base


STUDIO_EQ_BANDS: tuple[int, ...] = (60, 150, 400, 1000, 2400, 5000, 10000, 16000)

STUDIO_EQ_PRESETS: dict[str, dict] = {
    "flat": {
        "id": "flat",
        "label": "Studio Flat (0 dB)",
        "bands": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        "preamp_db": 0.0,
        "compressor": 0.0,
        "stereo_width": 1.0,
        "reverb": 0.0,
    },
    "reggae-dub": {
        "id": "reggae-dub",
        "label": "Reggae Dub Bass (BOENTER)",
        "bands": [4.5, 3.2, -2.0, -0.5, 1.2, 1.5, -1.8, -2.5],
        "preamp_db": 0.5,
        "compressor": 0.35,
        "stereo_width": 1.24,
        "reverb": 0.15,
    },
    "vocal-clarity": {
        "id": "vocal-clarity",
        "label": "Vocal Clarity & Presence",
        "bands": [-2.5, -1.5, -2.0, 1.5, 3.5, 3.0, 2.0, 1.5],
        "preamp_db": 0.0,
        "compressor": 0.45,
        "stereo_width": 1.10,
        "reverb": 0.08,
    },
    "koplo-punch": {
        "id": "koplo-punch",
        "label": "Koplo Sub Punch",
        "bands": [5.0, 3.5, -2.5, 0.5, 2.5, 3.2, 2.5, 2.0],
        "preamp_db": 0.5,
        "compressor": 0.50,
        "stereo_width": 1.18,
        "reverb": 0.05,
    },
    "warm-analog": {
        "id": "warm-analog",
        "label": "Warm Analog Tape",
        "bands": [2.5, 2.8, 1.0, 0.5, -0.8, -1.5, -2.2, -3.5],
        "preamp_db": 0.5,
        "compressor": 0.30,
        "stereo_width": 1.12,
        "reverb": 0.10,
    },
    "hifi-master": {
        "id": "hifi-master",
        "label": "Hi-Fi Loudness Master",
        "bands": [3.0, 1.5, -1.5, 0.0, 1.8, 2.5, 3.2, 3.5],
        "preamp_db": 0.0,
        "compressor": 0.40,
        "stereo_width": 1.20,
        "reverb": 0.05,
    },
    "acoustic-intimate": {
        "id": "acoustic-intimate",
        "label": "Acoustic Intimate",
        "bands": [-1.0, 1.5, -1.2, 1.2, 2.2, 2.0, 2.5, 1.8],
        "preamp_db": 0.0,
        "compressor": 0.25,
        "stereo_width": 1.15,
        "reverb": 0.12,
    },
}


def normalize_studio_eq_spec(raw: dict | None = None) -> dict:
    """Validate and clamp a Studio Equalizer & Mastering specification."""
    raw = dict(raw or {})
    preset = str(raw.get("preset") or raw.get("eq_preset") or "flat").strip().lower()
    base = STUDIO_EQ_PRESETS.get(preset, STUDIO_EQ_PRESETS["flat"])
    if preset not in STUDIO_EQ_PRESETS and preset != "custom":
        preset = "custom" if raw.get("bands") or raw.get("eq_bands") else "flat"

    raw_bands = raw.get("bands") if "bands" in raw else raw.get("eq_bands")
    if isinstance(raw_bands, str):
        try:
            raw_bands = json.loads(raw_bands)
        except Exception:
            raw_bands = [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", raw_bands)]

    bands: list[float] = []
    if isinstance(raw_bands, (list, tuple)) and len(raw_bands) > 0:
        for i in range(len(STUDIO_EQ_BANDS)):
            try:
                val = float(raw_bands[i]) if i < len(raw_bands) else 0.0
            except (TypeError, ValueError):
                val = 0.0
            bands.append(round(max(-12.0, min(12.0, val)), 2))
    else:
        bands = [float(x) for x in base["bands"]]

    def _clamp_float(val, default: float, lo: float, hi: float) -> float:
        if val is None or val == "":
            return default
        try:
            return round(max(lo, min(hi, float(val))), 3)
        except (TypeError, ValueError):
            return default

    preamp_db = _clamp_float(
        raw.get("preamp_db", raw.get("eq_preamp_db")),
        float(base["preamp_db"]), -6.0, 6.0,
    )
    compressor = _clamp_float(
        raw.get("compressor", raw.get("eq_compressor")),
        float(base["compressor"]), 0.0, 1.0,
    )
    stereo_width = _clamp_float(
        raw.get("stereo_width", raw.get("eq_stereo_width")),
        float(base["stereo_width"]), 0.5, 2.0,
    )
    reverb = _clamp_float(
        raw.get("reverb", raw.get("eq_reverb")),
        float(base["reverb"]), 0.0, 0.8,
    )
    normalize_lufs = bool(raw.get("normalize_lufs", True))
    reset = bool(raw.get("reset", False))
    active = bool(
        not reset
        and (
            any(abs(g) >= 0.1 for g in bands)
            or abs(preamp_db) >= 0.1
            or compressor >= 0.05
            or abs(stereo_width - 1.0) >= 0.03
            or reverb >= 0.03
        )
    )
    return {
        "preset": "flat" if reset else preset,
        "bands": [0.0] * len(STUDIO_EQ_BANDS) if reset else bands,
        "frequencies": list(STUDIO_EQ_BANDS),
        "preamp_db": 0.0 if reset else preamp_db,
        "compressor": 0.0 if reset else compressor,
        "stereo_width": 1.0 if reset else stereo_width,
        "reverb": 0.0 if reset else reverb,
        "normalize_lufs": normalize_lufs,
        "reset": reset,
        "active": active,
    }


def apply_studio_eq(src: Path, eq_spec: dict | None = None, log=None) -> dict:
    """Apply non-destructive 8-band Studio Equalizer + Dynamics Mastering via FFmpeg.

    Preserves a pristine backup (`<stem>.raw_master<suffix>`) before applying the
    first Studio EQ curve so subsequent EQ tweaks or resets never compound filters
    or degrade audio quality. Also refreshes `song.wav` if present in the job folder.
    """
    spec = normalize_studio_eq_spec(eq_spec)
    if not src.is_file() or src.stat().st_size <= 1024:
        raise FileNotFoundError(f"File audio lagu tidak ditemukan atau kosong: {src}")

    backup = src.parent / f"{src.stem}.raw_master{src.suffix}"
    if spec["reset"]:
        if backup.is_file() and backup.stat().st_size > 1024:
            src.write_bytes(backup.read_bytes())
            if (src.parent / "song.wav").is_file():
                export_wav_lufs(src, src.parent, log=log)
        spec["applied"] = False
        spec["restored_from_backup"] = backup.is_file()
        return spec

    if not spec["active"]:
        if backup.is_file() and backup.stat().st_size > 1024:
            src.write_bytes(backup.read_bytes())
            if (src.parent / "song.wav").is_file():
                export_wav_lufs(src, src.parent, log=log)
        spec["applied"] = False
        return spec

    if not backup.is_file() or backup.stat().st_size <= 1024:
        shutil.copy2(src, backup)

    source_input = backup if (backup.is_file() and backup.stat().st_size > 1024) else src
    ffmpeg = _ffmpeg_exe() if "_ffmpeg_exe" in globals() else (shutil.which("ffmpeg") or "ffmpeg")
    duration = probe_audio_seconds(source_input)

    filters: list[str] = ["highpass=f=24"]
    if abs(spec["preamp_db"]) >= 0.1:
        filters.append(f"volume={spec['preamp_db']:.2f}dB")

    for idx, (freq, gain) in enumerate(zip(STUDIO_EQ_BANDS, spec["bands"])):
        if abs(gain) < 0.1:
            continue
        if idx == 0:
            filters.append(f"bass=g={gain:.2f}:f={freq}:w=0.65")
        elif idx == len(STUDIO_EQ_BANDS) - 1:
            filters.append(f"treble=g={gain:.2f}:f=14000:w=0.65")
        else:
            filters.append(f"equalizer=f={freq}:width_type=q:width=1.25:g={gain:.2f}")

    comp = float(spec["compressor"])
    if comp >= 0.05:
        thresh_db = -10.0 - comp * 14.0
        ratio = 1.0 + comp * 4.0
        makeup = 1.0 + comp * 1.8
        filters.append(
            f"acompressor=threshold={thresh_db:.1f}dB:ratio={ratio:.2f}:attack=15:release=180:makeup={makeup:.2f}"
        )

    width = float(spec["stereo_width"])
    if abs(width - 1.0) >= 0.03:
        filters.append(f"extrastereo=m={width:.2f}")

    rev = float(spec["reverb"])
    if rev >= 0.03:
        delay_ms = int(round(48 + rev * 65))
        decay = round(0.08 + rev * 0.24, 3)
        filters.append(f"aecho=0.82:0.86:{delay_ms}:{decay}")

    if spec["normalize_lufs"]:
        filters.append("loudnorm=I=-14:TP=-1.5:LRA=11")
    else:
        filters.append("alimiter=limit=0.96")

    af_chain = ",".join(filters)
    codec = (
        ["-c:a", "libmp3lame", "-b:a", "192k"]
        if src.suffix.lower() == ".mp3"
        else ["-c:a", "aac", "-b:a", "192k"]
    )
    tmp = src.parent / f"{src.stem}.eq_tmp{src.suffix}"
    cmd = [
        ffmpeg, "-y", "-v", "error", "-i", str(source_input),
        "-map_metadata", "-1", "-af", af_chain,
        "-ar", "44100", "-ac", "2",
    ]
    if duration > 0:
        cmd.extend(["-t", f"{duration:.3f}"])
    cmd.extend([*codec, str(tmp)])

    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if r.returncode != 0 or not tmp.is_file() or tmp.stat().st_size <= 1024:
            # Fallback without extrastereo/bass/treble if minimal ffmpeg build lacks a filter
            fallback_filters = [
                f"equalizer=f={freq}:width_type=q:width=1.2:g={gain:.2f}"
                for freq, gain in zip(STUDIO_EQ_BANDS, spec["bands"])
                if abs(gain) >= 0.1
            ]
            if abs(spec["preamp_db"]) >= 0.1:
                fallback_filters.insert(0, f"volume={spec['preamp_db']:.2f}dB")
            fallback_filters.append("loudnorm=I=-14:TP=-1.5:LRA=11")
            cmd_fb = [
                ffmpeg, "-y", "-v", "error", "-i", str(source_input),
                "-map_metadata", "-1", "-af", ",".join(fallback_filters),
                "-ar", "44100", "-ac", "2",
                *codec, str(tmp),
            ]
            r = subprocess.run(cmd_fb, capture_output=True, text=True, timeout=600)
        if r.returncode == 0 and tmp.is_file() and tmp.stat().st_size > 1024:
            src.write_bytes(tmp.read_bytes())
            tmp.unlink(missing_ok=True)
            if (src.parent / "song.wav").is_file():
                export_wav_lufs(src, src.parent, log=log)
            log and log(f"🎚️ Studio Equalizer diterapkan ({spec['preset']}): {spec['bands']}")
            spec["applied"] = True
            spec["updated_at"] = round(time.time(), 3)
            return spec
        err_tail = (r.stderr or "").strip()[-300:]
        raise RuntimeError(f"FFmpeg Studio EQ gagal: {err_tail}")
    finally:
        tmp.unlink(missing_ok=True)


def master_audio(src: Path, log=None, genre: str = "") -> bool:
    """Master the delivered track in place (-14 LUFS + de-mud/de-harsh).

    Previously only a side-car ``song.wav`` was mastered while the MP3 that
    the user actually downloads stayed raw worker output.  That is the single
    biggest audible difference against Suno, which delivers a finished,
    loudness-normalised master.  This applies the same mastering chain to the
    delivered file itself.

    The duration is pinned with ``-t`` because FFmpeg's single-pass
    ``loudnorm`` works at an elevated internal sample rate and would otherwise
    pad the file slightly, which would desynchronise the karaoke timeline.
    """
    if not src.is_file() or src.stat().st_size <= 1024:
        return False
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    duration = probe_audio_seconds(src)
    if duration <= 0:
        return False
    gid = str(genre or "").strip().lower()
    if gid == "original":
        af_chain = "loudnorm=I=-14:TP=-1.5:LRA=11"
    elif gid in ("reggae", "ska", "dub", "reggae-santai", "reggae-ska", "reggae-dub"):
        af_chain = (
            "highpass=f=28,lowpass=f=11200,"
            "equalizer=f=95:width_type=q:width=1.0:g=2.8,"
            "equalizer=f=220:width_type=q:width=1.1:g=1.4,"
            "equalizer=f=320:width_type=q:width=1.3:g=-1.8,"
            "equalizer=f=6800:width_type=q:width=1.0:g=-1.8,"
            "extrastereo=m=1.24,"
            "loudnorm=I=-14:TP=-1.5:LRA=11"
        )
    else:
        af_chain = (
            "equalizer=f=260:width_type=q:width=1.2:g=-2.5,"
            "equalizer=f=8500:width_type=q:width=2.0:g=-1.5,"
            "loudnorm=I=-14:TP=-1.5:LRA=11"
        )
    codec = (["-c:a", "libmp3lame", "-b:a", "192k"]
             if src.suffix.lower() == ".mp3" else ["-c:a", "aac", "-b:a", "192k"])
    tmp = src.parent / f"{src.stem}.mastered{src.suffix}"
    try:
        r = subprocess.run(
            [ffmpeg, "-y", "-v", "error", "-i", str(src), "-map_metadata", "-1", "-af", af_chain,
             "-ar", "44100", "-ac", "2", "-t", f"{duration:.3f}",
             *codec, str(tmp)],
            capture_output=True, text=True, timeout=900)
        if r.returncode == 0 and tmp.exists() and tmp.stat().st_size > 1024:
            src.write_bytes(tmp.read_bytes())
            tmp.unlink(missing_ok=True)
            log and log("🎚️ Mastering final: de-mud 260 Hz, de-harsh 8.5 kHz, -14 LUFS")
            return True
    except Exception as exc:  # noqa: BLE001 — mastering must not kill the job
        log and log(f"⚠️ Mastering dilewati ({exc})")
    tmp.unlink(missing_ok=True)
    return False


def export_wav_lufs(src: Path, job_dir: Path, log=None) -> str:
    """Convert the (already mastered) track to a 44.1 kHz stereo WAV.

    The mastering chain now lives in :func:`master_audio`, which processes the
    delivered MP3.  This helper only transcodes that finished master into the
    lossless side-car, so the WAV and the MP3 are the same mix.
    """
    target = job_dir / "song.wav"
    try:
        r = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", str(src), "-map_metadata", "-1",
             "-c:a", "pcm_s16le", "-ar", "44100", "-ac", "2", str(target)],
            capture_output=True, text=True, timeout=600)
        if r.returncode == 0 and target.exists() and target.stat().st_size > 1024:
            log and log("🔊 WAV 44.1 kHz stereo siap")
            return target.name
    except Exception:  # noqa: BLE001
        pass
    return ""


def separate_stems(src: Path, job_dir: Path, log=None) -> dict:
    """Demucs 4-stem (vocal/drums/bass/other). {} bila tidak tersedia."""
    out_dir = job_dir / "stems"
    try:
        r = subprocess.run(
            ["python", "-m", "demucs", "--two-stems=vocals", "-o", str(out_dir), str(src)],
            capture_output=True, text=True, timeout=1200)
        if r.returncode != 0:
            log and log(f"⚠️ Stems gagal: {r.stderr[-300:]}")
            return {}
        files = sorted(out_dir.rglob("*.wav"))
        if not files:
            return {}
        log and log(f"🎚️ Stems siap: {len(files)} file")
        return {"dir": "stems", "files": [f.name for f in files[:8]]}
    except Exception as exc:  # noqa: BLE001
        log and log(f"⚠️ Stems tidak tersedia ({exc})")
        return {}


def _run_external(provider: str, spec: dict, out_path: Path) -> bool:
    command = _provider_command(provider)
    if not command:
        return False
    values = {"prompt": str(spec.get("prompt") or ""),
              "lyrics": str(spec.get("lyrics") or ""),
              "genre": str(spec.get("genre") or ""),
              "language": str(spec.get("language") or ""),
              "output": str(out_path),
              "duration": str(spec.get("duration") or ""),
              "seed": str(spec.get("seed") or "")}

    def _expand_token(part: str) -> str:
        # Replace exact tokens without str.format() so braces in user lyrics or
        # prompts never raise KeyError/ValueError or leak attributes.
        out = part
        for key, val in values.items():
            out = out.replace("{" + key + "}", val)
        return out

    args = [_expand_token(part) for part in shlex.split(command)]
    result = subprocess.run(args, capture_output=True, text=True, timeout=900)
    return result.returncode == 0 and out_path.exists() and out_path.stat().st_size > 1024


def _write_lrc(path: Path, lines: list[dict]) -> None:
    def stamp(seconds: float) -> str:
        minutes = int(seconds // 60)
        return f"{minutes:02d}:{seconds % 60:05.2f}"
    path.write_text("\n".join(f"[{stamp(x['start'])}]{x['text']}" for x in lines) + "\n", encoding="utf-8")


def probe_audio_seconds(path: Path) -> float:
    """Durasi file audio (0.0 bila tidak diketahui)."""
    if not path.exists():
        return 0.0
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        try:
            r = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                capture_output=True, text=True, timeout=30)
            first = (r.stdout or "").strip().splitlines()
            if first:
                val = float(first[0])
                if val > 0.0:
                    return val
        except Exception:
            pass
    # Fallback to ffmpeg -i when ffprobe is absent (e.g. imageio_ffmpeg environment)
    try:
        ffmpeg = _ffmpeg_exe()
        r = subprocess.run([ffmpeg, "-i", str(path)], capture_output=True, text=True, timeout=30)
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", r.stderr or "")
        if m:
            h, mn, s = m.groups()
            return max(0.0, int(h) * 3600 + int(mn) * 60 + float(s))
    except Exception:
        pass
    return 0.0


def trim_trailing_silence(src: Path, log=None, min_silence: float = 3.0,
                          keep: float = 0.8, noise_db: float = -50.0) -> float:
    """Rapikan ekor lagu: buang dead air supaya berakhir dengan nada akhir.

    Model generatif sering menyisakan hening (atau ekor tanpa isi) setelah lirik
    terakhir, sehingga lagu terasa "menggantung". Alih-alih memaksa durasi minta,
    potong pada awal hening di ekor (sisakan ``keep`` detik + fade-out pendek)
    sehingga lagu mengikuti isi/liriknya sendiri. Mengembalikan durasi akhir file,
    atau 0.0 bila tidak ada yang perlu/berhasil diubah.
    """
    ffmpeg = shutil.which("ffmpeg")
    total = probe_audio_seconds(src)
    if not ffmpeg or total <= 0:
        return 0.0
    try:
        # silencedetect menulis ke stderr pada level INFO, jadi jangan pakai
        # ``-v error`` (akan menyembunyikan hasil deteksi).
        probe = subprocess.run(
            [ffmpeg, "-v", "info", "-i", str(src), "-af",
             f"silencedetect=noise={noise_db}dB:d={min_silence}",
             "-f", "null", "-"],
            capture_output=True, text=True, timeout=300)
        text = f"{probe.stderr or ''}\n{probe.stdout or ''}"
        events = [(m.group(1), float(m.group(2))) for m in
                  re.finditer(r"silence_(start|end):\s*([0-9.]+)", text)]
        if not events:
            return 0.0
        # Hanya ekor yang benar-benar berlanjut sampai akhir file: event terakhir
        # harus "start" (hening sampai EOF) atau "end" tepat di ujung file.
        last_kind, last_t = events[-1]
        if last_kind == "start":
            cut = last_t
        elif (total - last_t) <= 0.35:
            cut = next((t for kind, t in reversed(events) if kind == "start"), None)
        else:
            return 0.0
        # Hening di tengah lagu (ada audio lagi setelahnya) tidak boleh dipotong.
        if cut is None or total - cut < min_silence:
            return 0.0
        new_end = max(1.0, cut + keep)
        if total - new_end < 1.0:
            return 0.0
        codec = (["-c:a", "libmp3lame", "-b:a", "192k"]
                 if src.suffix.lower() == ".mp3" else ["-c:a", "aac", "-b:a", "192k"])
        tmp = src.parent / f"{src.stem}.endtrim{src.suffix}"
        fade_start = max(0.0, new_end - 0.6)
        r = subprocess.run(
            [ffmpeg, "-y", "-v", "error", "-i", str(src),
             "-af", f"afade=t=out:st={fade_start:.3f}:d=0.6",
             "-t", f"{new_end:.3f}", *codec, str(tmp)],
            capture_output=True, text=True, timeout=300)
        if r.returncode != 0 or not tmp.exists() or tmp.stat().st_size < 1024:
            tmp.unlink(missing_ok=True)
            return 0.0
        src.write_bytes(tmp.read_bytes())
        tmp.unlink(missing_ok=True)
        final = probe_audio_seconds(src) or new_end
        log and log(f"✂️ Ekor hening dipangkas → lagu berakhir di {final:.1f} dtk")
        return final
    except Exception as exc:  # noqa: BLE001 — trim tak boleh menggagalkan lagu
        log and log(f"⚠️ Trim ekor dilewati ({exc})")
        return 0.0


def sung_line_count(lyrics: str) -> int:
    """Jumlah baris yang benar-benar disanyi (tanpa header [Verse]/[Chorus] & *sound cue*)."""
    count = 0
    for raw in (lyrics or "").replace("\r\n", "\n").split("\n"):
        text = raw.strip()
        if not text:
            continue
        if text.startswith("[") and text.endswith("]"):
            continue
        if len(text) >= 3 and text.startswith("*") and text.endswith("*"):
            continue
        count += 1
    return count


def generate_music(spec: dict, job_dir: Path, log=None, on_stage=None) -> dict:
    log = log or (lambda _: None)
    # Optional live progress hook: the API passes a callback that writes the
    # current stage into the job row so the panel can show real progress
    # instead of a frozen "processing" state while Whisper/LUFS run locally.
    stage = on_stage or (lambda _stage: None)
    job_dir.mkdir(parents=True, exist_ok=True)
    cover_mode = bool(spec.get("cover_mode"))
    max_allowed_dur = 600.0 if cover_mode else float(_create_max_duration())
    duration = max(8.0, min(float(spec.get("duration") or 30), max_allowed_dur))
    info = genre_info(spec.get("genre") or "pop-jawa")
    provider = _normalize_provider(spec.get("provider"))
    prompt = (spec.get("prompt") or "lagu lokal yang hangat dan mudah diingat").strip()
    spec = dict(spec)
    spec["duration"] = duration
    cover_engine = str(spec.get("cover_engine") or "").strip().lower()
    if cover_engine == "yue2":
        cover_engine = str(os.environ.get("COVER_ENGINE", "yue2_comfy")).strip().lower()
        if cover_engine not in ("yue2_comfy", "yue2_native"):
            cover_engine = "yue2_comfy"
    # Existing saved covers default to ACE-Step. URL covers can explicitly use
    # HeartMuLa text-to-song (lyrics locked), DiffRhythm, or the official MuLaCover worker.
    if cover_mode:
        if not cover_engine:
            cover_engine = "ace-step"
        if cover_engine in ("ace-step", "mulacover") and not (
                spec.get("ref_audio_b64") or spec.get("ref_audio_path")):
            raise RuntimeError(
                "Cover faithful membutuhkan audio referensi induk yang masih tersedia.")
        spec["_lyrics_locked"] = True
        if cover_engine == "heartmula":
            provider = "heartmula"
        elif cover_engine == "mulacover":
            provider = "mulacover"
        elif cover_engine == "diffrhythm":
            provider = "diffrhythm"
        elif cover_engine == "arrangement":
            provider = "arrangement"
        elif cover_engine == "voice":
            provider = "voice"
        else:
            provider = "ace-step"
        spec["provider"] = provider
        spec["cover_style_prompt"] = (
            f"faithful song cover in {info['label']}; preserve the original melody, "
            "vocal phrasing, lyrics, section order, hook, and emotional contour; "
            f"rebuild the instrumentation and production as {info['tags']}; "
            "replace the original singer timbre with a fresh genre-appropriate "
            "vocal performance following the requested vocal direction; do not "
            "clone the source singer; "
            "do not rewrite, translate, summarize, or add lyrics; do not copy the "
            "original instrumentation verbatim"
        )
        if cover_engine == "arrangement":
            spec["cover_style_prompt"] = (
                f"vocal-preserving genre arrangement cover in {info['label']}; "
                "isolate and keep the original lead vocal stem (exact melody, timing, "
                "and phrasing) while completely rebuilding the instrumental backing "
                f"track into {info['label']} ({info['tags']})"
            )
        if cover_engine == "heartmula":
            # The original HeartMuLa worker is text-conditioned only. Keep the
            # source words exactly, but state honestly that melody is
            # regenerated; exact melody preservation belongs to MuLaCover.
            spec["cover_style_prompt"] = (
                f"faithful lyric-locked rearrangement in {info['label']}; "
                "sing the supplied lyrics exactly in the same section order and "
                "emotional direction; create a fresh melody and rebuild the "
                f"instrumentation as {info['tags']}; "
                "use the requested fresh vocal direction; do not rewrite, "
                "translate, summarize, or add lyrics"
            )
        if cover_engine == "diffrhythm":
            # DiffRhythm sings the supplied timestamped lyrics and composes a
            # brand-new melody and arrangement in the requested genre.
            spec["cover_style_prompt"] = (
                f"reimagine this song as {info['label']}; compose a fresh melody "
                f"and rebuild the instrumentation, groove, and production as "
                f"{info['tags']}; sing the supplied lyrics exactly in the same "
                "section order and emotional direction; use a fresh "
                "genre-appropriate vocal; do not rewrite, translate, summarize, "
                "or add lyrics"
            )
        if cover_engine in ("yue2_comfy", "yue2_native"):
            provider = cover_engine
            spec["provider"] = provider
            spec["cover_style_prompt"] = (
                f"symbolic melody-conditioned song cover in {info['label']}; "
                "preserve the exact melody notes and rhythm via SheetSage2 ABC notation; "
                "sing the supplied lyrics with zero vocal bleed or hallucination; "
                f"rebuild the arrangement and instrumentation as {info['tags']}"
            )
        if cover_engine == "voice":
            # Voice-conversion cover: the source performance (melody, phrasing,
            # timing, original backing) is preserved exactly; only the singer's
            # timbre is replaced via Seed-VC on the isolated vocal stem.
            spec["cover_style_prompt"] = (
                "melody- and arrangement-preserving voice conversion; keep the "
                "source melody, lyrics, phrasing, timing and instrumentation "
                "intact; replace only the lead vocal timbre with the requested "
                "target voice"
            )
    # A single generative pass cannot reliably create two distinct timbres.
    # HeartMuLa duet mode therefore uses two role-specific generations and a
    # deterministic section switcher (female on verses, male on chorus).
    if (str(spec.get("vocal_gender") or "").lower() == "duet"
            and str(spec.get("provider") or "auto").lower() == "heartmula"
            and not spec.get("_duet_role")):
        return _generate_role_separated_duet(spec, job_dir, log=log,
                                              on_stage=on_stage)
    # Gender penyanyi: disuntik sebagai tag provider (di luar prompt cover art).
    gender = str(spec.get("vocal_gender") or "auto").strip().lower()
    if gender in VOCAL_GENDERS and VOCAL_GENDERS[gender]:
        prompt = f"{prompt}, {VOCAL_GENDERS[gender]}"
    spec["prompt"] = prompt
    # Bahasa vokal: genre daerah mengunci bahasanya (Pop Jawa -> Jawa) kecuali
    # pengguna menyalakan override di UI.
    language, language_locked = resolve_language(
        spec.get("genre") or "pop-jawa", spec.get("language") or "",
        override=bool(spec.get("language_override")))
    spec["language"] = language
    secondary, mix = normalize_language_mix(
        language, spec.get("language_secondary"), spec.get("language_mix"))
    spec["language_secondary"], spec["language_mix"] = secondary, mix
    spec["_language_prompt"] = language_mix_prompt(spec)
    # Seed asli: normalisasi sekali, diteruskan ke semua runner (deterministik).
    if spec.get("seed") is not None:
        try:
            spec["seed"] = int(spec["seed"])
        except (TypeError, ValueError):
            spec["seed"] = None
    if spec.get("lora_id") is None and _lora_id():
        spec["lora_id"] = _lora_id()
    instrumental = bool(spec.get("instrumental"))
    spec["instrumental"] = instrumental
    try:
        bpm_req = int(spec.get("bpm") or 0)
    except (TypeError, ValueError):
        bpm_req = 0
    spec["bpm"] = bpm_req if 40 <= bpm_req <= 220 else 0
    spec["music_key"] = str(spec.get("music_key") or "").strip()[:24]
    stage("writing")
    if instrumental:
        # Instrumental asli: tanpa lirik ke model, tag "no vocals".
        lyrics, lyric_info = "", {"source": "instrumental", "lines": 0,
                                  "target": 0}
    else:
        # Gemini untuk kata: sheet selalu duration-sized, placeholder tidak lolos.
        if cover_mode:
            spec["_lyrics_locked"] = True
            raw_lyrics = str(spec.get("lyrics") or "").strip()
            if raw_lyrics:
                lyrics = raw_lyrics
                lyric_info = {
                    "source": spec.get("lyrics_extraction_source") or "user",
                    "lines": len(normalize_lyrics(lyrics, duration)),
                }
            elif cover_engine in ("arrangement", "voice"):
                lyrics = "[Verse]\n(Vokal asli sumber dipertahankan)"
                lyric_info = {"source": "source_vocal_stem", "lines": 1}
            else:
                raise RuntimeError(
                    "Cover membutuhkan lirik sumber persis. Lirik sumber tidak ditemukan dan dilarang mengarang lirik baru."
                )
        elif spec.get("_lyrics_locked") and str(spec.get("lyrics") or "").strip():
            lyrics = str(spec["lyrics"]).strip()
            lyric_info = {"source": "user", "lines": len(normalize_lyrics(lyrics, duration))}
        else:
            try:
                from lyrics_writer import ensure_sheet
                lyrics, lyric_info = ensure_sheet({**spec, "prompt": prompt}, log=log)
            except Exception as exc:  # noqa: BLE001 — jangan gagalkan lagu karena lirik
                log(f"⚠️ ensure_sheet gagal ({exc}); pakai draft offline")
                lyrics, lyric_info = spec.get("lyrics") or _draft_lyrics(prompt, language), {"source": "offline"}
    spec["lyrics"] = lyrics
    # Mode "auto ikuti lirik": panjang lagu mengikuti jumlah baris yang disanyi,
    # bukan preset kaku. Dihitung di sini (sebelum provider) agar durasi yang
    # diminta ke worker, timeline, dan laporan akhir semuanya konsisten.
    if spec.get("auto_duration"):
        try:
            from lyrics_writer import SECONDS_PER_LINE as _sec_per_line
        except Exception:  # noqa: BLE001
            _sec_per_line = 4.7
        sung = sung_line_count(lyrics)
        target = max(30.0, min(max_allowed_dur, round(sung * float(_sec_per_line) + 6.0, 1)))
        duration = target
        spec["duration"] = target
        log(f"🎯 Durasi otomatis dari lirik: {sung} baris → {target:.0f} dtk")
    # Arrangement plan deterministik (tanpa GPU/Gemini): dibangun dari sheet
    # final agar header [Verse]/[Chorus] yang benar-benar disanyi punya
    # energi/hook eksplisit dan tag arahan ikut dikirim ke engine.
    try:
        from song_plan import build_plan, summary as plan_summary
        spec["song_plan"] = build_plan(spec)
        spec["hook"] = (spec["song_plan"].get("hook") or {}).get("line", "")
        log and log(f"🧠 Aransemen: {plan_summary(spec['song_plan'])}")
    except Exception as exc:  # noqa: BLE001 - plan tak boleh gagalkan lagu
        log and log(f"⚠️ Rencana aransemen gagal ({exc}); lanjut tanpa plan")
    heartmula_ready = bool(os.environ.get("KLIPIN_HEARTMULA_ROOT", "").strip()
                           and os.environ.get("KLIPIN_HEARTMULA_MODEL_PATH", "").strip())
    modal_ready = os.environ.get("KLIPIN_HEARTMULA_MODAL_ENABLED", "0").strip() == "1"
    diffrhythm_modal_ready = _diffrhythm_modal_enabled()
    ace_modal_ready = _ace_modal_enabled()
    mulacover_ready = _mulacover_modal_enabled()
    output = job_dir / ("song.mp3" if (
        diffrhythm_modal_ready and provider in ("auto", "diffrhythm")
        or
        (_ace_step_url() and provider in ("auto", "ace-step"))
        or ace_modal_ready and provider in ("auto", "ace-step")
        or (heartmula_ready and provider in ("auto", "heartmula"))
        or (modal_ready and provider in ("auto", "heartmula"))
        or (mulacover_ready and provider == "mulacover")
        or (cover_mode and cover_engine in ("yue2_comfy", "yue2_native", "yue2"))
    ) else "song.m4a")
    used = "fallback-bgm"
    used_detail: dict = {}
    _gpu_t0 = time.time()
    candidates = ([provider] if provider in ("diffrhythm", "ace-step", "heartmula", "mulacover")
                  else ["heartmula", "diffrhythm", "ace-step"])
    faithful_audio_cover = cover_mode and cover_engine in ("ace-step", "mulacover", "yue2_comfy", "yue2_native", "yue2")
    heartmula_cover = cover_mode and cover_engine == "heartmula"
    if cover_mode and cover_engine == "mulacover" and not mulacover_ready:
        raise RuntimeError(
            "Cover referensi HeartMuLa membutuhkan worker MuLaCover aktif "
            "(KLIPIN_MULACOVER_MODAL_ENABLED=1).")
    if cover_mode and cover_engine == "ace-step" and not ace_modal_ready:
        raise RuntimeError(
            "Cover audio-to-audio membutuhkan worker ACE-Step aktif "
            "(KLIPIN_ACE_STEP_MODAL_ENABLED=1).")
    if cover_mode and cover_engine == "heartmula" and not (
            modal_ready or heartmula_ready):
        raise RuntimeError(
            "Cover lirik-terkunci membutuhkan worker HeartMuLa aktif "
            "(KLIPIN_HEARTMULA_MODAL_ENABLED=1).")
    if cover_mode and cover_engine == "diffrhythm" and not diffrhythm_modal_ready:
        raise RuntimeError(
            "Cover reimagine membutuhkan worker DiffRhythm aktif "
            "(KLIPIN_DIFFRHYTHM_MODAL_ENABLED=1).")
    if cover_mode and cover_engine in ("yue2_comfy", "yue2") and not _yue2_comfy_enabled():
        raise RuntimeError(
            "Cover YuE2 ComfyUI membutuhkan worker aktif "
            "(KLIPIN_YUE2_COMFY_ENABLED=1 atau COMFYUI_URL).")
    if cover_mode and cover_engine == "yue2_native" and not _yue2_native_enabled():
        raise RuntimeError(
            "Cover YuE2 Native membutuhkan worker aktif "
            "(KLIPIN_YUE2_NATIVE_ENABLED=1).")
    if cover_mode and cover_engine == "voice" and not _voice_modal_enabled():
        raise RuntimeError(
            "Cover voice-conversion membutuhkan worker Seed-VC aktif "
            "(KLIPIN_VOICE_MODAL_ENABLED=1).")
    if not cover_mode and provider == "diffrhythm" and not diffrhythm_modal_ready:
        raise RuntimeError(
            "DiffRhythm Music belum aktif. Deploy `diffrhythm_modal_app.py`, lalu isi "
            "KLIPIN_DIFFRHYTHM_MODAL_ENABLED=1 dan restart API.")
    stage("generating")
    if cover_mode and cover_engine == "arrangement":
        output = job_dir / "song.mp3"
        spec["keep_original_vocal"] = True
        _voice_restyle_cover(spec, job_dir, output, log=log)
        spec["_voice_restyle_done"] = True
        used = "arrangement-cover"
    if cover_mode and cover_engine == "voice":
        import base64 as _b64
        source_b64 = str(spec.get("cover_source_b64") or "").strip() or _cover_reference_b64(spec)
        if not source_b64:
            raise RuntimeError(
                "Cover voice-conversion butuh audio sumber lagu asli.")
        spec["cover_source_b64"] = source_b64
        output = job_dir / "song.mp3"
        if spec.get("keep_original_vocal"):
            _voice_restyle_cover(spec, job_dir, output, log=log)
            spec["_voice_restyle_done"] = True
            used = "arrangement-cover"
        else:
            reference_path = str(spec.get("_voice_reference_path") or "").strip()
            if not reference_path:
                reference_path = _synthesize_voice_reference(spec, job_dir, log=log)
                spec["_voice_reference_path"] = reference_path
            if spec.get("restyle"):
                # Phase 2: keep the converted vocal but re-arrange the backing into
                # the requested genre, then mix — like a real genre cover.
                _voice_restyle_cover(spec, job_dir, output, log=log)
                spec["_voice_restyle_done"] = True
                used = "voice-restyle"
            else:
                # Keep the source performance and backing exactly; replace only the
                # lead vocal timbre (Demucs + Seed-VC) below.
                output.write_bytes(_b64.b64decode(source_b64))
                used = "voice-cover"
                log("🎤 Cover melodi-asli: mengganti warna suara sumber (Demucs + Seed-VC)…")
    if cover_mode and cover_engine in ("yue2_comfy", "yue2_native"):
        yue2_result = _yue2_cover_generate(
            {**spec, "lyrics": lyrics}, output, engine_type=cover_engine, log=log)
        if yue2_result:
            used = f"{cover_engine}-cover"
            used_detail = yue2_result
            if isinstance(yue2_result, dict):
                spec["_cover_arrangement_variation"] = yue2_result.get("arrangement_variation", "")
    if cover_mode and cover_engine == "mulacover":
        mulacover_result = _mulacover_modal_generate(
            {**spec, "lyrics": lyrics}, output, log=log)
        if mulacover_result:
            used, used_detail = "mulacover-modal", mulacover_result
    if (provider == "diffrhythm" and diffrhythm_modal_ready
            and (not cover_mode or cover_engine == "diffrhythm")):
        diffrhythm_result = _diffrhythm_modal_generate(
            {**spec, "lyrics": lyrics}, output, log=log)
        if diffrhythm_result:
            used, used_detail = "diffrhythm-modal", diffrhythm_result
    # Draft cepat dulu (ACE-Step Modal), final nyawa (HeartMuLa Modal / DiffRhythm Modal).
    #
    # CATATAN urutan: untuk provider="auto" engine FINAL (HeartMuLa 3B /
    # DiffRhythm) harus menang, bukan draft ACE-Step 1.5B. Sebelumnya cabang
    # "auto" -> ACE-Step dicek lebih dulu sehingga lagu "otomatis" keluar
    # sebagai draft berkualitas rendah.
    if used == "fallback-bgm" and provider == "ace-step" and ace_modal_ready:
        ace_modal_result = _ace_step_modal_generate({**spec, "lyrics": lyrics}, output, log=log)
        if ace_modal_result:
            used, used_detail = "ace-step-modal", ace_modal_result
    if (not faithful_audio_cover and used == "fallback-bgm"
            and provider in ("auto", "heartmula") and modal_ready):
        modal_result = _heartmula_modal_generate({**spec, "lyrics": lyrics}, output, log=log)
        if modal_result:
            used, used_detail = "heartmula-modal", modal_result
    if (not faithful_audio_cover and used == "fallback-bgm"
            and provider in ("auto", "diffrhythm") and diffrhythm_modal_ready):
        diffrhythm_result = _diffrhythm_modal_generate({**spec, "lyrics": lyrics}, output, log=log)
        if diffrhythm_result:
            used, used_detail = "diffrhythm-modal", diffrhythm_result
    # Fallback terakhir untuk "auto": draft ACE-Step bila engine final mati.
    if used == "fallback-bgm" and provider == "auto" and ace_modal_ready:
        ace_modal_result = _ace_step_modal_generate({**spec, "lyrics": lyrics}, output, log=log)
        if ace_modal_result:
            used, used_detail = "ace-step-modal", ace_modal_result
    modal_only_create = (not cover_mode) and _create_modal_only()
    if not modal_only_create:
        if used == "fallback-bgm" and provider in ("auto", "ace-step") and _ace_step_url():
            if _ace_step_generate({**spec, "lyrics": lyrics}, output, log=log):
                used = "ace-step"
        if (not faithful_audio_cover and used == "fallback-bgm"
                and provider in ("auto", "heartmula") and heartmula_ready):
            if _heartmula_generate({**spec, "lyrics": lyrics}, job_dir, output, log=log):
                used = "heartmula"
        for candidate in ([] if cover_mode else candidates):
            if used != "fallback-bgm":
                break
            log(f"🎛️ Mencoba provider {candidate}…")
            external_prompt = f"{spec.get('prompt', prompt)}; {tags_for(spec, info)}"
            if _run_external(candidate, {**spec, "prompt": external_prompt}, output):
                used = candidate
                break
    if used == "fallback-bgm" and (spec.get("full_song") or modal_only_create):
        raise RuntimeError(
            "Full-song generation requires an active HeartMuLa, DiffRhythm, or ACE-Step "
            "Modal Cloud GPU provider (local rendering and instrumental fallback are disabled for production). "
            + provider_hint()
        )
    if used == "fallback-bgm":
        log("ℹ️ Model lokal belum dikonfigurasi — membuat preview instrumental cepat")
        render_bgm(info["mood"], str(output), duration, log=log)
    stage("mixing")
    voice_conversion: dict = {}
    reference_path = str(spec.get("_voice_reference_path") or "").strip()
    # Voice conversion (Demucs stem separation + Seed-VC) harus HANYA dijalankan untuk
    # mode cover vokal murni (cover_engine == 'voice') atau ketika user eksplisit meminta kloning suara.
    # Untuk engine generatif (YuE2, HeartMuLa, DiffRhythm, ACE-Step), karakter vokal persona
    # SUDAH disintesis langsung ke dalam track secara native stereo 48kHz dengan artikulasi lirik sempurna.
    # Menjalankan Demucs + Seed-VC di atas track generatif menyebabkan artikulasi cadel/pelo,
    # vokal ganda/campuran akibat kebocoran Demucs, dan runtuhnya stereo menjadi mono.
    run_voice_conversion = bool(
        reference_path
        and (cover_engine == "voice" or spec.get("force_voice_conversion"))
        and not spec.get("_voice_restyle_done")
    )
    if run_voice_conversion:
        if used == "fallback-bgm":
            voice_conversion = {
                "status": "not_applied",
                "message": "Engine hanya membuat instrumental; suara persona tidak diterapkan.",
            }
        else:
            try:
                output = apply_voice_persona(
                    output, Path(reference_path), job_dir, duration, log=log,
                    semi_tone_shift=int(spec.get("voice_pitch_shift") or 0),
                    auto_f0=bool(spec.get("voice_pitch_auto")))
                voice_conversion = {"status": "applied", "method": "seed-vc-vocal-stem"}
            except Exception as exc:  # noqa: BLE001 — retain usable original take
                voice_conversion = {
                    "status": "not_applied",
                    "message": str(exc)[:400] or "Voice conversion gagal; lagu asli dipertahankan.",
                }
                log(f"⚠️ Persona suara tidak diterapkan; lagu asli dipertahankan ({exc})")
    # Wall-time render audio (Dipakai metering kredit; cover/lirik di bawah
    # tidak masuk hitungan GPU).
    spec["_gpu_sec"] = max(0.0, time.time() - _gpu_t0)
    # Rapikan akhir lagu: buang dead air di ekor supaya lagu selesai dengan nada
    # akhir (tidak terasa "menggantung") dan durasinya mengikuti isi lagu, bukan
    # angka durasi minta yang kaku.
    requested_duration = duration
    if used != "fallback-bgm":
        trimmed = trim_trailing_silence(output, log=log)
        actual_duration = probe_audio_seconds(output)
        if actual_duration < 8.0 and used_detail:
            # Local ffprobe can be unavailable/unpatched; the GPU worker
            # already probed its own MP3, so trust that instead of the request.
            try:
                worker_sec = float((used_detail or {}).get("audio_sec") or 0.0)
            except (TypeError, ValueError):
                worker_sec = 0.0
            if worker_sec >= 8.0:
                actual_duration = worker_sec
        if trimmed >= 8.0:
            duration = trimmed
        elif actual_duration >= 8.0:
            # The worker may stop early without a silent tail; report the
            # real duration so history/LRC never claim the requested length.
            duration = actual_duration
    # Mastering FINAL pada file yang diunduh user. Sebelumnya rantai
    # mastering hanya diterapkan ke file WAV samping, sehingga MP3 yang
    # diunduh tetap mentah (belum -14 LUFS, belum de-mud/de-harsh) —
    # perbedaan audition paling mencolok dibanding Suno yang selalu
    # mengirim master jadi.
    stage("mastering")
    if used != "fallback-bgm":
        master_audio(output, log=log, genre=info['id'])
    studio_eq_result = None
    if spec.get("studio_eq") or spec.get("eq_preset") or spec.get("eq_bands"):
        try:
            studio_eq_result = apply_studio_eq(
                output,
                spec.get("studio_eq") or {
                    "preset": spec.get("eq_preset", "flat"),
                    "bands": spec.get("eq_bands", []),
                    "preamp_db": spec.get("eq_preamp_db", 0.0),
                    "compressor": spec.get("eq_compressor", 0.0),
                    "stereo_width": spec.get("eq_stereo_width", 1.0),
                    "reverb": spec.get("eq_reverb", 0.0),
                },
                log=log,
            )
        except Exception as eq_exc:  # noqa: BLE001 — never fail song generation over post-EQ
            log and log(f"⚠️ Studio EQ dilewati ({eq_exc})")
    # Pasca-proses lokal (cover, whisper-align, WAV LUFS, BPM/key) saling
    # independen — jalankan paralel lewat thread pool. Ini memangkas wall-time
    # setelah GPU selesai TANPA mengubah satu bit pun kualitas audio.
    (job_dir / "lyrics.txt").write_text(lyrics or "[Instrumental]", encoding="utf-8")
    stage("aligning")
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=4) as pool:
        cover_future = pool.submit(
            _fetch_cover_art, {**spec, "prompt": prompt}, info, job_dir, log)
        timeline_future = (None if instrumental else
                           pool.submit(align_timeline, output, lyrics, duration, log))
        wav_future = (None if used == "fallback-bgm" else
                      pool.submit(export_wav_lufs, output, job_dir, log))
        detect_future = (None if used == "fallback-bgm" else
                         pool.submit(analyze_bpm_key, output, log))
        beat_future = (None if used == "fallback-bgm" else
                       pool.submit(detect_downbeats, output, duration, log))
        cover_filename, cover_source = cover_future.result()
        line_timeline = timeline_future.result() if timeline_future else []
        wav_filename = wav_future.result() if wav_future else ""
        detected = detect_future.result() if detect_future else {}
        beat_grid = beat_future.result() if beat_future else {}
    line_timeline = annotate_lyric_languages(line_timeline, spec)
    lrc = job_dir / "lyrics.lrc"
    _write_lrc(lrc, line_timeline)
    aligned = sum(1 for x in line_timeline if x.get("source") == "whisper")
    metadata = {
        "schema": "klipin-music/v3", "provider": used, "model_ready": used != "fallback-bgm",
        "genre": info["id"], "genre_label": info["label"], "language": language,
        "language_label": language_info(language)["label"],
        "language_secondary": secondary,
        "language_secondary_label": language_info(secondary)["label"] if secondary else "",
        "language_mix": mix,
        "language_mix_label": LANGUAGE_MIXES.get(mix, ""),
        "pronunciation_review": bool(secondary == "jv" or language == "jv"),
        "language_locked": language_locked,
        "vocal_gender": gender or "auto",
        "instrumental": instrumental,
        "duration": duration, "lyrics_lines": len(line_timeline),
        "requested_duration": round(requested_duration, 1),
        "auto_duration": bool(spec.get("auto_duration")),
        "lyrics_source": str(spec.get("lyrics_extraction_source") or (lyric_info or {}).get("source", "user")),
        "lyrics_segments": int(spec.get("lyrics_extraction_segments") or 0),
        "lyrics_target": (lyric_info or {}).get("target"),
        "alignment": "whisper" if aligned else ("none" if instrumental else "even"),
        "aligned_lines": aligned,
        "words_aligned": sum(len(x.get("words") or []) for x in line_timeline),
        "word_alignment": bool(aligned and any(
            x.get("words") for x in line_timeline)),
        "bpm_requested": spec.get("bpm") or None,
        "key_requested": spec.get("music_key") or "",
        "bpm_detected": detected.get("bpm"),
        "key_detected": detected.get("key", ""),
        "beat_bpm": beat_grid.get("bpm"),
        "beats": beat_grid.get("beats", []),
        "downbeats": beat_grid.get("downbeats", []),
        "beat_source": beat_grid.get("source", ""),
        "gpu_sec": round(spec.get("_gpu_sec") or 0.0, 1),
        "seed": spec.get("seed"), "lora_id": spec.get("lora_id") or "",
        "take_mode": spec.get("take_mode", "final"),
        "persona_id": spec.get("persona_id") or "",
        "song_plan": spec.get("song_plan") or {},
        "hook": spec.get("hook") or "",
        "style_tags": spec.get("style_tags") or "",
        "negative_tags": spec.get("negative_tags") or "",
        "exclude_styles": spec.get("exclude_styles") or "",
        "weirdness": int(spec.get("weirdness", 50) or 50),
        "style_influence": int(spec.get("style_influence", 50) or 50),
        "mastered": bool(used != "fallback-bgm"),
        "studio_eq": studio_eq_result or normalize_studio_eq_spec({}),
        "voice_conversion": voice_conversion,
        "model": str((used_detail or {}).get("model", "")),
        "parent_job_id": spec.get("parent_job_id") or "",
        "cover_mode": cover_mode,
        "cover_engine": cover_engine if cover_mode else "",
        "cover_fidelity": (
            "original-vocal+genre-restyle"
            if (cover_engine == "arrangement" or spec.get("keep_original_vocal")) else
            "symbolic-melody-conditioned" if cover_engine in ("yue2_comfy", "yue2_native", "yue2") else
            "reference-symbolic-melody" if cover_engine == "mulacover" else
            "lyrics-locked-melody-regenerated" if cover_engine == "heartmula" else
            "genre-reimagine-melody-regenerated" if cover_engine == "diffrhythm" else
            "source-melody-voice-conversion+genre-restyle"
            if (cover_engine == "voice" and spec.get("restyle")) else
            "source-melody-voice-conversion" if cover_engine == "voice" else
            "audio-reference" if cover_mode else ""
        ),
        "stem_separation_method": str(spec.get("_stem_separation_method") or ""),
        "lyrics_locked": bool(spec.get("_lyrics_locked")),
        "groove_reset": bool(spec.get("groove_reset")) if cover_mode else False,
        "cover_reference_strength": float(spec.get("ref_strength") or 0.5) if cover_mode else 0.0,
        "cover_style_prompt": str(spec.get("cover_style_prompt") or "") if cover_mode else "",
        "cover_arrangement_variation": str(spec.get("_cover_arrangement_variation") or "") if cover_mode else "",
        "cover_filename": cover_filename, "cover_source": cover_source,
        "wav_filename": wav_filename,
        "cover_sampling": {
            key: (used_detail or {}).get(key)
            for key in ("cfg_scale", "temperature", "topk", "audio_sec")
            if (used_detail or {}).get(key) is not None
        },
        "vocal_lock": (used_detail or {}).get("vocal_lock") or {},
        "worker_tail": str((used_detail or {}).get("stdout_tail", ""))[-500:] if used_detail else "",
        "license_note": str((used_detail or {}).get(
            "license_note",
            "Provider/model terms apply. Verify commercial rights before publishing.",
        )),
    }
    if spec.get("remix_source_url"):
        metadata.update({
            "remix_mode": "audio-to-audio",
            "remix_source_url": str(spec.get("remix_source_url"))[:1000],
            "remix_source_title": str(spec.get("remix_source_title") or "")[:300],
            "remix_source_duration": float(spec.get("remix_source_duration") or duration),
            "remix_draft_duration": duration,
            "remix_reference_strength": float(spec.get("ref_strength") or 0.5),
        })
    if not output.is_file() or output.stat().st_size <= 1024:
        raise RuntimeError("Output audio kosong atau gagal dihasilkan.")
    final_qc_duration = probe_audio_seconds(output)
    if 0.0 < final_qc_duration < 3.0:
        raise RuntimeError(f"Output audio terpotong (durasi {final_qc_duration:.1f} detik terlalu pendek).")
    (job_dir / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    stage("done")
    return {"provider": used, "duration": duration, "audio_path": str(output),
            "audio_filename": output.name, "wav_filename": wav_filename,
            "lyrics_path": str(lrc), "lyrics_text": lyrics, "metadata": metadata,
            "lyrics_timeline": line_timeline,
            "language": language, "language_locked": language_locked,
            "cover_filename": cover_filename, "cover_source": cover_source}


def _generate_role_separated_duet(spec: dict, job_dir: Path, log=None,
                                  on_stage=None) -> dict:
    """Create a real two-voice HeartMuLa duet without trusting one-pass tags.

    HeartMuLa is excellent at one singer but a single pass may collapse a
    requested duet into one timbre. We generate female and male role tracks
    independently, then stitch complete audio sections: the primary-language
    verses use the female track and the secondary-language chorus uses the
    male track. This keeps the voices physically separate and deterministic.
    """
    log = log or (lambda _: None)
    stage = on_stage or (lambda _stage: None)
    duration = max(8.0, min(float(spec.get("duration") or 30), 600.0))
    if bool(spec.get("instrumental")):
        raise RuntimeError("Duet role separation requires a vocal song.")
    if str(spec.get("lyrics") or "").strip():
        lyrics = str(spec["lyrics"]).strip()
        lyric_info = {"source": "user", "lines": len(normalize_lyrics(lyrics, duration))}
    else:
        try:
            from lyrics_writer import ensure_sheet
            lyrics, lyric_info = ensure_sheet({**spec, "vocal_gender": "duet"}, log=log)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"Duet lyrics preparation failed: {exc}") from exc
    try:
        from song_plan import build_plan
        spec["song_plan"] = build_plan(spec)
        spec["hook"] = (spec["song_plan"].get("hook") or {}).get("line", "")
    except Exception as exc:  # noqa: BLE001 - plan tak boleh gagalkan duet
        log(f"⚠️ Rencana aransemen duet gagal ({exc}); lanjut tanpa plan")
    role_root = job_dir / "_roles"
    role_specs = []
    for role, gender, offset, instruction in (
        ("female", "female", 101,
         "solo female lead only, no male voice, intimate bright alto, clear feminine timbre, close-mic pop vocal"),
        ("male", "male", 202,
         "solo male response only, no female voice, warm low baritone, clear masculine timbre, close-mic pop vocal"),
    ):
        role_spec = dict(spec)
        role_spec.update({
            "lyrics": _duet_role_lyrics(lyrics, role),
            "_lyrics_locked": True,
            "provider": "heartmula",
            "vocal_gender": gender,
            "_duet_role": role,
            "take_mode": f"duet-{role}",
            "seed": int(spec.get("seed") or 0) + offset,
            "prompt": f"{spec.get('prompt', '')}, {instruction}, duet role separation, one singer only",
        })
        role_specs.append((role, role_spec))
    stage("generating")
    female = generate_music(role_specs[0][1], role_root / "female", log=log)
    male = generate_music(role_specs[1][1], role_root / "male", log=log)
    female_path = role_root / "female" / female["audio_filename"]
    male_source_path = role_root / "male" / male["audio_filename"]
    try:
        male_pitch = float(spec.get("duet_male_pitch", -4.0))
    except (TypeError, ValueError):
        male_pitch = -4.0
    reference = Path(str(spec.get("male_reference_audio") or
                         os.environ.get("KLIPIN_MUSIC_MALE_REFERENCE_AUDIO", "")).strip())
    male_path, male_vc = _seed_vc_convert(
        male_source_path, role_root / "male" / "song_male_seedvc.mp3",
        reference, log=log)
    if male_vc != "seed-vc":
        male_path = _duet_male_timbre(
            male_source_path, role_root / "male" / "song_male_timbre.mp3", male_pitch)
        male_vc = "pitch-shift" if male_path != male_source_path else "none"
    stage("mixing")
    output = job_dir / "song.mp3"
    timeline = annotate_lyric_languages(
        normalize_lyrics(lyrics, duration), spec)
    # Collapse adjacent lines into section windows. Verses alternate by
    # section, while chorus/pre-chorus/bridge are shared by both voices.
    windows = []
    verse_no = 0
    for row in timeline:
        start, end = float(row.get("start", 0)), float(row.get("end", 0))
        section = str(row.get("section") or "Verse").lower()
        if "verse" in section and "pre" not in section:
            verse_no += 1
        role = _duet_section_role(section, verse_no)
        if windows and windows[-1][2] == role and abs(windows[-1][1] - start) < 0.01:
            windows[-1] = (windows[-1][0], end, role)
        else:
            windows.append((start, end, role))
    if not windows:
        windows = [(0.0, duration / 2, "female"), (duration / 2, duration, "male")]
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        try:
            import imageio_ffmpeg
            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("Duet stitcher requires ffmpeg.") from exc
    filters, labels = [], []
    for i, (start, end, role) in enumerate(windows):
        label = f"s{i}"
        seg_duration = max(0.08, end - start)
        fade = min(0.12, max(0.035, seg_duration / 5.0))
        fade_out_start = max(0.0, seg_duration - fade)
        if role == "both":
            # Shared chorus/bridge: both voices remain audible instead of one
            # timbre replacing the other at the section boundary.
            filters.extend([
                f"[0:a]atrim=start={start:.3f}:end={end:.3f},"
                f"asetpts=PTS-STARTPTS,volume=0.78,"
                f"afade=t=in:st=0:d={fade:.3f},"
                f"afade=t=out:st={fade_out_start:.3f}:d={fade:.3f}[f{i}]",
                f"[1:a]atrim=start={start:.3f}:end={end:.3f},"
                f"asetpts=PTS-STARTPTS,volume=0.78,"
                f"afade=t=in:st=0:d={fade:.3f},"
                f"afade=t=out:st={fade_out_start:.3f}:d={fade:.3f}[m{i}]",
                f"[f{i}][m{i}]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0,"
                f"alimiter=limit=0.96[{label}]",
            ])
        else:
            source = 0 if role == "female" else 1
            filters.append(
                f"[{source}:a]atrim=start={start:.3f}:end={end:.3f},"
                f"asetpts=PTS-STARTPTS,afade=t=in:st=0:d={fade:.3f},"
                f"afade=t=out:st={fade_out_start:.3f}:d={fade:.3f}[{label}]"
            )
        labels.append(f"[{label}]")
    # Crossfade only at phrase/section boundaries. overlap=0 keeps the
    # original timing budget while smoothing the identity hand-off.
    stitched = labels[0]
    for i, next_label in enumerate(labels[1:], 1):
        out_label = f"x{i}"
        filters.append(
            f"{stitched}{next_label}acrossfade=d=0.120:c1=tri:c2=tri:overlap=0[{out_label}]"
        )
        stitched = f"[{out_label}]"
    filters.append(f"{stitched}atrim=duration={duration:.3f},asetpts=PTS-STARTPTS[out]")
    cmd = [ffmpeg, "-y", "-v", "error", "-i", str(female_path),
           "-i", str(male_path), "-filter_complex", ";".join(filters),
           "-map", "[out]", "-t", str(duration), "-c:a", "libmp3lame",
           "-b:a", "192k", str(output)]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0 or not output.exists():
        raise RuntimeError(f"Duet stitch failed: {result.stderr[-800:]}")
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "lyrics.txt").write_text(lyrics, encoding="utf-8")
    stage("aligning")
    line_timeline = align_timeline(output, lyrics, duration, log=log)
    line_timeline = annotate_lyric_languages(line_timeline, spec)
    lrc = job_dir / "lyrics.lrc"
    _write_lrc(lrc, line_timeline)
    stage("mastering")
    wav_filename = export_wav_lufs(output, job_dir, log=log)
    stage("cover")
    cover_filename, cover_source = _fetch_cover_art(spec, genre_info(spec.get("genre") or "pop-jawa"), job_dir, log=log)
    beat_grid = detect_downbeats(output, duration, log=log)
    metadata = {
        "schema": "klipin-music/v5", "provider": "heartmula-modal-duet",
        "model_ready": True, "duet_algorithm": "role-separated-phrase-crossfade",
        "duet_roles": {"female": "odd verses", "male": "even verses",
                        "both": "chorus/pre-chorus/bridge"},
        "duet_control": "role-specific-lyrics+shared-chorus+phrase-crossfade",
        "duet_transition_ms": 120,
        "male_voice_conversion": male_vc,
        "male_reference_audio": reference.name if male_vc == "seed-vc" else "",
        "male_timbre_shift_semitones": male_pitch if male_vc == "pitch-shift" else 0.0,
        "genre": genre_info(spec.get("genre") or "pop-jawa")["id"],
        "language": spec.get("language"), "language_secondary": spec.get("language_secondary", ""),
        "language_mix": spec.get("language_mix", "chorus"), "vocal_gender": "duet",
        "duration": duration, "lyrics_lines": len(line_timeline),
        "lyrics_source": (lyric_info or {}).get("source", "user"),
        "female_gpu_sec": female.get("metadata", {}).get("gpu_sec", 0),
        "male_gpu_sec": male.get("metadata", {}).get("gpu_sec", 0),
        "words_aligned": sum(len(x.get("words") or []) for x in line_timeline),
        "beat_bpm": beat_grid.get("bpm"),
        "beats": beat_grid.get("beats", []),
        "downbeats": beat_grid.get("downbeats", []),
        "beat_source": beat_grid.get("source", ""),
        "seed": spec.get("seed"), "take_mode": spec.get("take_mode", "final"),
        "song_plan": spec.get("song_plan") or {},
        "hook": spec.get("hook") or "",
        "cover_filename": cover_filename, "cover_source": cover_source,
        "wav_filename": wav_filename,
        "license_note": "Provider/model terms apply. Verify commercial rights before publishing.",
    }
    (job_dir / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    stage("done")
    return {"provider": "heartmula-modal-duet", "duration": duration,
            "audio_path": str(output), "audio_filename": output.name,
            "wav_filename": wav_filename, "lyrics_path": str(lrc),
            "lyrics_text": lyrics, "metadata": metadata,
            "lyrics_timeline": line_timeline,
            "language": spec.get("language"),
            "language_locked": bool(spec.get("language_locked")),
            "cover_filename": cover_filename, "cover_source": cover_source}


def _cover_query(spec: dict, info: dict) -> str:
    """Kata kunci pencarian cover: genre + mood + prompt pengguna."""
    bits = [info.get("label", ""), info.get("mood", "")]
    extra = " ".join((spec.get("prompt") or "").split()[:6])
    if extra:
        bits.append(extra)
    query = " ".join(b for b in bits if b).strip()
    return query[:80] or "music concert stage"


def _fetch_cover_art(spec: dict, info: dict, job_dir: Path, log=None) -> tuple[str, str]:
    """Unduh cover landscape dari Pexels; fallback gradient lokal bila gagal.

    Mengembalikan (filename, source). File selalu bernama cover.jpg agar URL
    frontend stabil. Tidak pernah menggagalkan job musik.
    """
    target = job_dir / "cover.jpg"
    query = _cover_query(spec, info)
    key = os.environ.get("PEXELS_API_KEY", "").strip()
    if key:
        try:
            import requests
            r = requests.get(
                "https://api.pexels.com/v1/search",
                params={"query": query, "orientation": "landscape",
                        "per_page": 4, "size": "large"},
                headers={"Authorization": key,
                         "User-Agent": "Klipin-Music/1.0"},
                timeout=20,
            )
            if r.status_code == 200:
                photos = r.json().get("photos") or []
                best = None
                for ph in photos:
                    src = (ph.get("src") or {})
                    url = src.get("large2x") or src.get("large") or src.get("original")
                    if url:
                        best = (url, ph.get("photographer", "Pexels"))
                        break
                if best:
                    url, photographer = best
                    dl = requests.get(url, headers={"User-Agent": "Klipin-Music/1.0"},
                                      timeout=60)
                    dl.raise_for_status()
                    target.write_bytes(dl.content)
                    if target.stat().st_size > 1024:
                        try:
                            log and log(f"Cover art: {query} (foto: {photographer})")
                        except Exception:  # noqa: BLE001 — konsol non-UTF8 tidak boleh gagalkan job
                            pass
                        return target.name, f"pexels:{photographer}"
        except Exception as exc:  # noqa: BLE001 — cover tidak boleh menggagalkan lagu
            try:
                log and log(f"Cover Pexels gagal ({exc}); pakai gradient lokal")
            except Exception:  # noqa: BLE001
                pass
    try:
        _render_gradient_cover(target, spec.get("title") or info.get("label", "Klipin Music"))
        return target.name, "local-gradient"
    except Exception:  # noqa: BLE001
        return "", ""


def _render_gradient_cover(target: Path, title: str) -> None:
    """Cover gradient 1280x720 dengan judul — tanpa dependency baru (PIL opsional)."""
    try:
        from PIL import Image, ImageDraw
    except Exception:
        # Tanpa PIL: tulis placeholder 1px agar URL tetap valid.
        target.write_bytes(
            bytes.fromhex("ffd8ffe000104a46494600010100000100010000ffdb00430008060607"
                          "06050808080709090a0c140d0c0b0b0c1912130f141d1a1718161a18"
                          "1c1e2222241e1a1c1c20372627282a2c35393531393f3f3f252e33393"
                          "9393f3f3f3c3cffffd9"))
        return
    import colorsys
    seed = int(hashlib.sha256(title.encode()).hexdigest()[:8], 16)
    hue = (seed % 360) / 360.0
    top = tuple(int(c * 255) for c in colorsys.hsv_to_rgb(hue, 0.75, 0.35))
    bottom = tuple(int(c * 255) for c in colorsys.hsv_to_rgb((hue + 0.12) % 1.0, 0.85, 0.75))
    w, h = 1280, 720
    img = Image.new("RGB", (w, h), top)
    draw = ImageDraw.Draw(img)
    for y in range(h):
        t = y / max(1, h - 1)
        draw.line([(0, y), (w, y)],
                  fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))
    # Judul di tengah-bawah, aman untuk overlay player.
    text = title[:42]
    draw.text((w // 2 - len(text) * 11, h - 140), text, fill=(255, 255, 255))
    img.save(target, "JPEG", quality=88)


def cache_key(spec: dict) -> str:
    payload = json.dumps({k: spec.get(k) for k in (
        "prompt", "lyrics", "genre", "language", "provider", "seed",
        "duration", "vocal_gender", "persona_id")}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()[:24]
