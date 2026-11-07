"""Official HeartMuLa MuLaCover worker for reference-audio song covers.

Deploy separately from the ordinary HeartMuLa text-to-song worker::

    modal deploy mulacover_modal_app.py

The official MuLaCover weights are optional and their model/output license is
not the same as the Apache-2.0 source license.  Keep this worker disabled
unless the deployment owner has accepted those terms and has Hugging Face
access to the checkpoint.
"""
from __future__ import annotations

import base64
import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import modal

APP_NAME = "klipin-mulacover"
MODEL_VOLUME = "klipin-mulacover-models"
MODEL_ROOT = Path("/models/mulacover")
SYMBOLIC_CACHE_ROOT = MODEL_ROOT / "symbolic_cache"

app = modal.App(APP_NAME)
models = modal.Volume.from_name(MODEL_VOLUME, create_if_missing=True)

image = (
    modal.Image.from_registry(
        "pytorch/pytorch:2.10.0-cuda13.0-cudnn9-runtime",
        add_python="3.10",
    )
    .apt_install("git", "ffmpeg", "libsndfile1", "curl")
    .pip_install("huggingface_hub>=0.27")
    .run_commands(
        "git clone --depth 1 https://github.com/HeartMuLa/MuLaCover.git /opt/mulacover",
        "cd /opt/mulacover && pip install -e '.[audio]'",
    )
)


def _download(url: str, target: Path) -> None:
    from urllib.request import urlopen

    target.parent.mkdir(parents=True, exist_ok=True)
    with urlopen(url, timeout=120) as response, target.open("wb") as fp:
        fp.write(response.read())


def _ensure_models() -> None:
    from huggingface_hub import snapshot_download

    MODEL_ROOT.mkdir(parents=True, exist_ok=True)
    marker = MODEL_ROOT / ".mulacover-ready"
    if marker.exists():
        return
    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_HUB_TOKEN")
    common = {"token": token} if token else {}
    snapshot_download("HeartMuLa/MuLaCover", local_dir=str(MODEL_ROOT / "MuLaCover"), **common)
    snapshot_download("Qwen/Qwen3-Embedding-0.6B", local_dir=str(MODEL_ROOT / "Qwen3-Embedding-0.6B"), **common)
    snapshot_download("HeartMuLa/HeartCodec-oss-20260123", local_dir=str(MODEL_ROOT / "HeartCodec-oss"), **common)
    # MuLaCover's reference-audio frontend requires YourMT3 plus all five
    # ChordNet folds. The URLs are the ones documented by the official guide.
    _download(
        "https://huggingface.co/spaces/mimbres/YourMT3/resolve/main/amt/logs/2024/"
        "mc13_256_g4_all_v7_mt3f_sqr_rms_moe_wf4_n8k2_silu_rope_rp_b36_nops/"
        "checkpoints/last.ckpt",
        MODEL_ROOT / "SymbolicTranscriptor/yourmt3/last.ckpt",
    )
    for fold in range(5):
        name = (
            "joint_chord_net_ismir_naive_v1.0_reweight(0.0,10.0)_s"
            f"{fold}.best.sdict"
        )
        _download(
            "https://raw.githubusercontent.com/music-x-lab/"
            f"ISMIR2019-Large-Vocabulary-Chord-Recognition/master/cache_data/{name}",
            MODEL_ROOT / "SymbolicTranscriptor/chord" / name,
        )
    marker.write_text("ready", encoding="utf-8")
    models.commit()


def _encode_mp3(wav_path: Path, output_path: Path) -> None:
    encoded = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(wav_path),
         "-c:a", "libmp3lame", "-b:a", "192k", str(output_path)],
        capture_output=True, text=True, timeout=300)
    if encoded.returncode != 0 or not output_path.exists():
        raise RuntimeError(encoded.stderr[-1000:] or "MuLaCover MP3 encode failed")


def _probe_seconds(path: Path) -> float:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=30)
        return max(0.0, float((result.stdout or "").strip() or 0.0))
    except Exception:
        return 0.0


def _assert_usable_output(path: Path, requested_sec: int) -> float:
    """Fail loudly on the known MuLaCover silent-collapse output.

    The CLI can exit 0 while writing a sub-second file (~3 KB). Marking that
    as a completed cover is worse than retrying/failing.
    """
    seconds = _probe_seconds(path)
    size = path.stat().st_size if path.exists() else 0
    floor = max(4.0, float(requested_sec or 0) * 0.15)
    if size < 16_000 or (seconds and seconds < floor):
        raise RuntimeError(
            "MULACOVER_EMPTY_AUDIO: cover output is near-empty "
            f"({seconds:.2f}s, {size} bytes; expected ~{requested_sec}s)")
    return seconds


