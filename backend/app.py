"""Klipin backend — FastAPI server with async job queue.

Job kinds:
  process — full pipeline (source -> transcription -> clips -> render)
  retrim  — re-render a single clip with new bounds / caption overrides
  export  — bundle selected clips into a ZIP

Completed projects are mirrored into SQLite (db.py) so the frontend library
survives restarts; files are kept until the user deletes the project.
"""
import asyncio
import hashlib
import hmac
import json
import os

# Ensure ffmpeg/ffprobe from imageio-ffmpeg are on PATH (for systems
# without a system-wide ffmpeg install).
try:
    import imageio_ffmpeg
    _ffdir = os.path.dirname(imageio_ffmpeg.get_ffmpeg_exe())
    os.environ["PATH"] = _ffdir + os.pathsep + os.environ.get("PATH", "")
except Exception:
    pass
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import auth
import db
import middleware_security
from gpu import encode_args

load_dotenv()
db.init()

ROOT = Path(__file__).resolve().parent  # backend/
UPLOAD_DIR = ROOT / "uploads"
OUTPUT_DIR = ROOT / "output"
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

MAX_CONCURRENT_JOBS = int(os.environ.get("MAX_CONCURRENT_JOBS", "5"))
JOB_RETENTION_SECONDS = int(os.environ.get("JOB_RETENTION_SECONDS", "3600"))
# Non-terminal jobs (queued/processing/retry) that never reach a terminal state
# (a hung worker, a cancelled task my watchdog missed) must also expire, or they
# accumulate in memory forever. Three retention windows is safely past any
# legitimate retry/timeout budget.
JOB_STALE_SECONDS = int(os.environ.get(
    "JOB_STALE_SECONDS", str(JOB_RETENTION_SECONDS * 3)))
MAX_FILE_SIZE_MB = 2048

# ── signed media URLs ──────────────────────────────────────────────────────
# /videos files are served with a short-lived HMAC signature (exp + sig) so a
# shared clip link stops working after MEDIA_TTL seconds. Set KLIPIN_MEDIA_SECRET
# for stable signatures across restarts; otherwise a per-process secret is used
# (old links die on restart, which is fine — the frontend re-fetches project
# data and gets freshly signed URLs).
MEDIA_SECRET = os.environ.get("KLIPIN_MEDIA_SECRET") or secrets.token_hex(32)
MEDIA_TTL = int(os.environ.get("KLIPIN_MEDIA_TTL", str(6 * 3600)))
MEDIA_KEYS = ("video_url", "subtitle_url", "thumbnail_url", "zip_url", "thumb")


def _media_sig(path: str, exp: int) -> str:
    msg = f"{path}|{exp}".encode()
    return hmac.new(MEDIA_SECRET.encode(), msg, hashlib.sha256).hexdigest()


def _sign_media_url(url: str, ttl: int | None = None) -> str:
    """Append `?exp=<unix>&sig=<hmac>` to a /videos URL."""
    exp = int(time.time()) + (ttl if ttl is not None else MEDIA_TTL)
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}exp={exp}&sig={_media_sig(url, exp)}"


def _verify_media_sig(path: str, exp, sig: str) -> bool:
    """Constant-time check of exp + HMAC over the exact request path."""
    try:
        exp = int(exp)
    except (TypeError, ValueError):
        return False
    if exp < int(time.time()):
        return False
    expected = _media_sig(path, exp)
    return hmac.compare_digest(expected, sig)


def _sign_media_json(obj) -> bool:
    """Recursively sign /videos/ media URLs inside a JSON structure (in-place).
    Returns True when at least one URL was signed.

    Any string starting with /videos/ is signed regardless of the key or
    nesting (dict value under any key, or a bare string inside a list), so
    media URLs stored in unexpected fields are not missed. Already-signed
    URLs are left untouched (idempotent), and external URLs are never
    modified."""
    hit = False
    if isinstance(obj, dict):
        for k, v in list(obj.items()):
            if isinstance(v, str) and v.startswith("/videos/") and "exp=" not in v:
                obj[k] = _sign_media_url(v)
                hit = True
            else:
                hit = _sign_media_json(v) or hit
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            if isinstance(v, str) and v.startswith("/videos/") and "exp=" not in v:
                obj[i] = _sign_media_url(v)
                hit = True
            else:
                hit = _sign_media_json(v) or hit
    return hit

jobs: dict = {}
_jobs_lock = threading.RLock()
_semaphore = asyncio.Semaphore(MAX_CONCURRENT_JOBS)
# Music generation fans out one GPU task per take (A/B) and used to run
# unbounded, so a burst of requests could multiply Modal L4 jobs and its cost
# with no ceiling. A dedicated semaphore bounds concurrent music renders
# without letting a long song queue starve the video render pool.
MAX_CONCURRENT_MUSIC_JOBS = int(os.environ.get("MAX_CONCURRENT_MUSIC_JOBS", "3"))
_music_semaphore = asyncio.Semaphore(MAX_CONCURRENT_MUSIC_JOBS)
# A long song can legitimately spend 20+ minutes on Modal + local mastering.
# Past this window a queued/processing row with no artifact is treated as an
# orphaned task and failed so the UI stops polling forever.
MUSIC_JOB_STALE_SECONDS = int(os.environ.get(
    "KLIPIN_MUSIC_STALE_SECONDS", str(45 * 60)))
# asyncio only keeps a weak reference to a task created with create_task, so an
# un-referenced music task can be garbage-collected mid `await` while the Modal
# worker is still running. That left the DB row stuck in `processing` even
# though the GPU had finished. Hold a strong reference until the task is done.
_music_tasks: set = set()


def _spawn_music_job(job_id: str, spec: dict, user_id: str):
    """Schedule a music take while keeping a strong task reference."""
    task = asyncio.create_task(_run_music_job(job_id, spec, user_id))
    if task is not None:
        _music_tasks.add(task)
        task.add_done_callback(_music_tasks.discard)
    return task


def _process_options_for_user(options: dict | None, user: dict) -> dict:
    """Apply server-authoritative generation entitlements to job options."""
    from billing import output_clip_limit
    effective = dict(options or {})
    entitlement = output_clip_limit(user["google_sub"], user)
    try:
        requested = int(effective.get("render_count", entitlement))
    except (TypeError, ValueError):
        requested = entitlement
    # Product choices are intentionally discrete. The server remains
    # authoritative: a free account cannot unlock six clips by editing JSON.
    requested = requested if requested in (3, 6) else entitlement
    effective["render_count"] = min(requested, entitlement)
    effective["min_output_score"] = 90
    return effective


app = FastAPI(title="Klipin API", version="0.3.0")

# Register security headers, rate-limiting, and honeytrap middleware
middleware_security.register_middleware(app)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:5174",
        "https://klip-in.id",
        "https://www.klip-in.id",
    ],
    # Phone/remote access via ad-hoc tunnels (trycloudflare / ngrok free):
    # the web origin is a random subdomain each run, so match by pattern.
    # Tunnel domains are attacker-registrable, so we do NOT reflect credentials
    # for them: auth is a Bearer header (never a cookie), so
    # allow_credentials=False costs us nothing and denies any ambient-cookie
    # credential to a hostile origin on a shared tunnel domain.
    allow_origin_regex=r"https://([a-z0-9-]+\.)(trycloudflare\.com|ngrok-free\.app|ngrok-free\.dev|ngrok\.io)",
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def _sign_media_in_responses(request, call_next):
    """Sign /videos media URLs in every JSON response.

    The frontend renders <video>/<img>/download links straight from the URLs
    in API payloads (clip.video_url, thumbnail_url, zip_url, …). Signing them
    here means the app can serve media with expiring links WITHOUT the client
    ever putting a session token in a URL, and no endpoint needs to know about
    signing. Non-JSON responses pass through untouched.
    """
    response = await call_next(request)
    if not response.headers.get("content-type", "").startswith("application/json"):
        return response
    try:
        # NOTE: inside BaseHTTPMiddleware, `call_next` hands back a
        # _StreamingResponse — there is no `.body()` to await. Consume the
        # body_iterator instead (buffering JSON payloads is fine; they are
        # small). Without this, the except below silently swallowed the
        # AttributeError and media URLs were NEVER signed in production.
        chunks = [c async for c in response.body_iterator]
        data = json.loads(b"".join(chunks))
    except Exception:  # noqa: BLE001 — never break a response over signing
        return response
    # ALWAYS rebuild: the body_iterator was consumed above, so returning the
    # original response would send an EMPTY body even when nothing was signed.
    _sign_media_json(data)
    return JSONResponse(
        data, status_code=response.status_code,
        headers={k: v for k, v in response.headers.items()
                 if k.lower() not in ("content-length", "content-type")},
    )


async def require_user(request: Request):
    """FastAPI dependency: 401 unless a valid Bearer session token is sent."""
    token = _bearer_token(request)
    user = auth.user_from_token(token)
    if not user:
        raise HTTPException(status_code=401,
                            detail="Authentication required — sign in with Google")
    return user


