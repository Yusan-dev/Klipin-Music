"""Klipin pipeline: source -> transcription -> viral clips.

Single mode: Gemini picks 3-15 isolated viral moments from the transcript
plus sampled frames, then each is extracted, cropped vertical and burned
with captions.
"""
import json
import os
import shutil
import time
from pathlib import Path

from analyze import select_clips
from clip_selection import rank_clip_candidates, select_output_candidates
from reframe import clip_scene_plan, reframe_clip, reframe_scenes
from subtitles import build_ass, burn_subtitles
from transcript_resolver import file_media_id, resolve_transcript
from translate import translate_segments
from video import (apply_zoom, crop_video, download_url, resolve_url_metadata,
                   extract_clip, get_duration, get_resolution,
                   has_square_pixels, scale_clip)
from fused_render import eligible as fused_render_eligible, render as fused_render

# Cost model (approx, USD per unit — displayed to the user as Rp + USD).
# Groq whisper-large-v3 ~ $0.03/hr audio; Gemini flash ~ $0.15/M input tokens.
GROQ_PER_SEC = 0.0000083       # ≈ $0.03 / 3600s
GEMINI_PER_TOKEN = 0.00000015  # ≈ $0.15 / 1M tokens
FFMPEG_PER_SEC = 0.0000008     # negligible CPU, still counted for transparency

# Default TTS voice for the optional opening-caption narration.
DEFAULT_HOOK_VOICE = "id-ID-ArdiNeural"


def clip_quality_breakdown(clip: dict) -> dict:
    """Explain the aggregate viral score with stable, bounded sub-scores."""
    hook = max(0, min(100, int(clip.get("hook_score") or 0)))
    density = max(0, min(100, int(clip.get("speech_density") or 50)))
    duration = max(0.0, float(clip.get("end", 0)) - float(clip.get("start", 0)))
    hook_text = str(clip.get("hook") or "")
    reason = str(clip.get("reason") or "")
    return {
        "hook": hook,
        "context": max(0, min(100, round(45 + density * 0.45))),
        "payoff": max(0, min(100, round(
            40 + (25 if reason else 0) + min(25, duration / 3)))),
        "emotion": max(0, min(100, round(
            hook * 0.65 + (20 if any(c in hook_text for c in "!?") else 8)))),
        "technical": max(0, min(100, round(55 + density * 0.35))),
    }


class Logger:
    def __init__(self, sink):
        self._sink = sink

    def __call__(self, msg: str):
        if self._sink is not None:
            self._sink(msg)


def attach_clip_assets(source_path: str, work_dir: str, clips: list,
                       log=None) -> list:
    """Attach per-clip assets after all clips are rendered (best-effort).

    - thumbnail_url: ONE free ffmpeg thumbnail per clip (fast, ~1 call/clip)
    - hashtags: ONE batched Gemini call for the whole batch (8-12 tags/clip)

    Never fails the job — any failure just skips that asset.
    """
    from thumbnail import quick_thumbnail, suggest_hashtags_many

    thumb_dir = os.path.join(work_dir, "thumbnails")
    base = os.path.basename(work_dir)
    import concurrent.futures
    import threading

    def _thumb(i, clip):
        # Prefer the RENDERED clip so the thumbnail shows exactly what the
        # viewer sees (9:16 crop + burned captions), not a raw source frame.
        rendered = os.path.join(work_dir, f"clip_{i + 1}.mp4")
        if os.path.exists(rendered):
            return quick_thumbnail(
                rendered, 0, max(1.0, float(clip.get("end", 60)) -
                                 float(clip.get("start", 0))),
                "", "", thumb_dir, idx=i + 1, plain=True, log=None)
        return quick_thumbnail(
            source_path, clip.get("start", 0), clip.get("end", 60),
            clip.get("title", f"Clip {i + 1}"), clip.get("hook", ""),
            thumb_dir, idx=i + 1, log=None)

    # The Gemini request is deliberately isolated in a daemon thread.  Even if
    # an SDK/network regression ignores its own HTTP timeout, optional hashtag
    # generation cannot hold the completed render until Modal's deadline.
    tags_box: dict = {}

    def _hashtags():
        try:
            tags_box["value"] = suggest_hashtags_many(clips, log=log)
        except Exception as exc:  # noqa: BLE001
            tags_box["error"] = exc

    hashtags_thread = threading.Thread(
        target=_hashtags, name="optional-hashtags", daemon=True)
    hashtags_thread.start()

    # Thumbnails are local ffmpeg calls and remain parallelized normally.
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        future_map = {pool.submit(_thumb, i, c): i for i, c in enumerate(clips)}
        for fut in concurrent.futures.as_completed(future_map):
            i = future_map[fut]
            try:
                thumb = fut.result()
                if thumb:
                    clips[i]["thumbnail_url"] = (
                        f"/videos/{base}/thumbnails/{os.path.basename(thumb)}")
            except Exception as e:  # noqa: BLE001
                log and log(f"⚠️ thumb {i + 1} failed: {e}")
    hashtag_wait_sec = max(1.0, float(os.environ.get(
        "HASHTAG_ASSET_WAIT_SEC", "45")))
    hashtags_thread.join(timeout=hashtag_wait_sec)
    if hashtags_thread.is_alive():
        log and log(
            f"⚠️ Auto hashtags skipped after {hashtag_wait_sec:g}s timeout")
    elif tags_box.get("error"):
        log and log(f"⚠️ Auto hashtags skipped: {tags_box['error']}")
    else:
        tags = tags_box.get("value") or {}
        for i, clip in enumerate(clips):
            if i in tags:
                clip["hashtags"] = tags[i]
    return clips