def _build_genre_drum_midi(melody_mid: Path, chord_mid: Path,
                           tags: str, out_dir: Path) -> Path | None:
    """Synthesize a genre-authentic drum MIDI track on MuLaCover's exact 16th-note grid.

    Runs in a clean child Python process (cwd=/opt/mulacover) so torch is never
    imported inside Modal's parent harness process.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    script = out_dir / "_build_drums.py"
    script.write_text(
        """import sys
from pathlib import Path
from mulacover.symbolic import MAX_SYMBOLIC_LENGTH, SymbolicCondition

melody_mid = Path(sys.argv[1])
chord_mid = Path(sys.argv[2])
tags = sys.argv[3]
out_dir = Path(sys.argv[4])

base = SymbolicCondition.from_midi(melody_mid, chord_mid)
length = min(int(base.symbolic_length), MAX_SYMBOLIC_LENGTH - 4)
if length < 16:
    sys.exit(1)
low_tags = (tags or "").lower()
drums = []
for bar_start in range(0, length - 15, 16):
    bar_idx = bar_start // 16
    if "reggae" in low_tags or "ska" in low_tags:
        for step in (0, 4, 8, 12):
            drums.append((bar_start + step, 42, 1))
        for offbeat in (2, 6, 10, 14):
            drums.append((bar_start + offbeat, 46, 2))
        drums.append((bar_start + 8, 36, 2))
        drums.append((bar_start + 8, 37, 2))
        if bar_idx % 2 == 1:
            drums.append((bar_start + 14, 36, 1))
            drums.append((bar_start + 15, 37, 1))
    elif "koplo" in low_tags or "dangdut" in low_tags:
        for step in (0, 2, 4, 6, 8, 10, 12, 14):
            drums.append((bar_start + step, 42, 1))
        for k_step in (0, 6, 8, 14):
            drums.append((bar_start + k_step, 36, 2))
        for s_step in (4, 11, 12, 15):
            drums.append((bar_start + s_step, 38, 1))
    elif "jazz" in low_tags or "bossa" in low_tags:
        for step in (0, 4, 8, 12):
            drums.append((bar_start + step, 42, 1))
        for offbeat in (3, 7, 11, 15):
            drums.append((bar_start + offbeat, 51, 1))
        drums.append((bar_start + 4, 37, 1))
        drums.append((bar_start + 12, 37, 1))
    else:
        for step in (0, 2, 4, 6, 8, 10, 12, 14):
            drums.append((bar_start + step, 42, 1))
        drums.append((bar_start + 0, 36, 2))
        drums.append((bar_start + 4, 38, 2))
        drums.append((bar_start + 8, 36, 2))
        drums.append((bar_start + 12, 38, 2))
