"""Resolve which Modal credentials Klipin must call Modal with.

Two failures made the Music panel look like it silently did nothing:

1. The API container kept a **stale** ``MODAL_TOKEN_ID`` / ``MODAL_TOKEN_SECRET``
   after the Modal token was rotated. Compose only re-reads ``env_file`` when a
   container is recreated, and ``python-dotenv`` never overrides a variable that
   already exists in the process, so a token for a workspace that ran out of
   credit stayed in charge and every job died in under a second with the opaque
   error ``workspace ac-… is disabled``.
2. That error was technical, so the panel had nothing actionable to show.

``ensure_modal_credentials()`` therefore resolves the *current* token right
before a worker call, with the precedence documented in ``_resolve``, and
``friendly_modal_error()`` turns Modal's exceptions into one Indonesian
sentence that names the fix.
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent

try:  # Python 3.11+
    import tomllib as _toml
except ModuleNotFoundError:  # pragma: no cover - 3.10 fallback
    _toml = None


def _toml_candidates() -> list[Path]:
    """Files that may hold a ``[profile]`` table with Modal tokens."""
    candidates = []
    for env_key in ("MODAL_CONFIG_FILE",):
        value = os.environ.get(env_key, "").strip()
        if value:
            candidates.append(Path(value))
    candidates += [
        Path.home() / ".modal.toml",
        ROOT / ".modal.toml",
        Path("/app/.modal.toml"),
        Path("/home/klipin/.modal.toml"),
        Path("/app/.config/modal.toml"),
    ]
    return candidates


def _dotenv_value(key: str) -> str:
    """Read one key from the app's ``.env`` (used only for explicit hints)."""
    try:
        from dotenv import dotenv_values
    except Exception:  # pragma: no cover - dotenv always installed in prod
        return ""
    for candidate in (ROOT / ".env", Path("/app/.env")):
        if not candidate.is_file():
            continue
        try:
            value = dotenv_values(candidate).get(key)
        except Exception:  # noqa: BLE001 - a broken .env must never crash jobs
            continue
        if value:
            return str(value).strip()
    return ""


def load_profiles() -> dict[str, dict]:
    """``{profile_name: {token_id, token_secret, active}}`` from the first
    readable ``.modal.toml``. Empty dict when Modal was never configured."""
    if _toml is None:  # pragma: no cover
        return {}
    for path in _toml_candidates():
        if not str(path) or not path.is_file() or path.stat().st_size == 0:
            continue
        try:
            data = _toml.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        profiles = {name: value for name, value in data.items()
                    if isinstance(value, dict) and value.get("token_id")}
        if profiles:
            return profiles
    return {}


def requested_profile() -> str:
    """Explicit profile choice: process env first, then ``backend/.env``."""
    return (os.environ.get("MODAL_PROFILE", "").strip()
            or _dotenv_value("MODAL_PROFILE"))


def _resolve(profile: str | None = None) -> tuple[str, str, str, str]:
    """Return ``(token_id, token_secret, profile_name, source)``.

    Precedence — first hit wins:

    1. ``profile`` argument / ``MODAL_PROFILE`` -> that ``.modal.toml`` profile.
    2. the profile flagged ``active = true`` in ``.modal.toml`` (what
       ``modal token set`` maintains, so a rotation is picked up automatically).
    3. the first profile in ``.modal.toml``.
    4. ``MODAL_TOKEN_ID`` / ``MODAL_TOKEN_SECRET`` already in the environment.
    5. raise — with a message that says exactly which file to fix.
    """
    profiles = load_profiles()
    wanted = (profile or requested_profile()).strip()
    if wanted and wanted in profiles:
        entry = profiles[wanted]
        return str(entry["token_id"]), str(entry["token_secret"]), wanted, ".modal.toml"
    active = next((name for name, entry in profiles.items() if entry.get("active")),
                  "")
    if active:
        entry = profiles[active]
        return str(entry["token_id"]), str(entry["token_secret"]), active, ".modal.toml"
    if profiles:
        name, entry = next(iter(profiles.items()))
        return str(entry["token_id"]), str(entry["token_secret"]), name, ".modal.toml"
    token_id = os.environ.get("MODAL_TOKEN_ID", "").strip()
    token_secret = os.environ.get("MODAL_TOKEN_SECRET", "").strip()
    if token_id and token_secret:
        return token_id, token_secret, os.environ.get("MODAL_PROFILE", "").strip(), "environment"
    raise RuntimeError(
        "Kredensial Modal tidak ditemukan. Jalankan `modal token set` pada akun "
        "yang aktif, atau isi MODAL_TOKEN_ID dan MODAL_TOKEN_SECRET di "
        "backend/.env, lalu restart API (docker compose restart api)."
    )


