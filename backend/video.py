"""FFmpeg helpers for Klipin: download, clip extraction, layout crops.

Layout modes (target aspect default 9:16):
  general   -> blurred full-frame background + sharp fitted cutout
  center    -> plain center crop (fastest, cheapest)
  face      -> face-tracked crop window around the detected face (OpenCV)
  split     -> two detected faces side by side (podcast / 2-person)
  gameplay  -> full-width visible + vertical blur (screen recordings)
  square    -> 1:1 center crop on blurred background
"""
import json
import os
import shutil
import struct
import subprocess
import time

from gpu import encode_args

ASPECTS = {
    "9:16": (9, 16),
    "1:1": (1, 1),
    "16:9": (16, 9),
}


def _ffmpeg_executable() -> str:
    """Resolve FFmpeg from PATH or the bundled imageio-ffmpeg runtime."""
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg
        bundled = imageio_ffmpeg.get_ffmpeg_exe()
        if bundled and os.path.isfile(bundled):
            return bundled
    except Exception:
        pass
    return "ffmpeg"  # preserve the useful OS error when truly unavailable


def run(cmd: list, log=None):
    # Re-encode video at the real source cadence. Forcing 25 fps material to
    # 30 fps inserts one duplicate every five source frames (visible judder).
    if "ffmpeg" in cmd[:3] and "-i" in cmd:
        try:
            ii = cmd.index("-i")
            is_video_reencode = "-c:v" in cmd
        except (ValueError, StopIteration):
            pass
        else:
            if is_video_reencode and ii + 1 < len(cmd):
                try:
                    ci = cmd.index("-c:v")
                    source = _fps_source_from_command(cmd, ii)
                    cmd = cmd[:ci] + output_fps_args(source) + cmd[ci:]
                except StopIteration:
                    pass
    if cmd and str(cmd[0]).lower() in ("ffmpeg", "ffmpeg.exe"):
        cmd = [_ffmpeg_executable(), *cmd[1:]]
    log and log(" ".join(cmd))
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {r.stderr[-800:]}")
    return r


def _fps_source_from_command(cmd: list, first_input_index: int) -> str:
    source = str(cmd[first_input_index + 1])
    if source.lower().endswith((".txt", ".ffconcat")) and os.path.isfile(source):
        try:
            with open(source, "r", encoding="utf-8") as handle:
                for raw in handle:
                    line = raw.strip()
                    if line.lower().startswith("file "):
                        value = line[5:].strip().strip("'\"")
                        return value if os.path.isabs(value) else os.path.join(
                            os.path.dirname(source), value)
        except OSError:
            pass
    return source


def get_duration(path: str) -> float:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "json", path],
            capture_output=True, text=True,
        )
        return float(json.loads(r.stdout)["format"]["duration"])
    except (OSError, KeyError, ValueError):
        pass
    # Fallback: use OpenCV (works when ffprobe is stripped/broken)
    try:
        import cv2
        cap = cv2.VideoCapture(path)
        frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        cap.release()
        if frames > 0 and fps > 0:
            return frames / fps
    except Exception:
        pass
    return 0.0