rebuilt = SymbolicCondition(melody=base.melody, chords=base.chords, drums=drums, bpm=base.bpm)
saved = rebuilt.save_midi(out_dir)
""",
        encoding="utf-8",
    )
    try:
        r = subprocess.run(
            [sys.executable, str(script), str(melody_mid), str(chord_mid), str(tags or ""), str(out_dir)],
            cwd="/opt/mulacover",
            capture_output=True,
            text=True,
            timeout=60,
        )
        drum_out = out_dir / "drums.mid"
        return drum_out if r.returncode == 0 and drum_out.is_file() else None
    except Exception:
        return None


def _inject_vocal_timing_markers(lyrics: str, first_vocal_sec: float) -> str:
    """Ensure [Intro] precedes [Verse] when the original singer enters after >= 6.0s."""
    text = str(lyrics or "").strip()
    if not text or first_vocal_sec < 6.0:
        return text
    first_line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    if first_line.lower().startswith("[intro"):
        return text
    if first_line.startswith("["):
        return f"[Intro]\n\n{text}"
    return f"[Intro]\n\n[Verse]\n{text}"


_VOCAL_LOCK_SUBPROCESS_CODE = r'''import gc
import json
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
from mulacover.symbolic import MAX_SYMBOLIC_LENGTH, SymbolicCondition

ref_path = Path(sys.argv[1])
raw_melody_mid = Path(sys.argv[2])
chord_mid = Path(sys.argv[3])
out_melody_mid = Path(sys.argv[4])
meta_path = Path(sys.argv[5])
work_dir = Path(sys.argv[6])
model_root = Path(sys.argv[7])

base = SymbolicCondition.from_midi(raw_melody_mid, chord_mid)
bpm = float(base.bpm or 120.0)
step_sec = 15.0 / bpm
raw_triplets = base.melody.tolist() if base.melody.numel() else []

proc = subprocess.run(
    ["ffmpeg", "-v", "error", "-i", str(ref_path), "-ac", "2", "-ar", "44100", "-f", "f32le", "-"],
    capture_output=True,
    timeout=180,
)
if proc.returncode != 0 or len(proc.stdout) < 44100 * 8:
    shutil.copyfile(raw_melody_mid, out_melody_mid)
    meta_path.write_text(json.dumps({"status": "fallback_decode", "first_vocal_sec": 0.0}), encoding="utf-8")
    sys.exit(0)

stereo = np.frombuffer(proc.stdout, dtype=np.float32).reshape(-1, 2).T.copy()
sr = 44100
total_samples = stereo.shape[-1]
vocals = None
sep_method = "hdemucs_gpu"

try:
    os.environ.setdefault("TORCH_HOME", str(model_root / "torch_cache"))
    from torchaudio.pipelines import HDEMUCS_HIGH_MUSDB_PLUS

    bundle = HDEMUCS_HIGH_MUSDB_PLUS
    device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
    demucs_model = bundle.get_model().to(device).eval()
    sources_list = list(getattr(demucs_model, "sources", ("drums", "bass", "other", "vocals")))
    vocal_idx = sources_list.index("vocals")

    wav_t = torch.from_numpy(stereo)
    ref_mean = wav_t.mean(dim=0)
    ref_std = float(ref_mean.std().item()) + 1e-8
    wav_norm = (wav_t - ref_mean.mean()) / ref_std

    chunk_samples = sr * 10
    overlap_samples = sr * 1
    stride = chunk_samples - overlap_samples
    vocal_accum = np.zeros(total_samples, dtype=np.float32)
    weight_accum = np.zeros(total_samples, dtype=np.float32)
    fade_ramp = np.linspace(0.0, 1.0, overlap_samples, dtype=np.float32)

    with torch.inference_mode():
        for start in range(0, total_samples, stride):
            end = min(total_samples, start + chunk_samples)
            seg = wav_norm[:, start:end]
            seg_len = seg.shape[-1]
            if seg_len < chunk_samples:
                seg = torch.nn.functional.pad(seg, (0, chunk_samples - seg_len))
            out = demucs_model(seg.unsqueeze(0).to(device))[0, vocal_idx]
            v_mono = (out.mean(dim=0).cpu().numpy()[:seg_len]) * ref_std
            w = np.ones(seg_len, dtype=np.float32)
            if start > 0 and seg_len >= overlap_samples:
                w[:overlap_samples] *= fade_ramp
            if end < total_samples and seg_len >= overlap_samples:
                w[-overlap_samples:] *= fade_ramp[::-1]
            vocal_accum[start:end] += v_mono * w
            weight_accum[start:end] += w

    del demucs_model
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    vocals = (vocal_accum / np.maximum(weight_accum, 1e-6)).astype(np.float32)
except Exception as e:
    sep_method = f"mid_side_spectral({e})"
    mid = 0.5 * (stereo[0] + stereo[1])
    side = 0.5 * (stereo[0] - stereo[1])
    n_fft_ms = int(2 ** math.ceil(math.log2(max(2048, total_samples))))
    M = np.fft.rfft(mid, n=n_fft_ms)
    S = np.fft.rfft(side, n=n_fft_ms)
    mag_m = np.abs(M)
    mag_s = np.abs(S)
    mask = np.clip((mag_m - 1.2 * mag_s) / (mag_m + 1e-8), 0.0, 1.0)
    vocals = np.fft.irfft(M * mask, n=n_fft_ms)[:total_samples].astype(np.float32)

n_fft = int(2 ** math.ceil(math.log2(max(2048, len(vocals)))))
spec = np.fft.rfft(vocals, n=n_fft)
freqs = np.fft.rfftfreq(n_fft, d=1.0 / sr)
bp_mask = ((freqs >= 130.0) & (freqs <= 3400.0)).astype(np.float32)
vocals_bp = np.fft.irfft(spec * bp_mask, n=n_fft)[: len(vocals)].astype(np.float32)

samples_per_step = sr * step_sec
max_steps = min(
    MAX_SYMBOLIC_LENGTH - 4,
    max(int(base.symbolic_length), int(len(vocals) / samples_per_step) + 1),
)
step_rms = np.zeros(max_steps, dtype=np.float32)
for s in range(max_steps):
    i0 = int(round(s * samples_per_step))
    i1 = min(len(vocals_bp), int(round((s + 1) * samples_per_step)))
    if i1 > i0:
        step_rms[s] = float(np.sqrt(np.mean(vocals_bp[i0:i1] ** 2) + 1e-12))

non_zero_rms = step_rms[step_rms > 1e-5]
if len(non_zero_rms) < 8:
    shutil.copyfile(raw_melody_mid, out_melody_mid)
    meta_path.write_text(json.dumps({"status": "fallback_silent_vocal", "first_vocal_sec": 0.0}), encoding="utf-8")
    sys.exit(0)

p95 = float(np.percentile(non_zero_rms, 95))
gate_thr = p95 * 0.095
active = step_rms >= gate_thr

for s in range(1, max_steps - 1):
    if active[s] and not active[s - 1] and not active[s + 1] and step_rms[s] < p95 * 0.18:
        active[s] = False
for s in range(1, max_steps - 1):
    if not active[s] and active[s - 1] and active[s + 1] and step_rms[s] >= p95 * 0.045:
        active[s] = True

active_indices = np.flatnonzero(active)
if len(active_indices) < 8:
    shutil.copyfile(raw_melody_mid, out_melody_mid)
    meta_path.write_text(json.dumps({"status": "fallback_few_active_steps", "first_vocal_sec": 0.0}), encoding="utf-8")
    sys.exit(0)

first_vocal_step = int(active_indices[0])
first_vocal_sec = round(first_vocal_step * step_sec, 2)

# Fast vectorized FFT autocorrelation pitch estimator on isolated vocal stem (0.15s vs 42s YourMT3 rerun)
f0_fallback_notes = []
lag_min = max(1, int(sr / 880.0))  # A5 (MIDI 81)
lag_max = int(sr / 110.0)          # A2 (MIDI 45)
for s in range(max_steps):
    if not active[s]:
        continue
    i0 = int(round(s * samples_per_step))
    i1 = min(len(vocals_bp), int(round((s + 1) * samples_per_step)))
    seg = vocals_bp[i0:i1]
    if len(seg) < lag_max * 2:
        continue
    seg = seg - float(np.mean(seg))
    n_ac = int(2 ** math.ceil(math.log2(len(seg) * 2)))
    ac = np.fft.irfft(np.abs(np.fft.rfft(seg, n=n_ac)) ** 2)[: lag_max + 1]
    if ac[0] <= 1e-9:
        continue
    best_lag = int(lag_min + np.argmax(ac[lag_min : lag_max + 1]))
    clarity = float(ac[best_lag] / (ac[0] + 1e-9))
    if clarity >= 0.32:
        f0_hz = float(sr) / float(best_lag)
        midi_p = int(round(69.0 + 12.0 * math.log2(max(f0_hz, 50.0) / 440.0)))
        if 45 <= midi_p <= 84:
            f0_fallback_notes.append((s, midi_p, 1))

pitch_grid = np.zeros(max_steps, dtype=np.int32)
onset_grid = np.zeros(max_steps, dtype=bool)

def _apply_candidates(candidates, overwrite=False):
    for onset, pitch, dur in candidates:
        if not (43 <= pitch <= 86):
            continue
        s0 = max(0, int(onset))
        s1 = min(max_steps, int(onset + dur))
        first_on = True
        for s in range(s0, s1):
            if not active[s]:
                first_on = True
                continue
            if overwrite or pitch_grid[s] == 0:
                pitch_grid[s] = int(pitch)
                if first_on:
                    onset_grid[s] = True
                    first_on = False

# 1) Fill active vocal steps with YourMT3 notes (strictly gated by HDemucs active[s]),
# 2) Fill any remaining active vocal gaps with direct HDemucs vocal stem F0
_apply_candidates(f0_fallback_notes, overwrite=False)
_apply_candidates([(int(o), int(p), int(d)) for o, p, d in raw_triplets], overwrite=True)

mono_triplets = []
s = 0
while s < max_steps:
    p = int(pitch_grid[s])
    if p <= 0:
        s += 1
        continue
    dur = 1
    while (s + dur < max_steps) and (int(pitch_grid[s + dur]) == p) and (not onset_grid[s + dur]):
        dur += 1
    mono_triplets.append([s, p, dur])
    s += dur

smoothed = []
idx = 0
n_mono = len(mono_triplets)
while idx < n_mono:
    curr = list(mono_triplets[idx])
    if not smoothed:
        smoothed.append(curr)
        idx += 1
        continue

    prev = smoothed[-1]
    p_on, p_pitch, p_dur = prev
    c_on, c_pitch, c_dur = curr
    is_contiguous = (c_on == p_on + p_dur)
    has_next_contiguous = (
        idx + 1 < n_mono and mono_triplets[idx + 1][0] == c_on + c_dur
    )
    no_consonant_dip = (
        c_on > 0 and step_rms[c_on] >= 0.65 * step_rms[c_on - 1]
    )

    if is_contiguous and c_pitch == p_pitch and no_consonant_dip and p_dur + c_dur <= 12:
        prev[2] += c_dur
        idx += 1
        continue

    if (
        is_contiguous
        and has_next_contiguous
        and c_dur == 1
        and 1 <= abs(c_pitch - p_pitch) <= 2
        and no_consonant_dip
    ):
        prev[2] += c_dur
        nxt = mono_triplets[idx + 1]
        if (
            nxt[1] == p_pitch
            and nxt[0] < max_steps
            and step_rms[nxt[0]] >= 0.65 * step_rms[c_on]
            and prev[2] + nxt[2] <= 16
        ):
            prev[2] += nxt[2]
            idx += 2
            continue
        idx += 1
        continue

    smoothed.append(curr)
    idx += 1

if len(smoothed) < 4:
    shutil.copyfile(raw_melody_mid, out_melody_mid)
    meta_path.write_text(json.dumps({"status": "fallback_too_few_notes", "first_vocal_sec": 0.0}), encoding="utf-8")
    sys.exit(0)

final_triplets = [
    (int(o), int(p), max(1, min(int(d), MAX_SYMBOLIC_LENGTH - 1 - int(o))))
    for o, p, d in smoothed
    if 0 <= int(o) < MAX_SYMBOLIC_LENGTH - 1
]
rebuilt = SymbolicCondition(
    melody=final_triplets,
    chords=base.chords,
    drums=base.drums,
    bpm=bpm,
)
tmp_out = work_dir / "vocal_locked_midi"
saved = rebuilt.save_midi(tmp_out)
mel_saved = saved.get("melody")
if mel_saved and Path(mel_saved).is_file():
    out_melody_mid.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(mel_saved, out_melody_mid)
else:
    shutil.copyfile(raw_melody_mid, out_melody_mid)

voiced_pct = round(100.0 * float(np.mean(active[:max_steps])), 1)
meta = {
    "status": "vocal_stem_locked_v2",
    "separation_method": sep_method,
    "bpm": round(bpm, 2),
    "first_vocal_sec": first_vocal_sec,
    "voiced_step_pct": voiced_pct,
    "raw_note_count": len(raw_triplets),
    "locked_note_count": len(final_triplets),
}
meta_path.parent.mkdir(parents=True, exist_ok=True)
meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
'''


def _extract_vocal_stem_and_lock_melody(
    ref_path: Path,
    raw_melody_mid: Path,
    chord_mid: Path,
    out_melody_mid: Path,
    meta_path: Path,
    work_dir: Path,
) -> dict:
    """Run vocal stem isolation & melody locking in a clean child Python process."""
    import json as _json

    work_dir.mkdir(parents=True, exist_ok=True)
    script_path = work_dir / "_vocal_lock_worker.py"
    script_path.write_text(_VOCAL_LOCK_SUBPROCESS_CODE, encoding="utf-8")
    res = subprocess.run(
        [
            sys.executable,
            str(script_path),
            str(ref_path),
            str(raw_melody_mid),
            str(chord_mid),
            str(out_melody_mid),
            str(meta_path),
            str(work_dir),
            str(MODEL_ROOT),
        ],
        cwd="/opt/mulacover",
        capture_output=True,
        text=True,
        timeout=300,
    )
    if res.returncode != 0:
        raise RuntimeError((res.stderr or res.stdout or "vocal lock subprocess failed")[-1000:])
    if meta_path.is_file():
        return _json.loads(meta_path.read_text(encoding="utf-8"))
    return {"status": "unknown"}


def _sampling_extra(cfg_scale=None, temperature=None, topk=None):
    extra = []
    if cfg_scale is not None:
        extra += ["--cfg_scale", str(float(cfg_scale))]
    if temperature is not None:
        extra += ["--temperature", str(float(temperature))]
    if topk is not None:
        extra += ["--topk", str(int(topk))]
    return extra


def _run_cli(source_args, lyrics_path, tags_path, wav_path, duration_sec, seed, env, extra=()):
    lazy_args = ["--lazy_load"] if os.environ.get("KLIPIN_MULACOVER_LAZY_LOAD", "0").strip() == "1" else []
    cmd = [
        sys.executable, "-m", "mulacover.cli",
        "--model_path", str(MODEL_ROOT),
        *source_args,
        "--lyrics", str(lyrics_path), "--tags", str(tags_path),
        "--save_path", str(wav_path), "--device", "cuda:0",
        "--dtype", "bfloat16", *lazy_args,
        "--max_audio_length_ms", str(max(10, min(int(duration_sec), 600)) * 1000),
        "--seed", str(int(seed)) if seed is not None else "0",
        "--disable_progress",
        *extra,
    ]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=2300, env=env)


def _gpu_candidates() -> list[str]:
    """GPU fallback list so a job never sits in the L4 capacity queue.

    Modal tries each type in order and schedules on the first one with free
    capacity. All of these are 24 GB+ cards running the exact same weights and
    dtype, so output quality is identical - only the waiting time changes.
    """
    raw = os.environ.get("KLIPIN_MULACOVER_GPU", "L4,A10G,L40S")
    names = [g.strip() for g in raw.split(",") if g.strip()]
    return names or ["L4"]


def _transcribe_symbolic(ref_path: Path, cache_dir: Path) -> bool:
    """Run only MuLaCover's audio->symbolic frontend in a clean child subprocess.

    Skips loading the 3B MuLaCover LM, HeartCodec, and Qwen3-Embedding when
    populating the symbolic cache, cutting uncached transcription time by ~40s
    with zero change to the MIDI output.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    code = (
        "import sys; from pathlib import Path; import torch; "
        "from mulacover._symbolic_transcription import SymbolicTranscriber; "
        "device = torch.device('cuda:0' if torch.cuda.is_available() else 'cpu'); "
        "tr = SymbolicTranscriber(Path(sys.argv[1]) / 'SymbolicTranscriptor', device, torch.float32, lazy_load=True); "
        "cond = tr.transcribe(sys.argv[2]); "
        "cond.save_midi(Path(sys.argv[3]))"
    )
    r = subprocess.run(
        [sys.executable, "-c", code, str(MODEL_ROOT), str(ref_path), str(cache_dir)],
        cwd="/opt/mulacover",
        capture_output=True,
        text=True,
        timeout=600,
    )
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "SymbolicTranscriber failed")[-1000:])
    return (cache_dir / "melody.mid").is_file() and (cache_dir / "chord.mid").is_file()


