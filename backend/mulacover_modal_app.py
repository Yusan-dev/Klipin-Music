"""Official HeartMuLa MuLaCover worker for reference-audio song covers.

Deploy separately from the ordinary HeartMuLa text-to-song worker::

    modal deploy mulacover_modal_app.py

The official MuLaCover weights are optional and their model/output license is
not the same as the Apache-2.0 source license.  Keep this worker disabled
unless the deployment owner has accepted those terms and has Hugging Face
access to the checkpoint.
"""
from __future__ import annotations

import base64
import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import modal

APP_NAME = "klipin-mulacover"
MODEL_VOLUME = "klipin-mulacover-models"
MODEL_ROOT = Path("/models/mulacover")
SYMBOLIC_CACHE_ROOT = MODEL_ROOT / "symbolic_cache"

app = modal.App(APP_NAME)
models = modal.Volume.from_name(MODEL_VOLUME, create_if_missing=True)

image = (
    modal.Image.from_registry(
        "pytorch/pytorch:2.10.0-cuda13.0-cudnn9-runtime",
        add_python="3.10",
    )
    .apt_install("git", "ffmpeg", "libsndfile1", "curl")
    .pip_install("huggingface_hub>=0.27")
    .run_commands(
        "git clone --depth 1 https://github.com/HeartMuLa/MuLaCover.git /opt/mulacover",
        "cd /opt/mulacover && pip install -e '.[audio]'",
    )
)


def _download(url: str, target: Path) -> None:
    from urllib.request import urlopen

    target.parent.mkdir(parents=True, exist_ok=True)
    with urlopen(url, timeout=120) as response, target.open("wb") as fp:
        fp.write(response.read())


def _ensure_models() -> None:
    from huggingface_hub import snapshot_download

    MODEL_ROOT.mkdir(parents=True, exist_ok=True)
    marker = MODEL_ROOT / ".mulacover-ready"
    if marker.exists():
        return
    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_HUB_TOKEN")
    common = {"token": token} if token else {}
    snapshot_download("HeartMuLa/MuLaCover", local_dir=str(MODEL_ROOT / "MuLaCover"), **common)
    snapshot_download("Qwen/Qwen3-Embedding-0.6B", local_dir=str(MODEL_ROOT / "Qwen3-Embedding-0.6B"), **common)
    snapshot_download("HeartMuLa/HeartCodec-oss-20260123", local_dir=str(MODEL_ROOT / "HeartCodec-oss"), **common)
    # MuLaCover's reference-audio frontend requires YourMT3 plus all five
    # ChordNet folds. The URLs are the ones documented by the official guide.
    _download(
        "https://huggingface.co/spaces/mimbres/YourMT3/resolve/main/amt/logs/2024/"
        "mc13_256_g4_all_v7_mt3f_sqr_rms_moe_wf4_n8k2_silu_rope_rp_b36_nops/"
        "checkpoints/last.ckpt",
        MODEL_ROOT / "SymbolicTranscriptor/yourmt3/last.ckpt",
    )
    for fold in range(5):
        name = (
            "joint_chord_net_ismir_naive_v1.0_reweight(0.0,10.0)_s"
            f"{fold}.best.sdict"
        )
        _download(
            "https://raw.githubusercontent.com/music-x-lab/"
            f"ISMIR2019-Large-Vocabulary-Chord-Recognition/master/cache_data/{name}",
            MODEL_ROOT / "SymbolicTranscriptor/chord" / name,
        )
    marker.write_text("ready", encoding="utf-8")
    models.commit()


def _encode_mp3(wav_path: Path, output_path: Path) -> None:
    encoded = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(wav_path),
         "-c:a", "libmp3lame", "-b:a", "192k", str(output_path)],
        capture_output=True, text=True, timeout=300)
    if encoded.returncode != 0 or not output_path.exists():
        raise RuntimeError(encoded.stderr[-1000:] or "MuLaCover MP3 encode failed")


