"""Official Native SheetSage2 + YuE2 Pipeline Worker on Modal GPU.

Executes melody extraction via SheetSage2 and melody-conditioned generation
via official YuE2Pipeline directly in PyTorch GPU without ComfyUI fragility.
"""
from __future__ import annotations

import base64
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import modal

APP_NAME = "klipin-yue2-native"
MODEL_VOLUME = "klipin-yue2-models"
MODELS_ROOT = Path("/models/yue2")

app = modal.App(APP_NAME)
models = modal.Volume.from_name(MODEL_VOLUME, create_if_missing=True)

image = (
    modal.Image.from_registry(
        "pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime",
        add_python="3.11",
    )
    .apt_install("git", "ffmpeg", "curl", "libsndfile1")
    .pip_install(
        "huggingface_hub>=0.27.0",
        "transformers==4.45.2",
        "soundfile>=0.12.1",
        "accelerate>=0.26.0",
        "safetensors>=0.4.0",
        "mir_eval>=0.8.2",
        "pretty_midi>=0.2.10",
        "mido>=1.3.3",
        "librosa>=0.10.0",
        "scipy>=1.13.1",
        "resampy>=0.4.0",
        "tiktoken>=0.7.0",
    )
    .run_commands(
        "git clone --depth 1 https://github.com/multimodal-art-projection/YuE.git /opt/YuE",
        "cd /opt/YuE && pip install --no-deps -e .",
        "python -c \"import torch, torchaudio, transformers; print('Packages OK:', torch.__version__, torchaudio.__version__, transformers.__version__)\"",
    )
)


