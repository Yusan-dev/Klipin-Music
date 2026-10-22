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
    {"id": "jazz-pop", "label": "Jazz Pop Jawa", "mood": "chill", "tags": "Indonesian jazz pop, Javanese influence, male and female duet, piano, upright bass, brush drums"},
    {"id": "pop-jawa", "label": "Pop Jawa", "mood": "upbeat", "tags": "Javanese pop, warm gamelan textures, modern pop"},
    {"id": "campursari", "label": "Campursari", "mood": "chill", "tags": "campursari, kendang, siter, Javanese vocal"},
    {"id": "dangdut-koplo", "label": "Dangdut Koplo", "mood": "upbeat", "tags": "dangdut koplo, kendang, festive, danceable"},
    {"id": "keroncong", "label": "Keroncong", "mood": "chill", "tags": "keroncong, ukulele, cak cuk, nostalgic"},
    {"id": "sunda", "label": "Pop Sunda", "mood": "chill", "tags": "Sundanese pop, kacapi, suling, intimate"},
    {"id": "bali", "label": "Pop Bali", "mood": "upbeat", "tags": "Balinese pop, gamelan-inspired, bright"},
    {"id": "pop", "label": "Pop", "mood": "upbeat", "tags": "radio pop, polished, catchy chorus"},
    {"id": "kpop", "label": "K-Pop", "mood": "upbeat", "tags": "k-pop, energetic Korean pop, bright vocals, polished production, driving beat, catchy hook"},
    {"id": "jpop", "label": "J-Pop", "mood": "upbeat", "tags": "j-pop, upbeat Japanese pop, youthful vocal, bright synths, driving guitars"},
    {"id": "anime", "label": "Anime / J-Pop", "mood": "epic", "tags": "anime opening, j-pop, energetic female vocal, bright female singer, driving drums, sparkling synths, catchy anthemic chorus"},
    {"id": "arab", "label": "Arab / Middle East", "mood": "epic", "tags": "Arabic pop, middle eastern scales, oud, qanun, darbuka percussion, passionate vocal"},
    {"id": "nasyid", "label": "Nasyid / Islami", "mood": "chill", "tags": "nasyid, Islamic devotional, peaceful male vocal, frame drum, soft strings, uplifting chorus"},
    {"id": "latin", "label": "Latin / Reggaeton", "mood": "upbeat", "tags": "Latin pop, reggaeton beat, Spanish guitar, danceable groove, passionate vocal"},
    {"id": "edm", "label": "EDM / Dance", "mood": "epic", "tags": "EDM, festival dance, powerful drop, sidechain synths, euphoric build-up"},
    {"id": "dj-remix", "label": "DJ Remix", "mood": "epic", "tags": "DJ remix, club mix, punchy kick, bass drop, sidechain, dancefloor energy"},
    {"id": "pop-remix", "label": "Pop Remix", "mood": "upbeat", "tags": "pop remix, polished vocal, four-on-the-floor drums, synth bass, radio hook"},
    {"id": "reggae", "label": "Reggae", "mood": "chill", "tags": "roots reggae, one-drop drum groove (kick and snare together on beat 3), offbeat skank guitar on the upbeats, deep dub bass line, relaxed swung feel, no four-on-the-floor kick"},
    {"id": "dangdut-remix", "label": "Dangdut Remix", "mood": "upbeat", "tags": "dangdut remix, koplo groove, kendang, electronic bass, danceable drop"},
    {"id": "rock", "label": "Rock", "mood": "epic", "tags": "rock, electric guitars, driving drums, powerful chorus, energetic vocal"},
    {"id": "rnb", "label": "R&B / Soul", "mood": "chill", "tags": "R&B, soulful vocal, smooth groove, warm keys, late night vibe"},
    {"id": "jazz", "label": "Jazz", "mood": "chill", "tags": "jazz, swing rhythm, brushed drums, upright bass, smooth vocal"},
    {"id": "mandarin", "label": "Mandarin Pop", "mood": "upbeat", "tags": "mandopop, emotional ballad vocal, piano, strings, polished production"},
    {"id": "india", "label": "India / Bollywood", "mood": "upbeat", "tags": "Bollywood, Hindi pop, tabla, sitar textures, festive chorus"},
    {"id": "afrobeats", "label": "Afrobeats", "mood": "upbeat", "tags": "afrobeats, log drums, groovy percussion, warm vocal, danceable"},
    {"id": "folk", "label": "Folk / Akustik", "mood": "chill", "tags": "folk, acoustic guitar, intimate vocal, warm harmonies"},
    {"id": "akustik", "label": "Pop Akustik / Acoustic", "mood": "chill", "tags": "acoustic pop, unplugged, fingerpicked acoustic guitar, intimate vocal, gentle piano, warm harmonies, live session feel"},
    {"id": "metal", "label": "Metal", "mood": "epic", "tags": "metal, heavy guitars, double kick drums, intense vocal, epic chorus"},
    {"id": "ambient", "label": "Ambient / Sinematik", "mood": "chill", "tags": "ambient, cinematic pads, ethereal vocal textures, slow build"},
    {"id": "lofi", "label": "Lo-fi", "mood": "chill", "tags": "lo-fi, mellow, dusty drums, late night"},
    {"id": "hiphop", "label": "Hip-hop", "mood": "epic", "tags": "hip-hop, punchy drums, bass, confident"},
]