def get_fps(path: str) -> float:
    """Effective presentation frame rate, capped at 60 fps."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=avg_frame_rate,r_frame_rate",
             "-of", "json", path],
            capture_output=True, text=True,
        )
        stream = json.loads(r.stdout)["streams"][0]
        for field in ("avg_frame_rate", "r_frame_rate"):
            num, _, den = str(stream.get(field, "0/0")).partition("/")
            den = float(den) if den else 1.0
            fps = float(num) / den if den else 0.0
            if 5.0 < fps <= 60.0:
                return fps
    except (OSError, KeyError, IndexError, ValueError, ZeroDivisionError):
        pass
    # Fallback: use OpenCV
    try:
        import cv2
        cap = cv2.VideoCapture(path)
        fps = cap.get(cv2.CAP_PROP_FPS)
        cap.release()
        if fps and 5.0 < fps < 240.0:
            return fps
    except Exception:
        pass
    return 30.0


def output_fps_args(path: str) -> list[str]:
    fps = get_fps(path)
    common = ((24000 / 1001, "24000/1001"),
              (30000 / 1001, "30000/1001"),
              (60000 / 1001, "60000/1001"))
    rate = next((label for value, label in common if abs(fps - value) < 0.01),
                f"{fps:.6f}".rstrip("0").rstrip("."))
    return ["-fps_mode", "cfr", "-r", rate,
            "-g", str(max(12, int(round(fps * 2))))]


def extract_waveform(path: str, buckets: int = 600, log=None) -> dict:
    """Compute a lightweight amplitude waveform for a video/audio file."""
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", "8000",
         "-f", "s16le", "-"],
        capture_output=True,
    )
    if r.returncode != 0:
        raise RuntimeError(f"waveform decode failed: {r.stderr[-400:]}")
    raw = r.stdout
    n = len(raw) // 2
    if n == 0:
        return {"peaks": [], "duration": get_duration(path)}
    bucket = max(1, n // buckets)
    peaks = []
    for i in range(0, n, bucket):
        chunk = raw[i * 2:(i + bucket) * 2]
        if len(chunk) < 2:
            continue
        vals = struct.unpack(f"<{len(chunk) // 2}h", chunk[:len(chunk) // 2 * 2])
        peaks.append(max(abs(v) for v in vals) / 32768.0)
    return {"peaks": peaks, "duration": n / 8000.0}


def get_resolution(path: str):
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "json", path],
            capture_output=True, text=True,
        )
        s = json.loads(r.stdout)["streams"][0]
        return int(s["width"]), int(s["height"])
    except (OSError, KeyError, IndexError, ValueError):
        pass
    # Fallback: use OpenCV
    try:
        import cv2
        cap = cv2.VideoCapture(path)
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        cap.release()
        if w > 0 and h > 0:
            return w, h
    except Exception:
        pass
    return 1920, 1080


def has_square_pixels(path: str) -> bool:
    """Return whether the first video stream uses square display pixels.

    A 1080x1920 encoded frame is not sufficient to guarantee portrait
    playback: legacy files can carry a non-square sample aspect ratio that
    makes players display that pixel frame as 16:9.  Treat an unavailable
    probe as square so callers retain the existing OpenCV fallback behaviour.
    """
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=sample_aspect_ratio", "-of", "json", path],
            capture_output=True, text=True,
        )
        ratio = json.loads(r.stdout)["streams"][0].get("sample_aspect_ratio")
        if not ratio or ratio == "N/A":
            return True
        num, sep, den = ratio.partition(":")
        return bool(sep) and int(num) == int(den)
    except (OSError, KeyError, IndexError, TypeError, ValueError):
        return True


def _guess_platform(url: str) -> str:
    """Return a yt-dlp-friendly platform key for a pasted URL."""
    u = (url or "").lower()
    if "tiktok.com" in u or "vm.tiktok" in u or "vt.tiktok" in u:
        return "tiktok"
    if "instagram.com" in u or "instagr.am" in u:
        return "instagram"
    return "youtube"


# Hosts allowed as video sources (SSRF guard — never fetch internal hosts).
ALLOWED_DOWNLOAD_HOSTS = (
    "youtube.com", "youtu.be", "ytimg.com", "googlevideo.com",
    "tiktok.com", "vm.tiktok.com", "vt.tiktok.com",
    "instagram.com", "instagr.am", "cdninstagram.com",
)


def _is_allowed_download_host(host: str) -> bool:
    """Accept an allowlisted source/CDN domain and its subdomains."""
    host = (host or "").lower().rstrip(".")
    return bool(host) and any(
        host == domain or host.endswith("." + domain)
        for domain in ALLOWED_DOWNLOAD_HOSTS
    )


def _validate_download_url(url: str) -> None:
    """Block non-http(s) and disallowed hosts before yt-dlp touches them."""
    import urllib.parse
    parsed = urllib.parse.urlparse((url or "").strip())
    if parsed.scheme not in ("http", "https"):
        raise ValueError("Hanya URL http(s) yang didukung")
    host = (parsed.hostname or "").lower().rstrip(".")
    if not _is_allowed_download_host(host):
        raise ValueError(f"Domain tidak diizinkan: {host}")
    # Resolve once: refuse internal/private ranges (SSRF).
    try:
        import socket
        for info in socket.getaddrinfo(host, parsed.port or 443,
                                       proto=socket.IPPROTO_TCP):
            ip = info[4][0]
            if not _is_public_ip(ip):
                raise ValueError(f"Host internal tidak diizinkan: {host} ({ip})")
    except socket.gaierror:
        raise ValueError(f"Domain tidak dapat di-resolve: {host}")


def _is_public_ip(ip: str) -> bool:
    """True when *ip* is a globally-routable public address.

    ``is_global`` already excludes private, loopback, link-local, multicast,
    reserved, and unspecified ranges — the redundant checks are removed to
    avoid the confusing ``not ... is False`` precedence trap that existed
    before.
    """
    import ipaddress
    try:
        return ipaddress.ip_address(ip).is_global
    except ValueError:
        return False


def resolve_url_metadata(url: str, platform: str = "") -> dict:
    """Resolve permitted URL metadata without downloading the media payload."""
    from urllib.parse import urlparse
    import yt_dlp
    _validate_download_url(url)
    platform = (platform or _guess_platform(url)).lower()
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True}
    if platform == "youtube":
        opts["extractor_args"] = {
            "youtube": {"player_client": ["android", "web"]}}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    # Post-extract SSRF guard: verify the resolved URL is still public/allowed.
    # yt-dlp may follow redirects; ensure the final effective hostname hasn't
    # escaped the allowlist or resolved to a private IP.
    effective_url = info.get("url") or url
    parsed = urlparse(effective_url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if not _is_allowed_download_host(host):
        raise ValueError(f"Domain tidak diizinkan setelah redirect: {host}")
    # Also verify IP is still globally routable (protect against DNS rebinding)
    try:
        import socket
        for address_info in socket.getaddrinfo(
                host, parsed.port or 443, proto=socket.IPPROTO_TCP):
            ip = address_info[4][0]
            import ipaddress
            if not ipaddress.ip_address(ip).is_global:
                raise ValueError(f"Host internal setelah redirect: {host} ({ip})")
    except socket.gaierror:
        raise ValueError(f"Domain tidak dapat di-resolve setelah redirect: {host}")
    return {"info": info, "platform": platform, "url": url}


def download_audio_url(url: str, work_dir: str, log=None,
                       platform: str = "") -> str:
    """Download audio only for ASR; never reserves the visual/GPU worker."""
    import yt_dlp
    _validate_download_url(url)
    platform = (platform or _guess_platform(url)).lower()
    outtmpl = os.path.join(work_dir, "source_audio.%(ext)s")
    log and log(f"🎧 Downloading {platform} audio only...")
    opts = {
        "format": "bestaudio[ext=m4a]/bestaudio/best",
        "outtmpl": outtmpl,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
    }
    if platform == "youtube":
        opts["extractor_args"] = {
            "youtube": {"player_client": ["android", "web"]}}
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    candidates = sorted(
        [os.path.join(work_dir, name) for name in os.listdir(work_dir)
         if name.startswith("source_audio.")],
        key=os.path.getsize, reverse=True,
    )
    if not candidates:
        raise RuntimeError("Audio-only download produced no file")
    return candidates[0]


def _youtube_ydl_options() -> dict:
    """Build safe YouTube yt-dlp defaults and materialize optional cookies."""
    import base64
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "extractor_args": {
            "youtube": {"player_client": ["default", "mweb"]}},
    }
    encoded = os.environ.get("YOUTUBE_COOKIES_B64", "").strip()
    if not encoded:
        return opts
    try:
        raw = base64.b64decode(encoded, validate=True).decode("utf-8")
    except Exception as exc:  # noqa: BLE001 - secret/config boundary
        raise RuntimeError("YOUTUBE_COOKIES_B64 is not valid base64") from exc
    if not raw.lstrip().startswith("# Netscape HTTP Cookie File"):
        raise RuntimeError("YouTube cookies must use Netscape format")
    target = os.environ.get("YOUTUBE_COOKIEFILE") or os.path.join(
        os.environ.get("TMPDIR") or os.environ.get("TEMP") or ".",
        "klipin-youtube.cookies.txt")
    os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
    with open(target, "w", encoding="utf-8", newline="") as handle:
        handle.write(raw)
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass
    opts["cookiefile"] = target
    return opts


def _youtube_download_profiles(base: dict) -> list[tuple[str, dict]]:
    """Return bounded YouTube client/transport fallbacks without mutation."""
    import copy
    try:
        max_height = min(1080, max(144, int(os.environ.get(
            "YOUTUBE_SOURCE_MAX_HEIGHT", "720"))))
    except (TypeError, ValueError):
        max_height = 720
    first = copy.deepcopy(base or {})
    first.setdefault("noplaylist", True)
    first.setdefault("quiet", True)
    first.setdefault("no_warnings", True)
    first["format"] = first.get("format") or (
        f"bestvideo[height<={max_height}][ext=mp4]+bestaudio[ext=m4a]/"
        f"best[height<={max_height}][ext=mp4]/best")
    profiles = [(f"adaptive {max_height}p", first)]
    hls = copy.deepcopy(first)
    hls.pop("cookiefile", None)
    hls["format"] = (
        f"bestvideo[height<={max_height}][protocol^=m3u8]/"
        f"best[height<={max_height}][protocol^=m3u8]")
    hls["extractor_args"] = {"youtube": {"player_client": ["visionos"]}}
    profiles.append((f"visionOS HLS {max_height}p", hls))
    merged = copy.deepcopy(hls)
    # Merged video+audio at the ceiling: catches videos whose HLS variants
    # expose only separate streams. Never falls back below max_height.
    merged["format"] = (
        f"bestvideo[height<={max_height}][protocol^=m3u8]"
        f"+bestaudio[protocol^=m3u8]/"
        f"bestvideo[height<={max_height}][protocol^=m3u8]/"
        f"best[height<={max_height}][protocol^=m3u8]")
    profiles.append((f"visionOS HLS merged {max_height}p", merged))
    low = copy.deepcopy(hls)
    low["format"] = (
        "bestvideo[height<=480][protocol^=m3u8]/"
        "best[height<=480][protocol^=m3u8]")
    profiles.append(("visionOS adaptive 480p", low))
    return profiles


def _remove_partial_source_downloads(work_dir: str) -> None:
    """Remove only yt-dlp partial source artifacts in the given job folder."""
    for name in ("source.mp4", "source.mp4.part", "source.f299.mp4.part"):
        target = os.path.join(work_dir, name)
        try:
            if os.path.isfile(target):
                os.remove(target)
        except OSError:
            pass


def download_url(url: str, work_dir: str, log=None, platform: str = "",
                 resolved: dict | None = None) -> dict:
    """Download a public video URL (YouTube/TikTok/Instagram) via yt-dlp.
    
    Returns a dict with:
    - path: local file path
    - title: video title
    - thumbnail: best thumbnail URL
    - duration: video duration in seconds
    - channel: channel/uploader name
    - platform: detected platform
    """
    import yt_dlp
    _validate_download_url(url)
    out = os.path.join(work_dir, "source.mp4")
    platform = (platform or _guess_platform(url)).lower()
    log and log(f"⬇️ Downloading {platform} source...")
    ydl_opts = {
        "format": "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/best",
        "merge_output_format": "mp4",
        "outtmpl": out,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        # CDN edges occasionally accept the connection but never deliver the
        # first bytes. yt-dlp's defaults fail the entire paid job after one
        # short timeout, so make transient HTTP/fragment failures resumable.
        "socket_timeout": 45,
        "retries": 8,
        "fragment_retries": 12,
        "extractor_retries": 5,
        "file_access_retries": 3,
        "concurrent_fragment_downloads": 1,
        # Prefer IPv4: some Docker/ISP IPv6 routes to googlevideo are present
        # in DNS but black-hole the actual media connection.
        "source_address": "0.0.0.0",
    }
    if platform == "youtube":
        ydl_opts["extractor_args"] = {
            "youtube": {"player_client": ["android", "web"]}}
    
    # Reuse the metadata resolver result so the fast transcript path does not
    # repeat a remote extraction immediately before the media download.
    if resolved and isinstance(resolved.get("info"), dict):
        info = resolved["info"]
    else:
        info = resolve_url_metadata(url, platform=platform)["info"]
    
    # Retry the complete extraction as well as yt-dlp's per-request retries.
    # A new YoutubeDL instance refreshes the signed googlevideo CDN URL, which
    # is essential when the originally selected edge itself is unhealthy.
    last_error = None
    for attempt in range(1, 4):
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
            last_error = None
            break
        except Exception as exc:  # noqa: BLE001 - provider/network boundary
            last_error = exc
            if attempt >= 3:
                break
            delay = attempt * 2
            log and log(
                f"⚠️ CDN download attempt {attempt}/3 failed; "
                f"refreshing media URL in {delay}s...")
            time.sleep(delay)
    if last_error is not None:
        raise RuntimeError(
            f"Video CDN download failed after 3 attempts: {last_error}") from last_error
    
    if not os.path.exists(out):
        cands = sorted(
            [p for p in os.listdir(work_dir) if p.startswith("source")],
            key=lambda p: os.path.getsize(os.path.join(work_dir, p)), reverse=True,
        )
        if not cands:
            raise RuntimeError("Download produced no video file")
        out = os.path.join(work_dir, cands[0])
    
    log and log(f"✅ Downloaded: {os.path.basename(out)} ({os.path.getsize(out)/1e6:.0f} MB)")
    
    # Pick best thumbnail (prefer maxresdefault, then hqdefault)
    thumb = info.get("thumbnail")
    thumbnails = info.get("thumbnails", [])
    if thumbnails:
        # Sort by preference (higher = better quality)
        thumbnails.sort(key=lambda t: t.get("preference", -999), reverse=True)
        thumb = thumbnails[0].get("url", thumb)
    
    return {
        "path": out,
        "title": info.get("title", "Unknown"),
        "thumbnail": thumb,
        "duration": info.get("duration", 0),
        "channel": info.get("channel") or info.get("uploader", "Unknown"),
        "platform": platform,
        "view_count": info.get("view_count", 0),
        "url": url,
    }


def download_youtube(url: str, work_dir: str, log=None) -> dict:
    """Backwards-compatible alias: download a YouTube URL via download_url."""
    return download_url(url, work_dir, log=log, platform="youtube")


def extract_clip(src: str, start: float, end: float, out_path: str, log=None,
                 fast: bool = False) -> str:
    """Cut a clip with accurate keyframe-based seeking (re-encode for accuracy)."""
    dur = end - start
    if dur <= 0:
        raise ValueError("Clip end must follow start")
    args = list(encode_args(log=log))
    if fast:
        # Intermediate only: later options win in ffmpeg; override preset and
        # rate control so the throwaway cut encodes fast at ABOVE final quality.
        from gpu import use_gpu as _use_gpu_fast
        _fast_is_gpu = bool(_use_gpu_fast())
        _drop = ("-preset", "-cq") if _fast_is_gpu else ("-preset", "-crf")
        _kept = []
        _skip_next = False
        for _tok in args:
            if _skip_next:
                _skip_next = False
                continue
            if _tok in _drop:
                _skip_next = True
                continue
            _kept.append(_tok)
        if _fast_is_gpu:
            args = _kept + ["-preset", "p1", "-cq", "18"]
        else:
            args = _kept + ["-preset", "veryfast", "-crf", "16"]
    run([
        "ffmpeg", "-y",
        "-ss", f"{start:.2f}", "-i", src,
        "-t", f"{dur:.2f}",
        *args,
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        out_path,
    ], log)
    return out_path


# ─── face detection (MediaPipe -> YOLO -> Haar fallback) ───────────────

from detector import face_boxes, _person_to_face, _yolo_persons  # noqa: E402


def _face_centers(path: str, samples: int = 10,
                  subject_lock: tuple | None = None) -> list:
    """Return [(cx, cy, w, h), ...] of the largest face in each sample frame.

    subject_lock: normalized (x, y) in 0..1 — when given, the face whose
    center is nearest to that point wins each frame instead of the largest
    one (manual subject lock: the user clicked the face to follow).
    """
    import cv2

    w, h = get_resolution(path)
    lock_pt = None
    if subject_lock is not None and w > 0 and h > 0:
        lock_pt = (float(subject_lock[0]) * w, float(subject_lock[1]) * h)

    def _pick(faces):
        if lock_pt is None:
            return max(faces, key=lambda f: f[2] * f[3])
        return min(faces, key=lambda f: (f[0] - lock_pt[0]) ** 2
                   + (f[1] - lock_pt[1]) ** 2)

    dur = get_duration(path)
    if dur <= 0:
        return []
    cap = cv2.VideoCapture(path)
    sampled = []
    step = dur / (samples + 1)
    for i in range(samples):
        t = step * (i + 1)
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, frame = cap.read()
        if not ok or frame is None:
            continue
        faces = face_boxes(frame)
        # Drop sub-4.5% noise boxes (audience/photo/wall texture): without
        # this a 20px false positive counts as "a face" and drags the FACE
        # lock off-center — or worse, masks a genuine no-face scene that must
        # fall back to the static CENTER crop below.
        if faces and frame.size:
            fw = float(frame.shape[1])
            faces = [b for b in faces
                     if b[2] >= SPLIT_MIN_FACE_WIDTH * fw]
        if faces:
            sampled.append(faces)
    cap.release()
    # Poster/doll/photo exclusion (source coords here): a printed backdrop
    # face that wins "largest face" would own the whole-clip lock. Static
    # boxes are dropped only where a live face remains (fail-open).
    try:
        sampled = _drop_static_boxes(path, sampled)
    except Exception:
        pass
    return [_pick(faces) for faces in sampled if faces]


def _stable_face_center(path: str, log=None) -> tuple | None:
    """Median face center across sampled frames, or None if no face found."""
    found = _face_centers(path)
    if not found:
        return None
    found.sort(key=lambda f: f[0])
    cx = found[len(found) // 2][0]
    found.sort(key=lambda f: f[1])
    cy = found[len(found) // 2][1]
    return cx, cy


# ─── two-shot verification (ported from OpenShorts split_layout) ─────────
#
# Counting faces per frame is not enough to decide "is this a two-shot":
# a plano/contraplano edit (two cameras, one person on screen at a time)
# shows the same people but never TOGETHER — stacking them would display the
# same face twice. So a pair of speakers is only accepted when BOTH faces
# coexist in the same frame for most readable samples, are genuinely far
# apart, and are big enough to be subjects rather than background extras.

SPLIT_MIN_COEXISTENCE = 0.5   # fraction of readable frames that must show both
SPLIT_MIN_SEPARATION = 0.20   # of frame width; closer = shoulder-to-shoulder
# OpenShorts parity: accept profile/listener faces down to 4.5% of frame width,
# then choose the two largest coexisting, horizontally separated faces.  The
# previous 12% gate rejected real podcast two-shots and made SPLIT/PANEL/FACE
# silently collapse to the same single-face output.
SPLIT_MIN_FACE_WIDTH = 0.045
SPLIT_MAX_FACE_OVERLAP = 0.30 # IoU above this = same face detected twice
SPLIT_MIN_MOUTH_PEAK = 0.20  # mean gray delta; below this is likely a prop
SPLIT_MIN_MOUTH_SIGNAL = 0.08  # peak above the speaker's quiet baseline


def _iou_center(a, b) -> float:
    """IoU of two center-form boxes (cx, cy, w, h)."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(0.0, min(ax + aw / 2, bx + bw / 2) - max(ax - aw / 2, bx - bw / 2))
    iy = max(0.0, min(ay + ah / 2, by + bh / 2) - max(ay - ah / 2, by - bh / 2))
    inter = ix * iy
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def dedupe_faces(faces, frame_w, threshold=SPLIT_MAX_FACE_OVERLAP) -> list:
    """Drop duplicate detections of the same face, keeping the biggest.

    Faces are center-form (cx, cy, w, h). Keeps the largest box and drops
    any overlapping it past `threshold`.
    """
    kept = []
    for f in sorted(faces, key=lambda c: c[2] * c[3], reverse=True):
        if all(_iou_center(f, k) <= threshold for k in kept):
            kept.append(f)
    return kept


def _coexisting_pair(faces, frame_w):
    """The two largest distinct faces in ONE frame, or None.

    Filters out small background faces, dedupes same-face duplicates, and
    requires the two to be separated enough to be different people. Returns
    them left-first (by x) as center-form boxes.
    """
    big = [f for f in dedupe_faces(faces, frame_w)
           if f[2] >= SPLIT_MIN_FACE_WIDTH * frame_w]
    if len(big) != 2:
        return None
    big.sort(key=lambda f: f[2] * f[3], reverse=True)
    a, b = big[0], big[1]
    # Layout halves are left/right subjects.  Euclidean distance incorrectly
    # accepted a presenter plus a vertically separated audience head as a
    # pair; require real horizontal separation instead.
    horizontal_distance = abs(a[0] - b[0])
    if horizontal_distance < SPLIT_MIN_SEPARATION * frame_w:
        return None
    return tuple(sorted((a, b), key=lambda f: f[0]))


def speaker_pair_from_samples(frames, frame_w,
                              min_coexistence=SPLIT_MIN_COEXISTENCE) -> list:
    """Verified two-shot from sampled frames -> [(cx, cy), (cx, cy)] or [].

    `frames` is a list of per-sample face lists (center-form, in the same
    pixel space as `frame_w`). Mirrors OpenShorts split_layout.analyze_scene:
    only frames where BOTH faces coexist count, and a real two-shot must hold
    the pair in at least `min_coexistence` of the readable frames. Medians
    (not means) keep one detector false-positive from dragging the crop off
    the speaker for the whole clip.
    """
    if not frames:
        return []
    lefts, rights = [], []
    for faces in frames:
        pair = _coexisting_pair(faces, frame_w)
        if not pair:
            continue
        lefts.append(pair[0])
        rights.append(pair[1])
    if not lefts or len(lefts) < min_coexistence * len(frames):
        return []
    import numpy as np
    left = (float(np.median([c[0] for c in lefts])),
            float(np.median([c[1] for c in lefts])))
    right = (float(np.median([c[0] for c in rights])),
             float(np.median([c[1] for c in rights])))
    return [left, right]


