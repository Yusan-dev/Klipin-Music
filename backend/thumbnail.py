"""Thumbnail Studio — per-clip YouTube thumbnail generation.

Ported from QuClippers `thumbnail.py` and adapted to Klipin's per-clip model:
each clip already has a title + hook (from Gemini during analysis), so we
only send text (no video upload) to the image model, keeping it cheap.

  generate_thumbnails(title, hook, out_dir, count, extra_prompt)
      -> list of absolute file paths (9:16 JPGs, gemini image model)

  suggest_titles(title, hook, transcript, count)
      -> list of viral YouTube title candidates (text model, cheap)

Runs on demand (user clicks "Thumbnail" on a clip), not during the pipeline.
"""
import json
import os

IMAGE_MODEL = os.environ.get("GEMINI_IMAGE_MODEL", "gemini-3.1-flash-image-preview")
TEXT_MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")

THUMB_PROMPT = """You are a thumbnail designer for a YouTube Shorts creator.
Create a professional, eye-catching YouTube Shorts THUMBNAIL image (9:16 portrait).

VIDEO TITLE (for context — do NOT paste the full title on the image): "{title}"
HOOK (the emotional promise of the clip): "{hook}"

TEXT ON THE THUMBNAIL:
- A SHORT visual hook: 1 to 5 words maximum (ALL CAPS, split into 2-3 lines)
- It must capture the core emotion/surprise/promise of the clip
- It should COMPLEMENT the title, not repeat it
- Examples: "GAK NYANGKA! 🔥", "INI RAHASIANYA", "3 MENIT LAGI…", "NO WAY 🤯"
- The text MUST be large, bold, high-contrast — readable at small sizes

DESIGN REQUIREMENTS:
- Vibrant, eye-catching colors that match the clip's mood
- Clean composition, one clear focal point
- Portrait canvas, full-frame composition; never output a landscape image
- NO clutter, NO small text, NO watermarks, NO logos
"""


def _json_or_none(text: str):
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def generate_thumbnails(title: str, hook: str, out_dir: str, count: int = 3,
                        extra_prompt: str = "", log=None) -> list:
    """Generate `count` 9:16 thumbnails with Gemini. Returns list of paths."""
    from google import genai
    from google.genai import types

    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        raise RuntimeError("GEMINI_API_KEY not set (thumbnail needs it)")

    os.makedirs(out_dir, exist_ok=True)
    client = genai.Client(api_key=key)

    prompt = THUMB_PROMPT.format(title=title[:120], hook=hook[:200])
    if extra_prompt.strip():
        prompt += (f"\nMANDATORY USER INSTRUCTIONS (override defaults):\n"
                   f"{extra_prompt.strip()}")

    paths = []
    last_error = None
    for i in range(count):
        log and log(f"🎨 Generating thumbnail {i + 1}/{count} ({IMAGE_MODEL})...")
        try:
            resp = client.models.generate_content(
                model=IMAGE_MODEL,
                contents=[prompt],
                config=types.GenerateContentConfig(
                    response_modalities=["TEXT", "IMAGE"],
                    image_config=types.ImageConfig(aspect_ratio="9:16",
                                                   image_size="2K"),
                ),
            )
            saved = False
            for part in resp.parts:
                if part.text is not None:
                    log and log(f"📝 {part.text[:160]}")
                elif image := part.as_image():
                    path = os.path.join(out_dir, f"thumb_{i + 1}.jpg")
                    image.save(path)
                    paths.append(path)
                    saved = True
                    break
            if not saved:
                last_error = "Gemini returned no image part"
        except Exception as e:  # noqa: BLE001
            last_error = str(e)
            log and log(f"❌ Thumbnail {i + 1} failed: {e}")
    if not paths:
        raise RuntimeError(f"All thumbnail generations failed. "
                           f"Last error: {last_error or 'unknown'}")
    log and log(f"✅ {len(paths)} thumbnail(s) saved")
    return paths


