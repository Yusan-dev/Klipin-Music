"""Durable, account-owned singing auditions and frozen persona references."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import secrets
import threading
import time
import wave
from pathlib import Path

import db
import voice_personas

_cap_lock = threading.Lock()
_cap_cache = (0.0, {})
_materialize_lock = threading.Lock()
VERSION = "singing-persona-v1"


def enabled():
    return os.environ.get("KLIPIN_VOICE_MODAL_ENABLED", "0") == "1"


def _function(name):
    from modal_credentials import ensure_modal_credentials
    import modal
    ensure_modal_credentials()
    return modal.Function.from_name(os.environ.get("KLIPIN_VOICE_MODAL_APP", "klipin-voice"), name)


def capability():
    global _cap_cache
    if not enabled():
        return {"ready": False, "message": "Preview suara belum tersedia. Coba kembali setelah layanan diaktifkan."}
    with _cap_lock:
        if time.monotonic() - _cap_cache[0] < 60:
            return dict(_cap_cache[1])
        try:
            call = _function("readiness").spawn()
            result = call.get(timeout=25)
            ready = bool(isinstance(result, dict) and (result.get("ready") or result.get("version")))
            status = {"ready": ready, "message": "Preview suara siap." if ready else "Layanan suara sedang disiapkan."}
        except Exception:
            status = {"ready": False, "message": "Layanan suara belum terhubung. Silakan coba lagi nanti."}
        _cap_cache = (time.monotonic(), status)
        return dict(status)


def normalize_design(raw):
    choices = {"vocal_gender": ("auto", "male", "female"),
               "texture": ("smooth", "breathy", "raspy"),
               "energy": ("balanced", "soft", "powerful"),
               "register": ("mid", "low", "high")}
    spec = {}
    for key, allowed in choices.items():
        spec[key] = str(raw.get(key) or allowed[0]).lower()
        if spec[key] not in allowed:
            raise ValueError("Pilihan karakter vokal tidak valid.")
    for key in ("warmth", "brightness"):
        spec[key] = max(-100, min(100, int(raw.get(key, 0))))
    spec["seed"] = int(raw.get("seed", 1))
    if not 0 <= spec["seed"] < 2**31:
        raise ValueError("Variasi suara tidak valid.")
    spec["version"] = VERSION
    return spec


def tags_for_design(spec):
    warmth = "warm rich voice" if spec["warmth"] > 0 else "cool rounded voice" if spec["warmth"] < 0 else "neutral vocal tone"
    brightness = "bright forward vocal" if spec["brightness"] > 0 else "dark intimate vocal" if spec["brightness"] < 0 else "balanced vocal"
    gender = {"male": "male singer,masculine voice", "female": "female singer,feminine voice", "auto": "natural singer"}[spec["vocal_gender"]]
    # Keep the exact continuous controls in the prompt. The seed supplies the
    # large variation space; these values prevent every slider position from
    # collapsing into only three coarse prompt buckets.
    return ",".join((gender, warmth, f"warmth {spec['warmth']}/100",
                     brightness, f"brightness {spec['brightness']}/100",
                     {"smooth": "clear smooth voice", "breathy": "airy breathy voice", "raspy": "husky textured voice"}[spec["texture"]],
                     {"soft": "gentle intimate singing", "balanced": "expressive singing", "powerful": "powerful singing"}[spec["energy"]],
                     spec["register"] + " vocal register"))


def get(preview_id, user_id):
    with db._lock, db._connect() as conn:
        row = conn.execute("SELECT * FROM music_voice_previews WHERE preview_id=? AND user_id=?",
                           (preview_id, user_id)).fetchone()
    if not row:
        raise LookupError("Preview tidak ditemukan.")
    return dict(row)


def public(row):
    return {key: row[key] for key in ("preview_id", "status", "error", "duration", "saved_persona_id", "created_at")} | {
        "spec": json.loads(row["spec"]), "ready": row["status"] == "completed"}


def recent(user_id):
    with db._lock, db._connect() as conn:
        rows = conn.execute("SELECT * FROM music_voice_previews WHERE user_id=? AND status!='deleted' "
                            "ORDER BY created_at DESC LIMIT 6", (user_id,)).fetchall()
    return [public(dict(row)) for row in rows]


def _update(preview_id, user_id, **fields):
    fields["updated_at"] = time.time()
    with db._lock, db._connect() as conn:
        conn.execute("UPDATE music_voice_previews SET " + ",".join(k + "=?" for k in fields)
                     + " WHERE preview_id=? AND user_id=?", (*fields.values(), preview_id, user_id))


def begin(user_id, raw):
    spec = normalize_design(raw)
    serialized = json.dumps(spec, sort_keys=True)
    key = hashlib.sha256(serialized.encode()).hexdigest()
    now = time.time()
    with db._lock, db._connect() as conn:
        existing = conn.execute("SELECT * FROM music_voice_previews WHERE user_id=? AND request_key=?",
                                (user_id, key)).fetchone()
        if existing and existing["status"] != "deleted":
            return public(dict(existing))
    status = capability()
    if not status["ready"]:
        raise RuntimeError(status["message"])
    preview_id = "vp_" + secrets.token_hex(10)
    with db._lock, db._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute("SELECT * FROM music_voice_previews WHERE user_id=? AND request_key=?",
                                (user_id, key)).fetchone()
        if existing:
            return public(dict(existing))
        conn.execute("UPDATE music_voice_previews SET status='failed',error=? WHERE user_id=? "
                     "AND status IN ('queued','processing') AND created_at<?",
                     ("Preview melewati batas waktu. Pilih variasi lain untuk mencoba kembali.", user_id, now - 3600))
        if conn.execute("SELECT 1 FROM music_voice_previews WHERE user_id=? AND status IN ('queued','processing')",
                        (user_id,)).fetchone():
            raise ValueError("Satu preview masih diproses. Tunggu hingga selesai.")
        daily = conn.execute("SELECT COUNT(*) FROM music_voice_previews WHERE user_id=? AND created_at>?",
                             (user_id, now - 86400)).fetchone()[0]
        if daily >= 6:
            raise ValueError("Batas 6 preview baru per 24 jam tercapai. Preview yang sudah ada tetap bisa diputar.")
        conn.execute("INSERT INTO music_voice_previews (preview_id,user_id,request_key,spec,created_at,updated_at) "
                     "VALUES (?,?,?,?,?,?)", (preview_id, user_id, key, serialized, now, now))
    try:
        call = _function("design_preview").spawn(tags_for_design(spec), spec["seed"],
            os.environ.get("KLIPIN_HEARTMULA_MODAL_APP", "klipin-music"))
        _update(preview_id, user_id, status="processing", call_id=call.object_id)
    except Exception:
        _update(preview_id, user_id, status="failed", error="Preview gagal dimulai. Periksa layanan suara sebelum mencoba variasi lain.")
    return public(get(preview_id, user_id))


def poll(preview_id, user_id):
    import modal
    from modal_credentials import ensure_modal_credentials
    row = get(preview_id, user_id)
    if row["status"] not in ("queued", "processing"):
        return public(row)
    if time.time() - row["created_at"] > 3600:
        _update(preview_id, user_id, status="failed", error="Preview melewati batas waktu. Coba variasi lain.")
        return public(get(preview_id, user_id))
    if not row["call_id"]:
        if time.time() - row["created_at"] > 90:
            _update(preview_id, user_id, status="failed", error="Antrean terputus sebelum preview dimulai. Coba variasi lain.")
        return public(get(preview_id, user_id))
    try:
        ensure_modal_credentials()
        result = modal.FunctionCall.from_id(row["call_id"]).get(timeout=0)
    except TimeoutError:
        return public(row)
    except Exception as exc:
        # Transport failures must not discard a durable call that may still finish.
        if isinstance(exc, (ConnectionError, OSError)) or type(exc).__name__ in ("GRPCError", "StreamTerminatedError"):
            return public(row)
        _update(preview_id, user_id, status="failed", error="Pembuatan preview gagal. Coba variasi lain; lagu belum dibuat.")
        return public(get(preview_id, user_id))
    with _materialize_lock:
        row = get(preview_id, user_id)
        if row["status"] == "completed":
            return public(row)
        try:
            audio = base64.b64decode(result["audio_b64"], validate=True)
            if len(audio) > 4_000_000:
                raise ValueError("Preview terlalu besar")
            with wave.open(io.BytesIO(audio), "rb") as wav:
                duration = wav.getnframes() / wav.getframerate()
                if not 3 <= duration <= 30:
                    raise ValueError("Durasi preview tidak valid")
            sample = voice_personas.store_sample(user_id, preview_id, audio)
            metrics = {k: result[k] for k in ("model", "elapsed_sec", "gpu_sec") if k in result}
            _update(preview_id, user_id, status="completed", sample_name=sample,
                    duration=duration, metrics=json.dumps(metrics), error="")
        except Exception:
            _update(preview_id, user_id, status="failed", error="Audio preview tidak valid. Coba variasi lain.")
    return public(get(preview_id, user_id))


def audio(preview_id, user_id):
    row = get(preview_id, user_id)
    if row["status"] != "completed" or not row["sample_name"]:
        raise ValueError("Preview belum siap diputar.")
    return voice_personas.read_sample(user_id, preview_id, row["sample_name"])


def save(preview_id, user_id, name):
    row = get(preview_id, user_id)
    if row["status"] != "completed":
        raise ValueError("Dengarkan preview yang selesai sebelum menyimpan persona.")
    # Validate the exact audio still exists before committing its identity.
    audio(preview_id, user_id)
    spec = json.loads(row["spec"])
    with db._lock, db._connect() as conn:
        conn.execute("INSERT OR IGNORE INTO music_personas "
                     "(persona_id,user_id,name,vocal_gender,style_tags,notes,voice_ref_path,persona_type,created_at) "
                     "VALUES (?,?,?,?,?,?,?,?,?)", (preview_id, user_id, (name.strip() or "Penyanyi saya")[:80],
                     spec["vocal_gender"], tags_for_design(spec)[:300], row["spec"],
                     row["sample_name"], "synthetic", time.time()))
        conn.execute("UPDATE music_voice_previews SET saved_persona_id=? WHERE preview_id=? AND user_id=?",
                     (preview_id, preview_id, user_id))
    return db.get_music_persona(preview_id, user_id)


def convert_song(src: Path, reference: Path, output: Path, duration: float,
                 semi_tone_shift: int = 0, auto_f0: bool = False):
    result = _function("convert_song").remote(
        base64.b64encode(src.read_bytes()).decode("ascii"),
        base64.b64encode(reference.read_bytes()).decode("ascii"), duration,
        int(semi_tone_shift), bool(auto_f0))
    data = base64.b64decode(result["audio_b64"], validate=True)
    if len(data) < 1024:
        raise RuntimeError("Hasil konversi suara kosong.")
    output.write_bytes(data)
    return output
