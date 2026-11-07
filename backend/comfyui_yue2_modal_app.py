"""Dedicated ComfyUI Serverless Worker for YuE2 Cover (SheetSage2 + YuE2).

Executes the official Comfy-Org YuE2 Music Cover workflow
(LoadAudio -> SheetSage2AudioToABC -> YuE2GenerateMusic -> KSampler -> VAEDecodeAudio).

Deploy to Modal Cloud GPU (workspace yusan-dev):
    modal deploy backend/comfyui_yue2_modal_app.py
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen

import modal

APP_NAME = "klipin-comfyui-yue2"
MODEL_VOLUME = "klipin-yue2-models"
COMFY_DIR = Path("/opt/ComfyUI")
MODELS_ROOT = Path("/models/yue2")

app = modal.App(APP_NAME)
models = modal.Volume.from_name(MODEL_VOLUME, create_if_missing=True)

image = (
    modal.Image.from_registry(
        "pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime",
        add_python="3.11",
    )
    .apt_install("git", "ffmpeg", "curl", "libsndfile1", "build-essential", "gcc", "g++")
    .pip_install(
        "huggingface_hub>=0.27",
        "requests>=2.31.0",
        "soundfile>=0.12.1",
        "websockets>=12.0",
        "safetensors",
    )
    .run_commands(
        "git clone --depth 1 https://github.com/comfyanonymous/ComfyUI.git /opt/ComfyUI",
        "cd /opt/ComfyUI && pip install -r requirements.txt",
        "python -c \"import glob; [open(p, 'w').write('import typing\\n' + open(p).read().replace('list[int]', 'typing.List[int]')) for p in glob.glob('/opt/conda/**/comfy_kitchen/**/*.py', recursive=True) if 'list[int]' in open(p).read()]\"",
        "python -c \"import glob; [open(p, 'w').write('import typing\\n' + open(p).read().replace('list[int]', 'typing.List[int]')) for p in glob.glob('/usr/local/**/comfy_kitchen/**/*.py', recursive=True) if 'list[int]' in open(p).read()]\"",
        "python /opt/ComfyUI/main.py --help",
    )
)


def _ensure_models() -> None:
    """Download YuE2 and SheetSage2 checkpoints to persistent Volume if missing."""
    from huggingface_hub import hf_hub_download

    MODELS_ROOT.mkdir(parents=True, exist_ok=True)
    ckpt_dir = MODELS_ROOT / "checkpoints"
    enc_dir = MODELS_ROOT / "audio_encoders"
    ckpt_dir.mkdir(parents=True, exist_ok=True)
    enc_dir.mkdir(parents=True, exist_ok=True)

    yue2_ckpt = ckpt_dir / "yue2_3b_int8_convrot.safetensors"
    sheetsage_enc = enc_dir / "sheetsage2_bf16.safetensors"

    if not yue2_ckpt.exists():
        print(f"Downloading {yue2_ckpt.name} from HuggingFace Comfy-Org/YuE2...")
        dl_path = hf_hub_download(
            repo_id="Comfy-Org/YuE2",
            filename="checkpoints/yue2_3b_int8_convrot.safetensors",
            local_dir=str(MODELS_ROOT),
        )
        print(f"Downloaded YuE2 to: {dl_path}")

    if not sheetsage_enc.exists():
        print(f"Downloading {sheetsage_enc.name} from HuggingFace Comfy-Org/YuE2...")
        dl_path = hf_hub_download(
            repo_id="Comfy-Org/YuE2",
            filename="audio_encoders/sheetsage2_bf16.safetensors",
            local_dir=str(MODELS_ROOT),
        )
        print(f"Downloaded SheetSage2 to: {dl_path}")

    models.commit()

    # Link into ComfyUI models tree
    comfy_ckpts = COMFY_DIR / "models" / "checkpoints"
    comfy_encs = COMFY_DIR / "models" / "audio_encoders"
    comfy_ckpts.mkdir(parents=True, exist_ok=True)
    comfy_encs.mkdir(parents=True, exist_ok=True)

    dst_ckpt = comfy_ckpts / "yue2_3b_int8_convrot.safetensors"
    dst_enc = comfy_encs / "sheetsage2_bf16.safetensors"

    if not dst_ckpt.exists():
        os.symlink(str(yue2_ckpt), str(dst_ckpt))
    if not dst_enc.exists():
        os.symlink(str(sheetsage_enc), str(dst_enc))


_COMFY_PROC: subprocess.Popen | None = None


def _ensure_comfyui_server() -> subprocess.Popen:
    """Ensure headless ComfyUI background process is running and warm inside the container."""
    global _COMFY_PROC
    if _COMFY_PROC is not None and _COMFY_PROC.poll() is None:
        try:
            req = Request("http://127.0.0.1:8188/system_stats", headers={"User-Agent": "Klipin/1.0"})
            with urlopen(req, timeout=2.0) as resp:
                if resp.status == 200:
                    print("Reusing warm ComfyUI server in container (0s startup)!")
                    return _COMFY_PROC
        except Exception:
            pass
        try:
            _COMFY_PROC.terminate()
        except Exception:
            pass
        _COMFY_PROC = None

    log_path = Path("/tmp/comfyui.log")
    log_fp = log_path.open("w", encoding="utf-8", buffering=1)
    cmd = [
        sys.executable,
        str(COMFY_DIR / "main.py"),
        "--listen", "0.0.0.0",
        "--port", "8188",
        "--disable-auto-launch",
        "--preview-method", "none",
    ]
    proc = subprocess.Popen(cmd, stdout=log_fp, stderr=subprocess.STDOUT)

    # Wait for server to be responsive
    for sec in range(120):
        if proc.poll() is not None:
            log_content = log_path.read_text(encoding="utf-8", errors="replace")[-2000:]
            raise RuntimeError(f"ComfyUI process exited prematurely with code {proc.returncode}:\n{log_content}")
        try:
            req = Request("http://127.0.0.1:8188/system_stats", headers={"User-Agent": "Klipin/1.0"})
            with urlopen(req, timeout=1.0) as resp:
                if resp.status == 200:
                    print(f"ComfyUI server started successfully in {sec+1}s!")
                    _COMFY_PROC = proc
                    return proc
        except Exception:
            time.sleep(1.0)

    proc.terminate()
    log_content = log_path.read_text(encoding="utf-8", errors="replace")[-2000:]
    raise RuntimeError(f"ComfyUI server failed to respond within 120 seconds. Log:\n{log_content}")


@app.function(
    image=image,
    gpu="L4",
    cpu=8,
    memory=24576,
    timeout=2400,
    min_containers=0,
    scaledown_window=60,
    volumes={"/models": models},
)
def generate_yue2_cover(
    ref_audio_b64: str,
    lyrics: str,
    style: str,
    duration_sec: int = 180,
    seed: int = 42,
    cfg_scale: float | None = None,
    temperature: float | None = None,
    steps: int | None = None,
) -> dict:
    """Run full SheetSage2 + YuE2 ComfyUI cover workflow and return base64 audio."""
    _ensure_models()

    if not ref_audio_b64:
        raise ValueError("Audio referensi lagu asli diperlukan untuk cover YuE2.")

    raw_audio = base64.b64decode(ref_audio_b64)
    input_dir = COMFY_DIR / "input"
    output_dir = COMFY_DIR / "output"
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    ref_audio_filename = f"reference_{int(time.time())}.mp3"
    (input_dir / ref_audio_filename).write_bytes(raw_audio)

    _ensure_comfyui_server()
    eff_cfg = max(0.8, min(4.0, float(cfg_scale))) if cfg_scale is not None else 1.0
    eff_steps = max(16, min(64, int(steps))) if steps is not None else 32
    eff_denoise = max(0.6, min(1.0, float(temperature))) if temperature is not None else 1.0

    # Build ComfyUI API Prompt format
    prompt_api = {
        "10": {
            "inputs": {
                "filename_prefix": f"YuE2_cover_{int(time.time())}",
                "audio": ["9", 0],
            },
            "class_type": "SaveAudio",
        },
        "45": {
            "inputs": {
                "audio": ref_audio_filename,
            },
            "class_type": "LoadAudio",
        },
        "43": {
            "inputs": {
                "audio_encoder_name": "sheetsage2_bf16.safetensors",
            },
            "class_type": "AudioEncoderLoader",
        },
        "41": {
            "inputs": {
                "audio_encoder": ["43", 0],
                "audio": ["45", 0],
                "mode": "melody",
            },
            "class_type": "SheetSage2AudioToABC",
        },
        "15": {
            "inputs": {
                "ckpt_name": "yue2_3b_int8_convrot.safetensors",
            },
            "class_type": "CheckpointLoaderSimple",
        },
        "25": {
            "inputs": {
                "clip": ["15", 1],
                "style": style,
                "lyrics": lyrics,
                "abc": ["41", 0],
                "seed": seed,
                "max_duration": float(duration_sec),
            },
            "class_type": "YuE2GenerateMusic",
        },
        "18": {
            "inputs": {
                "conditioning": ["25", 0],
            },
            "class_type": "ConditioningZeroOut",
        },
        "5": {
            "inputs": {
                "seconds": ["25", 1],
            },
            "class_type": "EmptyYuE2LatentAudio",
        },
        "8": {
            "inputs": {
                "model": ["15", 0],
                "positive": ["25", 0],
                "negative": ["18", 0],
                "latent_image": ["5", 0],
                "seed": seed,
                "steps": eff_steps,
                "cfg": eff_cfg,
                "sampler_name": "dpm_2",
                "scheduler": "sgm_uniform",
                "denoise": eff_denoise,
            },
            "class_type": "KSampler",
        },
        "9": {
            "inputs": {
                "samples": ["8", 0],
                "vae": ["15", 2],
            },
            "class_type": "VAEDecodeAudio",
        },
    }

    # Queue prompt via ComfyUI REST API
    queue_payload = json.dumps({"prompt": prompt_api, "client_id": "modal_yue2"}).encode("utf-8")
    req = Request(
        "http://127.0.0.1:8188/prompt",
        data=queue_payload,
        headers={"Content-Type": "application/json", "User-Agent": "Klipin/1.0"},
        method="POST",
    )
    with urlopen(req, timeout=30.0) as resp:
        queue_res = json.loads(resp.read().decode("utf-8"))
        prompt_id = queue_res.get("prompt_id")

    if not prompt_id:
        raise RuntimeError(f"Gagal mengantrekan workflow ke ComfyUI: {queue_res}")

    # Poll history until complete
    completed = False
    rendered_filename = None
    for _ in range(600):  # Wait up to 20 minutes
        time.sleep(2.0)
        hist_req = Request(f"http://127.0.0.1:8188/history/{prompt_id}", headers={"User-Agent": "Klipin/1.0"})
        try:
            with urlopen(hist_req, timeout=5.0) as resp:
                history = json.loads(resp.read().decode("utf-8"))
                if prompt_id in history:
                    item = history[prompt_id]
                    status = item.get("status", {})
                    if status.get("completed", False):
                        completed = True
                        outputs = item.get("outputs", {})
                        for _, node_out in outputs.items():
                            if "audio" in node_out:
                                rendered_filename = node_out["audio"][0].get("filename")
                                break
                        break
                    if status.get("status_str") == "error":
                        raise RuntimeError(f"ComfyUI execution error: {status.get('messages')}")
        except Exception:
            continue

    if not completed:
        raise TimeoutError("ComfyUI workflow rendering timed out.")

    # Locate output file
    out_file = output_dir / (rendered_filename or "")
    if not out_file.exists():
        # Fallback scan newest file in output_dir
        files = list(output_dir.glob("*.flac")) + list(output_dir.glob("*.mp3")) + list(output_dir.glob("*.wav"))
        if files:
            out_file = max(files, key=lambda f: f.stat().st_mtime)

    if not out_file or not out_file.exists():
        raise FileNotFoundError("Audio output hasil render ComfyUI tidak ditemukan.")

    audio_bytes = out_file.read_bytes()
    return {
        "audio_b64": base64.b64encode(audio_bytes).decode("ascii"),
        "format": out_file.suffix.lstrip("."),
        "model": "m-a-p/YuE2-3B",
        "audio_encoder": "sheetsage2_bf16",
        "provider": "comfyui-yue2-modal",
        "duration": float(duration_sec),
        "cfg_scale": eff_cfg,
        "steps": eff_steps,
    }
