"""Dedicated DiffRhythm GPU worker for Klipin Music (Apache 2.0 commercial-ready).

DiffRhythm (ASLP-lab/DiffRhythm-full + ASLP-lab/DiffRhythm-vae) is an open-source
end-to-end latent diffusion model for full-length song generation with both vocals
and accompaniment from LRC-timed lyrics and style tags. Both code and model weights
are licensed under Apache 2.0, making it safe for commercial production in Klipin
Music (replacing the non-commercial YuE2 worker).

This worker is intentionally separate from ``modal_app.py`` (the Klipin Video
render worker) so the video portrait/render contract remains completely untouched.

Deploy from ``backend`` with::

    modal deploy diffrhythm_modal_app.py
"""
from __future__ import annotations

import base64
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import modal


APP_NAME = "klipin-diffrhythm"
MODEL_VOLUME = "klipin-diffrhythm-models"
MODEL_ROOT = Path("/models/diffrhythm")

app = modal.App(APP_NAME)
models = modal.Volume.from_name(MODEL_VOLUME, create_if_missing=True)

image = (
    modal.Image.from_registry(
        "pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime",
        add_python="3.10",
    )
    .apt_install("git", "ffmpeg", "espeak-ng", "libsndfile1")
    .pip_install("huggingface_hub>=0.27", "soundfile", "einops", "torchaudio")
    .run_commands(
        "git clone --depth 1 https://github.com/ASLP-lab/DiffRhythm.git /opt/diffrhythm",
        "cd /opt/diffrhythm && pip install -r requirements.txt",
    )
)


def _ensure_models() -> None:
    """Download DiffRhythm Apache-2.0 weights once into the persistent Volume."""
    from huggingface_hub import snapshot_download

    MODEL_ROOT.mkdir(parents=True, exist_ok=True)
    full_dir = MODEL_ROOT / "DiffRhythm-full"
    vae_dir = MODEL_ROOT / "DiffRhythm-vae"
    marker = MODEL_ROOT / ".diffrhythm-apache2-ready"
    if marker.exists() and full_dir.is_dir() and vae_dir.is_dir():
        return
    snapshot_download("ASLP-lab/DiffRhythm-full", local_dir=str(full_dir))
    snapshot_download("ASLP-lab/DiffRhythm-vae", local_dir=str(vae_dir))
    marker.write_text("ready", encoding="utf-8")
    models.commit()


def _style_prompt(style: str, instrumental: bool) -> str:
    value = " ".join(str(style or "warm modern pop, clear vocal").split())
    if instrumental:
        return (
            "Instrumental only, no vocals, no singing, no spoken words; "
            + value
        )
    return value


@app.function(
    image=image,
    gpu="L4",
    cpu=8,
    memory=24576,
    timeout=1200,
    scaledown_window=120,
    min_containers=int(os.environ.get("KLIPIN_DIFFRHYTHM_MIN_CONTAINERS", "0")),
    volumes={"/models": models},
)
def generate_song(
    style: str,
    lrc_text: str,
    duration_sec: int = 180,
    seed: int | None = None,
    instrumental: bool = False,
) -> dict:
    """Generate a full song via DiffRhythm and return a compact MP3 payload."""
    _ensure_models()
    duration = max(15, min(int(duration_sec or 180), 285))
    work = Path(tempfile.mkdtemp(prefix="klipin-diffrhythm-"))
    lrc_path = work / "lyrics.lrc"
    out_dir = work / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    lrc_path.write_text("" if instrumental else str(lrc_text or ""), encoding="utf-8")

    env = dict(os.environ)
    env["HF_HOME"] = str(MODEL_ROOT / "hf_cache")
    if seed is not None:
        env["PYTHONHASHSEED"] = str(int(seed))

    prompt = _style_prompt(style, bool(instrumental))
    cmd = [
        sys.executable,
        "/opt/diffrhythm/infer/infer.py",
        "--lrc-path", str(lrc_path),
        "--ref-prompt", prompt,
        "--audio-length", str(duration),
        "--output-dir", str(out_dir),
        "--chunked",
    ]
    result = subprocess.run(
        cmd,
        cwd="/opt/diffrhythm",
        capture_output=True,
        text=True,
        timeout=1100,
        env=env,
    )
    candidates = [
        p for p in out_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in (".wav", ".mp3", ".flac")
    ]
    if result.returncode != 0 or not candidates:
        raise RuntimeError(
            (result.stderr or result.stdout)[-2000:]
            or "DiffRhythm produced no audio output"
        )
    source_audio = max(candidates, key=lambda p: p.stat().st_mtime)
    mp3_path = work / "song.mp3"
    encoded = subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error", "-i", str(source_audio),
            "-vn", "-ac", "2", "-ar", "44100",
            "-c:a", "libmp3lame", "-b:a", "192k", str(mp3_path),
        ],
        capture_output=True,
        text=True,
        timeout=180,
    )
    if encoded.returncode != 0 or not mp3_path.is_file() or mp3_path.stat().st_size < 1024:
        raise RuntimeError(encoded.stderr[-1000:] or "DiffRhythm MP3 encoding failed")

    return {
        "provider": "diffrhythm-modal",
        "model": "ASLP-lab/DiffRhythm-full",
        "vae": "ASLP-lab/DiffRhythm-vae",
        "duration_sec": duration,
        "seed": seed,
        "instrumental": bool(instrumental),
        "audio_b64": base64.b64encode(mp3_path.read_bytes()).decode("ascii"),
        "stdout_tail": (result.stdout or "")[-800:],
        "license_note": "Apache-2.0 (ASLP-lab/DiffRhythm-full & DiffRhythm-vae). Commercial use permitted.",
    }


@app.function(image=image, gpu="L4", timeout=300)
def health() -> dict:
    import torch

    return {
        "provider": "diffrhythm",
        "license": "Apache-2.0",
        "cuda": bool(torch.cuda.is_available()),
        "device": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "none",
    }
