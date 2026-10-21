"""ACE-Step draft worker for Klipin Music (fast iteration, Suno-style 2-takes).

Deploy separately from the HeartMuLa final worker:
  python -m modal deploy ace_step_modal_app.py

Diffusion-based (DCAE + linear transformer): draft in ~1 minute on L4 with
reduced steps, vs minutes for the autoregressive final. Shares no
volume/image with ``music_modal_app.py`` so the portrait/render contract
stays untouched.

Inference path mirrors the official ``infer.py``: ``ACEStepPipeline`` called
with the same parameter order (audio_duration, prompt, lyrics, infer_step,
guidance_*, ..., save_path). Drafts use fewer steps for speed; full quality
remains HeartMuLa's job.
"""
from __future__ import annotations

import base64
import os
import tempfile
from pathlib import Path

import modal

APP_NAME = "klipin-ace-step"
MODEL_VOLUME = "klipin-ace-step-models"
MODEL_ROOT = Path("/models/ace-step")

app = modal.App(APP_NAME)
models = modal.Volume.from_name(MODEL_VOLUME, create_if_missing=True)

image = (
    modal.Image.from_registry(
        "pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime",
        add_python="3.10",
    )
    .apt_install("git", "ffmpeg")
    .pip_install("huggingface_hub>=0.27", "soundfile", "einops")
    .run_commands(
        "git clone --depth 1 https://github.com/ace-step/ACE-Step.git /opt/acestep",
        "cd /opt/acestep && pip install -r requirements.txt",
    )
)


def _ensure_models() -> None:
    from huggingface_hub import snapshot_download

    MODEL_ROOT.mkdir(parents=True, exist_ok=True)
    marker = MODEL_ROOT / ".ace-step-ready"
    if marker.exists():
        return
    snapshot_download("ACE-Step/ACE-Step-v1-3.5B", local_dir=str(MODEL_ROOT))
    marker.write_text("ready", encoding="utf-8")
    models.commit()


_PIPELINE = None


def _pipeline():
    """Singleton pipeline per container (model stays warm between takes)."""
    global _PIPELINE
    if _PIPELINE is None:
        import sys
        sys.path.insert(0, "/opt/acestep")
        from acestep.pipeline_ace_step import ACEStepPipeline
        _PIPELINE = ACEStepPipeline(
            checkpoint_dir=str(MODEL_ROOT),
            dtype="bfloat16",
            torch_compile=False,
        )
    return _PIPELINE


@app.function(
    image=image,
    gpu="L4",
    cpu=8,
    memory=24576,
    timeout=1200,
    scaledown_window=120,
    min_containers=int(os.environ.get("KLIPIN_ACE_MIN_CONTAINERS", "0")),
    volumes={"/models": models},
)
def generate_draft(prompt: str, lyrics: str, language: str = "id",
                   duration_sec: int = 120, seed: int | None = None,
                   lora_id: str | None = None,
                   ref_audio_b64: str | None = None,
                   ref_strength: float = 0.5) -> dict:
    """Fast draft take. Returns MP3 as base64. LoRA slot reserved (Jawa moat).

    ``ref_audio_b64`` enables cover mode (audio2audio): the new take follows
    the reference's musical identity. Falls back to plain text2music when the
    reference path fails — never a silent instrumental.
    """
    import base64 as _b64
    _ensure_models()
    pipe = _pipeline()
    if lora_id:
        try:
            pipe.load_lora(lora_id, 1.0)
        except Exception:
            pass
    work = Path(tempfile.mkdtemp(prefix="klipin-ace-"))
    wav_path = work / "draft.wav"
    output_path = work / "draft.mp3"
    duration = max(10, min(int(duration_sec), 600))
    seeds = [int(seed)] if seed is not None else [12345]
    steps = int(os.environ.get("KLIPIN_ACE_STEP_STEPS", "25"))
    ref_path = None
    if ref_audio_b64:
        try:
            ref_path = work / "ref.mp3"
            ref_path.write_bytes(_b64.b64decode(ref_audio_b64))
            if ref_path.stat().st_size < 1024:
                ref_path = None
        except Exception:
            ref_path = None
    # Keyword call — mirrors official infer.py parameter names.
    call_kwargs: dict = dict(
        audio_duration=float(duration),
        prompt=prompt or "",
        lyrics=lyrics or "",
        infer_step=steps,
        guidance_scale=15.0,
        scheduler_type="euler",
        cfg_type="apg",
        omega_scale=10.0,
        manual_seeds=seeds,
        guidance_interval=0.5,
        guidance_interval_decay=0.0,
        min_guidance_scale=3.0,
        use_erg_tag=True,
        use_erg_lyric=True,
        use_erg_diffusion=True,
        save_path=str(wav_path),
    )
    used_cover_ref = False
    if ref_path is not None:
        try:
            pipe(**{**call_kwargs,
                    "audio2audio_enable": True,
                    "ref_audio_input": str(ref_path),
                    "ref_audio_strength": max(0.1, min(0.9, float(ref_strength or 0.5)))})
            used_cover_ref = True
        except Exception:
            if wav_path.exists():
                wav_path.unlink()
    if not used_cover_ref:
        pipe(**call_kwargs)
    if not wav_path.exists():
        raise RuntimeError("ACE-Step produced no audio")
    # MP3 for the panel player (WAV kept only when loudnorm runs server-side).
    import subprocess
    r = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(wav_path),
         "-c:a", "libmp3lame", "-b:a", "192k", str(output_path)],
        capture_output=True, text=True, timeout=300)
    if r.returncode != 0 or not output_path.exists():
        raise RuntimeError(f"ACE-Step MP3 encode failed: {r.stderr[-500:]}")
    return {
        "provider": "ace-step-modal",
        "model": "ACE-Step-v1-3.5B",
        "duration_sec": duration,
        "seed": seed,
        "steps": steps,
        "used_cover_ref": used_cover_ref,
        "audio_b64": base64.b64encode(output_path.read_bytes()).decode("ascii"),
    }


@app.function(image=image, gpu="L4", timeout=300)
def health() -> dict:
    import torch
    return {"provider": "ace-step", "cuda": bool(torch.cuda.is_available()),
            "device": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "none"}
