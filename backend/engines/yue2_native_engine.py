"""Native Python SDK Cover Engine (SheetSage2 + YuE2).

Executes melody extraction via SheetSage2 and melody-conditioned generation
via YuE2 directly within a PyTorch GPU environment.
"""
from __future__ import annotations

import gc
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from engines.base_cover_engine import BaseCoverEngine


class Yue2NativeEngine(BaseCoverEngine):
    """Engine executing SheetSage2 + YuE2 cover generation via native Python SDK."""

    def __init__(
        self,
        sheetsage_model_id: str = "m-a-p/SheetSage2",
        yue2_model_id: str = "m-a-p/YuE2-3B",
        device: str = "cuda:0",
    ) -> None:
        self.sheetsage_model_id = os.environ.get("SHEETSAGE2_MODEL_PATH") or sheetsage_model_id
        self.yue2_model_id = os.environ.get("YUE2_MODEL_PATH") or yue2_model_id
        self.device = os.environ.get("CUDA_VISIBLE_DEVICES", device)

    @property
    def engine_id(self) -> str:
        return "yue2_native"

    @property
    def name(self) -> str:
        return "YuE2 + SheetSage2 (Native Python SDK — VRAM 16-24GB)"

    def is_available(self) -> tuple[bool, str]:
        """Check if native PyTorch environment and CUDA are ready."""
        try:
            import torch
            if not torch.cuda.is_available():
                return False, "PyTorch CUDA tidak terdeteksi (GPU diperlukan untuk YuE2 Native)"
            return True, ""
        except ImportError:
            return False, "Paket 'torch' belum terpasang di environment ini"
        except Exception as e:
            return False, f"Pemeriksaan PyTorch gagal: {e}"

    def extract_symbolic_melody(self, audio_path: Path, log: Any = None) -> str:
        """Stage 1: Extract melody-only ABC notation using SheetSage2."""
        log and log("🎼 [SheetSage2] Memuat model ekstraksi melodi...")
        import torch
        from transformers import AutoModel

        device = "cuda" if torch.cuda.is_available() else "cpu"
        dtype = torch.bfloat16 if torch.cuda.is_available() else torch.float32

        transcriber = AutoModel.from_pretrained(
            self.sheetsage_model_id,
            trust_remote_code=True,
            torch_dtype=dtype,
        ).to(device)

        log and log(f"🎼 [SheetSage2] Mentranskripsi {audio_path.name} (melody_only=True)...")
        result = transcriber.transcribe(str(audio_path), melody_only=True)

        score_abc = result.get("abc") or result.get("score_abc") or str(result)

        # Stage 2: Memory cleanup to free VRAM for YuE2
        log and log("🧹 [SheetSage2] Membersihkan VRAM GPU...")
        del transcriber
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

        return score_abc

    def generate_cover(
        self,
        source_audio_path: str | Path,
        lyrics: str,
        genre_style: str,
        vocal_persona: str = "",
        seed: int | None = None,
        max_duration: int = 180,
        output_dir: str | Path = "",
        extra_params: dict[str, Any] | None = None,
        log: Any = None,
    ) -> dict[str, Any]:
        """Execute end-to-end native cover generation."""
        available, reason = self.is_available()
        if not available:
            raise RuntimeError(f"Engine YuE2 Native tidak aktif: {reason}")

        import torch

        src_path = Path(source_audio_path).resolve()
        if not src_path.is_file():
            raise FileNotFoundError(f"Audio sumber tidak ditemukan: {src_path}")

        work_dir = Path(output_dir or ".").resolve()
        work_dir.mkdir(parents=True, exist_ok=True)

        # Stage 1 & 2: Symbolic ABC extraction + VRAM release
        melody_abc = self.extract_symbolic_melody(src_path, log=log)
        (work_dir / "score.abc").write_text(melody_abc, encoding="utf-8")

        # Stage 3: Load YuE2 and run melody-conditioned generation
        log and log(f"🚀 [YuE2 Native] Memuat model {self.yue2_model_id}...")

        # Import YuE2 pipeline dynamically
        try:
            from yue2 import YuE2Pipeline
        except ImportError:
            # Fallback for transformers-based checkpoint
            try:
                from transformers import pipeline
                YuE2Pipeline = None
            except ImportError as err:
                raise RuntimeError(
                    "YuE2 pipeline belum terpasang. Pasang via 'pip install yue2' atau gunakan Mode ComfyUI."
                ) from err

        device = "cuda" if torch.cuda.is_available() else "cpu"
        generator_seed = int(seed) if seed is not None else 42
        generator = torch.Generator(device=device).manual_seed(generator_seed)

        log and log(
            f"🎵 [YuE2 Native] Menghasilkan cover (seed={generator_seed}, "
            f"duration={max_duration}s, cot='melody')..."
        )

        target_wav = work_dir / "yue2_cover.wav"
        target_mp3 = work_dir / "yue2_cover.mp3"

        if YuE2Pipeline is not None:
            pipe = YuE2Pipeline.from_pretrained(
                self.yue2_model_id,
                torch_dtype=torch.bfloat16 if torch.cuda.is_available() else torch.float32,
            ).to(device)

            output_audio = pipe(
                style=genre_style,
                lyrics=lyrics,
                abc=melody_abc,
                cot="melody",
                max_duration=max_duration,
                generator=generator,
            )
            output_audio.save(str(target_wav))
            del pipe
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        else:
            raise RuntimeError(
                "YuE2Pipeline native module tidak ditemukan. Silakan konfigurasi COMFYUI_URL untuk menggunakan mode ComfyUI."
            )

        # Convert to MP3 if ffmpeg is available
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg and target_wav.exists():
            subprocess.run(
                [ffmpeg, "-y", "-i", str(target_wav), "-b:a", "192k", str(target_mp3)],
                capture_output=True,
                check=False,
            )
        output_file = target_mp3 if target_mp3.exists() else target_wav

        from music import probe_audio_seconds
        dur = probe_audio_seconds(output_file)

        return {
            "audio_path": str(output_file),
            "duration": dur or float(max_duration),
            "engine": self.engine_id,
            "status": "completed",
            "metadata": {
                "model": self.yue2_model_id,
                "symbolic_transcription": self.sheetsage_model_id,
                "cot": "melody",
                "backend": "native_sdk",
                "seed": generator_seed,
            },
        }