def _bearer_token(request: Request) -> str:
    header = request.headers.get("Authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return ""


def _own_project(job_id: str, user_id: str) -> dict:
    """Return the project iff it belongs to `user_id`, else 404 (no existence
    leak). Legacy rows with an empty owner are treated as orphaned — they are
    not reachable through the API."""
    project = db.get_project(job_id, user_id=user_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


class ProcessRequest(BaseModel):
    url: str | None = None
    platform: str | None = None  # "" (auto) | youtube | tiktok | instagram
    options: dict | None = None


class RetrimRequest(BaseModel):
    start: float
    end: float
    options: dict | None = None  # optional caption-style overrides
    # Manual subject lock: normalized [x, y] (0..1) click point marking the
    # face to follow, or [] to clear an existing lock. Persisted per clip.
    subject_lock: list[float] | None = None


class LayoutPlanRequest(BaseModel):
    """Per-scene layout editor payload.

    plan: scenes in CLIP-LOCAL seconds [{start, end, layout}], sorted,
    non-overlapping. layout is one of the allowed render strategies.
    Omit plan (or send []) to reset to the AI-generated plan.
    """
    plan: list[dict] | None = None


class LayerPlanRequest(BaseModel):
    """Non-destructive editor layers in clip-local seconds/normalized space."""
    layers: list[dict] = []


class CaptionEditRequest(BaseModel):
    lines: list[str]  # one corrected caption text per block, in block order


class ExportRequest(BaseModel):
    indices: list[int] | None = None
    resolution: str | None = None  # "720" | "1080" | None (source)
    aspect: str | None = None      # "9:16" | "1:1"; legacy "16:9" is forced portrait
    # Approval workflow: when True, only clips with approval_status='approved'
    # are exported (intersection with `indices`).
    only_approved: bool = False


class ApprovalRequest(BaseModel):
    """Per-clip review decision for the agency/team approval workflow."""
    status: str  # approved | changes_requested | pending | "" (clear)
    note: str = ""


class ClipMetaRequest(BaseModel):
    title: str | None = None
    hook: str | None = None


class OptionsPatchRequest(BaseModel):
    """Partial options update from Klipin Studio (subtitle colors, animation,
    words_per_line, caption_style, etc.). Only supplied keys overwrite."""
    options: dict = {}


class PublishRequest(BaseModel):
    platform: str  # youtube | tiktok | instagram
    title: str | None = None
    description: str | None = None
    privacy: str | None = None
    scheduled_at: float | None = None  # epoch seconds; future = schedule, else publish now


class PublishIdeasRequest(BaseModel):
    platform: str  # youtube | tiktok | instagram


class HookVariantSelectRequest(BaseModel):
    text: str  # the variant hook to apply as the clip's active hook


class DubRequest(BaseModel):
    target_lang: str = "en"  # id | en | ms | zh | ja | ko | es | ar | hi | th | vi | pt | ru | tr | fr | de
    voice: str | None = None


class ThumbnailRequest(BaseModel):
    count: int = 3      # 1..4 thumbnails per request
    extra_prompt: str | None = None
    thumbnail_format: str = "shorts"  # portrait only; legacy values normalize
    include_text: bool = True


class CleanRequest(BaseModel):
    remove_fillers: bool = True
    remove_silences: bool = True
    remove_bad_takes: bool = False  # Gemini AI: cut stumbles, false starts, repetitions


class SfxRequest(BaseModel):
    volume: float = 0.3  # 0.0-1.0, SFX loudness vs original audio


class BrollRequest(BaseModel):
    count: int = 2  # 1-4 b-roll windows per clip


class VoiceoverRequest(BaseModel):
    voice: str = ""  # edge-tts voice name (auto-detect from language if empty)
    mix_ratio: float = 0.15  # original audio volume (0.0=replace, 1.0=no change)


class EnhanceRequest(BaseModel):
    strength: int = 2  # 1-3


class OverlayTextRequest(BaseModel):
    kind: str = "text"  # text | image | audio
    text: str | None = None
    start: float = 0.0
    end: float = 2.0
    position: str = "bottom"  # top | center | bottom
    font_size: int = 64
    color: str = "#FFE500"
    width_pct: float = 60.0  # image only
    volume: float = 0.3      # audio only
    keep_original: bool = True


class RepickRequest(BaseModel):
    custom_prompt: str = ""  # natural-language criteria for re-selecting moments


class CutRequest(BaseModel):
    ranges: list[list[float]]  # [[start, end], ...] keep-ranges from source
    options: dict | None = None  # layout, aspect, caption overrides, title


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _new_job_id() -> str:
    """Unguessable job id (16 hex chars).

    The old `{timestamp:x}-{counter}` ids were sequential and trivially
    enumerable — combined with the (now closed) unauthenticated project
    endpoints that meant anyone could walk every user's videos. Random ids
    make the /videos media URLs effectively unguessable too.
    """
    return secrets.token_hex(8)


def _sink(job_id: str):
    def log(msg: str):
        with _jobs_lock:
            if job_id in jobs:
                jobs[job_id]["logs"].append(msg)
    return log


def _should_commit_credit(kind: str, result) -> bool:
    """Only charge full processing jobs that produced a usable result."""
    if kind != "process":
        return True
    return not (isinstance(result, dict) and
                result.get("status") == "no_viral_content")


def _retryable_job_error(exc: Exception) -> bool:
    """Classify transient provider/worker failures for bounded retries."""
    msg = str(exc).lower()
    if any(token in msg for token in ("corrupt", "invalid video", "unsupported codec",
                                      "permission denied", "no such file")):
        return False
    # Deterministic code bugs must NOT retry: re-running the same code burns
    # a full paid L4 run and fails identically 3x (e.g. UnboundLocalError on
    # 'precut_seconds' looped retry 3/3 in 15s). Retry is for transient
    # provider/network/worker faults only.
    if any(token in msg for token in ("cannot access local variable",
                                      "unboundlocalerror",
                                      "nameerror: name",
                                      "is not defined")):
        return False
    # Deterministic Modal task timeouts must NOT retry: the worker already
    # burned its full timeout budget (e.g. "hit its timeout of 3600s" for an
    # over-long source). A blind retry re-runs another full paid L4 hour that
    # times out the same way — this burned ~4-5h of Modal credit on a
    # 72-minute source (job 3a0dc5ef, 2026-09-05). Fail fast instead.
    if any(token in msg for token in ("hit its timeout", "hit the timeout",
                                      "timeout of 3600", "task timed out",
                                      "function timeout")):
        return False
    return any(token in msg for token in ("timeout", "timed out", "429", "rate limit",
                                          "503", "unavailable", "connection", "oom",
                                          "worker", "modal", "provider", "temporary"))


async def _run_job_attempt(job_id: str, spec: dict) -> float | None:
    """Run ONE attempt of a job inside the concurrency semaphore.

    spec = {"kind": "process"|"retrim"|"export", ...}. Returns the retry
    delay in seconds when the attempt should be retried, otherwise None once
    the job reached a terminal (completed/failed) state.
    """
    async with _semaphore:
        with _jobs_lock:
            if job_id not in jobs:
                # Project was deleted while queued; drop the job instead of
                # crashing on a missing entry (and don't burn a render).
                return None
            jobs[job_id]["status"] = "processing"
            jobs[job_id]["logs"].append(
                f"[{_now()}] worker aktif — job {job_id} mulai diproses")
        try:
            from modal_client import (REMOTE_EDIT_KINDS,
                                      enabled as modal_enabled)
            # A full `process` job and every Studio edit job (retrim, caption
            # edit, rerender-all, cut, export) belongs on the Modal L4 worker.
            # The host CPU is only a fallback when the remote dispatch fails.
            dispatched_remote = modal_enabled() and spec["kind"] == "process"
            handled_remote = False
            if dispatched_remote:
                from modal_client import run_remote_pipeline
                # options and spec parsed below or above
                opts = dict(spec.get("options") or {})
                if spec.get("user_id"):
                    opts["_user_id"] = spec["user_id"]
                try:
                    result = await asyncio.to_thread(
                        run_remote_pipeline, job_id, spec["source"],
                        str(OUTPUT_DIR / job_id), opts, _sink(job_id),
                    )
                except Exception as remote_exc:  # noqa: BLE001
                    # Recovery-first: the worker often finishes the render and
                    # only the finalize/download stumbled. Re-downloading the
                    # finished result takes seconds; a blind full re-run costs
                    # another ~7 minutes per attempt (the 20-minute hang).
                    from modal_client import recover_remote_result
                    try:
                        recovered = await asyncio.to_thread(
                            recover_remote_result, job_id,
                            str(OUTPUT_DIR / job_id), _sink(job_id),
                        )
                    except Exception:
                        recovered = None
                    if recovered is None:
                        raise remote_exc
                    result = recovered
            elif modal_enabled() and spec["kind"] in REMOTE_EDIT_KINDS:
                from modal_client import run_remote_edit
                try:
                    result = await asyncio.to_thread(
                        run_remote_edit, job_id, spec, str(OUTPUT_DIR),
                        _sink(job_id),
                    )
                    handled_remote = True
                except Exception as exc:  # noqa: BLE001 - keep edits available
                    _sink(job_id)(
                        f"⚠️ Modal edit gagal ({exc}) — fallback render lokal")
            if not dispatched_remote and not handled_remote:
                from pipeline import run_pipeline
            from ops import (run_retrim, run_export, run_publish, run_repick,
                             run_rerender, run_caption_edit)
            from effects import run_effect
            from thumbnail import run_thumbnail
            from faceswap import run_faceswap
            from filler import run_clean
            from sfx import run_sfx
            from dub import run_dub
            from cut import run_cut
            from broll import run_broll
            from enhance import run_enhance
            from overlay import run_layer_plan, run_overlay, run_proxy

            kind = spec["kind"]
            if dispatched_remote or handled_remote:
                # The remote worker already produced the complete result.
                pass
            elif kind == "retrim":
                result = await asyncio.to_thread(
                    run_retrim, spec["job_id"], spec["clip_idx"],
                    spec["start"], spec["end"], spec.get("options"),
                    str(OUTPUT_DIR), _sink(job_id),
                )
            elif kind == "export":
                result = await asyncio.to_thread(
                    run_export, spec["job_id"], spec.get("indices") or [],
                    spec.get("resolution"), str(OUTPUT_DIR), _sink(job_id),
                    aspect=spec.get("aspect"),
                )
            elif kind == "publish":
                result = await asyncio.to_thread(
                    run_publish, spec["job_id"], spec["clip_idx"],
                    spec["platform"], spec.get("title", ""),
                    spec.get("description", ""), spec.get("privacy", ""),
                    str(OUTPUT_DIR), _sink(job_id),
                    user_id=spec.get("user_id", ""),
                )
            elif kind == "scheduled_publish":
                result = await asyncio.to_thread(
                    run_publish, spec["job_id"], spec["clip_idx"],
                    spec["platform"], spec.get("title", ""),
                    spec.get("description", ""), spec.get("privacy", ""),
                    str(OUTPUT_DIR), _sink(job_id),
                    publish_id=spec.get("publish_id"),
                    user_id=spec.get("user_id", ""),
                )
            elif kind == "thumbnail":
                result = await asyncio.to_thread(
                    run_thumbnail, spec["job_id"], spec["clip_idx"],
                    spec.get("count", 3), spec.get("extra_prompt", ""),
                    str(OUTPUT_DIR), _sink(job_id),
                    spec.get("thumbnail_format", "shorts"),
                    spec.get("include_text", True),
                )
            elif kind == "faceswap":
                result = await asyncio.to_thread(
                    run_faceswap, spec["job_id"], spec["clip_idx"],
                    spec["face_image_path"], str(OUTPUT_DIR), _sink(job_id),
                )
            elif kind == "clean":
                result = await asyncio.to_thread(
                    run_clean, spec["job_id"], spec["clip_idx"],
                    str(OUTPUT_DIR),
                    spec.get("remove_fillers", True),
                    spec.get("remove_silences", True),
                    spec.get("remove_bad_takes", False), _sink(job_id),
                )
            elif kind == "sfx":
                result = await asyncio.to_thread(
                    run_sfx, spec["job_id"], spec["clip_idx"],
                    str(OUTPUT_DIR), spec.get("volume", 0.3), _sink(job_id),
                )
            elif kind == "broll":
                result = await asyncio.to_thread(
                    run_broll, spec["job_id"], spec["clip_idx"],
                    str(OUTPUT_DIR), spec.get("count", 2), _sink(job_id),
                )
            elif kind == "enhance":
                result = await asyncio.to_thread(
                    run_enhance, spec["job_id"], spec["clip_idx"],
                    str(OUTPUT_DIR), spec.get("strength", 2), _sink(job_id),
                )
            elif kind == "voiceover":
                from voiceover import run_voiceover
                result = await asyncio.to_thread(
                    run_voiceover, spec["job_id"], spec["clip_idx"],
                    str(OUTPUT_DIR), spec.get("voice", ""),
                    spec.get("mix_ratio", 0.15), _sink(job_id),
                )
            elif kind == "overlay":
                result = await asyncio.to_thread(
                    run_overlay, spec["job_id"], spec["clip_idx"],
                    str(OUTPUT_DIR), spec.get("overlay") or {}, _sink(job_id),
                )
            elif kind == "layer_plan":
                result = await asyncio.to_thread(
                    run_layer_plan, spec["job_id"], spec["clip_idx"],
                    str(OUTPUT_DIR), _sink(job_id),
                )
            elif kind == "proxy":
                result = await asyncio.to_thread(
                    run_proxy, spec["job_id"], spec["clip_idx"],
                    str(OUTPUT_DIR), _sink(job_id),
                )
            elif kind == "repick":
                result = await asyncio.to_thread(
                    run_repick, spec["job_id"], spec.get("custom_prompt", ""),
                    str(OUTPUT_DIR), _sink(job_id),
                )
            elif kind == "dub":
                result = await asyncio.to_thread(
                    run_dub, spec["job_id"], spec["clip_idx"],
                    spec.get("target_lang", "en"), spec.get("voice", ""),
                    str(OUTPUT_DIR), _sink(job_id),
                )
            elif kind == "cut":
                result = await asyncio.to_thread(
                    run_cut, spec["job_id"], spec.get("ranges") or [],
                    str(OUTPUT_DIR), spec.get("options") or {}, _sink(job_id),
                )
            elif kind == "rerender":
                result = await asyncio.to_thread(
                    run_rerender, spec["job_id"], str(OUTPUT_DIR),
                    _sink(job_id),
                )
            elif kind == "caption_edit":
                result = await asyncio.to_thread(
                    run_caption_edit, spec["job_id"], spec["clip_idx"],
                    spec.get("lines") or [], str(OUTPUT_DIR), _sink(job_id),
                )
            elif kind == "effect":
                result = await asyncio.to_thread(
                    run_effect, spec["job_id"], spec["clip_idx"],
                    spec.get("effect"), str(OUTPUT_DIR), _sink(job_id),
                )
            else:
                opts = dict(spec.get("options") or {})
                if spec.get("user_id"):
                    opts["_user_id"] = spec["user_id"]
                result = await asyncio.to_thread(
                    run_pipeline, spec["source"], str(OUTPUT_DIR / job_id),
                    opts, _sink(job_id),
                )
            with _jobs_lock:
                if job_id in jobs:
                    jobs[job_id]["status"] = "completed"
                    jobs[job_id]["result"] = result
            if spec.get("user_id"):
                # A pipeline may finish technically without producing a usable
                # deliverable (for example ``no_viral_content``).  That is not
                # a successful paid generation: users must only spend a credit
                # when they receive rendered clips.
                from billing import commit, release
                if _should_commit_credit(kind, result):
                    commit(spec["user_id"], job_id)
                else:
                    release(spec["user_id"], job_id)
            # Optional S3/R2 cloud backup of rendered clips (fire-and-forget).
            if kind == "process":
                try:
                    from cloud import backup_project
                    asyncio.create_task(asyncio.to_thread(
                        backup_project, spec["job_id"],
                        str(OUTPUT_DIR / spec["job_id"]), _sink(job_id)))
                except Exception:  # noqa: BLE001 — backup is best-effort
                    pass
            return None
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            with _jobs_lock:
                attempt = int(jobs.get(job_id, {}).get("attempt", 0) or 0)
            if spec.get("kind") == "process" and attempt < 3 and _retryable_job_error(e):
                delay = (1, 5, 15)[attempt]
                # Always attach the root cause: a bare "transient failure"
                # line made the 20-minute hang undiagnosable from the console.
                cause = str(e)[:500]
                with _jobs_lock:
                    if job_id in jobs:
                        jobs[job_id]["attempt"] = attempt + 1
                        jobs[job_id]["status"] = "queued_retry"
                        jobs[job_id]["logs"].append(
                            f"⚠️ transient failure; retry {attempt + 1}/3 "
                            f"in {delay}s ({cause})")
                # Retry is scheduled by _run_job AFTER this attempt has released
                # the semaphore — sleeping here would hold the permit and could
                # dead-lock the pool when all permits need to retry.
                return delay
            with _jobs_lock:
                if job_id in jobs:
                    jobs[job_id]["status"] = "failed"
                    jobs[job_id]["error"] = str(e)
                    jobs[job_id]["logs"].append(f"❌ {e}")
                    if "hit its timeout" in str(e).lower():
                        jobs[job_id]["logs"].append(
                            "💡 Worker kehabisan batas render 10 menit — video "
                            "terlalu panjang untuk 1x run. Coba video lebih "
                            "pendek (<45 mnt). Tidak di-retry agar saldo Modal "
                            "aman.")
            if spec.get("user_id"):
                from billing import release
                release(spec["user_id"], job_id)
            return None


async def _run_job(job_id: str, spec: dict):
    """Dispatch a job, retrying transient failures with the semaphore
    released between attempts.

    The retry loop lives OUTSIDE the semaphore so a retrying job frees its
    permit during the backoff. The previous design recursed while still
    holding the permit, which could dead-lock the whole render pool when
    every permit was held by a job that needed to retry.
    """
    try:
        while True:
            delay = await _run_job_attempt(job_id, spec)
            if delay is None:
                return
            await asyncio.sleep(delay)
    finally:
        # Zombie-job watchdog: mirror the outcome into SQLite for the
        # library. If the task died before reaching completed/failed
        # (cancel/shutdown/crash outside try/except), mark it failed here
        # so the UI never polls a 'queued' ghost forever.
        with _jobs_lock:
            _st = (jobs.get(job_id, {}) or {}).get("status")
        if _st not in ("completed", "failed"):
            with _jobs_lock:
                if job_id in jobs:
                    jobs[job_id]["status"] = "failed"
                    jobs[job_id]["error"] = (
                        jobs[job_id].get("error")
                        or "Worker berhenti sebelum selesai "
                           "(task terhenti/dibatalkan). "
                           "Silakan submit ulang.")
                    jobs[job_id]["logs"].append(
                        "❌ Worker berhenti sebelum selesai "
                        "(task terhenti/dibatalkan).")
        # Mirror the outcome into SQLite for the library.
        if spec["kind"] == "process":
            _persist_process_job(job_id, spec, kind="process")


def _persist_process_job(job_id: str, spec: dict, kind: str) -> None:
    with _jobs_lock:
        job = jobs.get(job_id) or {}
    source_label = ""
    user_id = spec.get("user_id", "") or ""
    if spec.get("source", {}).get("type") in ("youtube", "url"):
        source_label = spec["source"].get("url", "")
    else:
        source_label = os.path.basename(spec.get("source", {}).get("path", "")) \
            or "upload.mp4"
    result = job.get("result") or {}
    db.save_project(
        job_id,
        source_label=source_label,
        status=job.get("status", "failed"),
        created_at=job.get("created_at", time.time()),
        options=spec.get("options") or {},
        error=job.get("error", ""),
        duration=result.get("duration"),
        transcript=result.get("transcript"),
        mode=result.get("mode", "viral"),
        cost=result.get("cost"),
        user_id=user_id,
    )
    if job.get("status") == "completed":
        db.replace_clips(job_id, result.get("clips") or [])


# ─── Google Sign-In ───────────────────────────────────────────────────
# Pending OAuth states for Google Sign-In (CSRF protection): state -> expiry
_auth_states: dict = {}


@app.get("/api/pricing")
async def pricing_public(currency: str = "USD"):
    """Public multi-currency pricing (no auth) for the global Pricing page.

    Prices are sourced from billing.PACKS (IDR) and converted for display;
    checkout itself is charged in IDR via Stripe/Midtrans.
    """
    from pricing import packs_in_currency
    return packs_in_currency(currency)


@app.get("/api/trending")
async def trending_public(category: str = "berita", force: bool = False):
    """Channel YouTube yang sedang ramai per kategori (publik, tanpa auth).

    Dinamis: live ytsearch + cache 1 jam, serve-stale saat fetch gagal.
    `force=1` melewati cache untuk refresh manual dari beranda.
    Dipakai section "Lagi ramai" di beranda agar pengguna bisa 1-klik
    memasukkan video trending ke intake klip.
    """
    import trending as trending_mod
    try:
        return trending_mod.get_trending(category, force=force)
    except ValueError:
        raise HTTPException(status_code=400, detail="Unknown category")
    except Exception:
        raise HTTPException(status_code=503,
                            detail="Trending unavailable — try again later")


@app.get("/api/auth/config")
async def auth_config():
    return {
        "configured": auth.is_configured(),
        "base_url_ok": bool(auth.base_url()),
    }


@app.get("/api/auth/google/url")
async def auth_google_url(request: Request):
    if auth.rate_limited(request.client.host if request.client else None):
        raise HTTPException(status_code=429, detail="Too many attempts — try again later")
    if not auth.is_configured():
        raise HTTPException(status_code=400,
                            detail="GOOGLE_CLIENT_ID/SECRET not configured")
    if not auth.base_url():
        raise HTTPException(status_code=400,
                            detail="AUTH_BASE_URL/PUBLIC_BASE_URL not set — required for OAuth")
    state = auth.new_state()
    _auth_states[state] = time.time() + auth.AUTH_STATE_TTL
    return {"url": auth.auth_url(state, request), "state": state}


@app.get("/api/auth/callback/google")
async def auth_callback(request: Request, code: str = "", state: str = "",
                        error: str = ""):
    # CSRF: `state` must have been issued by us and must match.
    issued = _auth_states.pop(state, None)
    if not issued or issued < time.time():
        return HTMLResponse(_auth_page(False, "OAuth state mismatch or expired"))
    if error:
        return HTMLResponse(_auth_page(False, f"Authorization denied: {error}"))
    try:
        sub = auth.handle_callback(code, request)
    except Exception as e:  # noqa: BLE001
        return HTMLResponse(_auth_page(False, str(e)))
    token = auth.create_session(sub)
    auth.stash_claim(state, token)
    return HTMLResponse(_auth_page(True, "Signed in! You can close this tab."))


@app.get("/api/auth/claim")
async def auth_claim(request: Request, state: str = ""):
    """One-time claim of the session token, keyed by the OAuth state."""
    token = auth.claim_session(state)
    if not token:
        raise HTTPException(status_code=404,
                            detail="No pending session for this state")
    return {"token": token}


@app.get("/api/auth/me")
async def auth_me(request: Request):
    user = auth.user_from_token(_bearer_token(request))
    return {"user": user}


@app.post("/api/auth/logout")
async def auth_logout(request: Request):
    auth.revoke(_bearer_token(request))
    return {"ok": True}


class FavoritesRequest(BaseModel):
    prompt: str
    name: str | None = None  # optional short label
    group: str | None = None  # optional folder/group label (per niche)
    new_prompt: str | None = None  # optional: change the prompt text itself


class GroupRenameRequest(BaseModel):
    old: str
    new: str = ""


class IdeaRequest(BaseModel):
    title: str = ""
    description: str | None = None
    status: str = "idea"      # idea | draft | published
    platform: str | None = None
    scheduled_at: float | None = None
    source_url: str | None = None
    job_id: str | None = None


@app.get("/api/criteria/favorites")
async def criteria_favorites(_user: dict = Depends(require_user)):
    """User's saved custom-AI-criteria prompts (synced across devices)."""
    return {"favorites": db.list_favorites(_user["google_sub"])}


@app.get("/api/criteria/favorites/groups")
async def criteria_favorites_groups(_user: dict = Depends(require_user)):
    """Distinct group labels the user has used, most recent first."""
    return {"groups": db.list_groups(_user["google_sub"])}


@app.post("/api/criteria/favorites")
async def criteria_favorites_add(req: FavoritesRequest,
                                 _user: dict = Depends(require_user)):
    favorites = db.save_favorite(_user["google_sub"], req.prompt, req.name or "",
                                 req.group or "")
    return {"favorites": favorites}


@app.post("/api/criteria/favorites/rename")
async def criteria_favorites_rename(req: FavoritesRequest,
                                    _user: dict = Depends(require_user)):
    # None fields = leave untouched ('' = clear)
    favorites = db.rename_favorite(_user["google_sub"], req.prompt, req.name,
                                   req.group, req.new_prompt)
    return {"favorites": favorites}


@app.post("/api/criteria/favorites/groups/rename")
async def criteria_favorites_groups_rename(req: GroupRenameRequest,
                                            _user: dict = Depends(require_user)):
    favorites = db.rename_group(_user["google_sub"], req.old, req.new)
    return {"favorites": favorites}


@app.delete("/api/criteria/favorites/groups")
async def criteria_favorites_groups_clear(group: str = "",
                                           _user: dict = Depends(require_user)):
    favorites = db.clear_group(_user["google_sub"], group)
    return {"favorites": favorites}


@app.post("/api/criteria/favorites/pin")
async def criteria_favorites_pin(req: FavoritesRequest,
                                 _user: dict = Depends(require_user)):
    favorites = db.pin_favorite(_user["google_sub"], req.prompt)
    return {"favorites": favorites}


@app.delete("/api/criteria/favorites")
async def criteria_favorites_remove(prompt: str = "",
                                    _user: dict = Depends(require_user)):
    favorites = db.remove_favorite(_user["google_sub"], prompt)
    return {"favorites": favorites}


@app.get("/api/brand-kit")
async def brand_kit_get(_user: dict = Depends(require_user)):
    """User's brand kit (caption defaults), synced across devices."""
    return db.get_brand_kit(_user["google_sub"])


class BrandKitRequest(BaseModel):
    kit: dict = {}


class MusicGenerateRequest(BaseModel):
    prompt: str = ""
    title: str = ""
    lyrics: str = ""
    genre: str = "pop-jawa"
    language: str = "id"
    language_secondary: str = ""
    language_mix: str = "none"      # none | chorus | verse-chorus | line | auto
    language_override: bool = False   # advanced toggle: ignore the genre language lock
    vocal_gender: str = "auto"
    provider: str = "heartmula"
    duration: float = 180
    auto_duration: bool = False       # panjang mengikuti lirik, bukan preset
    seed: int | None = None
    full_song: bool = True
    cover_url: str = ""
    take_count: int = 1               # Suno-style A/B: 1 = final saja (default), 2 = A/B
    take_mode: str = "final"          # draft | final | both
    lora_id: str = ""                 # regional LoRA slot (Jawa moat), '' = base
    parent_job_id: str = ""           # extend/remix source
    instrumental: bool = False        # tanpa vokal sama sekali
    bpm: int = 0                      # hint 40-220, 0 = bebas
    music_key: str = ""               # hint mis. "C", "A minor", '' = bebas
    persona_id: str = ""              # voice/style lock dari persona tersimpan
    style_tags: str = ""              # tag tambahan (diisi otomatis dari persona)
    negative_tags: str = ""           # Suno-style "Exclude Styles" (tag yang dihindari)
    weirdness: int = 50               # creative slider 0-100 (Suno "Weirdness")
    style_influence: int = 50         # creative slider 0-100 (Suno "Style Influence")


class MusicLyricsRequest(BaseModel):
    title: str = ""
    prompt: str = ""
    lyrics: str = ""
    genre: str = "pop-jawa"
    language: str = "id"
    language_secondary: str = ""
    language_mix: str = "none"
    language_override: bool = False
    vocal_gender: str = "auto"
    duration: float = 180
    mode: str = "compose"             # compose | expand | refine | suggest | section
    section_index: int = 0            # mode=section: which section to rewrite
    instruction: str = ""             # mode=section: how to rewrite it


class MusicPlanRequest(BaseModel):
    title: str = ""
    prompt: str = ""
    lyrics: str = ""
    genre: str = "pop-jawa"
    language: str = "id"
    language_secondary: str = ""
    language_mix: str = "none"
    language_override: bool = False
    vocal_gender: str = "auto"
    instrumental: bool = False
    duration: float = 180
    bpm: int = 0
    style_tags: str = ""
    ai: bool = False                  # saran style + varian hook via Gemini (murah)


class MusicProjectCreateRequest(BaseModel):
    name: str = ""
    snapshot: dict = {}
    job_id: str = ""
    group_id: str = ""


class MusicProjectRevisionRequest(BaseModel):
    snapshot: dict = {}
    label: str = ""
    source_job_id: str = ""


class MusicLyricsDraftRequest(BaseModel):
    title: str = ""
    lyrics: str = ""
    notes: str = ""
    genre: str = ""
    language: str = ""


MUSIC_LYRICS_LIMIT = 100_000


def _music_lyrics_draft_fields(req: MusicLyricsDraftRequest) -> dict:
    lyrics = req.lyrics or ""
    if len(lyrics) > MUSIC_LYRICS_LIMIT:
        raise HTTPException(status_code=400, detail=(
            "Lirik draft terlalu panjang (maks 100.000 karakter)."))
    return {
        "title": (req.title or "").strip()[:80],
        "lyrics": lyrics,
        "notes": (req.notes or "").strip()[:2_000],
        "genre": (req.genre or "").strip()[:60],
        "language": (req.language or "").strip()[:20],
    }


def _music_project_snapshot(snapshot: dict) -> dict:
    """Validate a studio-project snapshot (form + plan state) before storing."""
    if not isinstance(snapshot, dict) or not snapshot:
        raise HTTPException(status_code=400, detail="Snapshot proyek kosong.")
    try:
        size = len(json.dumps(snapshot, ensure_ascii=False))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Snapshot proyek tidak valid.")
    if size > db.MUSIC_SNAPSHOT_LIMIT:
        raise HTTPException(status_code=400, detail=(
            "Snapshot proyek terlalu besar (maks 200 KB)."))
    return snapshot


@app.get("/api/music/genres")
async def music_genres(probe: bool = False, _user: dict = Depends(require_user)):
    """Music catalog: genres (with their locked vocal language) + engine status.

    ``probe=1`` also pings the Modal worker so the panel can warn *before* the
    user presses Create instead of after a silently failed job. Auth-gated:
    the probe triggers a network call to a paid worker, so anonymous callers
    must not be able to use this endpoint as a probe amplifier.
    """
    from music import (GENRES, engine_status, language_info, language_options,
                       resolve_language)
    genres = []
    for genre in GENRES:
        code, locked = resolve_language(genre["id"], "")
        genres.append({**genre, "language": code, "language_locked": locked,
                       "language_label": language_info(code)["label"]})
    engine = (await asyncio.to_thread(engine_status, True) if probe
              else engine_status())
    providers = [
        {"id": "heartmula", "label": "HeartMuLa (final · default)",
         "available": bool(engine.get("modal_enabled") or engine.get("heartmula_local"))},
        {"id": "diffrhythm", "label": "DiffRhythm (full song · Apache 2.0)",
         "available": bool(engine.get("diffrhythm_modal_enabled"))},
        {"id": "auto", "label": "Auto (A/B: draft + final)",
         "available": bool(engine.get("ready"))},
        {"id": "ace-step", "label": "ACE-Step (draft cepat)",
         "available": bool(engine.get("ace_step") or engine.get("ace_modal_enabled"))},
    ]
    cover_engines = [
        {"id": "arrangement", "label": "Aransemen Baru + Vokal Asli (YouTube Cover Style)",
         "faithful": True,
         "available": bool(engine.get("ace_modal_enabled") or engine.get("mulacover_enabled") or engine.get("ace_step"))},
        {"id": "mulacover", "label": "MuLaCover - melodi & chord asli (AI nyanyi ulang)",
         "faithful": True, "available": bool(engine.get("mulacover_enabled"))},
        {"id": "diffrhythm", "label": "DiffRhythm - melodi baru, vokal penuh (Apache 2.0)",
         "faithful": False, "available": bool(engine.get("diffrhythm_modal_enabled"))},
        {"id": "ace-step", "label": "ACE-Step - draft cepat, bukan faithful",
         "faithful": False,
         "available": bool(engine.get("ace_step") or engine.get("ace_modal_enabled"))},
    ]
    return {
        "genres": genres,
        "languages": language_options(),
        "providers": providers,
        "cover_engines": cover_engines,
        "engine": engine,
        "required_fields": ["title", "prompt_or_lyrics", "genre", "duration"],
    }


@app.get("/api/music/engine")
async def music_engine(_user: dict = Depends(require_user)):
    """Preflight for the Music panel: is a full-song engine reachable right now?

    Auth-gated because ``engine_status(True)`` probes the Modal worker."""
    from music import engine_status
    # Modal's blocking client must not run on the event loop.
    return await asyncio.to_thread(engine_status, True)


@app.post("/api/music/generate")
async def music_generate(req: MusicGenerateRequest, _user: dict = Depends(require_user)):
    from music import (GENRES, cache_key, language_info, provider_hint,
                       resolve_language)
    genre_ids = {g["id"] for g in GENRES}
    if req.genre not in genre_ids:
        raise HTTPException(status_code=400, detail=(
            f"Genre '{req.genre}' tidak dikenal. Pilih genre dari daftar "
            f"({len(genre_ids)} pilihan tersedia)."))
    req.title = (req.title or "").strip()
    req.prompt = (req.prompt or "").strip()
    req.lyrics = (req.lyrics or "").strip()
    # Field-level messages: the panel renders this string verbatim above the form.
    missing = []
    if not req.title:
        missing.append("Judul lagu")
    if len(req.prompt) < 8 and len(req.lyrics) < 20:
        missing.append("Ide lagu (min. 8 karakter) atau Lirik (min. 20 karakter)")
    if not req.auto_duration and not 15 <= float(req.duration) <= 600:
        missing.append("Durasi lagu (15–600 detik)")
    if missing:
        raise HTTPException(status_code=400, detail=(
            "Bagian berikut belum lengkap: " + "; ".join(missing) + "."))
    req.vocal_gender = (req.vocal_gender or "auto").strip().lower()
    if req.vocal_gender not in ("auto", "male", "female", "duet"):
        raise HTTPException(status_code=400, detail=(
            "Gender vokal tidak dikenal. Pilih: auto, female, male, atau duet."))
    # Persona lock: ciri suara/gaya dari lagu tersimpan (bila dipilih).
    persona = None
    if (req.persona_id or "").strip():
        persona = db.get_music_persona(req.persona_id.strip(), _user["google_sub"])
        if not persona:
            raise HTTPException(status_code=404, detail="Persona tidak ditemukan")
        if persona.get("voice_ref_path") and req.vocal_gender != "duet":
            req.vocal_gender = persona.get("vocal_gender") or "auto"
        if not (req.prompt or "").strip():
            req.prompt = f"lagu baru dengan ciri {persona['name']}"
        if req.vocal_gender == "auto" and persona.get("vocal_gender"):
            req.vocal_gender = persona["vocal_gender"]
        if not (req.language or "").strip() and persona.get("language"):
            req.language = persona["language"]
        if not (req.style_tags or "").strip() and persona.get("style_tags"):
            req.style_tags = persona["style_tags"]
        if req.seed is None and persona.get("seed_base") is not None:
            req.seed = int(persona["seed_base"])
    try:
        req.bpm = int(req.bpm or 0)
    except (TypeError, ValueError):
        req.bpm = 0
    if req.bpm and not 40 <= req.bpm <= 220:
        raise HTTPException(status_code=400, detail="BPM harus 40–220 (atau 0 = bebas).")
    req.music_key = (req.music_key or "").strip()[:24]
    # Genre daerah mengunci bahasa vokal (Pop Jawa -> Jawa) kecuali override aktif.
    language, language_locked = resolve_language(
        req.genre, req.language, override=bool(req.language_override))
    req.language = language
    from music import normalize_language_mix
    try:
        req.language_secondary, req.language_mix = normalize_language_mix(
            language, req.language_secondary, req.language_mix)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    take_count = 1 if req.take_count is None else max(1, min(int(req.take_count), 2))
    take_mode = (req.take_mode or "final").strip().lower()
    if take_mode not in ("draft", "final", "both"):
        take_mode = "final"
    if take_count == 1 and take_mode == "both":
        # A/B butuh dua take; satu take tunggal selalu final (draft tunggal
        # tidak berguna dan hanya membuang GPU).
        take_mode = "final"
    base_spec = req.model_dump()
    if req.auto_duration:
        # Durasi final dihitung dari lirik di dalam generate_music; pakai preset
        # sebagai estimasi awal untuk penulisan lirik & estimasi biaya.
        base_spec["duration"] = float(req.duration or 180)
        base_spec["auto_duration"] = True
    if persona and persona.get("voice_ref_path"):
        if req.instrumental:
            raise HTTPException(status_code=400, detail=(
                "Persona suara memerlukan lagu dengan vokal."))
        if req.vocal_gender == "duet":
            raise HTTPException(status_code=400, detail=(
                "Persona rekaman saat ini dipakai untuk satu suara utama; pilih Pria atau Wanita."))
        from music import voice_persona_capability
        capability = await asyncio.to_thread(voice_persona_capability)
        if not capability["ready"]:
            raise HTTPException(status_code=503, detail=(
                "Persona tersimpan, tetapi konversi suara belum siap. "
                + capability["message"]))
        # Voice identity must be applied to one final take. A/B or draft runs
        # would waste GPU time and could return an unconverted voice.
        base_spec.update({"provider": "heartmula", "take_count": 1,
                          "take_mode": "final", "voice_persona_id": persona["persona_id"]})
        take_count, take_mode = 1, "final"
    base_spec["cache_key"] = cache_key(base_spec)
    # Content-addressed reuse: spec identik yang sudah completed → kembalikan grupnya.
    cached = db.find_music_by_cache(_user["google_sub"], base_spec["cache_key"])
    if cached and cached.get("group_id"):
        takes = db.list_music_group(cached["group_id"], _user["google_sub"])
        return {
            "group_id": cached["group_id"], "cached": True,
            "jobs": takes, "job_id": takes[0]["job_id"], "status": "completed",
            "language": language, "language_label": language_info(language)["label"],
            "language_locked": language_locked,
        }
    from music import estimate_music_cost as _estimate
    grouped = _create_music_group(base_spec, _user["google_sub"],
                                  take_count, take_mode, user=_user)
    return {
        **grouped,
        "cache_key": base_spec["cache_key"],
        "language": language, "language_label": language_info(language)["label"],
        "language_locked": language_locked,
        "provider_hint": provider_hint(),
        "cost_estimate": _estimate(float(req.duration or 180), take_count),
    }


def _music_job_age_seconds(job: dict) -> float:
    try:
        base = float(job.get("created_at") or job.get("updated_at") or 0)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, time.time() - base)


def _reconcile_finished_music_job(job: dict | None) -> dict | None:
    """Materialize a music job whose audio finished but DB finalization missed.

    Modal can finish successfully while the API process is still completing
    local duet stitching/return handling, and an un-referenced asyncio task can
    be garbage-collected mid `await`. Both leave the row ``processing`` forever
    and the UI polling it. This recovery promotes a row whose final audio, LRC,
    cover, and metadata are all present, and also fails a row that has been
    non-terminal far longer than any legitimate render so the panel never gets
    stuck on a dead job.
    """
    if not job or job.get("status") not in ("queued", "processing"):
        return job
    job_id = str(job.get("job_id") or "")
    user_id = str(job.get("user_id") or "")
    if not job_id or not user_id:
        return job
    job_dir = OUTPUT_DIR / job_id
    audio = job_dir / "song.mp3"
    lyrics_lrc = job_dir / "lyrics.lrc"
    cover = job_dir / "cover.jpg"
    metadata_file = job_dir / "metadata.json"
    if not all(p.is_file() and p.stat().st_size > 0
               for p in (audio, lyrics_lrc, cover, metadata_file)):
        # No complete artifact set yet. Fail a job that has been non-terminal
        # far past any legitimate render so the UI stops polling a dead task.
        if _music_job_age_seconds(job) > MUSIC_JOB_STALE_SECONDS:
            try:
                from billing import release as _billing_release
                _billing_release(user_id, job_id)
            except Exception:
                pass
            metadata = dict(job.get("metadata") or {})
            metadata["stage"] = "stale"
            return db.update_music_job(
                job_id, user_id, status="failed",
                error=("Worker musik tidak merespons lebih dari "
                       f"{max(1, MUSIC_JOB_STALE_SECONDS // 60)} menit; job "
                       "dihentikan dan kredit dikembalikan. Silakan jalankan ulang."),
                metadata=json.dumps(metadata, ensure_ascii=False)) or job
        return job
    try:
        metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
        if not isinstance(metadata, dict) or not metadata.get("provider"):
            return job
        lyrics = (job_dir / "lyrics.txt").read_text(encoding="utf-8") \
            if (job_dir / "lyrics.txt").is_file() else (job.get("lyrics") or "")
        metadata = {**metadata, "stage": "done", "reconciled": True}
        updated = db.update_music_job(
            job_id, user_id, status="completed",
            provider=metadata.get("provider") or job.get("provider") or "heartmula-modal-duet",
            duration=float(metadata.get("duration") or job.get("duration") or 0),
            audio_path=f"/videos/{job_id}/{audio.name}",
            lyrics_path=f"/videos/{job_id}/{lyrics_lrc.name}",
            lyrics=lyrics[:6000],
            cover_path=f"/videos/{job_id}/{cover.name}",
            cover_source=metadata.get("cover_source") or "local-gradient",
            metadata=json.dumps(metadata, ensure_ascii=False),
        )
        return updated or job
    except Exception:
        # Status polling must never turn a recoverable artifact into a 500.
        return job


def _create_music_group(base_spec: dict, user_id: str,
                        take_count: int, take_mode: str,
                        user: dict | None = None) -> dict:
    """Buat 1 grup A/B (generate biasa atau cover audio-to-audio).

    Kredit direservasi per take SEBELUM task apa pun dijadwalkan. Menjadwalkan
    lebih dulu lalu menagih belakangan (atau menagih saat job sudah jalan)
    membuka celah GPU gratis; mereservasi seluruh take di muka juga memastikan
    bila take kedua tak cukup kredit, take pertama belum sempat dijalankan.
    """
    from music import cache_key as _ck
    import random as _random
    group_id = "mg_" + _new_job_id()
    now = time.time()
    base_seed = base_spec.get("seed")
    planned: list[dict] = []
    for take_index in range(max(1, min(int(take_count), 2))):
        if base_seed is not None:
            try:
                seed = int(base_seed) + take_index
            except (TypeError, ValueError):
                seed = _random.randint(1, 2 ** 31 - 1)
        else:
            seed = _random.randint(1, 2 ** 31 - 1)
        # Cover harus mempertahankan referensi audio pada setiap take. Jangan
        # kirim take kedua ke HeartMuLa biasa jika MuLaCover/ACE-Step dipilih;
        # kedua worker cover itu menerima referensi audio.
        if take_mode == "cover":
            requested_cover_provider = str(base_spec.get("provider") or "").lower()
            provider = (requested_cover_provider
                        if requested_cover_provider in (
                            "ace-step", "mulacover", "arrangement", "voice", "diffrhythm")
                        else "ace-step")
            mode = "cover"
        # Take 0 = draft cepat (ACE-Step), take 1 = final nyawa (HeartMuLa).
        elif take_mode == "both":
            provider = "ace-step" if take_index == 0 else "heartmula"
            mode = "draft" if take_index == 0 else "final"
        elif take_mode == "draft":
            provider, mode = "ace-step", "draft"
        else:
            provider, mode = "heartmula", "final"
        if base_spec.get("provider") not in ("auto", "", None):
            provider = base_spec["provider"]
        spec = dict(base_spec)
        spec.update({"seed": seed, "provider": provider, "take_mode": mode,
                     "lora_id": base_spec.get("lora_id") or "",
                     "cache_key": _ck({**base_spec, "seed": seed, "provider": provider}),
                     "stage": "queued-draft" if mode == "draft" else "queued-final"})
        planned.append({"spec": spec, "take_index": take_index, "provider": provider,
                        "mode": mode, "seed": seed,
                        "job_id": "music_" + _new_job_id()})
    group_cache_key = str(base_spec.get("cache_key") or "")
    # Cheap pre-check: join an identical in-flight or finished group instead of
    # starting a second one. The unique index below still guards the real race
    # where two requests pass this check at the same instant.
    if group_cache_key:
        winner = db.find_music_by_cache(user_id, group_cache_key,
                                        completed_only=False)
        if winner and winner.get("group_id"):
            return _music_group_result(winner["group_id"], user_id, deduped=True)
    from billing import enabled as _billing_enabled
    if _billing_enabled():
        from billing import release as _billing_release
        from billing import reserve as _billing_reserve
        reserved: list[str] = []
        for plan in planned:
            check = _billing_reserve(user_id, plan["job_id"], user=user)
            if not check["ok"]:
                for reserved_id in reserved:
                    _billing_release(user_id, reserved_id)
                raise HTTPException(status_code=402, detail=check["error"])
            reserved.append(plan["job_id"])
    jobs = []
    for plan in planned:
        spec, job_id = plan["spec"], plan["job_id"]
        stored = db.create_music_job({
            **spec, "job_id": job_id, "user_id": user_id,
            "status": "queued", "group_id": group_id,
            "take_index": plan["take_index"],
            "group_cache_key": group_cache_key,
            "created_at": now, "updated_at": now,
            "metadata": json.dumps({"cache_key": spec["cache_key"],
                                    "group_id": group_id,
                                    "take_index": plan["take_index"],
                                    "take_mode": plan["mode"],
                                    "cover_mode": bool(spec.get("cover_mode")),
                                    "lyrics_locked": bool(spec.get("_lyrics_locked")),
                                    "cover_reference_strength": spec.get("ref_strength", 0.0),
                                    "language_secondary": spec.get("language_secondary", ""),
                                    "language_mix": spec.get("language_mix", "none"),
                                    "stage": spec["stage"]})})
        if stored and stored.get("_deduped"):
            # Lost the dedup race with a concurrent identical request: reuse the
            # winner's group and release every reservation we took for this
            # aborted group (the first take is always the one that conflicts).
            if _billing_enabled():
                from billing import release as _billing_release
                for other in planned:
                    _billing_release(user_id, other["job_id"])
            winner_group = stored.get("group_id") or ""
            if winner_group:
                return _music_group_result(winner_group, user_id, deduped=True)
            raise HTTPException(status_code=409, detail=(
                "Permintaan lagu identik sedang diproses; coba lagi sebentar."))
        _spawn_music_job(job_id, spec, user_id)
        jobs.append({"job_id": job_id, "take_index": plan["take_index"],
                     "take_mode": plan["mode"], "provider": plan["provider"],
                     "seed": plan["seed"], "status": "queued"})
    return {"group_id": group_id, "jobs": jobs,
            "job_id": jobs[0]["job_id"], "status": "queued"}


def _music_group_result(group_id: str, user_id: str, deduped: bool = False) -> dict:
    """Shape an existing group into the same payload as a fresh creation."""
    takes = db.list_music_group(group_id, user_id)
    jobs = [{"job_id": t["job_id"], "take_index": int(t.get("take_index") or 0),
             "take_mode": t.get("take_mode") or "final",
             "provider": t.get("provider") or "", "seed": t.get("seed"),
             "status": t.get("status") or "queued"} for t in takes]
    return {"group_id": group_id, "jobs": jobs,
            "job_id": jobs[0]["job_id"] if jobs else "",
            "status": jobs[0]["status"] if jobs else "queued",
            "deduped": deduped}


async def _run_music_job(job_id: str, spec: dict, user_id: str):
    """Run one music take under the dedicated music concurrency gate.

    The credit reservation was made when the group was created; here we commit
    it on success and release it on failure so a failed take refunds cleanly.
    """
    async with _music_semaphore:
        await _run_music_take(job_id, spec, user_id)


async def _run_music_take(job_id: str, spec: dict, user_id: str):
    job_dir = OUTPUT_DIR / job_id
    mode = spec.get("take_mode", "final")
    base_meta = {"cache_key": spec.get("cache_key", ""),
                 "group_id": spec.get("group_id", ""),
                 "take_index": spec.get("take_index", 0),
                 "take_mode": mode,
                 "stage": "draft-render" if mode == "draft" else "final-render"}
    db.update_music_job(job_id, user_id, status="processing",
                        metadata=json.dumps(base_meta))

    def _set_stage(stage: str):
        """Publish a live stage so the panel reflects real progress.

        Runs inside the worker thread; SQLite access is lock-guarded so this is
        safe. Never let a status write kill a running render.
        """
        try:
            current = db.get_music_job(job_id, user_id) or {}
            metadata = dict(current.get("metadata") or base_meta)
            if metadata.get("stage") == stage:
                return
            metadata["stage"] = stage
            db.update_music_job(job_id, user_id,
                                metadata=json.dumps(metadata, ensure_ascii=False))
        except Exception:
            pass

    try:
        from music import generate_music
        voice_persona_id = str(spec.get("voice_persona_id") or "").strip()
        if voice_persona_id:
            persona = db.get_music_persona(voice_persona_id, user_id)
            if not persona or not persona.get("voice_ref_path"):
                raise RuntimeError("Persona suara tidak ditemukan; lagu belum dijalankan.")
            from voice_personas import decrypt_sample
            voice_tmp = Path(tempfile.mkdtemp(prefix="music-voice-job-", dir=str(db.DATA_DIR)))
            try:
                reference = decrypt_sample(
                    user_id, voice_persona_id, persona["voice_ref_path"],
                    voice_tmp / "voice-reference.wav")
                result = await asyncio.to_thread(
                    generate_music,
                    {**spec, "_voice_reference_path": str(reference)}, job_dir,
                    on_stage=_set_stage)
            finally:
                shutil.rmtree(voice_tmp, ignore_errors=True)
        else:
            result = await asyncio.to_thread(generate_music, spec, job_dir,
                                             on_stage=_set_stage)
        cover_path = f"/videos/{job_id}/{result['cover_filename']}" if result.get("cover_filename") else ""
        wav_path = f"/videos/{job_id}/{result['wav_filename']}" if result.get("wav_filename") else ""
        db.update_music_job(
            job_id, user_id, status="completed", provider=result["provider"],
            duration=result["duration"], audio_path=f"/videos/{job_id}/{result['audio_filename']}",
            lyrics_path=f"/videos/{job_id}/lyrics.lrc",
            lyrics=result.get("lyrics_text", spec.get("lyrics", ""))[:6000],
            cover_path=cover_path, cover_source=result.get("cover_source", ""),
            metadata=json.dumps({**result["metadata"], "lyrics_timeline": result["lyrics_timeline"],
                                 "cover_path": cover_path, "wav_path": wav_path,
                                 "cover_source": result.get("cover_source", ""),
                                 "group_id": spec.get("group_id", ""),
                                 "take_index": spec.get("take_index", 0),
                                 "stage": "done"}, ensure_ascii=False),
        )
    except Exception as exc:  # noqa: BLE001
        db.update_music_job(job_id, user_id, status="failed", error=str(exc)[:1000])
        try:
            from billing import release as _billing_release
            _billing_release(user_id, job_id)
        except Exception:
            pass
        try:
            db.record_music_cost(job_id, user_id, 0, 0, 0, state="released")
        except Exception:
            pass
        return
    try:
        from billing import commit as _billing_commit
        _billing_commit(user_id, job_id)
    except Exception:
        pass
    try:
        from music import music_cost as _cost
        meta_now = (db.get_music_job(job_id, user_id) or {}).get("metadata") or {}
        gpu_sec = float(meta_now.get("gpu_sec") or 0)
        cost = _cost(gpu_sec)
        db.record_music_cost(job_id, user_id, cost["gpu_sec"], cost["usd"],
                             cost["credits"], state="committed")
    except Exception:
        pass


class MusicPersonaRequest(BaseModel):
    name: str = ""
    job_id: str = ""


from voice_preview_api import router_for as _voice_preview_router
app.include_router(_voice_preview_router(require_user))


class MusicDesignedPersonaRequest(BaseModel):
    name: str = ""
    vocal_gender: str = "auto"
    warmth: int = 50
    brightness: int = 50
    texture: str = "smooth"
    energy: str = "balanced"
    vocal_register: str = Field(default="mid", alias="register")


@app.get("/api/music/personas")
async def music_personas(_user: dict = Depends(require_user)):
    """Voice/style locks milik user (Suno Personas versi Klipin)."""
    from music import voice_persona_capability
    capability = await asyncio.to_thread(voice_persona_capability)
    personas = []
    for row in db.list_music_personas(_user["google_sub"]):
        row["has_voice_sample"] = bool(row.get("voice_ref_path"))
        row.pop("voice_ref_path", None)
        row["voice_conversion_ready"] = bool(row["has_voice_sample"] and capability["ready"])
        personas.append(row)
    return {"personas": personas, "voice_conversion": capability}


@app.post("/api/music/personas/voice")
async def music_persona_voice_create(
        name: str = Form(""), vocal_gender: str = Form("auto"),
        consent: bool = Form(False), file: UploadFile = File(...),
        _user: dict = Depends(require_user)):
    """Save an explicitly consented singing-voice reference privately per account."""
    if not consent:
        raise HTTPException(status_code=400, detail=(
            "Centang persetujuan bahwa rekaman adalah suara Anda atau Anda "
            "memiliki izin, dan setuju audio diproses untuk membuat lagu."))
    vocal_gender = (vocal_gender or "auto").strip().lower()
    if vocal_gender not in ("auto", "male", "female"):
        raise HTTPException(status_code=400, detail="Pilih tipe suara auto, pria, atau wanita.")
    temp_root = Path(tempfile.mkdtemp(prefix="voice-persona-", dir=str(db.DATA_DIR)))
    try:
        upload_path = temp_root / "source-audio"
        total = 0
        with upload_path.open("wb") as handle:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > 25 * 1024 * 1024:
                    raise HTTPException(status_code=413, detail="Rekaman maksimal 25 MB.")
                handle.write(chunk)
        if total < 2048:
            raise HTTPException(status_code=400, detail="File audio kosong atau terlalu pendek.")
        ffprobe = shutil.which("ffprobe")
        ffmpeg = shutil.which("ffmpeg")
        if not ffprobe or not ffmpeg:
            raise HTTPException(status_code=503, detail="Pemeriksa audio server belum tersedia.")
        probe = await asyncio.to_thread(
            subprocess.run,
            [ffprobe, "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=codec_type", "-show_entries", "format=duration",
             "-of", "json", str(upload_path)],
            capture_output=True, text=True, timeout=30)
        if probe.returncode != 0:
            raise HTTPException(status_code=400, detail="File tidak terbaca sebagai audio.")
        try:
            probe_data = json.loads(probe.stdout or "{}")
            duration = float((probe_data.get("format") or {}).get("duration") or 0)
            has_audio = bool(probe_data.get("streams"))
        except (TypeError, ValueError, json.JSONDecodeError):
            duration, has_audio = 0, False
        if not has_audio or not 3 <= duration <= 30:
            raise HTTPException(status_code=400, detail=(
                "Pilih rekaman suara 3–30 detik yang jelas, tanpa musik latar."))
        canonical = temp_root / "reference.wav"
        converted = await asyncio.to_thread(
            subprocess.run,
            [ffmpeg, "-y", "-v", "error", "-i", str(upload_path), "-t", "30",
             "-vn", "-ac", "1", "-ar", "24000", "-c:a", "pcm_s16le", str(canonical)],
            capture_output=True, text=True, timeout=60)
        if converted.returncode != 0 or not canonical.exists():
            raise HTTPException(status_code=400, detail="Audio gagal dinormalisasi.")
        sample = canonical.read_bytes()
        persona_id = "persona_" + secrets.token_hex(6)
        from voice_personas import store_sample
        stored_name = store_sample(_user["google_sub"], persona_id, sample)
        try:
            persona = db.create_music_voice_persona(
                _user["google_sub"], (name or "Suara saya").strip(), vocal_gender,
                stored_name, time.time(), persona_id=persona_id)
        except Exception:
            from voice_personas import delete_sample
            delete_sample(_user["google_sub"], persona_id, stored_name)
            raise
        persona.pop("voice_ref_path", None)
        persona["has_voice_sample"] = True
        from music import voice_persona_capability
        capability = await asyncio.to_thread(voice_persona_capability)
        persona["voice_conversion_ready"] = bool(capability["ready"])
        return {"persona": persona, "voice_conversion": capability}
    finally:
        await file.close()
        shutil.rmtree(temp_root, ignore_errors=True)


@app.post("/api/music/personas")
async def music_persona_create(req: MusicPersonaRequest, _user: dict = Depends(require_user)):
    src = db.get_music_job((req.job_id or "").strip(), _user["google_sub"])
    if not src or src.get("status") != "completed":
        raise HTTPException(status_code=400, detail="Sumber persona harus lagu completed")
    return {"persona": db.create_music_persona(
        _user["google_sub"], (req.name or "").strip() or (src.get("title") or "Persona"), src)}


@app.post("/api/music/personas/design")
async def music_persona_design(req: MusicDesignedPersonaRequest,
                               _user: dict = Depends(require_user)):
    """Create a saved vocal direction using supported HeartMuLa prompt tags."""
    gender = (req.vocal_gender or "auto").strip().lower()
    texture = (req.texture or "smooth").strip().lower()
    energy = (req.energy or "balanced").strip().lower()
    register = (req.vocal_register or "mid").strip().lower()
    if gender not in ("auto", "male", "female"):
        raise HTTPException(status_code=400, detail="Pilih suara otomatis, pria, atau wanita.")
    if texture not in ("smooth", "breathy", "raspy"):
        raise HTTPException(status_code=400, detail="Tekstur vokal tidak valid.")
    if energy not in ("soft", "balanced", "powerful"):
        raise HTTPException(status_code=400, detail="Energi vokal tidak valid.")
    if register not in ("low", "mid", "high"):
        raise HTTPException(status_code=400, detail="Rentang vokal tidak valid.")
    warmth = max(0, min(100, int(req.warmth)))
    brightness = max(0, min(100, int(req.brightness)))
    warmth_label = "warm" if warmth >= 67 else "neutral" if warmth >= 34 else "cool"
    brightness_label = "bright" if brightness >= 67 else "balanced brightness" if brightness >= 34 else "dark"
    tags = ", ".join((
        f"{gender} vocal" if gender != "auto" else "natural vocal",
        f"{warmth_label} vocal tone", brightness_label,
        {"smooth": "smooth clear texture", "breathy": "airy breathy texture",
         "raspy": "light husky texture"}[texture],
        {"soft": "gentle intimate delivery", "balanced": "balanced expressive delivery",
         "powerful": "strong powerful delivery"}[energy],
        {"low": "low vocal register", "mid": "mid vocal register",
         "high": "high vocal register"}[register],
    ))[:300]
    persona = db.create_music_designed_persona(
        _user["google_sub"], (req.name or "").strip() or "Karakter vokal",
        gender, tags,
        notes=f"warmth={warmth}; brightness={brightness}; texture={texture}; "
              f"energy={energy}; register={register}")
    return {"persona": persona, "notice": (
        "Karakter ini menjadi arahan model, bukan identitas suara yang terkunci. "
        "Hasil dapat bervariasi antar lagu.")}


@app.delete("/api/music/personas/{persona_id}")
async def music_persona_delete(persona_id: str, _user: dict = Depends(require_user)):
    existing = db.get_music_persona(persona_id, _user["google_sub"])
    if not existing:
        raise HTTPException(status_code=404, detail="Persona tidak ditemukan")
    if existing.get("voice_ref_path"):
        from voice_personas import delete_sample
        delete_sample(_user["google_sub"], persona_id, existing["voice_ref_path"])
    db.delete_music_persona(persona_id, _user["google_sub"])
    return {"deleted": persona_id}


class MusicCoverSongRequest(BaseModel):
    prompt: str = ""                  # interpretasi baru; kosong = pakai prompt induk
    genre: str = ""                   # kosong = genre induk
    vocal_gender: str = ""            # kosong = ikut induk
    ref_strength: float = 0.45        # 0.1-0.9: keseimbangan identitas vs aransemennya
    take_count: int = 2


class MusicRemixUrlRequest(BaseModel):
    url: str = ""
    genre: str = "pop-remix"
    prompt: str = ""
    lyrics: str = ""            # optional phrase-accurate sheet; empty = ASR
    groove_reset: bool = False  # drop source drums so the genre rebuilds the groove
    ref_strength: float = 0.6
    vocal_gender: str = "female"
    persona_id: str = ""
    rights_confirmed: bool = False
    cover_engine: str = ""   # ""=auto | arrangement | voice | mulacover | diffrhythm | heartmula | ace-step
    pitch_shift: str = "auto"  # voice-conversion: "auto" | semitone int (mis. 12, -12, 0)
    restyle: bool = False      # voice engine: also re-arrange the backing into the genre
    restyle_strength: float = 0.6
    keep_original_vocal: bool = False  # True = keep original singer's vocal & only change arrangement
    voice_mode: str = "manual"  # "manual" = user picks voice; "auto" = Klipin picks


def _split_long_lyric_line(text: str, limit: int = 120) -> list[str]:
    """Split a long ASR segment at sentence boundaries.

    MuLaCover re-sings the supplied sheet; one multi-sentence paragraph makes
    its section/timing conditioning collapse. Sentence punctuation is preferred
    so each output line stays a full sung phrase instead of a mid-clause cut.
    """
    text = " ".join(str(text or "").split()).strip()
    if len(text) <= limit:
        return [text] if text else []
    import re as _re
    pieces = _re.split(r"(?<=[.!?;])\s+", text)
    out: list[str] = []
    for piece in pieces:
        piece = piece.strip()
        if not piece:
            continue
        if len(piece) <= limit:
            out.append(piece)
            continue
        chunk: list[str] = []
        for word in piece.split():
            if chunk and len(" ".join(chunk + [word])) > limit:
                out.append(" ".join(chunk))
                chunk = [word]
            else:
                chunk.append(word)
        if chunk:
            out.append(" ".join(chunk))
    return out


def _music_lyrics_from_manual(text: str) -> str:
    """Lock a user-supplied lyric sheet exactly as typed.

    One line means one sung sentence/phrase, which is what keeps the cover's
    vocal phrasing and timing tied to the original performance. Line breaks and
    section markers are preserved; only blank lines are dropped.
    """
    raw_lines = [ln.strip() for ln in str(text or "").splitlines()]
    lines = [ln for ln in raw_lines if ln]
    if not lines:
        raise RuntimeError("Lirik manual kosong.")
    has_marker = any(ln.startswith("[") and ln.endswith("]") for ln in lines)
    body = "\n".join(lines)
    if not has_marker:
        body = "[Verse]\n" + body
    return body[:6000].strip()


def _music_lyrics_from_transcript(transcript: dict) -> str:
    """Turn source-song ASR into locked, sectioned lyrics.

    This deliberately does not ask the lyric writer to paraphrase or improve
    the text.  The cover pipeline must pass the words heard in the source to
    the singing model unchanged; only a neutral ``[Verse]`` wrapper is added
    when the source has no usable section markers.
    """
    lines = []
    for segment in (transcript or {}).get("segments") or []:
        text = " ".join(str(segment.get("text") or "").split()).strip()
        if text and text.lower() not in {"[musik]", "[music]", "(musik)", "(music)"}:
            lines.extend(_split_long_lyric_line(text))
    if not lines:
        text = " ".join(str((transcript or {}).get("text") or "").split()).strip()
        if text:
            lines = _split_long_lyric_line(text)
    if not lines:
        raise RuntimeError("Lirik lagu tidak berhasil diekstrak dari audio sumber.")
    # Keep the job payload/model context bounded while retaining source order.
    lyrics = "[Verse]\n" + "\n".join(lines)
    return lyrics[:6000].strip()


@app.post("/api/music/cover-song/{job_id}")
async def music_cover_song(job_id: str, req: MusicCoverSongRequest,
                           _user: dict = Depends(require_user)):
    """Cover faithful: lirik/melodi induk tetap, genre mengubah aransemennya."""
    parent = db.get_music_job(job_id, _user["google_sub"])
    if not parent or parent.get("status") != "completed":
        raise HTTPException(status_code=400, detail="Induk harus lagu completed")
    from music import _mulacover_modal_enabled
    mulacover_enabled = _mulacover_modal_enabled()
    ace_enabled = os.environ.get("KLIPIN_ACE_STEP_MODAL_ENABLED", "0").strip() == "1"
    if not mulacover_enabled and not ace_enabled:
        raise HTTPException(status_code=503, detail=(
            "Cover audio-to-audio membutuhkan worker MuLaCover atau ACE-Step. "
            "Aktifkan KLIPIN_MULACOVER_MODAL_ENABLED=1 (pilihan HeartMuLa) "
            "atau KLIPIN_ACE_STEP_MODAL_ENABLED=1 terlebih dahulu."))
    try:
        strength = max(0.1, min(0.9, float(req.ref_strength or 0.5)))
    except (TypeError, ValueError):
        strength = 0.5
    # Simpan path artifact induk sebagai referensi. Worker akan membaca dan
    # meng-encode sekali saat job dijalankan; ini menjaga payload job tetap
    # ringan dan tidak menaruh audio besar di SQLite.
    ref_path = ""
    try:
        fname = (parent.get("audio_path") or "").rsplit("/", 1)[-1] or "song.mp3"
        src = OUTPUT_DIR / job_id / fname
        if not src.exists():
            cands = sorted((OUTPUT_DIR / job_id).glob("song.*"))
            src = cands[0] if cands else src
        if not src.is_file() or src.stat().st_size < 1024:
            raise RuntimeError("Audio induk tidak ditemukan atau kosong.")
        if src.stat().st_size > 15_000_000:
            raise RuntimeError("Audio induk terlalu besar untuk cover (maks. 15 MB).")
        ref_path = str(src)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Audio induk belum siap: {exc}") from exc
    genre = (req.genre or "").strip() or parent.get("genre", "pop")
    from music import GENRES as _GENRES
    if genre not in {g["id"] for g in _GENRES}:
        raise HTTPException(status_code=400, detail=f"Genre '{genre}' tidak dikenal")
    base_seed = int(parent.get("seed") or 0) + 5000
    parent_lyrics = str(parent.get("lyrics") or "").strip()
    if not parent_lyrics:
        lyric_file = OUTPUT_DIR / job_id / "lyrics.txt"
        if lyric_file.is_file():
            parent_lyrics = lyric_file.read_text(encoding="utf-8").strip()
    parent_meta = parent.get("metadata") or {}
    parent_instrumental = bool(parent.get("instrumental")) or bool(parent_meta.get("instrumental"))
    if not parent_lyrics and not parent_instrumental:
        raise HTTPException(status_code=400,
                            detail="Lirik induk belum tersedia; cover faithful dibatalkan.")
    base_spec = {
        "title": f"{parent.get('title') or 'Cover'} (reimagine)",
        "prompt": (req.prompt or "").strip() or parent.get("prompt", ""),
        "lyrics": parent_lyrics,
        "genre": genre, "language": parent.get("language", "id"),
        "language_override": True, "vocal_gender": (req.vocal_gender or "").strip() or parent.get("vocal_gender", "auto"),
        "provider": "mulacover" if mulacover_enabled else "ace-step",
        "cover_engine": "mulacover" if mulacover_enabled else "ace-step",
        "duration": float(parent.get("duration") or 120),
        "seed": base_seed, "full_song": True, "lora_id": parent.get("lora_id", ""),
        "instrumental": parent_instrumental, "bpm": 0, "music_key": "",
        "persona_id": "", "style_tags": "", "parent_job_id": job_id,
        "ref_audio_path": ref_path, "ref_strength": strength,
        "cover_mode": True, "_lyrics_locked": True,
    }
    grouped = _create_music_group(base_spec, _user["google_sub"],
                                  max(1, min(int(req.take_count or 2), 2)), "cover",
                                  user=_user)
    return {**grouped, "parent_job_id": job_id, "cover_ref": bool(ref_path),
            "cover_mode": "audio-to-audio", "cover_engine": base_spec["cover_engine"],
            "lyrics_locked": True,
            "ref_strength": strength}


def _probe_audio_duration(path: Path) -> float:
    """Return an audio file's duration in seconds (0.0 when unknown)."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=30)
        return float((result.stdout or "").strip() or 0.0)
    except Exception:  # noqa: BLE001 - duration is best-effort
        return 0.0


@app.post("/api/music/cover-upload")
async def music_cover_upload(
    file: UploadFile = File(...),
    lyrics: str = Form(""),
    genre: str = Form("dangdut-koplo"),
    vocal_gender: str = Form("auto"),
    cover_engine: str = Form("mulacover"),
    seed: int = Form(0),
    take_count: int = Form(1),
    rights_confirmed: bool = Form(False),
    _user: dict = Depends(require_user),
):
    """Cover faithful dari lagu yang diunggah sendiri (file + lirik).

    Meniru alur "cover lagu dari link", tetapi sumbernya file lokal: audio
    disimpan, lirik diambil dari input pengguna atau ditranskripsi (ASR), lalu
    dijalankan lewat grup cover A/B dengan engine MuLaCover / ACE-Step / DiffRhythm.
    """
    from music import (GENRES, _ace_modal_enabled, _mulacover_modal_enabled,
                       _diffrhythm_modal_enabled)

    if not rights_confirmed:
        raise HTTPException(
            status_code=400,
            detail="Konfirmasi dulu bahwa kamu berhak memakai lagu sumber ini.")
    genre_ids = {str(g.get("id")) for g in GENRES}
    if genre not in genre_ids:
        raise HTTPException(status_code=400, detail=f"Genre '{genre}' tidak dikenal.")
    engine = (cover_engine or "mulacover").strip().lower()
    if engine in ("acestep", "ace"):
        engine = "ace-step"
    availability = {
        "arrangement": _ace_modal_enabled() or _mulacover_modal_enabled(),
        "mulacover": _mulacover_modal_enabled(),
        "ace-step": _ace_modal_enabled(),
        "diffrhythm": _diffrhythm_modal_enabled(),
    }
    if engine not in availability:
        raise HTTPException(status_code=400,
                            detail="Engine cover tidak dikenal (arrangement|mulacover|ace-step|diffrhythm).")
    if not availability[engine]:
        raise HTTPException(status_code=503,
                            detail=f"Engine '{engine}' belum aktif di server ini.")
    gender = (vocal_gender or "auto").strip().lower()
    if gender not in ("auto", "male", "female", "duet"):
        raise HTTPException(status_code=400,
                            detail="Gender vokal harus auto, male, female, atau duet.")

    raw = await file.read()
    if len(raw) < 1024:
        raise HTTPException(status_code=400, detail="File audio kosong atau rusak.")
    if len(raw) > 15_000_000:
        raise HTTPException(status_code=400, detail="File audio terlalu besar (maks. 15 MB).")

    job_dir = OUTPUT_DIR / ("music_" + _new_job_id())
    job_dir.mkdir(parents=True, exist_ok=True)
    suffix = (Path(file.filename or "lagu.mp3").suffix or ".mp3").lower()
    if suffix not in (".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus"):
        suffix = ".mp3"
    ref_path = job_dir / f"source{suffix}"
    ref_path.write_bytes(raw)

    duration = await asyncio.to_thread(_probe_audio_duration, ref_path)
    duration = max(15.0, min(float(duration or 180.0), 600.0))

    text = (lyrics or "").strip()
    if text:
        if not text.lstrip().startswith("["):
            text = "[Verse]\n" + text
        lyrics_source = "user"
    else:
        from transcribe import transcribe
        try:
            transcript = await asyncio.to_thread(transcribe, str(ref_path))
        except Exception as exc:  # noqa: BLE001 - friendly message to the UI
            raise HTTPException(
                status_code=400,
                detail=("Lirik tidak diberikan dan transkripsi otomatis gagal: "
                        f"{str(exc)[:300]}")) from exc
        text = _music_lyrics_from_transcript(transcript)
        lyrics_source = str(transcript.get("source") or "audio_asr")

    try:
        base_seed = int(seed)
    except (TypeError, ValueError):
        base_seed = 0
    if not base_seed:
        base_seed = int(time.time()) % (2 ** 31 - 1)

    stem = Path(file.filename or "lagu").stem[:60] or "lagu"
    base_spec = {
        "title": f"{stem} — cover {genre}", "prompt": f"faithful cover in {genre}",
        "lyrics": text, "genre": genre, "language": "id", "language_override": True,
        "vocal_gender": gender, "provider": engine, "cover_engine": engine,
        "duration": duration, "seed": base_seed, "full_song": True,
        "take_mode": "final", "instrumental": False, "bpm": 0, "music_key": "",
        "persona_id": "", "style_tags": "", "parent_job_id": "",
        "ref_audio_path": str(ref_path), "ref_strength": 0.6,
        "cover_mode": True, "_lyrics_locked": True,
        "lyrics_extraction_source": lyrics_source,
        "cover_upload_name": file.filename or "",
    }
    grouped = _create_music_group(
        base_spec, _user["google_sub"],
        max(1, min(int(take_count or 1), 2)), "cover", user=_user)
    return {**grouped, "genre": genre, "duration": duration,
            "cover_engine": engine, "lyrics_locked": True,
            "lyrics_source": lyrics_source, "cover_source_title": stem}


@app.post("/api/music/remix-url")
async def music_remix_url(req: MusicRemixUrlRequest,
                          _user: dict = Depends(require_user)):
    """Create a lyric-locked cover from a pasted public URL.

    The source is downloaded first, its sung words are transcribed, and only
    then is a cover job queued.  The engine is selectable: DiffRhythm reimagines
    the song (fresh melody, exact lyrics, vocals, full length), MuLaCover/ACE-Step
    condition on the reference audio for faithful covers, and HeartMuLa is the
    lyric-locked fallback.
    """
    from video import download_audio_url, resolve_url_metadata
    from music import (GENRES, _mulacover_modal_enabled, _diffrhythm_modal_enabled,
                       _ace_modal_enabled, is_safe_public_url, voice_persona_capability)

    url = (req.url or "").strip()
    url_ok, url_err = is_safe_public_url(url)
    if not url_ok:
        raise HTTPException(status_code=400, detail=url_err or "Tempel URL publik yang valid.")
    # Strip YouTube Radio/Playlist query params (&list=RD...&start_radio=1) when v= is present
    try:
        from urllib.parse import urlparse as _urlparse, parse_qs as _parse_qs
        _p = _urlparse(url)
        if (_p.hostname or "").lower() in ("www.youtube.com", "youtube.com", "m.youtube.com") and _p.path == "/watch":
            _v = (_parse_qs(_p.query).get("v") or [""])[0].strip()
            if _v:
                url = f"https://www.youtube.com/watch?v={_v}"
    except Exception:
        pass
    if not req.rights_confirmed:
        raise HTTPException(status_code=400, detail=(
            "Konfirmasi bahwa kamu memiliki izin memakai lagu sumber "
            "sebelum membuat cover."
        ))
    genre_ids = {g["id"] for g in GENRES}
    if req.genre not in genre_ids:
        raise HTTPException(status_code=400, detail=f"Genre '{req.genre}' tidak dikenal.")
    mulacover_enabled = _mulacover_modal_enabled()
    heartmula_enabled = (
        os.environ.get("KLIPIN_HEARTMULA_MODAL_ENABLED", "0").strip() == "1"
        or bool(os.environ.get("KLIPIN_HEARTMULA_ROOT", "").strip()
                and os.environ.get("KLIPIN_HEARTMULA_MODEL_PATH", "").strip())
    )
    diffrhythm_enabled = _diffrhythm_modal_enabled()
    ace_enabled = _ace_modal_enabled()
    voice_enabled = os.environ.get("KLIPIN_VOICE_MODAL_ENABLED", "0").strip() == "1"
    arrangement_enabled = ace_enabled or mulacover_enabled or voice_enabled
    engine_available = {
        "arrangement": arrangement_enabled,
        "voice": voice_enabled,
        "diffrhythm": diffrhythm_enabled,
        "ace-step": ace_enabled,
        "mulacover": mulacover_enabled,
        "heartmula": heartmula_enabled,
    }
    requested_engine = (req.cover_engine or "").strip().lower()
    if requested_engine and requested_engine not in engine_available:
        raise HTTPException(status_code=400, detail=(
            "Engine cover tidak dikenal. Pilih salah satu: arrangement, voice, diffrhythm, "
            "ace-step, mulacover, heartmula, atau kosongkan untuk otomatis."))
    if requested_engine:
        cover_engine = requested_engine
        if not engine_available[cover_engine]:
            raise HTTPException(status_code=503, detail=(
                f"Engine cover '{cover_engine}' belum aktif di server ini."))
    else:
        # Auto only treats MuLaCover as faithful. DiffRhythm/HeartMuLa rewrite the
        # melody; ACE-Step is a fast draft (25 steps) and is the last resort.
        cover_engine = next(
            (eng for eng in ("mulacover", "arrangement", "diffrhythm", "heartmula", "ace-step")
             if engine_available[eng]), "")
    if not cover_engine:
        raise HTTPException(status_code=503, detail=(
            "Worker cover belum aktif. Aktifkan HeartMuLa "
            "(KLIPIN_HEARTMULA_MODAL_ENABLED=1), DiffRhythm "
            "(KLIPIN_DIFFRHYTHM_MODAL_ENABLED=1), ACE-Step "
            "(KLIPIN_ACE_STEP_MODAL_ENABLED=1), Voice/Seed-VC "
            "(KLIPIN_VOICE_MODAL_ENABLED=1), atau MuLaCover "
            "(KLIPIN_MULACOVER_MODAL_ENABLED=1) terlebih dahulu."))
    needs_ref = cover_engine in ("ace-step", "mulacover")
    needs_audio = needs_ref or cover_engine in ("voice", "arrangement")
    vocal_gender = (req.vocal_gender or "female").strip().lower()
    if vocal_gender not in ("auto", "male", "female", "duet"):
        raise HTTPException(status_code=400, detail=(
            "Gender vokal tidak dikenal. Pilih: auto, female, male, atau duet."))
    pitch_raw = str(req.pitch_shift or "auto").strip().lower()
    if pitch_raw in ("", "auto"):
        if cover_engine == "arrangement" or req.keep_original_vocal:
            # Keep original singer's exact natural pitch by default when overlaying original vocal
            voice_pitch_shift = 0
            voice_pitch_auto = False
        else:
            # Let Seed-VC auto-adjust median F0 to match the target female/male voice register
            # naturally without pushing high tenor choruses into chipmunk range.
            voice_pitch_shift = 0
            voice_pitch_auto = True
    else:
        try:
            voice_pitch_shift = int(float(pitch_raw))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail=(
                "pitch_shift harus 'auto' atau angka semitone (mis. 12, -12, 0)."))
        voice_pitch_shift = max(-24, min(24, voice_pitch_shift))
        voice_pitch_auto = False
    voice_mode = (req.voice_mode or "manual").strip().lower()
    if voice_mode not in ("manual", "auto"):
        voice_mode = "manual"
    if voice_mode == "auto" and vocal_gender == "auto":
        # Let Klipin pick a fitting register per genre when the user is unsure.
        _voice_hint = {"dangdut-koplo": "female", "dangdut-remix": "female",
                       "dangdut": "female", "dj-remix": "female", "edm": "female",
                       "pop-remix": "female", "reggae": "male", "rock": "male",
                       "dangdut-jawa": "female", "pop-jawa": "female"}
        vocal_gender = _voice_hint.get(req.genre, "female")
    persona = None
    persona_id = (req.persona_id or "").strip()
    if persona_id:
        persona = db.get_music_persona(persona_id, _user["google_sub"])
        if not persona:
            raise HTTPException(status_code=404, detail="Persona tidak ditemukan")
        if persona.get("vocal_gender") and vocal_gender == "auto":
            vocal_gender = str(persona["vocal_gender"]).strip().lower()
        if persona.get("voice_ref_path"):
            if vocal_gender == "duet":
                raise HTTPException(status_code=400, detail=(
                    "Persona rekaman dipakai untuk satu suara utama; pilih Pria atau Wanita."))
            capability = await asyncio.to_thread(voice_persona_capability)
            if not capability["ready"]:
                raise HTTPException(status_code=503, detail=capability["message"])
    try:
        strength = max(0.1, min(0.9, float(req.ref_strength or 0.6)))
    except (TypeError, ValueError):
        strength = 0.6

    def prepare_reference() -> dict:
        import base64
        import subprocess
        import tempfile
        from pathlib import Path as _Path
        from transcribe import transcribe

        work = _Path(tempfile.mkdtemp(prefix="music-remix-url-", dir=str(db.DATA_DIR)))
        try:
            resolved = resolve_url_metadata(url)
            source = download_audio_url(url, str(work), platform="youtube")
            normalized = work / "reference.mp3"
            result = subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-i", source,
                 "-t", "600", "-vn", "-ac", "2", "-ar", "44100",
                 "-c:a", "libmp3lame", "-b:a", "128k", str(normalized)],
                capture_output=True, text=True, timeout=180)
            if result.returncode != 0 or not normalized.exists():
                raise RuntimeError("Audio sumber gagal dinormalisasi.")
            manual = (req.lyrics or "").strip()
            if manual:
                # A user-supplied sheet keeps the original phrasing: one line
                # per sung sentence/phrase. Skip ASR so nothing re-wraps it.
                lyrics = _music_lyrics_from_manual(manual)
                lyrics_source = "user"
                lyrics_segments = 0
            else:
                try:
                    transcript = transcribe(str(normalized))
                    lyrics = _music_lyrics_from_transcript(transcript)
                    lyrics_source = str(transcript.get("source") or "audio_asr")
                    lyrics_segments = len(transcript.get("segments") or [])
                except Exception:
                    if cover_engine in ("arrangement", "voice"):
                        lyrics = "[Verse]\n(Vokal asli sumber dipertahankan)"
                        lyrics_source = "source_vocal_stem"
                        lyrics_segments = 1
                    else:
                        raise
            raw = normalized.read_bytes()
            if len(raw) > 15_000_000:
                raise RuntimeError("Audio sumber terlalu besar setelah normalisasi (maks. 15 MB).")
            info = resolved.get("info") or {}
            duration = max(15.0, min(float(info.get("duration") or 180), 600.0))
            return {"audio_b64": base64.b64encode(raw).decode("ascii") if needs_audio else "",
                    "title": str(info.get("title") or "YouTube remix"),
                    "duration": duration, "lyrics": lyrics,
                    "lyrics_source": lyrics_source,
                    "lyrics_segments": lyrics_segments}
        finally:
            shutil.rmtree(work, ignore_errors=True)

    try:
        ref = await asyncio.to_thread(prepare_reference)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Gagal mengambil audio dari link: {str(exc)[:500]}")

    title = (ref["title"] or "YouTube remix")[:180]
    prompt = (req.prompt or "").strip() or f"rearrange this song as {req.genre}"
    prompt = (
        f"{prompt}; preserve the extracted lyrics exactly, keep the original "
        "section order and emotional direction, and use a fresh singer; "
        f"make the arrangement clearly {req.genre}"
    )
    # Reggae/ska/dub live or die on the one-drop groove; a source ballad beat
    # leaks through MuLaCover's drum condition, so those genres drop the source
    # drums by default and let the model rebuild the groove from tags.
    groove_reset = bool(req.groove_reset) or (
        cover_engine in ("mulacover", "arrangement") and req.genre in (
            "reggae", "ska", "dub", "dangdut-koplo", "dangdut-remix", "dj-remix", "edm"))
    source_duration = ref["duration"]
    keep_original_vocal = bool(req.keep_original_vocal or cover_engine == "arrangement")
    base_spec = {
        "title": f"{title} — {req.genre}", "prompt": prompt,
        "lyrics": ref["lyrics"],
        "genre": req.genre, "language": "id", "language_override": True,
        "vocal_gender": vocal_gender, "provider": cover_engine,
        "duration": source_duration,
        "seed": int(time.time()) % (2 ** 31 - 1), "full_song": True,
        "take_mode": "final", "instrumental": False, "bpm": 0, "music_key": "",
        "persona_id": persona_id, "style_tags": (persona or {}).get("style_tags", ""),
        "parent_job_id": "", "ref_strength": strength,
        "voice_pitch_shift": voice_pitch_shift, "voice_pitch_auto": voice_pitch_auto,
        "voice_mode": voice_mode,
        "restyle": bool(req.restyle or cover_engine == "arrangement"),
        "keep_original_vocal": keep_original_vocal,
        "restyle_strength": max(0.1, min(0.9, float(req.restyle_strength or 0.42))),
        "cover_mode": True, "cover_engine": cover_engine, "_lyrics_locked": True,
        "groove_reset": groove_reset,
        "lyrics_extraction_source": ref["lyrics_source"],
        "lyrics_extraction_segments": ref["lyrics_segments"],
        "remix_source_url": url, "remix_source_title": title,
        "remix_source_duration": source_duration,
    }
    if needs_ref:
        base_spec["ref_audio_b64"] = ref["audio_b64"]
    elif cover_engine in ("voice", "arrangement"):
        base_spec["cover_source_b64"] = ref["audio_b64"]
    if persona and persona.get("voice_ref_path"):
        base_spec["voice_persona_id"] = persona_id
    grouped = _create_music_group(base_spec, _user["google_sub"], 1, "final",
                                  user=_user)
    return {**grouped, "remix_source_title": title, "remix_source_url": url,
            "genre": req.genre, "duration": source_duration,
            "source_duration": source_duration, "lyrics_locked": True,
            "lyrics_source": ref["lyrics_source"], "cover_engine": cover_engine,
            "groove_reset": groove_reset, "draft_duration": source_duration}


class MusicShareRequest(BaseModel):
    public: bool = True


@app.post("/api/music/share/{job_id}")
async def music_share(job_id: str, req: MusicShareRequest, _user: dict = Depends(require_user)):
    job = db.set_music_public(job_id, _user["google_sub"], bool(req.public))
    if not job:
        raise HTTPException(status_code=404, detail="Music job not found")
    return {"job_id": job_id, "is_public": bool(job.get("is_public"))}


@app.get("/api/music/feed")
async def music_feed(limit: int = 30):
    """Feed publik (tanpa login): lagu yang dibagikan pemiliknya."""
    limit = max(1, min(int(limit), 50))
    return {"songs": db.public_music_feed(limit)}


@app.get("/api/music/cost")
async def music_cost_summary(_user: dict = Depends(require_user)):
    """Metering: total takes, detik GPU, USD, dan kredit tercatat."""
    from music import gpu_rate_usd_per_sec
    return {**db.music_cost_total(_user["google_sub"]),
            "rate_usd_per_sec": gpu_rate_usd_per_sec()}


@app.post("/api/music/lyrics")
async def music_lyrics(req: MusicLyricsRequest, _user: dict = Depends(require_user)):
    """Gemini untuk kata: compose/expand lirik duration-sized (text-only, murah)."""
    from music import genre_info, resolve_language, language_mix_prompt
    language, _ = resolve_language(req.genre, req.language,
                                   override=bool(req.language_override))
    from music import normalize_language_mix
    try:
        secondary, mix = normalize_language_mix(
            language, req.language_secondary, req.language_mix)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    spec = {"title": (req.title or "").strip() or "Tanpa Judul",
            "prompt": (req.prompt or "").strip(),
            "lyrics": (req.lyrics or "").strip(),
            "genre": req.genre, "language": language,
            "language_secondary": secondary, "language_mix": mix,
            "vocal_gender": "auto", "duration": float(req.duration or 180)}
    lyric_language = language_mix_prompt(spec)
    mode = (req.mode or "compose").strip().lower()
    try:
        from lyrics_writer import (expand_lyrics, compose_lyrics, refine_lyrics,
                                   suggest_titles, target_lines, rewrite_section)
        info = genre_info(req.genre)
        if mode == "suggest":
            res = await asyncio.to_thread(
                suggest_titles, spec["title"], spec["prompt"], spec["lyrics"],
                req.genre, lyric_language)
            res["genre_label"] = info["label"]
            res["language"] = language
            return res
        if mode == "section":
            if not spec["lyrics"]:
                raise HTTPException(status_code=400, detail=(
                    "Tulis atau buat lirik dulu sebelum mengedit satu bagian."))
            res = await asyncio.to_thread(
                rewrite_section, spec["lyrics"], int(req.section_index or 0),
                req.instruction, spec["title"], req.genre, lyric_language)
            res["genre_label"] = info["label"]
            res["language"] = language
            return res
        if mode == "refine" and spec["lyrics"]:
            res = await asyncio.to_thread(
                refine_lyrics, spec["lyrics"], spec["title"],
                req.genre, lyric_language)
        elif mode == "expand" and spec["lyrics"]:
            res = await asyncio.to_thread(
                expand_lyrics, spec["lyrics"], spec["title"], spec["prompt"],
                req.genre, lyric_language, "auto", spec["duration"])
        else:
            res = await asyncio.to_thread(
                compose_lyrics, spec["title"], spec["prompt"] or spec["lyrics"] or spec["title"],
                req.genre, lyric_language, "auto", spec["duration"])
        res["genre_label"] = info["label"]
        res["language"] = language
        res["target_lines"] = target_lines(spec["duration"])
        return res
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)[:500])


@app.post("/api/music/plan")
async def music_plan(req: MusicPlanRequest, _user: dict = Depends(require_user)):
    """Rencana aransemen per seksi + hook — tanpa render GPU, tanpa biaya Modal.

    ``ai=1`` menambah saran style tag dan varian hook lewat Gemini teks murah;
    default memakai jalur deterministik yang tetap jalan tanpa kuota teks.
    """
    from music import language_info, resolve_language
    from song_plan import build_plan, summary
    language, language_locked = resolve_language(
        req.genre, req.language, override=bool(req.language_override))
    spec = {
        "title": (req.title or "").strip() or "Tanpa Judul",
        "prompt": (req.prompt or "").strip(),
        "lyrics": (req.lyrics or "").strip(),
        "genre": req.genre, "language": language,
        "language_secondary": (req.language_secondary or "").strip(),
        "language_mix": req.language_mix, "instrumental": bool(req.instrumental),
        "vocal_gender": (req.vocal_gender or "auto").strip().lower(),
        "duration": float(req.duration or 180), "bpm": int(req.bpm or 0),
        "style_tags": (req.style_tags or "").strip(),
    }
    plan = await asyncio.to_thread(build_plan, spec, bool(req.ai))
    plan["summary"] = summary(plan)
    plan["language"] = language
    plan["language_label"] = language_info(language)["label"]
    plan["language_locked"] = language_locked
    return plan


@app.get("/api/music/group/{group_id}")
async def music_group(group_id: str, _user: dict = Depends(require_user)):
    """Status grup A/B (Suno-style 2 takes) + stage riil per take."""
    takes = [_reconcile_finished_music_job(take) for take in
             db.list_music_group(group_id, _user["google_sub"])]
    if not takes:
        raise HTTPException(status_code=404, detail="Music group not found")
    return {"group_id": group_id, "takes": takes}


@app.get("/api/music/group/{group_id}/stream")
async def music_group_stream(group_id: str, request: Request,
                             _user: dict = Depends(require_user)):
    """Live Server-Sent Events stream for one music group.

    Pushes the take list the moment any status/stage changes so the panel
    reacts in real time instead of waiting for the next poll. The client keeps
    polling as a fallback if the stream cannot be established.
    """
    user_id = _user["google_sub"]

    def snapshot() -> tuple[list, bool]:
        takes = [_reconcile_finished_music_job(take)
                 for take in db.list_music_group(group_id, user_id)]
        done = bool(takes) and all(
            take.get("status") in ("completed", "failed") for take in takes)
        return takes, done

    async def event_stream():
        takes, done = await asyncio.to_thread(snapshot)
        if not takes:
            yield 'event: error\ndata: {"detail":"Music group not found"}\n\n'
            return
        last = None
        deadline = time.time() + 30 * 60
        while True:
            if await request.is_disconnected():
                break
            payload = json.dumps({"group_id": group_id, "takes": takes},
                                 ensure_ascii=False, default=str)
            if payload != last:
                last = payload
                yield f"event: group\ndata: {payload}\n\n"
            if done:
                yield "event: end\ndata: {}\n\n"
                break
            if time.time() > deadline:
                break
            yield ": keep-alive\n\n"
            await asyncio.sleep(1.0)
            takes, done = await asyncio.to_thread(snapshot)

    return StreamingResponse(
        event_stream(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache",
                 "Connection": "keep-alive",
                 "X-Accel-Buffering": "no"})


class MusicExtendRequest(BaseModel):
    extra_lyrics: str = ""
    extra_duration: float = 60


@app.post("/api/music/extend/{job_id}")
async def music_extend(job_id: str, req: MusicExtendRequest, _user: dict = Depends(require_user)):
    """Extend: lanjutkan lagu dari parent (durasi + lirik tambahan)."""
    parent = db.get_music_job(job_id, _user["google_sub"])
    if not parent or parent.get("status") != "completed":
        raise HTTPException(status_code=400, detail="Induk harus completed dulu")
    extra = (req.extra_lyrics or "").strip()
    if len(extra) < 4:
        raise HTTPException(status_code=400, detail="Lirik tambahan minimal 4 karakter")
    new_duration = min(600.0, float(parent.get("duration") or 120) + max(15.0, float(req.extra_duration or 60)))
    child_id = "music_" + _new_job_id()
    now = time.time()
    lyrics = ((parent.get("lyrics") or "") + "\n[Extended]\n" + extra)[:6000]
    from music import _normalize_provider
    spec = {"title": parent.get("title", ""), "prompt": parent.get("prompt", ""),
            "lyrics": lyrics, "genre": parent.get("genre", "pop"),
            "language": parent.get("language", "id"),
            "provider": _normalize_provider(parent.get("provider", "auto")),
            "seed": (parent.get("seed") or 0) + 1000, "duration": new_duration,
            "vocal_gender": parent.get("vocal_gender", "auto"), "full_song": True,
            "take_mode": "final", "take_index": 0, "group_id": "mg_" + _new_job_id(),
            "parent_job_id": job_id, "lora_id": parent.get("lora_id", "")}
    from billing import enabled as _billing_enabled
    if _billing_enabled():
        from billing import reserve as _billing_reserve
        check = _billing_reserve(_user["google_sub"], child_id, user=_user)
        if not check["ok"]:
            raise HTTPException(status_code=402, detail=check["error"])
    db.create_music_job({**spec, "job_id": child_id, "user_id": _user["google_sub"],
                         "status": "queued", "created_at": now, "updated_at": now,
                         "metadata": json.dumps({"parent_job_id": job_id,
                                                 "stage": "queued-extend"})})
    _spawn_music_job(child_id, spec, _user["google_sub"])
    return {"job_id": child_id, "status": "queued", "parent_job_id": job_id,
            "duration": new_duration}


@app.post("/api/music/stems/{job_id}")
async def music_stems(job_id: str, _user: dict = Depends(require_user)):
    """Demucs stems (vocals/accompaniment). Async via thread, hasil di metadata."""
    job = db.get_music_job(job_id, _user["google_sub"])
    if not job or job.get("status") != "completed" or not job.get("audio_path"):
        raise HTTPException(status_code=400, detail="Audio completed dulu")

    def _work():
        from pathlib import Path as _P
        from music import separate_stems
        job_dir = OUTPUT_DIR / job_id
        # Resolve file audio lokal dari audio_path (/videos/{id}/{file}).
        fname = (job.get("audio_path") or "").rsplit("/", 1)[-1] or "song.mp3"
        src = job_dir / fname
        if not src.exists():
            cands = sorted(job_dir.glob("song.*"))
            src = cands[0] if cands else src
        stems = separate_stems(src, job_dir, log=None)
        meta = dict(job.get("metadata") or {})
        meta["stems"] = stems
        meta["stage"] = "done-stems" if stems else "stems-failed"
        db.update_music_job(job_id, _user["google_sub"],
                            metadata=json.dumps(meta, ensure_ascii=False))
        return stems

    stems = await asyncio.to_thread(_work)
    return {"job_id": job_id, "stems": stems}


class MusicCoverRequest(BaseModel):
    cover_url: str = ""
    query: str = ""


@app.post("/api/music/cover/{job_id}")
async def music_cover(job_id: str, req: MusicCoverRequest, _user: dict = Depends(require_user)):
    """Ganti / isi ulang cover art lagu: upload URL atau cari via Pexels."""
    from pathlib import Path as _Path
    import requests as _requests
    job = db.get_music_job(job_id, _user["google_sub"])
    if not job:
        raise HTTPException(status_code=404, detail="Music job not found")
    job_dir = OUTPUT_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    target = job_dir / "cover.jpg"
    source = ""
    try:
        raw = req.cover_url.strip()
        if raw.startswith("data:"):
            # Browser upload: data URL dari FileReader — decode langsung.
            import base64 as _b64
            header, _, b64 = raw.partition(",")
            if "base64" not in header or not b64:
                raise RuntimeError("Invalid data URL cover")
            target.write_bytes(_b64.b64decode(b64))
            source = "upload-file"
        elif raw.startswith("http://") or raw.startswith("https://"):
            dl = await asyncio.to_thread(
                _requests.get, raw,
                headers={"User-Agent": "Klipin-Music/1.0"}, timeout=60)
            dl.raise_for_status()
            target.write_bytes(dl.content)
            source = "upload-url"
        else:
            from music import _fetch_cover_art, genre_info
            info = genre_info(job.get("genre") or "pop-jawa")
            prompt = (req.query or job.get("prompt") or job.get("title") or "").strip()
            fname, source = await asyncio.to_thread(
                _fetch_cover_art,
                {"prompt": prompt, "title": job.get("title") or ""}, info, _Path(str(job_dir)))
            if not fname:
                raise RuntimeError("Cover generation failed")
        cover_path = f"/videos/{job_id}/{target.name}"
        db.update_music_job(job_id, _user["google_sub"], cover_path=cover_path, cover_source=source,
                            metadata=json.dumps({**(job.get("metadata") or {}),
                                                 "cover_path": cover_path, "cover_source": source},
                                                ensure_ascii=False))
        return {"cover_path": cover_path, "cover_source": source}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)[:500])


@app.get("/api/music/status/{job_id}")
async def music_status(job_id: str, _user: dict = Depends(require_user)):
    job = db.get_music_job(job_id, _user["google_sub"])
    if not job:
        raise HTTPException(status_code=404, detail="Music job not found")
    return _reconcile_finished_music_job(job)


@app.post("/api/music/align/{job_id}")
async def music_realign(job_id: str, _user: dict = Depends(require_user)):
    """Hitung ulang word alignment + beat grid untuk lagu yang sudah selesai."""
    from pathlib import Path
    from music import align_timeline, detect_downbeats, probe_audio_seconds

    job = db.get_music_job(job_id, _user["google_sub"])
    if not job:
        raise HTTPException(status_code=404, detail="Music job not found")
    if job.get("status") != "completed":
        raise HTTPException(status_code=400, detail="Lagu belum selesai.")
    job_dir = Path(OUTPUT_DIR) / job_id
    audio_name = Path(job.get("audio_path") or "song.mp3").name or "song.mp3"
    audio = job_dir / audio_name
    if not audio.exists():
        for candidate in ("song.mp3", "song.wav", "audio.mp3"):
            if (job_dir / candidate).exists():
                audio = job_dir / candidate
                break
    if not audio.exists():
        raise HTTPException(status_code=404, detail="Audio lagu tidak ditemukan.")
    lyrics = (job.get("lyrics") or "").strip()
    if not lyrics:
        lyrics_file = job_dir / "lyrics.txt"
        if lyrics_file.exists():
            lyrics = lyrics_file.read_text(encoding="utf-8").strip()
    if not lyrics:
        raise HTTPException(status_code=400, detail="Lagu ini tidak punya lirik.")

    duration = float(job.get("duration") or 0) or await asyncio.to_thread(
        probe_audio_seconds, audio)
    timeline = await asyncio.to_thread(align_timeline, audio, lyrics, duration)
    grid = await asyncio.to_thread(detect_downbeats, audio, duration)
    aligned = sum(1 for line in timeline if line.get("source") == "whisper")

    metadata = dict(job.get("metadata") or {})
    metadata.update({
        "lyrics_timeline": timeline,
        "alignment": "whisper" if aligned else "even",
        "aligned_lines": aligned,
        "words_aligned": sum(len(line.get("words") or []) for line in timeline),
        "word_alignment": bool(any(line.get("words") for line in timeline)),
        "beat_bpm": grid.get("bpm"),
        "beats": grid.get("beats", []),
        "downbeats": grid.get("downbeats", []),
        "beat_source": grid.get("source", ""),
    })
    updated = db.update_music_job(
        job_id, _user["google_sub"],
        metadata=json.dumps(metadata, ensure_ascii=False))
    return _reconcile_finished_music_job(updated)


@app.get("/api/music/jobs")
async def music_jobs(q: str = "", _user: dict = Depends(require_user)):
    jobs = db.list_music_jobs(_user["google_sub"], query=q or "")
    return {"jobs": [_reconcile_finished_music_job(job) for job in jobs]}


@app.get("/api/music/projects")
async def music_projects(_user: dict = Depends(require_user)):
    """Daftar studio project musik (versi/revisi) milik akun."""
    return {"projects": db.list_music_projects(_user["google_sub"])}


@app.post("/api/music/projects")
async def music_project_create(req: MusicProjectCreateRequest,
                               _user: dict = Depends(require_user)):
    """Simpan form + rencana aransemen sebagai proyek baru (revisi 1)."""
    snapshot = _music_project_snapshot(req.snapshot)
    name = (req.name or "").strip()[:80] or "Proyek tanpa nama"
    project = db.create_music_project(
        _user["google_sub"], name, snapshot,
        job_id=(req.job_id or "").strip(), group_id=(req.group_id or "").strip())
    if not project:
        raise HTTPException(status_code=500, detail="Gagal menyimpan proyek.")
    return project


@app.get("/api/music/projects/{project_id}")
async def music_project_get(project_id: str,
                            _user: dict = Depends(require_user)):
    """Proyek + daftar revisi + snapshot revisi terbaru."""
    project = db.get_music_project(project_id, _user["google_sub"])
    if not project:
        raise HTTPException(status_code=404, detail="Music project not found")
    return project


@app.delete("/api/music/projects/{project_id}")
async def music_project_delete(project_id: str,
                               _user: dict = Depends(require_user)):
    if not db.delete_music_project(project_id, _user["google_sub"]):
        raise HTTPException(status_code=404, detail="Music project not found")
    return {"project_id": project_id, "deleted": True}


@app.post("/api/music/projects/{project_id}/revisions")
async def music_project_revision_add(project_id: str,
                                     req: MusicProjectRevisionRequest,
                                     _user: dict = Depends(require_user)):
    """Simpan revisi baru (snapshot form/plan) ke proyek yang sudah ada."""
    snapshot = _music_project_snapshot(req.snapshot)
    revision = db.save_music_project_revision(
        project_id, _user["google_sub"], snapshot,
        label=(req.label or "").strip()[:120],
        source_job_id=(req.source_job_id or "").strip())
    if not revision:
        raise HTTPException(status_code=404, detail="Music project not found")
    return revision


@app.get("/api/music/projects/{project_id}/revisions/{revision_id}")
async def music_project_revision_get(project_id: str, revision_id: str,
                                     _user: dict = Depends(require_user)):
    """Snapshot lengkap satu revisi (untuk dibuka kembali ke form)."""
    revision = db.get_music_project_revision(
        project_id, revision_id, _user["google_sub"])
    if not revision:
        raise HTTPException(status_code=404, detail="Music revision not found")
    return revision


@app.post("/api/music/projects/{project_id}/revisions/{revision_id}/restore")
async def music_project_revision_restore(project_id: str, revision_id: str,
                                         _user: dict = Depends(require_user)):
    """Buat revisi baru dari snapshot revisi lama (restore non-destruktif)."""
    source = db.get_music_project_revision(
        project_id, revision_id, _user["google_sub"])
    if not source:
        raise HTTPException(status_code=404, detail="Music revision not found")
    index = source.get("revision_index") or 0
    revision = db.save_music_project_revision(
        project_id, _user["google_sub"], source.get("snapshot") or {},
        label=f"restore revisi {index}",
        source_job_id=source.get("source_job_id") or "")
    if not revision:
        raise HTTPException(status_code=404, detail="Music project not found")
    return revision


@app.get("/api/music/lyrics-drafts")
async def music_lyrics_drafts(_user: dict = Depends(require_user)):
    """Daftar draft lirik (metadata saja) milik akun."""
    return {"drafts": db.list_music_lyric_drafts(_user["google_sub"])}


@app.post("/api/music/lyrics-drafts")
async def music_lyrics_draft_create(req: MusicLyricsDraftRequest,
                                    _user: dict = Depends(require_user)):
    fields = _music_lyrics_draft_fields(req)
    return db.create_music_lyric_draft(_user["google_sub"], **fields)


@app.get("/api/music/lyrics-drafts/{draft_id}")
async def music_lyrics_draft_get(draft_id: str,
                                 _user: dict = Depends(require_user)):
    draft = db.get_music_lyric_draft(draft_id, _user["google_sub"])
    if not draft:
        raise HTTPException(status_code=404, detail="Lyrics draft not found")
    return draft


@app.put("/api/music/lyrics-drafts/{draft_id}")
async def music_lyrics_draft_update(draft_id: str,
                                    req: MusicLyricsDraftRequest,
                                    _user: dict = Depends(require_user)):
    """Autosave/manual save: menimpa isi draft tanpa membuat versi baru."""
    fields = _music_lyrics_draft_fields(req)
    draft = db.update_music_lyric_draft(
        draft_id, _user["google_sub"], **fields)
    if not draft:
        raise HTTPException(status_code=404, detail="Lyrics draft not found")
    return draft


@app.delete("/api/music/lyrics-drafts/{draft_id}")
async def music_lyrics_draft_delete(draft_id: str,
                                    _user: dict = Depends(require_user)):
    if not db.delete_music_lyric_draft(draft_id, _user["google_sub"]):
        raise HTTPException(status_code=404, detail="Lyrics draft not found")
    return {"draft_id": draft_id, "deleted": True}


@app.put("/api/brand-kit")
async def brand_kit_put(req: BrandKitRequest,
                        _user: dict = Depends(require_user)):
    """Save the user's brand kit."""
    return db.save_brand_kit(_user["google_sub"], req.kit)


@app.get("/api/ideas")
async def ideas_list(_user: dict = Depends(require_user)):
    """User's ideas board (content calendar)."""
    return {"ideas": db.list_ideas(_user["google_sub"])}


@app.post("/api/ideas")
async def ideas_create(req: IdeaRequest, _user: dict = Depends(require_user)):
    if not (req.title or "").strip():
        raise HTTPException(status_code=400, detail="Title is required")
    idea = db.create_idea(_user["google_sub"], req.title, req.description or "",
                          req.status, req.platform or "", req.scheduled_at,
                          req.source_url or "", req.job_id or "")
    return idea


@app.patch("/api/ideas/{idea_id}")
async def ideas_update(idea_id: int, req: IdeaRequest,
                       _user: dict = Depends(require_user)):
    if not db.get_idea(_user["google_sub"], idea_id):
        raise HTTPException(status_code=404, detail="Idea not found")
    payload = req.model_dump(exclude_none=True)
    # For PATCH, an empty/default title means "leave it as-is" — only a
    # non-empty title is accepted as an update.
    if not (payload.get("title") or "").strip():
        payload.pop("title", None)
    idea = db.update_idea(_user["google_sub"], idea_id, **payload)
    return idea


@app.delete("/api/ideas/{idea_id}")
async def ideas_delete(idea_id: int, _user: dict = Depends(require_user)):
    db.delete_idea(_user["google_sub"], idea_id)
    return {"ok": True}


@app.post("/api/process")
async def process_video(req: ProcessRequest, _user: dict = Depends(require_user)):
    if not req.url:
        raise HTTPException(status_code=400, detail="Provide a video URL")
    try:
        from video import _validate_download_url
        _validate_download_url(req.url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    job_id = _new_job_id()
    from billing import enabled as billing_enabled
    if billing_enabled():
        from billing import reserve
        check = reserve(_user["google_sub"], job_id, user=_user)
        if not check["ok"]:
            raise HTTPException(status_code=402, detail=check["error"])
    effective_options = _process_options_for_user(req.options, _user)
    created_at = time.time()
    with _jobs_lock:
        jobs[job_id] = {
            "status": "queued",
            "logs": [f"[{_now()}] job {job_id} queued"],
            "created_at": created_at,
            "user_id": _user["google_sub"],
            "options": effective_options,
        }
    db.save_project(job_id, source_label=req.url, status="queued",
                    created_at=created_at, options=effective_options,
                    user_id=_user["google_sub"])
    spec = {"kind": "process",
            "source": {"type": "url", "url": req.url,
                       "platform": req.platform or ""},
            "options": effective_options, "user_id": _user["google_sub"]}
    asyncio.create_task(_run_job(job_id, spec))
    return {"job_id": job_id}


@app.post("/api/upload")
async def upload_video(file: UploadFile = File(...),
                       options: str = Form("{}"),
                       _user: dict = Depends(require_user)):
    job_id = _new_job_id()
    job_dir = OUTPUT_DIR / job_id
    job_dir.mkdir(exist_ok=True)
    dest = job_dir / "source.mp4"
    size = 0
    with open(dest, "wb") as out:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_FILE_SIZE_MB * 1024 * 1024:
                raise HTTPException(status_code=413, detail="File too large")
            out.write(chunk)
    try:
        opts = json.loads(options) if options else {}
    except json.JSONDecodeError:
        opts = {}
    opts = _process_options_for_user(opts, _user)
    from billing import enabled as billing_enabled
    if billing_enabled():
        from billing import reserve
        check = reserve(_user["google_sub"], job_id, user=_user)
        if not check["ok"]:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise HTTPException(status_code=402, detail=check["error"])
    created_at = time.time()
    with _jobs_lock:
        jobs[job_id] = {
            "status": "queued",
            "logs": [f"[{_now()}] job {job_id} queued"],
            "created_at": created_at,
            "user_id": _user["google_sub"],
            "options": opts,
        }
    db.save_project(job_id, source_label=file.filename or "upload.mp4",
                    status="queued", created_at=created_at, options=opts,
                    user_id=_user["google_sub"])
    spec = {"kind": "process", "source": {"type": "upload", "path": str(dest)},
            "options": opts, "user_id": _user["google_sub"]}
    asyncio.create_task(_run_job(job_id, spec))
    return {"job_id": job_id}


@app.get("/api/status/{job_id}")
async def job_status(job_id: str, _user: dict = Depends(require_user)):
    with _jobs_lock:
        job = jobs.get(job_id)
    if job:
            # In-memory jobs carry their owner; a mismatched (or legacy missing)
            # owner is a 404 — the id of someone else's in-flight job must not be
            # distinguishable from a random string.
            if job.get("user_id") != _user["google_sub"]:
                raise HTTPException(status_code=404, detail="Job not found")
            # Live source metadata: the pipeline writes <work_dir>/source_meta.json
            # as soon as the video is fetched, so the frontend can render the video
            # preview card DURING processing (result is still null until the end).
            source_meta = (job.get("result") or {}).get("source_meta")
            if not source_meta:
                try:
                    meta_path = OUTPUT_DIR / job_id / "source_meta.json"
                    if meta_path.exists():
                        source_meta = json.loads(meta_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    source_meta = None
            return {
                "job_id": job_id,
                "status": job["status"],
                "attempt": int(job.get("attempt", 0) or 0),
                "degraded": job["status"] in ("queued_retry", "degraded"),
                "logs": job["logs"][-80:],
                "error": job.get("error"),
                "result": job.get("result"),
                "source_meta": source_meta,
                "created_at": job.get("created_at"),
            }
    # Fall back to the library (e.g. after a server restart).
    project = db.get_project(job_id, user_id=_user["google_sub"])
    if not project:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "job_id": job_id,
        "status": project["status"],
        "attempt": int(project.get("attempt", 0) or 0),
        "degraded": project.get("status") in ("queued_retry", "degraded"),
        "logs": [],
        "error": project.get("error"),
        "result": {"clips": project["clips"],
                   "transcript": project.get("transcript"),
                   "duration": project.get("duration"),
                   "source_meta": project.get("source_meta", {})},
        "created_at": project.get("created_at"),
    }


@app.post("/api/projects/{job_id}/clips/{clip_idx}/retrim")
async def retrim_clip(job_id: str, clip_idx: int, req: RetrimRequest,
                      _user: dict = Depends(require_user)):
    if not _own_project(job_id, _user["google_sub"]):
        raise HTTPException(status_code=404, detail="Project not found")
    clips = db.get_clips(job_id)
    if clip_idx < 0 or clip_idx >= len(clips):
        raise HTTPException(status_code=404, detail="Clip not found")
    if req.subject_lock is not None:
        # Persist the manual subject lock before the job starts so run_retrim
        # (which re-reads the clip row) picks it up. [] clears the lock.
        if len(req.subject_lock) == 2:
            fx, fy = float(req.subject_lock[0]), float(req.subject_lock[1])
            if not (0.0 <= fx <= 1.0 and 0.0 <= fy <= 1.0):
                raise HTTPException(
                    status_code=422,
                    detail="subject_lock must be normalized [x, y] in 0..1")
            db.update_clip(job_id, clip_idx,
                           subject_lock=json.dumps([fx, fy]))
        else:
            db.update_clip(job_id, clip_idx, subject_lock=None)
    job_id_new = _new_job_id()
    with _jobs_lock:
        jobs[job_id_new] = {
            "status": "queued",
            "logs": [f"[{_now()}] retrim job {job_id_new} queued"],
            "created_at": time.time(),
            "user_id": _user["google_sub"],
        }
    spec = {"kind": "retrim", "job_id": job_id, "clip_idx": clip_idx,
            "start": req.start, "end": req.end, "options": req.options}
    asyncio.create_task(_run_job(job_id_new, spec))
    return {"job_id": job_id_new, "clip_idx": clip_idx}


@app.post("/api/projects/{job_id}/clips/{clip_idx}/layout-preview")
async def preview_clip_layout(job_id: str, clip_idx: int,
                               req: OptionsPatchRequest,
                               _user: dict = Depends(require_user)):
    """Render a separate layout proof; preserve the saved clip and its revision."""
    if not _own_project(job_id, _user["google_sub"]):
        raise HTTPException(status_code=404, detail="Project not found")
    clips = db.get_clips(job_id)
    if clip_idx < 0 or clip_idx >= len(clips):
        raise HTTPException(status_code=404, detail="Clip not found")
    clip = clips[clip_idx]
    project = db.get_project(job_id)
    options = {**(project.get("options") or {}), **(req.options or {})}
    if clip.get("subject_lock"):
        lock = clip["subject_lock"]
        options["subject_lock"] = json.loads(lock) if isinstance(lock, str) else lock
    if clip.get("layout_plan_edited"):
        options["layout_plan_override"] = clip.get("layout_plan")
    from layout_preview import render_preview
    root = OUTPUT_DIR / job_id
    try:
        target = await asyncio.to_thread(render_preview, root / "source.mp4", root,
                                         {"start": clip["start"], "end": clip["end"]}, options)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"video_url": _sign_media_url(f"/videos/{job_id}/{target.relative_to(root).as_posix()}"),
            "layout_only": True, "edited_plan": bool(clip.get("layout_plan_edited"))}


# Strategies the per-scene layout editor may assign to a scene. These map
# 1:1 onto reframe_scenes / crop_video render strategies ("screencast" is a
# UI alias of "gameplay" and is normalized here).
LAYOUT_PLAN_STRATEGIES = ("track", "general", "split", "panel", "center",
                          "gameplay")
LAYOUT_PLAN_ALIASES = {"screencast": "gameplay", "face": "track",
                       "auto": "track"}


@app.put("/api/projects/{job_id}/clips/{clip_idx}/layout-plan")
async def set_clip_layout_plan(job_id: str, clip_idx: int,
                               req: LayoutPlanRequest,
                               _user: dict = Depends(require_user)):
    """Save a user-edited per-scene layout plan for ONE clip.

    Times are clip-local seconds (0 = clip start). The plan is normalized
    (sorted, overlap-merged, clamped to the clip duration) and rendered
    as-is on the next re-render — no AI re-analysis. Send plan=[] (or omit
    it) to reset to the AI-generated plan.
    """
    if not _own_project(job_id, _user["google_sub"]):
        raise HTTPException(status_code=404, detail="Project not found")
    clips = db.get_clips(job_id)
    if clip_idx < 0 or clip_idx >= len(clips):
        raise HTTPException(status_code=404, detail="Clip not found")
    clip = clips[clip_idx]
    duration = max(0.0, float(clip.get("end", 0)) - float(clip.get("start", 0)))

    if not req.plan:
        # Reset: drop the edited plan; AI plan (already stored) applies again.
        db.update_clip(job_id, clip_idx, layout_plan_edited=0)
        return {"clip_idx": clip_idx, "layout_plan": clip.get("layout_plan"),
                "layout_plan_edited": False}

    scenes = []
    for sc in req.plan:
        try:
            s = float(sc.get("start", 0))
            e = float(sc.get("end", 0))
        except (TypeError, ValueError):
            raise HTTPException(status_code=422,
                                detail="scene start/end must be numbers")
        layout = str(sc.get("layout", "track") or "track").strip().lower()
        layout = LAYOUT_PLAN_ALIASES.get(layout, layout)
        if layout not in LAYOUT_PLAN_STRATEGIES:
            raise HTTPException(
                status_code=422, detail=f"unknown layout strategy: {layout}")
        s = max(0.0, min(s, duration))
        e = max(0.0, min(e, duration))
        if e - s >= 0.5:
            scenes.append({"start": round(s, 2), "end": round(e, 2),
                           "strategy": layout, "layout": layout})
    scenes.sort(key=lambda sc: sc["start"])
    # Merge overlaps: keep the later scene inside the earlier one's span.
    merged = []
    for sc in scenes:
        if merged and sc["start"] < merged[-1]["end"]:
            if sc["end"] <= merged[-1]["end"]:
                continue  # fully covered — drop
            merged[-1]["end"] = sc["start"]  # trim previous
            if merged[-1]["end"] - merged[-1]["start"] < 0.5:
                merged.pop()
        merged.append(sc)
    if not merged:
        raise HTTPException(status_code=422,
                            detail="plan has no scene of at least 0.5s")
    db.update_clip(job_id, clip_idx,
                   layout_plan=json.dumps(merged), layout_plan_edited=1)
    return {"clip_idx": clip_idx, "layout_plan": merged,
            "layout_plan_edited": True}


LAYER_KINDS = {"broll", "text", "channel", "image", "audio", "caption"}


def _normalize_layer_plan(layers: list[dict], duration: float) -> list[dict]:
    if len(layers) > 80:
        raise HTTPException(status_code=422, detail="layer plan exceeds 80 layers")
    out = []
    for index, raw in enumerate(layers):
        kind = str(raw.get("kind", "text")).strip().lower()
        if kind not in LAYER_KINDS:
            raise HTTPException(status_code=422, detail=f"unknown layer kind: {kind}")
        try:
            start = max(0.0, min(float(raw.get("start", 0)), duration))
            end = max(start + 0.1, min(float(raw.get("end", duration)), duration))
            x = max(0.0, min(float(raw.get("x", .5)), 1.0))
            y = max(0.0, min(float(raw.get("y", .5)), 1.0))
            width = max(.05, min(float(raw.get("width", .7)), 1.0))
            opacity = max(0.0, min(float(raw.get("opacity", 1)), 1.0))
            rotation = max(-180.0, min(float(raw.get("rotation", 0)), 180.0))
            volume = max(0.0, min(float(raw.get("volume", .3)), 1.5))
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="invalid numeric layer value")
        if end <= start:
            continue
        layer = {
            "id": str(raw.get("id") or f"layer-{index + 1}")[:80],
            "kind": kind, "label": str(raw.get("label") or kind.title())[:80],
            "start": round(start, 3), "end": round(end, 3),
            "x": round(x, 4), "y": round(y, 4), "width": round(width, 4),
            "opacity": round(opacity, 3), "visible": bool(raw.get("visible", True)),
            "locked": bool(raw.get("locked", False)),
            "rotation": round(rotation, 2), "volume": round(volume, 3),
            "keep_original": bool(raw.get("keep_original", True)),
        }
        for key in ("text", "color", "asset_url", "media_path", "animation", "transition"):
            if raw.get(key) is not None:
                layer[key] = str(raw.get(key))[:500]
        out.append(layer)
    return sorted(out, key=lambda item: (item["start"], item["kind"], item["id"]))