def _probe_seconds(path: Path) -> float:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=30)
        return max(0.0, float((result.stdout or "").strip() or 0.0))
    except Exception:
        return 0.0


def _assert_usable_output(path: Path, requested_sec: int) -> float:
    """Fail loudly on the known MuLaCover silent-collapse output.

    The CLI can exit 0 while writing a sub-second file (~3 KB). Marking that
    as a completed cover is worse than retrying/failing.
    """
    seconds = _probe_seconds(path)
    size = path.stat().st_size if path.exists() else 0
    floor = max(4.0, float(requested_sec or 0) * 0.15)
    if size < 16_000 or (seconds and seconds < floor):
        raise RuntimeError(
            "MULACOVER_EMPTY_AUDIO: cover output is near-empty "
            f"({seconds:.2f}s, {size} bytes; expected ~{requested_sec}s)")
    return seconds


def _build_genre_drum_midi(melody_mid: Path, chord_mid: Path,
                           tags: str, out_dir: Path) -> Path | None:
    """Synthesize a genre-authentic drum MIDI track on MuLaCover's exact 16th-note grid.

    MuLaCover conditions on ``drum_pianoroll`` via gated adaptive cross-attention.
    Passing the original pop song's ``drums.mid`` locks the cover to a pop drum beat,
    while omitting ``--drum_midi`` leaves ``drum_pianoroll`` all-zero. Building a
    genre-specific drum grid (e.g., Reggae One-Drop on beat 3 + offbeat hi-hats)
    via ``SymbolicCondition`` gives the model an authentic genre groove locked 1:1
    to the source song's bars, chords, and melody.
    """
    try:
        from mulacover.symbolic import MAX_SYMBOLIC_LENGTH, SymbolicCondition

        base = SymbolicCondition.from_midi(melody_mid, chord_mid)
        length = min(int(base.symbolic_length), MAX_SYMBOLIC_LENGTH - 4)
        if length < 16:
            return None
        low_tags = (tags or "").lower()
        drums: list[tuple[int, int, int]] = []
        for bar_start in range(0, length - 15, 16):
            bar_idx = bar_start // 16
            if "reggae" in low_tags or "ska" in low_tags:
                # Classic Reggae One-Drop:
                # - Beat 1 (step 0) is open (no kick!)
                # - Offbeats ("&" = steps 2, 6, 10, 14) have crisp skank hi-hat (46/42)
                # - Beat 3 (step 8) has the signature One-Drop Kick (36) + Cross-stick (37)
                for step in (0, 4, 8, 12):
                    drums.append((bar_start + step, 42, 1))
                for offbeat in (2, 6, 10, 14):
                    drums.append((bar_start + offbeat, 46, 2))
                drums.append((bar_start + 8, 36, 2))
                drums.append((bar_start + 8, 37, 2))
                if bar_idx % 2 == 1:
                    drums.append((bar_start + 14, 36, 1))
                    drums.append((bar_start + 15, 37, 1))
            elif "koplo" in low_tags or "dangdut" in low_tags:
                # Dangdut Koplo syncopated kendang/drum groove
                for step in (0, 2, 4, 6, 8, 10, 12, 14):
                    drums.append((bar_start + step, 42, 1))
                for k_step in (0, 6, 8, 14):
                    drums.append((bar_start + k_step, 36, 2))
                for s_step in (4, 11, 12, 15):
                    drums.append((bar_start + s_step, 38, 1))
            else:
                # Clean steady backbeat groove replacement
                for step in (0, 2, 4, 6, 8, 10, 12, 14):
                    drums.append((bar_start + step, 42, 1))
                drums.append((bar_start + 0, 36, 2))
                drums.append((bar_start + 4, 38, 2))
                drums.append((bar_start + 8, 36, 2))
                drums.append((bar_start + 12, 38, 2))
        if not drums:
            return None
        rebuilt = SymbolicCondition(
            melody=base.melody,
            chords=base.chords,
            drums=drums,
            bpm=base.bpm,
        )
        saved = rebuilt.save_midi(out_dir)
        drum_out = saved.get("drums")
        return drum_out if drum_out and Path(drum_out).is_file() else None
    except Exception:
        return None