def _speaker_positions(path: str, samples: int = 10,
                       max_speakers: int = 2,
                       min_coexistence: float = SPLIT_MIN_COEXISTENCE) -> list:
    """Distinct speaker face positions [(cx, cy), ...] in SOURCE coords.

    Unlike `_face_centers` (which keeps only the largest face per frame),
    this samples face LISTS across the clip and accepts a pair only when the
    two people actually coexist in the same frames (a real two-shot), are
    separated enough to be different subjects, and both are big enough to be
    on-camera. A plano/contraplano edit or a single steady face returns [] or
    one center, so callers fall back to a single-speaker / general layout
    instead of stacking the same person twice. Empty when no faces are found.
    """
    import cv2

    dur = get_duration(path)
    if dur <= 0:
        return []
    w, h = get_resolution(path)
    scale = 1.0
    fr_w, fr_h = w, h
    if w > 640:
        scale = 640.0 / w
        fr_w, fr_h = 640, int(h * scale)
    cap = cv2.VideoCapture(path)
    frames = []  # per-sample face lists in analysis coords (readable only)
    step = dur / (samples + 1)
    for i in range(samples):
        t = step * (i + 1)
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, frame = cap.read()
        if not ok or frame is None:
            continue
        if frame.shape[1] > 640:
            frame = cv2.resize(frame, (fr_w, fr_h),
                               interpolation=cv2.INTER_AREA)
        faces = face_boxes(frame)
        frames.append(faces)
    cap.release()
    # Poster/doll/photo exclusion: a printed face is always present, so it
    # inflates pair counts (solo + poster reads as "2 faces") and can win the
    # "largest face" slot. Drop static boxes where a live face remains
    # (source coords for the liveness measurement, then back to analysis).
    try:
        src_lists = [[(f[0] / scale, f[1] / scale, f[2] / scale, f[3] / scale)
                      for f in faces] for faces in frames]
        kept_lists = _drop_static_boxes(path, src_lists)
        frames = [[(f[0] * scale, f[1] * scale, f[2] * scale, f[3] * scale)
                   for f in faces] for faces in kept_lists]
    except Exception:
        pass
    pair = speaker_pair_from_samples(frames, fr_w,
                                     min_coexistence=min_coexistence)
    if not pair:
        # Face-pair fallback: MediaPipe loses faces in profile, behind
        # mics, or behind sunglasses — exactly how guests sit while
        # talking to each other — while YOLO still sees both bodies.
        # A face-only gate then demotes a genuine two-shot to FACE and
        # an explicit SPLIT request never materialises. Bodies are
        # verified subjects too (same coexistence + separation bar via
        # face estimates), so try a body-derived pair before giving up.
        # Poster safety is unchanged: static bodies go through the same
        # _drop_static_boxes filter here, and the renderer's mouth-
        # activity gate (_real_two_shot) still rejects a poster half.
        pair = _person_pair_positions(path, dur, samples, scale,
                                      fr_w, fr_h,
                                      min_coexistence=min_coexistence)
    if not pair:
        return []
    if max_speakers < 2:
        pair = pair[:1]
    return [(float(cx / scale), float(cy / scale)) for (cx, cy) in pair]


def _person_pair_positions(path: str, dur: float, samples: int,
                           scale: float, fr_w: int, fr_h: int,
                           min_coexistence: float = SPLIT_MIN_COEXISTENCE) -> list:
    """Verified two-shot from YOLO bodies -> [(cx, cy), (cx, cy)] or [].

    Same contract as the face path: only frames where BOTH subjects coexist
    count, the pair must hold in at least `min_coexistence` of the readable
    frames, and medians keep one false positive from dragging the crop.
    Bodies are mapped to face estimates (detector._person_to_face) so the
    size filter, separation check, and median geometry are identical to the
    face path. Coordinates are returned in the caller's analysis space;
    the caller converts to source coords. Empty when YOLO is unavailable.
    """
    import cv2

    if dur <= 0:
        return []
    cap = cv2.VideoCapture(path)
    estimates = []  # per-sample face-estimate lists in analysis coords
    step = dur / (samples + 1)
    for i in range(samples):
        t = step * (i + 1)
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, frame = cap.read()
        if not ok or frame is None:
            continue
        if frame.shape[1] > 640:
            frame = cv2.resize(frame, (fr_w, fr_h),
                               interpolation=cv2.INTER_AREA)
        try:
            bodies = _yolo_persons(frame) or []
        except Exception:
            bodies = []
        estimates.append([_person_to_face(p) for p in bodies])
    cap.release()
    if not any(estimates):
        return []
    # Poster/doll/photo exclusion, mirroring the face path: source coords
    # for the liveness measurement, then back to analysis coords.
    try:
        src_lists = [[(f[0] / scale, f[1] / scale, f[2] / scale, f[3] / scale)
                      for f in faces] for faces in estimates]
        kept_lists = _drop_static_boxes(path, src_lists)
        estimates = [[(f[0] * scale, f[1] * scale, f[2] * scale, f[3] * scale)
                      for f in faces] for faces in kept_lists]
    except Exception:
        pass
    return speaker_pair_from_samples(estimates, fr_w,
                                     min_coexistence=min_coexistence)