@app.post("/api/projects/{job_id}/clips/{clip_idx}/layer-plan/media")
async def upload_clip_layer_media(job_id: str, clip_idx: int,
                                  file: UploadFile = File(...),
                                  kind: str = Form("image"),
                                  start: float = Form(0.0),
                                  end: float = Form(4.0),
                                  _user: dict = Depends(require_user)):
    """Store an editable media asset and append it to the layer plan."""
    if not _own_project(job_id, _user["google_sub"]):
        raise HTTPException(status_code=404, detail="Project not found")
    clips = db.get_clips(job_id)
    if clip_idx < 0 or clip_idx >= len(clips):
        raise HTTPException(status_code=404, detail="Clip not found")
    if kind not in ("image", "audio", "broll"):
        raise HTTPException(status_code=422, detail="kind must be image, audio, or broll")
    ext = os.path.splitext(file.filename or "")[1].lower()
    allowed = {"image": {".png", ".jpg", ".jpeg", ".webp"},
               "audio": {".mp3", ".wav", ".m4a", ".aac", ".ogg"},
               "broll": {".mp4", ".mov", ".webm", ".mkv"}}
    if ext not in allowed[kind]:
        raise HTTPException(status_code=422, detail=f"Unsupported {kind} file")
    media_dir = OUTPUT_DIR / job_id / "inserts"
    media_dir.mkdir(parents=True, exist_ok=True)
    safe_name = f"layer_{clip_idx + 1}_{int(time.time() * 1000)}{ext}"
    media_path = media_dir / safe_name
    with open(media_path, "wb") as out_file:
        shutil.copyfileobj(file.file, out_file)
    if media_path.stat().st_size > 200 * 1024 * 1024:
        media_path.unlink(missing_ok=True)
        raise HTTPException(status_code=413, detail="Media layer exceeds 200 MB")
    clip = clips[clip_idx]
    duration = max(0.1, float(clip.get("end", 0)) - float(clip.get("start", 0)))
    layer = {
        "id": f"media-{int(time.time() * 1000)}", "kind": kind,
        "label": (file.filename or kind)[:80], "text": "",
        "start": start, "end": end, "x": .5, "y": .5,
        "width": .72 if kind == "image" else 1.0, "opacity": 1.0,
        "visible": True, "locked": False, "color": "#FFFFFF",
        "media_path": str(media_path),
        "asset_url": f"/videos/{job_id}/inserts/{safe_name}",
        "animation": "none", "transition": "fade",
    }
    layers = _normalize_layer_plan((clip.get("layer_plan") or []) + [layer], duration)
    db.update_clip(job_id, clip_idx, layer_plan=json.dumps(layers))
    return {"clip_idx": clip_idx, "layer": next(x for x in layers if x["id"] == layer["id"]), "layers": layers}


