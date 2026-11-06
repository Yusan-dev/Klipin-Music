"""Local orchestration client for Klipin's Modal media worker."""

from __future__ import annotations

import json
import hashlib
import os
import subprocess
import threading
import time
import tomllib
from pathlib import Path, PurePosixPath
from urllib.parse import parse_qs, urlsplit


APP_NAME = os.environ.get("MODAL_APP_NAME", "klipin-media")
VOLUME_NAME = os.environ.get("MODAL_VOLUME_NAME", "klipin-media-jobs")
FUNCTION_NAME = os.environ.get("MODAL_FUNCTION_NAME", "render_pipeline_v2")

# Seconds without a downloaded byte before a Volume stream is declared
# stalled and restarted (whole-file retry, up to 3 attempts).
_FETCH_STALL_SEC = 25.0

# First-byte grace: Modal Volume reads go cold before the first byte
# (observed 40-50 s of zero bytes, then a full-speed burst). Killing the
# stream at _FETCH_STALL_SEC before any byte ever arrives turns every cold
# start into 3 doomed retries. Mid-stream stalls keep the tight 25 s budget;
# only the wait for the very first byte gets the long grace.
_FETCH_FIRST_BYTE_SEC = 180.0

# Modal's displayed L4 rate plus the reserved CPU/RAM for render_pipeline_v2.
# Keep these configurable because provider pricing can change; values are the
# public list prices as of 2026-08-30.
MODAL_L4_USD_PER_SEC = float(os.environ.get("MODAL_L4_USD_PER_SEC", "0.000222"))
MODAL_CPU_CORE_USD_PER_SEC = float(os.environ.get("MODAL_CPU_CORE_USD_PER_SEC", "0.0000131"))
MODAL_MEMORY_GIB_USD_PER_SEC = float(os.environ.get("MODAL_MEMORY_GIB_USD_PER_SEC", "0.00000222"))
MODAL_CPU_CORES = float(os.environ.get("MODAL_CPU_CORES", "8"))
MODAL_MEMORY_GIB = float(os.environ.get("MODAL_MEMORY_GIB", "16"))

_SOURCE_CACHE_LOCKS: dict[str, threading.Lock] = {}
_SOURCE_CACHE_LOCKS_GUARD = threading.Lock()


def _add_modal_compute_cost(result: dict, worker_sec: float) -> dict:
    """Attach a transparent Modal compute estimate without hiding API costs."""
    worker_sec = max(0.0, float(worker_sec or 0.0))
    rate = (MODAL_L4_USD_PER_SEC
            + MODAL_CPU_CORES * MODAL_CPU_CORE_USD_PER_SEC
            + MODAL_MEMORY_GIB * MODAL_MEMORY_GIB_USD_PER_SEC)
    compute = worker_sec * rate
    cost = result.setdefault("cost", {})
    api_total = float(cost.get("total_usd") or 0.0)
    combined = api_total + compute
    cost.update({
        "modal_compute_usd": round(compute, 6),
        "modal_compute_rate_usd_per_sec": round(rate, 8),
        "total_with_compute_usd": round(combined, 6),
        "total_with_compute_idr": round(combined * 16500),
        "compute_cost_basis": "Modal L4 + 8 CPU cores + 16 GiB reserved, estimate",
    })
    # Preserve the cost contract consumed by ResultsStage and older clients.
    cost.setdefault("total_usd", round(combined, 6))
    cost.setdefault("total_idr", round(combined * 16500))
    return result


def _localize_media_urls(value, job_id: str):
    """Rewrite Modal's `/videos/output/...` URLs to the local job directory.

    The remote worker intentionally renders inside a directory named `output`,
    while downloaded files are materialized under `/app/output/<job_id>`.
    Returning the remote basename made every successful clip point at the
    nonexistent local `/app/output/output` directory.
    """
    if isinstance(value, dict):
        return {key: _localize_media_urls(item, job_id)
                for key, item in value.items()}
    if isinstance(value, list):
        return [_localize_media_urls(item, job_id) for item in value]
    if isinstance(value, str) and value.startswith("/videos/output/"):
        return f"/videos/{job_id}/{value[len('/videos/output/') :]}"
    return value


