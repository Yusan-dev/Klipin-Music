"""LoRA Jawa scaffold — dataset validator + training launcher + eval.

Tidak melatih apa pun tanpa dataset berizin (lihat docs/MUSIC_LORA_PLAN.md).
``validate_dataset`` dipakai CI/operator untuk memastikan folder dataset layak
latih; ``launch`` menolak dengan pesan jelas bila syarat belum terpenuhi.
"""
from __future__ import annotations

import json
from pathlib import Path

REQUIRED_FIELDS = ("audio", "lyrics", "language", "license")
LANGS = {"jv", "id", "su", "ban", "ms"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".flac", ".ogg"}
MIN_TRACKS = 300


def validate_dataset(root: str | Path) -> dict:
    """Periksa folder dataset: manifest.jsonl + file audio + lisensi."""
    root = Path(root)
    report: dict = {"root": str(root), "ok": False, "tracks": 0,
                    "langs": {}, "errors": [], "warnings": []}
    manifest = root / "manifest.jsonl"
    if not manifest.exists():
        report["errors"].append("manifest.jsonl tidak ditemukan")
        return report
    seen = 0
    for n, line in enumerate(manifest.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            report["errors"].append(f"baris {n}: bukan JSON")
            continue
        missing = [k for k in REQUIRED_FIELDS if not row.get(k)]
        if missing:
            report["errors"].append(f"baris {n}: tanpa {','.join(missing)}")
            continue
        audio = root / str(row["audio"])
        if audio.suffix.lower() not in AUDIO_EXTS or not audio.exists():
            report["errors"].append(f"baris {n}: audio hilang/format salah")
            continue
        if not str(row.get("lyrics") or "").strip():
            report["warnings"].append(f"baris {n}: lirik kosong")
        lang = str(row.get("language") or "").strip().lower()
        if lang not in LANGS:
            report["warnings"].append(f"baris {n}: bahasa '{lang}' di luar fokus")
        report["langs"][lang] = report["langs"].get(lang, 0) + 1
        seen += 1
        if len(report["errors"]) > 50:
            report["errors"].append("…terlalu banyak error, berhenti")
            break
    report["tracks"] = seen
    if seen < MIN_TRACKS:
        report["errors"].append(
            f"butuh ≥{MIN_TRACKS} lagu berizin, baru {seen}")
    report["ok"] = not report["errors"]
    return report


def launch(root: str | Path, base: str = "ace-step", rank: int = 16,
           out: str = "jawa-v1", dry_run: bool = True) -> dict:
    """Jalankan (atau simulasikan) training LoRA."""
    report = validate_dataset(root)
    if not report["ok"]:
        raise RuntimeError(
            "Dataset belum layak latih: " + "; ".join(report["errors"][:5]))
    if base not in ("ace-step", "heartmula"):
        raise ValueError("base harus 'ace-step' atau 'heartmula'")
    plan = {"base": base, "rank": int(rank), "out": out,
            "tracks": report["tracks"], "langs": report["langs"],
            "steps": max(500, report["tracks"] * 2)}
    if dry_run:
        return {"dry_run": True, **plan,
                "note": "dry-run: tanpa GPU. Set dry_run=False bila siap bayar."}
    # Titik masuk training nyata (diimplementasikan saat dataset terkumpul).
    raise RuntimeError(
        "Training nyata belum diimplementasikan — kumpulkan dataset dulu "
        "(lihat docs/MUSIC_LORA_PLAN.md), lalu sambungkan trainer " + base)


def eval_report(generated: list[dict]) -> dict:
    """Agregat skor panel native: {intelligibility, rhyme, cengkok} 0-100."""
    if not generated:
        return {"n": 0}
    keys = ("intelligibility", "rhyme", "cengkok")
    out: dict = {"n": len(generated)}
    for k in keys:
        vals = [float(g.get(k, 0)) for g in generated
                if isinstance(g.get(k), (int, float))]
        out[k] = round(sum(vals) / len(vals), 1) if vals else None
    return out
