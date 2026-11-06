"""AI Dubbing — translate a clip's speech into another language and re-voice it.

Pipeline (R6):
  1. Grab the transcript segments overlapping the clip's [start, end].
  2. Translate those segments to the target language (Gemini text model,
     cheap — reuses translate.translate_segments).
  3. Generate a TTS track per segment with edge-tts (free, no API key).
  4. Time-stretch each TTS track (atempo) to match the ORIGINAL segment
     duration so speech stays in sync, then delay it to the segment's
     position inside the clip (adelay).
  5. Mix all dubbed tracks over the original audio (original at 15 %
     volume so the room tone/music stays audible, dubbed voice at 100 %).

Kept in a tiny module — edge-tts is only imported when dubbing runs.
"""
import os
import subprocess
import tempfile

from translate import LANG_NAMES, translate_segments

# Default edge-tts voice per language (fallback when the user doesn't pick).
DEFAULT_VOICES = {
    "id": "id-ID-ArdiNeural",
    "en": "en-US-GuyNeural",
    "ms": "ms-MY-OsmanNeural",
    "zh": "zh-CN-YunxiNeural",
    "ja": "ja-JP-KeitaNeural",
    "ko": "ko-KR-InJoonNeural",
    "es": "es-ES-AlvaroNeural",
    "ar": "ar-SA-HamedNeural",
    "hi": "hi-IN-MadhurNeural",
    "th": "th-TH-NiwatNeural",
    "vi": "vi-VN-NamMinhNeural",
    "pt": "pt-BR-AntonioNeural",
    "ru": "ru-RU-DmitryNeural",
    "tr": "tr-TR-AhmetNeural",
    "fr": "fr-FR-HenriNeural",
    "de": "de-DE-ConradNeural",
}

# Extra voices per language users can pick from (male/female variants).
EXTRA_VOICES = {
    "id": ["id-ID-GadisNeural"],
    "en": ["en-US-JennyNeural", "en-GB-SoniaNeural"],
    "ms": ["ms-MY-YasminNeural"],
    "zh": ["zh-CN-XiaoxiaoNeural"],
    "ja": ["ja-JP-NanamiNeural"],
    "ko": ["ko-KR-SunHiNeural"],
    "es": ["es-ES-ElviraNeural"],
    "ar": ["ar-SA-ZariyahNeural"],
    "hi": ["hi-IN-SwaraNeural"],
    "th": ["th-TH-PremwadeeNeural"],
    "vi": ["vi-VN-HoaiMyNeural"],
    "pt": ["pt-BR-FranciscaNeural"],
    "ru": ["ru-RU-SvetlanaNeural"],
    "tr": ["tr-TR-EmelNeural"],
    "fr": ["fr-FR-DeniseNeural"],
    "de": ["de-DE-KatjaNeural"],
}


def available_languages() -> dict:
    """lang code -> human name, for the dubbing language picker."""
    return {code: LANG_NAMES.get(code, code) for code in DEFAULT_VOICES}


def voices_for(lang: str) -> list:
    """All voices usable for a language (default first)."""
    voices = [DEFAULT_VOICES.get(lang, "")]
    voices += EXTRA_VOICES.get(lang, [])
    return [v for v in voices if v]


def _generate_tts(text: str, voice: str, out_path: str, log=None,
                  rate: str = "+0%", pitch: str = "+0Hz") -> str:
    import asyncio
    import edge_tts

    async def _run():
        await edge_tts.Communicate(text, voice, rate=rate, pitch=pitch).save(out_path)

    asyncio.run(_run())
    if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
        raise RuntimeError("TTS produced no audio")
    return out_path


def _audio_duration(path: str) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True,
    )
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def _atempo_chain(ratio: float) -> str:
    """Build an atempo filter chain for any ratio (single atempo caps ~2.0)."""
    ratio = max(0.4, min(ratio, 4.0))
    factors = []
    while ratio > 2.0:
        factors.append("atempo=2.0")
        ratio /= 2.0
    while ratio < 0.5:
        factors.append("atempo=0.5")
        ratio /= 0.5
    factors.append(f"atempo={ratio:.3f}")
    return ",".join(factors)