def _ensure_credentials() -> None:
    """Point Modal's SDK at the *current* token before a worker call.

    Delegates to ``modal_credentials`` so the resolution rules (and the fix for
    the stale ``MODAL_TOKEN_*`` a container picks up at creation) live in one
    place shared with the Music workers. Docker entrypoints can change HOME
    after Compose constructs the container, so relying only on the SDK's
    implicit ``~/.modal.toml`` discovery also made the Uvicorn process differ
    from ``docker exec`` diagnostics.
    """
    from modal_credentials import ensure_modal_credentials
    ensure_modal_credentials()


def enabled() -> bool:
    return os.environ.get("MODAL_RENDER_ENABLED", "0").strip().lower() in {
        "1", "true", "yes", "on",
    }


def direct_url_enabled() -> bool:
    """Let the Modal worker fetch public URLs instead of relaying full media.

    The old path downloaded an entire source to the API host, uploaded the
    same bytes to a Volume, and later downloaded them again with the results.
    Direct URL dispatch removes the redundant first two transfers. Operators
    can turn it off for providers that block datacenter addresses.
    """
    # Relay is the safe default: YouTube commonly blocks serverless/datacenter
    # egress IPs even when the same URL works from the API host. Operators may
    # opt back into direct mode after a Modal egress probe succeeds.
    return os.environ.get("MODAL_DIRECT_URL", "0").strip().lower() in {
        "1", "true", "yes", "on",
    }


def _source_cache_key(source: dict) -> str:
    """Stable identity for host/Volume relay reuse, excluding playlist noise."""
    url = str(source.get("url") or "").strip()
    parsed = urlsplit(url)
    query = parse_qs(parsed.query)
    video_id = (query.get("v") or [""])[0]
    identity = (f"youtube:{video_id}" if video_id else
                f"{source.get('platform') or 'url'}:{url}")
    source_height = os.environ.get("YOUTUBE_SOURCE_MAX_HEIGHT", "720")
    return hashlib.sha256(
        f"relay-v2:{source_height}:{identity}".encode("utf-8")).hexdigest()[:32]


def _source_cache_lock(key: str) -> threading.Lock:
    with _SOURCE_CACHE_LOCKS_GUARD:
        return _SOURCE_CACHE_LOCKS.setdefault(key, threading.Lock())


def _cached_url_download(source: dict, sink=None,
                         resolved: dict | None = None) -> tuple[dict, bool]:
    """Download once per media identity and persist it across API retries."""
    from video import download_url

    key = _source_cache_key(source)
    root = Path(os.environ.get(
        "KLIPIN_SOURCE_CACHE_DIR",
        str(Path(__file__).resolve().parent / ".cache" / "source-relay"),
    ))
    entry = root / key
    media = entry / "source.mp4"
    metadata_path = entry / "metadata.json"

    def cached() -> dict | None:
        if not media.is_file() or media.stat().st_size < 1024 * 1024:
            return None
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(metadata, dict):
            return None
        metadata["path"] = str(media)
        return metadata

    hit = cached()
    if hit:
        if hit.get("height") and int(hit["height"]) < 480:
            sink and sink(
                f"⚠️ Cached source is low-resolution "
                f"({hit.get('width')}x{hit.get('height')})")
        sink and sink(
            f"⚡ Source cache hit: {media.stat().st_size / 1e6:.0f} MB; "
            "download skipped")
        return hit, True

    with _source_cache_lock(key):
        hit = cached()
        if hit:
            sink and sink(
                f"⚡ Source cache hit: {media.stat().st_size / 1e6:.0f} MB; "
                "download skipped")
            return hit, True
        entry.mkdir(parents=True, exist_ok=True)
        downloaded = download_url(
            str(source.get("url") or ""), str(entry), log=sink,
            platform=str(source.get("platform") or ""),
            resolved=resolved,
        )
        downloaded_path = Path(downloaded["path"])
        if downloaded_path != media:
            os.replace(downloaded_path, media)
        metadata = {key: value for key, value in downloaded.items()
                    if key != "path"}
        try:
            from video import get_resolution
            _w, _h = get_resolution(str(media))
            metadata["width"], metadata["height"] = _w, _h
            if _h and _h < 480:
                sink and sink(
                    f"⚠️ Downloaded source is low-resolution {_w}x{_h} — "
                    "all 720p profiles failed for this video")
        except Exception:  # noqa: BLE001 - probing must never fail the job
            pass
        temporary = metadata_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(metadata), encoding="utf-8")
        os.replace(temporary, metadata_path)
        metadata["path"] = str(media)
        return metadata, False