@app.get("/api/projects/{job_id}/clips/{clip_idx}/layer-plan")
async def get_clip_layer_plan(job_id: str, clip_idx: int,
                              _user: dict = Depends(require_user)):
    if not _own_project(job_id, _user["google_sub"]):
        raise HTTPException(status_code=404, detail="Project not found")
    clips = db.get_clips(job_id)
    if clip_idx < 0 or clip_idx >= len(clips):
        raise HTTPException(status_code=404, detail="Clip not found")
    return {"clip_idx": clip_idx, "layers": clips[clip_idx].get("layer_plan") or []}


@app.put("/api/projects/{job_id}/clips/{clip_idx}/layer-plan")
async def set_clip_layer_plan(job_id: str, clip_idx: int,
                              req: LayerPlanRequest,
                              _user: dict = Depends(require_user)):
    if not _own_project(job_id, _user["google_sub"]):
        raise HTTPException(status_code=404, detail="Project not found")
    clips = db.get_clips(job_id)
    if clip_idx < 0 or clip_idx >= len(clips):
        raise HTTPException(status_code=404, detail="Clip not found")
    clip = clips[clip_idx]
    duration = max(0.1, float(clip.get("end", 0)) - float(clip.get("start", 0)))
    layers = _normalize_layer_plan(req.layers, duration)
    db.update_clip(job_id, clip_idx, layer_plan=json.dumps(layers))
    return {"clip_idx": clip_idx, "layers": layers, "saved": True}


