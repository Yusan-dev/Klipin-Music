"""Dedicated HeartMuLa GPU worker for Klipin Music.

Deploy separately from the video worker:
  modal deploy music_modal_app.py

The checkpoint lives in its own persistent Volume and is never copied into the
video image. This keeps Klipin's portrait/render deployment unchanged.
"""
from __future__ import annotations

import base64
import gc
import os
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

import modal

APP_NAME = "klipin-music"
MODEL_VOLUME = "klipin-music-models"
MODEL_ROOT = Path("/models/heartmula")
MAX_CREATE_DURATION_SEC = 300  # Mendukung lagu penuh hingga 5 menit (rentang 4-5 menit)
_HEARTMULA_PIPE = None

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


def _release_mula_vram(mula) -> None:
    """Delete KV caches & causal masks from GPU and move HeartMuLa weights to CPU RAM."""
    if mula is None:
        return
    for sub in (getattr(mula, "backbone", None), getattr(mula, "decoder", None)):
        if sub is None:
            continue
        for layer in getattr(sub, "layers", []):
            attn = getattr(layer, "attn", None)
            if attn is not None and hasattr(attn, "kv_cache"):
                attn.kv_cache = None
    if hasattr(mula, "backbone_causal_mask"):
        mula.backbone_causal_mask = None
    if hasattr(mula, "decoder_causal_mask"):
        mula.decoder_causal_mask = None
    mula.to("cpu")


def _get_heartmula_pipe():
    """Keep HeartMuLa-oss-3B + HeartCodec warm in CPU RAM and stage onto GPU VRAM per phase.

    Why staged GPU execution is mandatory on 24 GB GPUs (A10G / L4 = 22.06 GiB usable):
      - HeartMuLa-oss-3B weights (~7.2 GiB) + KV cache + torch.compile buffers (~8 GiB)
        + HeartCodec-oss FP32 weights & flow-matching activations (~7.5 GiB) = ~22.7 GiB,
        which triggers CUDA OutOfMemoryError during HeartCodec.detokenize() at 100% progress.
      - By keeping both models warm in the container's 32 GB CPU RAM and moving ONLY
        HeartMuLa to CUDA during Stage 1 (_forward) and ONLY HeartCodec to CUDA during
        Stage 2 (postprocess), peak GPU VRAM stays under ~11 GiB (leaving >10 GiB free)
        while avoiding slow disk reloads across warm container calls.
    """
    global _HEARTMULA_PIPE
    if _HEARTMULA_PIPE is not None:
        return _HEARTMULA_PIPE
    import torch
    from heartlib import HeartMuLaGenPipeline
    from heartlib.heartmula.modeling_heartmula import HeartMuLa
    from heartlib.heartcodec.modeling_heartcodec import HeartCodec

    # Initialize pipeline metadata & tokenizer with lazy_load=True so __init__
    # does not load both models onto CUDA simultaneously or run torch.compile.
    pipe = HeartMuLaGenPipeline.from_pretrained(
        str(MODEL_ROOT),
        device={
            "mula": torch.device("cuda"),
            "codec": torch.device("cuda"),
        },
        dtype={
            "mula": torch.bfloat16,
            "codec": torch.float32,
        },
        version="3B",
        lazy_load=True,
    )
    pipe._mula = HeartMuLa.from_pretrained(
        pipe.mula_path,
        dtype=pipe.mula_dtype,
    )
    # Cap backbone max_seq_len to 4096 tokens (~320s of audio instead of 8192)
    # to cut attention KV cache & causal mask VRAM by 50%.
    if hasattr(pipe._mula, "backbone") and hasattr(pipe._mula.backbone, "max_seq_len"):
        pipe._mula.backbone.max_seq_len = 4096
    pipe._mula.eval()

    pipe._codec = HeartCodec.from_pretrained(
        pipe.codec_path,
        dtype=pipe.codec_dtype,
    )
    pipe._codec.eval()

    # Disable upstream _unload() so models stay resident in RAM instead of being deleted.
    pipe.lazy_load = False
    _HEARTMULA_PIPE = pipe
    return _HEARTMULA_PIPE