def _volume_file_exists(volume, remote_path: str) -> bool:
    """Check a cached Modal Volume file without downloading its bytes."""
    parent = str(PurePosixPath(remote_path).parent)
    name = PurePosixPath(remote_path).name
    # ``reload`` is valid inside mounted Modal functions but deliberately
    # rejected by the local SDK client for legacy V1 Volumes. A failed reload
    # must not suppress the authoritative remote list operation.
    try:
        volume.reload()
    except Exception:  # noqa: BLE001 - continue with remote listdir
        pass
    try:
        for entry in volume.listdir(parent):
            if PurePosixPath(str(entry.path)).name == name:
                return int(getattr(entry, "size", 0) or 0) >= 1024 * 1024
    except Exception:  # noqa: BLE001 - cache miss falls back to safe upload
        return False
    return False


def _prepare_cached_relay(volume, source: dict, work_dir: str, sink=None
                          ) -> tuple[dict, dict]:
    """Return a Volume-backed input while avoiding repeated host transfers."""
    key = _source_cache_key(source)
    remote_path = f"/sources/{key}/source.mp4"
    mounted_path = f"/mnt/klipin{remote_path}"
    metrics = {
        "source_cache_key": key,
        "host_source_cache_hit": False,
        "modal_source_cache_hit": False,
        "relay_download_sec": 0.0,
        "relay_upload_sec": 0.0,
    }
    preview, resolved = _provider_context(source)
    _persist_processing_preview(work_dir, preview)
    remote_input = {
        "type": "url_relay",
        "path": mounted_path,
        "url": str(source.get("url") or ""),
        "platform": str(source.get("platform") or ""),
        "resolved": resolved,
    }
    if source.get("language_hint"):
        remote_input["language_hint"] = source["language_hint"]

    if _volume_file_exists(volume, remote_path):
        metrics["modal_source_cache_hit"] = True
        sink and sink("⚡ Modal source cache hit — download and upload skipped")
        return remote_input, metrics

    stage = time.perf_counter()
    downloaded, host_hit = _cached_url_download(
        source, sink=sink, resolved=resolved)
    metrics["relay_download_sec"] = round(time.perf_counter() - stage, 3)
    metrics["host_source_cache_hit"] = host_hit
    local_source = Path(downloaded["path"])
    _persist_processing_preview(work_dir, downloaded, local_source)

    sink and sink(f"☁️ Uploading {local_source.name} to Modal source cache...")
    stage = time.perf_counter()
    with volume.batch_upload(force=True) as batch:
        batch.put_file(local_source, remote_path)
    metrics["relay_upload_sec"] = round(time.perf_counter() - stage, 3)
    sink and sink(
        f"✅ Modal source upload finished in "
        f"{metrics['relay_upload_sec']:.1f}s")
    return remote_input, metrics


def _provider_context(source: dict) -> tuple[dict, dict]:
    """Resolve preview plus the small metadata subset needed by Modal.

    Extraction stays on the API host to avoid YouTube bot challenges on
    serverless egress. Expiring media format URLs are intentionally excluded;
    relay mode already supplies the source bytes through the Volume.
    """
    from video import resolve_url_metadata

    resolved = resolve_url_metadata(
        str(source.get("url") or ""),
        platform=str(source.get("platform") or ""),
    )
    info = resolved.get("info") or {}
    thumbnails = sorted(
        info.get("thumbnails") or [],
        key=lambda item: item.get("preference", -999), reverse=True,
    )
    thumbnail = ((thumbnails[0].get("url") if thumbnails else None)
                 or info.get("thumbnail"))
    preview = {
        "title": info.get("title") or "Online video",
        "thumbnail": thumbnail,
        "duration": float(info.get("duration") or 0),
        "channel": info.get("channel") or info.get("uploader") or "Unknown",
        "platform": resolved.get("platform") or source.get("platform") or "url",
        "view_count": info.get("view_count") or 0,
        "url": source.get("url") or "",
    }
    keep = {
        "id", "title", "thumbnail", "thumbnails", "duration", "channel",
        "uploader", "view_count", "subtitles", "automatic_captions",
        "modified_date", "upload_date",
    }
    remote_info = {key: info.get(key) for key in keep if key in info}
    remote_context = {
        "platform": resolved.get("platform") or source.get("platform") or "url",
        "info": remote_info,
    }
    return preview, remote_context


