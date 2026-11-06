"""AI Voiceover — re-voice a clip with TTS from its transcript.

Uses edge-tts (free, no API key) to regenerate speech, then mixes it
with the original audio so background music/ambience stays audible.

Pipeline:
  1. Extract transcript segments overlapping the clip window.
  2. Concatenate segment texts into full narration.
  3. Generate TTS audio with edge-tts (same language as source).
  4. Time-stretch TTS to match original speech duration.
  5. Mix: original audio at low volume (15%) + TTS at full volume.

Different from dubbing (dub.py): voiceover keeps the SAME language,
while dubbing translates to another language first.
"""
import os
import subprocess
import tempfile

from gpu import encode_args

# Reuse dub.py infrastructure
from dub import (
    _generate_tts,
    _audio_duration,
    _atempo_chain,
    DEFAULT_VOICES,
)


def generate_voiceover(text: str, voice: str, out_path: str,
                       target_duration: float = 0.0, log=None,
                       tone: str = "natural") -> str:
    """Generate TTS audio from text.

    Args:
        text: narration text
        voice: edge-tts voice name (e.g. "en-US-GuyNeural")
        out_path: output audio file path
        target_duration: if >0, time-stretch to match this duration
        log: logging callback

    Returns:
        out_path on success
    """
    log and log(f"🎤 Generating TTS: {len(text)} chars, voice={voice}")

    # Generate raw TTS
    tmp_tts = out_path + ".tts.mp3"
    tone_settings = {
        "santai": ("-10%", "-2Hz"),
        "energik": ("+12%", "+2Hz"),
        "serius": ("-5%", "-1Hz"),
        "berita": ("+5%", "+0Hz"),
        "natural": ("+0%", "+0Hz"),
    }
    rate, pitch = tone_settings.get(tone, tone_settings["natural"])
    log and log(f"   Tone: {tone} (rate {rate}, pitch {pitch})")
    _generate_tts(text, voice, tmp_tts, log=log, rate=rate, pitch=pitch)

    if target_duration > 0:
        tts_dur = _audio_duration(tmp_tts)
        if tts_dur > 0:
            ratio = target_duration / tts_dur
            log and log(f"   Time-stretch: {tts_dur:.1f}s -> {target_duration:.1f}s (ratio={ratio:.2f})")
            atempo = _atempo_chain(ratio)
            subprocess.run([
                "ffmpeg", "-y", "-i", tmp_tts,
                "-filter:a", atempo,
                *encode_args(log=log),
                out_path,
            ], capture_output=True, text=True)
            os.remove(tmp_tts)
        else:
            os.rename(tmp_tts, out_path)
    else:
        os.rename(tmp_tts, out_path)

    log and log(f"   TTS generated: {_audio_duration(out_path):.1f}s")
    return out_path


def mix_voiceover(src: str, tts_audio: str, out_path: str,
                  original_vol: float = 0.15, tts_vol: float = 1.0,
                  log=None) -> str:
    """Mix TTS voiceover with original audio.

    Args:
        src: source video (original audio)
        tts_audio: TTS audio file
        out_path: output video with mixed audio
        original_vol: volume multiplier for original audio (0.0-1.0)
        tts_vol: volume multiplier for TTS audio (0.0-1.0)
        log: logging callback

    Returns:
        out_path on success
    """
    log and log(f"🔊 Mixing: original={original_vol:.0%}, tts={tts_vol:.0%}")

    filter_complex = (
        f"[0:a]volume={original_vol}[orig];"
        f"[1:a]volume={tts_vol}[tts];"
        f"[orig][tts]amix=inputs=2:duration=first:dropout_transition=2[mix]"
    )

    subprocess.run([
        "ffmpeg", "-y",
        "-i", src,
        "-i", tts_audio,
        "-filter_complex", filter_complex,
        "-map", "0:v", "-map", "[mix]",
        "-c:v", "copy",
        *encode_args(log=log),
        "-c:a", "aac", "-b:a", "128k",
        out_path,
    ], capture_output=True, text=True)

    log and log(f"   Mixed audio saved: {out_path}")
    return out_path


def _narrated_intro_duration(tts_duration: float) -> float:
    """Return the frozen-card length for an AI-read opening caption.

    Narrated cards are driven by the generated audio, not by the visual hook
    duration selector. A short 2.8 second read must not leave users staring at
    a frozen frame for 5 or 10 seconds. Keep only a small natural tail so the
    last syllable is never clipped by AAC framing.
    """
    return max(0.75, float(tts_duration or 0.0) + 0.15)