def _ensure_symbolic_cache(ref_path: Path, cache_dir: Path, lyrics_path: Path,
                           tags_path: Path, work: Path, env: dict) -> str:
    """Fill the symbolic cache; fall back to the official CLI if the API moves."""
    try:
        if _transcribe_symbolic(ref_path, cache_dir):
            return ""
    except Exception as exc:  # noqa: BLE001 - keep the proven CLI path as fallback
        print(f"[Symbolic] direct transcription fallback to CLI: {exc}")
    result = _run_cli(["--ref_audio", str(ref_path), "--symbolic_save_dir", str(cache_dir)],
                      lyrics_path, tags_path, work / "transcribe.wav", 10, 0, env)
    return (result.stderr or "")[-2000:]


_GPU = dict(
    image=image,
    gpu=_gpu_candidates(),
    cpu=8,
    memory=24576,
    timeout=2400,
    scaledown_window=int(os.environ.get("KLIPIN_MULACOVER_SCALEDOWN", "60")),
    min_containers=int(os.environ.get("KLIPIN_MULACOVER_MIN_CONTAINERS", "0")),
    volumes={"/models": models},
    secrets=[modal.Secret.from_name("klipin-backend-secrets")],
)


@app.function(**_GPU)
def generate_cover(ref_audio_b64: str, lyrics: str, tags: str,
                   duration_sec: int = 180, seed: int | None = None,
                   lora_id: str | None = None, cfg_scale: float | None = None,
                   temperature: float | None = None, topk: int | None = None,
                   drop_drums: bool = False) -> dict:
    """Generate a cover from source audio + locked lyrics + style tags.

    ``drop_drums`` skips the reference drum track even when its symbolic MIDI is
    cached, so a genre like reggae can rebuild the groove (one-drop) from the
    tags instead of inheriting the source song's pop/ballad beat.
    """
    _ensure_models()
    raw = base64.b64decode(ref_audio_b64 or "")
    if len(raw) < 1024 or len(raw) > 15_000_000:
        raise RuntimeError("MuLaCover reference audio must be between 1 KiB and 15 MB")
    if not str(lyrics or "").strip():
        raise RuntimeError("MuLaCover requires non-empty extracted lyrics")
    work = Path(tempfile.mkdtemp(prefix="klipin-mulacover-"))
    ref_path = work / "reference.mp3"
    lyrics_path = work / "lyrics.txt"
    tags_path = work / "tags.txt"
    wav_path = work / "cover.wav"
    output_path = work / "cover.mp3"
    ref_path.write_bytes(raw)
    lyrics_path.write_text(lyrics, encoding="utf-8")
    tags_path.write_text(tags, encoding="utf-8")
    # The reference-audio frontend (YourMT3 + 5 ChordNet, fp32) is the slow
    # part and it rescans the whole song on every call.  MuLaCover's
    # ``--symbolic_save_dir`` exports the exact condition as replayable MIDI,
    # so cache it keyed by the reference bytes and reuse it as
    # ``--melody_midi/--chord_midi`` to skip the transcription entirely.
    sha = hashlib.sha1(raw).hexdigest()[:16]
    cache_dir = SYMBOLIC_CACHE_ROOT / sha
    melody_mid = cache_dir / "melody.mid"
    locked_melody_mid = cache_dir / "melody_vocal_locked_v3.mid"
    vocal_meta_file = cache_dir / "vocal_lock_meta_v3.json"
    chord_mid = cache_dir / "chord.mid"
    drum_mid = cache_dir / "drums.mid"
    have_midi = melody_mid.is_file() and chord_mid.is_file()
    env = dict(os.environ)
    if lora_id:
        env["KLIPIN_MUSIC_LORA_ID"] = str(lora_id)
    need_genre_groove = bool(drop_drums) or any(
        g in (tags or "").lower() for g in ("reggae", "ska", "koplo", "dangdut")
    )
    if not have_midi:
        # Ensure symbolic cache (BPM, chord.mid, raw melody.mid) exists first so we can
        # lock melody.mid strictly to the isolated vocal stem and build genre drum groove.
        _ensure_symbolic_cache(ref_path, cache_dir, lyrics_path, tags_path, work, env)
        have_midi = melody_mid.is_file() and chord_mid.is_file()

    vocal_meta: dict = {}
    if have_midi:
        if not locked_melody_mid.is_file():
            try:
                vocal_meta = _extract_vocal_stem_and_lock_melody(
                    ref_path, melody_mid, chord_mid, locked_melody_mid, vocal_meta_file, work
                )
            except Exception as lock_exc:
                print(f"[VocalLock] Warning: fallback to raw melody ({lock_exc})")
        elif vocal_meta_file.is_file():
            try:
                import json as _json
                vocal_meta = _json.loads(vocal_meta_file.read_text(encoding="utf-8"))
            except Exception:
                vocal_meta = {}
        try:
            models.commit()
        except Exception:
            pass

    first_vocal_sec = float(vocal_meta.get("first_vocal_sec") or 0.0)
    if first_vocal_sec >= 6.0:
        lyrics = _inject_vocal_timing_markers(lyrics, first_vocal_sec)
        lyrics_path.write_text(lyrics, encoding="utf-8")

    active_melody_mid = locked_melody_mid if locked_melody_mid.is_file() else melody_mid
    if have_midi:
        source_args = ["--melody_midi", str(active_melody_mid), "--chord_midi", str(chord_mid)]
        if need_genre_groove:
            genre_drum = _build_genre_drum_midi(
                active_melody_mid, chord_mid, tags, work / "genre_midi"
            )
            if genre_drum and genre_drum.is_file():
                source_args += ["--drum_midi", str(genre_drum)]
            elif drum_mid.is_file() and not drop_drums:
                source_args += ["--drum_midi", str(drum_mid)]
        elif drum_mid.is_file():
            source_args += ["--drum_midi", str(drum_mid)]
        symbolic_args: list[str] = []
    else:
        source_args = ["--ref_audio", str(ref_path)]
        symbolic_args = ["--symbolic_save_dir", str(cache_dir)]
    result = _run_cli(source_args, lyrics_path, tags_path, wav_path,
                      duration_sec, seed, env,
                      extra=tuple(symbolic_args) + tuple(
                          _sampling_extra(cfg_scale, temperature, topk)))
    if result.returncode != 0 or not wav_path.exists():
        raise RuntimeError(result.stderr[-3000:] or "MuLaCover produced no audio")
    if not have_midi and melody_mid.is_file() and chord_mid.is_file():
        try:
            models.commit()
        except Exception:
            pass
    _encode_mp3(wav_path, output_path)
    actual_sec = _assert_usable_output(output_path, duration_sec)
    return {
        "provider": "mulacover-modal",
        "model": "HeartMuLa/MuLaCover",
        "duration_sec": int(duration_sec),
        "audio_sec": round(actual_sec, 2),
        "seed": seed,
        "lora_id": lora_id or "",
        "cfg_scale": cfg_scale,
        "temperature": temperature,
        "topk": topk,
        "drop_drums": bool(drop_drums),
        "vocal_lock": vocal_meta,
        "audio_b64": base64.b64encode(output_path.read_bytes()).decode("ascii"),
        "stdout_tail": result.stdout[-1000:],
    }