def run_dub(job_id: str, clip_idx: int, target_lang: str,
            voice: str = "", out_root: str = "", sink=None) -> dict:
    """Dub one clip into `target_lang`. Returns {video_url, size_mb, lang, voice}."""
    import db

    lang = (target_lang or "en").strip().lower()
    if lang not in DEFAULT_VOICES:
        raise RuntimeError(f"Unsupported dubbing language: {lang}")
    voice = voice or DEFAULT_VOICES[lang]

    project = db.get_project(job_id)
    if not project:
        raise RuntimeError("Project not found")
    clips = project.get("clips") or []
    if clip_idx < 0 or clip_idx >= len(clips):
        raise RuntimeError(f"Clip index out of range: {clip_idx}")
    clip = clips[clip_idx]
    c0 = clip.get("start", 0)
    c1 = clip.get("end", c0 + 60)

    log = sink or (lambda m: None)
    src = os.path.join(out_root, job_id, f"clip_{clip_idx + 1}.mp4")
    if not os.path.exists(src):
        raise RuntimeError("Clip video no longer available on disk")
    out = os.path.join(out_root, job_id, f"clip_{clip_idx + 1}_dub_{lang}.mp4")

    # 1. segments overlapping the clip window
    segments = (project.get("transcript") or {}).get("segments") or []
    segs = [s for s in segments
            if s.get("end", 0) > c0 and s.get("start", 0) < c1]
    if not segs:
        raise RuntimeError("No speech found in this clip range — cannot dub")

    # 2. translate (only the in-clip segments, so the Gemini call stays tiny)
    log(f"🌐 Dubbing clip {clip_idx + 1} to {LANG_NAMES.get(lang, lang)}...")
    sub = {"segments": [{"start": s.get("start", 0), "end": s.get("end", 0),
                         "text": s.get("text", "")} for s in segs]}
    translated = translate_segments(sub, lang, log)
    tsegs = translated.get("segments") or segs

    # 3-5. per-segment TTS -> sync -> mix
    with tempfile.TemporaryDirectory() as tmp:
        # Generate one TTS file per *valid* segment (text + duration > 0.5s).
        jobs = []  # (tts_path, delay_ms, seg_dur)
        for i, (orig, tseg) in enumerate(zip(segs, tsegs)):
            text = (tseg.get("text") or orig.get("text") or "").strip()
            seg_start = max(0.0, float(orig.get("start", 0)) - c0)
            seg_dur = float(orig.get("end", 0)) - float(orig.get("start", 0))
            if not text or seg_dur <= 0.5:
                continue
            tts_path = os.path.join(tmp, f"seg_{i}.mp3")
            log(f"🎙️ Segment {i + 1}: TTS ({voice})...")
            _generate_tts(text, voice, tts_path, log)
            tts_dur = _audio_duration(tts_path)
            if tts_dur <= 0.1:
                continue
            jobs.append((tts_path, int(seg_start * 1000), seg_dur, tts_dur))
        if not jobs:
            raise RuntimeError("Dubbing failed: no segments produced audio")

        # Build the filter graph: original (15%) + each stretched/delayed dub.
        filters = ["[0:a]volume=0.15[aorig]"]
        for n, (path, delay_ms, seg_dur, tts_dur) in enumerate(jobs):
            ratio = seg_dur / tts_dur
            filters.append(
                f"[{n + 1}:a]{_atempo_chain(ratio)},"
                f"adelay={delay_ms}|{delay_ms}[dub{n}]"
            )
        mix_inputs = "".join(f"[dub{n}]" for n in range(len(jobs)))
        filters.append(
            f"{mix_inputs}[aorig]amix=inputs={len(jobs) + 1}:"
            f"duration=longest:normalize=0[aout]"
        )

        cmd = ["ffmpeg", "-y", "-i", src]
        for path, *_ in jobs:
            cmd += ["-i", path]
        cmd += ["-filter_complex", ";".join(filters),
                "-map", "0:v", "-map", "[aout]",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart", "-shortest", out]
        log(f"🎚️ Mixing {len(jobs)} dubbed segment(s) over original audio...")
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"Dubbing render failed: {r.stderr[-500:]}")

    log(f"✅ Dubbed clip {clip_idx + 1} → {lang} ({voice})")
    return {
        "clip_idx": clip_idx,
        "lang": lang,
        "voice": voice,
        "video_url": f"/videos/{job_id}/clip_{clip_idx + 1}_dub_{lang}.mp4",
        "size_mb": round(os.path.getsize(out) / 1e6, 1),
    }
