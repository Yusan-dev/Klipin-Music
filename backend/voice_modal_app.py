"""Private singing persona worker. Deploy: modal deploy voice_modal_app.py.

No public HTTP endpoint. Only model weights are persisted in the Volume.
Seed-VC is GPL-3.0; upstream license remains in /opt/seed-vc/LICENSE.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import modal

app = modal.App("klipin-voice")
models = modal.Volume.from_name("klipin-voice-models", create_if_missing=True)
image = (modal.Image.debian_slim(python_version="3.10")
         .apt_install("git", "ffmpeg", "libsndfile1", "build-essential")
         .pip_install("torch==2.4.0", "torchaudio==2.4.0", "numpy==1.26.4",
                      "scipy==1.13.1", "librosa==0.10.2", "soundfile==0.12.1",
                      "huggingface-hub>=0.28.1,<1", "transformers==4.46.3",
                      "accelerate", "munch==4.0.0", "einops==0.8.0",
                      "descript-audio-codec==1.0.0", "resemblyzer",
                      "hydra-core==1.3.2", "pyyaml", "modelscope==1.18.1",
                      "funasr==1.1.5", "demucs==4.0.1", "python-dotenv")
         .run_commands("git clone --depth 1 https://github.com/Plachtaa/seed-vc.git /opt/seed-vc",
                       "ln -s /models/seed /opt/seed-vc/checkpoints")
         # Audio dependencies pull an old protobuf incompatible with Modal's runner.
         .pip_install("protobuf>=5.29,<6")
         .env({"HF_HOME": "/models/huggingface", "TORCH_HOME": "/models/torch"}))

GPU = dict(image=image, gpu="L4", cpu=8, memory=16384, timeout=1800,
           max_containers=1, scaledown_window=60, retries=0,
           volumes={"/models": models})

# Cover conversion can be kept warm for an active render session without
# pinning every GPU function.  Default 0 so idle time is never billed.
CONVERT_GPU = {**GPU, "min_containers": int(os.environ.get("KLIPIN_VOICE_MIN_CONTAINERS", "0"))}


def _run(args, timeout=1200, cwd=None):
    completed = subprocess.run(args, capture_output=True, text=True,
                               timeout=timeout, cwd=cwd)
    if completed.returncode:
        raise RuntimeError(completed.stderr[-1800:] or "Voice worker failed")
    return completed


def _separate(source: Path, work: Path) -> tuple[Path, Path]:
    _run([sys.executable, "-m", "demucs", "-n", "htdemucs", "--two-stems=vocals",
          "--shifts", "0", "-d", "cuda", "-o", str(work / "stems"), str(source)])
    vocals = work / "stems" / "htdemucs" / source.stem / "vocals.wav"
    backing = vocals.with_name("no_vocals.wav")
    if not vocals.is_file() or not backing.is_file():
        raise RuntimeError("Stem vokal belum berhasil dibuat.")
    return vocals, backing


@app.function(**GPU)
def warmup() -> dict:
    """Explicit setup: download and load both models before enabling the UI."""
    Path("/models/seed").mkdir(parents=True, exist_ok=True)
    _run([sys.executable, "-c",
          "from demucs.pretrained import get_model; get_model('htdemucs'); "
          "import inference; from argparse import Namespace; "
          "inference.load_models(Namespace(f0_condition=True,fp16=True,checkpoint=None,config=None))"],
         timeout=1700, cwd="/opt/seed-vc")
    Path("/models/ready-v1").write_text("demucs + seed-vc f0 loaded", encoding="utf-8")
    models.commit()
    return {"ready": True}


@app.function(image=image, volumes={"/models": models}, timeout=30)
def readiness() -> dict:
    models.reload()
    return {"ready": Path("/models/ready-v1").is_file(), "version": "singing-persona-v1"}


@app.function(**GPU)
def extract_voice(audio_b64: str) -> dict:
    import numpy as np
    import soundfile as sf

    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="persona-preview-") as folder:
        work = Path(folder)
        source = work / "source.mp3"
        source.write_bytes(base64.b64decode(audio_b64, validate=True))
        vocal, _ = _separate(source, work)
        normalized = work / "reference.wav"
        _run(["ffmpeg", "-y", "-v", "error", "-i", str(vocal), "-t", "16",
              "-ac", "1", "-ar", "24000", "-af", "loudnorm=I=-20:TP=-2:LRA=7",
              "-c:a", "pcm_s16le", str(normalized)])
        samples, sr = sf.read(normalized)
        duration = len(samples) / sr
        if duration < 3 or not np.isfinite(samples).all() or np.sqrt(np.mean(samples ** 2)) < 0.002:
            raise RuntimeError("Preview belum berisi vokal yang cukup jelas. Coba variasi lain.")
        models.commit()
        return {"audio_b64": base64.b64encode(normalized.read_bytes()).decode("ascii"),
                "duration": duration, "gpu_sec": time.monotonic() - started}


@app.function(image=modal.Image.debian_slim(python_version="3.10"),
              timeout=3000, max_containers=2, retries=0)
def design_preview(tags: str, seed: int, music_app: str = "klipin-music") -> dict:
    """Durable orchestration; CPU waits while the two GPU stages run."""
    started = time.monotonic()
    audio = modal.Function.from_name(music_app, "generate_song").remote(
        lyrics="[Verse]\nDi sini ku bernyanyi\nMembawa warna sendiri\nNada mengalun di hati\nSuara ini menemani\n[Outro]",
        tags="indonesian vocal,solo singer,a cappella,dry studio vocal,immediate vocals,no intro," + tags,
        duration_sec=16, seed=int(seed))
    result = extract_voice.remote(audio["audio_b64"])
    result.update({"seed": seed, "elapsed_sec": time.monotonic() - started,
                   "model": "heartmula-seed-vc-persona-v1"})
    return result


@app.function(**CONVERT_GPU)
def separate_stems(audio_b64: str, duration: float,
                   semi_tone_shift: int = 0) -> dict:
    """Separate the source song into clean original vocals + backing stem (Demucs L4).

    Used by vocal-overlay arrangement covers (where the original singer's vocal
    is preserved and overlaid onto a newly arranged genre instrumental).
    """
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="arrange-stems-") as folder:
        work = Path(folder)
        source = work / "source.mp3"
        source.write_bytes(base64.b64decode(audio_b64, validate=True))
        vocals, backing = _separate(source, work)
        cap = str(max(3, min(float(duration), 600)))
        vout, bout = work / "vocals.mp3", work / "backing.mp3"
        shift = max(-12, min(12, int(semi_tone_shift or 0)))
        if shift != 0:
            ratio = 2.0 ** (shift / 12.0)
            tempo_fix = 1.0 / ratio
            # Preserve duration while shifting pitch by semitones
            _run(["ffmpeg", "-y", "-v", "error", "-i", str(vocals), "-t", cap,
                  "-af", f"asetrate=44100*{ratio:.6f},aresample=44100,atempo={tempo_fix:.6f}",
                  "-c:a", "libmp3lame", "-b:a", "192k", str(vout)])
        else:
            _run(["ffmpeg", "-y", "-v", "error", "-i", str(vocals), "-t", cap,
                  "-c:a", "libmp3lame", "-b:a", "192k", str(vout)])
        _run(["ffmpeg", "-y", "-v", "error", "-i", str(backing), "-t", cap,
              "-c:a", "libmp3lame", "-b:a", "192k", str(bout)])
        models.commit()
        return {"vocals_b64": base64.b64encode(vout.read_bytes()).decode("ascii"),
                "backing_b64": base64.b64encode(bout.read_bytes()).decode("ascii"),
                "gpu_sec": time.monotonic() - started,
                "method": "demucs-htdemucs-original-vocal"}


@app.function(**CONVERT_GPU)
def convert_stems(audio_b64: str, reference_b64: str, duration: float,
                  semi_tone_shift: int = 0, auto_f0: bool = False) -> dict:
    """Separate the source and return *both* stems: converted vocals + backing.

    Used by arrangement-restyle covers: the converted vocal keeps the original
    melody/timing while the caller re-arranges the backing into a new genre.
    """
    started = time.monotonic()
    Path("/models/seed").mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="persona-stems-") as folder:
        work = Path(folder)
        source, reference = work / "source.mp3", work / "reference.wav"
        source.write_bytes(base64.b64decode(audio_b64, validate=True))
        reference.write_bytes(base64.b64decode(reference_b64, validate=True))
        vocals, backing = _separate(source, work)
        output_dir = work / "converted"
        _run([sys.executable, "/opt/seed-vc/inference.py", "--source", str(vocals),
              "--target", str(reference), "--output", str(output_dir),
              "--diffusion-steps", "30", "--f0-condition", "True",
              "--auto-f0-adjust", "True" if auto_f0 else "False",
              "--semi-tone-shift", str(int(semi_tone_shift))],
             timeout=1500, cwd="/opt/seed-vc")
        converted = list(output_dir.glob("*.wav"))
        if len(converted) != 1:
            raise RuntimeError("Hasil konversi vokal tidak ditemukan.")
        cap = str(max(3, min(float(duration), 600)))
        vout, bout = work / "vocals.mp3", work / "backing.mp3"
        for src, dst in ((converted[0], vout), (backing, bout)):
            _run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-t", cap,
                  "-c:a", "libmp3lame", "-b:a", "192k", str(dst)])
        models.commit()
        return {"vocals_b64": base64.b64encode(vout.read_bytes()).decode("ascii"),
                "backing_b64": base64.b64encode(bout.read_bytes()).decode("ascii"),
                "gpu_sec": time.monotonic() - started,
                "method": "seed-vc-vocal-stem"}


@app.function(**CONVERT_GPU)
def convert_song(audio_b64: str, reference_b64: str, duration: float,
                 semi_tone_shift: int = 0, auto_f0: bool = False) -> dict:
    """Preserve the generated melody and backing; replace the vocal identity."""
    started = time.monotonic()
    Path("/models/seed").mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="persona-convert-") as folder:
        work = Path(folder)
        source, reference = work / "source.mp3", work / "reference.wav"
        source.write_bytes(base64.b64decode(audio_b64, validate=True))
        reference.write_bytes(base64.b64decode(reference_b64, validate=True))
        vocals, backing = _separate(source, work)
        output_dir = work / "converted"
        _run([sys.executable, "/opt/seed-vc/inference.py", "--source", str(vocals),
              "--target", str(reference), "--output", str(output_dir),
              "--diffusion-steps", "30", "--f0-condition", "True",
              "--auto-f0-adjust", "True" if auto_f0 else "False",
              "--semi-tone-shift", str(int(semi_tone_shift))],
             timeout=1500, cwd="/opt/seed-vc")
        converted = list(output_dir.glob("*.wav"))
        if len(converted) != 1:
            raise RuntimeError("Hasil konversi vokal tidak ditemukan.")
        target = work / "song.mp3"
        _run(["ffmpeg", "-y", "-v", "error", "-i", str(backing), "-i", str(converted[0]),
              "-filter_complex", "[0:a][1:a]amix=inputs=2:duration=longest:normalize=0,alimiter=limit=0.96[out]",
              "-map", "[out]", "-t", str(max(3, min(float(duration), 600))),
              "-c:a", "libmp3lame", "-b:a", "192k", str(target)])
        models.commit()
        return {"audio_b64": base64.b64encode(target.read_bytes()).decode("ascii"),
                "gpu_sec": time.monotonic() - started, "method": "seed-vc-vocal-stem"}