def suggest_titles(title: str, hook: str, transcript: dict | None = None,
                   count: int = 5, log=None) -> list:
    """Suggest viral YouTube titles for a clip (cheap text call)."""
    from google import genai

    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        raise RuntimeError("GEMINI_API_KEY not set (thumbnail needs it)")

    segs = (transcript or {}).get("segments") or []
    snippet = " ".join(s.get("text", "").strip() for s in segs[:120])[:1200]

    prompt = f"""You are a YouTube title expert. Suggest {count} viral, click-worthy
titles for this Shorts clip (max 60 chars each, same language as the content).

CLIP TITLE: "{title}"
HOOK: "{hook}"
{('SOURCE SNIPPET: ' + snippet) if snippet else ''}

Mix styles: curiosity gap, emotional trigger, question, number/list, story-driven.
Return STRICT JSON: {{"titles": ["...", "..."]}} — no markdown."""

    client = genai.Client(api_key=key)
    log and log(f"💡 Suggesting titles ({TEXT_MODEL})...")
    resp = client.models.generate_content(model=TEXT_MODEL,
                                          contents=[prompt])
    data = _json_or_none(resp.text)
    titles = (data or {}).get("titles") or []
    return [str(t).strip()[:70] for t in titles if str(t).strip()][:count]


def suggest_thumbnail_hook(title: str, hook: str,
                           transcript: dict | None,
                           start: float, end: float, log=None) -> str:
    """Read the clip transcript and return one short editorial thumbnail hook."""
    from google import genai

    segs = (transcript or {}).get("segments") or []
    clip_text = " ".join(
        str(s.get("text", "")).strip() for s in segs
        if float(s.get("end", 0) or 0) >= start
        and float(s.get("start", 0) or 0) <= end
    )[:2400]
    fallback = (hook or title or "WAJIB NONTON").strip()[:80]
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key or not clip_text:
        return fallback

    prompt = f"""Baca isi klip berikut lalu buat SATU headline thumbnail yang kuat.
Gunakan bahasa yang sama dengan klip. Panjang 3-8 kata, konkret, memancing rasa
ingin tahu tanpa clickbait palsu. Jangan beri tanda kutip, penjelasan, pilihan,
nomor, markdown, atau JSON. Keluarkan headline saja.

JUDUL: {title}
HOOK LAMA: {hook}
TRANSKRIP KLIP: {clip_text}"""
    try:
        log and log("🧠 AI membaca isi klip untuk auto-hook thumbnail...")
        # Keep a strong reference for the whole request. Constructing the
        # client inline can let its finalizer close the HTTP transport while
        # generate_content is still running.
        client = genai.Client(api_key=key)
        resp = client.models.generate_content(model=TEXT_MODEL,
                                              contents=[prompt])
        value = " ".join((resp.text or "").strip().strip('"').split())
        if value:
            log and log(f"✨ Auto-hook: {value[:100]}")
            return value[:100]
    except Exception as e:  # noqa: BLE001
        log and log(f"⚠️ Auto-hook memakai hook klip ({e})")
    return fallback