def masked(token: str) -> str:
    """``ak-mLkN…GV0`` — safe to return to the UI or write to a log."""
    token = token or ""
    return f"{token[:7]}…{token[-3:]}" if len(token) > 12 else ("…" if token else "")


def ensure_modal_credentials(profile: str | None = None) -> dict:
    """Export the current Modal token into the environment for this process.

    Safe to call before every worker interaction: it is a dict lookup plus a
    couple of small file reads, and it repairs the stale-variable case that a
    plain ``load_dotenv()`` cannot fix.
    """
    token_id, token_secret, name, source = _resolve(profile)
    os.environ["MODAL_TOKEN_ID"] = token_id
    os.environ["MODAL_TOKEN_SECRET"] = token_secret
    if name:
        os.environ["MODAL_PROFILE"] = name
    return {"profile": name, "source": source, "token_id": masked(token_id)}



def _workspace_from(raw: str) -> str:
    """``workspace ac-xxx is disabled`` -> ``ac-xxx``."""
    marker = "workspace "
    low = raw.lower()
    if marker not in low:
        return ""
    tail = raw[low.index(marker) + len(marker):]
    return tail.split()[0].strip() if tail.split() else ""


def friendly_modal_error(exc: BaseException, app: str = "", function: str = "") -> str:
    """One actionable Indonesian sentence replacing Modal's raw exception.

    The UI prints this verbatim, so it always ends with what to do next.
    """
    raw = str(exc).strip() or exc.__class__.__name__
    low = raw.lower()
    target = app or "worker"
    if "is disabled" in low:
        workspace = _workspace_from(raw) or "tidak diketahui"
        return (
            f"Akun Modal yang dipakai sudah tidak aktif: workspace {workspace} "
            "dinonaktifkan atau kreditnya habis. Perbarui MODAL_TOKEN_ID dan "
            "MODAL_TOKEN_SECRET di backend/.env dengan token dari akun Modal yang "
            "masih aktif (atau set MODAL_PROFILE ke nama profil di ~/.modal.toml), "
            "lalu restart API: docker compose restart api. "
            f"Detail Modal: {raw}"
        )
    if "not found in environment" in low or "lookup failed" in low:
        return (
            f"Worker Modal '{target}' belum ada di akun Modal yang aktif"
            f"{f' ({function})' if function else ''}. Deploy dulu dengan profil yang "
            "sama: `modal deploy modal_app.py` (video) atau "
            "`modal deploy music_modal_app.py` (musik). "
            f"Detail Modal: {raw}"
        )
    if "unauthenticated" in low or ("invalid" in low and "token" in low):
        return (
            "Token Modal ditolak (unauthorized). Isi ulang MODAL_TOKEN_ID dan "
            "MODAL_TOKEN_SECRET di backend/.env, lalu restart API. "
            f"Detail Modal: {raw}"
        )
    if "insufficient" in low or "billing" in low or "quota" in low or "balance" in low:
        return (
            f"Kredit/kuota akun Modal habis untuk worker '{target}'. Top up atau "
            "pindah ke profil Modal lain lewat MODAL_PROFILE, lalu restart API. "
            f"Detail Modal: {raw}"
        )
    if "hit its timeout" in low or "timed out" in low or "timeout" in low:
        return (
            f"Worker Modal '{target}' melewati batas waktu. Coba durasi lagu yang "
            "lebih pendek atau ulangi job. "
            f"Detail Modal: {raw}"
        )
    if "out of memory" in low or "cuda out of memory" in low or "oom" in low:
        return (
            f"Worker Modal '{target}' kehabisan GPU memory. Ulangi job — GPU akan "
            f"dialokasikan ulang. Detail Modal: {raw}"
        )
    return f"Worker Modal '{target}' gagal: {raw}"


def modal_worker_status(app: str, function: str = "generate_song") -> dict:
    """Best-effort preflight for the UI: can this app be reached right now?

    Returns ``{"ready": bool, "profile": str, "message": str, "error": str}``.
    Never raises: an unreachable worker is a UI state, not a crash.
    """
    try:
        info = ensure_modal_credentials()
    except RuntimeError as exc:
        return {"ready": False, "profile": "", "message": str(exc), "error": str(exc)}
    try:
        import modal
        handle = modal.Function.from_name(app, function)
        handle.get_current_stats()
        return {"ready": True, "profile": info["profile"],
                "message": f"Worker {app} siap (profil Modal: {info['profile'] or info['source']}).",
                "error": ""}
    except Exception as exc:  # noqa: BLE001 - preflight must never raise
        message = friendly_modal_error(exc, app=app, function=function)
        return {"ready": False, "profile": info["profile"], "message": message,
                "error": str(exc)}
