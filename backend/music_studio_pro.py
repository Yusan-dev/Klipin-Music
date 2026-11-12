"""Klipin Music Studio Pro utilities.

Modular helpers for the 7 Suno-level pro studio workflows:
1. Cross-lingual lyric translation & vocal/instrumental stem dubbing.
2. Section replacement / audio inpainting splice with smooth crossfade.
3. 4-stem separation, interactive mixdown (gains/mute/solo/pitch/tempo), and ZIP bundle.
4. Lyric video MP4 exporter (9:16 Reels/TikTok & 16:9 YouTube) with ASS karaoke + waveform.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import time
import zipfile
from pathlib import Path


LANG_LABELS = {
    "id": "Bahasa Indonesia",
    "ja": "Japanese (Bahasa Jepang)",
    "en": "English (Bahasa Inggris)",
    "ko": "Korean (Bahasa Korea)",
    "jv": "Javanese (Basa Jawa)",
    "su": "Sundanese (Basa Sunda)",
    "ar": "Arabic (Bahasa Arab)",
}


def _ffmpeg_bin() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"


def _probe_duration(path: Path) -> float:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe or not path.is_file():
        return 0.0
    try:
        res = subprocess.run(
            [
                ffprobe, "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True, text=True, timeout=20,
        )
        line = (res.stdout or "").strip().splitlines()
        return max(0.0, float(line[0])) if line else 0.0
    except Exception:
        return 0.0


def _ensure_raw_master(src: Path) -> Path:
    backup = src.parent / f"{src.stem}.raw_master{src.suffix}"
    if not backup.is_file() or backup.stat().st_size <= 1024:
        shutil.copy2(src, backup)
    return backup


def _export_wav_if_present(src: Path, job_dir: Path, log=None) -> None:
    if (job_dir / "song.wav").is_file():
        try:
            from music import export_wav_lufs
            export_wav_lufs(src, job_dir, log=log)
        except Exception:
            pass


# ── 1. Cross-Lingual Syllable-Matched Lyric Translation & Dubbing ────────────

def translate_lyrics_matched(
    lyrics: str,
    target_language: str = "ja",
    genre: str = "pop",
    title: str = "",
    bpm: int = 0,
    log=None,
) -> dict:
    """Translate lyrics into `target_language` matching syllable/mora count and section structure."""
    clean = (lyrics or "").strip()
    if len(clean) < 4:
        raise ValueError("Lirik minimal 4 karakter untuk diterjemahkan.")
    lang_code = (target_language or "ja").strip().lower()[:12]
    lang_label = LANG_LABELS.get(lang_code, lang_code)

    from lyrics_writer import _call_gemini

    script_hint = ""
    if lang_code == "ja":
        script_hint = (
            "Write Japanese lyrics in natural singable Romaji mixed with light Hiragana ONLY IF needed, "
            "WAIT — prefer pure clean Romaji OR natural Japanese Kana; actually output clean Kana (Hiragana/Katakana) "
            "with matching Japanese mora count per line so Japanese singing synthesis pronounces every syllable cleanly."
        )
    elif lang_code == "ko":
        script_hint = "Write natural Hangul Korean lines with matching syllable count per line."
    elif lang_code == "ar":
        script_hint = "Write natural singable Arabic lines with matching rhythmic meter per line."
    else:
        script_hint = f"Write natural poetic lyrics in {lang_label} with matching syllable count per line."

    bpm_note = f" Tempo is around {int(bpm)} BPM." if bpm else ""
    title_note = f' Song title: "{title}".' if title else ""
    prompt = (
        f"You are an expert song translator and lyricist for {genre} music.{title_note}{bpm_note}\n"
        f"Translate and adapt the following song lyrics into {lang_label}.\n"
        "STRICT RULES:\n"
        "1. Preserve every section tag verbatim on its own line (e.g. [Intro], [Verse], [Chorus], [Bridge], [Outro]).\n"
        "2. Output the EXACT same number of sung lines per section.\n"
        "3. Match each line's approximate syllable/mora count and phrasing rhythm so it fits the exact original melody.\n"
        f"4. {script_hint}\n"
        "5. Output ONLY the translated lyrics sheet. No explanations, no notes, no markdown fences.\n\n"
        f"ORIGINAL LYRICS:\n{clean}"
    )

    translated = _call_gemini(prompt, log=log).strip()
    translated = re.sub(r"^```[a-zA-Z]*\n?", "", translated).rstrip("`").strip()
    if not translated:
        raise RuntimeError("Penerjemah AI mengembalikan lirik kosong.")

    return {
        "target_language": lang_code,
        "target_language_label": lang_label,
        "translated_lyrics": translated,
        "source_line_count": len([ln for ln in clean.splitlines() if ln.strip()]),
        "translated_line_count": len([ln for ln in translated.splitlines() if ln.strip()]),
    }


def apply_crosslingual_dub(
    source_audio: Path,
    generated_dub_audio: Path,
    job_dir: Path,
    duration: float,
    preserve_music: bool = True,
    preserve_voice: bool = True,
    pitch_shift: int = 0,
    auto_f0: bool = True,
    log=None,
) -> dict:
    """Blend dubbed vocal over original instrumental and/or convert vocal back to source singer timbre.

    - preserve_music=True: keeps the isolated backing instruments from `source_audio`.
    - preserve_voice=True: clones timbre from `source_audio` vocals onto `generated_dub_audio` vocals.
    """
    if not source_audio.is_file() or not generated_dub_audio.is_file():
        return {"applied": False, "reason": "missing_audio"}

    if not preserve_music and not preserve_voice:
        return {"applied": False, "reason": "none_requested"}

    from music import _separate_source_stems, apply_voice_persona

    dur = max(8.0, float(duration or _probe_duration(generated_dub_audio) or 180.0))
    src_dir = job_dir / "_dub_src"
    new_dir = job_dir / "_dub_new"
    src_dir.mkdir(parents=True, exist_ok=True)
    new_dir.mkdir(parents=True, exist_ok=True)

    log and log("🌐 Memisahkan stem lagu asli untuk dubbing lintas bahasa…")
    src_b64 = base64.b64encode(source_audio.read_bytes()).decode("ascii")
    src_vocals, src_backing, src_method = _separate_source_stems(
        src_b64, dur, src_dir, pitch_shift=0, log=log
    )

    log and log("🎙️ Memisahkan stem vokal dubbing baru…")
    new_b64 = base64.b64encode(generated_dub_audio.read_bytes()).decode("ascii")
    new_vocals, new_backing, new_method = _separate_source_stems(
        new_b64, dur, new_dir, pitch_shift=0, log=log
    )

    vocal_track = new_vocals
    voice_applied = False
    if preserve_voice and src_vocals.is_file() and src_vocals.stat().st_size > 1024:
        try:
            log and log("🧬 Mengalihkan warna vokal penyanyi asli ke bahasa target…")
            converted = apply_voice_persona(
                new_vocals,
                src_vocals,
                new_dir,
                dur,
                log=log,
                semi_tone_shift=int(pitch_shift or 0),
                auto_f0=bool(auto_f0),
            )
            if converted.is_file() and converted.stat().st_size > 1024:
                vocal_track = converted
                voice_applied = True
        except Exception as exc:  # noqa: BLE001
            log and log(f"⚠️ Konversi warna suara dilewati ({exc}); memakai vokal target")

    backing_track = src_backing if preserve_music else new_backing
    ffmpeg = _ffmpeg_bin()
    mixed_out = job_dir / f"{generated_dub_audio.stem}.dub_mixed{generated_dub_audio.suffix}"
    codec = (
        ["-c:a", "libmp3lame", "-b:a", "192k"]
        if generated_dub_audio.suffix.lower() == ".mp3"
        else ["-c:a", "aac", "-b:a", "192k"]
    )
    cmd = [
        ffmpeg, "-y", "-v", "error",
        "-i", str(backing_track),
        "-i", str(vocal_track),
        "-filter_complex",
        (
            "[0:a]aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo,volume=0.95[bg];"
            "[1:a]aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo,volume=1.10[vo];"
            "[bg][vo]amix=inputs=2:duration=first:dropout_transition=2:normalize=0,"
            "highpass=f=26,alimiter=limit=0.96[out]"
        ),
        "-map", "[out]",
        "-t", f"{dur:.3f}",
        *codec,
        str(mixed_out),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=240)
    if res.returncode != 0 or not mixed_out.is_file() or mixed_out.stat().st_size <= 1024:
        raise RuntimeError(f"Mix dubbing gagal: {(res.stderr or '')[-250:]}")

    generated_dub_audio.write_bytes(mixed_out.read_bytes())
    mixed_out.unlink(missing_ok=True)
    log and log("✅ Dubbing lintas bahasa selesai di-mix ke master lagu")
    return {
        "applied": True,
        "preserve_music": bool(preserve_music),
        "preserve_voice": bool(preserve_voice),
        "voice_converted": voice_applied,
        "source_sep_method": src_method,
        "dub_sep_method": new_method,
    }


# ── 2. Replace Section / Inpainting Splice ───────────────────────────────────

def replace_audio_section(
    src: Path,
    job_dir: Path,
    start_sec: float,
    end_sec: float,
    replacement_audio: Path,
    crossfade_sec: float = 0.25,
    target_lufs: float = -14.0,
    log=None,
) -> dict:
    """Splice `replacement_audio` into `src` between [start_sec, end_sec] with smooth crossfades."""
    if not src.is_file() or src.stat().st_size <= 1024:
        raise FileNotFoundError("File audio lagu tidak ditemukan.")
    if not replacement_audio.is_file() or replacement_audio.stat().st_size <= 1024:
        raise FileNotFoundError("Audio pengganti tidak valid.")

    total_dur = _probe_duration(src)
    if total_dur <= 1.0:
        raise RuntimeError("Durasi lagu tidak valid.")

    s = max(0.0, min(float(start_sec), total_dur - 0.5))
    e = max(s + 0.5, min(float(end_sec), total_dur))
    seg_dur = round(e - s, 3)
    cf = max(0.04, min(float(crossfade_sec or 0.25), seg_dur * 0.25, 0.8))
    lufs = max(-18.0, min(-7.0, float(target_lufs or -14.0)))

    _ensure_raw_master(src)
    ffmpeg = _ffmpeg_bin()
    work_dir = job_dir / "_replace_tmp"
    work_dir.mkdir(parents=True, exist_ok=True)
    rep_norm = work_dir / "replacement.wav"
    out_tmp = work_dir / f"spliced{src.suffix}"

    try:
        # Match replacement segment exact duration + sample format
        rep_actual = _probe_duration(replacement_audio)
        rep_cmd = [
            ffmpeg, "-y", "-v", "error", "-i", str(replacement_audio),
        ]
        if rep_actual > 0.5 and abs(rep_actual - seg_dur) > 0.15 and seg_dur <= rep_actual + 0.5:
            rep_cmd.extend(["-ss", "0", "-t", f"{seg_dur:.3f}"])
        else:
            # Pad with silence if shorter, or trim to seg_dur
            rep_cmd.extend(["-af", f"apad=whole_dur={seg_dur:.3f}", "-t", f"{seg_dur:.3f}"])
        rep_cmd.extend(["-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le", str(rep_norm)])
        subprocess.run(rep_cmd, capture_output=True, text=True, timeout=120, check=True)

        inputs = []
        filter_parts = []
        has_prefix = s >= 0.08
        has_suffix = (total_dur - e) >= 0.08

        idx = 0
        labels = []
        if has_prefix:
            inputs.extend(["-ss", "0", "-t", f"{s:.3f}", "-i", str(src)])
            filter_parts.append(f"[{idx}:a]aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo[a_pre]")
            labels.append("[a_pre]")
            idx += 1

        inputs.extend(["-i", str(rep_norm)])
        filter_parts.append(f"[{idx}:a]aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo[a_mid]")
        labels.append("[a_mid]")
        idx += 1

        if has_suffix:
            inputs.extend(["-ss", f"{e:.3f}", "-i", str(src)])
            filter_parts.append(f"[{idx}:a]aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo[a_suf]")
            labels.append("[a_suf]")

        if len(labels) == 1:
            chain = f"{labels[0]}loudnorm=I={lufs:.1f}:TP=-1.5:LRA=11[out]"
        elif len(labels) == 2:
            chain = (
                f"{labels[0]}{labels[1]}acrossfade=d={cf:.3f}:c1=tri:c2=tri,"
                f"loudnorm=I={lufs:.1f}:TP=-1.5:LRA=11[out]"
            )
        else:
            chain = (
                f"{labels[0]}{labels[1]}acrossfade=d={cf:.3f}:c1=tri:c2=tri[m1];"
                f"[m1]{labels[2]}acrossfade=d={cf:.3f}:c1=tri:c2=tri,"
                f"loudnorm=I={lufs:.1f}:TP=-1.5:LRA=11[out]"
            )

        filter_complex = ";".join([*filter_parts, chain])
        codec = (
            ["-c:a", "libmp3lame", "-b:a", "192k"]
            if src.suffix.lower() == ".mp3"
            else ["-c:a", "aac", "-b:a", "192k"]
        )
        cmd = [
            ffmpeg, "-y", "-v", "error",
            *inputs,
            "-filter_complex", filter_complex,
            "-map", "[out]",
            "-ar", "44100", "-ac", "2",
            *codec,
            str(out_tmp),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=240)
        if res.returncode != 0 or not out_tmp.is_file() or out_tmp.stat().st_size <= 1024:
            raise RuntimeError(f"FFmpeg replace-section gagal: {(res.stderr or '')[-250:]}")

        src.write_bytes(out_tmp.read_bytes())
        _export_wav_if_present(src, job_dir, log=log)
        new_dur = _probe_duration(src) or total_dur
        log and log(f"✂️ Replace Section [{s:.1f}s–{e:.1f}s] selesai diterapkan")
        return {
            "start_sec": round(s, 3),
            "end_sec": round(e, 3),
            "crossfade_sec": round(cf, 3),
            "duration": round(new_dur, 3),
        }
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


# ── 3. Interactive 4-Stem Separation, Mixer & ZIP Bundle ─────────────────────

STEM_NAMES = ("vocals", "drums", "bass", "other")


def _find_cached_stems(stems_dir: Path) -> dict[str, Path]:
    found: dict[str, Path] = {}
    if not stems_dir.is_dir():
        return found
    for stem in STEM_NAMES:
        for ext in (".mp3", ".wav"):
            direct = stems_dir / f"{stem}{ext}"
            if direct.is_file() and direct.stat().st_size > 1024:
                found[stem] = direct
                break
        if stem not in found:
            nested = next(stems_dir.rglob(f"{stem}.*"), None)
            if nested and nested.is_file() and nested.stat().st_size > 1024:
                found[stem] = nested
    return found


def ensure_4stems(src: Path, job_dir: Path, log=None) -> dict:
    """Ensure 4 stems (vocals, drums, bass, other) exist under `job_dir/stems`."""
    if not src.is_file() or src.stat().st_size <= 1024:
        raise FileNotFoundError(f"Audio tidak ditemukan: {src}")

    stems_dir = job_dir / "stems"
    stems_dir.mkdir(parents=True, exist_ok=True)
    cached = _find_cached_stems(stems_dir)
    if len(cached) == 4:
        return {
            "dir": "stems",
            "files": [cached[s].name for s in STEM_NAMES],
            "stems": {s: f"stems/{cached[s].name}" for s in STEM_NAMES},
            "method": "cached",
        }

    dur = max(5.0, min(600.0, _probe_duration(src) or 180.0))

    # 1. Modal L4 GPU Demucs htdemucs (returns all 4 stems + backing)
    try:
        from music import _voice_modal_enabled
        if _voice_modal_enabled():
            from persona_previews import _function as _voice_fn
            log and log("🎚️ Memisahkan 4-stem via GPU L4 (Demucs htdemucs)…")
            src_b64 = base64.b64encode(src.read_bytes()).decode("ascii")
            res = _voice_fn("separate_stems").remote(src_b64, dur, 0)
            if isinstance(res, dict):
                mapping = {
                    "vocals": "vocals_b64",
                    "drums": "drums_b64",
                    "bass": "bass_b64",
                    "other": "other_b64",
                }
                wrote = {}
                for stem_key, b64_key in mapping.items():
                    if res.get(b64_key):
                        p = stems_dir / f"{stem_key}.mp3"
                        p.write_bytes(base64.b64decode(res[b64_key]))
                        if p.stat().st_size > 1024:
                            wrote[stem_key] = p
                if len(wrote) == 4:
                    return {
                        "dir": "stems",
                        "files": [wrote[s].name for s in STEM_NAMES],
                        "stems": {s: f"stems/{wrote[s].name}" for s in STEM_NAMES},
                        "method": str(res.get("method") or "demucs-modal-gpu"),
                    }
    except Exception as exc:  # noqa: BLE001
        log and log(f"ℹ️ GPU stems dilewati ({exc}); fallback ke pemisah lokal")

    # 2. Local Demucs htdemucs (4 stems)
    try:
        import importlib.util
        import sys as _sys
        if importlib.util.find_spec("demucs") is not None:
            log and log("🎚️ Menjalankan Demucs lokal (4-stem)…")
            tmp_demucs = stems_dir / "_raw"
            r = subprocess.run(
                [_sys.executable, "-m", "demucs", "-n", "htdemucs", "-o", str(tmp_demucs), str(src)],
                capture_output=True, text=True, timeout=900,
            )
            if r.returncode == 0:
                found_local = _find_cached_stems(tmp_demucs)
                if len(found_local) == 4:
                    ffmpeg = _ffmpeg_bin()
                    wrote = {}
                    for stem_key, wav_p in found_local.items():
                        mp3_p = stems_dir / f"{stem_key}.mp3"
                        subprocess.run(
                            [ffmpeg, "-y", "-v", "error", "-i", str(wav_p),
                             "-c:a", "libmp3lame", "-b:a", "192k", str(mp3_p)],
                            capture_output=True, timeout=120, check=True,
                        )
                        wrote[stem_key] = mp3_p
                    shutil.rmtree(tmp_demucs, ignore_errors=True)
                    return {
                        "dir": "stems",
                        "files": [wrote[s].name for s in STEM_NAMES],
                        "stems": {s: f"stems/{wrote[s].name}" for s in STEM_NAMES},
                        "method": "demucs-htdemucs-local",
                    }
    except Exception as exc:  # noqa: BLE001
        log and log(f"ℹ️ Demucs lokal dilewati ({exc})")

    # 3. Deterministic FFmpeg 4-band / Mid-Side Studio Extractor (guaranteed offline fallback)
    ffmpeg = _ffmpeg_bin()
    log and log("🎚️ Mengekstrak 4-stem via FFmpeg Studio Filterbank…")
    recipes = {
        "vocals": (
            "pan=stereo|c0=0.5*c0+0.5*c1|c1=0.5*c0+0.5*c1,"
            "highpass=f=220,lowpass=f=8500,"
            "equalizer=f=300:width_type=q:width=1.2:g=-3.0,"
            "equalizer=f=2800:width_type=q:width=1.1:g=2.5"
        ),
        "bass": "lowpass=f=180,volume=1.25",
        "drums": (
            "highpass=f=60,lowpass=f=14000,"
            "acompressor=threshold=-22dB:ratio=3.5:attack=4:release=70,"
            "equalizer=f=100:width_type=q:width=1.4:g=2.0,"
            "equalizer=f=6500:width_type=q:width=1.4:g=2.0"
        ),
        "other": (
            "pan=stereo|c0=c0-c1|c1=c1-c0,"
            "highpass=f=190,lowpass=f=12000,volume=1.3"
        ),
    }
    wrote = {}
    for stem_key, af in recipes.items():
        out_p = stems_dir / f"{stem_key}.mp3"
        subprocess.run(
            [ffmpeg, "-y", "-v", "error", "-i", str(src), "-af", af,
             "-ar", "44100", "-ac", "2", "-c:a", "libmp3lame", "-b:a", "192k", str(out_p)],
            capture_output=True, text=True, timeout=180, check=True,
        )
        wrote[stem_key] = out_p
    return {
        "dir": "stems",
        "files": [wrote[s].name for s in STEM_NAMES],
        "stems": {s: f"stems/{wrote[s].name}" for s in STEM_NAMES},
        "method": "ffmpeg-studio-filterbank",
    }


def mix_stems_track(
    src: Path,
    job_dir: Path,
    gains: dict[str, float] | None = None,
    mutes: dict[str, bool] | None = None,
    solos: list[str] | set[str] | None = None,
    pitch_semitones: int = 0,
    tempo: float = 1.0,
    target_lufs: float = -14.0,
    log=None,
) -> dict:
    """Render a custom mixdown of the 4 stems (+ optional pitch/tempo) onto `src`."""
    stems_meta = ensure_4stems(src, job_dir, log=log)
    stems_map = _find_cached_stems(job_dir / "stems")
    if not stems_map:
        raise RuntimeError("Stem gagal disiapkan untuk mixdown.")

    _ensure_raw_master(src)
    gains = gains or {}
    mutes = mutes or {}
    solo_set = {str(s).strip().lower() for s in (solos or []) if str(s).strip()}
    semitones = max(-12, min(12, int(pitch_semitones or 0)))
    tempo_val = max(0.75, min(1.35, float(tempo or 1.0)))
    lufs = max(-18.0, min(-7.0, float(target_lufs or -14.0)))

    inputs: list[str] = []
    chains: list[str] = []
    labels: list[str] = []
    applied_gains: dict[str, float] = {}

    for idx, stem_key in enumerate(STEM_NAMES):
        stem_path = stems_map.get(stem_key)
        if not stem_path:
            continue
        is_muted = bool(mutes.get(stem_key, False))
        if solo_set and stem_key not in solo_set:
            is_muted = True
        try:
            g = max(0.0, min(2.0, float(gains.get(stem_key, 1.0))))
        except (TypeError, ValueError):
            g = 1.0
        eff_g = 0.0 if is_muted else g
        applied_gains[stem_key] = round(eff_g, 3)
        inputs.extend(["-i", str(stem_path)])
        chains.append(
            f"[{idx}:a]aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo,"
            f"volume={eff_g:.3f}[s{idx}]"
        )
        labels.append(f"[s{idx}]")

    if not labels:
        raise RuntimeError("Tidak ada file stem yang valid untuk di-mix.")

    mix_expr = "".join(labels) + f"amix=inputs={len(labels)}:duration=longest:normalize=0"
    post_filters = ["highpass=f=24"]
    if semitones != 0:
        ratio = 2.0 ** (semitones / 12.0)
        post_filters.append(f"asetrate=44100*{ratio:.6f},aresample=44100,atempo={1.0/ratio:.6f}")
    if abs(tempo_val - 1.0) >= 0.01:
        post_filters.append(f"atempo={tempo_val:.4f}")
    post_filters.append(f"loudnorm=I={lufs:.1f}:TP=-1.5:LRA=11")

    filter_complex = ";".join([*chains, f"{mix_expr},{','.join(post_filters)}[out]"])
    ffmpeg = _ffmpeg_bin()
    tmp = src.parent / f"{src.stem}.mix_tmp{src.suffix}"
    codec = (
        ["-c:a", "libmp3lame", "-b:a", "192k"]
        if src.suffix.lower() == ".mp3"
        else ["-c:a", "aac", "-b:a", "192k"]
    )
    try:
        res = subprocess.run(
            [
                ffmpeg, "-y", "-v", "error",
                *inputs,
                "-filter_complex", filter_complex,
                "-map", "[out]",
                "-ar", "44100", "-ac", "2",
                *codec,
                str(tmp),
            ],
            capture_output=True, text=True, timeout=300,
        )
        if res.returncode != 0 or not tmp.is_file() or tmp.stat().st_size <= 1024:
            raise RuntimeError(f"FFmpeg stem mixdown gagal: {(res.stderr or '')[-250:]}")
        src.write_bytes(tmp.read_bytes())
        _export_wav_if_present(src, job_dir, log=log)
    finally:
        tmp.unlink(missing_ok=True)

    return {
        "stems": stems_meta,
        "gains": applied_gains,
        "pitch_semitones": semitones,
        "tempo": round(tempo_val, 3),
        "target_lufs": lufs,
        "duration": round(_probe_duration(src), 3),
    }


def zip_stems_bundle(job_dir: Path, src: Path, log=None) -> Path:
    """Create `job_dir/stems/stems_bundle.zip` containing all 4 isolated stems."""
    ensure_4stems(src, job_dir, log=log)
    stems_map = _find_cached_stems(job_dir / "stems")
    zip_path = job_dir / "stems" / "stems_bundle.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for stem_key in STEM_NAMES:
            p = stems_map.get(stem_key)
            if p and p.is_file():
                zf.write(p, arcname=f"{stem_key}{p.suffix}")
    return zip_path


# ── 5. Export Lyric Video MP4 (9:16 & 16:9 with ASS karaoke + waveform) ──────

def _fmt_ass_ts(seconds: float) -> str:
    sec = max(0.0, float(seconds))
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60.0
    cs = int(round((s - int(s)) * 100))
    if cs >= 100:
        cs = 99
    return f"{h}:{m:02d}:{int(s):02d}.{cs:02d}"


def _build_ass_subtitles(
    timeline: list[dict],
    fallback_lyrics: str,
    duration: float,
    width: int,
    height: int,
    out_ass: Path,
) -> Path:
    lines: list[dict] = []
    if timeline:
        for item in timeline:
            txt = str(item.get("text") or "").strip()
            if not txt or (txt.startswith("[") and txt.endswith("]")):
                continue
            st = max(0.0, float(item.get("start") or 0.0))
            en = float(item.get("end") or (st + 3.5))
            if en <= st:
                en = st + 3.0
            lines.append({"start": st, "end": min(duration, en), "text": txt})
    if not lines:
        raw_lines = [
            ln.strip()
            for ln in (fallback_lyrics or "").splitlines()
            if ln.strip() and not (ln.strip().startswith("[") and ln.strip().endswith("]"))
        ]
        if not raw_lines:
            raw_lines = ["Klipin Music Studio"]
        step = max(1.8, (duration - 1.0) / max(1, len(raw_lines)))
        for idx, txt in enumerate(raw_lines):
            st = idx * step + 0.5
            en = min(duration, st + step - 0.1)
            if st < duration:
                lines.append({"start": st, "end": max(st + 0.8, en), "text": txt})

    font_size = 44 if width < height else 40
    margin_v = 230 if width < height else 96
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {width}\n"
        f"PlayResY: {height}\n"
        "WrapStyle: 0\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,Poppins,{font_size},&H00FFFFFF,&H0038BDF8,&H00090E1A,&H80000000,"
        f"-1,0,0,0,100,100,0,0,1,3,1,2,48,48,{margin_v},1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    events = []
    for ln in lines:
        clean_txt = ln["text"].replace("{", "(").replace("}", ")").replace("\n", "\\N")
        events.append(
            f"Dialogue: 0,{_fmt_ass_ts(ln['start'])},{_fmt_ass_ts(ln['end'])},Default,,0,0,0,,{clean_txt}"
        )
    out_ass.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    return out_ass


def render_lyric_video_mp4(
    job: dict,
    job_dir: Path,
    audio_path: Path,
    ratio: str = "9:16",
    show_waveform: bool = True,
    log=None,
) -> dict:
    """Render MP4 lyric video combining cover art, waveform bar, and synced ASS lyrics."""
    if not audio_path.is_file() or audio_path.stat().st_size <= 1024:
        raise FileNotFoundError("File audio lagu tidak ditemukan.")

    norm_ratio = "16:9" if str(ratio or "").strip() == "16:9" else "9:16"
    width, height = (1280, 720) if norm_ratio == "16:9" else (720, 1280)
    duration = max(4.0, _probe_duration(audio_path) or float(job.get("duration") or 30.0))

    meta = job.get("metadata") or {}
    timeline = meta.get("lyrics_timeline") if isinstance(meta.get("lyrics_timeline"), list) else []
    ass_path = _build_ass_subtitles(
        timeline=timeline,
        fallback_lyrics=str(job.get("lyrics") or ""),
        duration=duration,
        width=width,
        height=height,
        out_ass=job_dir / f"lyric_video_{norm_ratio.replace(':', 'x')}.ass",
    )

    # Locate cover art or synthesize sleek indigo gradient backdrop
    cover_img = None
    for cand in (job_dir / "cover.png", job_dir / "cover.jpg", job_dir / "cover.webp"):
        if cand.is_file() and cand.stat().st_size > 1024:
            cover_img = cand
            break

    ffmpeg = _ffmpeg_bin()
    out_name = f"lyric_video_{norm_ratio.replace(':', 'x')}.mp4"
    out_mp4 = job_dir / out_name
    tmp_mp4 = job_dir / f".tmp_{out_name}"

    escaped_ass = str(ass_path).replace("\\", "/").replace(":", "\\:")
    font_file = "/usr/share/fonts/truetype/klipin/Poppins-Bold.ttf"
    has_poppins = Path(font_file).is_file()
    raw_title = (job.get("title") or "Klipin Music").replace("'", "").replace(":", " -")[:48]
    raw_genre = (meta.get("genre_label") or job.get("genre") or "AI Studio").replace("'", "").replace(":", " -")[:32]

    inputs = []
    if cover_img:
        inputs.extend(["-loop", "1", "-i", str(cover_img)])
    else:
        inputs.extend(["-f", "lavfi", "-i", f"color=c=#0f172a:s={width}x{height}:r=24"])
    inputs.extend(["-i", str(audio_path)])

    art_size = 440 if norm_ratio == "9:16" else 340
    art_y = 170 if norm_ratio == "9:16" else 70
    wave_h = 110 if norm_ratio == "9:16" else 70
    wave_y = height - 155 if norm_ratio == "9:16" else height - 80

    # Background blurred cover + centered sharp artwork card
    vf_parts = [
        f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},boxblur=24:4,eq=brightness=-0.18:saturation=1.15[bg]",
        f"[0:v]scale={art_size}:{art_size}:force_original_aspect_ratio=increase,"
        f"crop={art_size}:{art_size}[art]",
        f"[bg][art]overlay=(W-w)/2:{art_y}[canvas]",
    ]

    cur = "[canvas]"
    if show_waveform:
        vf_parts.append(
            f"[1:a]showwaves=s={width - 120}x{wave_h}:mode=cline:rate=24:colors=0x38bdf8|0xa855f7[wave]"
        )
        vf_parts.append(f"{cur}[wave]overlay=60:{wave_y}:shortest=1[wcanvas]")
        cur = "[wcanvas]"

    if has_poppins:
        title_y = art_y + art_size + 26 if norm_ratio == "9:16" else art_y + art_size + 18
        vf_parts.append(
            f"{cur}drawtext=fontfile={font_file}:text='{raw_title}':fontcolor=white:"
            f"fontsize={34 if norm_ratio == '9:16' else 28}:x=(w-text_w)/2:y={title_y}:"
            f"shadowcolor=black@0.7:shadowx=2:shadowy=2,"
            f"drawtext=fontfile={font_file}:text='{raw_genre}':fontcolor=#38bdf8:"
            f"fontsize=20:x=(w-text_w)/2:y={title_y + 42}[titled]"
        )
        cur = "[titled]"

    vf_parts.append(f"{cur}ass='{escaped_ass}'[vout]")
    filter_complex = ";".join(vf_parts)

    try:
        res = subprocess.run(
            [
                ffmpeg, "-y", "-v", "error",
                *inputs,
                "-filter_complex", filter_complex,
                "-map", "[vout]",
                "-map", "1:a",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "192k",
                "-t", f"{duration:.3f}",
                "-movflags", "+faststart",
                str(tmp_mp4),
            ],
            capture_output=True, text=True, timeout=300,
        )
        if res.returncode != 0 or not tmp_mp4.is_file() or tmp_mp4.stat().st_size <= 4096:
            raise RuntimeError(f"FFmpeg lyric video gagal: {(res.stderr or '')[-300:]}")
        tmp_mp4.replace(out_mp4)
        log and log(f"🎬 Video Lirik MP4 ({norm_ratio}) siap: {out_name}")
        return {
            "filename": out_name,
            "path": f"/videos/{job['job_id']}/{out_name}",
            "ratio": norm_ratio,
            "width": width,
            "height": height,
            "duration": round(duration, 3),
        }
    finally:
        tmp_mp4.unlink(missing_ok=True)