def _ffmpeg_thumbnails(source_path: str, start: float, end: float,
                       title: str, hook: str, out_dir: str, count: int,
                       log=None, frame_mode: str = "full",
                       include_text: bool = True,
                       filename_prefix: str = "thumb",
                       canvas: tuple[int, int] = (1080, 1920)) -> list:
    """Free fallback: extract clean frames from the SOURCE video (no burned
    subtitles) and overlay a big hook line + small title, 1080x1920 JPGs.
    Frames are picked by an "interestingness" heuristic (cv2 variance).
    """
    import subprocess
    import cv2
    import tempfile

    log and log("🖼️ Gemini image model unavailable (free tier) — "
                "falling back to local ffmpeg thumbnails")
    duration = max(1.0, end - start)
    # candidate timestamps spread across the clip
    cand = [start + duration * f for f in (0.15, 0.3, 0.45, 0.6, 0.75, 0.9)]
    scored = []
    with tempfile.TemporaryDirectory() as tmp:
        for t in cand:
            probe = subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.2f}",
                 "-i", source_path, "-frames:v", "1", "-vf",
                 "scale=320:-2", "-q:v", "6",
                 os.path.join(tmp, "f.jpg")],
                capture_output=True, text=True)
            if probe.returncode != 0 or not os.path.exists(os.path.join(tmp, "f.jpg")):
                continue
            img = cv2.imread(os.path.join(tmp, "f.jpg"))
            if img is None:
                continue
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            scored.append((float(gray.var()), t))
            os.remove(os.path.join(tmp, "f.jpg"))
    if not scored:
        scored = [(0.0, start + duration * 0.3)]
    scored.sort(reverse=True)
    picks = scored[:count]

    from subtitles import _drawtext_escape

    palettes = [
        ("#FFD400", "#0B0E1F"),   # gold on navy
        ("#00F0FF", "#0B0E1F"),   # cyan on navy
        ("#FFFFFF", "#000000"),   # white on black
        ("#FF2EAE", "#0B0E1F"),   # magenta on navy
    ]
    hook_line = _split_lines(hook or title, 3)
    canvas_w, canvas_h = canvas
    hook_font_size = 80 if canvas_h > canvas_w else 72
    paths = []
    for i, (_, t) in enumerate(picks):
        fg, bg = palettes[i % len(palettes)]
        out = os.path.join(out_dir, f"{filename_prefix}_{i + 1}.jpg")
        draw = []
        if include_text:
            y = int(canvas_h * 0.62)
            for line in hook_line:
                draw.append(
                    f"drawtext=text='{_drawtext_escape(line)}':"
                    f"fontsize={hook_font_size}:fontcolor={fg}:borderw=6:bordercolor=black@0.9:"
                    f"x=(w-text_w)/2:y={y}:line_spacing=6")
                y += hook_font_size + 24
            if title:
                draw.append(
                    f"drawtext=text='{_drawtext_escape(title[:80])}':"
                    f"fontsize=24:fontcolor=white@0.85:borderw=2:bordercolor=black@0.8:"
                    f"x=(w-text_w)/2:y=h-text_h-28")
        if frame_mode == "crop":
            vf = (f"scale={canvas_w}:{canvas_h}:force_original_aspect_ratio=increase,"
                  f"crop={canvas_w}:{canvas_h},setsar=1")
        else:
            vf = (f"scale={canvas_w}:{canvas_h}:force_original_aspect_ratio=decrease,"
                  f"pad={canvas_w}:{canvas_h}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1")
        if draw:
            vf += "," + ",".join(reversed(draw))
        r = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.2f}",
             "-i", source_path, "-frames:v", "1", "-vf", vf, out],
            capture_output=True, text=True)
        if r.returncode == 0 and os.path.exists(out):
            paths.append(out)
    if not paths:
        raise RuntimeError("Local thumbnail generation failed")
    log and log(f"✅ {len(paths)} local thumbnail(s) saved")
    return paths


def quick_thumbnail(source_path: str, start: float, end: float,
                    title: str, hook: str, out_dir: str, idx: int = 1,
                    log=None, plain: bool = False) -> str | None:
    """Fast single-frame pipeline thumbnail (no cv2, ~1 ffmpeg call/clip).

    Used for the automatic per-clip thumbnail during the pipeline (free tier)
    so every clip ships with a thumbnail_url without the ~6-frame
    interestingness scan. Picks a frame ~45% into the clip and overlays the
    hook (big, gold, split to ≤2 lines) + small title on a dark scrim.
    `plain=True` skips all text overlays (used when the source is already the
    rendered clip, which has captions burned in).
    """
    import subprocess
    from subtitles import _drawtext_escape

    os.makedirs(out_dir, exist_ok=True)
    dur = max(1.0, float(end) - float(start))
    t = float(start) + dur * 0.45
    draw = []
    if not plain:
        y = 1540
        for line in _split_lines(hook or title, 2)[:2]:
            draw.append(
                f"drawtext=text='{_drawtext_escape(line)}':"
                f"fontsize=58:fontcolor=#FFD400:borderw=5:bordercolor=black@0.9:"
                f"x=(w-text_w)/2:y={y}:line_spacing=6")
            y -= 84
    if not plain:
        draw.append(
            f"drawtext=text='{_drawtext_escape(title[:80])}':"
            f"fontsize=24:fontcolor=white@0.85:borderw=2:bordercolor=black@0.8:"
            f"x=(w-text_w)/2:y=h-text_h-28")
        draw.append(
            "drawbox=x=0:y=0:w=iw:h=ih:color=#0B0E1F@0.38:t=fill")
    # Automatic thumbnails are extracted from the rendered 9:16 clip. Use a
    # contain + pad transform as a final guardian so the full frame survives
    # even if a legacy/non-square-SAR input reaches this function.
    vf = ("scale=1080:1920:force_original_aspect_ratio=decrease,"
          "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1")
    if draw:
        vf += "," + ",".join(reversed(draw))
    out = os.path.join(out_dir, f"thumb_{idx}.jpg")
    r = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.2f}",
         "-i", source_path, "-frames:v", "1", "-vf", vf, out],
        capture_output=True, text=True)
    if r.returncode == 0 and os.path.exists(out):
        log and log(f"🖼️ Thumbnail clip {idx} → {os.path.basename(out)}")
        return out
    log and log(f"⚠️ thumbnail clip {idx} failed: {r.stderr[-200:]}")
    return None


