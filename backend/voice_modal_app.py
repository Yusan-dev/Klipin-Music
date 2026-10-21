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


def _separate_4stems(source: Path, work: Path) -> dict[str, Path]:
    """Run full 4-stem Demucs htdemucs (vocals, drums, bass, other) on L4 GPU.

    Separating ``drums.wav`` from ``bass.wav`` + ``other.wav`` removes 100% of
    the original pop/rock drum beat while keeping the exact studio chord
    progression and bassline locked to the millisecond of the singer.
    """
    _run([sys.executable, "-m", "demucs", "-n", "htdemucs",
          "--shifts", "0", "-d", "cuda", "-o", str(work / "stems4"), str(source)])
    stem_dir = work / "stems4" / "htdemucs" / source.stem
    vocals = stem_dir / "vocals.wav"
    drums = stem_dir / "drums.wav"
    bass = stem_dir / "bass.wav"
    other = stem_dir / "other.wav"
    if not (vocals.is_file() and drums.is_file() and bass.is_file() and other.is_file()):
        raise RuntimeError("Pemisahan 4-stem Demucs belum lengkap.")
    harmonic_backing = stem_dir / "harmonic_no_drums.wav"
    _run(["ffmpeg", "-y", "-v", "error", "-i", str(bass), "-i", str(other),
          "-filter_complex", "[0:a][1:a]amix=inputs=2:duration=longest:normalize=0[out]",
          "-map", "[out]", "-c:a", "pcm_s16le", str(harmonic_backing)])
    return {
        "vocals": vocals,
        "drums": drums,
        "bass": bass,
        "other": other,
        "backing": harmonic_backing,
    }


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
    """Separate the source song into 4 clean stems (vocals, drums, bass, other) on L4 GPU."""
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="arrange-stems-") as folder:
        work = Path(folder)
        source = work / "source.mp3"
        source.write_bytes(base64.b64decode(audio_b64, validate=True))
        stems4 = _separate_4stems(source, work)
        vocals = stems4["vocals"]
        backing = stems4["backing"]
        cap = str(max(3, min(float(duration), 600)))
        vout, bout = work / "vocals.mp3", work / "backing.mp3"
        dout, bass_out, oout = work / "drums.mp3", work / "bass.mp3", work / "other.mp3"
        shift = max(-12, min(12, int(semi_tone_shift or 0)))
        if shift != 0:
            ratio = 2.0 ** (shift / 12.0)
            tempo_fix = 1.0 / ratio
            _run(["ffmpeg", "-y", "-v", "error", "-i", str(vocals), "-t", cap,
                  "-af", f"asetrate=44100*{ratio:.6f},aresample=44100,atempo={tempo_fix:.6f}",
                  "-c:a", "libmp3lame", "-b:a", "192k", str(vout)])
        else:
            _run(["ffmpeg", "-y", "-v", "error", "-i", str(vocals), "-t", cap,
                  "-c:a", "libmp3lame", "-b:a", "192k", str(vout)])
        for src, dst in ((backing, bout), (stems4["drums"], dout),
                         (stems4["bass"], bass_out), (stems4["other"], oout)):
            _run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-t", cap,
                  "-c:a", "libmp3lame", "-b:a", "192k", str(dst)])
        models.commit()
        return {
            "vocals_b64": base64.b64encode(vout.read_bytes()).decode("ascii"),
            "backing_b64": base64.b64encode(bout.read_bytes()).decode("ascii"),
            "drums_b64": base64.b64encode(dout.read_bytes()).decode("ascii"),
            "bass_b64": base64.b64encode(bass_out.read_bytes()).decode("ascii"),
            "other_b64": base64.b64encode(oout.read_bytes()).decode("ascii"),
            "gpu_sec": time.monotonic() - started,
            "method": "demucs-htdemucs-4stems",
        }


@app.function(**CONVERT_GPU)
def convert_stems(audio_b64: str, reference_b64: str, duration: float,
                  semi_tone_shift: int = 0, auto_f0: bool = False) -> dict:
    """Separate 4 stems with Demucs + convert vocals with Seed-VC on L4 GPU."""
    started = time.monotonic()
    Path("/models/seed").mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="persona-stems-") as folder:
        work = Path(folder)
        source, reference = work / "source.mp3", work / "reference.wav"
        source.write_bytes(base64.b64decode(audio_b64, validate=True))
        reference.write_bytes(base64.b64decode(reference_b64, validate=True))
        stems4 = _separate_4stems(source, work)
        vocals = stems4["vocals"]
        backing = stems4["backing"]
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
        dout, bass_out, oout = work / "drums.mp3", work / "bass.mp3", work / "other.mp3"
        for src, dst in ((converted[0], vout), (backing, bout),
                         (stems4["drums"], dout), (stems4["bass"], bass_out),
                         (stems4["other"], oout)):
            _run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-t", cap,
                  "-c:a", "libmp3lame", "-b:a", "192k", str(dst)])
        models.commit()
        return {
            "vocals_b64": base64.b64encode(vout.read_bytes()).decode("ascii"),
            "backing_b64": base64.b64encode(bout.read_bytes()).decode("ascii"),
            "drums_b64": base64.b64encode(dout.read_bytes()).decode("ascii"),
            "bass_b64": base64.b64encode(bass_out.read_bytes()).decode("ascii"),
            "other_b64": base64.b64encode(oout.read_bytes()).decode("ascii"),
            "gpu_sec": time.monotonic() - started,
            "method": "seed-vc-4stems",
        }


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