@app.function(
    image=image,
    # GPU fallback: never wait in the L4 queue. Same weights/dtype on every
    # card, so the audio is identical — only the queue time changes.
    gpu=[g.strip() for g in os.environ.get("KLIPIN_YUE2_GPU", "L4,A10G,L40S").split(",") if g.strip()],
    cpu=8,
    memory=24576,
    timeout=2400,
    scaledown_window=180,
    volumes={"/models": models},
)
def generate_native_cover(
    ref_audio_b64: str,
    lyrics: str,
    style: str,
    duration_sec: int = 180,
    seed: int = 42,
) -> dict:
    """End-to-end Cover: Audio -> SheetSage2 (ABC melody) -> YuE2 (Reggae Cover Audio)."""
    import gc
    import torch

    hf_cache_dir = MODELS_ROOT / "hf_cache"
    hf_cache_dir.mkdir(parents=True, exist_ok=True)
    os.environ["HF_HOME"] = str(hf_cache_dir)
    os.environ["HUGGINGFACE_HUB_CACHE"] = str(hf_cache_dir / "hub")

    # Safetensors metadata None patch for transformers 4.45.2
    try:
        import safetensors
        import transformers.modeling_utils
        _orig_safe_open = safetensors.safe_open

        class _SafeOpenWrapper:
            def __init__(self, *args, **kwargs):
                self._f = _orig_safe_open(*args, **kwargs)
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return self._f.__exit__(*args)
            def metadata(self):
                m = self._f.metadata()
                return m if m is not None else {"format": "pt"}
            def __getattr__(self, name):
                return getattr(self._f, name)

        safetensors.safe_open = _SafeOpenWrapper
        transformers.modeling_utils.safe_open = _SafeOpenWrapper
        print("Safetensors metadata patch applied successfully.")
    except Exception as e:
        print(f"Safetensors patch note: {e}")

    # Monkey-patch BartDecoder in transformers to guarantee compatibility with SheetSage2
    try:
        import transformers.models.bart.modeling_bart as bart_module
        _orig_bart_decoder_init = bart_module.BartDecoder.__init__

        def _patched_bart_decoder_init(self, config, embed_tokens=None, **kwargs):
            try:
                return _orig_bart_decoder_init(self, config, embed_tokens=embed_tokens, **kwargs)
            except TypeError:
                res = _orig_bart_decoder_init(self, config, **kwargs)
                if embed_tokens is not None:
                    self.embed_tokens = embed_tokens
                return res

        bart_module.BartDecoder.__init__ = _patched_bart_decoder_init
        print("BartDecoder monkey patch verified.")
    except Exception as e:
        print(f"BartDecoder patch note: {e}")

    from transformers import AutoModel

    work = Path(tempfile.mkdtemp(prefix="klipin-yue2-cover-"))
    ref_path = work / "reference.mp3"
    ref_path.write_bytes(base64.b64decode(ref_audio_b64))

    print("=" * 60)
    print("STEP 0: Isolasi Vokal Murni (GPU HDemucs) untuk Kunci Irama Vokal & Cengkok")
    print("=" * 60)
    t_demucs = time.time()
    sheetsage_input_path = ref_path
    first_vocal_sec = 0.0
    voiced_pct = 0.0
    try:
        import math
        import numpy as np
        import soundfile as sf
        from torchaudio.pipelines import HDEMUCS_HIGH_MUSDB_PLUS

        os.environ.setdefault("TORCH_HOME", str(MODELS_ROOT / "torch_cache"))
        proc = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(ref_path), "-ac", "2", "-ar", "44100", "-f", "f32le", "-"],
            capture_output=True,
            timeout=180,
        )
        if proc.returncode == 0 and len(proc.stdout) >= 44100 * 8:
            stereo = np.frombuffer(proc.stdout, dtype=np.float32).reshape(-1, 2).T.copy()
            sr = 44100
            total_samples = stereo.shape[-1]

            bundle = HDEMUCS_HIGH_MUSDB_PLUS
            device_d = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
            demucs_model = bundle.get_model().to(device_d).eval()
            sources_list = list(getattr(demucs_model, "sources", ("drums", "bass", "other", "vocals")))
            vocal_idx = sources_list.index("vocals")
            drums_idx = sources_list.index("drums")
            bass_idx = sources_list.index("bass")

            wav_t = torch.from_numpy(stereo)
            ref_mean = wav_t.mean(dim=0)
            ref_std = float(ref_mean.std().item()) + 1e-8
            wav_norm = (wav_t - ref_mean.mean()) / ref_std

            chunk_samples = sr * 10
            overlap_samples = sr * 1
            stride = chunk_samples - overlap_samples
            vocal_accum = np.zeros(total_samples, dtype=np.float32)
            rhythm_accum = np.zeros(total_samples, dtype=np.float32)
            weight_accum = np.zeros(total_samples, dtype=np.float32)
            fade_ramp = np.linspace(0.0, 1.0, overlap_samples, dtype=np.float32)

            with torch.inference_mode():
                for start in range(0, total_samples, stride):
                    end = min(total_samples, start + chunk_samples)
                    seg = wav_norm[:, start:end]
                    seg_len = seg.shape[-1]
                    if seg_len < chunk_samples:
                        seg = torch.nn.functional.pad(seg, (0, chunk_samples - seg_len))
                    out = demucs_model(seg.unsqueeze(0).to(device_d))[0]
                    v_mono = (out[vocal_idx].mean(dim=0).cpu().numpy()[:seg_len]) * ref_std
                    r_mono = ((out[drums_idx] + out[bass_idx]).mean(dim=0).cpu().numpy()[:seg_len]) * ref_std
                    w = np.ones(seg_len, dtype=np.float32)
                    if start > 0 and seg_len >= overlap_samples:
                        w[:overlap_samples] *= fade_ramp
                    if end < total_samples and seg_len >= overlap_samples:
                        w[-overlap_samples:] *= fade_ramp[::-1]
                    vocal_accum[start:end] += v_mono * w
                    rhythm_accum[start:end] += r_mono * w
                    weight_accum[start:end] += w

            del demucs_model
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

            vocals = (vocal_accum / np.maximum(weight_accum, 1e-6)).astype(np.float32)
            rhythm = (rhythm_accum / np.maximum(weight_accum, 1e-6)).astype(np.float32)

            # FFT bandpass (130-3400 Hz) on vocals for clean activity gating,
            # and lowpass (< 125 Hz) on rhythm so SheetSage2 tracks beats without melody bleed
            n_fft = int(2 ** math.ceil(math.log2(max(2048, len(vocals)))))
            v_spec = np.fft.rfft(vocals, n=n_fft)
            r_spec = np.fft.rfft(rhythm, n=n_fft)
            freqs = np.fft.rfftfreq(n_fft, d=1.0 / sr)
            bp_mask = ((freqs >= 130.0) & (freqs <= 3400.0)).astype(np.float32)
            lp_mask = ((freqs >= 35.0) & (freqs <= 125.0)).astype(np.float32)
            vocals_bp = np.fft.irfft(v_spec * bp_mask, n=n_fft)[: len(vocals)].astype(np.float32)
            rhythm_lp = np.fft.irfft(r_spec * lp_mask, n=n_fft)[: len(rhythm)].astype(np.float32)

            # 80ms frame gate to remove instrumental intro/interlude/guitar fills completely
            frame_len = int(0.080 * sr)
            n_frames = max(1, len(vocals_bp) // frame_len)
            frame_rms = np.zeros(n_frames, dtype=np.float32)
            for idx_f in range(n_frames):
                seg_f = vocals_bp[idx_f * frame_len : (idx_f + 1) * frame_len]
                frame_rms[idx_f] = float(np.sqrt(np.mean(seg_f ** 2) + 1e-12))
            nz_rms = frame_rms[frame_rms > 1e-5]
            if len(nz_rms) >= 8:
                p95 = float(np.percentile(nz_rms, 95))
                active_f = frame_rms >= (p95 * 0.095)
                for idx_f in range(1, n_frames - 1):
                    if active_f[idx_f] and not active_f[idx_f - 1] and not active_f[idx_f + 1] and frame_rms[idx_f] < p95 * 0.18:
                        active_f[idx_f] = False
                for idx_f in range(1, n_frames - 1):
                    if not active_f[idx_f] and active_f[idx_f - 1] and active_f[idx_f + 1] and frame_rms[idx_f] >= p95 * 0.045:
                        active_f[idx_f] = True

                act_idx = np.flatnonzero(active_f)
                if len(act_idx) >= 8:
                    first_vocal_sec = round(float(act_idx[0]) * 0.080, 2)
                    voiced_pct = round(100.0 * float(np.mean(active_f)), 1)
                    sample_gain = np.zeros(len(vocals), dtype=np.float32)
                    for idx_f in act_idx:
                        i0 = max(0, int((idx_f - 0.2) * frame_len))
                        i1 = min(len(vocals), int((idx_f + 1.2) * frame_len))
                        sample_gain[i0:i1] = 1.0
                    win_len = max(1, int(0.012 * sr))
                    sample_gain = np.convolve(sample_gain, np.ones(win_len, dtype=np.float32) / win_len, mode="same")
                    # Combine gated lead vocal with subtle sub-125Hz beat pulse for SheetSage2
                    guided_audio = (vocals * sample_gain + 0.07 * rhythm_lp).astype(np.float32)
                    sheetsage_wav = work / "sheetsage_vocal_locked.wav"
                    sf.write(str(sheetsage_wav), guided_audio, sr)
                    sheetsage_input_path = sheetsage_wav
                    print(
                        f"Vocal stem isolated in {time.time() - t_demucs:.1f}s "
                        f"(first_vocal_sec={first_vocal_sec}s, voiced_pct={voiced_pct}%)"
                    )
    except Exception as demucs_exc:
        print(f"Vocal isolation note (fallback to full ref): {demucs_exc}")

    if first_vocal_sec >= 6.0:
        first_ln = next((ln.strip() for ln in str(lyrics or "").splitlines() if ln.strip()), "")
        if not first_ln.lower().startswith("[intro"):
            lyrics = f"[Intro]\n\n{lyrics}" if first_ln.startswith("[") else f"[Intro]\n\n[Verse]\n{lyrics}"
            print(f"Prepended [Intro] tag for {first_vocal_sec}s instrumental intro.")

    print("=" * 60)
    print("STEP 1: Ekstraksi Melodi Sumber dengan SheetSage2 (m-a-p/SheetSage2)")
    print("=" * 60)
    t0 = time.time()

    device = "cuda" if torch.cuda.is_available() else "cpu"
    transcriber = AutoModel.from_pretrained(
        "m-a-p/SheetSage2",
        trust_remote_code=True,
        torch_dtype=torch.bfloat16 if torch.cuda.is_available() else torch.float32,
    ).to(device)

    res_transcribe = transcriber.transcribe(str(sheetsage_input_path), melody_only=True)
    melody_abc = res_transcribe.get("abc") if isinstance(res_transcribe, dict) else str(res_transcribe)
    (work / "score.abc").write_text(melody_abc, encoding="utf-8")
    print(f"Melodi ABC terekstraksi ({len(melody_abc)} chars) dalam {time.time() - t0:.1f}s")
    print(f"Cuplikan ABC:\n{melody_abc[:250]}...")

    # VRAM Cleanup after SheetSage2
    del transcriber
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

    print("=" * 60)
    print("STEP 2: Generasi Audio Cover YuE2 dengan Kondisi Melodi ABC")
    print("=" * 60)
    t1 = time.time()

    # Load YuE2Pipeline
    from yue2 import YuE2Pipeline

    local_yue2_dir = MODELS_ROOT / "YuE2-3B"
    model_source = str(local_yue2_dir) if local_yue2_dir.is_dir() else "m-a-p/YuE2-3B"

    out_dir = work / "output"
    out_dir.mkdir(parents=True, exist_ok=True)

    with YuE2Pipeline.from_pretrained(
        model_source,
        device=device,
    ) as pipe:
        cover_result = pipe(
            style=style,
            lyrics=lyrics,
            abc=melody_abc,
            cot="melody",
            seed=seed,
        )
        cover_result.save_artifacts(str(out_dir))

    print(f"Generasi YuE2 selesai dalam {time.time() - t1:.1f}s")
    try:
        models.commit()
    except Exception:
        pass

    # Scan output audio
    audio_files = (
        list(out_dir.glob("*.flac"))
        + list(out_dir.glob("*.wav"))
        + list(out_dir.glob("*.mp3"))
    )
    if not audio_files:
        raise FileNotFoundError(f"YuE2 output audio file tidak ditemukan di {out_dir}")

    final_audio = audio_files[0]
    final_mp3 = work / "final_cover.mp3"
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(final_audio), "-c:a", "libmp3lame", "-b:a", "192k", str(final_mp3)],
        capture_output=True,
        check=False,
    )
    res_path = final_mp3 if final_mp3.exists() else final_audio
    audio_bytes = res_path.read_bytes()

    return {
        "audio_b64": base64.b64encode(audio_bytes).decode("ascii"),
        "model": "m-a-p/YuE2-3B",
        "audio_encoder": "m-a-p/SheetSage2",
        "provider": "yue2-native-modal",
        "duration": float(duration_sec),
        "melody_abc": melody_abc[:500],
    }