@app.post("/api/projects/{job_id}/clips/{clip_idx}/layer-plan/render")
async def render_clip_layer_plan(job_id: str, clip_idx: int,
                                 _user: dict = Depends(require_user)):
    if not _own_project(job_id, _user["google_sub"]):
        raise HTTPException(status_code=404, detail="Project not found")
    clips = db.get_clips(job_id)
    if clip_idx < 0 or clip_idx >= len(clips):
        raise HTTPException(status_code=404, detail="Clip not found")
    job_id_new = _new_job_id()
    with _jobs_lock:
        jobs[job_id_new] = {
            "status": "queued",
            "logs": [f"[{_now()}] layer render {job_id_new} queued"],
            "created_at": time.time(), "user_id": _user["google_sub"],
        }
    asyncio.create_task(_run_job(job_id_new, {
        "kind": "layer_plan", "job_id": job_id, "clip_idx": clip_idx,
    }))
    return {"job_id": job_id_new, "clip_idx": clip_idx}


@app.post("/api/projects/{job_id}/clips/{clip_idx}/proxy")
async def create_clip_proxy(job_id: str, clip_idx: int,
                            _user: dict = Depends(require_user)):
    if not _own_project(job_id, _user["google_sub"]):
        raise HTTPException(status_code=404, detail="Project not found")
    if clip_idx < 0 or clip_idx >= len(db.get_clips(job_id)):
        raise HTTPException(status_code=404, detail="Clip not found")
    proxy = OUTPUT_DIR / job_id / f"clip_{clip_idx + 1}_proxy.mp4"
    final = OUTPUT_DIR / job_id / f"clip_{clip_idx + 1}.mp4"
    url = f"/videos/{job_id}/clip_{clip_idx + 1}_proxy.mp4"
    if proxy.exists() and final.exists() and proxy.stat().st_mtime >= final.stat().st_mtime:
        return {"ready": True, "proxy_url": url}
    job_id_new = _new_job_id()
    with _jobs_lock:
        jobs[job_id_new] = {"status": "queued", "logs": [f"[{_now()}] proxy queued"],
                            "created_at": time.time(), "user_id": _user["google_sub"]}
    asyncio.create_task(_run_job(job_id_new, {"kind": "proxy", "job_id": job_id,
                                               "clip_idx": clip_idx}))
    return {"ready": False, "job_id": job_id_new}


