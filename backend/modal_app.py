"""Modal GPU worker for the complete Klipin media pipeline.

Deploy from ``backend`` with::

    modal deploy modal_app.py

Inputs and outputs are exchanged through a persistent Modal Volume so large
videos never have to fit in a function argument or return value.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

import modal


APP_NAME = "klipin-media"
VOLUME_NAME = "klipin-media-jobs"
MOUNT = Path("/mnt/klipin")

# Competitive wall-clock budget: a full render job or a Studio edit must
# finish in ~10 minutes (download + transcript + analysis + GPU render).
# Override for exceptional one-off runs with RENDER_TIMEOUT_SEC.
RENDER_TIMEOUT_SEC = int(os.environ.get("RENDER_TIMEOUT_SEC", "600"))

app = modal.App(APP_NAME)
jobs_volume = modal.Volume.from_name(VOLUME_NAME, create_if_missing=True)


def _ignore_local(path: Path) -> bool:
    """Keep user media, caches, credentials and the local venv out of images."""
    blocked = {
        "venv", "output", "uploads", "data", ".cache", ".config",
        ".insightface", ".pytest_cache", ".test-runs", "backend_tmp", "tests",
        "__pycache__",
    }
    if blocked.intersection(path.parts):
        return True
    if any(part.startswith("klipin_out_") for part in path.parts):
        return True
    if path.name in {".env", "=1.0", "$null"}:
        return True
    return path.suffix.lower() in {".mp4", ".jpg", ".jpeg", ".pyc"}

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install(
        "ffmpeg", "fontconfig", "fonts-liberation", "fonts-noto-core",
        "fonts-noto-cjk", "fonts-symbola", "libgl1", "libglib2.0-0",
    )
    .pip_install_from_requirements("requirements.txt")
    .pip_install("faster-whisper>=1.0")
    .add_local_dir(
        ".", remote_path="/app", copy=True,
        ignore=_ignore_local,
    )
)


def _manifest(root: Path, result: dict | None = None) -> list[str]:
    keep_names = {"source.mp4", "source_meta.json", "transcript.json"}
    keep_suffixes = {".ass", ".jpg", ".jpeg", ".png"}
    discovered = {
        str(path.relative_to(root)).replace("\\", "/")
        for path in root.rglob("*")
        if path.is_file()
        and ".render_" not in path.parts
        and (
            path.name in keep_names
            or path.name.startswith("clip_")
            or path.suffix.lower() in keep_suffixes
        )
    }
    # Freshly written Modal Volume directories can briefly return a stale
    # recursive listing. Result metadata gives deterministic deliverable names.
    for clip in (result or {}).get("clips") or []:
        index = int(clip.get("index", 0)) + 1
        discovered.update({
            f"clip_{index}.mp4",
            f"clip_{index}.ass",
            f"thumbnails/thumb_{index}.jpg",
        })
    discovered.add("source_meta.json")
    return sorted(relative for relative in discovered
                  if (root / relative).is_file())


def _write_json_atomic(path: Path, value: dict) -> None:
    """Write recovery metadata without ever exposing a partial JSON file."""
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, default=str), encoding="utf-8")
    os.replace(temporary, path)


def _edit_manifest(work: Path) -> list[str]:
    """Deliverable files produced by an edit job, relative to its work dir.

    Unlike the full-pipeline manifest, edit jobs also produce export ZIPs and
    source.mp4 stays EXCLUDED (it is the input, never downloaded back).
    """
    keep_suffixes = {".mp4", ".zip", ".ass", ".jpg", ".jpeg", ".png", ".m4a"}
    return sorted(
        str(path.relative_to(work)).replace("\\", "/")
        for path in work.rglob("*")
        if path.is_file()
        and path.name != "source.mp4"
        and not path.name.endswith(".tmp")
        and path.suffix.lower() in keep_suffixes
    )


@app.function(image=image, cpu=1.0, memory=1024, timeout=120)
def health() -> dict:
    import sqlite3
    import subprocess
    import sys

    sys.path.insert(0, "/app")
    import db

    db.init()
    with sqlite3.connect(str(db.DB_PATH)) as conn:
        tables = [row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )]
    return {
        "revision": "modal-db-bootstrap-v2",
        "python": sys.version.split()[0],
        "ffmpeg": subprocess.check_output(
            ["ffmpeg", "-version"], text=True
        ).splitlines()[0],
        "transcript_cache": "transcript_cache" in tables,
    }


@app.function(
    image=image,
    gpu="L4",
    cpu=8.0,
    memory=16384,
    timeout=RENDER_TIMEOUT_SEC,
    scaledown_window=30,
    volumes={str(MOUNT): jobs_volume},
    secrets=[modal.Secret.from_name("klipin-backend-secrets")],
)
def render_pipeline_v2(job_id: str, source: dict, options: dict | None = None) -> dict:
    """Run one complete job on an L4 and return only small JSON metadata."""
    import sys

    sys.path.insert(0, "/app")
    os.environ.update({
        "KLIPIN_GPU": "1",
        "WHISPER_DEVICE": "cuda",
        "WHISPER_COMPUTE": "float16",
        "TRANSCRIBE_BACKEND": os.environ.get("MODAL_TRANSCRIBE_BACKEND", "economy"),
        "CLIP_RENDER_WORKERS": os.environ.get("MODAL_CLIP_RENDER_WORKERS", "3"),
        # Local scene classification remains active. Skip two Gemini vision
        # calls per clip by default; they were the main source of 503 waits.
        "KLIPIN_FAST_RENDER": os.environ.get("MODAL_FAST_RENDER", "1"),
        "KLIPIN_FUSED_RENDER": os.environ.get("MODAL_FUSED_RENDER", "1"),
        # The model is shipped with the application image. Without this path,
        # Ultralytics downloads the same 6 MB weight on every cold container.
        "YOLO_MODEL_PATH": "/app/yolov8n.pt",
    })

    import db
    from pipeline import run_pipeline

    # The remote container uses SQLite only for transient transcript/model
    # caches. Project ownership, billing and durable job state stay local.
    db.init()
    # Warm containers keep their previous Volume snapshot until explicitly
    # reloaded. Inputs are uploaded by the API immediately before invocation.
    jobs_volume.reload()

    root = MOUNT / "jobs" / job_id
    output = root / "output"
    output.mkdir(parents=True, exist_ok=True)
    remote_source = dict(source)
    if remote_source.get("type") == "upload":
        remote_source["path"] = str(root / "input" / "source.mp4")
    elif remote_source.get("type") == "url_relay":
        source_root = (MOUNT / "sources").resolve()
        cached_source = Path(str(remote_source.get("path") or "")).resolve()
        if not cached_source.is_relative_to(source_root):
            raise RuntimeError("Unsafe Modal source-cache path")
        if not cached_source.is_file():
            raise RuntimeError("Cached Modal source is missing")
        remote_source["path"] = str(cached_source)

    logs: list[str] = []
    started = time.perf_counter()
    checkpoint_path = root / "result.checkpoint.json"

    def persist_core(result: dict) -> None:
        payload = {
            "result": result,
            "files": _manifest(output, result),
            "logs": logs[-500:],
            "modal_wall_sec": round(time.perf_counter() - started, 3),
            "gpu": "L4",
            "recovered_from_checkpoint": True,
        }
        _write_json_atomic(checkpoint_path, payload)
        jobs_volume.commit()

    try:
        result = run_pipeline(
            remote_source, str(output), options or {}, logs.append,
            checkpoint=persist_core)
    except Exception as exc:  # noqa: BLE001 - RPC serialization boundary
        # Some provider exceptions (notably yt-dlp DownloadError) retain a
        # traceback object and cannot be pickled by Modal. Return a plain JSON
        # failure envelope so the API receives the real actionable message.
        failure = {
            "error": {
                "type": type(exc).__name__,
                "message": str(exc),
                "retryable": False,
            },
            "logs": logs[-500:],
            "modal_wall_sec": round(time.perf_counter() - started, 3),
            "gpu": "L4",
        }
        _write_json_atomic(root / "failure.json", failure)
        jobs_volume.commit()
        return failure
    payload = {
        "result": result,
        "files": _manifest(output, result),
        "logs": logs[-500:],
        "modal_wall_sec": round(time.perf_counter() - started, 3),
        "gpu": "L4",
    }
    _write_json_atomic(root / "result.json", payload)
    jobs_volume.commit()
    return payload


@app.function(
    image=image,
    gpu="L4",
    cpu=8.0,
    memory=16384,
    timeout=RENDER_TIMEOUT_SEC,
    scaledown_window=30,
    volumes={str(MOUNT): jobs_volume},
    secrets=[modal.Secret.from_name("klipin-backend-secrets")],
)
def edit_pipeline_v2(job_id: str, edit: dict) -> dict:
    """Run ONE Studio edit job (retrim/rerender/cut/export/caption_edit) on an
    L4 instead of the API host's CPU.

    Same production contract as render_pipeline_v2: NVENC encode, three clip
    workers, fast render, and the portrait guardian still validates every
    output. Durable state stays on the API host: the remote SQLite is seeded
    from the host's project snapshot (import_project_state) and every write
    the edit performs is CAPTURED (db.start_capture) and replayed host-side.
    """
    import sys

    sys.path.insert(0, "/app")
    os.environ.update({
        "KLIPIN_GPU": "1",
        "CLIP_RENDER_WORKERS": os.environ.get("MODAL_CLIP_RENDER_WORKERS", "3"),
        "KLIPIN_FAST_RENDER": os.environ.get("MODAL_FAST_RENDER", "1"),
        "YOLO_MODEL_PATH": "/app/yolov8n.pt",
    })

    import db
    db.init()
    # Seed the throwaway remote SQLite BEFORE capture mode: import_project_state
    # itself writes (save_project + clip insert) and must actually execute here,
    # while every write performed by the edit ops below gets captured instead.
    if edit.get("project"):
        db.import_project_state(edit["project"], edit.get("clips") or [])
        pack = str(edit.get("free_pack") or "")
        db.seed_billing_state(
            str((edit.get("project") or {}).get("user_id", "")), pack)
    captured = db.start_capture()
    jobs_volume.reload()

    mount = MOUNT.resolve()
    source_volume = Path(str(edit.get("source_volume_path") or "")).resolve()
    if source_volume != mount and not source_volume.is_relative_to(mount):
        raise RuntimeError("Unsafe Modal edit source path")
    if not source_volume.is_file():
        raise RuntimeError("Modal edit source is missing on the Volume")

    edit_root = MOUNT / "jobs" / job_id / "edit_out"
    work = edit_root / job_id
    work.mkdir(parents=True, exist_ok=True)
    local_source = work / "source.mp4"
    if source_volume != local_source.resolve():
        try:
            os.link(source_volume, local_source)  # same Volume → instant
        except OSError:
            shutil.copyfile(source_volume, local_source)

    kind = str(edit.get("kind", ""))
    logs: list[str] = []
    started = time.perf_counter()
    try:
        import ops
        if kind == "retrim":
            result = ops.run_retrim(
                job_id, int(edit["clip_idx"]), float(edit["start"]),
                float(edit["end"]), edit.get("overrides") or {},
                str(edit_root), logs.append)
        elif kind == "caption_edit":
            result = ops.run_caption_edit(
                job_id, int(edit["clip_idx"]),
                [str(line) for line in edit.get("lines") or []],
                str(edit_root), logs.append)
        elif kind == "rerender":
            result = ops.run_rerender(job_id, str(edit_root), logs.append)
        elif kind == "cut":
            from cut import run_cut
            result = run_cut(job_id, edit.get("ranges") or [],
                             str(edit_root), edit.get("options") or {},
                             logs.append)
        elif kind == "export":
            result = ops.run_export(
                job_id, edit.get("indices") or [],
                edit.get("resolution") or "", str(edit_root), logs.append,
                aspect=edit.get("aspect") or "")
        else:
            raise RuntimeError(f"Unknown remote edit kind: {kind}")
    except Exception as exc:  # noqa: BLE001 - RPC serialization boundary
        failure = {
            "error": {
                "type": type(exc).__name__,
                "message": str(exc),
                "retryable": False,
            },
            "logs": logs[-500:],
            "modal_wall_sec": round(time.perf_counter() - started, 3),
            "gpu": "L4",
        }
        _write_json_atomic(MOUNT / "jobs" / job_id / "edit_failure.json",
                           failure)
        jobs_volume.commit()
        return failure

    payload = {
        "result": result,
        "files": _edit_manifest(work),
        "logs": logs[-500:],
        "db_writes": captured,
        "modal_wall_sec": round(time.perf_counter() - started, 3),
        "gpu": "L4",
    }
    _write_json_atomic(MOUNT / "jobs" / job_id / "edit_result.json", payload)
    jobs_volume.commit()
    return payload


@app.local_entrypoint()
def smoke(job_id: str = ""):
    """Confirm deployment/auth, or execute a previously uploaded job."""
    if not job_id:
        print({"app": APP_NAME, "volume": VOLUME_NAME, "ready": True})
        return
    print(render_pipeline_v2.remote(job_id, {"type": "upload"}, {}))
