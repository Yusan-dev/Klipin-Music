"""Private singing persona worker with RVC v2 (RMVPE) + Seed-VC fallback.
Deploy: modal deploy voice_modal_app.py.

No public HTTP endpoint. Model weights and checkpoints are persisted in the Volume.
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
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
                      "funasr==1.1.5", "demucs==4.0.1", "python-dotenv",
                      "faiss-cpu", "soxr", "torchcrepe", "tqdm", "onnxruntime")
         .run_commands("git clone --depth 1 https://github.com/Plachtaa/seed-vc.git /opt/seed-vc",
                       "ln -s /models/seed /opt/seed-vc/checkpoints",
                       "git clone --depth 1 https://github.com/IAHispano/Applio.git /opt/applio")
         # Audio dependencies pull an old protobuf incompatible with Modal's runner.
         .pip_install("protobuf>=5.29,<6")
         .env({"HF_HOME": "/models/huggingface", "TORCH_HOME": "/models/torch"}))

GPU = dict(image=image, gpu=["L4", "A10G"], cpu=8, memory=16384, timeout=1800,
           max_containers=1, scaledown_window=60, retries=0,
           volumes={"/models": models})

# Cover conversion can be kept warm for an active render session without
# pinning every GPU function. Default 0 so idle time is never billed.
CONVERT_GPU = {**GPU, "min_containers": int(os.environ.get("KLIPIN_VOICE_MIN_CONTAINERS", "0"))}


def _run(args, timeout=1200, cwd=None):
    completed = subprocess.run(args, capture_output=True, text=True,
                               timeout=timeout, cwd=cwd)
    if completed.returncode:
        raise RuntimeError(completed.stderr[-1800:] or "Voice worker failed")
    return completed


def _ensure_rvc_models():
    """Ensure RVC v2 base models (ContentVec + RMVPE) and voice checkpoints exist in volume."""
    base_dir = Path("/models/rvc")
    embedder_dir = base_dir / "models" / "embedders" / "contentvec"
    predictor_dir = base_dir / "models" / "predictors"
    voices_dir = base_dir / "models" / "checkpoints"

    embedder_dir.mkdir(parents=True, exist_ok=True)
    predictor_dir.mkdir(parents=True, exist_ok=True)
    voices_dir.mkdir(parents=True, exist_ok=True)

    applio_models = Path("/opt/applio/rvc/models")
    if applio_models.exists() and not applio_models.is_symlink():
        shutil.rmtree(applio_models, ignore_errors=True)
    if not applio_models.exists():
        applio_models.parent.mkdir(parents=True, exist_ok=True)
        try:
            applio_models.symlink_to(base_dir / "models", target_is_directory=True)
        except Exception:
            pass

    files_to_download = [
        (embedder_dir / "config.json", "https://huggingface.co/IAHispano/Applio/resolve/main/Resources/embedders/contentvec/config.json"),
        (embedder_dir / "pytorch_model.bin", "https://huggingface.co/IAHispano/Applio/resolve/main/Resources/embedders/contentvec/pytorch_model.bin"),
        (predictor_dir / "rmvpe.pt", "https://huggingface.co/IAHispano/Applio/resolve/main/Resources/predictors/rmvpe.pt"),
        (voices_dir / "female_iu.pth", "https://huggingface.co/Zonas/RVC2-IU-1K/resolve/main/IU-Zonas.pth"),
        (voices_dir / "female_iu.index", "https://huggingface.co/Zonas/RVC2-IU-1K/resolve/main/added_IVF1231_Flat_nprobe_1_IU-Zonas_v2.index"),
    ]

    for local_path, url in files_to_download:
        if not local_path.is_file() or local_path.stat().st_size < 1000:
            print(f"[RVC] Downloading {local_path.name}...")
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req) as resp, open(local_path, "wb") as f:
                f.write(resp.read())
            print(f"[RVC] Downloaded {local_path.name} ({local_path.stat().st_size} bytes)")

    models.commit()


def _rvc_convert(
    input_wav: Path,
    output_wav: Path,
    persona: str = "female",
    pitch_shift: int = 0,
    f0_method: str = "rmvpe",
    index_rate: float = 0.75,
    protect: float = 0.33,
) -> str:
    """Run RVC v2 inference with Applio + RMVPE."""
    _ensure_rvc_models()

    voices_dir = Path("/models/rvc/models/checkpoints")
    persona_key = str(persona or "female").strip().lower()

    if "male" in persona_key and "fe" not in persona_key:
        pth = voices_dir / "male_jungkook.pth"
        idx = voices_dir / "male_jungkook.index"
    else:
        pth = voices_dir / "female_iu.pth"
        idx = voices_dir / "female_iu.index"

    if not pth.is_file():
        raise RuntimeError(f"RVC model {pth.name} belum tersedia.")

    code = f'''
import sys
sys.path.insert(0, "/opt/applio")

from rvc.infer.infer import VoiceConverter
vc = VoiceConverter()
vc.convert_audio(
    audio_input_path={repr(str(input_wav))},
    audio_output_path={repr(str(output_wav))},
    model_path={repr(str(pth))},
    index_path={repr(str(idx) if idx.is_file() else "")},
    pitch={int(pitch_shift)},
    f0_method={repr(f0_method)},
    index_rate={float(index_rate)},
    protect={float(protect)},
    export_format="WAV",
    embedder_model="contentvec",
)
'''
    res = subprocess.run([sys.executable, "-c", code], cwd="/opt/applio", capture_output=True, text=True, timeout=1200)
    if res.returncode != 0 or not output_wav.is_file() or output_wav.stat().st_size < 1024:
        raise RuntimeError(f"RVC conversion failed: {res.stderr[-1000:] or res.stdout[-1000:]}")
    return "rvc-v2-rmvpe"


def _separate(source: Path, work: Path) -> tuple[Path, Path]:
    _run([sys.executable, "-m", "demucs", "-n", "htdemucs", "--two-stems=vocals",
          "--shifts", "0", "-d", "cuda", "-o", str(work / "stems"), str(source)])
    vocals = work / "stems" / "htdemucs" / source.stem / "vocals.wav"
    backing = vocals.with_name("no_vocals.wav")
    if not vocals.is_file() or not backing.is_file():
        raise RuntimeError("Stem vokal belum berhasil dibuat.")
    return vocals, backing


def _separate_4stems(source: Path, work: Path) -> dict[str, Path]:
    """Run full 4-stem Demucs htdemucs (vocals, drums, bass, other) on L4 GPU."""
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
    """Explicit setup: download and load Seed-VC, Demucs, and RVC v2 before enabling the UI."""
    Path("/models/seed").mkdir(parents=True, exist_ok=True)
    _run([sys.executable, "-c",
          "from demucs.pretrained import get_model; get_model('htdemucs'); "
          "import inference; from argparse import Namespace; "
          "inference.load_models(Namespace(f0_condition=True,fp16=True,checkpoint=None,config=None))"],
         timeout=1700, cwd="/opt/seed-vc")
    try:
        _ensure_rvc_models()
    except Exception as exc:
        print(f"[Warmup] Warning: RVC warmup skipped ({exc})")
    Path("/models/ready-v1").write_text("demucs + seed-vc + rvc v2 loaded", encoding="utf-8")
    models.commit()
    return {"ready": True}


@app.function(image=image, volumes={"/models": models}, timeout=30)
def readiness() -> dict:
    models.reload()
    return {"ready": Path("/models/ready-v1").is_file(), "version": "singing-persona-v2-rvc"}


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
def convert_stems(audio_b64: str, reference_b64: str = "", duration: float = 180.0,
                  semi_tone_shift: int = 0, auto_f0: bool = False,
                  voice_engine: str = "rvc", voice_persona: str = "female") -> dict:
    """Separate 4 stems with Demucs + convert vocals with RVC v2 (RMVPE) or Seed-VC on L4 GPU."""
    started = time.monotonic()
    use_rvc = str(voice_engine or "rvc").lower() == "rvc" or not reference_b64
    method_name = "rvc-v2-rmvpe" if use_rvc else "seed-vc-4stems"

    with tempfile.TemporaryDirectory(prefix="persona-stems-") as folder:
        work = Path(folder)
        source = work / "source.mp3"
        source.write_bytes(base64.b64decode(audio_b64, validate=True))
        stems4 = _separate_4stems(source, work)
        vocals = stems4["vocals"]
        backing = stems4["backing"]
        converted_wav = work / "converted.wav"

        if use_rvc:
            try:
                _rvc_convert(vocals, converted_wav, persona=voice_persona, pitch_shift=semi_tone_shift)
            except Exception as rvc_exc:
                if not reference_b64:
                    raise RuntimeError(f"RVC conversion error: {rvc_exc}") from rvc_exc
                print(f"[RVC] Warning: RVC inference failed ({rvc_exc}), falling back to Seed-VC")
                use_rvc = False

        if not use_rvc:
            if not reference_b64:
                raise RuntimeError("Seed-VC conversion requires reference audio.")
            reference = work / "reference.wav"
            reference.write_bytes(base64.b64decode(reference_b64, validate=True))
            output_dir = work / "converted"
            _run([sys.executable, "/opt/seed-vc/inference.py", "--source", str(vocals),
                  "--target", str(reference), "--output", str(output_dir),
                  "--diffusion-steps", "30", "--f0-condition", "True",
                  "--auto-f0-adjust", "True" if auto_f0 else "False",
                  "--semi-tone-shift", str(int(semi_tone_shift))],
                 timeout=1500, cwd="/opt/seed-vc")
            flist = list(output_dir.glob("*.wav"))
            if not flist:
                raise RuntimeError("Hasil konversi Seed-VC tidak ditemukan.")
            shutil.copyfile(flist[0], converted_wav)
            method_name = "seed-vc-4stems"

        cap = str(max(3, min(float(duration), 600)))
        vout, bout = work / "vocals.mp3", work / "backing.mp3"
        dout, bass_out, oout = work / "drums.mp3", work / "bass.mp3", work / "other.mp3"
        for src, dst in ((converted_wav, vout), (backing, bout),
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
            "method": method_name,
        }


@app.function(**CONVERT_GPU)
def convert_vocal_variants(vocal_b64: str, variants: list, voice_persona: str = "female") -> dict:
    """Run RVC v2 on an already-isolated vocal stem with several parameter sets.

    Skips Demucs so multiple variants (pitch/protect/index_rate) can be rendered
    cheaply; the caller picks the clearest variant per lyric line (Whisper QA).
    Each variant: {"name", "pitch", "protect", "index_rate", "f0_method"}.
    """
    started = time.monotonic()
    out = {}
    with tempfile.TemporaryDirectory(prefix="persona-variants-") as folder:
        work = Path(folder)
        src = work / "vocal_in.mp3"
        src.write_bytes(base64.b64decode(vocal_b64, validate=True))
        wav_in = work / "vocal_in.wav"
        _run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-ar", "44100", "-ac", "1", str(wav_in)])
        for i, v in enumerate(variants or []):
            name = str(v.get("name") or f"v{i}")
            wav_out = work / f"out_{i}.wav"
            mp3_out = work / f"out_{i}.mp3"
            _rvc_convert(wav_in, wav_out, persona=voice_persona,
                         pitch_shift=int(v.get("pitch", 0)),
                         f0_method=str(v.get("f0_method", "rmvpe")),
                         index_rate=float(v.get("index_rate", 0.75)),
                         protect=float(v.get("protect", 0.33)))
            _run(["ffmpeg", "-y", "-v", "error", "-i", str(wav_out),
                  "-c:a", "libmp3lame", "-b:a", "192k", str(mp3_out)])
            out[name] = base64.b64encode(mp3_out.read_bytes()).decode("ascii")
        models.commit()
    return {"variants": out, "gpu_sec": time.monotonic() - started, "method": "rvc-v2-rmvpe-variants"}


@app.function(**CONVERT_GPU)
def convert_song(audio_b64: str, reference_b64: str = "", duration: float = 180.0,
                 semi_tone_shift: int = 0, auto_f0: bool = False,
                 voice_engine: str = "rvc", voice_persona: str = "female") -> dict:
    """Preserve the generated melody and backing; replace the vocal identity via RVC v2 or Seed-VC."""
    started = time.monotonic()
    use_rvc = str(voice_engine or "rvc").lower() == "rvc" or not reference_b64
    method_name = "rvc-v2-rmvpe" if use_rvc else "seed-vc-vocal-stem"

    with tempfile.TemporaryDirectory(prefix="persona-convert-") as folder:
        work = Path(folder)
        source = work / "source.mp3"
        source.write_bytes(base64.b64decode(audio_b64, validate=True))
        vocals, backing = _separate(source, work)
        converted_wav = work / "converted.wav"

        if use_rvc:
            try:
                _rvc_convert(vocals, converted_wav, persona=voice_persona, pitch_shift=semi_tone_shift)
            except Exception as rvc_exc:
                if not reference_b64:
                    raise RuntimeError(f"RVC conversion error: {rvc_exc}") from rvc_exc
                print(f"[RVC] Warning: RVC inference failed ({rvc_exc}), falling back to Seed-VC")
                use_rvc = False

        if not use_rvc:
            if not reference_b64:
                raise RuntimeError("Seed-VC conversion requires reference audio.")
            reference = work / "reference.wav"
            reference.write_bytes(base64.b64decode(reference_b64, validate=True))
            output_dir = work / "converted"
            _run([sys.executable, "/opt/seed-vc/inference.py", "--source", str(vocals),
                  "--target", str(reference), "--output", str(output_dir),
                  "--diffusion-steps", "30", "--f0-condition", "True",
                  "--auto-f0-adjust", "True" if auto_f0 else "False",
                  "--semi-tone-shift", str(int(semi_tone_shift))],
                 timeout=1500, cwd="/opt/seed-vc")
            flist = list(output_dir.glob("*.wav"))
            if not flist:
                raise RuntimeError("Hasil konversi Seed-VC tidak ditemukan.")
            shutil.copyfile(flist[0], converted_wav)
            method_name = "seed-vc-vocal-stem"

        target = work / "song.mp3"
        _run(["ffmpeg", "-y", "-v", "error", "-i", str(backing), "-i", str(converted_wav),
              "-filter_complex", "[0:a][1:a]amix=inputs=2:duration=longest:normalize=0,alimiter=limit=0.96[out]",
              "-map", "[out]", "-t", str(max(3, min(float(duration), 600))),
              "-c:a", "libmp3lame", "-b:a", "192k", str(target)])
        models.commit()
        return {"audio_b64": base64.b64encode(target.read_bytes()).decode("ascii"),
                "gpu_sec": time.monotonic() - started, "method": method_name}