@app.get("/api/projects/{job_id}/clips/{clip_idx}/captions")
async def clip_captions(job_id: str, clip_idx: int,
                        _user: dict = Depends(require_user)):
    """Caption blocks that WILL be burned for a clip (source-timed), with any
    saved typo fixes already applied — powers the in-app subtitle editor."""
    from subtitles import clip_caption_blocks

    project = _own_project(job_id, _user["google_sub"])
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    clips = db.get_clips(job_id)
    if clip_idx < 0 or clip_idx >= len(clips):
        raise HTTPException(status_code=404, detail="Clip not found")
    sc = clips[clip_idx]
    options = project.get("options") or {}
    clip = {"start": sc["start"], "end": sc["end"],
            "title": sc.get("title", ""), "hook": sc.get("hook", "")}
    transcript = project.get("transcript") or {"segments": []}
    blocks = clip_caption_blocks(
        transcript, clip,
        emoji=bool(options.get("emoji", True)),
        lang=options.get("translate_to", ""),
        words_per_line=options.get("words_per_line", 3))
    edits = sc.get("caption_edits") or {}
    from subtitles import sanitize_caption_text
    for b in blocks:
        b["text"] = sanitize_caption_text(
            edits.get(f"{b['start']:.2f}", b["text"]))
    return {"blocks": blocks}


@app.post("/api/projects/{job_id}/clips/{clip_idx}/caption")
async def edit_clip_caption(job_id: str, clip_idx: int, req: CaptionEditRequest,
                            _user: dict = Depends(require_user)):
    """Fix caption typo(s) for one clip and re-burn it (fast, no AI)."""
    if not _own_project(job_id, _user["google_sub"]):
        raise HTTPException(status_code=404, detail="Project not found")
    clips = db.get_clips(job_id)
    if clip_idx < 0 or clip_idx >= len(clips):
        raise HTTPException(status_code=404, detail="Clip not found")
    job_id_new = _new_job_id()
    with _jobs_lock:
        jobs[job_id_new] = {
            "status": "queued",
            "logs": [f"[{_now()}] caption-edit job {job_id_new} queued"],
            "created_at": time.time(),
            "user_id": _user["google_sub"],
        }
    spec = {"kind": "caption_edit", "job_id": job_id, "clip_idx": clip_idx,
            "lines": req.lines}
    asyncio.create_task(_run_job(job_id_new, spec))
    return {"job_id": job_id_new, "clip_idx": clip_idx}


@app.post("/api/projects/{job_id}/clips/{clip_idx}/caption/autofix")
async def autofix_clip_caption(job_id: str, clip_idx: int,
                               req: CaptionEditRequest,
                               _user: dict = Depends(require_user)):
    """AI typo auto-fix: correct the caption lines with Gemini. Suggestion
    only — the user reviews the result, then saves via POST .../caption."""
    if not _own_project(job_id, _user["google_sub"]):
        raise HTTPException(status_code=404, detail="Project not found")
    clips = db.get_clips(job_id)
    if clip_idx < 0 or clip_idx >= len(clips):
        raise HTTPException(status_code=404, detail="Clip not found")
    from autofix import fix_caption_lines
    try:
        lines = fix_caption_lines(req.lines or [])
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    # Gemini can echo tofu characters (U+FFFD etc.) back into captions —
    # sanitize before the user reviews/saves them.
    from subtitles import sanitize_caption_text
    return {"lines": [sanitize_caption_text(x) for x in lines]}


@app.post("/api/projects/{job_id}/clips/{clip_idx}/meta")
async def update_clip_meta(job_id: str, clip_idx: int, req: ClipMetaRequest,
                           _user: dict = Depends(require_user)):
    if not _own_project(job_id, _user["google_sub"]):
        raise HTTPException(status_code=404, detail="Project not found")
    fields = {}
    if req.title is not None:
        fields["title"] = req.title
    if req.hook is not None:
        fields["hook"] = req.hook
    if fields:
        db.update_clip(job_id, clip_idx, **fields)
    return {"ok": True}


@app.post("/api/projects/{job_id}/export")
async def export_project(job_id: str, req: ExportRequest,
                         _user: dict = Depends(require_user)):
    if not _own_project(job_id, _user["google_sub"]):
        raise HTTPException(status_code=404, detail="Project not found")
    indices = list(req.indices or [])
    if req.only_approved:
        # Approval workflow: export only clips a reviewer approved.
        approved = {c["index"] for c in db.get_clips(job_id)
                    if c.get("approval_status") == "approved"}
        indices = ([i for i in indices if i in approved]
                   if indices else sorted(approved))
        if not indices:
            raise HTTPException(
                status_code=422,
                detail="No approved clips in selection - approve clips first")
    job_id_new = _new_job_id()
    with _jobs_lock:
        jobs[job_id_new] = {
            "status": "queued",
            "logs": [f"[{_now()}] export job {job_id_new} queued"],
            "created_at": time.time(),
            "user_id": _user["google_sub"],
        }
    spec = {"kind": "export", "job_id": job_id, "indices": indices,
            "resolution": req.resolution, "aspect": req.aspect}
    asyncio.create_task(_run_job(job_id_new, spec))
    return {"job_id": job_id_new}


@app.post("/api/projects/{job_id}/clips/{clip_idx}/approval")
async def clip_approval(job_id: str, clip_idx: int, req: ApprovalRequest,
                        _user: dict = Depends(require_user)):
    """Set the review decision for ONE clip (agency/team approval workflow).

    status: 'approved' | 'changes_requested' | 'pending' | '' (clear).
    Export can be gated to approved-only clips (ExportRequest.only_approved).
    """
    if not _own_project(job_id, _user["google_sub"]):
        raise HTTPException(status_code=404, detail="Project not found")
    clips = db.get_clips(job_id)
    if clip_idx < 0 or clip_idx >= len(clips):
        raise HTTPException(status_code=404, detail="Clip not found")
    status = str(req.status or "").strip().lower()
    if status not in ("approved", "changes_requested", "pending", ""):
        raise HTTPException(status_code=422, detail=f"unknown status: {status}")
    note = (req.note or "").strip()[:500]
    db.update_clip(job_id, clip_idx, approval_status=status,
                   approval_note=note or None)
    return {"clip_idx": clip_idx, "approval_status": status, "note": note}


# ─── social publishing (OAuth + publish) ───────────────────────────────
# Pending OAuth states (CSRF protection): state -> (user_id, platform, expiry)
_oauth_states: dict = {}


@app.get("/api/publish/config")
async def publish_config(_user: dict = Depends(require_user)):
    from publish import config_status
    return config_status(_user["google_sub"])


@app.get("/api/publish/{platform}/auth-url")
async def publish_auth_url(platform: str,
                           _user: dict = Depends(require_user)):
    from publish import auth_url, base_url, is_configured
    if not is_configured(platform):
        raise HTTPException(status_code=400,
                            detail=f"{platform} credentials not configured")
    if not base_url():
        raise HTTPException(status_code=400,
                            detail="PUBLIC_BASE_URL not set — required for OAuth")
    state = f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"
    _oauth_states[state] = (_user["google_sub"], platform, time.time() + 600)
    return {"url": auth_url(platform, state), "state": state}


@app.get("/api/publish/callback/{platform}")
async def publish_callback(platform: str, code: str = "", state: str = "",
                           error: str = ""):
    from publish import handle_callback, redirect_uri

    pending = _oauth_states.pop(state, None)
    if not pending or pending[1] != platform or pending[2] < time.time():
        html = _oauth_page(False, f"OAuth state mismatch or expired "
                                  f"(platform: {platform})")
        return HTMLResponse(html)
    if error:
        html = _oauth_page(False, f"Authorization denied: {error}")
        return HTMLResponse(html)
    try:
        handle_callback(pending[0], platform, code)
        html = _oauth_page(True, f"Connected to {platform}! "
                                 f"You can close this tab.")
    except Exception as e:  # noqa: BLE001
        html = _oauth_page(False, str(e))
    return HTMLResponse(html)


@app.get("/api/publish/{platform}/status")
async def publish_status(platform: str,
                         _user: dict = Depends(require_user)):
    from publish import connection_info
    return connection_info(_user["google_sub"], platform)


@app.delete("/api/publish/{platform}")
async def publish_disconnect(platform: str, _user: dict = Depends(require_user)):
    db.delete_token(_user["google_sub"], platform)
    return {"ok": True}


@app.get("/api/projects/{job_id}/waveform")
async def project_waveform(job_id: str, _user: dict = Depends(require_user)):
    from video import extract_waveform
    project = _own_project(job_id, _user["google_sub"])
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    source = OUTPUT_DIR / job_id / "source.mp4"
    if not source.exists():
        raise HTTPException(status_code=404,
                            detail="Source video no longer available on disk")
    cache = OUTPUT_DIR / job_id / "waveform.json"
    if cache.exists():
        try:
            data = json.loads(cache.read_text())
        except (json.JSONDecodeError, OSError):
            data = None
    else:
        data = None
    if not data:
        data = extract_waveform(str(source))
        try:
            cache.write_text(json.dumps(data))
        except OSError:
            pass
    # Attach transcript segments (speech regions) for overlay.
    transcript = project.get("transcript") or {}
    data["segments"] = [
        {"start": s.get("start", 0), "end": s.get("end", 0),
         "text": (s.get("text") or "")[:80]}
        for s in (transcript.get("segments") or [])
    ]
    return data


@app.get("/api/projects/{job_id}/clips/{clip_idx}/publishes")
async def clip_publishes(job_id: str, clip_idx: int,
                         _user: dict = Depends(require_user)):
    _own_project(job_id, _user["google_sub"])
    return {"publishes": db.list_publishes(job_id, clip_idx)}


@app.get("/api/projects/{job_id}/subtitle/{clip_idx}")
async def project_subtitle_download(job_id: str, clip_idx: int,
                                    _user: dict = Depends(require_user)):
    """Download a clip's .ass subtitle with a forced attachment.

    Browsers IGNORE the <a download> attribute on cross-origin URLs, so a bare
    static link either navigates away or shows the raw text. Serving the file
    with Content-Disposition: attachment forces a real download everywhere.
    """
    project = _own_project(job_id, _user["google_sub"])
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    ass = OUTPUT_DIR / job_id / f"clip_{clip_idx}.ass"
    if not ass.exists():
        raise HTTPException(status_code=404,
                            detail="Subtitle file no longer available on disk")
    title = ""
    for c in project.get("clips") or []:
        if int(c.get("index", -1)) + 1 == clip_idx:
            title = (c.get("title") or "").strip()
            break
    safe = "".join(ch for ch in title if ch.isalnum() or ch in " -_").strip()
    name = f"{safe or f'clip_{clip_idx}'}.ass"
    return FileResponse(str(ass), media_type="application/octet-stream",
                        filename=name)