def _provider_preview(source: dict) -> dict:
    """Backwards-compatible preview-only view of provider context."""
    preview, _resolved = _provider_context(source)
    return preview


def _persist_processing_preview(work_dir: str, metadata: dict,
                                source_path: Path | None = None) -> dict:
    """Expose source metadata while a remote render is still running.

    The API polls ``source_meta.json`` from the local job directory. Modal URL
    jobs are downloaded on the API host first, so publish that metadata before
    the blocking remote call. Uploads do not have a provider thumbnail; for
    those, extract one local frame and serve it through the existing media
    route.
    """
    local_root = Path(work_dir)
    local_root.mkdir(parents=True, exist_ok=True)
    preview = dict(metadata or {})

    if not preview.get("thumbnail") and source_path and source_path.is_file():
        preview_path = local_root / "source_preview.jpg"
        try:
            subprocess.run([
                "ffmpeg", "-y", "-ss", "1", "-i", str(source_path),
                "-frames:v", "1", "-vf",
                "scale=1280:720:force_original_aspect_ratio=increase,"
                "crop=1280:720,setsar=1",
                str(preview_path),
            ], check=True, capture_output=True, timeout=45)
            preview["thumbnail"] = (
                f"/videos/{local_root.name}/{preview_path.name}")
        except (OSError, subprocess.SubprocessError):
            pass

    try:
        (local_root / "source_meta.json").write_text(
            json.dumps(preview), encoding="utf-8")
    except OSError:
        pass
    return preview


def _read_recovery_payload(volume, remote_root: str) -> dict | None:
    """Return a durable completed-render payload after an RPC/deadline error."""
    try:
        volume.reload()
    except Exception:  # noqa: BLE001 - the subsequent read is authoritative
        pass
    for name in ("result.json", "result.checkpoint.json"):
        try:
            raw = b"".join(volume.read_file(f"{remote_root}/{name}"))
            payload = json.loads(raw.decode("utf-8"))
            if isinstance(payload, dict) and payload.get("result"):
                payload["recovered_from_checkpoint"] = True
                return payload
        except Exception:  # noqa: BLE001 - try the older checkpoint next
            continue
    return None


def _raise_remote_failure(payload: dict, sink=None) -> None:
    """Convert Modal's JSON-safe failure envelope into a local exception."""
    failure = payload.get("error") if isinstance(payload, dict) else None
    if not failure:
        return
    for line in payload.get("logs") or []:
        sink and sink(line)
    message = (failure.get("message") if isinstance(failure, dict)
               else str(failure))
    raise RuntimeError(f"Modal media worker failed: {message}")


def _is_remote_media_download_failure(payload: dict) -> bool:
    """True when a direct-URL worker failure can use the local relay path."""
    failure = payload.get("error") if isinstance(payload, dict) else None
    message = (failure.get("message", "") if isinstance(failure, dict)
               else str(failure or ""))
    lowered = message.lower()
    return any(marker in lowered for marker in (
        "video cdn download failed",
        "http error 403",
        "sign in to confirm you're not a bot",
        "unable to download video data",
    ))