@app.function(**_GPU)
def transcribe_ref(ref_audio_b64: str) -> dict:
    """Reference audio -> symbolic MIDI (melody/chord/drums/bpm).

    The frontend (YourMT3 + ChordNet) is the slow part; the exported MIDI is
    what Fase 3 edits and feeds back through ``generate_cover_from_midi``.
    Cached on the model volume keyed by the reference hash.
    """
    _ensure_models()
    raw = base64.b64decode(ref_audio_b64 or "")
    if len(raw) < 1024 or len(raw) > 15_000_000:
        raise RuntimeError("MuLaCover reference audio must be between 1 KiB and 15 MB")
    sha = hashlib.sha1(raw).hexdigest()[:16]
    cache_dir = SYMBOLIC_CACHE_ROOT / sha
    melody_mid = cache_dir / "melody.mid"
    locked_melody_mid = cache_dir / "melody_vocal_locked_v3.mid"
    vocal_meta_file = cache_dir / "vocal_lock_meta_v3.json"
    chord_mid = cache_dir / "chord.mid"
    drum_mid = cache_dir / "drums.mid"
    cached = melody_mid.is_file() and chord_mid.is_file()
    work = Path(tempfile.mkdtemp(prefix="klipin-mulacover-tr-"))
    ref_path = work / "reference.mp3"
    if not cached or not locked_melody_mid.is_file():
        ref_path.write_bytes(raw)
    if not cached:
        lyrics_path = work / "lyrics.txt"
        lyrics_path.write_text("[Intro]\n\n[Outro]\n", encoding="utf-8")
        tags_path = work / "tags.txt"
        tags_path.write_text("genre:[pop]\n", encoding="utf-8")
        err_tail = _ensure_symbolic_cache(
            ref_path, cache_dir, lyrics_path, tags_path, work, dict(os.environ))
        if not (melody_mid.is_file() and chord_mid.is_file()):
            raise RuntimeError("MuLaCover transcription produced no symbolic MIDI: "
                               + err_tail)
    vocal_meta: dict = {}
    if not locked_melody_mid.is_file() and melody_mid.is_file() and chord_mid.is_file():
        try:
            vocal_meta = _extract_vocal_stem_and_lock_melody(
                ref_path, melody_mid, chord_mid, locked_melody_mid, vocal_meta_file, work
            )
        except Exception as lock_exc:
            vocal_meta = {"error": str(lock_exc)}
        try:
            models.commit()
        except Exception:
            pass
    elif vocal_meta_file.is_file():
        try:
            import json as _json
            vocal_meta = _json.loads(vocal_meta_file.read_text(encoding="utf-8"))
        except Exception:
            vocal_meta = {}
    active_melody_mid = locked_melody_mid if locked_melody_mid.is_file() else melody_mid
    bpm = ""
    bpm_file = cache_dir / "bpm.txt"
    if bpm_file.is_file():
        bpm = bpm_file.read_text(encoding="utf-8").strip()
    return {
        "provider": "mulacover-modal", "kind": "symbolic", "sha": sha, "cached": cached,
        "bpm": bpm,
        "vocal_lock": vocal_meta,
        "melody_b64": base64.b64encode(active_melody_mid.read_bytes()).decode("ascii"),
        "chord_b64": base64.b64encode(chord_mid.read_bytes()).decode("ascii"),
        "drums_b64": (base64.b64encode(drum_mid.read_bytes()).decode("ascii")
                      if drum_mid.is_file() else ""),
    }