def _stable_body_seats(path: str, samples: int = 10) -> list:
    """Stable seated people [(cx, cy, w, h), ...] in SOURCE coords, or [].

    YOLO bodies clustered into seats across samples (greedy clustering,
    6%-of-frame radius — same as the poster filter); a seat must hold in
    at least half the readable samples. Static printed bodies are excluded
    via _static_side_mask, so a solo speaker in front of a backdrop photo
    resolves to ONE seat, never a group. Sorted left-first by x.
    """
    import cv2

    dur = get_duration(path)
    if dur <= 0:
        return []
    w, h = get_resolution(path)
    if w <= 0 or h <= 0:
        return []
    scale = 1.0
    fr_w, fr_h = w, h
    if w > 640:
        scale = 640.0 / w
        fr_w, fr_h = 640, int(h * scale)
    cap = cv2.VideoCapture(path)
    per_sample = []
    readable = 0
    step = dur / (samples + 1)
    for i in range(samples):
        cap.set(cv2.CAP_PROP_POS_MSEC, step * (i + 1) * 1000)
        ok, frame = cap.read()
        if not ok or frame is None:
            continue
        if frame.shape[1] > 640:
            frame = cv2.resize(frame, (fr_w, fr_h),
                               interpolation=cv2.INTER_AREA)
        try:
            bodies = _yolo_persons(frame) or []
        except Exception:
            bodies = []
        bodies = [b for b in bodies
                  if b[2] >= SPLIT_MIN_FACE_WIDTH * float(fr_w)]
        if bodies:
            readable += 1
        per_sample.append([(b[0] / scale, b[1] / scale,
                            b[2] / scale, b[3] / scale) for b in bodies])
    cap.release()
    if readable <= 0:
        return []
    dim = float(max(w, h))
    radius = dim * 0.06
    flat = []
    for si, boxes in enumerate(per_sample):
        for b in boxes:
            flat.append((si, (float(b[0]), float(b[1]),
                              float(b[2]), float(b[3]))))
    flat.sort(key=lambda item: item[1][2] * item[1][3], reverse=True)
    clusters: list[list[tuple]] = []
    for si, box in flat:
        placed = False
        for cl in clusters:
            mx = sum(b[0] for _, b in cl) / len(cl)
            my = sum(b[1] for _, b in cl) / len(cl)
            if (not any(sample == si for sample, _ in cl)
                    and (box[0] - mx) ** 2 + (box[1] - my) ** 2 <= radius ** 2):
                cl.append((si, box))
                placed = True
                break
        if not placed:
            clusters.append([(si, box)])
    import numpy as np
    need = max(2, (readable + 1) // 2)
    # Merge clusters split by box jitter: YOLO box centres wobble as people
    # lean/talk, so one person lands in several radius-clusters. Clusters
    # whose median boxes substantially overlap (same body) and never share
    # a sample (different people would appear together) are one seat.
    def _iou(a, b):
        ix = max(0.0, min(a[0] + a[2] / 2, b[0] + b[2] / 2)
                 - max(a[0] - a[2] / 2, b[0] - b[2] / 2))
        iy = max(0.0, min(a[1] + a[3] / 2, b[1] + b[3] / 2)
                 - max(a[1] - a[3] / 2, b[1] - b[3] / 2))
        inter = ix * iy
        union = a[2] * a[3] + b[2] * b[3] - inter
        return inter / union if union > 0 else 0.0

    def _median(cl):
        arr = np.array([[b[0], b[1], b[2], b[3]] for _, b in cl])
        return [float(v) for v in np.median(arr, axis=0)]

    merged = True
    while merged:
        merged = False
        meds = [_median(cl) for cl in clusters]
        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                si = set(s for s, _ in clusters[i])
                sj = set(s for s, _ in clusters[j])
                if si & sj:
                    continue  # same frame twice = different people
                if _iou(meds[i], meds[j]) > 0.30:
                    clusters[i].extend(clusters[j])
                    del clusters[j]
                    merged = True
                    break
            if merged:
                break
    seats = []
    for cl in clusters:
        if len(cl) < need:
            continue
        arr = np.array([[b[0], b[1], b[2], b[3]] for _, b in cl])
        seats.append([float(v) for v in np.median(arr, axis=0)])
    if len(seats) < 2:
        return sorted(seats, key=lambda b: b[0])
    # Exclude static printed bodies (poster/doll/photo): a group must be
    # living people, never a solo speaker plus wall prints.
    try:
        mask = _static_side_mask(path, [(s[0], s[1]) for s in seats])
        seats = [s for s, static in zip(seats, mask) if not static]
    except Exception:
        pass
    return sorted(seats, key=lambda b: b[0])


def _group_split_windows(path: str, w: int, h: int, log=None,
                         samples: int = 10):
    """Two crop windows dividing a 3+ group, or None.

    Seats left/right of the room's widest x-gap become the top/bottom
    halves, so the halves always show different people. Each window spans
    its group's bodies plus a margin, aspect-adjusted to the half-frame
    (1.125) with the same chin-bias geometry as the pair path. None when
    fewer than 3 live seats hold still (caller uses the pair/FACE path).
    Windows are (x, y, w, h) in SOURCE coords.
    """
    seats = _stable_body_seats(path, samples=samples)
    if len(seats) < 3:
        return None
    xs = [s[0] for s in seats]
    cut = max(range(len(xs) - 1),
              key=lambda i: xs[i + 1] - xs[i]) + 1
    groups = (seats[:cut], seats[cut:])
    # A widest-gap split is not useful when nearly every seat lands on one
    # side. In that case both portrait panels need to widen toward the same
    # room and the result becomes a duplicated middle-band shot. Require a
    # meaningful share of the live seats on both sides before building the
    # geometry; the caller will use the verified pair/FACE fallback instead.
    n = len(seats)
    min_group = max(1, int(round(n * 0.4)))
    if min(len(groups[0]), len(groups[1])) < min_group:
        log and log(f"⚠️ SPLIT group unbalanced "
                    f"({len(groups[0])}v{len(groups[1])} of {n}) — "
                    "FACE fallback (no duplicate stack)")
        return None
    margin = 0.03 * w
    y_bias = 0.42  # same chin-bias geometry as the pair path below
    windows = []
    for grp in groups:
        x0 = min(s[0] - s[2] / 2 for s in grp) - margin
        x1 = max(s[0] + s[2] / 2 for s in grp) + margin
        cw = int(x1 - x0)
        # Keep each panel owned by its group. Without this cap, a small group
        # beside a large group can expand to most of the source width and put
        # the same speaker in both stacked outputs even when their origins do
        # not technically overlap.
        share = max(0.30, min(0.70, len(grp) / max(1, n)))
        cw = min(cw, max(4, int(w * share)))
        ch = max(4, int(cw / 1.125))
        if cw > w:
            cw = w
            ch = max(4, int(cw / 1.125))
        if ch > h:
            ch = h
            cw = max(4, int(ch * 1.125))
        cw = min(max(2, cw), w)
        ch = min(max(2, ch), h)
        cx = (max(x0, 0) + min(x1, w)) / 2.0
        cy = sum(s[1] for s in grp) / len(grp)
        x = max(0, min(int(cx - cw / 2), w - cw))
        y = max(0, min(int(cy - y_bias * ch), h - ch))
        windows.append((x, y, cw, ch))
    return windows[0], windows[1]


def _speaker_activity_boxes(path: str, positions: list,
                            samples: int = 12) -> list:
    """Return real face-sized boxes for active-speaker mouth analysis.

    The crop window can cover most of the frame; using it as a mouth ROI mixes
    both speakers and can route the camera to the wrong person. Match each
    stable speaker position to detected face boxes and take median dimensions.
    """
    import cv2
    import numpy as np
    if len(positions) < 2:
        return []
    dur = get_duration(path)
    w, h = get_resolution(path)
    if dur <= 0 or w <= 0 or h <= 0:
        return []
    scale = min(1.0, 640.0 / w)
    fw, fh = int(w * scale), int(h * scale)
    cap = cv2.VideoCapture(path)
    matches = [[] for _ in positions]
    for i in range(samples):
        cap.set(cv2.CAP_PROP_POS_MSEC, (dur * (i + 1) / (samples + 1)) * 1000)
        ok, frame = cap.read()
        if not ok or frame is None:
            continue
        if scale < 1.0:
            frame = cv2.resize(frame, (fw, fh), interpolation=cv2.INTER_AREA)
        faces = face_boxes(frame)
        for j, (px, py) in enumerate(positions):
            candidates = [f for f in faces if
                          ((f[0] / scale - px) ** 2 +
                           (f[1] / scale - py) ** 2) ** 0.5 < max(w, h) * 0.18]
            if candidates:
                f = min(candidates, key=lambda x:
                        (x[0] / scale - px) ** 2 + (x[1] / scale - py) ** 2)
                matches[j].append([f[0] / scale, f[1] / scale,
                                   f[2] / scale, f[3] / scale])
    cap.release()
    boxes = []
    for j, (px, py) in enumerate(positions):
        if matches[j]:
            boxes.append([float(v) for v in np.median(matches[j], axis=0)])
        else:
            boxes.append([float(px), float(py), float(w * 0.14), float(h * 0.22)])
    return boxes


# ─── layout crops ─────────────────────────────────────────────────────────

# Fixed output frame per aspect ratio (keeps every clip consistent).
TARGET_FRAMES = {
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
    "16:9": (1920, 1080),
}

DELIVERY_MIN_WIDTH = 1080  # OpenShorts parity: never output below this


def delivery_size(src_w: int, src_h: int, aspect: str = "9:16") -> tuple:
    """Compute delivery resolution — OpenShorts parity.

    OpenShorts delivery_size(): output = largest crop from source; upscale
    only to DELIVERY_MIN_WIDTH; NEVER downscale.

    For a 16:9 1920×1080 source → 9:16 crop window is 607×1080 →
    upscale to 1080×1920 (hits DELIVERY_MIN_WIDTH).
    For a 4K 3840×2160 source → 9:16 crop window is 1215×2160 →
    keep as-is (already above DELIVERY_MIN_WIDTH).
    For a 480p 640×360 source → 9:16 crop window is 202×360 →
    upscale to 1080×1920 (hits DELIVERY_MIN_WIDTH).
    """
    tw, th = TARGET_FRAMES.get(aspect, TARGET_FRAMES["9:16"])
    # Largest crop window inside source matching target aspect
    if src_w * th <= src_h * tw:   # width-limited
        cw, ch = src_w, int(src_w * th / tw)
    else:
        cw, ch = int(src_h * tw / th), src_h
    # Never downscale: use the larger of crop or DELIVERY_MIN_WIDTH
    if cw < DELIVERY_MIN_WIDTH:
        scale = DELIVERY_MIN_WIDTH / cw
        cw = DELIVERY_MIN_WIDTH
        ch = max(DELIVERY_MIN_WIDTH, int(ch * scale))
    # Snap to even dimensions (ffmpeg requirement)
    cw = cw + (cw % 2)
    ch = ch + (ch % 2)
    return cw, ch


# High-quality upscale for low-res sources (e.g. 640×360 → 1080×1920).
# Bilinear `scale` softens on upscale; lanczos + a light unsharp restores
# crispness so the delivered 1080p actually reads as 1080p.
UPSCALE = ":flags=lanczos,unsharp=5:5:0.6:5:5:0.0"


def _unsharp_strength(crop_w: int, crop_h: int, tw: int, th: int,
                      base: float = 0.6) -> float:
    """Unsharp amount scaled by how much this crop axis is upscaled."""
    ratio = max(tw / max(1, crop_w), th / max(1, crop_h))
    strength = base
    if ratio > 1.05:
        # Slope 0.35 x, capped at 1.2: meaningful repair on a 2.7x upscale
        # (0.6 -> ~1.2) without letting the encode explode on noisy content or
        # adding ringing near 1x. Near-1x sources keep the base amount (0.6) so
        # already-1080 portrait inputs are never oversharpened.
        strength = min(1.2, base + (ratio - 1.05) * 0.35)
    return strength


def _sharpen_for_upscale(crop_w: int, crop_h: int, tw: int, th: int,
                         base: float = 0.6) -> str:
    """Adaptive lanczos + unsharp for the current upscale ratio.

    A fixed unsharp 0.6 repairs a near-1x scale (already-portrait 9:16 sources)
    but leaves a 2.7x upscale of a 720p landscape talking-head visibly soft --
    exactly the gap users report vs OpenShorts, which reframes close to the
    source resolution instead of always forcing 1080x1920. We cannot raise the
    720p relay ceiling without a contract benchmark, so we sharpen more the
    larger the axis ratio, clamped to a band that never adds ringing on
    near-1x content or amplification of sensor noise. Pure filter, single pass,
    encoder-agnostic -- the same graph runs on NVENC (Modal L4) and CPU.
    """
    return (f":flags=lanczos,unsharp=5:5:"
            f"{_unsharp_strength(crop_w, crop_h, tw, th, base):.1f}:5:5:0.0")


def _split_sharpen(src_w: int, src_h: int, tw: int, th: int) -> str:
    """Unsharp amount (number only) for one split/panel half-crop upscale.

    Split/panel already emit `scale=...flags=lanczos` and only need the unsharp
    amount appended, so this returns the bare strength value.
    """
    return f"{_unsharp_strength(src_w, src_h, tw, th):.1f}"


def crop_video(src: str, out_path: str, mode: str = "general",
               aspect: str = "9:16", log=None,
               adaptive_size: bool = False,
               height_ratio: float = 0.0,
               gameplay_face_position: str = "bottom",
               gameplay_ratio: float = 0.60,
               subject_lock: tuple | None = None) -> str:
    """Crop `src` to `aspect` using layout `mode`. Returns out_path.

    adaptive_size: OpenShorts parity — when True, output resolution is
    computed from source (never downscale) instead of fixed TARGET_FRAMES.
    height_ratio: foreground height fraction for GENERAL mode. 0.0 = auto
      contain-fit (no cropping). >0.05 = force this fraction (e.g. 0.45
      for group shots, 0.55 for large crowds).
    subject_lock: normalized (x, y) manual subject lock for face modes.
    """
    mode = str(mode or "general").strip().lower()
    # UI aliases share the same render engine but remain separate product
    # labels.  Keep this normalization in one place so pipeline/retrim/cut
    # cannot disagree about what a layout means.
    aliases = {
        "screencast": "gameplay",
        "track": "face",       # direct callers get a stable face crop;
        "auto": "face",        # pipeline AUTO still uses dynamic reframe.
    }
    mode = aliases.get(mode, mode)

    w, h = get_resolution(src)
    if adaptive_size:
        tw, th = delivery_size(w, h, aspect)
    else:
        tw, th = TARGET_FRAMES.get(aspect, TARGET_FRAMES["9:16"])
    if mode == "square":
        tw, th = TARGET_FRAMES["1:1"]

    if mode == "center":
        _center_crop(src, out_path, w, h, tw, th, log)
    elif mode == "face":
        _face_crop(src, out_path, w, h, tw, th, log,
                   subject_lock=subject_lock)
    elif mode == "split":
        _split_crop(src, out_path, w, h, tw, th, log,
                    subject_lock=subject_lock)
    elif mode == "panel":
        _panel_crop(src, out_path, w, h, tw, th, log,
                    subject_lock=subject_lock)
    elif mode == "alternate":
        _alternate_crop(src, out_path, w, h, tw, th, log,
                        subject_lock=subject_lock)
    elif mode == "gameplay":
        _gameplay_crop(src, out_path, w, h, tw, th, log,
                       face_position=gameplay_face_position,
                       main_ratio=gameplay_ratio)
    elif mode == "square":
        _square_crop(src, out_path, w, h, tw, th, log)
    else:
        if mode != "general":
            log and log(f"⚠️ Unknown layout '{mode}' — fallback GENERAL")
        # GENERAL: a source that already matches the requested aspect should
        # fill the frame directly.  Blur-fill in that case shrank an existing
        # vertical Short into a small middle band with huge blur above/below.
        source_ratio = w / h if h else 0
        target_ratio = tw / th if th else 0
        if target_ratio and abs(source_ratio - target_ratio) / target_ratio < 0.02:
            _center_crop(src, out_path, w, h, tw, th, log)
            return out_path
        # Legacy GENERAL (restored): the WHOLE source is contained inside the
        # portrait canvas and the leftover area is a blurred copy of the same
        # frame. Nobody is cropped, the full frame stays visible — exactly the
        # behaviour before the zero-blur experiment (commit b5a559a).
        vf = (
            f"[0:v]split=2[bg][fg];"
            f"[bg]scale={tw}:{th}:force_original_aspect_ratio=increase,"
            f"crop={tw}:{th},gblur=sigma=12[bg_blur];"
            f"[fg]scale={tw}:{th}:force_original_aspect_ratio=decrease,"
            f"setsar=1[fg_scaled];"
            f"[bg_blur][fg_scaled]overlay=(W-w)/2:(H-h)/2:shortest=1,setsar=1[v]"
        )
        run(["ffmpeg", "-y", "-i", src, "-filter_complex", vf,
             "-map", "[v]", "-map", "0:a?", *encode_args(log=log),
             "-c:a", "aac", "-b:a", "128k", out_path], log)
    return out_path


def _aspect_window(w: int, h: int, tw: int, th: int) -> tuple:
    """Largest window inside (w,h) matching tw:th aspect. Returns (ww, wh).

    Clamped so the window never exceeds the source frame — a too-tall window
    made ffmpeg crop beyond the frame (black/glitched output)."""
    if w * th <= h * tw:   # width-limited
        ww, wh = w, int(w * th / tw)
    else:
        ww, wh = int(h * tw / th), h
    if wh > h:
        wh = h
        ww = int(wh * tw / th)
    if ww > w:
        ww = w
        wh = int(ww * th / tw)
    return max(2, ww), max(2, wh)


def _center_crop(src, out_path, w, h, tw, th, log):
    """Plain center crop (no blur) — cheapest path."""
    ww, wh = _aspect_window(w, h, tw, th)
    x = (w - ww) // 2
    y = (h - wh) // 2
    run([
        "ffmpeg", "-y", "-i", src,
        "-vf", f"crop={ww}:{wh}:{x}:{y},scale={tw}:{th}"
               f"{_sharpen_for_upscale(ww, wh, tw, th)},setsar=1",
        *encode_args(log=log),
        "-c:a", "aac", "-b:a", "128k",
        out_path,
    ], log)


def _face_crop(src, out_path, w, h, tw, th, log,
               subject_lock: tuple | None = None):
    """Full-frame face layout: sharp crop, with active-speaker cuts for 2-shot.

    The explicit ``FACE`` layout is a deliberate close-up choice, so adding a
    blurred canvas here is misleading (and softens the whole composition).
    When two verified speakers coexist, reuse the single-pass speaker-cut
    engine so a left/right conversation does not remain locked to its first
    median face.
    """
    try:
        # Explicit FACE always stays face-framed. Speaker state resets at fast
        # camera cuts, and every chosen crop target is guarded by a real face
        # detection so a TV/table/body proxy cannot become the destination.
        from reframe import reframe_clip
        log and log("🎙️ FACE: fast-cut active speaker + face guardian")
        return reframe_clip(src, out_path, aspect="9:16", strategy="track",
                            log=log, explicit_face=True,
                            subject_lock=subject_lock)
    except Exception as exc:  # noqa: BLE001 — retain safe sharp fallback
        log and log(f"⚠️ FACE active-speaker tracking unavailable: {exc}")
        center = _stable_face_center(src, log)
        cx, cy = center if center else (w / 2, h / 2)
        ww, wh = _aspect_window(w, h, tw, th)
        x = max(0, min(int(cx - ww / 2), w - ww))
        y = max(0, min(int(cy - wh / 2), h - wh))
        _crop_window(src, out_path, x, y, ww, wh, tw, th,
                     blurred=False, log=log)
        return out_path


def _person_window(w, h, aspect_w, aspect_h):
    """Largest (ww, wh) crop window that fits the source and matches a target
    sub-frame aspect (aspect_w × aspect_h, e.g. one split/panel half)."""
    if aspect_w >= aspect_h:
        ww = w
        wh = int(w * aspect_h / aspect_w)
        if wh > h:
            wh = h
            ww = int(h * aspect_w / aspect_h)
    else:
        wh = h
        ww = int(h * aspect_w / aspect_h)
        if ww > w:
            ww = w
            wh = int(w * aspect_h / aspect_w)
    return max(2, ww), max(2, wh)


def _static_side_mask(src, positions, log=None):
    """Per-position static flags via mouth-activity (True = poster/doll/photo).

    Same measurement and thresholds as the SPLIT anti-poster gate, but
    per-side instead of all-or-nothing, so callers can EXCLUDE the static
    candidate and keep the live speakers instead of rejecting everything.
    Fail-open: any sampling failure marks every side live, so a quiet
    speaker is never deleted by a broken measurement.
    """
    mask = [False] * len(positions)
    if len(positions) < 2:
        return mask
    try:
        boxes = _speaker_activity_boxes(src, positions, samples=10)
    except Exception:
        return mask
    if len(boxes) < 2:
        return mask
    dur = get_duration(src)
    if dur <= 0:
        return mask
    try:
        import active_speaker
        activity = active_speaker.decode_activity(src, 0.0, dur, boxes)
    except Exception:
        return mask
    if not activity:
        return mask
    import numpy as np
    for si in range(len(boxes)):
        scores = [frame[si] for frame in activity if si < len(frame)]
        if not scores:
            continue
        peak = float(max(scores))
        baseline = float(np.percentile(scores, 20))
        if (peak < SPLIT_MIN_MOUTH_PEAK
                and peak - baseline < SPLIT_MIN_MOUTH_SIGNAL):
            mask[si] = True
            log and log(f"🖼️ candidate {si} is static (poster/doll/photo) — "
                        "excluded from subject selection")
    return mask


def _drop_static_boxes(src, frames, log=None, start=0.0, end=None):
    """Exclude static printed faces, including samples with no live face left.

    Liveness is measured only inside the scene, in source coordinates. A
    rejected face must not become an anchor again during a detector miss.
    """
    try:
        return _drop_static_boxes_impl(src, frames, log=log, start=start, end=end)
    except Exception as exc:  # noqa: BLE001 — fail-open to unfiltered lists
        log and log(f"⚠️ poster filter skipped ({exc})")
        return frames


def _drop_static_boxes_impl(src, frames, log=None, start=0.0, end=None):
    import numpy as np
    w, h = get_resolution(src)
    if w <= 0 or h <= 0:
        return frames
    dim = float(max(w, h))
    radius = dim * 0.06
    # Greedy position clustering across samples (largest boxes seed first so
    # a big poster does not fragment into several "people").
    flat = []
    for si, faces in enumerate(frames):
        for f in faces or []:
            flat.append((si, (float(f[0]), float(f[1]),
                             float(f[2]), float(f[3]))))
    if not flat:
        return frames
    flat.sort(key=lambda item: item[1][2] * item[1][3], reverse=True)
    clusters: list[list[tuple]] = []
    for si, box in flat:
        placed = False
        for cl in clusters:
            mx = sum(b[0] for _, b in cl) / len(cl)
            my = sum(b[1] for _, b in cl) / len(cl)
            if (not any(sample == si for sample, _ in cl)
                    and (box[0] - mx) ** 2 + (box[1] - my) ** 2 <= radius ** 2):
                cl.append((si, box))
                placed = True
                break
        if not placed:
            clusters.append([(si, box)])
    # Only stable clusters earn a liveness verdict; transient singletons are
    # kept (fail-open) — they can never win a median pair anyway.
    testable = [cl for cl in clusters if len(cl) >= 2][:6]
    if not testable:
        return frames
    medians = []
    for cl in testable:
        arr = np.array([[b[0], b[1], b[2], b[3]] for _, b in cl])
        medians.append([float(v) for v in np.median(arr, axis=0)])
    dur = get_duration(src)
    if dur <= 0:
        return frames
    import active_speaker
    stop = min(dur, end) if end is not None else dur
    activity = active_speaker.decode_activity(src, start, max(0.0, stop - start), medians)
    if not activity:
        return frames
    static_idx = set()
    for ci, cl in enumerate(testable):
        arr = np.array([[b[0], b[1], b[2], b[3]] for _, b in cl])
        # Print test: identical pixels return identical boxes, so a cluster
        # frozen in place across samples is a printed image even when its
        # mouth ROI flickers (LED videotron refresh / compression shimmer
        # can push gray deltas above the mouth-activity bar). Real footage
        # of a person never yields bit-identical boxes — detector noise and
        # micro-motion always move them. Fail-open: needs 3+ samples so a
        # brief still moment cannot delete a quiet speaker, and the
        # keep-one-face rule below still applies.
        frozen = (len(cl) >= 3 and float(np.std(arr[:, 0])) < 0.5
                  and float(np.std(arr[:, 1])) < 0.5
                  and float(np.std(arr[:, 2])) < 0.5
                  and float(np.std(arr[:, 3])) < 0.5)
        scores = [frame[ci] for frame in activity if ci < len(frame)]
        mouth_static = False
        if scores:
            peak = float(max(scores))
            baseline = float(np.percentile(scores, 20))
            mouth_static = (peak < SPLIT_MIN_MOUTH_PEAK
                            and peak - baseline < SPLIT_MIN_MOUTH_SIGNAL)
        # Stable detector boxes alone are not proof of a photograph: a seated
        # human can speak without moving their head. Require flat mouth signal
        # too before using the LED/compression-shimmer rejection.
        flat_signal = bool(scores) and float(np.percentile(scores, 90) -
                                            np.percentile(scores, 20)) < SPLIT_MIN_MOUTH_SIGNAL
        if mouth_static or (frozen and flat_signal):
            static_idx.add(ci)
    if not static_idx:
        return frames
    static_meds = [medians[ci] for ci in sorted(static_idx)]
    log and log(f"🖼️ poster filter: {len(static_meds)} static face(s) excluded")
    out = []
    for faces in frames:
        if not faces:
            out.append(faces)
            continue
        kept = [f for f in faces
                if not any((float(f[0]) - m[0]) ** 2
                           + (float(f[1]) - m[1]) ** 2 <= radius ** 2
                           for m in static_meds)]
        # Never resurrect a rejected poster just because it is the only face
        # remaining. An empty result must use a no-subject layout instead.
        out.append(kept)
    return out
    """True when both stacked subjects are alive speakers, not static props.

    Anti-poster / anti-doll gate for SPLIT: the only way to know a detected
    "face" is a poster, mannequin or photo on the wall (rather than a person)
    is motion — real faces blink, breathe and move the mouth; a flat image does
    nothing. We reuse active_speaker's mouth-motion measurement: if EITHER side
    averages zero mouth activity across the whole clip, it is a static region
    masquerading as a speaker and the split would render a frozen poster
    half-height — the exact "detik terakhir poster di-split" bug.
    """
    if len(positions) < 2:
        return False
    try:
        boxes = _speaker_activity_boxes(src, positions, samples=10)
    except Exception:
        # If activity sampling fails (corrupt clip etc.), don't risk splitting
        # onto a likely-poster region — fall back to single FACE.
        return False
    if len(boxes) < 2:
        return False
    dur = get_duration(src)
    if dur <= 0:
        return False
    try:
        import active_speaker
        activity = active_speaker.decode_activity(src, 0.0, dur, boxes)
    except Exception:
        return False
    if not activity:
        return False
    # Each frame entry = [mouth_score_speaker_0, mouth_score_speaker_1].
    # A real speaking person moves; a poster stays at compression-noise level.
    import numpy as np
    for si in range(len(boxes)):
        scores = [frame[si] for frame in activity if si < len(frame)]
        if not scores:
            return False
        peak = float(max(scores))
        baseline = float(np.percentile(scores, 20))
        if (peak < SPLIT_MIN_MOUTH_PEAK
                and peak - baseline < SPLIT_MIN_MOUTH_SIGNAL):
            log and log(f"⚠️ speaker {si} has zero mouth activity (poster/doll) — "
                        "rejecting SPLIT")
            return False
    return True


def layout_fit_score(path: str, start: float, end: float, layout: str,
                     samples: int = 8, log=None) -> float:
    """How well does [start, end] suit the requested layout (0..1)?

    Layout-aware moment finding: an explicit SPLIT should prefer ranges
    where two (or more) live people actually share the frame, FACE ranges
    with a visible subject, GAMEPLAY ranges with no talking head to crop.
    CENTER/GENERAL/AUTO accept anything (1.0). Posters/dolls are excluded
    via the shared static-box filter, so a solo speaker in front of a
    backdrop photo does NOT count as "2 people". Fail-open: any measurement
    failure returns 1.0 so a broken probe never demotes a viral moment.
    """
    import cv2
    mode = str(layout or "").strip().lower()
    aliases = {"screencast": "gameplay", "track": "face", "auto": "face"}
    mode = aliases.get(mode, mode)
    if mode in ("", "auto", "none", "user", "center", "general"):
        return 1.0
    try:
        dur = get_duration(path)
        start = max(0.0, float(start))
        end = min(float(end), dur if dur > 0 else float(end))
        if end - start < 1.0:
            return 1.0
        cap = cv2.VideoCapture(path)
        sampled = []
        for i in range(max(1, samples)):
            t = start + (end - start) * (i + 1) / (max(1, samples) + 1)
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            if frame.mean() < 16:  # fade to black, same as the classifier
                continue
            faces = face_boxes(frame)
            if faces and frame.size:
                fw = float(frame.shape[1])
                faces = [b for b in faces
                         if b[2] >= SPLIT_MIN_FACE_WIDTH * fw]
            sampled.append(faces)
        cap.release()
        if len(sampled) < max(2, samples // 2):
            return 1.0
        # Source coords here (full-res frames) — same contract as _face_centers.
        filtered = _drop_static_boxes(path, sampled)
        counts = [len(f) for f in filtered]
        n = len(counts)
        if mode in ("split", "alternate"):
            fit = sum(1 for c in counts if c >= 2) / n
        elif mode == "panel":
            fit = min(1.0, sum(1 for c in counts if c >= 2) / n * 0.5
                      + sum(1 for c in counts if c >= 3) / n * 0.5)
        elif mode == "face":
            fit = sum(1 for c in counts if c >= 1) / n
        elif mode == "gameplay":
            fit = sum(1 for c in counts if c == 0) / n
        else:
            fit = 1.0
        log and log(f"📐 layout-fit {mode}: {fit:.0%} "
                    f"({start:.0f}-{end:.0f}s)")
        return round(max(0.0, min(1.0, fit)), 3)
    except Exception as exc:  # noqa: BLE001 — fail-open
        log and log(f"⚠️ layout-fit skipped ({exc})")
        return 1.0


def _mature_face(src, out_path, log, subject_lock=None):
    """Reuse the mature per-scene FACE path (no duplicate tracking logic).

    All SPLIT/PANEL/ALTERNATE fallbacks funnel through here so they render
    exactly like explicit FACE: per-scene locks, anchor hold across no-face
    gaps, active-speaker cuts on verified two-shots, and manual subject-lock
    support. Falls back to the sharp single-lock _face_crop only if the
    per-scene engine is unavailable (keeps old safe behaviour, no new code
    path for tracking itself).
    """
    try:
        from reframe import reframe_face
        log and log("🎙️ fallback → mature FACE (reframe_face per-scene)")
        return reframe_face(src, out_path, aspect="9:16", log=log,
                            work_dir=os.path.dirname(out_path) or None,
                            subject_lock=subject_lock)
    except Exception as exc:  # noqa: BLE001 — retain safe sharp fallback
        log and log(f"⚠️ mature FACE unavailable ({exc}) — single-lock FACE")
        w, h = get_resolution(src)
        tw, th = TARGET_FRAMES.get("9:16", (1080, 1920))
        _face_crop(src, out_path, w, h, tw, th, log,
                   subject_lock=subject_lock)
        return out_path


def _real_two_shot(src, positions, log=None):
    """True when both stacked subjects are alive speakers, not static props.

    Anti-poster / anti-doll gate for SPLIT: the only way to know a detected
    "face" is a poster, mannequin or photo on the wall (rather than a person)
    is motion — real faces blink, breathe and move the mouth; a flat image does
    nothing. We reuse active_speaker's mouth-motion measurement: if EITHER side
    averages zero mouth activity across the whole clip, it is a static region
    masquerading as a speaker and the split would render a frozen poster
    half-height — the exact "detik terakhir poster di-split" bug.
    """
    if len(positions) < 2:
        return False
    mask = _static_side_mask(src, positions, log=log)
    for si, static in enumerate(mask):
        if static:
            log and log(f"⚠️ speaker {si} has zero mouth activity (poster/doll) — "
                        "rejecting SPLIT")
            return False
    return True


def _split_windows_duplicate_subject(src, windows, samples=5):
    """Reject duplicate, empty, or face-cutting split panels.

    Window-overlap alone cannot catch two halves slicing the same large face.
    Use full-frame detections so each face retains its source identity.
    Both panels must own a mostly intact face, and every significant visible
    face must belong to one panel. Positive evidence of a bad crop triggers
    FACE fallback, including at shot boundaries where a person is leaning.
    """
    import cv2
    duration = get_duration(src)
    cap = cv2.VideoCapture(src)
    readable = valid = 0
    # Midpoint-only samples missed the initial lean and the last reaction in
    # otherwise stable shots. Include both ends and sample longer shots more
    # densely, capped like the existing split detector to bound analysis cost.
    samples = max(samples, min(24, int(duration * 2) + 1))
    try:
        for i in range(samples):
            margin = min(0.04, duration / 4)
            timestamp = margin + max(0, duration - 2 * margin) * i / max(1, samples - 1)
            cap.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000)
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            readable += 1
            scale = min(1.0, 640.0 / frame.shape[1])
            if scale < 1:
                frame = cv2.resize(frame, (640, int(frame.shape[0] * scale)))
            invalid = False
            occupied = [False, False]
            face_count = 0
            for cx, cy, fw, fh in dedupe_faces(face_boxes(frame), frame.shape[1]):
                if fw < SPLIT_MIN_FACE_WIDTH * frame.shape[1]:
                    continue
                face_count += 1
                fx, fy = cx - fw / 2, cy - fh / 2
                coverage = []
                for x, y, w, h in windows:
                    x, y, w, h = (v * scale for v in (x, y, w, h))
                    iw = max(0, min(fx + fw, x + w) - max(fx, x))
                    ih = max(0, min(fy + fh, y + h) - max(fy, y))
                    coverage.append(iw * ih / max(1, fw * fh))
                if all(c >= 0.30 for c in coverage):
                    invalid = True
                for panel, amount in enumerate(coverage):
                    if amount >= 0.30:
                        occupied[panel] = True
            invalid = invalid or not all(occupied)
            valid += int(not invalid)
    finally:
        cap.release()
    if readable == 0:
        # Probe unreadable = evidence unavailable, not proof of a bad
        # split. Failing CLOSED here broke synthetic/non-CV2 sources
        # (test_two_speaker_layout_builds_real_stack_filter) and any real
        # source whose frames decode only through the ffmpeg path.
        return False
    return valid < (readable + 1) // 2


def _split_crop(src, out_path, w, h, tw, th, log,
                subject_lock: tuple | None = None):
    """Two faces stacked vertically — OpenShorts parity.

    OpenShorts split_filtergraph geometry (SPLIT_TIGHTNESS=0.8):
      - crop_h = 0.8 × source_h  (tight crop, not full-height)
      - crop_w = crop_h × 1.125  (each half is ~9:16 portrait)
      - y = cy − 0.42 × crop_h   (bias UP so chins don't get cut)
      - vstack + pad (no double resample)
      - left person on top, right on bottom (by x position)

    Anti-poster gate: when one of the two detected "faces" is a static image
    (poster, mannequin, photo on the wall) rather than a live speaker, stacking
    it produces the exact "detik terakhir dibelakang poster tetap di-split"
    glitch. We reject a candidate whose crop region shows no mouth movement at
    all across the scene — real people blink and breathe, posters never do.
    """
    SPLIT_TIGHTNESS = 0.8
    SPLIT_Y_BIAS = 0.42  # fraction of crop_h above face center (avoids chin crop)

    # Portrait/square source guard: a vertical stack of two full-width crops
    # from an already-portrait frame shows the SAME frame twice (top/bottom
    # duplicate) instead of dividing one frame into two speakers. A portrait
    # two-shot is already full-frame — follow the active speaker instead.
    if h >= w:
        log and log("⚠️ SPLIT needs a landscape source — portrait input uses "
                    "FACE active-speaker fallback (no duplicate stack)")
        _mature_face(src, out_path, log, subject_lock=subject_lock)
        return
    # Group split (explicit SPLIT on 3+ people): divide the room at its
    # widest gap — top = left group, bottom = right group. The strategy
    # gate keeps 'split' for groups only on explicit request; AUTO still
    # routes crowds to GENERAL. Overlapping windows fall back to FACE so
    # the halves can never show the same frame twice.
    # Prefer a verified pair over noisy body-seat clustering. Failure to find
    # a pair does NOT reject a group: it proceeds to the independent 3+ path.
    found = _speaker_positions(src, samples=10)
    group = _group_split_windows(src, w, h, log=log) if len(found) < 2 else None
    if group is not None:
        (lx, ly, lw, lh), (rx, ry, rw, rh) = group
        _ix = max(0, min(lx + lw, rx + rw) - max(lx, rx))
        _iy = max(0, min(ly + lh, ry + rh) - max(ly, ry))
        _overlap = (_ix * _iy) / float(max(1, min(lw * lh, rw * rh)))
        if _overlap > 0.35:
            log and log(f"⚠️ SPLIT group halves overlap {_overlap:.0%} "
                        "(single-row lineup) — using FACE fallback")
            _mature_face(src, out_path, log, subject_lock=subject_lock)
            return
        if _split_windows_duplicate_subject(src, group):
            log and log("⚠️ SPLIT group has duplicate/empty/partial-face panels — FACE fallback")
            _mature_face(src, out_path, log, subject_lock=subject_lock)
            return
        log and log("👥 SPLIT group: left room on top, right room at bottom")
        half_th = th // 2
        divider_y = max(0, half_th - 2)
        vf = (
            f"[0:v]crop={lw}:{lh}:{lx}:{ly},"
            f"scale={tw}:{half_th}:flags=lanczos,"
            f"unsharp=5:5:{_split_sharpen(lw, lh, tw, half_th)}:5:5:0.0,"
            f"pad={tw}:{half_th}:(ow-iw)/2:(oh-ih)/2[L];"
            f"[0:v]crop={rw}:{rh}:{rx}:{ry},"
            f"scale={tw}:{th - half_th}:flags=lanczos,"
            f"unsharp=5:5:{_split_sharpen(rw, rh, tw, th - half_th)}:5:5:0.0,"
            f"pad={tw}:{th - half_th}:(ow-iw)/2:(oh-ih)/2[R];"
            f"[L][R]vstack=inputs=2,"
            f"drawbox=x=0:y={divider_y}:w={tw}:h=4:color=black@0.85:t=fill,"
            f"setsar=1"
        )
        run([
            "ffmpeg", "-y", "-i", src,
            "-filter_complex", vf,
            *encode_args(log=log),
            "-c:a", "aac", "-b:a", "128k",
            out_path,
        ], log)
        return
    if len(found) < 2:
        log and log("⚠️ <2 speakers detected — SPLIT uses single-speaker FACE fallback")
        _mature_face(src, out_path, log, subject_lock=subject_lock)
        return
    if not _real_two_shot(src, found, log=log):
        log and log("⚠️ one of two 'faces' is static (poster/doll) — "
                    "SPLIT uses static FACE fallback")
        _mature_face(src, out_path, log, subject_lock=subject_lock)
        return

    # OpenShorts: left person on top, right on bottom (by x position)
    left, right = sorted(found, key=lambda p: p[0])
    lc, rc = left, right

    # Size the close-up from speaker separation.  The old h*0.8 window became
    # almost the full frame on portrait sources, duplicating the same wide
    # studio shot in both halves instead of showing each speaker.
    separation = abs(rc[0] - lc[0])
    crop_w = int(max(w * 0.30, min(w * 0.65,
                                  separation * SPLIT_TIGHTNESS)))
    crop_h = max(4, int(crop_w / 1.125))
    # Clamp to source frame
    if crop_w > w:
        crop_w = w
        crop_h = max(4, int(crop_w / 1.125))
    if crop_h > h:
        crop_h = h
        crop_w = max(4, int(crop_h * 1.125))

    # Vertical bias: y = cy − 0.42 × crop_h (face sits at 42% from top,
    # giving 58% below for chin/neck/shoulders — avoids the chin crop bug)
    lx = max(0, min(int(lc[0] - crop_w / 2), w - crop_w))
    ly = max(0, min(int(lc[1] - SPLIT_Y_BIAS * crop_h), h - crop_h))
    rx = max(0, min(int(rc[0] - crop_w / 2), w - crop_w))
    ry = max(0, min(int(rc[1] - SPLIT_Y_BIAS * crop_h), h - crop_h))

    # Duplicate-half guard: SPLIT must divide ONE frame into TWO different
    # speakers, top and bottom. When the two windows overlap substantially
    # (close pair + wide crop), both halves show the same frame — fall back
    # to FACE with active-speaker cuts instead of a fake stack. Measured in
    # 2D area (not just horizontal) so vertically stacked duplicates are
    # caught too.
    if crop_w > 0 and crop_h > 0:
        _ix = max(0, min(lx + crop_w, rx + crop_w) - max(lx, rx))
        _iy = max(0, min(ly + crop_h, ry + crop_h) - max(ly, ry))
        _overlap = (_ix * _iy) / float(crop_w * crop_h)
        if _overlap > 0.35:
            log and log(f"⚠️ SPLIT halves overlap {_overlap:.0%} (same frame "
                        "twice) — using FACE active-speaker fallback")
            _mature_face(src, out_path, log, subject_lock=subject_lock)
            return

    if _split_windows_duplicate_subject(
            src, ((lx, ly, crop_w, crop_h), (rx, ry, crop_w, crop_h))):
        log and log("⚠️ SPLIT has duplicate/empty/partial-face panels — FACE fallback")
        _mature_face(src, out_path, log, subject_lock=subject_lock)
        return

    # OpenShorts: vstack + pad (no double resample). Each half is cropped
    # to crop_w × crop_h, then scaled to fit the delivery half-height.
    # pad ensures the final output is exactly tw × th even if the two
    # halves don't perfectly fill it after the scale. A thin dark divider
    # (Opus Clip style) separates the two stacked speakers at the seam.
    half_th = th // 2
    divider_y = max(0, half_th - 2)
    vf = (
        f"[0:v]crop={crop_w}:{crop_h}:{lx}:{ly},"
        f"scale={tw}:{half_th}:flags=lanczos,"
        f"unsharp=5:5:{_split_sharpen(crop_w, crop_h, tw, half_th)}:5:5:0.0,"
        f"pad={tw}:{half_th}:(ow-iw)/2:(oh-ih)/2[L];"
        f"[0:v]crop={crop_w}:{crop_h}:{rx}:{ry},"
        f"scale={tw}:{th - half_th}:flags=lanczos,"
        f"unsharp=5:5:{_split_sharpen(crop_w, crop_h, tw, th - half_th)}:5:5:0.0,"
        f"pad={tw}:{th - half_th}:(ow-iw)/2:(oh-ih)/2[R];"
        f"[L][R]vstack=inputs=2,"
        f"drawbox=x=0:y={divider_y}:w={tw}:h=4:color=black@0.85:t=fill,setsar=1"
    )
    run([
        "ffmpeg", "-y", "-i", src,
        "-filter_complex", vf,
        *encode_args(log=log),
        "-c:a", "aac", "-b:a", "128k",
        out_path,
    ], log)


def _panel_crop(src, out_path, w, h, tw, th, log,
                subject_lock: tuple | None = None, allow_group=False):
    """Panel layout: 3-4 people tiled into a 2x2 grid, or 2 people stacked.

    The old QuClippers panel did a side-by-side hstack of two 540x1920 strips,
    which made every participant a narrow pillar.  A panel of 2 people is
    better served by the top/bottom SPLIT stack (1080x960 each); 3-4 people
    use the 2x2 portrait grid from panel_layout (540x960 each).  Explicit
    PANEL selection is never replaced by GENERAL.
    """
    import main as m
    import panel_layout

    # Sample the clip and look for a stable panel of 3-4 coexisting faces.
    centres = _panel_centres(src, samples=10)
    if centres and len(centres) >= 3:
        if not allow_group:
            log and log("🧩 PANEL requires two subjects — group uses GENERAL")
            return crop_video(src, out_path, mode="general", log=log,
                              subject_lock=subject_lock)
        log and log(f"🧩 PANEL 2x2 grid: {len(centres)} people")
        graph = panel_layout.panel_filtergraph(w, h, tw, th, centres)
        # The grid graph ends in a labelled [v] output, which ffmpeg only
        # connects with an explicit map (otherwise "setsar has an unconnected
        # output" and the whole segment is dropped).
        run([
            "ffmpeg", "-y", "-i", src,
            "-filter_complex", graph,
            "-map", "[v]", "-map", "0:a?",
            *encode_args(log=log),
            "-c:a", "aac", "-b:a", "128k",
            out_path,
        ], log)
        return

    # Fall back to two people stacked top/bottom (SPLIT) — never the thin
    # side-by-side strips that read as a pillar of one person.
    found = _speaker_positions(src, samples=10)
    if len(found) >= 2 and not _real_two_shot(src, found, log=log):
        log and log("⚠️ one of two 'faces' is static (poster/doll) — "
                    "PANEL uses static FACE fallback")
        _mature_face(src, out_path, log, subject_lock=subject_lock)
        return
    if len(found) >= 2:
        # PANEL remains visually distinct from SPLIT: each verified speaker
        # owns a full-height portrait column, while the final canvas stays
        # exactly 1080x1920 (never a landscape band).
        left, right = sorted(found, key=lambda point: point[0])[:2]
        separation = abs(right[0] - left[0])
        panel_w = int(max(w * 0.22, min(w * 0.50, separation * 0.55)))
        panel_h = h
        panel_w = max(2, min(w, panel_w))
        positions = []
        for cx, cy in (left, right):
            x = max(0, min(int(cx - panel_w / 2), w - panel_w))
            positions.append((x, 0))
        # Duplicate-column guard: PANEL must divide ONE frame into TWO
        # different speakers, left and right. Overlapping columns show the
        # same person twice — fall back to FACE instead of a fake split.
        _col_overlap = (max(0, min(positions[0][0] + panel_w,
                                   positions[1][0] + panel_w)
                            - max(positions[0][0], positions[1][0]))
                        / float(panel_w)) if panel_w > 0 else 1.0
        if _col_overlap > 0.35:
            log and log(f"⚠️ PANEL columns overlap {_col_overlap:.0%} (same "
                        "frame twice) — using FACE fallback")
            _mature_face(src, out_path, log, subject_lock=subject_lock)
            return
        vf = (
            f"[0:v]crop={panel_w}:{panel_h}:{positions[0][0]}:0,"
            f"scale={tw // 2}:{th}:force_original_aspect_ratio=decrease:flags=lanczos,"
            f"unsharp=5:5:{_split_sharpen(panel_w, panel_h, tw // 2, th)}:5:5:0.0,"
            f"pad={tw // 2}:{th}:(ow-iw)/2:(oh-ih)/2:color=0x101218,setsar=1[L];"
            f"[0:v]crop={panel_w}:{panel_h}:{positions[1][0]}:0,"
            f"scale={tw - tw // 2}:{th}:force_original_aspect_ratio=decrease:flags=lanczos,"
            f"unsharp=5:5:{_split_sharpen(panel_w, panel_h, tw - tw // 2, th)}:5:5:0.0,"
            f"pad={tw - tw // 2}:{th}:(ow-iw)/2:(oh-ih)/2:color=0x101218,setsar=1[R];"
            f"[L][R]hstack=inputs=2,setsar=1"
        )
        log and log("🧩 PANEL (2 people) → side-by-side portrait columns")
        run(["ffmpeg", "-y", "-i", src, "-filter_complex", vf,
             *encode_args(log=log), "-c:a", "aac", "-b:a", "128k", out_path], log)
        return

    # A false-positive PANEL classification with one visible speaker must
    # still centre that face. GENERAL creates the legacy landscape band
    # and aims at the empty midpoint/table, as seen in job 9feb clip 1.
    log and log("⚠️ <2 speakers detected — PANEL uses centred FACE fallback")
    _mature_face(src, out_path, log, subject_lock=subject_lock)


def _panel_centres(path: str, samples: int = 10) -> list:
    """Median centres of a stable 3-4 person panel, or [].

    Mirrors panel_layout.detect_panel_scenes: only frames where 3-4 faces
    coexist and are well separated count, and the headcount must hold in at
    least half the readable samples so a single stray detection cannot decide
    the layout.
    """
    import cv2
    import main as m
    import panel_layout

    w, h = get_resolution(path)
    frame_w = w if w > 0 else 1920
    dur = get_duration(path)
    if dur <= 0:
        return []
    cap = cv2.VideoCapture(path)
    sampled = []
    step = dur / (samples + 1)
    for i in range(samples):
        cap.set(cv2.CAP_PROP_POS_MSEC, step * (i + 1) * 1000)
        ok, frame = cap.read()
        if not ok or frame is None:
            continue
        if frame.mean() < 16:  # fade to black, same as the classifier
            continue
        sampled.append(m.detect_face_candidates(frame))
    cap.release()
    if len(sampled) < max(4, samples // 2):
        return []
    # Poster/doll/photo exclusion (same shared filter as the pair paths):
    # a printed backdrop face must not occupy a grid tile. Candidates here
    # are corner-form [x, y, w, h] dicts, the filter works in center-form,
    # so convert, filter, and match survivors back by centre. A fully
    # static room keeps its candidates (fail-open) and degrades to columns.
    try:
        center_lists = []
        for cands in sampled:
            center_lists.append([
                (c["box"][0] + c["box"][2] / 2.0,
                 c["box"][1] + c["box"][3] / 2.0,
                 float(c["box"][2]), float(c["box"][3]))
                for c in cands])
        kept_lists = _drop_static_boxes(path, center_lists)
        filtered = []
        for cands, centers, kept in zip(sampled, center_lists, kept_lists):
            keep = set(kept)
            lived = [c for c, ctr in zip(cands, centers) if ctr in keep]
            filtered.append(lived)
        sampled = filtered
    except Exception:
        pass
    centres = panel_layout.analyze_scene(sampled, frame_w)
    return centres or []


def _alternate_crop(src, out_path, w, h, tw, th, log,
                    subject_lock: tuple | None = None):
    """Audio-driven active-speaker cut between two speakers (OpenShorts ALTERNATE).

    Replaces the fixed 3-second timer alternation. The two speakers are
    verified as a real two-shot, then hard cuts are driven by who actually
    holds the floor (mouth motion gated by the audio envelope), so the frame
    lands on the speaker who is talking instead of flipping on a clock.
    Every fallback is a static FACE tripod-lock — the fixed 3-second timer
    alternation is retired (it read as getar on single-speaker clips).
    """
    from active_speaker import (
        WINDOW_SECONDS,
        attribute_windows,
        audio_envelope,
        decode_activity,
        hold,
        normalise_activity,
    )

    found = _speaker_positions(src, samples=12)
    if len(found) < 2:
        log and log("⚠️ <2 speakers verified — ALTERNATE uses static FACE (no timer)")
        _mature_face(src, out_path, log, subject_lock=subject_lock)
        return out_path

    spk1, spk2 = found[0], found[1]
    ww, wh = _aspect_window(w, h, tw, th)
    work = os.path.dirname(out_path) or "."
    sp1_raw = os.path.join(work, "alt_sp1.mp4")
    sp2_raw = os.path.join(work, "alt_sp2.mp4")

    def _closeup(cx, cy, raw):
        x = max(0, min(int(cx - ww / 2), w - ww))
        y = max(0, min(int(cy - wh / 2), h - wh))
        run([
            "ffmpeg", "-y", "-i", src,
            "-vf", f"crop={ww}:{wh}:{x}:{y},scale={tw}:{th}"
                   f"{_sharpen_for_upscale(ww, wh, tw, th)},setsar=1",
            *encode_args(log=log),
            "-c:a", "aac", "-b:a", "128k",
            raw,
        ], log)

    _closeup(spk1[0], spk1[1], sp1_raw)
    _closeup(spk2[0], spk2[1], sp2_raw)

    def _cleanup():
        for p in (sp1_raw, sp2_raw,
                  os.path.join(work, "alt_concat.txt")):
            try:
                os.remove(p)
            except OSError:
                pass

    dur = get_duration(src)
    if dur <= 0:
        _cleanup()
        log and log("⚠️ ALTERNATE unreadable duration — static FACE")
        _mature_face(src, out_path, log, subject_lock=subject_lock)
        return out_path
    # Median face boxes from the verified two-shot (source coords).
    boxes = _speaker_activity_boxes(src, found)
    if len(boxes) < 2:
        _cleanup()
        log and log("⚠️ ALTERNATE no activity boxes — static FACE")
        _mature_face(src, out_path, log, subject_lock=subject_lock)
        return out_path
    activity = decode_activity(src, 0, dur, boxes)
    verdicts = None
    if activity:
        normed = normalise_activity(activity)
        loudness = audio_envelope(src, 0, dur)
        verdicts = hold(attribute_windows(normed, loudness))
    if not verdicts or all(v is None for v in verdicts):
        _cleanup()
        log and log("⚠️ No speaker verdicts — ALTERNATE uses static FACE (no timer)")
        _mature_face(src, out_path, log, subject_lock=subject_lock)
        return out_path

    window_dur = WINDOW_SECONDS
    # Build (speaker, start, end) segments from the per-window verdicts.
    segments = []
    current = verdicts[0] if verdicts[0] is not None else 0
    seg_start = 0.0
    for i, v in enumerate(verdicts):
        spk = v if v is not None else current
        if spk != current:
            segments.append((current, seg_start, i * window_dur))
            current = spk
            seg_start = i * window_dur
    segments.append((current, seg_start, dur))

    concat_file = os.path.join(work, "alt_concat.txt")
    with open(concat_file, "w", encoding="utf-8") as f:
        for spk_idx, start, end in segments:
            clip_path = sp1_raw if spk_idx == 0 else sp2_raw
            f.write(f"file '{clip_path}'\n")
            f.write(f"inpoint {start:.3f}\n")
            f.write(f"outpoint {end:.3f}\n")

    log and log(f"🎤 ALTERNATE: {len(segments)} speaker-cut segments")
    run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_file,
        "-c", "copy", out_path,
    ], log)
    _cleanup()
    return out_path