def prepend_caption_intro(src: str, text: str, tts_audio: str,
                          out_path: str, duration: float = 5.0,
                          style="yellow", log=None) -> str:
    """Prepend a frozen opening-caption card, then start ``src``.

    Unlike ``mix_voiceover``, the source video does not advance underneath the
    narration. The main clip starts as soon as the TTS finishes; ``duration``
    is retained for stored-job/API compatibility but does not force dead air.
    """
    from PIL import Image, ImageEnhance, ImageFilter
    from hooks import create_hook_image
    from video import get_resolution, run

    tts_duration = _audio_duration(tts_audio)
    intro_duration = _narrated_intro_duration(tts_duration)
    work_dir = os.path.dirname(out_path) or "."
    stem = os.path.splitext(os.path.basename(out_path))[0]
    first_frame = os.path.join(work_dir, f"{stem}_opening_frame.jpg")
    overlay_path = os.path.join(work_dir, f"{stem}_opening_caption.png")
    card_path = os.path.join(work_dir, f"{stem}_opening_card.jpg")
    intro_path = os.path.join(work_dir, f"{stem}_opening_intro.mp4")

    try:
        run(["ffmpeg", "-y", "-ss", "0", "-i", src,
             "-frames:v", "1", first_frame], log)
        base = Image.open(first_frame).convert("RGB")
        if base.size != (1080, 1920):
            # The render pipeline is portrait already; this is a final safety
            # net for old jobs or imported clips with non-square pixels.
            from PIL import ImageOps
            base = ImageOps.fit(base, (1080, 1920), method=Image.Resampling.LANCZOS)
        base = ImageEnhance.Brightness(base).enhance(0.48)
        base = base.filter(ImageFilter.GaussianBlur(radius=2.0))

        _, box_w, box_h = create_hook_image(
            text, int(base.width * 0.88), overlay_path,
            font_scale=1.35, style=style,
        )
        overlay = Image.open(overlay_path).convert("RGBA")
        card = base.convert("RGBA")
        x = max(0, (base.width - box_w) // 2)
        # Keep the card above the subtitle zone and below the Shorts UI/header.
        y = max(120, min(int(base.height * 0.28), base.height - box_h - 260))
        card.alpha_composite(overlay, (x, y))
        card.convert("RGB").save(card_path, quality=95)

        run([
            "ffmpeg", "-y", "-loop", "1", "-framerate", "30",
            "-i", card_path, "-i", tts_audio,
            "-filter_complex",
            (f"[0:v]scale=1080:1920,setsar=1,format=yuv420p,"
             f"trim=duration={intro_duration:.3f},setpts=PTS-STARTPTS[v];"
             f"[1:a]aresample=48000,apad=pad_dur={intro_duration:.3f},"
             f"atrim=duration={intro_duration:.3f},asetpts=PTS-STARTPTS[a]"),
            "-map", "[v]", "-map", "[a]", "-t", f"{intro_duration:.3f}",
            *encode_args(log=log), "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart", intro_path,
        ], log)

        run([
            "ffmpeg", "-y", "-i", intro_path, "-i", src,
            "-filter_complex",
            ("[0:v]fps=30,scale=1080:1920,setsar=1,format=yuv420p,"
             "settb=AVTB,setpts=PTS-STARTPTS[v0];"
             "[1:v]fps=30,scale=1080:1920,setsar=1,format=yuv420p,"
             "settb=AVTB,setpts=PTS-STARTPTS[v1];"
             "[0:a]aresample=48000,aformat=sample_fmts=fltp:"
             "channel_layouts=stereo,asetpts=PTS-STARTPTS[a0];"
             "[1:a]aresample=48000,aformat=sample_fmts=fltp:"
             "channel_layouts=stereo,asetpts=PTS-STARTPTS[a1];"
             "[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]"),
            "-map", "[v]", "-map", "[a]",
            *encode_args(log=log), "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart", out_path,
        ], log)
        log and log(
            f"🎙️ Caption pembuka dibacakan {tts_duration:.1f}s; "
            f"video utama mulai setelah intro {intro_duration:.1f}s"
        )
        return out_path
    finally:
        for path in (first_frame, overlay_path, card_path, intro_path):
            try:
                os.remove(path)
            except OSError:
                pass


def replace_voiceover(src: str, tts_audio: str, out_path: str,
                      log=None) -> str:
    """Replace original speech with TTS (keep original volume envelope).

    Useful when the TTS quality is high enough to fully replace original.
    """
    log and log("🎤 Replacing original speech with TTS...")

    subprocess.run([
        "ffmpeg", "-y",
        "-i", src,
        "-i", tts_audio,
        "-map", "0:v", "-map", "1:a",
        "-c:v", "copy",
        *encode_args(log=log),
        "-c:a", "aac", "-b:a", "128k",
        out_path,
    ], capture_output=True, text=True)

    log and log(f"   Voiceover replaced: {out_path}")
    return out_path


def run_voiceover(job_id: str, clip_idx: int, out_root: str = "",
                  voice: str = "", mix_ratio: float = 0.15,
                  sink=None) -> dict:
    """Job entrypoint: generate AI voiceover for one clip.

    Args:
        job_id: project ID
        clip_idx: clip index
        out_root: output directory
        voice: edge-tts voice (auto-detect from transcript language if empty)
        mix_ratio: original audio volume (0.0 = full replace, 1.0 = no change)
        sink: logging callback

    Returns:
        {clip_idx, video_url, size_mb, voice, duration}
    """
    import db

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
    out = os.path.join(out_root, job_id, f"clip_{clip_idx + 1}_voiceover.mp4")

    # 1. Get transcript segments overlapping the clip window
    segments = (project.get("transcript") or {}).get("segments") or []
    segs = [s for s in segments
            if s.get("end", 0) > c0 and s.get("start", 0) < c1]
    if not segs:
        raise RuntimeError("No speech found in this clip range")

    # 2. Determine language and voice
    lang = (project.get("options") or {}).get("translate_to", "")
    if not lang or lang == "original":
        # Auto-detect from transcript (simple heuristic)
        text_sample = " ".join(s.get("text", "") for s in segs[:5])
        lang = _detect_language(text_sample)
    if not voice:
        voice = DEFAULT_VOICES.get(lang, DEFAULT_VOICES.get("en", "en-US-GuyNeural"))

    # 3. Build narration text from segments
    narration = " ".join(s.get("text", "").strip() for s in segs)
    if not narration.strip():
        raise RuntimeError("Empty transcript — cannot generate voiceover")

    # 4. Get clip duration for time-stretching
    clip_duration = c1 - c0

    log(f"🎤 Generating voiceover for clip {clip_idx + 1}...")

    with tempfile.TemporaryDirectory() as tmp:
        tts_path = os.path.join(tmp, "tts.mp3")

        # Generate TTS
        generate_voiceover(narration, voice, tts_path,
                          target_duration=clip_duration, log=log)

        # Mix with original
        mix_voiceover(src, tts_path, out,
                     original_vol=mix_ratio, tts_vol=1.0 - mix_ratio,
                     log=log)

    size_mb = round(os.path.getsize(out) / 1e6, 1) if os.path.exists(out) else 0
    log(f"✅ Voiceover done: {out} ({size_mb}MB)")

    return {
        "clip_idx": clip_idx,
        "video_url": f"/videos/{job_id}/clip_{clip_idx + 1}_voiceover.mp4",
        "size_mb": size_mb,
        "voice": voice,
        "duration": clip_duration,
    }


def _detect_language(text: str) -> str:
    """Simple language detection from text (CJK/Latin/Arabic heuristic)."""
    import re
    if not text:
        return "en"
    # Count character types
    cjk = len(re.findall(r"[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff]", text))
    arabic = len(re.findall(r"[\u0600-\u06ff]", text))
    latin = len(re.findall(r"[a-zA-Z]", text))
    total = max(1, cjk + arabic + latin)

    if cjk / total > 0.3:
        # CJK — try to distinguish Chinese/Japanese/Korean
        if re.search(r"[\u3040-\u309f\u30a0-\u30ff]", text):
            return "ja"  # Japanese hiragana/katakana
        if re.search(r"[\uac00-\ud7af]", text):
            return "ko"  # Korean hangul
        return "zh"  # Chinese
    if arabic / total > 0.3:
        return "ar"
    # Latin script — distinguish Indonesian from English via stopwords so
    # narasi ID tidak selalu dapat voice EN.
    low = text.lower()
    id_hits = sum(1 for w in (" yang ", " dan ", " dengan ", " untuk ", " dari ",
                              " adalah ", " ini ", " itu ", " tidak ", " saya ",
                              " kita ", " mereka ") if w in f" {low} ")
    if id_hits >= 2:
        return "id"
    return "en"