@app.function(**_GPU)
def generate_cover_from_midi(melody_b64: str, chord_b64: str, drum_b64: str | None,
                             lyrics: str, tags: str, duration_sec: int = 180,
                             seed: int | None = None, lora_id: str | None = None,
                             cfg_scale: float | None = None,
                             temperature: float | None = None,
                             topk: int | None = None) -> dict:
    """Render a cover from saved/edited symbolic MIDI — skips the slow frontend."""
    _ensure_models()
    if not str(lyrics or "").strip():
        raise RuntimeError("MuLaCover requires non-empty lyrics")
    work = Path(tempfile.mkdtemp(prefix="klipin-mulacover-midi-"))
    melody_path = work / "melody.mid"
    chord_path = work / "chord.mid"
    melody_path.write_bytes(base64.b64decode(melody_b64))
    chord_path.write_bytes(base64.b64decode(chord_b64))
    source_args = ["--melody_midi", str(melody_path), "--chord_midi", str(chord_path)]
    need_genre_groove = any(
        g in (tags or "").lower() for g in ("reggae", "ska", "koplo", "dangdut")
    )
    if not drum_b64 and need_genre_groove:
        genre_drum = _build_genre_drum_midi(
            melody_path, chord_path, tags, work / "genre_midi"
        )
        if genre_drum and genre_drum.is_file():
            source_args += ["--drum_midi", str(genre_drum)]
    elif drum_b64:
        drum_path = work / "drums.mid"
        drum_path.write_bytes(base64.b64decode(drum_b64))
        source_args += ["--drum_midi", str(drum_path)]
    lyrics_path = work / "lyrics.txt"
    lyrics_path.write_text(lyrics, encoding="utf-8")
    tags_path = work / "tags.txt"
    tags_path.write_text(tags, encoding="utf-8")
    wav_path = work / "cover.wav"
    output_path = work / "cover.mp3"
    env = dict(os.environ)
    if lora_id:
        env["KLIPIN_MUSIC_LORA_ID"] = str(lora_id)
    result = _run_cli(source_args, lyrics_path, tags_path, wav_path,
                      duration_sec, seed, env,
                      extra=tuple(_sampling_extra(cfg_scale, temperature, topk)))
    if result.returncode != 0 or not wav_path.exists():
        raise RuntimeError(result.stderr[-3000:] or "MuLaCover produced no audio")
    _encode_mp3(wav_path, output_path)
    actual_sec = _assert_usable_output(output_path, duration_sec)
    return {
        "provider": "mulacover-modal", "model": "HeartMuLa/MuLaCover",
        "duration_sec": int(duration_sec), "audio_sec": round(actual_sec, 2),
        "seed": seed, "lora_id": lora_id or "", "source": "midi",
        "cfg_scale": cfg_scale, "temperature": temperature, "topk": topk,
        "audio_b64": base64.b64encode(output_path.read_bytes()).decode("ascii"),
        "stdout_tail": result.stdout[-1000:],
    }


@app.function(image=image, gpu="L4", timeout=300)
def health() -> dict:
    import torch

    return {"provider": "mulacover", "cuda": bool(torch.cuda.is_available()),
            "device": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "none"}