@app.get("/api/dub/voices")
async def dub_voices():
    """Languages + voices available for AI dubbing (for the picker)."""
    from dub import available_languages, voices_for
    return {
        "languages": available_languages(),
        "voices": {lang: voices_for(lang) for lang in available_languages()},
    }


@app.post("/api/projects/{job_id}/clips/{clip_idx}/dub")
async def dub_clip(job_id: str, clip_idx: int, req: DubRequest,
                   _user: dict = Depends(require_user)):
    if not _own_project(job_id, _user["google_sub"]):
        raise HTTPException(status_code=404, detail="Project not found")
    clips = db.get_clips(job_id)
    if clip_idx < 0 or clip_idx >= len(clips):
        raise HTTPException(status_code=404, detail="Clip not found")
    job_id_new = _new_job_id()
    with _jobs_lock:
        jobs[job_id_new] = {
            "status": "queued",
            "logs": [f"[{_now()}] dub job {job_id_new} queued"],
            "created_at": time.time(),
            "user_id": _user["google_sub"],
        }
    spec = {"kind": "dub", "job_id": job_id, "clip_idx": clip_idx,
            "target_lang": req.target_lang, "voice": req.voice or ""}
    asyncio.create_task(_run_job(job_id_new, spec))
    return {"job_id": job_id_new}


@app.get("/api/projects/{job_id}/clips/{clip_idx}/analytics")
async def clip_analytics(job_id: str, clip_idx: int,
                         _user: dict = Depends(require_user)):
    """Live metrics (views/likes/comments…) for every published post of a clip."""
    if not _own_project(job_id, _user["google_sub"]):
        raise HTTPException(status_code=404, detail="Project not found")
    clips = db.get_clips(job_id)
    if clip_idx < 0 or clip_idx >= len(clips):
        raise HTTPException(status_code=404, detail="Clip not found")
    from analytics import clip_analytics as fetch
    data = await asyncio.to_thread(fetch, job_id, clip_idx,
                                   _user["google_sub"])
    return {"analytics": data}


@app.get("/api/projects/{job_id}/calibration")
async def project_calibration(job_id: str, _user: dict = Depends(require_user)):
    """Prediction calibration: compares each clip's predicted score with the
    real performance of its published posts. Powers the roadmap's final step —
    validating/improving Klipin's own score ranking."""
    if not _own_project(job_id, _user["google_sub"]):
        raise HTTPException(status_code=404, detail="Project not found")
    from calibration import project_calibration as calc
    data = await asyncio.to_thread(calc, job_id, _user["google_sub"])
    return data


@app.post("/api/projects/{job_id}/clips/{clip_idx}/faceswap")
async def clip_faceswap(job_id: str, clip_idx: int,
                        face: UploadFile = File(...),
                        _user: dict = Depends(require_user)):
    from faceswap import is_available, availability_reason
    if not is_available():
        raise HTTPException(status_code=501,
                            detail=f"Face swap unavailable: {availability_reason()}")
    if not _own_project(job_id, _user["google_sub"]):
        raise HTTPException(status_code=404, detail="Project not found")
    clips = db.get_clips(job_id)
    if clip_idx < 0 or clip_idx >= len(clips):
        raise HTTPException(status_code=404, detail="Clip not found")
    face_dir = OUTPUT_DIR / job_id / "faceswap"
    face_dir.mkdir(parents=True, exist_ok=True)
    face_path = face_dir / "face.jpg"
    data = await face.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty face image")
    face_path.write_bytes(data)
    job_id_new = _new_job_id()
    with _jobs_lock:
        jobs[job_id_new] = {
            "status": "queued",
            "logs": [f"[{_now()}] faceswap job {job_id_new} queued"],
            "created_at": time.time(),
            "user_id": _user["google_sub"],
        }
    spec = {"kind": "faceswap", "job_id": job_id, "clip_idx": clip_idx,
            "face_image_path": str(face_path)}
    asyncio.create_task(_run_job(job_id_new, spec))
    return {"job_id": job_id_new}


@app.post("/api/projects/{job_id}/cut")
async def project_cut(job_id: str, req: CutRequest,
                      _user: dict = Depends(require_user)):
    if not _own_project(job_id, _user["google_sub"]):
        raise HTTPException(status_code=404, detail="Project not found")
    job_id_new = _new_job_id()
    with _jobs_lock:
        jobs[job_id_new] = {
            "status": "queued",
            "logs": [f"[{_now()}] cut job {job_id_new} queued"],
            "created_at": time.time(),
            "user_id": _user["google_sub"],
        }
    spec = {"kind": "cut", "job_id": job_id, "ranges": req.ranges,
            "options": req.options or {}}
    asyncio.create_task(_run_job(job_id_new, spec))
    return {"job_id": job_id_new}


@app.post("/api/projects/{job_id}/repick")
async def project_repick(job_id: str, req: RepickRequest,
                         _user: dict = Depends(require_user)):
    """Re-run AI moment selection with new criteria (re-pick momen klip)."""
    if not _own_project(job_id, _user["google_sub"]):
        raise HTTPException(status_code=404, detail="Project not found")
    job_id_new = _new_job_id()
    with _jobs_lock:
        jobs[job_id_new] = {
            "status": "queued",
            "logs": [f"[{_now()}] repick job {job_id_new} queued"],
            "created_at": time.time(),
            "user_id": _user["google_sub"],
        }
    spec = {"kind": "repick", "job_id": job_id,
            "custom_prompt": req.custom_prompt or ""}
    asyncio.create_task(_run_job(job_id_new, spec))
    return {"job_id": job_id_new}


@app.post("/api/projects/{job_id}/clips/{clip_idx}/clean")
async def clip_clean(job_id: str, clip_idx: int,
                     req: CleanRequest | None = None,
                     _user: dict = Depends(require_user)):
    if not _own_project(job_id, _user["google_sub"]):
        raise HTTPException(status_code=404, detail="Project not found")
    clips = db.get_clips(job_id)
    if clip_idx < 0 or clip_idx >= len(clips):
        raise HTTPException(status_code=404, detail="Clip not found")
    job_id_new = _new_job_id()
    with _jobs_lock:
        jobs[job_id_new] = {
            "status": "queued",
            "logs": [f"[{_now()}] clean job {job_id_new} queued"],
            "created_at": time.time(),
            "user_id": _user["google_sub"],
        }
    spec = {"kind": "clean", "job_id": job_id, "clip_idx": clip_idx,
            "remove_fillers": req.remove_fillers if req else True,
            "remove_silences": req.remove_silences if req else True,
            "remove_bad_takes": req.remove_bad_takes if req else False}
    asyncio.create_task(_run_job(job_id_new, spec))
    return {"job_id": job_id_new}


@app.post("/api/projects/{job_id}/clips/{clip_idx}/sfx")
async def clip_sfx(job_id: str, clip_idx: int, req: SfxRequest | None = None,
                   _user: dict = Depends(require_user)):
    if not _own_project(job_id, _user["google_sub"]):
        raise HTTPException(status_code=404, detail="Project not found")
    clips = db.get_clips(job_id)
    if clip_idx < 0 or clip_idx >= len(clips):
        raise HTTPException(status_code=404, detail="Clip not found")
    job_id_new = _new_job_id()
    with _jobs_lock:
        jobs[job_id_new] = {
            "status": "queued",
            "logs": [f"[{_now()}] sfx job {job_id_new} queued"],
            "created_at": time.time(),
            "user_id": _user["google_sub"],
        }
    spec = {"kind": "sfx", "job_id": job_id, "clip_idx": clip_idx,
            "volume": req.volume if req else 0.3}
    asyncio.create_task(_run_job(job_id_new, spec))
    return {"job_id": job_id_new}


@app.post("/api/projects/{job_id}/clips/{clip_idx}/broll")
async def clip_broll(job_id: str, clip_idx: int, req: BrollRequest | None = None,
                     _user: dict = Depends(require_user)):
    if not _own_project(job_id, _user["google_sub"]):
        raise HTTPException(status_code=404, detail="Project not found")
    clips = db.get_clips(job_id)
    if clip_idx < 0 or clip_idx >= len(clips):
        raise HTTPException(status_code=404, detail="Clip not found")
    job_id_new = _new_job_id()
    with _jobs_lock:
        jobs[job_id_new] = {
            "status": "queued",
            "logs": [f"[{_now()}] broll job {job_id_new} queued"],
            "created_at": time.time(),
            "user_id": _user["google_sub"],
        }
    spec = {"kind": "broll", "job_id": job_id, "clip_idx": clip_idx,
            "count": req.count if req else 2}
    asyncio.create_task(_run_job(job_id_new, spec))
    return {"job_id": job_id_new}


@app.post("/api/projects/{job_id}/clips/{clip_idx}/voiceover")
async def clip_voiceover(job_id: str, clip_idx: int, req: VoiceoverRequest | None = None,
                         _user: dict = Depends(require_user)):
    if not _own_project(job_id, _user["google_sub"]):
        raise HTTPException(status_code=404, detail="Project not found")
    clips = db.get_clips(job_id)
    if clip_idx < 0 or clip_idx >= len(clips):
        raise HTTPException(status_code=404, detail="Clip not found")
    job_id_new = _new_job_id()
    with _jobs_lock:
        jobs[job_id_new] = {
            "status": "queued",
            "logs": [f"[{_now()}] voiceover job {job_id_new} queued"],
            "created_at": time.time(),
            "user_id": _user["google_sub"],
        }
    spec = {"kind": "voiceover", "job_id": job_id, "clip_idx": clip_idx,
            "voice": req.voice if req else "",
            "mix_ratio": req.mix_ratio if req else 0.15}
    asyncio.create_task(_run_job(job_id_new, spec))
    return {"job_id": job_id_new}


@app.post("/api/projects/{job_id}/clips/{clip_idx}/thumbnail")
async def clip_thumbnail(job_id: str, clip_idx: int, req: ThumbnailRequest,
                         _user: dict = Depends(require_user)):
    if not _own_project(job_id, _user["google_sub"]):
        raise HTTPException(status_code=404, detail="Project not found")
    clips = db.get_clips(job_id)
    if clip_idx < 0 or clip_idx >= len(clips):
        raise HTTPException(status_code=404, detail="Clip not found")
    count = max(1, min(4, req.count))
    thumbnail_format = "shorts"
    job_id_new = _new_job_id()
    with _jobs_lock:
        jobs[job_id_new] = {
            "status": "queued",
            "logs": [f"[{_now()}] thumbnail job {job_id_new} queued"],
            "created_at": time.time(),
            "user_id": _user["google_sub"],
        }
    spec = {"kind": "thumbnail", "job_id": job_id, "clip_idx": clip_idx,
            "count": count, "extra_prompt": req.extra_prompt or "",
            "thumbnail_format": thumbnail_format,
            "include_text": req.include_text}
    asyncio.create_task(_run_job(job_id_new, spec))
    return {"job_id": job_id_new}


@app.post("/api/projects/{job_id}/clips/{clip_idx}/publish-ideas")
async def publish_ideas(job_id: str, clip_idx: int, req: PublishIdeasRequest,
                        _user: dict = Depends(require_user)):
    """AI caption + hashtags for one clip, tuned to the target platform."""
    from ideas import suggest_post
    if req.platform not in ("youtube", "tiktok", "instagram"):
        raise HTTPException(status_code=400,
                            detail=f"Unknown platform: {req.platform}")
    if not _own_project(job_id, _user["google_sub"]):
        raise HTTPException(status_code=404, detail="Project not found")
    clips = db.get_clips(job_id)
    if clip_idx < 0 or clip_idx >= len(clips):
        raise HTTPException(status_code=404, detail="Clip not found")
    project = _own_project(job_id, _user["google_sub"])
    clip = clips[clip_idx]
    try:
        ideas = await asyncio.to_thread(
            suggest_post, req.platform, clip.get("title", ""),
            clip.get("hook", ""), project.get("transcript"), None,
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(e))
    return {"ideas": ideas}


@app.post("/api/projects/{job_id}/clips/{clip_idx}/hook-variants")
async def clip_hook_variants(job_id: str, clip_idx: int,
                             _user: dict = Depends(require_user)):
    """A/B hook generator: 3 scored hook variants (curiosity / bold /
    emotional) for one clip. Variants are stored on the clip so they survive
    reloads; picking one via .../select applies it on the next re-render."""
    from hook_variants import generate_hook_variants
    if not _own_project(job_id, _user["google_sub"]):
        raise HTTPException(status_code=404, detail="Project not found")
    clips = db.get_clips(job_id)
    if clip_idx < 0 or clip_idx >= len(clips):
        raise HTTPException(status_code=404, detail="Clip not found")
    project = _own_project(job_id, _user["google_sub"])
    clip = clips[clip_idx]
    try:
        result = await asyncio.to_thread(
            generate_hook_variants, clip.get("title", ""),
            clip.get("hook", ""), project.get("transcript"), None,
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(e))
    variants = result.get("variants") or []
    db.update_clip(job_id, clip_idx, hook_variants=json.dumps(variants))
    return {"clip_idx": clip_idx, "variants": variants}


@app.post("/api/projects/{job_id}/clips/{clip_idx}/hook-variants/select")
async def clip_hook_variant_select(job_id: str, clip_idx: int,
                                   req: HookVariantSelectRequest,
                                   _user: dict = Depends(require_user)):
    """Apply one hook variant as the clip's active hook. The next re-render
    (retrim / rerender) burns it as the hook overlay."""
    if not _own_project(job_id, _user["google_sub"]):
        raise HTTPException(status_code=404, detail="Project not found")
    clips = db.get_clips(job_id)
    if clip_idx < 0 or clip_idx >= len(clips):
        raise HTTPException(status_code=404, detail="Clip not found")
    text = (req.text or "").strip()
    if not text or len(text) > 200:
        raise HTTPException(status_code=422,
                            detail="hook text must be 1..200 chars")
    # A/B hook safety: an edit-selected hook must be ONE of the clip's own
    # stored variants — reject arbitrary strings so a stale/mis-issued token
    # cannot inject a random hook that will be burned into a render.
    stored = clips[clip_idx].get("hook_variants")
    if stored:
        try:
            stored_variants = json.loads(stored) if isinstance(stored, str) else stored
        except (TypeError, ValueError):
            stored_variants = []
        if stored_variants:
            allowed = {str(v.get("text") or "").strip() for v in (stored_variants or [])}
            if text not in allowed:
                raise HTTPException(status_code=422,
                                    detail="Selected hook is not one of the clip's hook variants - re-generate variants first")
    db.update_clip(job_id, clip_idx, hook=text)
    return {"clip_idx": clip_idx, "hook": text}


@app.post("/api/projects/{job_id}/clips/{clip_idx}/publish")
async def publish_clip(job_id: str, clip_idx: int, req: PublishRequest,
                       _user: dict = Depends(require_user)):
    from publish import PLATFORMS
    if req.platform not in PLATFORMS:
        raise HTTPException(status_code=400,
                            detail=f"Unknown platform: {req.platform}")
    if not _own_project(job_id, _user["google_sub"]):
        raise HTTPException(status_code=404, detail="Project not found")
    clips = db.get_clips(job_id)
    if clip_idx < 0 or clip_idx >= len(clips):
        raise HTTPException(status_code=404, detail="Clip not found")

    # Scheduled publish: store the row now, the background scheduler picks it
    # up when the time comes (survives restarts via SQLite).
    if req.scheduled_at and req.scheduled_at > time.time():
        publish_id = db.schedule_publish(
            job_id, clip_idx, req.platform,
            title=req.title or "", description=req.description or "",
            privacy=req.privacy or "", scheduled_at=req.scheduled_at,
            user_id=_user["google_sub"],
        )
        return {"publish_id": publish_id, "scheduled": True,
                "scheduled_at": req.scheduled_at}

    job_id_new = _new_job_id()
    with _jobs_lock:
        jobs[job_id_new] = {
            "status": "queued",
            "logs": [f"[{_now()}] publish job {job_id_new} queued"],
            "created_at": time.time(),
            "user_id": _user["google_sub"],
        }
    spec = {"kind": "publish", "job_id": job_id, "clip_idx": clip_idx,
            "platform": req.platform, "title": req.title or "",
            "description": req.description or "", "privacy": req.privacy or "",
            "user_id": _user["google_sub"]}
    asyncio.create_task(_run_job(job_id_new, spec))
    return {"job_id": job_id_new}


# ─── repurpose, transcript editor, effects ─────────────────────────────────

class RepurposeRequest(BaseModel):
    format: str = "x_thread"  # x_thread | linkedin | blog | ig_long
    clip_idx: int | None = None  # repurpose ONE clip's window (its own text)


class EffectRequest(BaseModel):
    effect: str | None = None  # glitch | zoom | pulse | shake | vignette | color_grade | b_w


class TranscriptEditRequest(BaseModel):
    segments: list = []


class SegmentEditRequest(BaseModel):
    """Text-based editing: delete/reorder transcript segments.

    `segments` — full ordered list of remaining segments (deleted ones omitted).
    Each entry must have at least {start, end, text}.
    The clip's `keep_segments` is derived from the remaining segment windows
    so the re-render extracts only the kept parts (audio + video stay in sync).
    """
    segments: list = []  # remaining segments [{start, end, text}, ...]


@app.post("/api/projects/{job_id}/clips/{clip_idx}/edit-segments")
async def clip_edit_segments(job_id: str, clip_idx: int,
                             req: SegmentEditRequest,
                             _user: dict = Depends(require_user)):
    """Text-based editing: update a clip's keep_segments from transcript edits.

    The frontend sends the remaining (non-deleted) segments in display order.
    We derive keep_segments = the union of those segments' time windows,
    clipped to the clip's own bounds.  After this call, re-render via
    POST /api/projects/{job_id}/rerender to apply.
    """
    if not _own_project(job_id, _user["google_sub"]):
        raise HTTPException(status_code=404, detail="Project not found")
    stored_clips = db.get_clips(job_id)
    if clip_idx < 0 or clip_idx >= len(stored_clips):
        raise HTTPException(status_code=404, detail="Clip not found")
    clip = stored_clips[clip_idx]
    c0 = float(clip.get("start", 0))
    c1 = float(clip.get("end", c0 + 60))

    # Build keep_segments from the remaining segment windows, clipped to
    # the clip's bounds so we don't extract outside the original cut.
    keep = []
    for s in req.segments:
        if not isinstance(s, dict):
            continue
        seg_start = max(c0, float(s.get("start", 0)))
        seg_end = min(c1, float(s.get("end", seg_start)))
        if seg_end - seg_start >= 0.1:  # ignore sub-frame fragments
            keep.append({"start": round(seg_start, 3),
                         "end": round(seg_end, 3)})

    # Merge with any prior caption text edits for surviving segments.
    db.update_clip(job_id, clip_idx,
                   keep_segments=keep if keep else None)
    return {"ok": True, "keep_segments": keep, "count": len(keep)}


@app.post("/api/projects/{job_id}/repurpose")
async def project_repurpose(job_id: str, req: RepurposeRequest,
                            _user: dict = Depends(require_user)):
    """Turn a project's transcript into written content (X/LinkedIn/blog/IG).

    When `clip_idx` is given, the content is generated from that clip's own
    transcript window + its title/hook (per-clip repurpose). Otherwise the
    whole transcript is used.
    """
    from repurpose import FORMATS, repurpose
    if req.format not in FORMATS:
        raise HTTPException(status_code=400, detail=f"Unknown format: {req.format}")
    project = _own_project(job_id, _user["google_sub"])
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    clips = project.get("clips") or []
    if req.clip_idx is not None:
        if req.clip_idx < 0 or req.clip_idx >= len(clips):
            raise HTTPException(status_code=404, detail="Clip not found")
        clip = clips[req.clip_idx]
        # Window the transcript to the clip's bounds.
        segs = (project.get("transcript") or {}).get("segments") or []
        window = [s for s in segs
                  if float(s.get("end", 0) or 0) > clip.get("start", 0)
                  and float(s.get("start", 0) or 0) < clip.get("end", 30)]
        transcript = {"segments": window}
    else:
        clip = clips[0] if clips else {}
        transcript = project.get("transcript") or {}
    try:
        out = await asyncio.to_thread(
            repurpose, transcript, req.format,
            clip.get("title", ""), clip.get("hook", ""), _sink(job_id))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(e))
    out["clip_idx"] = req.clip_idx
    return out


@app.post("/api/projects/{job_id}/clips/{clip_idx}/effects")
async def clip_effects(job_id: str, clip_idx: int,
                       req: EffectRequest | None = None,
                       _user: dict = Depends(require_user)):
    """Apply an AI-suggested (or user-picked) video effect to a clip."""
    if not _own_project(job_id, _user["google_sub"]):
        raise HTTPException(status_code=404, detail="Project not found")
    clips = db.get_clips(job_id)
    if clip_idx < 0 or clip_idx >= len(clips):
        raise HTTPException(status_code=404, detail="Clip not found")
    job_id_new = _new_job_id()
    with _jobs_lock:
        jobs[job_id_new] = {
            "status": "queued",
            "logs": [f"[{_now()}] effect job {job_id_new} queued"],
            "created_at": time.time(),
            "user_id": _user["google_sub"],
        }
    spec = {"kind": "effect", "job_id": job_id, "clip_idx": clip_idx,
            "effect": req.effect if req else None}
    asyncio.create_task(_run_job(job_id_new, spec))
    return {"job_id": job_id_new}


@app.post("/api/projects/{job_id}/clips/{clip_idx}/enhance")
async def clip_enhance(job_id: str, clip_idx: int,
                       req: EnhanceRequest | None = None,
                       _user: dict = Depends(require_user)):
    """Upscale + denoise + vibrance a clip to a clean 1080p vertical."""
    if not _own_project(job_id, _user["google_sub"]):
        raise HTTPException(status_code=404, detail="Project not found")
    clips = db.get_clips(job_id)
    if clip_idx < 0 or clip_idx >= len(clips):
        raise HTTPException(status_code=404, detail="Clip not found")
    job_id_new = _new_job_id()
    with _jobs_lock:
        jobs[job_id_new] = {
            "status": "queued",
            "logs": [f"[{_now()}] enhance job {job_id_new} queued"],
            "created_at": time.time(),
            "user_id": _user["google_sub"],
        }
    spec = {"kind": "enhance", "job_id": job_id, "clip_idx": clip_idx,
            "strength": req.strength if req else 2}
    asyncio.create_task(_run_job(job_id_new, spec))
    return {"job_id": job_id_new}


@app.post("/api/projects/{job_id}/clips/{clip_idx}/overlay")
async def clip_overlay(job_id: str, clip_idx: int,
                       req: OverlayTextRequest,
                       _user: dict = Depends(require_user)):
    """Insert text/image/audio into a clip. Text arrives as JSON; image/audio
    use multipart so the media file rides alongside the overlay spec."""
    if not _own_project(job_id, _user["google_sub"]):
        raise HTTPException(status_code=404, detail="Project not found")
    clips = db.get_clips(job_id)
    if clip_idx < 0 or clip_idx >= len(clips):
        raise HTTPException(status_code=404, detail="Clip not found")

    overlay = req.model_dump()
    if overlay.get("kind") in ("image", "audio"):
        raise HTTPException(
            status_code=400,
            detail="Use multipart for image/audio overlays")

    job_id_new = _new_job_id()
    with _jobs_lock:
        jobs[job_id_new] = {
            "status": "queued",
            "logs": [f"[{_now()}] overlay job {job_id_new} queued"],
            "created_at": time.time(),
            "user_id": _user["google_sub"],
        }
    spec = {"kind": "overlay", "job_id": job_id, "clip_idx": clip_idx,
            "overlay": overlay}
    asyncio.create_task(_run_job(job_id_new, spec))
    return {"job_id": job_id_new}