def run_remote_pipeline(job_id: str, source: dict, work_dir: str,
                        options: dict | None = None, sink=None) -> dict:
    """Upload input, invoke Modal, and materialize every output locally."""
    _ensure_credentials()
    import modal

    volume = modal.Volume.from_name(VOLUME_NAME)
    remote_root = f"/jobs/{job_id}"
    Path(work_dir).mkdir(parents=True, exist_ok=True)
    remote_source = dict(source)
    temporary_download = None
    orchestration_metrics: dict = {}
    if source.get("type") in ("youtube", "url"):
        if direct_url_enabled():
            # Resolve only metadata locally. The L4 worker downloads the media
            # once, after transcript-first selection, over Modal's fast link.
            preview = _provider_preview(source)
            _persist_processing_preview(work_dir, preview)
            local_source = None
            sink and sink("⚡ Direct source mode: Modal fetches URL once (no relay upload)")
        else:
            # Compatibility path for providers that reject datacenter IPs.
            # The content-addressed cache makes retries reuse both the host
            # download and the already-uploaded Modal Volume object.
            remote_source, orchestration_metrics = _prepare_cached_relay(
                volume, source, work_dir, sink=sink)
            local_source = None
    elif source.get("type") == "upload":
        local_source = Path(str(source.get("path") or ""))
        _persist_processing_preview(work_dir, {
            "title": local_source.name or "Uploaded video",
            "thumbnail": None,
            "duration": 0,
            "channel": "Local upload",
            "platform": "upload",
            "view_count": 0,
            "url": "",
        }, local_source)
    else:
        local_source = None

    if local_source is not None:
        if not local_source.is_file():
            raise RuntimeError("Uploaded video file missing before Modal dispatch")
        sink and sink(f"☁️ Uploading {local_source.name} to Modal volume...")
        with volume.batch_upload(force=True) as batch:
            batch.put_file(local_source, f"{remote_root}/input/source.mp4")

    sink and sink("⚡ Modal L4 job started (hosted ASR + GPU visual/FFmpeg pipeline)")
    started = time.perf_counter()
    function = modal.Function.from_name(APP_NAME, FUNCTION_NAME)

    def invoke(remote_input: dict) -> dict:
        try:
            return function.remote(job_id, remote_input, options or {})
        except Exception:
            recovered = _read_recovery_payload(volume, remote_root)
            if recovered is None:
                raise
            sink and sink(
                "♻️ Modal RPC melewati deadline; hasil render dipulihkan "
                "dari checkpoint persisten")
            return recovered

    try:
        payload = invoke(remote_source)
        if (remote_source.get("type") not in ("upload", "url_relay")
                and source.get("type") in ("youtube", "url")
                and _is_remote_media_download_failure(payload)):
            sink and sink(
                "♻️ CDN Modal menolak source; beralih otomatis ke local "
                "relay fallback...")
            relay_source, orchestration_metrics = _prepare_cached_relay(
                volume, source, work_dir, sink=sink)
            payload = invoke(relay_source)
    finally:
        if temporary_download is not None:
            temporary_download.cleanup()

    _raise_remote_failure(payload, sink=sink)

    return _materialize_remote_payload(
        volume, remote_root, work_dir, payload, job_id, sink, started,
        orchestration_metrics)


def _is_deliverable(relative: str) -> bool:
    """Only files the host actually needs come back over the wire.

    The worker's output dir also holds intermediates (source.mp4, raw_*
    extracts, vertical_*/sc_* segment renders, concat lists, cmd files).
    Shipping those doubles the download (source.mp4 alone is ~200 MB) while
    Studio edits rebuild the source from the provider URL via the
    content-addressed relay cache. Skipping them is what turns a ~10-minute
    "Modal result downloaded" into ~1 minute, with zero quality impact.
    """
    low = relative.lower()
    base = PurePosixPath(low).name
    if base == "source.mp4":
        return False
    if base.startswith(("raw_", "vertical_", "sc_")):
        return False
    if ("/input/" in low or low.startswith("input/")
            or ".render_" in low or "layout_previews" in low):
        return False
    if low.endswith((".log", ".tmp", ".txt")):
        return False
    return True