def suggest_hashtags_many(clips: list, count: int = 10, log=None) -> dict:
    """Suggest hashtags for MANY clips in ONE batched Gemini call.

    Returns {list_position: [hashtags, ...]}. One call for the whole batch
    (~1-2s) instead of one call per clip — keeps the pipeline fast. Never
    raises: best-effort, returns {} on any failure.
    """
    from google import genai
    from google.genai import types

    key = os.environ.get("GEMINI_API_KEY", "")
    if not key or not clips:
        return {}
    rows = "\n".join(
        f"{i}. title='{(c.get('title') or '')[:100]}' "
        f"hook='{(c.get('hook') or '')[:120]}'"
        for i, c in enumerate(clips))
    prompt = (
        "You are a social media hashtag expert for Indonesian creators. "
        f"For EACH clip below suggest {count} hashtags (mix: 1-2 trending/"
        "general tags, 3-6 content tags, 1-2 Indonesian-language tags, "
        "1 platform tag like shortstok/reels/viral). "
        "Return STRICT JSON: "
        '{"clips": [{"idx": <number>, "hashtags": ["#...", ...]}, ...]} '
        "- no markdown, one entry per clip, use the idx numbers given.\n\n"
        f"CLIPS:\n{rows}")
    # Hashtags are an optional convenience, never a reason to keep an
    # otherwise completed video render alive.  The SDK timeout is expressed
    # in milliseconds.
    timeout_ms = max(1000, int(os.environ.get(
        "GEMINI_HASHTAG_TIMEOUT_MS", "30000")))
    client = genai.Client(
        api_key=key, http_options=types.HttpOptions(timeout=timeout_ms))
    log and log("#️⃣ Generating hashtags for all clips (1 batched call)…")
    try:
        resp = client.models.generate_content(
            model=os.environ.get("GEMINI_MODEL", "gemini-flash-latest"),
            contents=[prompt])
        data = _json_or_none(resp.text)
    except Exception as e:  # noqa: BLE001
        log and log(f"⚠️ Hashtag batch failed: {e}")
        return {}
    out = {}
    for item in (data or {}).get("clips") or []:
        try:
            pos = int(item.get("idx"))
        except (TypeError, ValueError):
            continue
        if pos < 0 or pos >= len(clips):
            continue
        tags = []
        for t in item.get("hashtags") or []:
            t = str(t).strip().strip("#")
            if t and t not in tags:
                tags.append(f"#{t[:30]}")
        if tags:
            out[pos] = tags[:count]
    log and log(f"✅ Hashtags ready for {len(out)}/{len(clips)} clips")
    return out