def genre_info(genre: str) -> dict:
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


def _normalize_provider(provider: str | None) -> str:
    """Map persisted runtime provider labels back to request provider IDs.

    Completed jobs store the concrete engine label (for example
    ``heartmula-modal``).  Follow-up operations such as Extend reuse that
    value, while the generation dispatcher accepts only the logical IDs.
    Legacy ``yue2`` labels are transparently migrated to ``heartmula`` so
    existing database rows never break after removing non-commercial YuE2.
    """
    value = str(provider or "auto").strip().lower()
    if value.startswith("heartmula") or value.startswith("yue2"):
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
    "duet": "real male and female duet, female lead vocal, male response vocal, alternating singers, two distinct voices, duet harmony",
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
             "[0:a]volume=1.0[bg];[1:a]volume=1.0[v];"
             "[bg][v]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0,"
             "alimiter=limit=0.96[out]",
             "-map", "[out]", "-t", f"{float(duration):.3f}",
             "-c:a", "libmp3lame", "-b:a", "192k", str(target)],
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
    if gender not in ("male", "female"):
        gender = "female"
    timbre = ("female singer,feminine voice" if gender == "female"
              else "male singer,masculine voice")
    tags = (f"{timbre},clear smooth voice,expressive singing,balanced vocal,"
            "solo singer,a cappella,dry studio vocal")
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
        "roots Indonesian pop-reggae cover instrumental arrangement (#reggaeindonesia style), "
        "crisp offbeat guitar and keyboard skank on the '&' upbeats, warm round dub bassline "
        "following the chord roots, classic one-drop reggae drum groove (rimshot + kick on beat 3, "
        "crisp hi-hat shuffle), warm brass horn stabs and organ bubble, relaxed santai feel, "
        "NO four-on-the-floor kick and NO original pop ballad drums"
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
        "reggae", "dangdut-koplo", "dangdut-remix", "dj-remix", "edm",
        "pop-remix", "keroncong", "campursari", "jazz-pop", "jazz", "latin", "afrobeats"
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


def _mix_vocal_and_arrangement(vocals_path: Path, restyled_path: Path,
                               output: Path, duration: float,
                               genre: str = "", log=None) -> Path:
    """Studio vocal-over-arrangement mixer & master:
    - Cleans residual bleed from isolated vocal stem (highpass 95Hz, de-mud 260Hz,
      presence lift 3.2kHz, optical-style leveling compressor, subtle room glue).
    - Carves a -1.8dB mid-range vocal pocket at 2.6kHz in the new genre arrangement
      so the vocal sits naturally inside the band (like YouTube Reggae/Koplo covers).
    - Masters the combined stereo bus to -14 LUFS with true-peak limiting.
    """
    ffmpeg = _ffmpeg_exe()
    vocal_chain = (
        "[1:a]highpass=f=95,lowpass=f=13800,"
        "equalizer=f=260:width_type=q:width=1.4:g=-2.2,"
        "equalizer=f=3200:width_type=q:width=1.2:g=2.2,"
        "acompressor=threshold=-18dB:ratio=3.2:attack=8:release=130:makeup=2.0,"
        "aecho=0.8:0.86:36:0.11,volume=1.18[v]"
    )
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