def _sampling_extra(cfg_scale=None, temperature=None, topk=None):
    extra = []
    if cfg_scale is not None:
        extra += ["--cfg_scale", str(float(cfg_scale))]
    if temperature is not None:
        extra += ["--temperature", str(float(temperature))]
    if topk is not None:
        extra += ["--topk", str(int(topk))]
    return extra


def _run_cli(source_args, lyrics_path, tags_path, wav_path, duration_sec, seed, env, extra=()):
    cmd = [
        sys.executable, "-m", "mulacover.cli",
        "--model_path", str(MODEL_ROOT),
        *source_args,
        "--lyrics", str(lyrics_path), "--tags", str(tags_path),
        "--save_path", str(wav_path), "--device", "cuda:0",
        "--dtype", "bfloat16", "--lazy_load",
        "--max_audio_length_ms", str(max(10, min(int(duration_sec), 600)) * 1000),
        "--seed", str(int(seed)) if seed is not None else "0",
        "--disable_progress",
        *extra,
    ]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=2300, env=env)


_GPU = dict(
    image=image,
    gpu=os.environ.get("KLIPIN_MULACOVER_GPU", "L4"),
    cpu=8,
    memory=24576,
    timeout=2400,
    scaledown_window=120,
    min_containers=int(os.environ.get("KLIPIN_MULACOVER_MIN_CONTAINERS", "0")),
    volumes={"/models": models},
    secrets=[modal.Secret.from_name("klipin-backend-secrets")],
)