def _split_lines(text: str, max_lines: int = 3) -> list:
    words = text.split()
    if not words:
        return [""]
    lines, cur = [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if len(trial) > 26 and cur:
            lines.append(cur)
            cur = w
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines[:max_lines]


def suggest_hashtags(title: str, hook: str, count: int = 8, log=None) -> list:
    """Suggest viral hashtags for a clip (cheap text call)."""
    from google import genai

    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        return []

    prompt = ("You are a social media hashtag expert for Indonesian creators. "
              f"Suggest {count} hashtags for this Shorts clip. Mix: 1 trending/"
              "general tag, 2-3 content tags, 1-2 Indonesian-language tags, "
              "1 platform tag (shortstok, reels, viral). Return STRICT JSON: "
              '{"hashtags": ["#...", ...]} - no markdown.\n\n'
              f"CLIP TITLE: {title}\nHOOK: {hook}")

    client = genai.Client(api_key=key)
    log and log("#️⃣ Suggesting hashtags...")
    resp = client.models.generate_content(
        model=os.environ.get("GEMINI_MODEL", "gemini-flash-latest"),
        contents=[prompt])
    data = _json_or_none(resp.text)
    tags = (data or {}).get("hashtags") or []
    clean = []
    for t in tags:
        t = str(t).strip().strip('#')
        if t and t not in clean:
            clean.append(f"#{t[:30]}")
    return clean[:count]


def run_thumbnail(job_id: str, clip_idx: int, count: int,
                  extra_prompt: str = "", out_root: str = "", sink=None,
                  thumbnail_format: str = "shorts",
                  include_text: bool = True) -> dict:
    """Job entrypoint: generate thumbnails for one clip of a project.

    Prefers the Gemini image model; falls back to local ffmpeg frames when
    the image model is not available on the free tier. Also suggests titles
    and hashtags.

    Returns {clip_idx, thumbnails: [url, ...], titles: [...], hashtags: [...]}.
    """
    from pathlib import Path

    # Klipin projects target Shorts/Reels/TikTok. Keep legacy callers from
    # reviving the old landscape thumbnail path.
    thumbnail_format = "shorts"
    clip_dir = Path(out_root) / job_id / "thumbnails"
    clip_dir.mkdir(parents=True, exist_ok=True)

    project = _get_project(job_id)
    clips = project.get("clips") or []
    if clip_idx < 0 or clip_idx >= len(clips):
        raise RuntimeError(f"Clip index out of range: {clip_idx}")
    clip = clips[clip_idx]

    title = clip.get("title") or f"Clip {clip_idx + 1}"
    hook = clip.get("hook") or ""
    log = sink or (lambda m: None)
    auto_hook = suggest_thumbnail_hook(
        title, hook, project.get("transcript"),
        float(clip.get("start", 0)), float(clip.get("end", 60)), log=log)
    project_dir = os.path.join(out_root, job_id)
    rendered_path = os.path.join(project_dir, f"clip_{clip_idx + 1}.mp4")
    clean_vertical_path = os.path.join(
        project_dir, f"vertical_{clip_idx + 1}.mp4")
    # Use the portrait reframe from before subtitles were burned in. The
    # thumbnail must show only the AI auto-hook, never both subtitles and a
    # second competing headline.
    source_path = (clean_vertical_path if os.path.exists(clean_vertical_path)
                   else rendered_path)
    source_start = 0.0
    source_end = max(1.0, float(clip.get("end", 60)) - float(clip.get("start", 0)))
    canvas = (1080, 1920)
    if not os.path.exists(source_path):
        source_path = os.path.join(project_dir, "source.mp4")
        source_start = float(clip.get("start", 0))
        source_end = float(clip.get("end", 60))

    paths = []
    prefix = f"thumb_{thumbnail_format}_autohook"
    paths = _ffmpeg_thumbnails(
        source_path, source_start, source_end, "", auto_hook,
        str(clip_dir), count, log=log, frame_mode="crop",
        include_text=bool(include_text), filename_prefix=prefix, canvas=canvas)
    urls = [f"/videos/{job_id}/thumbnails/{os.path.basename(p)}"
            for p in sorted(paths)]

    # Titles + hashtags use the (free) Gemini TEXT model — not the paid
    # image model — so run them regardless of whether thumbnails came from
    # Gemini or the local ffmpeg fallback.
    titles = []
    hashtags = []
    try:
        titles = suggest_titles(title, hook, project.get("transcript"),
                                log=log)
    except Exception as e:  # noqa: BLE001
        log(f"⚠️ Title suggestions skipped: {e}")
    try:
        hashtags = suggest_hashtags(title, hook, log=log)
    except Exception as e:  # noqa: BLE001
        log(f"⚠️ Hashtag suggestions skipped: {e}")

    return {"clip_idx": clip_idx, "thumbnails": urls,
            "thumbnail_format": thumbnail_format, "auto_hook": auto_hook,
            "titles": titles, "hashtags": hashtags}


def _get_project(job_id: str):
    import db
    project = db.get_project(job_id)
    if not project:
        raise RuntimeError("Project not found")
    return project