def _estimate_cost(audio_secs: float, token_estimate: int,
                   rendered_secs: float) -> dict:
    asr = audio_secs * GROQ_PER_SEC
    gemini = token_estimate * GEMINI_PER_TOKEN
    ffmpeg = rendered_secs * FFMPEG_PER_SEC
    total = asr + gemini + ffmpeg
    return {
        "asr_usd": round(asr, 6),
        "gemini_usd": round(gemini, 6),
        "render_usd": round(ffmpeg, 6),
        "total_usd": round(total, 6),
        "total_idr": round(total * 16500),  # ~Rp per USD, shown for transparency
    }


def run_pipeline(source: dict, work_dir: str, options: dict | None = None,
                 sink=None, checkpoint=None) -> dict:
    """Execute the full pipeline. `source` = {"type": "url"|"youtube"|"upload", ...}.

    `url` accepts YouTube, TikTok, and Instagram links (platform detected from
    the URL). `youtube` is kept for backwards compatibility.
    """
    options = options or {}
    log = Logger(sink)
    os.makedirs(work_dir, exist_ok=True)
    pipeline_started = time.perf_counter()
    metrics = {}
    resolved = None
    info = None
    from gpu import compute_backend
    backend = compute_backend()
    metrics["compute_backend"] = backend
    log(f"🖥️ compute backend: {backend} "
        f"({'NVENC' if backend == 'modal-l4' else 'libx264 CPU'})")

    # 1. Resolve metadata only. URL jobs deliberately postpone the large video
    # download until transcript analysis has selected useful timestamps.
    stage_started = time.perf_counter()
    if source.get("type") in ("youtube", "url"):
        resolved = resolve_url_metadata(
            source["url"], platform=source.get("platform", ""))
        info = resolved["info"]
        platform = resolved["platform"]
        thumbnails = sorted(
            info.get("thumbnails") or [],
            key=lambda item: item.get("preference", -999), reverse=True,
        )
        thumb = (thumbnails[0].get("url") if thumbnails else None) or info.get("thumbnail")
        video_path = ""
        source_meta = {
            "title": info.get("title", "Unknown"),
            "thumbnail": thumb,
            "duration": float(info.get("duration") or 0),
            "channel": info.get("channel") or info.get("uploader", "Unknown"),
            "platform": platform,
            "view_count": info.get("view_count", 0),
            "url": source["url"],
        }
        media_id = ":".join(str(part) for part in (
            info.get("id") or source["url"],
            info.get("duration") or 0,
            info.get("modified_date") or info.get("upload_date") or "",
        ))
    else:
        video_path = source["path"]
        if not video_path or not os.path.exists(video_path):
            raise RuntimeError("Uploaded video file missing")
        dst = os.path.join(work_dir, "source.mp4")
        if os.path.abspath(video_path) != os.path.abspath(dst):
            shutil.copy2(video_path, dst)
        video_path = dst
        source_meta = {
            "title": "Uploaded video",
            "thumbnail": None,
            "duration": get_duration(video_path),
            "channel": "Local upload",
            "platform": "upload",
            "view_count": 0,
            "url": "",
        }
        platform = "upload"
        if source.get("type") == "url_relay":
            # Relayed provider video: the host already resolved subtitle
            # tracks (subtitles/automatic_captions with json3 URLs). Wiring
            # them through lets the worker use native captions in seconds
            # instead of Groq-transcribing 72 minutes of audio (~80s + API
            # cost). Falls back to Groq when tracks are absent/expired.
            relay_info = (source.get("resolved") or {}).get("info") or {}
            if relay_info.get("subtitles") or relay_info.get("automatic_captions"):
                info = relay_info
                platform = str(
                    (source.get("resolved") or {}).get("platform")
                    or source.get("platform") or "youtube")
                source_meta.update({
                    "platform": platform,
                    "url": str(source.get("url") or ""),
                })
        media_id = file_media_id(video_path)
    metrics["metadata_resolve_sec"] = round(time.perf_counter() - stage_started, 3)

    # Uploaded files have no provider-hosted poster. Create the same portrait
    # processing preview used by the Modal path before exposing source_meta.
    if not source_meta.get("thumbnail"):
        from modal_client import _persist_processing_preview
        source_meta = _persist_processing_preview(
            work_dir, source_meta, Path(video_path))

    # Persist source metadata as soon as it's known so the in-flight
    # /api/status/{job_id} endpoint can stream the video preview card to the
    # frontend DURING processing (not just after completion).
    try:
        with open(os.path.join(work_dir, "source_meta.json"), "w",
                  encoding="utf-8") as f:
            json.dump(source_meta, f)
    except OSError:
        pass  # best-effort; the preview card just stays hidden

    # Free-tier duration gate: a video over 30 minutes requires a paid plan.
    # The source duration is known now (download returned it, or we probe the
    # upload). This raises before any expensive ASR/Gemini work runs.
    user_id = options.get("_user_id", "")
    if user_id:
        try:
            import billing
            if billing.enabled() and billing.is_free_tier(user_id):
                src_duration = float(source_meta.get("duration") or 0)
                if src_duration <= 0:
                    src_duration = get_duration(video_path)
                if src_duration > billing.FREE_MAX_DURATION:
                    raise RuntimeError(
                        f"Video {src_duration / 60:.1f} menit melebihi batas "
                        f"gratis {billing.FREE_MAX_DURATION // 60} menit — "
                        "langganan premium untuk video lebih panjang")
        except RuntimeError:
            raise
        except Exception:
            pass  # billing unavailable → fully local, no gate

    # 2. Resolve a trustworthy transcript: cache -> valid native captions ->
    # audio-only Groq fallback. Bad/truncated caption tracks never reach render.
    stage_started = time.perf_counter()
    log("🎙️ STAGE 2/6: Resolving validated transcript...")
    transcript, transcript_info = resolve_transcript(
        source=platform,
        media_id=media_id,
        duration=float(source_meta.get("duration") or 0),
        language_hint=str(source.get("language_hint") or ""),
        info=info,
        video_path=video_path,
        source_url=source.get("url", ""),
        work_dir=work_dir,
        platform=platform,
        log=log,
    )
    metrics["transcript_resolve_sec"] = round(time.perf_counter() - stage_started, 3)
    metrics["transcript_source"] = transcript_info.get("source")
    metrics["transcript_cache_hit"] = bool(transcript_info.get("cache_hit"))
    segs = transcript.get("segments") or []
    log(f"📄 STAGE 3/6: Transcript ready — {len(segs)} segments")
    # Show subtitle timestamps in the console (subtitle sync at a glance).
    for s in segs[:40]:
        text = (s.get("text") or "").strip()
        log(f"   ⏱️ [{float(s.get('start', 0)):.2f}s → "
            f"{float(s.get('end', s.get('start', 0))):.2f}s] {text[:90]}")
    if len(segs) > 40:
        log(f"   … +{len(segs) - 40} segmen lagi")

    # 2b. Optional subtitle translation (Gemini batch, cheap)
    translate_to = (options.get("translate_to") or "").strip().lower()
    if translate_to and translate_to != "original":
        log(f"🌐 Translating subtitles to {translate_to}...")
        transcript = translate_segments(transcript, translate_to, log)

    # 3. Transcript-only clip analysis. No source frames, face detector or GPU
    # are touched before the useful ranges are known.
    stage_started = time.perf_counter()
    log("🧠 STAGE 4/6: Transcript-first viral analysis...")
    # API injects the account entitlement. Clamp again as defense-in-depth and
    # pass it into analysis so six-output jobs build a sufficiently wide
    # candidate pool before duplicate and quality filtering.
    render_limit = max(1, min(6, int(options.get("render_count") or 3)))
    clips = select_clips(None, transcript, work_dir, log,
                         custom_prompt=options.get("custom_prompt", ""),
                         clip_length=options.get("clip_length", "cerita"),
                         video_duration=float(source_meta.get("duration") or 0),
                         target_count=render_limit)
    metrics["llm_analysis_sec"] = round(time.perf_counter() - stage_started, 3)
    log(f"🎯 Selected {len(clips)} viral clips")

    # Prioritize clips above the product quality bar, then fill the requested
    # output count with the best remaining unique candidates. Scores remain
    # honest; a paid six-output choice must not silently become one.
    requested_threshold = int(options.get("min_output_score") or
                              os.environ.get("MIN_OUTPUT_SCORE", "90"))
    quality_threshold = max(90, min(99, requested_threshold))
    clips, _low_confidence = rank_clip_candidates(clips, quality_threshold + 1)
    ranked_count = len(clips)
    # Snapshot the full ranked pool before slicing to the render limit.
    # `result["candidates"]` below exposes this pool for audit/debug.
    candidates = list(clips)
    if not ranked_count:
        log("⚠️ Analisis tidak menghasilkan kandidat klip")
        return {"status": "no_viral_content", "clips": [],
                "mode": "viral",
                "message": ("Tidak ada kandidat klip yang dapat dirender. "
                            "Kredit tidak dipotong; coba video atau kriteria lain."),
                "transcript": transcript, "metrics": metrics}

    clips, fallback_count = select_output_candidates(
        clips, render_limit, quality_threshold)
    if fallback_count:
        log(f"ℹ️ {fallback_count} slot diisi kandidat unik terbaik dengan score "
            f"≤{quality_threshold}; skor asli tetap ditampilkan")
    metrics["candidate_count"] = ranked_count
    metrics["output_clip_count"] = len(clips)
    metrics["quality_fallback_count"] = fallback_count
    log(f"🎯 {ranked_count} ranked candidates; rendering top {len(clips)}")

    # 4. Fetch the visual source only after timestamps are known. Upload jobs
    # already have their source locally; URL jobs now avoid blocking transcript
    # analysis on a potentially multi-gigabyte download.
    stage_started = time.perf_counter()
    if not video_path:
        log("⬇️ STAGE 5/6: Fetching source after range selection...")
        source_info = download_url(
            source["url"], work_dir, log, platform=platform, resolved=resolved)
        video_path = source_info["path"]
    metrics["media_fetch_sec"] = round(time.perf_counter() - stage_started, 3)

    # Layout-aware moment finding: an explicit layout is a promise about what
    # is on screen (split = two people sharing the frame, face = a visible
    # subject, gameplay = no talking head to crop). Transcript-only selection
    # cannot see that, so re-rank the chosen candidates by measured visual
    # fit — a split request prefers ranges where two live people actually
    # coexist instead of a solo monologue. Reorder only, never drop: the
    # render count contract is untouched.
    _fit_layout = str(options.get("layout") or options.get("crop")
                      or "auto").strip().lower()
    if video_path and _fit_layout in ("split", "panel", "alternate",
                                      "face", "gameplay"):
        _layout_fit_started = time.perf_counter()
        try:
            from video import layout_fit_score
            from clip_selection import order_by_layout_fit
            _fits = [layout_fit_score(
                video_path, float(c.get("start", 0)), float(c.get("end", 0)),
                _fit_layout, log=log) for c in clips]
            clips, _order = order_by_layout_fit(clips, _fits)
            if any(_order[i] != i for i in range(len(clips))):
                log("📐 Layout-fit reorder ({}): {}".format(
                    _fit_layout,
                    ", ".join(f"clip{i + 1}:{_fits[i]:.0%}"
                              for i in _order)))
            metrics["layout_fit"] = [_fits[i] for i in _order]
        except Exception as e:  # noqa: BLE001 — fail-open, keep viral order
            log(f"⚠️ layout-fit ranking skipped ({e})")
        finally:
            metrics["layout_fit_sec"] = round(
                time.perf_counter() - _layout_fit_started, 3)

    log("🎬 STAGE 6/6: Rendering selected clips...")
    crop_mode = options.get("crop") or options.get("layout") or "auto"
    aspect = options.get("aspect", "9:16")
    # Persist the effective choice so the result is auditable: users can tell
    # whether a render used explicit FACE or scene-aware AUTO, rather than
    # inferring it from the pixels after the fact.
    metrics["layout_requested"] = str(options.get("layout") or options.get("crop") or "auto")
    metrics["layout_effective"] = str(crop_mode)
    log(f"🧭 Layout: requested={metrics['layout_requested']} effective={crop_mode}")
    if aspect != "9:16" or crop_mode == "square":
        log(f"⚠️ {aspect}/{crop_mode} is disabled for Shorts/Reels — forcing 9:16 portrait")
        aspect = "9:16"
        if crop_mode == "square":
            crop_mode = "general"
    anim = options.get("animation", "karaoke")
    use_emoji = bool(options.get("emoji", True))
    caption_color = options.get("color")  # None -> default white
    caption_font = options.get("caption_font", "Montserrat")
    caption_size = int(options.get("caption_size", 84))
    caption_style = options.get("caption_style", "solid")
    watermark = options.get("watermark", "")
    title_overlay = options.get("title_overlay", "")

    # Free-tier watermark: show 'Klip-in' branding for users who haven't
    # purchased credits (free trial or Mulai pack without explicit opt-out).
    # The toggle 'show_klipin_watermark' lets paid users opt in/out.
    user_id = options.get("_user_id", "")
    show_klipin = options.get("show_klipin_watermark", True)
    is_free_tier = False
    if show_klipin and user_id:
        try:
            import billing
            if billing.enabled():
                is_free_tier = billing.requires_klipin_watermark(user_id)
        except Exception:
            pass  # billing unavailable → no watermark (local dev mode)
    elif not os.environ.get("BILLING_ENABLED"):
        is_free_tier = False  # billing disabled → fully free local mode, no watermark

    ass_files = build_ass(transcript, clips, work_dir,
                              animation=anim, color=caption_color,
                              karaoke_color=options.get("karaoke_color"),
                              emoji=use_emoji, font=caption_font,
                              size=caption_size, style=caption_style,
                              bg=options.get("caption_bg", "solid"),
                              bg_color=options.get("caption_bg_color"),
                              log=log,
                              emoji_lang=options.get("translate_to", ""),
                              words_per_line=options.get("words_per_line", 3),
                              enabled=bool(options.get("subtitles_enabled", True)),
                              position_y=options.get("caption_position_y", 73))

    results = []
    vision_seconds = 0.0
    precut_lock = None
    try:
        import threading as _prec_threading
        precut_lock = _prec_threading.Lock()
        precut_total = [0.0]
    except Exception:
        precut_total = [0.0]
    render_started = time.perf_counter()
    def _render_one(item):
        i, clip = item
        progress = int((i / len(clips)) * 100)
        log(f"✂️ Part {i + 1}/{len(clips)} ({progress}%): {clip['start']:.0f}s-{clip['end']:.0f}s")
        vision_seconds_local = 0.0
        clip_work_dir = os.path.join(work_dir, f".render_{i + 1}")
        os.makedirs(clip_work_dir, exist_ok=True)
        raw = os.path.join(work_dir, f"raw_{i + 1}.mp4")
        cropped = os.path.join(work_dir, f"vertical_{i + 1}.mp4")
        final = os.path.join(work_dir, f"clip_{i + 1}.mp4")

        # Fuse only the static CENTER path. Split/track/scene-aware output
        # retains its existing anti-duplicate composition and guards.
        hook_text = (clip.get("hook") or "").strip()
        opening_enabled = bool(options.get("hook_overlay") and hook_text)
        narrated_opening = bool(opening_enabled and options.get("hook_voiceover"))
        if fused_render_eligible(
                crop_mode, aspect, options, watermark=watermark,
                title_overlay=(title_overlay if options.get("title_enabled", True) else ""),
                hook_overlay=opening_enabled, hook_v2=clip.get("hook_v2"),
                narrated_opening=narrated_opening, bgm=options.get("bgm") or ""):
            fused_render(video_path, clip["start"], clip["end"], ass_files[i], final, log)
            if aspect == "9:16" and (get_resolution(final) != (1080, 1920)
                                     or not has_square_pixels(final)):
                raise RuntimeError("LAYOUT_LOCK_VIOLATION: fused render did not produce 1080x1920 SAR 1:1")
            rendered_clip = {
                "index": i, "start": clip["start"], "end": clip["end"],
                "title": clip.get("title", f"Clip {i + 1}"),
                "hook": clip.get("hook", ""), "hook_score": clip.get("hook_score", 0),
                "predicted_score": clip.get("predicted_score", clip.get("hook_score", 0)),
                "reason": clip.get("reason", ""), "topic": clip.get("topic", ""),
                "speech_density": clip.get("speech_density", 50),
                "quality_breakdown": clip_quality_breakdown(clip),
                "layout_plan": [{"start": 0.0, "end": round(float(clip["end"] - clip["start"]), 2), "layout": "center"}],
                "layout_mode": crop_mode, "layout_prior": "user",
                "video_url": f"/videos/{os.path.basename(work_dir)}/clip_{i + 1}.mp4",
                "subtitle_url": f"/videos/{os.path.basename(work_dir)}/clip_{i + 1}.ass",
                "size_mb": round(os.path.getsize(final) / 1e6, 1),
            }
            shutil.rmtree(clip_work_dir, ignore_errors=True)
            return rendered_clip, 0.0

        precut_started = time.perf_counter()
        extract_clip(video_path, clip["start"], clip["end"], raw, log,
                     fast=True)
        precut_delta = time.perf_counter() - precut_started
        if precut_lock is not None:
            with precut_lock:
                precut_total[0] += precut_delta
        else:
            precut_total[0] += precut_delta
        clip_layout_plan = []
        detected_genre = ""
        layout_prior = "none"
        if crop_mode != "auto":
            # Explicit user choice is deterministic for the whole clip.
            clip_layout_plan = [{"start": 0.0, "end": get_duration(raw),
                                 "strategy": crop_mode}]
            layout_prior = "user"
        if crop_mode == "auto":
            vision_started = time.perf_counter()
            try:
                clip_segments = [
                    segment for segment in segs
                    if float(segment.get("end", 0) or 0) > clip["start"]
                    and float(segment.get("start", 0) or 0) < clip["end"]
                ]
                transcript_snippet = " ".join(
                    str(segment.get("text") or "") for segment in clip_segments[:20])
                if options.get("genre_detect", True):
                    from genre import detect_genre
                    genre_result = detect_genre(
                        raw, clip_work_dir, log=log,
                        transcript_snippet=transcript_snippet)
                    detected_genre = str(genre_result.get("genre") or "")
                if options.get("layout_picker", True):
                    from layout_picker import pick_layout
                    layout_prior = pick_layout(
                        raw, clip_work_dir, log,
                        title=clip.get("title", ""), hook=clip.get("hook", ""),
                        force=True)
                # Portrait inputs still need speaker-aware composition.  The
                # old shortcut merely upscaled them and left the camera fixed
                # at frame centre, bypassing the existing 9:16 face-follow
                # renderer entirely.
                clip_layout_plan = clip_scene_plan(
                    raw, log=log, genre=detected_genre,
                    layout_prior=layout_prior)
                if clip_layout_plan:
                    log("🎛️ AUTO layout: " + ", ".join(
                        f"{scene['start']:.1f}-{scene['end']:.1f}s={scene['strategy']}"
                        for scene in clip_layout_plan))
            except Exception as exc:  # noqa: BLE001 - safe TRACK fallback
                log(f"⚠️ Dynamic layout analysis skipped: {exc}")
            finally:
                vision_seconds_local += time.perf_counter() - vision_started
        if crop_mode == "face":
            # FACE is an explicit visual contract: fill the 9:16 canvas with
            # a real portrait crop around the detected subject.  Do not send
            # it through the V2 AUTO picker, which may legitimately choose a
            # wide/general/stack composition and thereby ignore the user's
            # selection. reframe_face keeps the contract per camera cut so a
            # multi-shot debate does not inherit one static window.
            from reframe import reframe_face
            reframe_face(raw, cropped, aspect=aspect, log=log,
                         work_dir=clip_work_dir)
        elif crop_mode in ("auto", "track"):
            strategy = "track"
            crop_ratio = 1.0
            track_mode = "aggressive"
            # Composited punch-in: when auto_zoom is enabled, pass the
            # zoom value to reframe_clip so zoompan is composed into the
            # same filtergraph (single ffmpeg pass) instead of a separate
            # apply_zoom pass. The UI label is "Punch-in ala Submagic"
            # so auto_zoom IS the punch-in feature.
            punch_zoom_val = 0.0
            if options.get("auto_zoom"):
                punch_zoom_val = float(options.get("zoom_strength", 1.15))
            try:
                # REFRAME_V2 feature flag: when enabled, use the new Opus Clip-level
                # dynamic reframing pipeline (per-frame tracking + smart subject
                # selection + Kalman smoothing). Falls back to old pipeline on error.
                # Layout Guardian: Smart-Locking for 9:16 parity (no melebar/crop issues)
                def validate_layout(w, h, aspect):
                    if aspect == "9:16" and (w != 1080 or h != 1920):
                        raise RuntimeError(f"LAYOUT_LOCK_VIOLATION: target 9:16 (1080x1920) but actual {w}x{h}")
                
                use_v2 = os.environ.get("REFRAME_V2", "0") == "1"
                if crop_mode == "auto" and clip_layout_plan:
                    # AUTO is truly per-scene: talking head, multiple speakers,
                    # screen/slide and wide shots may use different renderers
                    # inside the same selected clip.
                    reframe_scenes(
                        raw, cropped, aspect=aspect, plan=clip_layout_plan,
                        clip_start=0.0, clip_end=get_duration(raw),
                        log=log, work_dir=clip_work_dir,
                        punch_zoom=punch_zoom_val,
                        gameplay_ratio=float(options.get("gameplay_ratio", 0.60)),
                    )
                elif use_v2:

                    from reframe_v2 import reframe_clip_v2
                    # The legacy 3-stack (sharp landscape band between two
                    # blurred bands) is opt-in only.  Making it the default
                    # resurrected the old stretched/letterbox visual bug even
                    # though the encoded pixel frame itself was portrait.
                    stack_opt = bool(options.get("stack_layout", False))
                    reframe_clip_v2(raw, cropped, aspect=aspect, log=log,
                                    stack=stack_opt)
                elif crop_mode == "track":
                    # Explicit talking-head: same per-cut treatment as FACE so
                    # each camera shot centres its own speaker instead of one
                    # static median window for the whole clip.
                    from reframe import reframe_face
                    reframe_face(raw, cropped, aspect=aspect, log=log,
                                 work_dir=clip_work_dir,
                                 punch_zoom=punch_zoom_val,
                                 gameplay_ratio=float(options.get("gameplay_ratio", 0.60)))
                else:
                    reframe_clip(raw, cropped, aspect=aspect, strategy=strategy,
                                 log=log, work_dir=clip_work_dir,
                                 crop_ratio=crop_ratio, track_mode=track_mode,
                                 punch_zoom=punch_zoom_val,
                                 gameplay_ratio=float(options.get("gameplay_ratio", 0.60)))
            except Exception as e:
                log(f"⚠️ reframe error — fallback center: {e}")
                crop_video(raw, cropped, mode="center", aspect=aspect, log=log)
        elif crop_mode in ("split", "panel", "alternate"):
            # Explicit two-subject layouts are per-scene preferences, not a
            # whole-clip lock: split/panel only where two live subjects are
            # verified together, FACE on solo cuts, CENTER on no-face gaps
            # (same compatibility matrix as the AUTO proofs). A whole-clip
            # median would stack people who never share the frame. Falls back
            # to the whole-clip crop when scene analysis is unavailable.
            try:
                two_plan = clip_scene_plan(
                    raw, log=log, requested_layout=crop_mode)
                if two_plan:
                    log("🎛️ Explicit {} per-scene: ".format(crop_mode.upper()) +
                        ", ".join(
                            f"{s['start']:.1f}-{s['end']:.1f}s={s['strategy']}"
                            for s in two_plan))
                    reframe_scenes(
                        raw, cropped, aspect=aspect, plan=two_plan,
                        clip_start=0.0, clip_end=get_duration(raw),
                        log=log, work_dir=clip_work_dir,
                        gameplay_ratio=float(options.get("gameplay_ratio", 0.60)))
                else:
                    raise RuntimeError("empty scene plan")
            except Exception as e:  # noqa: BLE001 — whole-clip fallback
                log(f"⚠️ per-scene {crop_mode} skipped ({e}) — whole-clip crop")
                crop_video(raw, cropped, mode=crop_mode, aspect=aspect, log=log,
                           adaptive_size=bool(options.get("adaptive_size")),
                           gameplay_face_position=options.get(
                               "gameplay_face_position", "bottom"),
                           gameplay_ratio=float(options.get("gameplay_ratio", 0.60)))
        else:
            crop_video(raw, cropped, mode=crop_mode, aspect=aspect, log=log,
                       adaptive_size=bool(options.get("adaptive_size")),
                       gameplay_face_position=options.get(
                           "gameplay_face_position", "bottom"),
                       gameplay_ratio=float(options.get("gameplay_ratio", 0.60)))
        # auto_zoom is fully composited in reframe_clip above —
        # no separate apply_zoom pass needed anymore.
        # OpenShorts: AI-powered video effects (zoom_in, color_pop, etc.)
        # Uses Gemini File API for true video understanding
        if options.get("auto_effects", False):
            try:
                from video_editor import VideoEditor
                editor = VideoEditor(os.environ.get("GEMINI_API_KEY", ""))
                # Upload video to Gemini
                video_file = editor.upload_video(cropped)
                dur = get_duration(cropped)
                # Get transcript for context
                clip_transcript = [s for s in segs
                                   if float(s.get("end", 0) or 0) > clip["start"]
                                   and float(s.get("start", 0) or 0) < clip["end"]]
                effect_result = editor.get_ffmpeg_filter(
                    video_file, dur, transcript=clip_transcript, has_captions=True
                )
                if effect_result and effect_result.get("filter_string"):
                    effected = os.path.join(work_dir, f"effected_{i + 1}.mp4")
                    from video import run
                    run([
                        "ffmpeg", "-y", "-i", cropped,
                        "-vf", effect_result["filter_string"],
                        *encode_args(log=log),
                        "-c:a", "aac", "-b:a", "128k",
                        effected,
                    ], log)
                    os.replace(effected, cropped)
                    log(f"✨ Applied {len(effect_result['edits'])} AI effects")
            except Exception as e:
                log(f"⚠️ AI effects skipped: {e}")

        # One opening caption only.  Internally the historical option keys are
        # retained for stored-job compatibility, but the product no longer
        # exposes a separate/ambiguous "Auto Hook" concept.
        hook_text = (clip.get("hook") or "").strip()
        opening_enabled = bool(options.get("hook_overlay") and hook_text)
        narrated_opening = bool(opening_enabled and options.get("hook_voiceover"))
        hook_v2 = clip.get("hook_v2") if opening_enabled else None
        if not hook_v2:
            auto_hook = (clip.get("hook") or "").strip()
            if auto_hook:
                # Background hook: user picks a preset (yellow/red/dark) or a
                # free color via hook_bg_color. Text color pairs with it so
                # contrast is always readable (dark text on light bg).
                style = options.get("hook_bg") or "color"
                if style == "color":
                    bg_hex = (options.get("hook_bg_color") or "#FFE500").lstrip("#")
                    txt_hex = (options.get("hook_text_color") or "#0B0C0E").lstrip("#")
                    try:
                        rgb = tuple(int(bg_hex[i:i + 2], 16) for i in (0, 2, 4))
                        trgb = tuple(int(txt_hex[i:i + 2], 16) for i in (0, 2, 4))
                        hook_v2 = {"enabled": True, "duration": float(options.get("hook_duration", 5) or 5),
                                   "style": {"box": rgb + (245,), "text": trgb, "outline": None, "shadow": True},
                                   "headline": auto_hook}
                    except ValueError:
                        hook_v2 = {"enabled": True, "duration": float(options.get("hook_duration", 5) or 5),
                                   "style": "yellow", "headline": auto_hook}
                else:
                    # Named preset maps straight to hooks.HOOK_STYLES keys.
                    hook_v2 = {"enabled": True,
                               "duration": float(options.get("hook_duration", 5) or 5),
                               "style": style if style in ("yellow", "red", "dark", "classic", "outline") else "yellow",
                               "headline": auto_hook}
        # When narration is enabled the caption belongs to a dedicated frozen
        # intro. Do not duplicate it over the first seconds of the main video.
        main_hook_v2 = None if narrated_opening else hook_v2
        burn_subtitles(cropped, ass_files[i], final, log, watermark=watermark,
                       title_overlay=(title_overlay
                                      if options.get("title_enabled", True) else ""),
                       title_color=options.get("title_color", "#FFFFFF"),
                       title_position_y=options.get("title_position_y", 14),
                       # hook_v2 is the single professional caption card.
                       # The legacy drawtext fallback must not render at the
                       # same time or users see two competing opening texts.
                       hook_overlay=(hook_text if opening_enabled
                                     and not narrated_opening
                                     and not main_hook_v2 else ""),
                       hook_v2=main_hook_v2,
                       free_tier=is_free_tier)

        # Optional YouTube Shorts-style opening: freeze the first portrait
        # frame, show the caption card and let AI finish reading it. Only then
        # does frame zero of the actual clip begin.
        if narrated_opening:
            try:
                from voiceover import generate_voiceover, prepend_caption_intro
                hook_voice = options.get("hook_voice") or DEFAULT_HOOK_VOICE
                hook_tone = options.get("hook_tone") or "natural"
                hook_tts = os.path.join(work_dir, f"hook_voice_{i + 1}.mp3")
                generate_voiceover(hook_text, hook_voice, hook_tts,
                                   log=log, tone=hook_tone)
                voiced = os.path.join(work_dir, f"opening_caption_{i + 1}.mp4")
                prepend_caption_intro(
                    final, hook_text, hook_tts, voiced,
                    duration=float(options.get("hook_duration", 5) or 5),
                    style=hook_v2.get("style", "yellow") if hook_v2 else "yellow",
                    log=log,
                )
                os.replace(voiced, final)
                if os.path.exists(hook_tts):
                    os.remove(hook_tts)
            except Exception as e:
                log(f"⚠️ Bacaan caption pembuka dilewati: {e}")

        # Optional background music bed (Submagic-style) under the audio
        bgm_mood = options.get("bgm") or ""
        if bgm_mood and bgm_mood != "none":
            from bgm import mix_bgm, render_bgm
            bgm_path = os.path.join(work_dir, f"bgm_{i + 1}.m4a")
            render_bgm(bgm_mood, bgm_path, duration=clip["end"] - clip["start"],
                       log=log)
            bgmed = os.path.join(work_dir, f"bgmed_{i + 1}.mp4")
            mix_bgm(final, bgm_path, bgmed,
                    volume=float(options.get("bgm_volume", 0.15)), log=log)
            os.replace(bgmed, final)
            if os.path.exists(bgm_path):
                os.remove(bgm_path)

        # Final delivery guardian. Optional effects and audio passes must
        # never reintroduce the historical landscape-output bug.
        if aspect == "9:16":
            fw, fh = get_resolution(final)
            square_pixels = has_square_pixels(final)
            if (fw, fh) != (1080, 1920) or not square_pixels:
                reason = f"{fw}x{fh}"
                if not square_pixels:
                    reason += " with non-square display pixels"
                log(f"⚠️ Portrait guardian repaired {reason} → 1080x1920 SAR 1:1")
                repaired = os.path.join(work_dir, f"portrait_{i + 1}.mp4")
                scale_clip(final, repaired, "1080", log=log)
                os.replace(repaired, final)
            fw, fh = get_resolution(final)
            if (fw, fh) != (1080, 1920) or not has_square_pixels(final):
                raise RuntimeError(
                    "LAYOUT_LOCK_VIOLATION: expected 1080x1920 SAR 1:1, "
                    f"got {fw}x{fh}")

        rendered_clip = {
            "index": i,
            "start": clip["start"],
            "end": clip["end"],
            "title": clip.get("title", f"Clip {i + 1}"),
            "hook": clip.get("hook", ""),
            "hook_score": clip.get("hook_score", 0),
            "predicted_score": clip.get("predicted_score",
                                        clip.get("hook_score", 0)),
            "reason": clip.get("reason", ""),
            "topic": clip.get("topic", ""),
            "speech_density": clip.get("speech_density", 50),
            "quality_breakdown": clip_quality_breakdown(clip),
            "layout_plan": [
                {
                    "start": round(float(sc.get("start", 0)), 2),
                    "end": round(float(sc.get("end", 0)), 2),
                    "layout": sc.get("strategy", "general"),
                }
                for sc in clip_layout_plan
            ],
            "layout_mode": crop_mode,
            "layout_prior": layout_prior,
            "video_url": f"/videos/{os.path.basename(work_dir)}/clip_{i + 1}.mp4",
            "subtitle_url": f"/videos/{os.path.basename(work_dir)}/clip_{i + 1}.ass",
            "size_mb": round(os.path.getsize(final) / 1e6, 1) if os.path.exists(final) else 0,
        }
        shutil.rmtree(clip_work_dir, ignore_errors=True)
        return rendered_clip, vision_seconds_local


    worker_default = "3" if os.environ.get("KLIPIN_GPU", "0") == "1" else "2"
    clip_workers = max(1, min(len(clips), int(os.environ.get(
        "CLIP_RENDER_WORKERS", worker_default))))
    if clip_workers == 1:
        rendered = [_render_one(item) for item in enumerate(clips)]
    else:
        from concurrent.futures import ThreadPoolExecutor
        log(f"⚡ Parallel render: {clip_workers} clip workers")
        with ThreadPoolExecutor(max_workers=clip_workers, thread_name_prefix="clip-render") as pool:
            rendered = list(pool.map(_render_one, enumerate(clips)))
    for rendered_clip, vision_delta in rendered:
        results.append(rendered_clip)
        vision_seconds += vision_delta
    metrics["vision_sec"] = round(vision_seconds, 3)
    metrics["render_sec"] = round(time.perf_counter() - render_started, 3)
    metrics["precut_sec"] = round(precut_total[0], 3)

    duration = round(get_duration(video_path), 2)
    audio_secs = (
        duration
        if not transcript_info.get("cache_hit")
        and "whisper" in str(transcript_info.get("source") or "")
        else 0.0
    )
    rendered_secs = sum(max(0.0, c["end"] - c["start"]) for c in clips)
    token_estimate = 3000
    if translate_to and translate_to != "original":
        token_estimate += min(4000, len(segs) * 80)  # translation pass
    cost = _estimate_cost(audio_secs, token_estimate, rendered_secs)
    try:
        from gpu import use_gpu
        gpu_enabled = bool(use_gpu())
    except Exception:
        gpu_enabled = False
    metrics["source_duration_sec"] = duration
    metrics["rendered_duration_sec"] = round(rendered_secs, 3)
    metrics["gpu_enabled"] = gpu_enabled
    # This is wall time spent in the visual/render stage while a GPU encoder is
    # selected. It is deliberately named estimated until provider telemetry can
    # report allocation-active seconds independently.
    metrics["gpu_active_sec_estimate"] = metrics["render_sec"] if gpu_enabled else 0.0
    metrics["total_sec"] = round(time.perf_counter() - pipeline_started, 3)

    scores = [int(c.get("hook_score") or 0) for c in results]
    avg = round(sum(scores) / len(scores)) if scores else 0
    top = max(scores) if scores else 0
    viral = sum(1 for s in scores if s >= 85)
    log(f"🎯 {len(results)} viral clips · avg score {avg} · top {top} · "
        f"{viral} 🔥 (≥85)")

    log(f"✅ Done: {len(results)} clips — "
        f"≈ Rp{cost['total_idr']:,} ({cost['total_usd']:.5f} USD)")

    result = {
        "mode": "viral",
        "compute_backend": metrics.get("compute_backend", "local-cpu"),
        "transcript": transcript,
        "clips": results,
        "candidates": candidates,
        "duration": duration,
        "cost": cost,
        "metrics": metrics,
        "transcript_source": transcript_info.get("source"),
        "source_meta": source_meta,
    }

    # Persist the valuable output before optional network-backed decoration.
    # Modal supplies this callback; local callers remain backwards compatible.
    if checkpoint:
        try:
            checkpoint(result)
            log("💾 Core render checkpoint persisted")
        except Exception as e:  # noqa: BLE001
            log(f"⚠️ Core checkpoint failed: {e}")

    # Auto assets (free tier): 1 thumbnail + 1 batched hashtag set per clip.
    # Runs in parallel AFTER Done is printed so the console feels complete
    # while thumbnails/hashtags finish in the background.
    try:
        attach_clip_assets(video_path, work_dir, results, log)
    except Exception as e:  # noqa: BLE001
        log(f"⚠️ clip assets skipped: {e}")

    return result