def _render_beat_locked_genre_backing(
    job_dir: Path,
    backing_path: Path,
    out_path: Path,
    genre_id: str,
    duration: float,
    bpm_hint: float = 0.0,
    log=None,
) -> bool:
    """Build a 100% beat-locked genre arrangement from 4-stem Demucs tracks.

    Unlike generative text-to-music models (which drift in tempo or play wrong
    chords), this stem-rearrangement engine:
      1. Discards 100% of the original pop/rock ``drums.wav`` stem.
      2. Uses ``Beat This!`` ONNX on ``drums.wav`` to lock onto every exact
         millisecond beat and downbeat of the original performance (0.0ms drift).
      3. Reshapes ``other.wav`` (the original studio guitars/keys/chords) into
         an offbeat Reggae Skank ("&" chop + dub echo) + warm harmonic organ pad.
      4. Reshapes ``bass.wav`` (the original studio bassline) into a deep
         Reggae dub sub-bass locked to the original root notes.
      5. Synthesizes a studio Reggae One-Drop drum & percussion kit (Kick,
         Wooden Cross-Stick Rimshot, Offbeat Skank Hi-Hat, Cabasa Shaker, and
         4-bar Dub Fills) directly on the exact millisecond beat timestamps,
         following the original song's dynamic contour (gentle in soft intros,
         full groove when the band enters).
    """
    try:
        import numpy as np
    except Exception:
        return False

    drums_path = job_dir / "cover_drums_source.mp3"
    bass_path = job_dir / "cover_bass_source.mp3"
    other_path = job_dir / "cover_other_source.mp3"
    if not (other_path.is_file() and bass_path.is_file()):
        return False

    sr = 44100
    ffmpeg = _ffmpeg_exe()
    log and log("🥁 Menyusun aransemen Reggae One-Drop & Offbeat Skank terkunci-ketukan (4-Stem Studio Rearranger)…")

    # 1. Pre-shape harmonic layers with studio EQ & dub delay via FFmpeg
    skank_src = job_dir / "_other_skank_eq.wav"
    pad_src = job_dir / "_other_pad_eq.wav"
    bass_src = job_dir / "_bass_dub_eq.wav"
    subprocess.run(
        [ffmpeg, "-y", "-v", "error", "-i", str(other_path),
         "-af", "highpass=f=280,lowpass=f=6200,equalizer=f=1850:width_type=q:width=1.3:g=5.0,aecho=0.8:0.78:112:0.24",
         "-ar", str(sr), "-ac", "2", str(skank_src)],
        capture_output=True, timeout=180, check=False)
    subprocess.run(
        [ffmpeg, "-y", "-v", "error", "-i", str(other_path),
         "-af", "highpass=f=130,lowpass=f=1150,equalizer=f=420:width_type=q:width=1.1:g=2.5",
         "-ar", str(sr), "-ac", "2", str(pad_src)],
        capture_output=True, timeout=180, check=False)
    subprocess.run(
        [ffmpeg, "-y", "-v", "error", "-i", str(bass_path),
         "-af", "highpass=f=32,lowpass=f=240,equalizer=f=68:width_type=q:width=1.1:g=5.5,acompressor=threshold=-18dB:ratio=4:attack=5:release=90:makeup=2.5",
         "-ar", str(sr), "-ac", "2", str(bass_src)],
        capture_output=True, timeout=180, check=False)

    other_skank = _decode_stereo_f32(skank_src if skank_src.is_file() else other_path, sr, duration)
    other_pad = _decode_stereo_f32(pad_src if pad_src.is_file() else other_path, sr, duration)
    bass_arr = _decode_stereo_f32(bass_src if bass_src.is_file() else bass_path, sr, duration)
    drums_orig = _decode_stereo_f32(drums_path if drums_path.is_file() else backing_path, sr, duration)

    n_samples = min(len(other_skank), len(other_pad), len(bass_arr))
    if n_samples < sr * 3:
        return False
    other_skank = other_skank[:n_samples]
    other_pad = other_pad[:n_samples]
    bass_arr = bass_arr[:n_samples]

    # 2. Detect exact beat & downbeat timestamps from the isolated drum stem
    grid = _beat_this_grid(drums_path if drums_path.is_file() else backing_path, duration=duration)
    beats = [float(b) for b in (grid.get("beats") or []) if 0.0 <= float(b) < (n_samples / sr)]
    downbeats = set(round(float(d), 2) for d in (grid.get("downbeats") or []))
    if len(beats) < 8:
        eff_bpm = float(bpm_hint or grid.get("bpm") or 120.0)
        if eff_bpm < 55 or eff_bpm > 190:
            eff_bpm = 120.0
        step_sec = 60.0 / eff_bpm
        beats = list(np.arange(0.0, n_samples / sr, step_sec, dtype=np.float64))

    diffs = np.diff(beats)
    median_dt = float(np.median(diffs)) if len(diffs) else 0.5
    median_dt = max(0.28, min(1.0, median_dt))

    # 3. Compute local drum energy of the original song so soft intros stay gentle
    drum_mono = drums_orig[:n_samples].mean(axis=1) if len(drums_orig) >= n_samples else np.ones(n_samples, dtype=np.float32) * 0.1
    win_rms = int(sr * 1.2)
    beat_energies = []
    for b in beats:
        idx = int(b * sr)
        lo = max(0, idx - win_rms // 2)
        hi = min(len(drum_mono), idx + win_rms // 2)
        rms = float(np.sqrt(np.mean(drum_mono[lo:hi] ** 2) + 1e-12)) if hi > lo else 0.02
        beat_energies.append(rms)
    ref_rms = float(np.percentile(beat_energies, 75)) if beat_energies else 0.05
    ref_rms = max(ref_rms, 0.008)

    # 4. Pre-synthesize studio Reggae drum & percussion one-shot samples (44.1kHz stereo)
    rng = np.random.default_rng(1973)

    def _make_kick(length_s: float = 0.24) -> np.ndarray:
        t = np.linspace(0.0, length_s, int(sr * length_s), endpoint=False, dtype=np.float32)
        # Pitch drops from 118Hz -> 46Hz rapidly for warm Reggae sub-kick thump
        freq = 44.0 + 76.0 * np.exp(-t * 34.0)
        phase = 2.0 * np.pi * np.cumsum(freq) / sr
        body = np.sin(phase) * np.exp(-t * 11.5)
        click = np.sin(2.0 * np.pi * 1200.0 * t) * np.exp(-t * 95.0) * 0.22
        mono = np.tanh((body + click) * 1.45) * 0.88
        return np.column_stack([mono, mono]).astype(np.float32)

    def _make_rimshot(length_s: float = 0.13) -> np.ndarray:
        t = np.linspace(0.0, length_s, int(sr * length_s), endpoint=False, dtype=np.float32)
        # Authentic wooden cross-stick rimshot (430Hz, 1680Hz, 2920Hz resonant wood + crisp snap)
        wood = (
            0.55 * np.sin(2.0 * np.pi * 435.0 * t) * np.exp(-t * 38.0)
            + 0.70 * np.sin(2.0 * np.pi * 1690.0 * t) * np.exp(-t * 52.0)
            + 0.40 * np.sin(2.0 * np.pi * 2940.0 * t) * np.exp(-t * 70.0)
        )
        snap = rng.standard_normal(len(t)).astype(np.float32) * np.exp(-t * 68.0) * 0.35
        mono = np.tanh((wood + snap) * 1.6) * 0.82
        return np.column_stack([mono * 0.96, mono * 1.04]).astype(np.float32)

    def _make_hihat(open_hat: bool = False) -> np.ndarray:
        length_s = 0.16 if open_hat else 0.055
        t = np.linspace(0.0, length_s, int(sr * length_s), endpoint=False, dtype=np.float32)
        # Metallic high-frequencyinharmonic partials + differentiated white noise
        n = rng.standard_normal(len(t)).astype(np.float32)
        hp = np.diff(n, prepend=0.0)
        hp2 = np.diff(hp, prepend=0.0)
        metal = (
            np.sin(2.0 * np.pi * 6350.0 * t)
            + np.sin(2.0 * np.pi * 8920.0 * t)
            + np.sin(2.0 * np.pi * 11400.0 * t)
        ) * 0.12
        decay = np.exp(-t * (19.0 if open_hat else 58.0))
        mono = (hp2 * 0.45 + metal) * decay * (0.48 if open_hat else 0.30)
        return np.column_stack([mono * 1.06, mono * 0.94]).astype(np.float32)

    def _make_shaker(length_s: float = 0.07) -> np.ndarray:
        t = np.linspace(0.0, length_s, int(sr * length_s), endpoint=False, dtype=np.float32)
        n = rng.standard_normal(len(t)).astype(np.float32)
        hp = np.diff(n, prepend=0.0)
        env = np.sin(np.pi * (t / length_s)) ** 1.6
        mono = hp * env * 0.16
        return np.column_stack([mono * 0.92, mono * 1.08]).astype(np.float32)

    kick_sample = _make_kick()
    rim_sample = _make_rimshot()
    hat_closed = _make_hihat(open_hat=False)
    hat_open = _make_hihat(open_hat=True)
    shaker_sample = _make_shaker()

    def _place(buf: np.ndarray, sample: np.ndarray, pos: int, gain: float) -> None:
        if pos < 0 or pos >= len(buf) or gain <= 0.001:
            return
        end = min(len(buf), pos + len(sample))
        buf[pos:end] += sample[: end - pos] * float(gain)

    # 5. Build sample-accurate Skank Envelope, Bass Pocket Envelope & Reggae One-Drop Drum Bus
    skank_env = np.full(n_samples, 0.06, dtype=np.float32)
    bass_env = np.ones(n_samples, dtype=np.float32)
    drums_bus = np.zeros((n_samples, 2), dtype=np.float32)

    bar_beat = 0
    bar_count = 0
    is_koplo = "koplo" in genre_id or "dangdut" in genre_id
    for i, b in enumerate(beats):
        t0 = b
        t1 = beats[i + 1] if (i + 1) < len(beats) else min(n_samples / sr, t0 + median_dt)
        dt = max(0.24, min(1.1, t1 - t0))
        if round(t0, 2) in downbeats and i > 0:
            bar_beat = 0
            bar_count += 1
        s0 = int(t0 * sr)
        s_off = int((t0 + 0.48 * dt) * sr)
        s_off_end = int((t0 + 0.82 * dt) * sr)
        s_chuck = int((t0 + 0.84 * dt) * sr)
        s_chuck_end = int((t0 + 0.95 * dt) * sr)

        # Offbeat skank chop ("&" of every beat) on the real guitar/keyboard chords
        if 0 <= s_off < s_off_end <= n_samples:
            seg_len = s_off_end - s_off
            t_seg = np.linspace(0.0, 1.0, seg_len, endpoint=False, dtype=np.float32)
            # Fast 8% attack, crisp percussive skank decay
            chop = np.where(t_seg < 0.08, t_seg / 0.08, np.exp(-(t_seg - 0.08) * 3.2))
            skank_env[s_off:s_off_end] = 0.12 + 1.75 * chop
            bass_env[s_off:s_off_end] = 0.82
        if bar_beat in (1, 3) and 0 <= s_chuck < s_chuck_end <= n_samples:
            seg_len = s_chuck_end - s_chuck
            t_seg = np.linspace(0.0, 1.0, seg_len, endpoint=False, dtype=np.float32)
            skank_env[s_chuck:s_chuck_end] = 0.10 + 0.85 * np.exp(-t_seg * 4.0)

        # Dynamic scaling from original drum stem so intros/breakdowns breathe naturally
        raw_ratio = beat_energies[i] / ref_rms
        full_band = raw_ratio >= 0.22
        dyn = float(np.clip(raw_ratio, 0.28, 1.15)) if full_band else 0.28

        # Place Hi-Hat & Shaker on beat and accented offbeat
        _place(drums_bus, hat_closed, s0, 0.55 * dyn)
        _place(drums_bus, shaker_sample, int((t0 + 0.25 * dt) * sr), 0.45 * dyn)
        _place(drums_bus, hat_open, s_off, 0.92 * dyn)
        _place(drums_bus, shaker_sample, int((t0 + 0.75 * dt) * sr), 0.50 * dyn)

        if is_koplo:
            # Dangdut Koplo syncopated kick + tak/dut pattern
            if full_band:
                if bar_beat in (0, 2):
                    _place(drums_bus, kick_sample, s0, 1.05 * dyn)
                    _place(drums_bus, kick_sample, int((t0 + 0.75 * dt) * sr), 0.85 * dyn)
                else:
                    _place(drums_bus, rim_sample, s0, 1.0 * dyn)
                    _place(drums_bus, kick_sample, s_off, 0.90 * dyn)
                    _place(drums_bus, rim_sample, int((t0 + 0.75 * dt) * sr), 0.85 * dyn)
            else:
                if bar_beat == 2:
                    _place(drums_bus, rim_sample, s0, 0.50)
        else:
            # Authentic Reggae One-Drop + Rockers Chorus Groove:
            # - Soft intro: subtle cross-stick on beat 3
            # - Verse/Chorus: signature One-Drop Kick + Rimshot on beat 3 (bar_beat == 2),
            #   plus Kick on beat 1 during high-energy chorus bars, and pickup kick at bar end
            if full_band:
                if bar_beat == 0 and raw_ratio > 0.72:
                    _place(drums_bus, kick_sample, s0, 0.88 * dyn)
                if bar_beat == 2:
                    _place(drums_bus, kick_sample, s0, 1.15 * dyn)
                    _place(drums_bus, rim_sample, s0, 1.12 * dyn)
                elif bar_beat == 1 and (bar_count % 2 == 1):
                    _place(drums_bus, rim_sample, s0, 0.55 * dyn)
                if bar_beat == 3 and (bar_count % 2 == 1):
                    _place(drums_bus, kick_sample, s_off, 0.78 * dyn)
                    if bar_count % 4 == 3:
                        _place(drums_bus, rim_sample, int((t0 + 0.75 * dt) * sr), 0.85 * dyn)
            else:
                if bar_beat == 2:
                    _place(drums_bus, rim_sample, s0, 0.48)

        bar_beat = (bar_beat + 1) % 4

    # 6. Combine Beat-Locked Skank Guitars/Keys + Warm Harmonic Pad + Dub Bass + Reggae Drums
    skank_stereo = other_skank * skank_env[:, None]
    pad_stereo = other_pad * 0.34
    bass_stereo = bass_arr * (bass_env[:, None] * 1.15)
    mix = skank_stereo + pad_stereo + bass_stereo + drums_bus * 0.95

    peak = float(np.max(np.abs(mix))) if len(mix) else 0.0
    if peak > 0.95:
        mix = mix * (0.92 / peak)

    import soundfile as sf

    raw_wav = job_dir / "_beat_locked_backing.wav"
    sf.write(str(raw_wav), mix, sr)
    # Add subtle studio room glue and encode to 192k MP3
    res = subprocess.run(
        [ffmpeg, "-y", "-v", "error", "-i", str(raw_wav),
         "-af", "aecho=0.8:0.84:28:0.10,alimiter=limit=0.95",
         "-c:a", "libmp3lame", "-b:a", "192k", str(out_path)],
        capture_output=True, text=True, timeout=180)
    raw_wav.unlink(missing_ok=True)
    return res.returncode == 0 and out_path.is_file() and out_path.stat().st_size > 1024


def _voice_restyle_cover(spec: dict, job_dir: Path, output: Path, log=None) -> Path:
    """Vocal-locked cover with a genre-restyled instrumental arrangement.

    Supports two vocal modes:
    - ``keep_original_vocal=True`` (or ``cover_engine="arrangement"``):
      Preserves the original singer's vocal from the source song (Demucs stem)
      and overlays it onto the newly arranged genre instrumental (like YouTube
      Pop-to-Reggae / Koplo covers).
    - ``keep_original_vocal=False`` (``cover_engine="voice"`` + ``restyle=True``):
      Converts the isolated vocal to the target Voice Persona / gender via
      Seed-VC while keeping 100% of the source melody and phrasing, then mixes
      it over the newly arranged genre instrumental.
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

    if keep_original_vocal:
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
        log and log("🎤 Memisah & menyelaraskan warna vokal sumber (Demucs + Seed-VC)…")
        stems = _voice_fn("convert_stems").remote(
            source_b64, ref_b64, duration,
            pitch_shift, bool(spec.get("voice_pitch_auto", True)))
        vocals_path = job_dir / "cover_vocals.mp3"
        backing_path = job_dir / "cover_backing_source.mp3"
        vocals_path.write_bytes(base64.b64decode(stems["vocals_b64"]))
        backing_path.write_bytes(base64.b64decode(stems["backing_b64"]))
        for extra_key, extra_name in (
            ("drums_b64", "cover_drums_source.mp3"),
            ("bass_b64", "cover_bass_source.mp3"),
            ("other_b64", "cover_other_source.mp3"),
        ):
            if stems.get(extra_key):
                (job_dir / extra_name).write_bytes(base64.b64decode(stems[extra_key]))
        spec["_stem_separation_method"] = str(stems.get("method") or "seed-vc-vocal-stem")

    info = genre_info(spec.get("genre") or "reggae")
    detected = analyze_bpm_key(backing_path, log=log)
    bpm = detected.get("bpm") or spec.get("bpm") or 0
    key = detected.get("key") or spec.get("music_key") or ""
    if bpm:
        log and log(f"🧭 Tempo & nada sumber terdeteksi: {bpm} BPM{f', {key}' if key else ''}")

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
    restyled_path = job_dir / "cover_backing_restyled.mp3"
    if _render_beat_locked_genre_backing(
        job_dir, backing_path, restyled_path, info["id"], duration,
        bpm_hint=float(bpm or 0.0), log=log
    ):
        spec["_arrangement_method"] = "beat-locked-4stem-rearranger"
    elif _mulacover_modal_enabled():
        log and log(f"🎛️ Menata ulang aransemen instrumen ke {info['label']} (MuLaCover)…")
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
    elif _ace_modal_enabled():
        _ace_step_modal_generate(restyle_spec, restyled_path, log=log)
    elif _ace_step_url():
        _ace_step_generate(restyle_spec, restyled_path, log=log)
    else:
        log and log("ℹ️ Worker GPU aransemen belum aktif — menyusun aransemen BGM lokal…")
        bgm_fallback = job_dir / "cover_backing_restyled.m4a"
        render_bgm(info["mood"], str(bgm_fallback), duration, log=log)
        restyled_path = bgm_fallback

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


def _heartmula_modal_generate(spec: dict, out_path: Path, log=None) -> dict | None:
    """Generate a full song on the deployed HeartMuLa Modal GPU worker.

    Every Modal/credential failure is re-raised as a ``RuntimeError`` whose text
    is safe to show to the user (see ``modal_credentials.friendly_modal_error``):
    the panel prints it verbatim instead of dying silently.
    """
    if os.environ.get("KLIPIN_HEARTMULA_MODAL_ENABLED", "0").strip() != "1":
        return None
    import base64

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
    log and log(f"🎤 HeartMuLa Modal L4 full-song task started… "
                f"({language['label']}, tags={tags[:120]}, seed={seed})")
    try:
        ensure_modal_credentials()
        import modal
        function = modal.Function.from_name(app_name, function_name)
        # Seed + LoRA diteruskan asli (deterministik); worker lama yang tidak
        # kenal kwargs baru tetap kompatibel via fallback 4-arg.
        try:
            result = function.remote(
                spec.get("lyrics", ""), tags,
                int(float(spec.get("duration") or 180)), seed,
                lora_id=lora or None,
            )
        except TypeError:
            result = function.remote(
                spec.get("lyrics", ""), tags,
                int(float(spec.get("duration") or 180)), seed
            )
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


def _lyrics_to_lrc(lyrics: str, duration: float) -> str:
    """Format plain/structured lyrics into LRC timestamps for DiffRhythm."""
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
    lrc_text = "" if instrumental else _lyrics_to_lrc(spec.get("lyrics", ""), duration)
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
        r"jangan\s+lupa\s+subscribe|subtitle\s+oleh|^intro$)",
        re.IGNORECASE,
    )
    out: list[str] = []
    for raw_line in raw.splitlines():
        s = raw_line.strip()
        if not s:
            continue
        # Keep the user's section header exactly as written (incl. pipe stacks).
        if s.startswith("[") and s.endswith("]"):
            out.append(s)
            out.append("")
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


_KEY_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
# Krumhansl major/minor profiles (12 pitch classes, C-based).
_KRUMHANSL = {
    "major": [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88],
    "minor": [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17],
}


def analyze_bpm_key(audio_path: Path, log=None) -> dict:
    """Detect BPM + musical key with librosa (90s excerpt, CPU). {} bila gagal.

    Honest by design: returns detected values or nothing — never invented.
    """
    try:
        import librosa
        import numpy as np
        y, sr = librosa.load(str(audio_path), sr=22050, mono=True, duration=90.0)
        if y.size < sr * 3:
            return {}
        tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
        bpm = round(float(tempo), 1)
        chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
        mean = chroma.mean(axis=1)
        best, best_score = "", -1.0
        for mode, profile in _KRUMHANSL.items():
            p = np.array(profile)
            for shift in range(12):
                score = float(np.dot(mean, np.roll(p, shift)))
                if score > best_score:
                    best_score, best = score, f"{_KEY_NAMES[shift]} {mode}"
        out = {"bpm": bpm if 40 <= bpm <= 220 else None, "key": best}
        log and log(f"🎼 Deteksi: {bpm} BPM, {best}")
        return out
    except Exception as exc:  # noqa: BLE001 — librosa opsional
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
    if matched / max(1, len(lyric_words)) < 0.35:
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


def detect_downbeats(audio_path: Path, duration: float = 0,
                     log=None) -> dict:
    """Beat grid + downbeat dari Beat This! ONNX, fallback onset autocorr.

    Model Beat This! (MIT) memberi downbeat nyata; bila model belum tersedia
    atau gagal, Klipin memakai autocorrelation onset yang ditandai
    ``beat_source=onset-autocorr`` supaya UI tidak mengklaim presisi studio.
    Tidak pernah gagal - mengembalikan {} bila audio tidak bisa dibaca.
    """
    try:
        grid = _beat_this_grid(audio_path, duration)
        if grid:
            return grid
        log and log("Beat This! tidak tersedia, fallback onset-autocorr")
    except Exception as exc:  # noqa: BLE001 - grid opsional
        log and log(f"Beat This! gagal ({exc}), fallback onset-autocorr")
    try:
        import numpy as np

        ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
        decoded = subprocess.run(
            [ffmpeg, "-v", "error", "-i", str(audio_path), "-ac", "1",
             "-ar", "11025", "-t", "360", "-f", "s16le", "-"],
            capture_output=True, timeout=180)
        if decoded.returncode != 0 or len(decoded.stdout) < 22050:
            return {}
        y = np.frombuffer(decoded.stdout, dtype=np.int16).astype(np.float32)
        y /= 32768.0
        sr, hop, frame = 11025, 256, 512
        if y.size < frame:
            return {}
        count = 1 + (y.size - frame) // hop
        frames = np.lib.stride_tricks.as_strided(
            y, shape=(count, frame),
            strides=(y.strides[0] * hop, y.strides[0]))
        energy = np.sqrt(np.mean(frames * frames, axis=1))
        onset = np.maximum(0.0, np.diff(energy, prepend=energy[0]))
        onset -= onset.mean()
        norm = float(np.linalg.norm(onset))
        if norm <= 0:
            return {}
        onset /= norm

        size = 2 * count
        spectrum = np.fft.rfft(onset, n=size)
        autocorr = np.fft.irfft(spectrum * np.conj(spectrum))[:count]
        if autocorr[0] <= 0:
            return {}
        autocorr = autocorr / autocorr[0]

        min_lag = max(2, int(round(60 * sr / (200 * hop))))
        max_lag = min(count - 1, int(round(60 * sr / (60 * hop))))
        if max_lag <= min_lag:
            return {}
        lags = np.arange(min_lag, max_lag + 1)
        bpms = 60.0 * sr / (lags * hop)
        prior = np.exp(-0.5 * (np.log2(bpms / 120.0) / 0.35) ** 2)
        lag = int(lags[int(np.argmax(autocorr[lags] * prior))])

        bpm = 60.0 * sr / (lag * hop)
        while bpm < 70 and lag * 2 <= max_lag:
            lag *= 2
            bpm *= 2
        while bpm > 170 and lag // 2 >= min_lag:
            lag //= 2
            bpm /= 2

        best_offset, best_score = 0, float("-inf")
        for offset in range(lag):
            score = float(onset[np.arange(offset, count, lag)].sum())
            if score > best_score:
                best_offset, best_score = offset, score
        beat_frames = list(range(best_offset, count, lag))
        beats = [round(f * hop / sr, 3) for f in beat_frames]
        if duration:
            beats = [beat for beat in beats if beat < float(duration)]
        if len(beats) < 4:
            return {}

        best_shift, best_energy = 0, float("-inf")
        for shift in range(4):
            frames_idx = [int(round(beat * sr / hop)) for beat in beats[shift::4]]
            frames_idx = [i for i in frames_idx if 0 <= i < count]
            score = float(onset[frames_idx].sum()) if frames_idx else float("-inf")
            if score > best_energy:
                best_shift, best_energy = shift, score

        return {
            "bpm": round(float(bpm), 1),
            "beats": beats,
            "downbeats": [round(b, 3) for b in beats[best_shift::4]],
            "time_signature": 4,
            "source": "onset-autocorr",
        }
    except Exception as exc:  # noqa: BLE001 - grid opsional
        log and log(f"Beat grid fallback ({exc})")
        return {}


def align_timeline(audio_path: Path, lyrics: str, duration: float,
                   log=None) -> list[dict]:
    """Word/line alignment nyata via faster-whisper bila ada; fallback merata.

    Tidak pernah gagal — selalu kembalikan timeline karaoke yang valid.
    """
    base = normalize_lyrics(lyrics, duration)
    if not base or not audio_path.exists():
        return base
    try:
        from faster_whisper import WhisperModel
        model_name = os.environ.get("KLIPIN_MUSIC_ALIGN_MODEL", "small")
        model = WhisperModel(model_name, device="cpu", compute_type="int8")
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
        log and log(f"🎯 Alignment Whisper: {len(words)} kata → {len(out)} baris")
        return out
    except Exception as exc:  # noqa: BLE001 — fallback merata tetap valid
        log and log(f"⚠️ Alignment fallback merata ({exc})")
        return base


def master_audio(src: Path, log=None) -> bool:
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
    ffprobe = shutil.which("ffprobe")
    if not ffprobe or not path.exists():
        return 0.0
    try:
        r = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=30)
        first = (r.stdout or "").strip().splitlines()
        return max(0.0, float(first[0])) if first else 0.0
    except Exception:  # noqa: BLE001 — durasi bersifat best-effort
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
    duration = max(8.0, min(float(spec.get("duration") or 30), 600.0))
    info = genre_info(spec.get("genre") or "pop-jawa")
    provider = _normalize_provider(spec.get("provider"))
    prompt = (spec.get("prompt") or "lagu lokal yang hangat dan mudah diingat").strip()
    spec = dict(spec)
    cover_mode = bool(spec.get("cover_mode"))
    cover_engine = str(spec.get("cover_engine") or "").strip().lower()
    if cover_engine == "yue2":
        cover_engine = "diffrhythm"
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
        if spec.get("_lyrics_locked") and str(spec.get("lyrics") or "").strip():
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
        target = max(30.0, min(600.0, round(sung * float(_sec_per_line) + 6.0, 1)))
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
    ) else "song.m4a")
    used = "fallback-bgm"
    used_detail: dict = {}
    _gpu_t0 = time.time()
    candidates = ([provider] if provider in ("diffrhythm", "ace-step", "heartmula", "mulacover")
                  else ["heartmula", "diffrhythm", "ace-step"])
    faithful_audio_cover = cover_mode and cover_engine in ("ace-step", "mulacover")
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
    if used == "fallback-bgm" and spec.get("full_song"):
        raise RuntimeError(
            "Full-song generation requires an active HeartMuLa, DiffRhythm, or ACE-Step "
            "provider (instrumental fallback is disabled for full_song). "
            + provider_hint()
        )
    if used == "fallback-bgm":
        log("ℹ️ Model lokal belum dikonfigurasi — membuat preview instrumental cepat")
        render_bgm(info["mood"], str(output), duration, log=log)
    stage("mixing")
    voice_conversion: dict = {}
    reference_path = str(spec.get("_voice_reference_path") or "").strip()
    if reference_path and not spec.get("_voice_restyle_done"):
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
        master_audio(output, log=log)
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
        "lyrics_source": (lyric_info or {}).get("source", "user"),
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
        "voice_conversion": voice_conversion,
        "model": str((used_detail or {}).get("model", "")),
        "parent_job_id": spec.get("parent_job_id") or "",
        "cover_mode": cover_mode,
        "cover_engine": cover_engine if cover_mode else "",
        "cover_fidelity": (
            "original-vocal+genre-restyle"
            if (cover_engine == "arrangement" or spec.get("keep_original_vocal")) else
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
        "cover_filename": cover_filename, "cover_source": cover_source,
        "wav_filename": wav_filename,
        "cover_sampling": {
            key: (used_detail or {}).get(key)
            for key in ("cfg_scale", "temperature", "topk", "audio_sec")
            if (used_detail or {}).get(key) is not None
        },
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
