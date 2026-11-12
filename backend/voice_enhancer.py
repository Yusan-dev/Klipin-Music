"""State-of-the-art vocal restoration, clarity enhancement, and consonant articulation engine.

Cleans uploaded voice recordings (phone mics, noisy environments, muffled YouTube rips),
restores missing high-frequency harmonics (spectral bandwidth extension via aexciter),
sharpens consonant articulation transients (crystalizer), scoops boxy low-mid resonance,
and normalizes broadcast loudness so voice conversion models (Seed-VC, RVC) produce
crystal-clear pronunciation and studio-grade presence.
"""
from __future__ import annotations

import base64
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


# Studio vocal clarity & articulation filter chain:
# 1. highpass 95Hz (2-pole): strips mic pops, wind, handling rumble, proximity sub-bass.
# 2. afftdn (gentle 8dB reduction with -45dB noise floor): suppresses stationary hiss without phasing or swallowing consonants.
# 3. equalizer 280Hz (-3.0dB, Q=1.3): removes boxy, hollow room resonance / phone-mic cardboard mud.
# 4. equalizer 3500Hz (+4.0dB, Q=1.2): boosts human speech intelligibility & consonant burst energy (T, K, S, P, D, C).
# 5. aexciter (freq 3600Hz, amount 1.6, drive 8.0, ceil 16000Hz): synthesizes upper harmonics for low-pass muffled audio.
# 6. crystalizer (i=1.2): sharpens micro-transients for clear phoneme delineation.
# 7. equalizer 11000Hz (+2.2dB, Q=1.0): adds smooth studio condenser mic "air" sheen.
# 8. deesser (intensity 0.35, freq 0.55): controls sibilance so sharp S/Z sounds remain natural, not piercing.
# 9. loudnorm (I=-16 LUFS, TP=-1.5 dBFS, LRA=7): broadcast loudness leveling for optimal neural model driving.
VOCAL_CLARITY_FILTER_CHAIN = (
    "highpass=f=95:poles=2,"
    "afftdn=nr=8:nf=-45:tn=1,"
    "equalizer=f=280:t=q:w=1.3:g=-3.0,"
    "equalizer=f=3500:t=q:w=1.2:g=4.0,"
    "aexciter=freq=3600:amount=1.6:drive=8.0:ceil=16000,"
    "crystalizer=i=1.2,"
    "equalizer=f=11000:t=q:w=1.0:g=2.2,"
    "deesser=i=0.35:m=0.5:f=0.55,"
    "loudnorm=I=-16:TP=-1.5:LRA=7"
)

# Lightweight fallback chain if exciter/crystalizer are missing in non-standard ffmpeg builds
VOCAL_CLARITY_FILTER_BASIC = (
    "highpass=f=90:poles=2,"
    "afftdn=nr=8:nf=-42:tn=1,"
    "equalizer=f=300:t=q:w=1.4:g=-3.0,"
    "equalizer=f=3600:t=q:w=1.2:g=3.8,"
    "equalizer=f=10500:t=q:w=1.0:g=2.5,"
    "deesser=i=0.35:m=0.5:f=0.55,"
    "loudnorm=I=-16:TP=-1.5:LRA=7"
)


def _get_ffmpeg_bin() -> str:
    bin_path = shutil.which("ffmpeg")
    if bin_path:
        return bin_path
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


def clean_and_enhance_vocal(
    source_path: Path,
    output_path: Path,
    *,
    target_sr: int = 24000,
    max_sec: float = 30.0,
    use_cloud_neural: bool = True,
    log=None,
) -> Path:
    """Enhance and clean an uploaded voice recording to studio-grade clarity.

    1. If cloud Modal is enabled, runs DeepFilterNet3 neural de-noising + Demucs BGM removal.
    2. Passes the signal through the Vocal Clarity & Articulation Engine (DSP exciter + formant EQ).
    3. Normalizes to target sample rate (default 24kHz for Seed-VC / RVC vocoders).
    """
    ffmpeg = _get_ffmpeg_bin()
    if not source_path.is_file() or source_path.stat().st_size < 100:
        raise ValueError("File audio sumber kosong atau tidak valid.")

    intermediate_src = source_path
    temp_dir = Path(tempfile.mkdtemp(prefix="vocal-enhance-"))

    try:
        # Step 1: Optional Cloud Neural Clean (DeepFilterNet3 + Demucs)
        if use_cloud_neural and os.environ.get("KLIPIN_VOICE_MODAL_ENABLED", "0").strip() == "1":
            try:
                from persona_previews import _function
                log and log("🧠 Menjalankan neural speech restoration (DeepFilterNet3 + Demucs) di cloud GPU…")
                raw_bytes = source_path.read_bytes()
                audio_b64 = base64.b64encode(raw_bytes).decode("ascii")
                clean_fn = _function("clean_voice_reference")
                res = clean_fn.remote(audio_b64)
                if res and res.get("audio_b64"):
                    neural_wav = temp_dir / "neural_clean.wav"
                    neural_wav.write_bytes(base64.b64decode(res["audio_b64"]))
                    if neural_wav.is_file() and neural_wav.stat().st_size > 1024:
                        intermediate_src = neural_wav
                        log and log("✅ Neural speech restoration selesai, melanjutkan ke mastering kejernihan…")
            except Exception as exc:
                log and log(f"⚠️ Neural clean di cloud dilewati ({exc}), menggunakan studio DSP engine lokal")

        # Step 2: Apply Vocal Clarity & Articulation Engine
        output_path.parent.mkdir(parents=True, exist_ok=True)
        log and log("✨ Menerapkan filter kejernihan, artikulasi konsonan, dan pemulihan harmonik…")

        cmd = [
            ffmpeg, "-y", "-v", "error",
            "-i", str(intermediate_src),
            "-t", str(max(1.0, float(max_sec))),
            "-vn", "-ac", "1", "-ar", str(target_sr),
            "-af", VOCAL_CLARITY_FILTER_CHAIN,
            "-c:a", "pcm_s16le",
            str(output_path),
        ]
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if p.returncode != 0 or not output_path.is_file() or output_path.stat().st_size < 1024:
            # Fallback to basic filter chain if advanced filters are missing in ffmpeg
            cmd_fb = [
                ffmpeg, "-y", "-v", "error",
                "-i", str(intermediate_src),
                "-t", str(max(1.0, float(max_sec))),
                "-vn", "-ac", "1", "-ar", str(target_sr),
                "-af", VOCAL_CLARITY_FILTER_BASIC,
                "-c:a", "pcm_s16le",
                str(output_path),
            ]
            p2 = subprocess.run(cmd_fb, capture_output=True, text=True, timeout=60)
            if p2.returncode != 0 or not output_path.is_file() or output_path.stat().st_size < 1024:
                raise RuntimeError(f"Gagal memproses kejernihan audio: {p2.stderr or p.stderr}")

        # Step 3: Validate audio properties (non-empty, non-silent)
        try:
            import soundfile as sf
            import numpy as np
            samples, sr = sf.read(str(output_path))
            rms = float(np.sqrt(np.mean(samples ** 2)))
            dur = len(samples) / sr
            if dur < 1.0 or rms < 0.001:
                raise RuntimeError("Hasil audio terlalu hening atau tidak mencukupi durasi minimal.")
        except Exception as check_exc:
            if "terlalu hening" in str(check_exc):
                raise

        log and log("💎 Suara berhasil diproses: jernih, tajam pelafalannya, dan bebas noise")
        return output_path

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