@app.function(
    image=image,
    gpu=["A10G", "L4", "A100"],
    cpu=8,
    memory=32768,
    timeout=720,
    max_containers=10,
    min_containers=0,
    scaledown_window=60,
    volumes={"/models": models},
)
def generate_song(lyrics: str, tags: str, duration_sec: int = 120,
                  seed: int | None = None,
                  lora_id: str | None = None,
                  cfg_scale: float | None = None,
                  temperature: float | None = None,
                  topk: int | None = None) -> dict:
    """Generate a full vocal song on Modal GPU and return the MP3 as base64 metadata."""
    _ensure_models()
    clamped_duration = max(15, min(int(duration_sec or 120), MAX_CREATE_DURATION_SEC))
    eff_cfg = max(1.0, min(6.0, float(cfg_scale))) if cfg_scale is not None else 1.5
    eff_temp = max(0.1, min(1.5, float(temperature))) if temperature is not None else 1.0
    eff_topk = max(10, min(1000, int(topk))) if topk is not None else 50

    work = Path(tempfile.mkdtemp(prefix="klipin-heartmula-"))
    lyrics_path = work / "lyrics.txt"
    tags_path = work / "tags.txt"
    output_path = work / "song.mp3"
    lyrics_path.write_text(lyrics, encoding="utf-8")
    tags_path.write_text(tags, encoding="utf-8")

    stdout_tail = ""
    pipe = None
    try:
        import random
        import torch

        if seed is not None:
            s_val = int(seed)
            random.seed(s_val)
            torch.manual_seed(s_val % (2**63))
            if torch.cuda.is_available():
                torch.cuda.manual_seed_all(s_val % (2**63))

        pipe = _get_heartmula_pipe()
        preprocess_kwargs, forward_kwargs, postprocess_kwargs = pipe._sanitize_parameters(
            max_audio_length_ms=clamped_duration * 1000,
            save_path=str(output_path),
            topk=eff_topk,
            temperature=eff_temp,
            cfg_scale=eff_cfg,
        )
        model_inputs = pipe.preprocess(
            {
                "lyrics": str(lyrics_path),
                "tags": str(tags_path),
            },
            **preprocess_kwargs,
        )

        # Stage 1: Autoregressive token generation (ONLY HeartMuLa on CUDA)
        if pipe._codec is not None:
            pipe._codec.to("cpu")
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

        pipe._mula.to(pipe.mula_device)
        with torch.no_grad():
            model_outputs = pipe._forward(model_inputs, **forward_kwargs)
        frames_cpu = model_outputs["frames"].detach().cpu()
        del model_outputs
        _release_mula_vram(pipe._mula)
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

        # Stage 2: High-fidelity 48kHz stereo waveform decoding (ONLY HeartCodec on CUDA)
        pipe._codec.to(pipe.codec_device)
        with torch.no_grad():
            pipe.postprocess({"frames": frames_cpu}, **postprocess_kwargs)
        del frames_cpu
        pipe._codec.to("cpu")
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

        stdout_tail = f"Staged in-RAM HeartMuLaGenPipeline completed ({clamped_duration}s) -> {output_path}"
    except Exception as inproc_exc:
        # Release ALL GPU references from Process 1 before running fallback subprocess CLI
        global _HEARTMULA_PIPE
        try:
            if pipe is not None:
                _release_mula_vram(getattr(pipe, "_mula", None))
                if getattr(pipe, "_codec", None) is not None:
                    pipe._codec.to("cpu")
        except Exception:
            pass
        pipe = None
        _HEARTMULA_PIPE = None
        gc.collect()
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                torch.cuda.ipc_collect()
        except Exception:
            pass
        # Always use --lazy_load=true in fallback CLI so 24 GB GPUs never OOM
        runner_script = Path("/opt/heartlib/examples/run_music_generation.py")
        runner_src = runner_script.read_text(encoding="utf-8", errors="ignore") if runner_script.exists() else ""
        cmd = [
            sys.executable, str(runner_script),
            f"--model_path={MODEL_ROOT}", "--version=3B",
            f"--lyrics={lyrics_path}", f"--tags={tags_path}",
            f"--save_path={output_path}",
            f"--max_audio_length_ms={clamped_duration * 1000}",
            "--lazy_load=true",
        ]
        if "--cfg_scale" in runner_src:
            cmd.append(f"--cfg_scale={eff_cfg:.2f}")
        if "--temperature" in runner_src:
            cmd.append(f"--temperature={eff_temp:.2f}")
        if "--topk" in runner_src:
            cmd.append(f"--topk={eff_topk}")
        env = dict(os.environ)
        env["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"
        if seed is not None:
            env["PYTHONHASHSEED"] = str(int(seed))
        if lora_id:
            env["KLIPIN_MUSIC_LORA_ID"] = str(lora_id)
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
            capture_output=True, text=True, timeout=660, env=env)
        if result.returncode != 0 or not output_path.exists():
            raise RuntimeError(
                f"In-proc error: {inproc_exc}\nCLI stderr: {result.stderr[-2500:]}"
            ) from inproc_exc
        stdout_tail = result.stdout[-1000:]

    if not output_path.exists() or output_path.stat().st_size < 1024:
        raise RuntimeError("HeartMuLa produced no valid audio file")

    return {
        "provider": "heartmula",
        "model": "HeartMuLa-oss-3B-happy-new-year",
        "duration_sec": clamped_duration,
        "seed": seed,
        "lora_id": lora_id or "",
        "cfg_scale": eff_cfg,
        "temperature": eff_temp,
        "topk": eff_topk,
        "audio_b64": base64.b64encode(output_path.read_bytes()).decode("ascii"),
        "stdout_tail": stdout_tail,
    }


@app.function(image=image, gpu=["A10G", "L4", "A100"], timeout=180)
def health() -> dict:
    import torch
    return {"provider": "heartmula", "cuda": bool(torch.cuda.is_available()),
            "device": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "none"}