def _alternate_crop_legacy(src, out_path, w, h, tw, th, log,
                           subject_lock: tuple | None = None):
    """Retired timer alternation — now an alias for a static FACE lock.

    The 3-second `mod(t)` flip kept moving on single-speaker/silent clips and
    read as getar. Kept by name so old callers/tests import cleanly, but it
    renders zero motion. Falls back to a face crop when <2 faces are found."""
    log and log("⚠️ legacy timer alternation retired — static FACE lock")
    _mature_face(src, out_path, log, subject_lock=subject_lock)
    return out_path


def _gameplay_crop(src, out_path, w, h, tw, th, log,
                   face_position="bottom", main_ratio=0.60):
    """Sharp 60:40 gameplay + presenter stack inside a true 9:16 frame.

    The main/game pane always gets 60% of the canvas and the presenter pane
    gets 40%. Users choose whether the presenter is above or below; neither
    pane is a 16:9 band and no blurred filler is introduced.

    When the source is a screen recording with a webcam composited into a
    corner (OBS/stream VOD), the presenter pane crops exactly that inset box
    instead of a large window around the face — which used to show the same
    spreadsheet/game twice (once whole, once enlarged).
    """
    face_position = "top" if str(face_position).lower() == "top" else "bottom"
    try:
        main_ratio = float(main_ratio)
    except (TypeError, ValueError):
        main_ratio = 0.60
    # Keep the UI contract bounded to the three safe presets (50/50, 60/40,
    # 70/30). Arbitrary values make captions and face panels too small.
    main_ratio = min(0.70, max(0.50, main_ratio))
    main_h = int(round(th * main_ratio))
    main_h -= main_h % 2
    face_h = th - main_h

    main_w, main_src_h = _person_window(w, h, tw, main_h)
    main_x = max(0, (w - main_w) // 2)
    main_y = max(0, (h - main_src_h) // 2)

    # Prefer the detected webcam inset for the presenter pane.  Only use it
    # when the box is genuinely cornered/stable (camera_inset.detect returns
    # None otherwise), so a normal talking head still uses the face window.
    inset = None
    try:
        import camera_inset
        inset = camera_inset.detect(src)
    except Exception as exc:  # noqa: BLE001 — inset is an optimisation, never fatal
        log and log(f"⚠️ webcam inset check skipped: {exc}")

    if inset is not None:
        ix, iy, iw, ih = inset
        # Scale the inset crop to the presenter pane, preserving its aspect.
        face = (f"crop={iw}:{ih}:{ix}:{iy},"
                f"scale={tw}:{face_h}:flags=lanczos[face]")
        log and log(f"🎮 GAMEPLAY 60:40 · presenter {face_position} "
                    f"(webcam inset {iw}x{ih})")
    else:
        center = _stable_face_center(src, log)
        cx, cy = center if center else (w / 2, h / 2)
        face_w, face_src_h = _person_window(w, h, tw, face_h)
        face_x = max(0, min(int(cx - face_w / 2), w - face_w))
        face_y = max(0, min(int(cy - face_src_h * 0.42), h - face_src_h))
        # Duplicate-stack guard: when there is no distinct presenter, the
        # face pane centres on the same point as the main pane and stacking
        # shows the same frame twice, top and bottom. Render one sharp
        # full-frame pane instead. A real face anchor keeps the stack even
        # when the windows partly overlap — the panes are different framings
        # of a genuine presenter, not a duplicated wide shot.
        ix0, iy0 = max(main_x, face_x), max(main_y, face_y)
        ix1, iy1 = (min(main_x + main_w, face_x + face_w),
                    min(main_y + main_src_h, face_y + face_src_h))
        inter = max(0, ix1 - ix0) * max(0, iy1 - iy0)
        face_area = max(1, face_w * face_src_h)
        if center is None or inter / face_area > 0.85:
            log and log("🎮 GAMEPLAY single-pane: presenter overlaps main "
                        "(no distinct speaker) — no duplicate stack")
            _crop_window(src, out_path, main_x, main_y, main_w, main_src_h,
                         tw, th, blurred=False, log=log)
            return
        face = (f"crop={face_w}:{face_src_h}:{face_x}:{face_y},"
                f"scale={tw}:{face_h}:flags=lanczos[face]")
        log and log(f"🎮 GAMEPLAY 60:40 · presenter {face_position}")

    main = (f"crop={main_w}:{main_src_h}:{main_x}:{main_y},"
            f"scale={tw}:{main_h}:flags=lanczos[main]")
    stack = "[face][main]vstack=inputs=2" if face_position == "top" else \
        "[main][face]vstack=inputs=2"
    vf = f"[0:v]{main};[0:v]{face};{stack},setsar=1"
    run([
        "ffmpeg", "-y", "-i", src,
        "-filter_complex", vf,
        *encode_args(log=log),
        "-c:a", "aac", "-b:a", "128k",
        out_path,
    ], log)


def _square_crop(src, out_path, w, h, tw, th, log):
    """1:1 center crop on a blurred background."""
    size = min(w, h)
    x = (w - size) // 2
    y = (h - size) // 2
    vf = (
        f"split[a][b];"
        f"[a]scale={tw}:{th},boxblur=20:5[bg];"
        f"[b]crop={size}:{size}:{x}:{y},scale={tw}:{th}"
        f"{_sharpen_for_upscale(size, size, tw, th)}[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2"
    )
    run([
        "ffmpeg", "-y", "-i", src,
        "-vf", vf,
        *encode_args(log=log),
        "-c:a", "aac", "-b:a", "128k",
        out_path,
    ], log)


def _crop_window(src, out_path, x, y, win_w, win_h, tw, th, blurred=True, log=None):
    if blurred:
        vf = (
            f"split[a][b];"
            f"[a]scale={tw}:{th},setsar=1,boxblur=20:5[bg];"
            f"[b]crop={win_w}:{win_h}:{x}:{y},"
            f"scale={tw}:{th}{_sharpen_for_upscale(win_w, win_h, tw, th)},setsar=1[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1"
        )
    else:
        vf = (f"crop={win_w}:{win_h}:{x}:{y},"
              f"scale={tw}:{th}{_sharpen_for_upscale(win_w, win_h, tw, th)},setsar=1")
    run([
        "ffmpeg", "-y", "-i", src,
        "-vf", vf,
        *encode_args(log=log),
        "-c:a", "aac", "-b:a", "128k",
        out_path,
    ], log)


# ─── scaling / export ─────────────────────────────────────────────────────

RESOLUTIONS = {
    "720": "720x1280",
    "1080": "1080x1920",
}


def scale_clip(src: str, out_path: str, res: str = "1080", log=None) -> str:
    """Re-encode a clip to a target resolution.

    For portrait (9:16) targets, use the SAME OpenShorts blur-fill layout as
    the v2 reframe engine: the source is scaled to fit width, the overflow is
    filled with a blurred copy of the video (NOT black letterbox bars). This
    keeps 720p exports pixel-identical in framing to the 1080p engine output.
    For 1:1 / 16:9 targets fall back to plain letterbox pad.
    """
    dims = RESOLUTIONS.get(res, RESOLUTIONS["1080"])
    w, h = dims.split("x")
    ow, oh = int(w), int(h)
    # Portrait -> blur fill (OpenShorts parity). Landscape/square -> pad.
    if oh > ow:  # 9:16
        vf = (
            f"[0:v]split=2[bg][fg];"
            f"[bg]scale={ow}:{oh}:force_original_aspect_ratio=increase,"
            # A portrait pixel frame can still be displayed as landscape when
            # FFmpeg carries the source's non-square sample aspect ratio into
            # the export.  Normalise BOTH branches and the composed frame.
            f"crop={ow}:{oh},setsar=1,gblur=sigma=12[bg_blur];"
            f"[fg]scale={ow}:{oh}:force_original_aspect_ratio=decrease,"
            f"setsar=1[fg_scaled];"
            f"[bg_blur][fg_scaled]overlay=(W-w)/2:(H-h)/2:shortest=1,"
            f"setsar=1[v]"
        )
        run([
            "ffmpeg", "-y", "-i", src,
            "-filter_complex", vf, "-map", "[v]", "-map", "0:a?",
            *encode_args(log=log),
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            out_path,
        ], log)
    else:
        vf = (
            f"scale={dims}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,setsar=1"
        )
        run([
            "ffmpeg", "-y", "-i", src,
            "-vf", vf,
            *encode_args(log=log),
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            out_path,
        ], log)
    return out_path


def apply_auto_zoom(src: str, out_path: str, zoom: float = 1.15,
                    log=None) -> str:
    """Submagic-style punch-in: smooth zoom from 1.0 toward `zoom` over the
    whole clip (center-anchored). Applied BEFORE captions are burned so the
    subtitle text stays a constant size.
    """
    w, h = get_resolution(src)
    dur = get_duration(src)
    if dur <= 0:
        dur = 5.0
    fps = 30
    n = max(1, int(dur * fps))
    zoom = max(1.0, float(zoom))
    step = (zoom - 1.0) / n
    # zoompan only needs the source upscaled to ~zoom× (any larger just burns
    # CPU: 8000px frames on 1080p sources are ~50× slower). `on` = output
    # frame counter, so z=1 on frame 0 and reaches `zoom` on the last frame.
    # NB: the comma inside min() MUST be escaped (\,) or ffmpeg parses it
    # as a filter separator.
    sw = max(w, int(w * zoom) + 2)
    sh = max(h, int(h * zoom) + 2)
    vf = (
        f"scale={sw}:{sh},"
        f"zoompan=z='min(1+{step:.6f}*on\\,{zoom:.4f})':d=1:"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"s={w}x{h}:fps={fps}"
    )
    run([
        "ffmpeg", "-y", "-i", src, "-vf", vf,
        *encode_args(crf=23, preset="veryfast", log=log),
        "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
        out_path,
    ], log)
    return out_path


def apply_punch_zoom(src: str, out_path: str, max_zoom: float = 1.12,
                     log=None) -> str:
    """Audio-beat punch-in (OpenShorts): a fast-in / hold / slow-out zoom on
    each emphasis beat instead of the whole-clip linear ramp. Applied BEFORE
    captions so subtitle text stays a constant size. When the source has no
    beats worth punching, the file is copied through untouched rather than
    re-encoded.
    """
    import punch_in
    from punch_in import emphasis_times, punch_expression

    dur = get_duration(src)
    if dur <= 0:
        dur = 5.0
    max_zoom = float(max_zoom)
    beats = emphasis_times(src, dur)
    expr = punch_expression(beats, max_zoom=max_zoom, fps=30)
    if expr is None:
        log and log("🔇 No punch beats — copying through untouched")
        shutil.copyfile(src, out_path)
        return out_path

    w, h = get_resolution(src)
    sw = max(w, int(w * max_zoom) + 2)
    sh = max(h, int(h * max_zoom) + 2)
    # Commas inside the expression are filter separators unless escaped
    # (same reason apply_auto_zoom escapes the one inside min()).
    expr = expr.replace(",", "\\,")
    vf = (
        f"scale={sw}:{sh},"
        f"zoompan=z='{expr}':d=1:"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"s={w}x{h}:fps=30"
    )
    run([
        "ffmpeg", "-y", "-i", src, "-vf", vf,
        *encode_args(crf=23, preset="veryfast", log=log),
        "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
        out_path,
    ], log)
    return out_path


def apply_zoom(src: str, out_path: str, zoom: float = 1.15,
               punch_zoom=None, log=None) -> str:
    """Punch-in entry point. The OpenShorts audio-beat punch-in is the default
    whenever `auto_zoom` is enabled; pass punch_zoom=False to keep the legacy
    whole-clip linear ramp."""
    if punch_zoom is not None and not punch_zoom:
        return apply_auto_zoom(src, out_path, zoom=zoom, log=log)
    return apply_punch_zoom(src, out_path, max_zoom=zoom, log=log)


def concat_clips(paths: list, out_path: str, log=None,
                 durations: list[float] | None = None) -> str:
    """Concatenate clips (same codec/size) into one file.

    When 2+ segments are joined, a short crossfade (xfade) smooths the
    scene-to-scene cut so the reframe change doesn't snap hard — OpenShorts
    delivers a continuous camera move, not a jump. Single clip = stream copy.
    """
    if len(paths) == 1:
        run(["ffmpeg", "-y", "-i", paths[0], "-c", "copy", out_path], log)
        return out_path

    # Scene parts are produced by the same renderer with identical delivery
    # parameters. A concat-demuxer stream copy therefore preserves exact
    # camera-cut timing and avoids a full extra 1080x1920 encode. The former
    # 250 ms xfade was expensive, ghosted across genuine camera cuts, and
    # shortened the timeline relative to ASS subtitle timestamps.
    if os.environ.get("KLIPIN_SCENE_CROSSFADE", "0").strip().lower() not in {
            "1", "true", "yes", "on"}:
        list_file = os.path.join(os.path.dirname(out_path),
                                 f".concat_{os.path.basename(out_path)}.txt")
        with open(list_file, "w", encoding="utf-8") as handle:
            handle.write("ffconcat version 1.0\n")
            for index, path in enumerate(paths):
                escaped = os.path.abspath(path).replace("'", "'\\''")
                handle.write(f"file '{escaped}'\n")
                # The concat demuxer otherwise offsets each next file using
                # the MP4 container duration. Short CFR/AAC scene renders can
                # differ by an encoder-delay frame, accumulating timestamp
                # gaps (and, with recent FFmpeg, an unsupported PTS/DTS pair)
                # before the final subtitle pass. The scene planner already
                # knows the exact intended durations, so make those offsets
                # explicit without adding another video encode.
                if durations is not None and index < len(durations):
                    try:
                        duration = float(durations[index])
                    except (TypeError, ValueError):
                        duration = 0.0
                    if duration > 0:
                        handle.write(f"duration {duration:.6f}\n")
        try:
            log and log(f"⚡ Fast scene join: {len(paths)} parts (stream copy)")
            run([
                "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                "-i", list_file, "-c", "copy", "-movflags", "+faststart",
                out_path,
            ], log)
        finally:
            try:
                os.remove(list_file)
            except OSError:
                pass
        return out_path

    # Gather durations to place xfade points (offset = cumulative end - fade).
    durs = []
    for p in paths:
        d = get_duration(p)
        if d <= 0:
            d = 1.0
        durs.append(d)

    fade = 0.25  # smooth 250ms crossfade between scenes
    # xfade chain: needs N-1 xfades; each offset is the cumulative END of the
    # previous joined segment minus the fade overlap.
    offsets = []
    cum = durs[0]
    for d in durs[1:]:
        # xfade's offset is measured on the already-composed timeline: the
        # next transition begins just before the CURRENT composition ends.
        # The old code added the incoming clip first, placing the transition
        # past the end of its first input and truncating multi-scene renders.
        offsets.append(max(0.1, cum - fade))
        cum += d - fade  # overlap consumed by the fade

    if len(paths) == 2:
        vf = (
            f"[0:v][1:v]xfade=transition=fade:duration={fade}:"
            f"offset={offsets[0]:.3f}[v];"
            f"[0:a][1:a]acrossfade=duration={fade}[a]"
        )
        run([
            "ffmpeg", "-y",
            "-i", paths[0], "-i", paths[1],
            "-filter_complex", vf,
            "-map", "[v]", "-map", "[a]",
            *encode_args(log=log),
            out_path,
        ], log)
    else:
        # Chain xfade for 3+ inputs via labels. Video and audio chains are
        # built separately — interleaving them in one string glues an audio
        # label to the next video filter (ffmpeg parse error).
        inputs = []
        for p in paths:
            inputs += ["-i", p]
        vparts = []
        aparts = []
        vlabel = "0:v"
        alabel = "0:a"
        for i in range(1, len(paths)):
            out_v = f"v{i}" if i < len(paths) - 1 else "vout"
            out_a = f"a{i}" if i < len(paths) - 1 else "aout"
            vparts.append(
                f"[{vlabel}][{i}:v]xfade=transition=fade:duration={fade}:"
                f"offset={offsets[i-1]:.3f}[{out_v}]")
            aparts.append(
                f"[{alabel}][{i}:a]acrossfade=duration={fade}[{out_a}]")
            vlabel = out_v
            alabel = out_a
        run([
            "ffmpeg", "-y", *inputs,
            "-filter_complex", ";".join(vparts + aparts),
            "-map", "[vout]", "-map", "[aout]",
            *encode_args(log=log),
            out_path,
        ], log)
    try:
        list_file = os.path.join(os.path.dirname(out_path), ".concat.txt")
        if os.path.exists(list_file):
            os.remove(list_file)
    except OSError:
        pass
    return out_path