@app.function(**_GPU)
def generate_cover(ref_audio_b64: str, lyrics: str, tags: str,
                   duration_sec: int = 180, seed: int | None = None,
                   lora_id: str | None = None, cfg_scale: float | None = None,
                   temperature: float | None = None, topk: int | None = None,
                   drop_drums: bool = False) -> dict:
    """Generate a cover from source audio + locked lyrics + style tags.

    ``drop_drums`` skips the reference drum track even when its symbolic MIDI is
    cached, so a genre like reggae can rebuild the groove (one-drop) from the
    tags instead of inheriting the source song's pop/ballad beat.
    """
    _ensure_models()
    raw = base64.b64decode(ref_audio_b64 or "")
    if len(raw) < 1024 or len(raw) > 15_000_000:
        raise RuntimeError("MuLaCover reference audio must be between 1 KiB and 15 MB")
    if not str(lyrics or "").strip():
        raise RuntimeError("MuLaCover requires non-empty extracted lyrics")
    work = Path(tempfile.mkdtemp(prefix="klipin-mulacover-"))
    ref_path = work / "reference.mp3"
    lyrics_path = work / "lyrics.txt"
    tags_path = work / "tags.txt"
    wav_path = work / "cover.wav"
    output_path = work / "cover.mp3"
    ref_path.write_bytes(raw)
    lyrics_path.write_text(lyrics, encoding="utf-8")
    tags_path.write_text(tags, encoding="utf-8")
    # The reference-audio frontend (YourMT3 + 5 ChordNet, fp32) is the slow
    # part and it rescans the whole song on every call.  MuLaCover's
    # ``--symbolic_save_dir`` exports the exact condition as replayable MIDI,
    # so cache it keyed by the reference bytes and reuse it as
    # ``--melody_midi/--chord_midi`` to skip the transcription entirely.
    sha = hashlib.sha1(raw).hexdigest()[:16]
    cache_dir = SYMBOLIC_CACHE_ROOT / sha
    melody_mid = cache_dir / "melody.mid"
    chord_mid = cache_dir / "chord.mid"
    drum_mid = cache_dir / "drums.mid"
    have_midi = melody_mid.is_file() and chord_mid.is_file()
    env = dict(os.environ)
    if lora_id:
        env["KLIPIN_MUSIC_LORA_ID"] = str(lora_id)
    need_genre_groove = bool(drop_drums) or any(
        g in (tags or "").lower() for g in ("reggae", "ska", "koplo", "dangdut")
    )
    if not have_midi and need_genre_groove:
        # Groove replacement needs the symbolic cache to exist so we can
        # replace the original pop drum MIDI with a genre-authentic drum grid.
        _run_cli(["--ref_audio", str(ref_path), "--symbolic_save_dir", str(cache_dir)],
                 lyrics_path, tags_path, work / "transcribe.wav", 10, 0, env)
        have_midi = melody_mid.is_file() and chord_mid.is_file()
        if have_midi:
            try:
                models.commit()
            except Exception:
                pass
    if have_midi:
        source_args = ["--melody_midi", str(melody_mid), "--chord_midi", str(chord_mid)]
        if need_genre_groove:
            genre_drum = _build_genre_drum_midi(
                melody_mid, chord_mid, tags, work / "genre_midi"
            )
            if genre_drum and genre_drum.is_file():
                source_args += ["--drum_midi", str(genre_drum)]
            elif drum_mid.is_file() and not drop_drums:
                source_args += ["--drum_midi", str(drum_mid)]
        elif drum_mid.is_file():
            source_args += ["--drum_midi", str(drum_mid)]
        symbolic_args: list[str] = []
    else:
        source_args = ["--ref_audio", str(ref_path)]
        symbolic_args = ["--symbolic_save_dir", str(cache_dir)]
    result = _run_cli(source_args, lyrics_path, tags_path, wav_path,
                      duration_sec, seed, env,
                      extra=tuple(symbolic_args) + tuple(
                          _sampling_extra(cfg_scale, temperature, topk)))
    if result.returncode != 0 or not wav_path.exists():
        raise RuntimeError(result.stderr[-3000:] or "MuLaCover produced no audio")
    if not have_midi and melody_mid.is_file() and chord_mid.is_file():
        try:
            models.commit()
        except Exception:
            pass
    _encode_mp3(wav_path, output_path)
    actual_sec = _assert_usable_output(output_path, duration_sec)
    return {
        "provider": "mulacover-modal",
        "model": "HeartMuLa/MuLaCover",
        "duration_sec": int(duration_sec),
        "audio_sec": round(actual_sec, 2),
        "seed": seed,
        "lora_id": lora_id or "",
        "cfg_scale": cfg_scale,
        "temperature": temperature,
        "topk": topk,
        "drop_drums": bool(drop_drums),
        "audio_b64": base64.b64encode(output_path.read_bytes()).decode("ascii"),
        "stdout_tail": result.stdout[-1000:],
    }


@app.function(**_GPU)
def transcribe_ref(ref_audio_b64: str) -> dict:
    """Reference audio -> symbolic MIDI (melody/chord/drums/bpm).

    The frontend (YourMT3 + ChordNet) is the slow part; the exported MIDI is
    what Fase 3 edits and feeds back through ``generate_cover_from_midi``.
    Cached on the model volume keyed by the reference hash.
    """
    _ensure_models()
    raw = base64.b64decode(ref_audio_b64 or "")
    if len(raw) < 1024 or len(raw) > 15_000_000:
        raise RuntimeError("MuLaCover reference audio must be between 1 KiB and 15 MB")
    sha = hashlib.sha1(raw).hexdigest()[:16]
    cache_dir = SYMBOLIC_CACHE_ROOT / sha
    melody_mid = cache_dir / "melody.mid"
    chord_mid = cache_dir / "chord.mid"
    drum_mid = cache_dir / "drums.mid"
    cached = melody_mid.is_file() and chord_mid.is_file()
    if not cached:
        work = Path(tempfile.mkdtemp(prefix="klipin-mulacover-tr-"))
        ref_path = work / "reference.mp3"
        ref_path.write_bytes(raw)
        lyrics_path = work / "lyrics.txt"
        lyrics_path.write_text("[Intro]\n\n[Outro]\n", encoding="utf-8")
        tags_path = work / "tags.txt"
        tags_path.write_text("genre:[pop]\n", encoding="utf-8")
        wav_path = work / "preview.wav"
        result = _run_cli(
            ["--ref_audio", str(ref_path), "--symbolic_save_dir", str(cache_dir)],
            lyrics_path, tags_path, wav_path, 10, 0, dict(os.environ))
        if not (melody_mid.is_file() and chord_mid.is_file()):
            raise RuntimeError("MuLaCover transcription produced no symbolic MIDI: "
                               + (result.stderr or "")[-2000:])
        try:
            models.commit()
        except Exception:
            pass
    bpm = ""
    bpm_file = cache_dir / "bpm.txt"
    if bpm_file.is_file():
        bpm = bpm_file.read_text(encoding="utf-8").strip()
    return {
        "provider": "mulacover-modal", "kind": "symbolic", "sha": sha, "cached": cached,
        "bpm": bpm,
        "melody_b64": base64.b64encode(melody_mid.read_bytes()).decode("ascii"),
        "chord_b64": base64.b64encode(chord_mid.read_bytes()).decode("ascii"),
        "drums_b64": (base64.b64encode(drum_mid.read_bytes()).decode("ascii")
                      if drum_mid.is_file() else ""),
    }