def _materialize_remote_payload(volume, remote_root: str, work_dir: str,
                                payload: dict, job_id: str, sink=None,
                                started: float | None = None,
                                orchestration_metrics: dict | None = None,
                                ) -> dict:
    """Download a completed worker payload and localize its media URLs.

    Shared by the fresh-render path and the recovery path so a retry never
    re-renders clips that already exist on the Volume.
    """
    import concurrent.futures

    local_root = Path(work_dir)
    local_root.mkdir(parents=True, exist_ok=True)
    wanted = []
    for relative in payload.get("files") or []:
        safe = PurePosixPath(relative)
        if safe.is_absolute() or ".." in safe.parts:
            raise RuntimeError(f"Unsafe Modal output path: {relative}")
        if not _is_deliverable(safe.as_posix()):
            continue
        wanted.append(safe)

    def _fetch_one(safe: PurePosixPath) -> str:
        # Stall-proof fetch: Volume reads occasionally stall mid-stream
        # (31 MB took 259 s once, 150 MB took 34 s another time). The stream
        # runs in a thread while the main thread watches file growth; 25 s
        # without a byte restarts the file (up to 3 attempts). Slow-but-moving
        # streams are never interrupted — only dead ones.
        target = local_root.joinpath(*safe.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        remote_path = f"{remote_root}/output/{safe.as_posix()}"
        t_start = time.perf_counter()
        last_exc: Exception | None = None
        for _attempt in range(3):
            stop = threading.Event()
            done = threading.Event()
            err: list[Exception] = []

            def _stream() -> None:
                try:
                    with target.open("wb") as handle:
                        for chunk in volume.read_file(remote_path):
                            if stop.is_set():
                                return
                            handle.write(chunk)
                    done.set()
                except Exception as exc:  # noqa: BLE001 — retried below
                    err.append(exc)

            t0 = time.perf_counter()
            th = threading.Thread(target=_stream, daemon=True)
            th.start()
            last_size, last_move = -1, t0
            while th.is_alive():
                th.join(timeout=2.0)
                try:
                    size = target.stat().st_size
                except OSError:
                    size = 0
                now = time.perf_counter()
                if size != last_size:
                    last_size, last_move = size, now
                if err or done.is_set():
                    break
                # No byte ever received yet -> cold-start grace; otherwise
                # the tight mid-stream stall budget.
                budget = (_FETCH_FIRST_BYTE_SEC if last_size <= 0
                          else _FETCH_STALL_SEC)
                if now - last_move > budget:
                    stop.set()
                    th.join(timeout=10.0)
                    break
            if done.is_set() and not err:
                break
            last_exc = err[0] if err else TimeoutError(
                f"stalled Volume read: {safe.as_posix()}")
            sink and sink(f"↻ retry {safe.name} "
                          f"({'error' if err else 'stall'})")
        else:
            raise RuntimeError(
                f"Volume download failed after retries: "
                f"{safe.as_posix()} ({last_exc})")
        dt = max(0.001, time.perf_counter() - t_start)
        try:
            mb = target.stat().st_size / 1e6
        except OSError:
            mb = 0.0
        return f"⬇️ {safe.name} ({mb:.1f} MB, {mb / dt:.1f} MB/s)"

    # Parallel file fetch: Volume reads are throughput-per-stream capped, so
    # concurrent streams finish several times faster than one. Order of
    # completion is nondeterministic; files land in their final paths.
    # Small sidecars (subtitles, thumbnails, preview images, meta) download
    # first so the Studio UI has something to show even if a big clip stalls;
    # clips (.mp4) follow after them.
    _SIDECAR_SUFFIXES = (".ass", ".srt", ".vtt", ".jpg", ".jpeg", ".png",
                         ".webp", ".json")

    def _wanted_sort_key(safe: PurePosixPath) -> tuple:
        name = safe.as_posix().lower()
        return (0, name) if name.endswith(_SIDECAR_SUFFIXES) else (1, name)

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for line in pool.map(_fetch_one, sorted(wanted, key=_wanted_sort_key)):
            sink and sink(line)

    for line in payload.get("logs") or []:
        sink and sink(line)
    elapsed = (round(time.perf_counter() - started, 3)
               if started is not None else 0.0)
    result = _localize_media_urls(payload.get("result") or {}, job_id)
    result.setdefault("metrics", {})
    result["metrics"].update({
        "compute_provider": "modal",
        "modal_gpu": payload.get("gpu", "L4"),
        "modal_worker_sec": payload.get("modal_wall_sec", 0),
        "modal_roundtrip_sec": elapsed,
        **(orchestration_metrics or {}),
    })
    _add_modal_compute_cost(result, payload.get("modal_wall_sec", 0))
    sink and sink(f"✅ Modal result downloaded in {elapsed:.1f}s")
    return result


def recover_remote_result(job_id: str, work_dir: str, sink=None) -> dict | None:
    """Materialize an already-completed remote render without re-rendering.

    Returns the localized result when the Volume holds a finished
    ``result.json``/checkpoint for this job, else None. Raises when the worker
    recorded an explicit error envelope (caller decides retry vs fail).
    This is what stops a post-render hiccup (e.g. a stumbled download) from
    redoing a 7-minute render three more times.
    """
    _ensure_credentials()
    import modal

    volume = modal.Volume.from_name(VOLUME_NAME)
    remote_root = f"/jobs/{job_id}"
    payload = _read_recovery_payload(volume, remote_root)
    if payload is None:
        return None
    _raise_remote_failure(payload, sink=sink)
    sink and sink(
        "♻️ Hasil render sudah jadi di worker — download ulang tanpa "
        "render ulang")
    return _materialize_remote_payload(
        volume, remote_root, work_dir, payload, job_id, sink)


# ── Remote Studio edit jobs (retrim / rerender / cut / export / caption_edit) ─
# The API host's CPU no longer renders edits: the same L4 worker class that
# renders full pipelines handles them (modal_app.edit_pipeline_v2). Durable
# state stays host-side: the worker gets a project snapshot, and every DB
# write it performs is captured and replayed here after the rendered files
# are materialized.

MODAL_MOUNT = os.environ.get("MODAL_MOUNT", "/mnt/klipin")
REMOTE_EDIT_KINDS = ("retrim", "caption_edit", "rerender", "cut", "export")
EDIT_FUNCTION_NAME = os.environ.get(
    "MODAL_EDIT_FUNCTION_NAME", "edit_pipeline_v2")


def _resolve_edit_source_volume(job_id: str, out_root: str, volume,
                                sink=None) -> str:
    """Put the source on the Modal Volume and return its mounted path.

    Prefers the host's job-dir copy; when it is gone (YouTube relay jobs never
    needed one on the host), rebuilds the content-addressed source cache from
    the provider URL instead of failing with "source no longer on disk".
    """
    local = Path(out_root) / job_id / "source.mp4"
    if local.is_file():
        sink and sink("☁️ Uploading source to Modal volume for edit...")
        with volume.batch_upload(force=True) as batch:
            batch.put_file(local, f"/jobs/{job_id}/edit_input/source.mp4")
        return f"{MODAL_MOUNT}/jobs/{job_id}/edit_input/source.mp4"

    meta_path = Path(out_root) / job_id / "source_meta.json"
    url, platform = "", "url"
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            url = str(meta.get("url") or "")
            platform = str(meta.get("platform") or "url").lower()
        except (OSError, json.JSONDecodeError):
            pass
    if not url:
        raise RuntimeError(
            "Source video no longer available on disk and no provider URL to "
            "rebuild the Modal source cache")
    source = {"type": "youtube" if "youtube" in platform else "url",
              "url": url, "platform": platform}
    remote_source, _metrics = _prepare_cached_relay(
        volume, source, str(Path(out_root) / job_id), sink=sink)
    return str(remote_source.get("path") or "")


def _patch_result_revs(result, revs: dict) -> None:
    """Replace placeholder revs in an edit result with the host's true revs."""
    if not revs:
        return
    if isinstance(result, dict):
        if ("rev" in result and isinstance(result.get("rev"), int)
                and isinstance(result.get("index"), int)
                and result["index"] in revs):
            result["rev"] = revs[result["index"]]
        for value in result.values():
            _patch_result_revs(value, revs)
    elif isinstance(result, list):
        for value in result:
            _patch_result_revs(value, revs)


def run_remote_edit(job_id: str, spec: dict, out_root: str,
                    sink=None) -> dict:
    """Render a Studio edit job on the Modal L4 and materialize outputs locally.

    Returns the same dict shape the local ops handler would return, so the
    job bookkeeping in app._run_job is unchanged.
    """
    _ensure_credentials()
    import modal

    import db
    project = db.get_project(job_id)
    if not project:
        raise RuntimeError("Project not found")
    kind = str(spec.get("kind", ""))
    if kind not in REMOTE_EDIT_KINDS:
        raise RuntimeError(f"Kind is not a remote-editable job: {kind}")

    volume = modal.Volume.from_name(VOLUME_NAME)
    started = time.perf_counter()

    edit = {
        "kind": kind,
        "free_pack": (db.highest_paid_pack(project.get("user_id") or "")
                      if project.get("user_id") else "free"),
    }
    if kind == "retrim":
        edit.update({
            "clip_idx": int(spec["clip_idx"]),
            "start": float(spec["start"]),
            "end": float(spec["end"]),
            "overrides": dict(spec.get("overrides") or {}),
        })
    elif kind == "caption_edit":
        edit.update({"clip_idx": int(spec["clip_idx"]),
                     "lines": list(spec.get("lines") or [])})
    elif kind == "cut":
        edit.update({"ranges": list(spec.get("ranges") or []),
                     "options": dict(spec.get("options") or {})})
    elif kind == "export":
        edit.update({"indices": list(spec.get("indices") or []),
                     "resolution": str(spec.get("resolution") or ""),
                     "aspect": str(spec.get("aspect") or "")})

    edit["source_volume_path"] = _resolve_edit_source_volume(
        job_id, out_root, volume, sink=sink)

    # Export re-encodes ALREADY rendered clips: upload them next to the source
    # so the worker does not need anything from the host's disk.
    if kind == "export":
        clips = db.get_clips(job_id)
        indices = edit["indices"] or [int(c["idx"]) for c in clips]
        missing = [f"clip_{i + 1}.mp4" for i in indices
                   if not (Path(out_root) / job_id
                           / f"clip_{i + 1}.mp4").is_file()]
        if missing:
            raise RuntimeError("Clip file(s) no longer available on disk: "
                               + ", ".join(missing))
        with volume.batch_upload(force=True) as batch:
            for i in indices:
                batch.put_file(
                    Path(out_root) / job_id / f"clip_{i + 1}.mp4",
                    f"/jobs/{job_id}/edit_out/{job_id}/clip_{i + 1}.mp4")

    # Seed payload: the worker needs options/transcript/clips to read and the
    # watermark tier to honor — everything else is recomputed remotely.
    edit["project"] = {key: project.get(key) for key in (
        "job_id", "user_id", "source_label", "status", "created_at",
        "options", "error", "duration", "transcript", "mode", "cost",
        "metrics")}
    edit["clips"] = db.get_clips(job_id)

    sink and sink("⚡ Modal L4 edit job started (GPU re-render, NVENC)")
    function = modal.Function.from_name(APP_NAME, EDIT_FUNCTION_NAME)
    payload = function.remote(job_id, edit)
    if not isinstance(payload, dict):
        raise RuntimeError("Modal edit worker returned an invalid payload")
    failure = payload.get("error")
    if failure:
        for line in payload.get("logs") or []:
            sink and sink(line)
        raise RuntimeError(
            "Modal edit worker failed: "
            + (failure.get("message") if isinstance(failure, dict)
               else str(failure)))
    _raise_remote_failure(payload, sink=sink)

    local_root = Path(out_root) / job_id
    local_root.mkdir(parents=True, exist_ok=True)
    remote_prefix = f"/jobs/{job_id}/edit_out/{job_id}"
    for relative in payload.get("files") or []:
        safe = PurePosixPath(str(relative))
        if safe.is_absolute() or ".." in safe.parts:
            raise RuntimeError(f"Unsafe Modal edit output path: {relative}")
        target = local_root.joinpath(*safe.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("wb") as handle:
            for chunk in volume.read_file(f"{remote_prefix}/{safe.as_posix()}"):
                handle.write(chunk)

    for line in payload.get("logs") or []:
        sink and sink(line)

    revs = db.apply_captured_writes(job_id, payload.get("db_writes") or [])
    result = payload.get("result") or {}
    _patch_result_revs(result, revs)
    elapsed = round(time.perf_counter() - started, 3)
    if isinstance(result, dict):
        metrics = result.setdefault("metrics", {})
        metrics.update({
            "compute_provider": "modal",
            "modal_gpu": payload.get("gpu", "L4"),
            "modal_worker_sec": payload.get("modal_wall_sec", 0),
            "modal_roundtrip_sec": elapsed,
        })
    sink and sink(f"✅ Modal edit result downloaded in {elapsed:.1f}s")
    return result
