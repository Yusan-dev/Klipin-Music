"""Private, per-account storage for user-supplied singing voice references.

Samples are encrypted before they touch persistent storage. Clear audio exists
only in the request/job temp directory and is removed by its caller.
"""
from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from db import DATA_DIR

STORE_DIR = DATA_DIR / "music_voice_personas"
KEY_PATH = DATA_DIR / ".music_voice_persona.key"
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,100}$")


def _fernet() -> Fernet:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not KEY_PATH.exists():
        key = Fernet.generate_key()
        try:
            fd = os.open(str(KEY_PATH), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "wb") as handle:
                handle.write(key)
            try:
                KEY_PATH.chmod(0o600)
            except OSError:
                pass
    return Fernet(KEY_PATH.read_bytes().strip())


def _account_dir(user_id: str) -> Path:
    account_key = hashlib.sha256(str(user_id).encode("utf-8")).hexdigest()
    return STORE_DIR / account_key


def _sample_path(user_id: str, persona_id: str, stored_name: str) -> Path:
    if not _SAFE_ID.fullmatch(str(persona_id)) or not str(user_id):
        raise ValueError("Identitas persona tidak valid")
    expected = f"{persona_id}.voice.fernet"
    if stored_name != expected:
        raise ValueError("Referensi suara tidak valid")
    target = _account_dir(user_id) / expected
    if not target.resolve().is_relative_to(STORE_DIR.resolve()):
        raise ValueError("Lokasi referensi suara tidak valid")
    return target


def store_sample(user_id: str, persona_id: str, audio_bytes: bytes) -> str:
    """Encrypt and persist a normalized audio sample; return opaque DB name."""
    target_name = f"{persona_id}.voice.fernet"
    target = _sample_path(user_id, persona_id, target_name)
    target.parent.mkdir(parents=True, exist_ok=True)
    encrypted = _fernet().encrypt(audio_bytes)
    fd, temp_name = tempfile.mkstemp(prefix=".voice-", dir=str(target.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encrypted)
        try:
            os.chmod(temp_name, 0o600)
        except OSError:
            pass
        os.replace(temp_name, target)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    return target_name


def decrypt_sample(user_id: str, persona_id: str, stored_name: str,
                   destination: Path) -> Path:
    """Decrypt to a caller-owned temporary path for an active conversion only."""
    clear = read_sample(user_id, persona_id, stored_name)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(clear)
    try:
        destination.chmod(0o600)
    except OSError:
        pass
    return destination


def read_sample(user_id: str, persona_id: str, stored_name: str) -> bytes:
    """Return decrypted bytes only after the caller authenticates ownership."""
    source = _sample_path(user_id, persona_id, stored_name)
    if not source.is_file():
        raise FileNotFoundError("Rekaman persona tidak ditemukan")
    try:
        return _fernet().decrypt(source.read_bytes())
    except InvalidToken as exc:
        raise RuntimeError("Kunci enkripsi persona berubah atau file rusak") from exc


def delete_sample(user_id: str, persona_id: str, stored_name: str) -> bool:
    """Remove only the exact encrypted sample belonging to this account."""
    source = _sample_path(user_id, persona_id, stored_name)
    try:
        source.unlink()
    except FileNotFoundError:
        return False
    try:
        source.parent.rmdir()
    except OSError:
        pass
    return True