@app.function(**_GPU)
def generate_cover_from_midi(melody_b64: str, chord_b64: str, drum_b64: str | None,
                             lyrics: str, tags: str, duration_sec: int = 180,
                             seed: int | None = None, lora_id: str | None = None,
                             cfg_scale: float | None = None,
                             temperature: float | None = None,
                             topk: int | None = None) -> dict:
    """Render a cover from saved/edited symbolic MIDI — skips the slow frontend."""
    _ensure_models()
    if not str(lyrics or "").strip():
        raise RuntimeError("MuLaCover requires non-empty lyrics")
    work = Path(tempfile.mkdtemp(prefix="klipin-mulacover-midi-"))
    melody_path = work / "melody.mid"
    chord_path = work / "chord.mid"
    melody_path.write_bytes(base64.b64decode(melody_b64))
    chord_path.write_bytes(base64.b64decode(chord_b64))
    source_args = ["--melody_midi", str(melody_path), "--chord_midi", str(chord_path)]
    if drum_b64:
        drum_path = work / "drums.mid"
        drum_path.write_bytes(base64.b64decode(drum_b64))
        source_args += ["--drum_midi", str(drum_path)]
    lyrics_path = work / "lyrics.txt"
    lyrics_path.write_text(lyrics, encoding="utf-8")
    tags_path = work / "tags.txt"
    tags_path.write_text(tags, encoding="utf-8")
    wav_path = work / "cover.wav"
    output_path = work / "cover.mp3"
    env = dict(os.environ)
    if lora_id:
        env["KLIPIN_MUSIC_LORA_ID"] = str(lora_id)
    result = _run_cli(source_args, lyrics_path, tags_path, wav_path,
                      duration_sec, seed, env,
                      extra=tuple(_sampling_extra(cfg_scale, temperature, topk)))
    if result.returncode != 0 or not wav_path.exists():
        raise RuntimeError(result.stderr[-3000:] or "MuLaCover produced no audio")
    _encode_mp3(wav_path, output_path)
    actual_sec = _assert_usable_output(output_path, duration_sec)
    return {
        "provider": "mulacover-modal", "model": "HeartMuLa/MuLaCover",
        "duration_sec": int(duration_sec), "audio_sec": round(actual_sec, 2),
        "seed": seed, "lora_id": lora_id or "", "source": "midi",
        "cfg_scale": cfg_scale, "temperature": temperature, "topk": topk,
        "audio_b64": base64.b64encode(output_path.read_bytes()).decode("ascii"),
        "stdout_tail": result.stdout[-1000:],
    }


@app.function(image=image, gpu="L4", timeout=300)
def health() -> dict:
    import torch

    return {"provider": "mulacover", "cuda": bool(torch.cuda.is_available()),
            "device": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "none"}