@app.post("/api/projects/{job_id}/clips/{clip_idx}/overlay/media")
async def clip_overlay_media(job_id: str, clip_idx: int,
                             file: UploadFile = File(...),
                             kind: str = Form("image"),
                             start: float = Form(0.0),
                             end: float = Form(2.0),
                             position: str = Form("bottom"),
                             width_pct: float = Form(60.0),
                             volume: float = Form(0.3),
                             keep_original: bool = Form(True),
                             _user: dict = Depends(require_user)):
    """Insert an uploaded image or audio into a clip (multipart)."""
    if not _own_project(job_id, _user["google_sub"]):
        raise HTTPException(status_code=404, detail="Project not found")
    clips = db.get_clips(job_id)
    if clip_idx < 0 or clip_idx >= len(clips):
        raise HTTPException(status_code=404, detail="Clip not found")
    if kind not in ("image", "audio"):
        raise HTTPException(status_code=400, detail="kind must be image or audio")
    ext = os.path.splitext(file.filename or "")[1].lower()
    allowed = {"image": {".png", ".jpg", ".jpeg", ".webp"},
               "audio": {".mp3", ".wav", ".m4a", ".aac", ".ogg"}}
    if ext not in allowed[kind]:
        raise HTTPException(status_code=422, detail=f"Unsupported {kind} file")

    job_dir = OUTPUT_DIR / job_id
    media_dir = job_dir / "inserts"
    media_dir.mkdir(exist_ok=True)
    media_path = media_dir / f"clip_{clip_idx + 1}_{int(time.time())}{ext}"
    with open(media_path, "wb") as out:
        shutil.copyfileobj(file.file, out)
    if media_path.stat().st_size > 200 * 1024 * 1024:
        media_path.unlink(missing_ok=True)
        raise HTTPException(status_code=413, detail="Media exceeds 200 MB")

    overlay = {
        "kind": kind, "media_path": str(media_path),
        "start": start, "end": end, "position": position,
        "width_pct": width_pct, "volume": volume,
        "keep_original": bool(keep_original),
    }
    job_id_new = _new_job_id()
    with _jobs_lock:
        jobs[job_id_new] = {
            "status": "queued",
            "logs": [f"[{_now()}] overlay media job {job_id_new} queued"],
            "created_at": time.time(),
            "user_id": _user["google_sub"],
        }
    spec = {"kind": "overlay", "job_id": job_id, "clip_idx": clip_idx,
            "overlay": overlay}
    asyncio.create_task(_run_job(job_id_new, spec))
    return {"job_id": job_id_new}


@app.put("/api/projects/{job_id}/transcript")
async def project_transcript_put(job_id: str, req: TranscriptEditRequest,
                                 _user: dict = Depends(require_user)):
    """Save an edited transcript (transcript editor). Re-render afterwards
    via POST /api/projects/{job_id}/rerender."""
    if not _own_project(job_id, _user["google_sub"]):
        raise HTTPException(status_code=404, detail="Project not found")
    segments = [s for s in req.segments if isinstance(s, dict)
                and (s.get("text") or "").strip()]
    db.save_transcript(job_id, {"segments": segments})
    return {"ok": True, "count": len(segments)}


@app.put("/api/projects/{job_id}/options")
async def project_options_put(job_id: str, req: OptionsPatchRequest,
                              _user: dict = Depends(require_user)):
    """Merge-patch a project's caption/layout options (Klipin Studio).

    e.g. ``{"options": {"color": "#FFFFFF", "karaoke_color": "#FFE500"}}``
    — only keys sent are overwritten; the rest are preserved. Re-render the
    clips afterwards via ``POST /api/projects/{job_id}/rerender`` to apply.
    """
    if not _own_project(job_id, _user["google_sub"]):
        raise HTTPException(status_code=404, detail="Project not found")
    merged = db.update_project_options(job_id, req.options or {})
    return {"ok": True, "options": merged}


@app.post("/api/projects/{job_id}/rerender")
async def project_rerender(job_id: str, _user: dict = Depends(require_user)):
    """Re-render ALL clips with the stored transcript + options (free, no AI)."""
    if not _own_project(job_id, _user["google_sub"]):
        raise HTTPException(status_code=404, detail="Project not found")
    job_id_new = _new_job_id()
    with _jobs_lock:
        jobs[job_id_new] = {
            "status": "queued",
            "logs": [f"[{_now()}] rerender job {job_id_new} queued"],
            "created_at": time.time(),
            "user_id": _user["google_sub"],
        }
    spec = {"kind": "rerender", "job_id": job_id}
    asyncio.create_task(_run_job(job_id_new, spec))
    return {"job_id": job_id_new}


# ─── batch multi-URL ──────────────────────────────────────────────────────

class BatchRequest(BaseModel):
    urls: list[str] = []
    options: dict | None = None


@app.post("/api/batch")
async def batch_process(req: BatchRequest, _user: dict = Depends(require_user)):
    """Queue N YouTube URLs as separate jobs (batch ingest). Returns the job
    ids so the frontend can show per-job progress."""
    urls = [u.strip() for u in req.urls if (u or "").strip()]
    if not urls:
        raise HTTPException(status_code=400, detail="Provide at least one URL")
    if len(urls) > 20:
        raise HTTPException(status_code=400, detail="Max 20 URLs per batch")
    try:
        from video import _validate_download_url
        for url in urls:
            _validate_download_url(url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    from billing import enabled as billing_enabled
    effective_options = _process_options_for_user(req.options, _user)
    # Two-phase batch creation: reserve the whole batch before starting any
    # task.  The previous loop started early jobs immediately; when a later
    # reservation failed those tasks kept running and their reservations were
    # stranded even though the API returned an error for the entire batch.
    job_ids = [_new_job_id() for _ in urls]
    reserved_ids = []
    if billing_enabled():
        from billing import release, reserve
        for job_id in job_ids:
            check = reserve(_user["google_sub"], job_id, user=_user)
            if not check["ok"]:
                for reserved_id in reserved_ids:
                    release(_user["google_sub"], reserved_id)
                raise HTTPException(status_code=402, detail=check["error"])
            reserved_ids.append(job_id)

    for url, job_id in zip(urls, job_ids):
        created_at = time.time()
        with _jobs_lock:
            jobs[job_id] = {
                "status": "queued",
                "logs": [f"[{_now()}] job {job_id} queued"],
                "created_at": created_at,
                "options": effective_options, "user_id": _user["google_sub"],
            }
        db.save_project(job_id, source_label=url, status="queued",
                        created_at=created_at, options=effective_options,
                        user_id=_user["google_sub"])
        spec = {"kind": "process",
                "source": {"type": "url", "url": url, "platform": ""},
                "options": effective_options, "user_id": _user["google_sub"]}
        asyncio.create_task(_run_job(job_id, spec))
    return {"batch_id": f"batch-{int(time.time() * 1000):x}", "job_ids": job_ids,
            "count": len(job_ids)}


def _auth_page(ok: bool, message: str):
    color = "#8FE36B" if ok else "#FF7A70"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Klipin — Sign in</title>
<style>
  body {{ background:#0B0C0E; color:#EDEDEA; font-family:ui-monospace,monospace;
         display:grid; place-items:center; height:100vh; margin:0; }}
  .box {{ text-align:center; padding:2rem; }}
  .dot {{ width:12px; height:12px; border-radius:50%; background:{color};
         display:inline-block; margin-bottom:1rem; }}
  h1 {{ font-size:1rem; font-weight:600; letter-spacing:.1em; }}
  p {{ color:#8B8B8B; font-size:.85rem; }}
</style></head><body>
<div class="box"><span class="dot"></span>
<h1>{'SYS.AUTH OK' if ok else 'SYS.AUTH ERR'}</h1>
<p>{message}</p>
</div>
<script>if ({'true' if ok else 'false'}) {{
  window.opener && window.opener.postMessage(
    {{ type: 'klipin:auth-done' }}, '*');
  setTimeout(() => window.close(), 1500);
}}</script>
</body></html>"""


def _oauth_page(ok: bool, message: str):
    color = "#8FE36B" if ok else "#FF7A70"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Klipin — OAuth</title>
<style>
  body {{ background:#0B0C0E; color:#EDEDEA; font-family:ui-monospace,monospace;
         display:grid; place-items:center; height:100vh; margin:0; }}
  .box {{ text-align:center; padding:2rem; }}
  .dot {{ width:12px; height:12px; border-radius:50%; background:{color};
         display:inline-block; margin-bottom:1rem; }}
  h1 {{ font-size:1rem; font-weight:600; letter-spacing:.1em; }}
  p {{ color:#8B8B8B; font-size:.85rem; }}
</style></head><body>
<div class="box"><span class="dot"></span>
<h1>{'SYS.OAUTH OK' if ok else 'SYS.OAUTH ERR'}</h1>
<p>{message}</p>
<p id="close" style="display:none">Closing in 2s…</p>
</div>
<script>if ({'true' if ok else 'false'}) {{
  document.getElementById('close').style.display='block';
  setTimeout(() => window.close(), 2000);
}}</script>
</body></html>"""


def _with_source_title(project: dict) -> dict:
    """Expose cached display metadata without replacing the source URL."""
    project = dict(project)
    try:
        folder = (OUTPUT_DIR / str(project["job_id"])).resolve()
        if folder.parent != OUTPUT_DIR.resolve():
            return project
        meta = json.loads((folder / "source_meta.json").read_text(encoding="utf-8"))
        title = meta.get("title") if isinstance(meta, dict) else None
        if isinstance(title, str) and title.strip():
            project["source_title"] = title.strip()
    except (OSError, ValueError, KeyError):
        pass
    return project


@app.get("/api/projects")
async def list_projects(_user: dict = Depends(require_user)):
    return {"projects": [_with_source_title(p) for p in
                         db.list_projects(user_id=_user["google_sub"])]}


@app.get("/api/projects/{job_id}")
async def project_detail(job_id: str, _user: dict = Depends(require_user)):
    project = db.get_project(job_id, user_id=_user["google_sub"])
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return _with_source_title(project)


@app.get("/api/projects/{job_id}/source-url")
async def project_source_url(job_id: str, _user: dict = Depends(require_user)):
    """Signed, expiring URL for a project's original source.mp4.

    Editor modals preview the source before any clip exists. The frontend must
    never embed the raw session token in a media URL, so the server mints a
    short-lived signature here (same HMAC scheme as every other /videos link).
    """
    if not _own_project(job_id, _user["google_sub"]):
        raise HTTPException(status_code=404, detail="Project not found")
    if not (OUTPUT_DIR / job_id / "source.mp4").exists():
        raise HTTPException(status_code=404, detail="Source not found")
    return {"url": _sign_media_url(f"/videos/{job_id}/source.mp4")}


@app.delete("/api/projects/{job_id}")
async def delete_project(job_id: str, _user: dict = Depends(require_user)):
    if not db.get_project(job_id, user_id=_user["google_sub"]):
        raise HTTPException(status_code=404, detail="Project not found")
    db.delete_project(job_id, user_id=_user["google_sub"])
    shutil.rmtree(OUTPUT_DIR / job_id, ignore_errors=True)
    with _jobs_lock:
        jobs.pop(job_id, None)
    return {"ok": True}


@app.get("/health")
async def health():
    return {"status": "ok", "app": "Klipin"}


def _requested_worker_count() -> int:
    """Best-effort detect a multi-process launch (in-memory state is not
    shared between processes)."""
    for key in ("WEB_CONCURRENCY", "UVICORN_WORKERS", "GUNICORN_WORKERS"):
        raw = os.environ.get(key, "").strip()
        if raw.isdigit() and int(raw) > 1:
            return int(raw)
    return 1


def _single_worker_guard() -> None:
    """Refuse to start with >1 worker unless explicitly overridden.

    OAuth handshake state (`_auth_states`), login claims (`auth._claims`), the
    rate limiter, and the in-memory `jobs` queue all live in this process, so
    more than one worker/instance would randomly fail logins and desync the
    queue. Failing loud beats serving subtly-broken auth.
    """
    workers = _requested_worker_count()
    if workers > 1 and os.environ.get(
            "KLIPIN_ALLOW_MULTIWORKER", "").strip().lower() not in (
                "1", "true", "yes", "on"):
        raise RuntimeError(
            f"Klipin must run with a single worker (detected {workers}). "
            "In-memory OAuth state, login claims, rate limiting, and the job "
            "queue are NOT shared across processes. Run one process/instance, "
            "or set KLIPIN_ALLOW_MULTIWORKER=1 after moving that state to a "
            "shared store (Redis/SQLite).")


@app.on_event("startup")
async def _startup():
    _single_worker_guard()
    # Crash recovery: rows stuck in 'processing' from a previous process are
    # released back to 'scheduled' so the scheduler re-fires them.
    db.release_processing_publishes()
    # Interrupted project jobs: the in-memory queue is gone after restart, so
    # any row still 'queued'/'processing' can never advance. Fail them so the
    # frontend stops polling and offers a re-run instead of spinning at QUEUE.
    db.release_stuck_projects()
    # Same for interrupted music jobs: their asyncio task died with the process,
    # so the row would otherwise stay 'processing' and the panel would load
    # forever. Fail and refund the reserved credit.
    for _music_row in db.list_active_music_jobs():
        try:
            from billing import release as _billing_release
            _billing_release(_music_row["user_id"], _music_row["job_id"])
        except Exception:
            pass
        try:
            _music_job = db.get_music_job(_music_row["job_id"],
                                          _music_row["user_id"]) or {}
            _meta = dict(_music_job.get("metadata") or {})
            _meta["stage"] = "stale"
            db.update_music_job(
                _music_row["job_id"], _music_row["user_id"], status="failed",
                error=("Terputus oleh restart server; kredit dikembalikan. "
                       "Silakan jalankan ulang lagu."),
                metadata=json.dumps(_meta, ensure_ascii=False))
        except Exception:
            pass

    async def cleanup():
        while True:
            await asyncio.sleep(300)
            now = time.time()
            # Only drop in-memory job state — project files & DB persist
            # so the library keeps working after restarts.
            with _jobs_lock:
                for jid in list(jobs):
                    j = jobs[jid]
                    age = now - j.get("created_at", now)
                    if j.get("status") in ("completed", "failed"):
                        if age > JOB_RETENTION_SECONDS:
                            del jobs[jid]
                    elif age > JOB_STALE_SECONDS:
                        # Stuck non-terminal job: evict so memory cannot grow
                        # without bound when a worker never reports back.
                        del jobs[jid]
            auth.cleanup()
    asyncio.create_task(cleanup())
    asyncio.create_task(_publish_scheduler())


async def _publish_scheduler():
    """Fire due scheduled publishes. Runs every 15s; survives restarts because
    the schedule lives in SQLite (status='scheduled'), not in-memory."""
    while True:
        await asyncio.sleep(15)
        try:
            for row in db.list_due_publishes():
                if not db.claim_publish(row["id"]):
                    continue  # already claimed by a previous tick
                job_id = _new_job_id()
                with _jobs_lock:
                    jobs[job_id] = {
                        "status": "queued",
                        "logs": [f"[{_now()}] scheduled publish {job_id} queued"],
                        "created_at": time.time(),
                        "user_id": row.get("user_id") or "",
                    }
                spec = {
                    "kind": "scheduled_publish",
                    "job_id": row["job_id"],
                    "clip_idx": row["clip_idx"],
                    "platform": row["platform"],
                    "title": row.get("title") or "",
                    "description": row.get("description") or "",
                    "privacy": row.get("privacy") or "",
                    "publish_id": row["id"],
                }
                asyncio.create_task(_run_job(job_id, spec))
        except Exception:  # noqa: BLE001 — never kill the scheduler
            import traceback
            traceback.print_exc()


class _VideosApp:
    """Serve /videos files inline (video player) unless `?download=1` is set,
    in which case force Content-Disposition: attachment so browsers actually
    download instead of navigating to the file (the naive <a download>
    attribute is ignored on cross-origin URLs).

    `?download=1&res=720|1080|2160` additionally transcodes to that width on
    the fly (live ffmpeg stream), so users can grab the same clip at 720p,
    1080p (Full HD) or 4K without storing extra copies."""

    def __init__(self, static):
        self.static = static

    @staticmethod
    def _probe_width(full: Path) -> int:
        try:
            p = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=width", "-of", "csv=p=0", str(full)],
                capture_output=True, text=True, timeout=30)
            return int(p.stdout.strip().splitlines()[0])
        except Exception:
            return 0

    async def _stream_transcode(self, full: Path, res: int, fname: str,
                                scope, receive, send):
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(full),
            "-vf", f"scale={res}:-2",
            *encode_args(preset="veryfast", log=None),
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "frag_keyframe+empty_moov", "-f", "mp4", "pipe:1",
        ]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL)

        async def gen():
            loop = asyncio.get_running_loop()
            try:
                while True:
                    chunk = await loop.run_in_executor(
                        None, proc.stdout.read, 1 << 16)
                    if not chunk:
                        break
                    yield chunk
            finally:
                proc.stdout.close()
                try:
                    proc.terminate()
                except Exception:
                    pass

        resp = StreamingResponse(
            gen(), media_type="video/mp4",
            headers={"Content-Disposition": f'attachment; filename="{fname}"'})
        await resp(scope, receive, send)

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            query = (scope.get("query_string") or b"").decode()
            path = scope.get("path") or ""
            params = {}
            for part in query.split("&"):
                if "=" in part:
                    k, _, v = part.partition("=")
                    params[k] = v
            # Signed media URL: expiring HMAC links (minted by the response
            # middleware) need no session token, so shared links die after
            # MEDIA_TTL instead of living forever (or leaking the token).
            signed_ok = bool(params.get("exp") and params.get("sig") and
                             _verify_media_sig(path, params.get("exp"),
                                               params.get("sig")))
            if not signed_ok:
                # Auth gate: only signed-in users may fetch clip files.
                headers = dict(scope.get("headers") or [])
                token = ""
                for k, v in headers.items():
                    if k == b"authorization":
                        token = v.decode(errors="ignore").removeprefix("Bearer ").strip()
                # The legacy `?auth=<session token>` bypass was removed: it
                # leaked a 7-day session token through referrers, browser
                # history, and proxy logs. Un-signed media (e.g. source.mp4 in
                # editor previews) now gets a short-lived signed URL from
                # /api/projects/{id}/source-url instead.
                user = auth.user_from_token(token) if token else None
                if not user:
                    resp = JSONResponse({"detail": "Not authenticated"},
                                        status_code=401)
                    await resp(scope, receive, send)
                    return
            if "download" in query:
                rel = (scope.get("path") or "").lstrip("/")
                root = (scope.get("root_path") or "").lstrip("/")
                for p in (rel,):
                    if p.startswith("videos/"):
                        p = p[len("videos/"):]
                    if root and p.startswith(root + "/"):
                        p = p[len(root) + 1:]
                    full = OUTPUT_DIR / p
                    # Path traversal guard: resolve the request path and refuse
                    # anything that escapes OUTPUT_DIR (dot-segment URLs such as
                    # /videos/../../backend/.env would otherwise read files
                    # outside the media root).
                    try:
                        full = full.resolve()
                        if not full.is_relative_to(OUTPUT_DIR.resolve()):
                            continue
                    except (OSError, ValueError):
                        continue
                    if full.is_file():
                        res = None
                        if "res=" in query:
                            for part in query.split("&"):
                                if part.startswith("res="):
                                    try:
                                        res = int(part[4:].split(",")[0])
                                    except ValueError:
                                        res = None
                                    break
                        if res and res >= 240:
                            src_w = await asyncio.to_thread(self._probe_width, full)
                            if src_w and res != src_w:
                                stem = full.stem
                                suffix = full.suffix or ".mp4"
                                await self._stream_transcode(
                                    full, res, f"{stem}_{res}{suffix}",
                                    scope, receive, send)
                                return
                        resp = FileResponse(str(full), filename=full.name)
                        await resp(scope, receive, send)
                        return
        await self.static(scope, receive, send)


app.mount("/videos", _VideosApp(StaticFiles(directory=str(OUTPUT_DIR))),
          name="videos")


# ─── billing / SaaS ──────────────────────────────────────────────────────

class CheckoutRequest(BaseModel):
    provider: str = "stripe"   # stripe | midtrans
    plan_id: str = "kreator"   # prepaid pack id: mulai | kreator | studio


@app.get("/api/billing/status")
async def billing_status(_user: dict = Depends(require_user)):
    import billing
    if not billing.enabled():
        return {
            "enabled": False,
            "max_output_clips": billing.output_clip_limit(
                _user["google_sub"], _user),
        }
    return {
        "enabled": True,
        "balance": billing.balance(_user["google_sub"]),
        "max_output_clips": billing.output_clip_limit(
            _user["google_sub"], _user),
    }


@app.get("/api/billing/plans")
async def billing_plans(_user: dict = Depends(require_user)):
    import billing
    return {
        "enabled": billing.enabled(),
        "packs": list(billing.PACKS.values()),
    }


@app.post("/api/billing/checkout")
async def billing_checkout(req: CheckoutRequest,
                           _user: dict = Depends(require_user)):
    import billing
    if not billing.enabled():
        raise HTTPException(status_code=400, detail="Billing not enabled")
    try:
        if req.provider == "midtrans":
            return billing.midtrans_charge(_user["google_sub"], req.plan_id)
        return billing.stripe_checkout(_user["google_sub"], req.plan_id)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(e))


# ── Mock Payment Endpoints (Testing Mode) ───────────────────────────
class MockCheckoutReq(BaseModel):
    pack_id: str = "mulai"
    method: str = "qris"


def _require_mock_payments() -> None:
    import billing
    if not billing.mock_payments_enabled():
        raise HTTPException(status_code=404, detail="Mock payments disabled")

@app.post("/api/payment/mock/checkout")
async def mock_payment_checkout(req: MockCheckoutReq, _user: dict = Depends(require_user)):
    import billing
    _require_mock_payments()
    if req.pack_id not in billing.PACKS:
        raise HTTPException(status_code=400, detail="Invalid pack")
    if req.method not in {"qris", "card", "payment_link"}:
        raise HTTPException(status_code=400, detail="Invalid payment method")
    txs = billing._load_mock_txs()
    tx_id = f"mock_{os.urandom(4).hex()}"
    txs[tx_id] = {
        "status": "pending",
        "pack_id": req.pack_id,
        "email": _user.get("email"),
        "user_id": _user.get("google_sub"),
        "method": req.method,
        "created_at": time.time()
    }
    billing._save_mock_txs(txs)
    return {
        "checkout_url": f"/pricing?mock_tx={tx_id}",
        "transaction_id": tx_id,
        "pack": billing.PACKS[req.pack_id]
    }

@app.get("/api/payment/mock/status/{tx_id}")
async def mock_payment_status(tx_id: str, _user: dict = Depends(require_user)):
    import billing
    _require_mock_payments()
    txs = billing._load_mock_txs()
    tx = txs.get(tx_id)
    if not tx or tx.get("user_id") != _user.get("google_sub"):
        raise HTTPException(status_code=404, detail="Transaction not found")
    return tx

@app.post("/api/payment/mock/approve/{tx_id}")
async def mock_payment_approve(tx_id: str, _user: dict = Depends(require_user)):
    import billing
    _require_mock_payments()
    txs = billing._load_mock_txs()
    tx = txs.get(tx_id)
    if not tx or tx.get("user_id") != _user.get("google_sub"):
        raise HTTPException(status_code=404, detail="Transaction not found")
    if tx.get("status") == "approved":
        return {"status": "approved", "message": "Transaksi sudah dibayar"}
    tx["status"] = "approved"
    billing._save_mock_txs(txs)
    # Tambahkan kredit ke user
    billing.credit_paid(tx["user_id"], tx["pack_id"], "mock", tx_id)
    return {"status": "approved", "message": f"Kredit paket {tx['pack_id']} berhasil ditambahkan"}


@app.post("/api/billing/webhook/stripe")
async def billing_stripe_webhook(request: Request):
    """Stripe checkout.session.completed -> add the pack's permanent credits."""
    import billing
    if not billing.enabled():
        return {"ok": False}
    payload = await request.body()
    data = billing.stripe_verify_webhook(
        payload, request.headers.get("Stripe-Signature", ""))
    if not data:
        raise HTTPException(status_code=400, detail="Invalid signature")
    if data.get("type") == "checkout.session.completed":
        session = data.get("data", {}).get("object", {})
        # Never grant credits for a session that has not actually been paid.
        # Async/delayed payment methods emit checkout.session.completed while
        # payment_status is still "unpaid"; granting here would mint free
        # credits. "no_payment_required" covers 100%-discount/0-amount flows.
        if session.get("payment_status") not in ("paid", "no_payment_required"):
            return {"ok": True, "pending": True}
        user_id = session.get("metadata", {}).get("user_id", "")
        pack_id = session.get("metadata", {}).get("pack_id", "")
        if user_id and pack_id:
            billing.credit_paid(user_id, pack_id, "stripe", session.get("id", ""))
    return {"ok": True}


@app.post("/api/billing/webhook/midtrans")
async def billing_midtrans_webhook(request: Request):
    import billing
    if not billing.enabled():
        return {"ok": False}
    data = billing.midtrans_verify_webhook(await request.body())
    if not data:
        raise HTTPException(status_code=400, detail="Invalid signature")
    order_id = data.get("order_id", "")
    pack_id = data.get("item_details", [{}])[0].get("id", "") \
        .replace("pack-", "")
    if order_id:
        user_id = data.get("user_id", "")
        if not user_id:
            return {"ok": True}  # unknown user — already validated upstream
        billing.credit_paid(user_id, pack_id, "midtrans", order_id)
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
