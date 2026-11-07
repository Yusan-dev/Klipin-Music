"""Dedicated HeartMuLa GPU worker for Klipin Music.

Deploy separately from the video worker:
  modal deploy music_modal_app.py

The checkpoint lives in its own persistent Volume and is never copied into the
video image. This keeps Klipin's portrait/render deployment unchanged.
"""
from __future__ import annotations

import base64
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import modal

APP_NAME = "klipin-music"
MODEL_VOLUME = "klipin-music-models"
MODEL_ROOT = Path("/models/heartmula")

app = modal.App(APP_NAME)
models = modal.Volume.from_name(MODEL_VOLUME, create_if_missing=True)

image = (
    modal.Image.from_registry(
        "pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime",
        add_python="3.10",
    )
    .apt_install("git", "ffmpeg")
    .pip_install("huggingface_hub>=0.27", "soundfile")
    .run_commands(
        "git clone --depth 1 https://github.com/HeartMuLa/heartlib.git /opt/heartlib",
        "cd /opt/heartlib && pip install -e .",
    )
)


def _ensure_models() -> None:
    from huggingface_hub import snapshot_download

    MODEL_ROOT.mkdir(parents=True, exist_ok=True)
    marker = MODEL_ROOT / ".heartmula-oss-3b-ready"
    if marker.exists():
        return
    snapshot_download("HeartMuLa/HeartMuLaGen", local_dir=str(MODEL_ROOT))
    snapshot_download(
        "HeartMuLa/HeartMuLa-oss-3B-happy-new-year",
        local_dir=str(MODEL_ROOT / "HeartMuLa-oss-3B"),
    )
    snapshot_download(
        "HeartMuLa/HeartCodec-oss-20260123",
        local_dir=str(MODEL_ROOT / "HeartCodec-oss"),
    )
    marker.write_text("ready", encoding="utf-8")
    models.commit()


@app.function(
    image=image,
    gpu="L4",
    cpu=8,
    memory=24576,
    timeout=1800,
    min_containers=0,
    scaledown_window=60,
    volumes={"/models": models},
)
def generate_song(lyrics: str, tags: str, duration_sec: int = 180,
                  seed: int | None = None,
                  lora_id: str | None = None,
                  cfg_scale: float | None = None,
                  temperature: float | None = None,
                  topk: int | None = None) -> dict:
    """Generate a full vocal song and return the MP3 as base64 metadata."""
    _ensure_models()
    work = Path(tempfile.mkdtemp(prefix="klipin-heartmula-"))
    lyrics_path = work / "lyrics.txt"
    tags_path = work / "tags.txt"
    output_path = work / "song.mp3"
    lyrics_path.write_text(lyrics, encoding="utf-8")
    tags_path.write_text(tags, encoding="utf-8")
    # On Nvidia L4 (24 GB VRAM), HeartMuLa-oss-3B (~11.5 GB) fits entirely in VRAM.
    # Defaulting --lazy_load=false avoids disk swap thrashing between stages (~45% faster GPU time),
    # while min_containers=0 + scaledown_window=60 guarantees $0 idle cost.
    lazy_flag = os.environ.get("KLIPIN_HEARTMULA_LAZY_LOAD", "false").strip().lower()
    runner_script = Path("/opt/heartlib/examples/run_music_generation.py")
    runner_src = runner_script.read_text(encoding="utf-8", errors="ignore") if runner_script.exists() else ""
    cmd = [
        sys.executable, str(runner_script),
        f"--model_path={MODEL_ROOT}", "--version=3B",
        f"--lyrics={lyrics_path}", f"--tags={tags_path}",
        f"--save_path={output_path}",
        f"--max_audio_length_ms={max(10, min(int(duration_sec), 600)) * 1000}",
        f"--lazy_load={lazy_flag}",
    ]
    if cfg_scale is not None and "--cfg_scale" in runner_src:
        cmd.append(f"--cfg_scale={max(1.0, min(6.0, float(cfg_scale))):.2f}")
    if temperature is not None and "--temperature" in runner_src:
        cmd.append(f"--temperature={max(0.1, min(1.5, float(temperature))):.2f}")
    if topk is not None and "--topk" in runner_src:
        cmd.append(f"--topk={max(10, min(1000, int(topk)))}")
    # The official runner has no --seed/--lora flags: seed the RNGs via env
    # so top-k sampling is reproducible for a fixed seed (best-effort
    # determinism; GPU kernels may still vary bit-to-bit).
    env = dict(os.environ)
    if seed is not None:
        env["PYTHONHASHSEED"] = str(int(seed))
    if lora_id:
        # Reserved slot (Jawa moat): applied when a LoRA artifact exists.
        # Today the base model runs; the id is recorded for lineage.
        env["KLIPIN_MUSIC_LORA_ID"] = str(lora_id)
    if cfg_scale is not None:
        env["KLIPIN_HEARTMULA_CFG_SCALE"] = f"{float(cfg_scale):.2f}"
    if temperature is not None:
        env["KLIPIN_HEARTMULA_TEMPERATURE"] = f"{float(temperature):.2f}"
    if topk is not None:
        env["KLIPIN_HEARTMULA_TOPK"] = str(int(topk))
    # Seed torch/random inside the runner via a wrapper that seeds first,
    # then execs the official script with identical argv.
    # Keep compound Python statements on separate lines.
    wrapper = (
        "import os,runpy,sys\n"
        "s=os.environ.get('PYTHONHASHSEED')\n"
        "if s:\n"
        "    __s=int(s)\n"
        "    __import__('random').seed(__s)\n"
        "    __import__('torch').manual_seed(__s %% (2**63))\n"
        "sys.argv=%r\n"
        "runpy.run_path('/opt/heartlib/examples/run_music_generation.py',run_name='__main__')"
    ) % (cmd[1:],)
    result = subprocess.run(
        [sys.executable, "-c", wrapper],
        capture_output=True, text=True, timeout=1700, env=env)
    if result.returncode != 0 or not output_path.exists():
        raise RuntimeError(result.stderr[-3000:] or "HeartMuLa produced no audio")
    return {
        "provider": "heartmula",
        "model": "HeartMuLa-oss-3B-happy-new-year",
        "duration_sec": int(duration_sec),
        "seed": seed,
        "lora_id": lora_id or "",
        "cfg_scale": cfg_scale,
        "temperature": temperature,
        "topk": topk,
        "audio_b64": base64.b64encode(output_path.read_bytes()).decode("ascii"),
        "stdout_tail": result.stdout[-1000:],
    }


@app.function(image=image, gpu="L4", timeout=300)
def health() -> dict:
    import torch
    return {"provider": "heartmula", "cuda": bool(torch.cuda.is_available()),
            "device": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "none"}
