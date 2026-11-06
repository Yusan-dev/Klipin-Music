"""GPU detection + NVENC encode helpers for Klipin.

LOCKED CONTRACT (do not "improve" — see docs/RENDER_PERFORMANCE_CONTRACT.md):
GPU encoding runs ONLY on the Modal L4 production worker, which sets
KLIPIN_GPU=1 explicitly. Local/laptop/VPS renders are ALWAYS CPU
(libx264), even when an NVIDIA card is present — auto-detection is
deliberately disabled. Any agent (human or AI) re-enabling local-GPU
auto-detect violates the production contract and must be reverted.

Explicit control via env vars (the ONLY switch):
    KLIPIN_GPU=1   allow NVENC (set solely by backend/modal_app.py)
    anything else (incl. unset) → force CPU (libx264)
"""
import os
import shutil
import subprocess

_NVENC_CACHE: bool | None = None


def nvidia_available() -> bool:
    """True when an NVIDIA GPU is visible (nvidia-smi present & succeeds)."""
    nvsmi = shutil.which("nvidia-smi")
    if not nvsmi:
        return False
    try:
        r = subprocess.run([nvsmi, "-L"], capture_output=True, text=True)
    except OSError:
        return False
    return r.returncode == 0 and bool(r.stdout.strip())


def nvenc_available() -> bool:
    """True when this ffmpeg build exposes the h264_nvenc encoder."""
    global _NVENC_CACHE
    if _NVENC_CACHE is not None:
        return _NVENC_CACHE
    try:
        r = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True, text=True,
        )
    except OSError:
        return False
    _NVENC_CACHE = r.returncode == 0 and "h264_nvenc" in r.stdout
    return _NVENC_CACHE


def use_gpu() -> bool:
    """Whether to encode with NVENC — True ONLY on explicit opt-in.

    LOCKED: no auto-detection. Only KLIPIN_GPU=1 (set solely by the Modal
    L4 worker) enables NVENC. Every other value — including unset and "0",
    even on machines with an NVIDIA card — means CPU. See module docstring.
    """
    override = os.environ.get("KLIPIN_GPU", "").strip().lower()
    if override in ("1", "true", "yes", "on"):
        return True
    return False


def compute_backend() -> str:
    """Where this render runs: "modal-l4" or "local-cpu".

    Single source of truth for the render-backend marker stamped into job
    results and logs, so nobody ever has to guess whether a job used the
    Modal GPU or fell back to local CPU. KLIPIN_GPU=1 is set solely by the
    Modal L4 worker (backend/modal_app.py).
    """
    return "modal-l4" if use_gpu() else "local-cpu"


def encode_args(crf: int = 18, preset: str = "fast", log=None) -> list:
    """Return the `-c:v …` encode block for the current hardware.

    CPU:   libx264 -preset {preset} -crf {crf}
    GPU:   h264_nvenc -preset p4 -cq {crf}   (cq ~ crf in NVENC terms)

    Default CRF 18 matches OpenShorts' "quality" tier. The old default (crf 20)
    produced clips that read visibly softer and carried a lower data rate than
    OpenShorts renders — crf 18 closes that gap (higher data rate, crisper
    image) without imposing a hard bitrate ceiling.
    """
    if use_gpu():
        log and log("[gpu] h264_nvenc (GPU)")
        # -b:v 0 lifts NVENC's default ~2 Mbps vbr cap so the constant-quality
        # -cq target actually drives bitrate on complex frames (OpenShorts
        # quality-tier parity); spatial-aq preserves fine detail.
        nvenc_cq = min(35, max(19, int(crf) + 7))
        return ["-c:v", "h264_nvenc", "-preset", "p4", "-tune", "hq",
                "-cq", str(nvenc_cq), "-rc", "vbr", "-b:v", "0",
                "-spatial-aq", "1", "-temporal-aq", "1",
                "-pix_fmt", "yuv420p"]
    log and log("[gpu] libx264 (CPU)")
    return ["-c:v", "libx264", "-preset", preset, "-crf", str(crf),
            "-pix_fmt", "yuv420p"]
