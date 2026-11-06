"""Re-render & export operations for Klipin.

run_retrim: re-cut a single clip with new start/end (and optional caption
            style overrides) using the stored source video + transcript.
run_export: bundle selected clips into a ZIP with optional resolution
            re-encode and a manifest.json.
"""
import json
import os
import time
import zipfile

import db
from analyze import select_clips
from pipeline import attach_clip_assets
from subtitles import (build_ass, burn_subtitles, clip_caption_blocks,
                       sanitize_caption_text)
from hooks import render_hook_v2_intro
from video import (apply_zoom, extract_clip, crop_video, get_duration,
                   get_resolution, has_square_pixels,
                   scale_clip)  # noqa: E501


def _is_free_tier(user_id: str = "", show_klipin: bool = True) -> bool:
    """Return True if user is on the free tier AND watermark toggle is on.
    Used to burn 'Klip-in' branding watermark on free/Mulai plan clips.
    """
    if not show_klipin or not user_id:
        return False
    try:
        import billing
        if billing.enabled():
            return billing.requires_klipin_watermark(user_id)
    except Exception:
        pass
    return False


def _subtitle_editor_options(options: dict) -> dict:
    return {
        "enabled": bool(options.get("subtitles_enabled", True)),
        "position_y": options.get("caption_position_y", 73),
    }


def _title_editor_options(options: dict) -> dict:
    return {
        "title_overlay": (options.get("title_overlay", "")
                          if options.get("title_enabled", True) else ""),
        "title_color": options.get("title_color", "#FFFFFF"),
        "title_position_y": options.get("title_position_y", 14),
    }




def _portrait_guardian(final: str, work_dir: str, tag: str,
                       aspect: str = "9:16", sink=None) -> None:
    """Delivery guardian for every edit path — identical contract to pipeline."""
    if aspect != "9:16":
        return
    fw, fh = get_resolution(final)
    square = has_square_pixels(final)
    if (fw, fh) != (1080, 1920) or not square:
        reason = f"{fw}x{fh}"
        if not square:
            reason += " with non-square display pixels"
        sink and sink(f"⚠️ Portrait guardian repaired {reason} → 1080x1920 SAR 1:1")
        repaired = os.path.join(work_dir, f"guardian_{tag}.mp4")
        # Re-crop to 9:16 center (no blur-fill band). The historical bug was
        # a landscape middle band, so a stretch/blur repair would leave a thin
        # perceptible band. center crop keeps the real, full-height subject..
        try:
            crop_video(final, repaired, mode="center", aspect="9:16", log=sink)
        except Exception:
            scale_clip(final, repaired, "1080", sink)
        os.replace(repaired, final)
    fw, fh = get_resolution(final)
    if (fw, fh) != (1080, 1920) or not has_square_pixels(final):
        raise RuntimeError(
            "LAYOUT_LOCK_VIOLATION: expected 1080x1920 SAR 1:1, "
            f"got {fw}x{fh}")


def _extract_clip_with_segments(source: str, clip: dict, raw: str, sink,
                               work_dir: str) -> str:
    """Extract a single clip, honoring keep_segments (dead-air trim).

    Falls back to a plain extract_clip when no keep_segments are stored —
    preserving the original single-shot path for backward compat.
    """
    segs = clip.get("keep_segments") or []
    if not segs:
        extract_clip(source, clip["start"], clip["end"], raw, sink,
                     fast=True)
        return raw
    # One raw_N.mp4 per segment, then concat (decode-safe, same reframe).
    seg_files = []
    for j, seg in enumerate(segs):
        sf = os.path.join(work_dir, f"seg_{os.path.basename(raw)[:-4]}_{j}.mp4")
        extract_clip(source, seg["start"], seg["end"], sf, sink)
        seg_files.append(sf)
    # Concat — use the concat demuxer (no re-encode, preserves VFR).
    list_file = os.path.join(work_dir, f"concat_{os.path.basename(raw)[:-4]}.txt")
    with open(list_file, "w", encoding="utf-8") as lf:
        for sf in seg_files:
            lf.write("file '" + sf + "'\n")
    from video import run
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat",
           "-safe", "0", "-i", list_file, "-c", "copy", "-vsync", "vfr", raw]
    run(cmd, sink)
    for sf in seg_files:
        if os.path.exists(sf):
            os.remove(sf)
    if os.path.exists(list_file):
        os.remove(list_file)
    return raw


def _clip_basename(idx: int) -> str:
    return f"clip_{idx + 1}.mp4"


def _render_crop(raw: str, cropped: str, options: dict, aspect: str,
                 sink, work_dir: str) -> str:
    """Crop/re-frame `raw` into `cropped`, mirroring the pipeline.

    AUTO/TRACK use scene-aware reframing. Explicit FACE always uses a real
    portrait face crop and can never be replaced by the AUTO picker.

    Genre-aware: when options contains _detected_genre, the reframe strategy
    is tuned for the genre (e.g. sports/gaming → wider crop, no face track).
    """
    if aspect != "9:16":
        sink and sink(f"⚠️ legacy {aspect} request overridden → 9:16")
        aspect = "9:16"
    mode = options.get("layout") or options.get("crop") or "auto"
    if mode == "square":
        sink and sink("⚠️ square layout overridden → portrait GENERAL")
        mode = "general"
    genre = options.get("_detected_genre", "")
    # Manual subject lock: normalized [x, y] click point stored per clip.
    lock_raw = options.get("subject_lock")
    subject_lock = None
    try:
        if lock_raw and len(lock_raw) == 2:
            fx, fy = float(lock_raw[0]), float(lock_raw[1])
            if 0.0 <= fx <= 1.0 and 0.0 <= fy <= 1.0:
                subject_lock = (fx, fy)
    except (TypeError, ValueError):
        subject_lock = None
    if subject_lock:
        sink and sink(f"🔒 Manual subject lock at "
                      f"({subject_lock[0]:.2f}, {subject_lock[1]:.2f})")
    # Compute genre-aware crop_ratio and track_mode for re-renders.
    from genre import crop_style_height_ratio, track_mode_from_genre
    cr = crop_style_height_ratio(genre) if genre else 1.0
    tm = track_mode_from_genre(genre) if genre else "aggressive"
    # Cached AI/editor plan: render as-is (clip-local times). This is the
    # critical rerender fast path: do not call genre, layout-picker, scene
    # detection, or subject classification again. Per-frame TRACK/FACE
    # tracking still runs inside the chosen scene renderer.
    override = options.get("layout_plan_override")
    if override:
        if isinstance(override, str):
            override = json.loads(override)
        try:
            from reframe import reframe_scenes
            duration = get_duration(raw)
            aliases = {"face": "track", "screencast": "gameplay", "auto": "track"}
            allowed = {"track", "general", "center", "split", "panel",
                       "alternate", "gameplay"}
            plan = []
            for scene in override:
                start = max(0.0, min(float(scene.get("start", 0)), duration))
                end = max(0.0, min(float(scene.get("end", duration)), duration))
                requested = str(scene.get("strategy") or
                                scene.get("layout") or "track").lower()
                strategy = aliases.get(requested, requested)
                if end - start >= 0.1 and strategy in allowed:
                    plan.append({"start": start, "end": end,
                                 "strategy": strategy,
                                 "crop_ratio": float(scene.get("crop_ratio", 1.0) or 1.0),
                                 "track_mode": scene.get("track_mode", "aggressive")})
            plan.sort(key=lambda scene: scene["start"])
            if not plan or any(a["end"] > b["start"] for a, b in zip(plan, plan[1:])):
                raise ValueError("cached layout plan is empty or overlaps")
            sink and sink("🎛️ Cached layout plan: " + ", ".join(
                f"{sc['start']:.1f}-{sc['end']:.1f}s={sc['strategy']}"
                for sc in plan))
            reframe_scenes(raw, cropped, aspect=aspect, plan=plan,
                           clip_start=0.0, clip_end=get_duration(raw),
                           log=sink, work_dir=work_dir,
                           subject_lock=subject_lock)
            return cropped
        except Exception as e:
            raise RuntimeError(f"Edited layout plan failed: {e}") from e
    if mode in ("face", "track"):
        # Per-scene FACE (not one static whole-clip lock): each camera cut
        # centres its own speaker, gaps hold the last window, verified
        # two-shots cut to the active speaker.
        from reframe import reframe_face
        reframe_face(raw, cropped, aspect=aspect, log=sink,
                     work_dir=work_dir, subject_lock=subject_lock)
    elif mode in ("auto", "track"):
        # REFRAME_V2 feature flag: use Opus Clip-level dynamic reframing
        use_v2 = os.environ.get("REFRAME_V2", "0") == "1"
        try:
            if mode == "auto":
                from reframe import clip_scene_plan, reframe_scenes
                clip_genre = ""
                layout_prior = "none"
                try:
                    if options.get("genre_detect", True):
                        from genre import detect_genre
                        clip_genre = str(detect_genre(
                            raw, work_dir, log=sink,
                            transcript_snippet="",
                        ).get("genre") or "")
                    if options.get("layout_picker", True):
                        from layout_picker import pick_layout
                        layout_prior = pick_layout(
                            raw, work_dir, sink, force=True)
                except Exception as analysis_error:  # safe heuristic fallback
                    sink and sink(f"⚠️ layout prior skipped: {analysis_error}")
                # Scene-aware like the main pipeline: a re-cut clip that spans
                # talk -> whiteboard keeps each part in the right layout.
                plan = clip_scene_plan(
                    raw, log=sink, genre=clip_genre,
                    layout_prior=layout_prior)
                reframe_scenes(raw, cropped, aspect=aspect, plan=plan,
                               clip_start=0.0, clip_end=get_duration(raw),
                               log=sink, work_dir=work_dir,
                               subject_lock=subject_lock)
            elif use_v2:
                from reframe_v2 import reframe_clip_v2
                reframe_clip_v2(raw, cropped, aspect=aspect, log=sink)
            elif genre and genre in ("sports", "gaming", "tutorial"):
                # Genre override: sports/gaming/tutorial → center crop
                # (full frame, no face tracking) even when explicitly set to face/track.
                crop_video(raw, cropped, mode="center", aspect=aspect, log=sink)
            else:
                from reframe import reframe_clip
                reframe_clip(raw, cropped, aspect=aspect, strategy="track",
                             log=sink, work_dir=work_dir,
                             crop_ratio=cr, track_mode=tm,
                             subject_lock=subject_lock)
        except Exception as e:  # noqa: BLE001
            sink and sink(f"⚠️ reframe error — fallback center: {e}")
            crop_video(raw, cropped, mode="center", aspect=aspect, log=sink)
    elif mode in ("split", "panel", "alternate", "gameplay", "screencast"):
        # Same per-scene matrix as the pipeline: two-subject layouts only
        # where two live subjects are verified together (never a fake stack
        # of people who never share the frame, never a poster half).
        try:
            from reframe import clip_scene_plan, reframe_scenes
            _plan = clip_scene_plan(raw, log=sink, requested_layout=mode)
            if _plan:
                sink and sink("🎛️ Explicit {} per-scene".format(mode.upper()))
                reframe_scenes(raw, cropped, aspect=aspect, plan=_plan,
                               clip_start=0.0, clip_end=get_duration(raw),
                               log=sink, work_dir=work_dir,
                               subject_lock=subject_lock)
                return cropped
        except Exception as e:  # noqa: BLE001 — whole-clip fallback below
            sink and sink(f"⚠️ per-scene {mode} skipped ({e})")
        crop_video(raw, cropped, mode=mode, aspect=aspect, log=sink,
                   adaptive_size=bool(options.get("adaptive_size")),
                   gameplay_face_position=options.get(
                       "gameplay_face_position", "bottom"),
                   subject_lock=subject_lock)
    else:
        crop_video(raw, cropped, mode=mode, aspect=aspect, log=sink,
                   adaptive_size=bool(options.get("adaptive_size")),
                   gameplay_face_position=options.get(
                       "gameplay_face_position", "bottom"),
                   subject_lock=subject_lock)
    return cropped


def run_retrim(job_id: str, clip_idx: int, start: float, end: float,
               overrides: dict | None, out_root: str, sink=None) -> dict:
    """Re-render clip `clip_idx` with new bounds. Returns updated clip dict."""
    project = db.get_project(job_id)
    if not project:
        raise RuntimeError("Project not found")
    stored = project.get("options") or {}
    options = {**stored, **(overrides or {})}

    work_dir = os.path.join(out_root, job_id)
    source = os.path.join(work_dir, "source.mp4")
    if not os.path.exists(source):
        raise RuntimeError("Source video no longer available on disk")

    duration = get_duration(source) or 3600
    start = max(0.0, min(float(start), duration - 5))
    end = min(max(float(end), start + 5), duration)
    if end - start > 180:  # max 3 minutes per clip
        end = start + 180
    clip = {"start": round(start, 2), "end": round(end, 2)}
    stored_clips = db.get_clips(job_id)
    if clip_idx < len(stored_clips):
        clip["title"] = stored_clips[clip_idx].get("title", "")
        clip["hook"] = stored_clips[clip_idx].get("hook", "")
        # Manual subject lock: persisted per clip (JSON [x, y] normalized).
        _lock = stored_clips[clip_idx].get("subject_lock")
        if _lock:
            try:
                options["subject_lock"] = (json.loads(_lock)
                                           if isinstance(_lock, str) else _lock)
            except (TypeError, ValueError):
                pass
        # Per-scene layout editor: user-edited plan renders as-is.
        if stored_clips[clip_idx].get("layout_plan_edited"):
            _plan = stored_clips[clip_idx].get("layout_plan")
            if _plan:
                options["layout_plan_override"] = _plan

    transcript = project.get("transcript") or {"segments": []}
    ass_files = build_ass(
        transcript, [clip], work_dir,
        animation=options.get("animation", "karaoke"),
        color=options.get("color"),
        karaoke_color=options.get("karaoke_color"),
        emoji=bool(options.get("emoji", True)),
        font=options.get("caption_font", "Montserrat"),
        size=int(options.get("caption_size", 84)),
        style=options.get("caption_style", "solid"),
        bg=options.get("caption_bg", "solid"),
        log=sink,
        emoji_lang=options.get("translate_to", ""),
        words_per_line=options.get("words_per_line", 3),
        edits={0: (stored_clips[clip_idx].get("caption_edits") or {})
               if clip_idx < len(stored_clips) else {}},
        **_subtitle_editor_options(options),
    )

    sink and sink(f"✂️ Re-rendering clip {clip_idx + 1}: "
                  f"{clip['start']:.0f}s-{clip['end']:.0f}s")
    raw = os.path.join(work_dir, f"raw_r{clip_idx}.mp4")
    cropped = os.path.join(work_dir, f"vertical_r{clip_idx}.mp4")
    final = os.path.join(work_dir, _clip_basename(clip_idx))

    _extract_clip_with_segments(source, clip, raw, sink, work_dir)
    _render_crop(raw, cropped, options, options.get("aspect", "9:16"),
                 sink, work_dir)
    if options.get("auto_zoom"):
        zoomed = os.path.join(work_dir, f"zoom_r{clip_idx}.mp4")
        apply_zoom(cropped, zoomed,
                   zoom=float(options.get("zoom_strength", 1.15)),
                   punch_zoom=options.get("punch_zoom"),
                   log=sink)
        cropped = zoomed
    burn_subtitles(cropped, ass_files[0], final, sink,
                   watermark=options.get("watermark", ""),
                   **_title_editor_options(options),
                   hook_overlay=clip.get("hook", "")
                   if options.get("hook_overlay") else "",
                   free_tier=_is_free_tier(project.get("user_id", ""), options.get("show_klipin_watermark", True)))
    bgm_mood = options.get("bgm") or ""
    if bgm_mood and bgm_mood != "none":
        from bgm import mix_bgm, render_bgm
        bgm_path = os.path.join(work_dir, f"bgm_r{clip_idx}.m4a")
        render_bgm(bgm_mood, bgm_path, duration=clip["end"] - clip["start"],
                   log=sink)
        bgmed = os.path.join(work_dir, f"bgmed_r{clip_idx}.mp4")
        mix_bgm(final, bgm_path, bgmed,
                volume=float(options.get("bgm_volume", 0.15)), log=sink)
        os.replace(bgmed, final)
        if os.path.exists(bgm_path):
            os.remove(bgm_path)
    for tmp in (raw, cropped):
        if os.path.exists(tmp):
            os.remove(tmp)
    zoomed = os.path.join(work_dir, f"zoom_r{clip_idx}.mp4")
    if os.path.exists(zoomed):
        os.remove(zoomed)
    for tmp in (raw, cropped):
        if os.path.exists(tmp):
            os.remove(tmp)

    _portrait_guardian(final, work_dir, f"retrim{clip_idx}", options.get("aspect", "9:16"), sink)
    rev = db.bump_clip_rev(job_id, clip_idx)
    updated = {
        "start": clip["start"],
        "end": clip["end"],
        "size_mb": round(os.path.getsize(final) / 1e6, 1),
        "rev": rev,
    }
    db.update_clip(job_id, clip_idx, **updated)
    updated.update({"index": clip_idx, "video_url":
                    f"/videos/{job_id}/{_clip_basename(clip_idx)}"})
    sink and sink(f"✅ Clip {clip_idx + 1} re-rendered")
    return {"clip": updated}


def run_caption_edit(job_id: str, clip_idx: int, lines: list[str],
                     out_root: str, sink=None) -> dict:
    """Apply a caption typo fix to ONE clip and re-burn it (no AI, no re-cut).

    `lines` = the new text for each caption block, in the same order as
    `clip_caption_blocks`. Blocks whose text changed are stored as edits
    (keyed by block SOURCE start) and survive subsequent re-renders, because
    both run_rerender and run_retrim pass stored `caption_edits` to build_ass.
    """
    import json

    project = db.get_project(job_id)
    if not project:
        raise RuntimeError("Project not found")
    stored = project.get("options") or {}
    work_dir = os.path.join(out_root, job_id)
    source = os.path.join(work_dir, "source.mp4")
    if not os.path.exists(source):
        raise RuntimeError("Source video no longer available on disk")
    stored_clips = db.get_clips(job_id)
    if clip_idx < 0 or clip_idx >= len(stored_clips):
        raise RuntimeError(f"Clip index out of range: {clip_idx}")
    sc = stored_clips[clip_idx]
    clip = {"start": sc["start"], "end": sc["end"],
            "title": sc.get("title", ""), "hook": sc.get("hook", "")}

    transcript = project.get("transcript") or {"segments": []}
    blocks = clip_caption_blocks(transcript, clip,
                                 emoji=bool(stored.get("emoji", True)),
                                 lang=stored.get("translate_to", ""),
                                 words_per_line=stored.get("words_per_line", 3))
    edits = {}
    for i, new_text in enumerate(lines):
        if i >= len(blocks):
            break
        new_text = sanitize_caption_text((new_text or "").strip())
        key = f"{blocks[i]['start']:.2f}"
        if new_text and new_text != blocks[i]["text"]:
            edits[key] = new_text
        elif new_text == blocks[i]["text"] and key in edits:
            edits.pop(key, None)
    # Merge with any prior edits for OTHER blocks (keep untouched fixes).
    merged = dict(sc.get("caption_edits") or {})
    merged.update(edits)
    sink and sink(f"✏️ Fixing captions for clip {clip_idx + 1} "
                  f"({len(merged)} edited block(s))")

    ass_files = build_ass(
        transcript, [clip], work_dir,
        animation=stored.get("animation", "karaoke"),
        color=stored.get("color"),
        karaoke_color=stored.get("karaoke_color"),
        emoji=bool(stored.get("emoji", True)),
        font=stored.get("caption_font", "Montserrat"),
        size=int(stored.get("caption_size", 84)),
        style=stored.get("caption_style", "solid"),
        bg=stored.get("caption_bg", "solid"),
        log=sink,
        emoji_lang=stored.get("translate_to", ""),
        words_per_line=stored.get("words_per_line", 3),
        edits={0: merged},
        **_subtitle_editor_options(stored),
    )

    final = os.path.join(work_dir, _clip_basename(clip_idx))
    raw = os.path.join(work_dir, f"raw_c{clip_idx}.mp4")
    cropped = os.path.join(work_dir, f"vertical_c{clip_idx}.mp4")
    extract_clip(source, clip["start"], clip["end"], raw, sink,
                 fast=True)
    _render_crop(raw, cropped, stored, stored.get("aspect", "9:16"),
                 sink, work_dir)
    if stored.get("auto_zoom"):
        zoomed = os.path.join(work_dir, f"zoom_c{clip_idx}.mp4")
        apply_zoom(cropped, zoomed,
                   zoom=float(stored.get("zoom_strength", 1.15)),
                   punch_zoom=stored.get("punch_zoom"),
                   log=sink)
        cropped = zoomed
    burn_subtitles(cropped, ass_files[0], final, sink,
                   watermark=stored.get("watermark", ""),
                   **_title_editor_options(stored),
                   hook_overlay=clip.get("hook", "")
                   if stored.get("hook_overlay") else "",
                   free_tier=_is_free_tier(project.get("user_id", ""), stored.get("show_klipin_watermark", True)))
    bgm_mood = stored.get("bgm") or ""
    if bgm_mood and bgm_mood != "none":
        from bgm import mix_bgm, render_bgm
        bgm_path = os.path.join(work_dir, f"bgm_c{clip_idx}.m4a")
        render_bgm(bgm_mood, bgm_path,
                   duration=clip["end"] - clip["start"], log=sink)
        bgmed = os.path.join(work_dir, f"bgmed_c{clip_idx}.mp4")
        mix_bgm(final, bgm_path, bgmed,
                volume=float(stored.get("bgm_volume", 0.15)), log=sink)
        os.replace(bgmed, final)
        for tmp in (bgm_path, bgmed):
            if os.path.exists(tmp):
                os.remove(tmp)
    for tmp in (raw, cropped):
        if os.path.exists(tmp):
            os.remove(tmp)

    _portrait_guardian(final, work_dir, f"caption{clip_idx}", stored.get("aspect", "9:16"), sink)
    rev = db.bump_clip_rev(job_id, clip_idx)
    db.update_clip(job_id, clip_idx,
                   caption_edits=json.dumps(merged),
                   size_mb=round(os.path.getsize(final) / 1e6, 1),
                   rev=rev)
    updated = {
        "index": clip_idx,
        "start": clip["start"],
        "end": clip["end"],
        "title": clip["title"],
        "hook": clip["hook"],
        "rev": rev,
        "caption_edits": merged,
        "video_url": f"/videos/{job_id}/{_clip_basename(clip_idx)}",
        "subtitle_url": f"/videos/{job_id}/clip_{clip_idx + 1}.ass",
        "size_mb": round(os.path.getsize(final) / 1e6, 1),
    }
    sink and sink(f"✅ Clip {clip_idx + 1} captions fixed")
    return {"clip": updated}


def run_publish(job_id: str, clip_idx: int, platform: str,
                title: str = "", description: str = "", privacy: str = "",
                out_root: str = "", sink=None, publish_id: int | None = None,
                user_id: str = "") -> dict:
    """Publish one clip to a social platform via publish.py.

    When `publish_id` is given (a scheduled publish row), the row is updated
    in place to `published`/`failed` instead of appending a new history entry.
    Returns {platform, post_url, post_id, clip_idx}.
    """
    from publish import publish

    try:
        clips = db.get_clips(job_id)
        if clip_idx < 0 or clip_idx >= len(clips):
            raise RuntimeError(f"Clip index out of range: {clip_idx}")
        clip = clips[clip_idx]
        src = os.path.join(out_root, job_id, _clip_basename(clip_idx))
        if not os.path.exists(src):
            raise RuntimeError("Clip file no longer available on disk")

        caption = title or clip.get("title") or f"Clip {clip_idx + 1}"
        desc = description or clip.get("hook") or ""
        sink and sink(f"🚀 Publishing clip {clip_idx + 1} to {platform}...")
        result = publish(user_id, platform, src, caption, desc, privacy)
    except Exception as e:  # noqa: BLE001
        if publish_id is not None:
            db.update_publish(publish_id, status="failed", error=str(e))
        else:
            db.log_publish(job_id, clip_idx, platform, "failed", error=str(e),
                           user_id=user_id)
        raise
    if publish_id is not None:
        db.update_publish(publish_id, status="published",
                          post_url=result.get("post_url", ""),
                          post_id=result.get("post_id", ""))
    else:
        db.log_publish(job_id, clip_idx, platform, "published",
                       post_url=result.get("post_url", ""),
                       post_id=result.get("post_id", ""), user_id=user_id)
    sink and sink(f"✅ Published to {platform}: {result.get('post_url')}")
    result.update({"platform": platform, "clip_idx": clip_idx})
    return result


def run_export(job_id: str, indices: list, resolution: str,
               out_root: str, sink=None, aspect: str = "") -> dict:
    """Bundle selected clips into a ZIP. Returns {zip_url, size_mb, count}.

    `aspect` (9:16 | 1:1 | 16:9) re-crops the stored clips from the source
    video, so an export can differ from the rendered aspect.
    """
    clips = db.get_clips(job_id)
    if not clips:
        raise RuntimeError("Project has no clips to export")
    if not indices:
        indices = [c["idx"] for c in clips]
    missing = [i for i in indices if i < 0 or i >= len(clips)]
    if missing:
        raise RuntimeError(f"Clip index out of range: {missing}")

    work_dir = os.path.join(out_root, job_id)
    project = db.get_project(job_id) or {}
    source = os.path.join(work_dir, "source.mp4")
    stored_options = project.get("options") or {}
    export_aspect = aspect or stored_options.get("aspect", "9:16")
    if export_aspect == "16:9":
        sink and sink("⚠️ legacy 16:9 export overridden → 9:16")
        export_aspect = "9:16"
    layout = stored_options.get("layout", stored_options.get("crop", "general"))

    stamp = int(time.time())
    zip_path = os.path.join(work_dir, f"export_{stamp}.zip")
    manifest = []
    sink and sink(f"📦 Exporting {len(indices)} clip(s) "
                  f"({resolution or 'source'} · {export_aspect})...")

    def slug(title: str) -> str:
        keep = "".join(ch if ch.isalnum() or ch in " _-" else "-"
                       for ch in title.lower())
        return keep.strip(" -")[:40] or "clip"

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for i, idx in enumerate(indices):
            clip = clips[idx]
            src = os.path.join(work_dir, _clip_basename(idx))
            if not os.path.exists(src):
                sink and sink(f"⚠️ Missing file for clip {idx + 1}, skipping")
                continue
            name = f"clip_{idx + 1:02d}_{slug(clip.get('title', ''))}.mp4"
            tmp = os.path.join(work_dir, f"export_tmp_{i}.mp4")
            if export_aspect != stored_options.get("aspect", "9:16") \
                    and os.path.exists(source):
                # Re-crop from source for the requested aspect — re-burn the
                # captions (same styling as the stored render) so the exported
                # clips keep their subtitles.
                raw = os.path.join(work_dir, f"export_raw_{i}.mp4")
                extract_clip(source, clip.get("start", 0), clip.get("end", 30),
                             raw, sink, fast=True)
                _render_crop(raw, tmp, stored_options, export_aspect, sink,
                             work_dir)
                os.remove(raw)
                transcript = project.get("transcript") or {"segments": []}
                ass_files = build_ass(
                    transcript, [clip], work_dir,
                    animation=stored_options.get("animation", "karaoke"),
                    color=stored_options.get("color"),
                    karaoke_color=stored_options.get("karaoke_color"),
                    emoji=bool(stored_options.get("emoji", True)),
                    font=stored_options.get("caption_font", "Montserrat"),
                    size=int(stored_options.get("caption_size", 84)),
                    style=stored_options.get("caption_style", "solid"),
                    bg=stored_options.get("caption_bg", "solid"),
                    log=sink,
                    emoji_lang=stored_options.get("translate_to", ""),
                    words_per_line=stored_options.get("words_per_line", 3),
                    **_subtitle_editor_options(stored_options),
                )
                captioned = os.path.join(work_dir, f"export_cap_{i}.mp4")
                burn_subtitles(tmp, ass_files[0], captioned, sink,
                               watermark=stored_options.get("watermark", ""),
                               **_title_editor_options(stored_options),
                               hook_overlay=clip.get("hook", "")
                               if stored_options.get("hook_overlay") else "",
                               free_tier=_is_free_tier(project.get("user_id", ""), stored_options.get("show_klipin_watermark", True)))
                os.remove(tmp)
                tmp = captioned
            elif resolution and resolution in ("720", "1080"):
                scale_clip(src, tmp, res=resolution, log=sink)
            else:
                tmp = src
            zf.write(tmp, name)
            if tmp != src and os.path.exists(tmp):
                os.remove(tmp)
            manifest.append({
                "file": name,
                "index": idx,
                "start": clip.get("start"),
                "end": clip.get("end"),
                "title": clip.get("title", ""),
                "hook": clip.get("hook", ""),
            })
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))

    if not manifest:
        raise RuntimeError("No clips were exported")
    sink and sink(f"✅ Export ready: {len(manifest)} clip(s)")
    return {
        "zip_url": f"/videos/{job_id}/{os.path.basename(zip_path)}",
        "size_mb": round(os.path.getsize(zip_path) / 1e6, 1),
        "count": len(manifest),
    }


def run_rerender(job_id: str, out_root: str = "", sink=None) -> dict:
    """Re-render ALL clips with the stored (possibly transcript-edited)
    transcript + options. No AI call — captions are rebuilt from the saved
    transcript, so a transcript edit (or a caption-style change) is free.
    Returns {clips, count}.
    """
    project = db.get_project(job_id)
    if not project:
        raise RuntimeError("Project not found")
    work_dir = os.path.join(out_root, job_id)
    source = os.path.join(work_dir, "source.mp4")
    if not os.path.exists(source):
        raise RuntimeError("Source video no longer available on disk")

    options = project.get("options") or {}
    transcript = project.get("transcript") or {"segments": []}
    clips = db.get_clips(job_id)
    if not clips:
        raise RuntimeError("Project has no clips to re-render")

    crop_mode = options.get("layout", options.get("crop", "auto"))
    aspect = options.get("aspect", "9:16")
    watermark = options.get("watermark", "")
    title_overlay = options.get("title_overlay", "")
    hook_overlay = bool(options.get("hook_overlay"))

    ass_files = build_ass(
        transcript, clips, work_dir,
        animation=options.get("animation", "karaoke"),
        color=options.get("color"),
        karaoke_color=options.get("karaoke_color"),
        emoji=bool(options.get("emoji", True)),
        font=options.get("caption_font", "Montserrat"),
        size=int(options.get("caption_size", 84)),
        style=options.get("caption_style", "solid"),
        bg=options.get("caption_bg", "solid"), log=sink,
        emoji_lang=options.get("translate_to", ""),
        words_per_line=options.get("words_per_line", 3),
        edits={i: (c.get("caption_edits") or {})
               for i, c in enumerate(clips)},
        **_subtitle_editor_options(options),
    )

    results = []
    for i, clip in enumerate(clips):
        sink and sink(f"✂️ Re-render {i + 1}/{len(clips)}: "
                      f"{clip['start']:.0f}s-{clip['end']:.0f}s")
        raw = os.path.join(work_dir, f"rr_raw_{i + 1}.mp4")
        cropped = os.path.join(work_dir, f"rr_vertical_{i + 1}.mp4")
        final = os.path.join(work_dir, f"clip_{i + 1}.mp4")

        _extract_clip_with_segments(source, clip, raw, sink, work_dir)
        clip_opts = dict(options)
        _lock = clip.get("subject_lock")
        if _lock:
            try:
                clip_opts["subject_lock"] = (json.loads(_lock)
                                             if isinstance(_lock, str)
                                             else _lock)
            except (TypeError, ValueError):
                pass
        # Reuse the AI-generated plan too. The plan is already the result of
        # scene classification from the original project run.
        if clip.get("layout_plan"):
            clip_opts["layout_plan_override"] = clip.get("layout_plan")
        else:
            # Legacy projects created before layout_plan persistence have no
            # safe scene map to replay. Never send those rerenders back into
            # Gemini/scene classification: use deterministic TRACK for the
            # selected clip and let the per-frame face tracker keep the
            # speaker framed. A fresh project can still produce full AUTO
            # plans; this fallback is only for old projects.
            clip_opts["layout_plan_override"] = [{
                "start": 0.0,
                "end": round(float(clip["end"] - clip["start"]), 3),
                "strategy": "track",
            }]
            sink and sink("⚡ Legacy clip has no cached layout plan; "
                         "using deterministic TRACK without re-analysis")
        _render_crop(raw, cropped, clip_opts, aspect, sink, work_dir)
        if options.get("auto_zoom"):
            zoomed = os.path.join(work_dir, f"rr_zoom_{i + 1}.mp4")
            apply_zoom(cropped, zoomed,
                       zoom=float(options.get("zoom_strength", 1.15)),
                       punch_zoom=bool(options.get("punch_zoom")),
                       log=sink)
            cropped = zoomed
        burn_subtitles(cropped, ass_files[i], final, sink,
                       watermark=watermark, **_title_editor_options(options),
                       hook_overlay=clip.get("hook", "") if hook_overlay else "",
                       free_tier=_is_free_tier(project.get("user_id", ""), options.get("show_klipin_watermark", True)))

        # Render Hook V2 intro once, AFTER subtitles so it is not overwritten,
        # and BEFORE BGM/guardian so the final prepend is validated.
        hook_v2_path = ""
        if clip.get("hook_v2") and clip["hook_v2"].get("enabled"):
            sink and sink(f"   🎬 Rendering Hook V2 intro for clip {i+1}...")
            hook_v2_path = render_hook_v2_intro(
                source, clip["hook_v2"], work_dir, aspect, options, sink, rank=i+1
            )

        # If hook_v2 rendered, prepend it to final
        if hook_v2_path and os.path.exists(hook_v2_path):
            # Concat hook_v2 + final
            list_file = os.path.join(work_dir, f"hookv2_concat_{i+1}.txt")
            with open(list_file, "w", encoding="utf-8") as lf:
                lf.write("file '" + hook_v2_path + "'\nfile '" + final + "'\n")
            final_with_hook = os.path.join(work_dir, f"clip_{i+1}_withhook.mp4")
            from video import run
            cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                   "-f", "concat", "-safe", "0", "-i", list_file,
                   "-c", "copy", "-vsync", "vfr", final_with_hook]
            run(cmd, sink)
            if os.path.exists(list_file):
                os.remove(list_file)
            os.replace(final_with_hook, final)

        burn_subtitles(cropped, ass_files[i], final, sink,
                       watermark=watermark, **_title_editor_options(options),
                       hook_overlay=clip.get("hook", "") if hook_overlay else "",
                       free_tier=_is_free_tier(project.get("user_id", ""), options.get("show_klipin_watermark", True)))

        bgm_mood = options.get("bgm") or ""
        if bgm_mood and bgm_mood != "none":
            from bgm import mix_bgm, render_bgm
            bgm_path = os.path.join(work_dir, f"rr_bgm_{i + 1}.m4a")
            render_bgm(bgm_mood, bgm_path,
                       duration=clip["end"] - clip["start"], log=sink)
            bgmed = os.path.join(work_dir, f"rr_bgmed_{i + 1}.mp4")
            mix_bgm(final, bgm_path, bgmed,
                    volume=float(options.get("bgm_volume", 0.15)), log=sink)
            os.replace(bgmed, final)
            for tmp in (bgm_path, bgmed):
                if os.path.exists(tmp):
                    os.remove(tmp)

        for tmp in (raw, cropped):
            if os.path.exists(tmp):
                os.remove(tmp)

        _portrait_guardian(final, work_dir, f"rr{i + 1}", aspect, sink)
        rev = db.bump_clip_rev(job_id, i)
        db.update_clip(job_id, i, size_mb=round(os.path.getsize(final) / 1e6, 1),
                       rev=rev)
        results.append({
            "index": i,
            "start": clip["start"],
            "end": clip["end"],
            "title": clip.get("title", f"Clip {i + 1}"),
            "hook": clip.get("hook", ""),
            "hook_score": clip.get("hook_score", 0),
            "predicted_score": clip.get("predicted_score",
                                        clip.get("hook_score", 0)),
            "reason": clip.get("reason", ""),
            "thumbnail_url": clip.get("thumbnail_url", ""),
            "hashtags": clip.get("hashtags") or [],
            "video_url": f"/videos/{job_id}/clip_{i + 1}.mp4",
            "subtitle_url": f"/videos/{job_id}/clip_{i + 1}.ass",
            "size_mb": round(os.path.getsize(final) / 1e6, 1),
            "rev": rev,
        })

    sink and sink(f"✅ Re-rendered {len(results)} clips")
    return {"clips": results, "count": len(results)}


def run_repick(job_id: str, custom_prompt: str, out_root: str = "",
               sink=None) -> dict:
    """Re-run Gemini moment selection with new criteria (re-pick momen klip).

    Uses the stored source video + transcript (no re-transcribe), re-renders
    every clip with the project's existing options, and replaces the clip
    list in the DB. Returns {clips, count, custom_prompt}.
    """
    project = db.get_project(job_id)
    if not project:
        raise RuntimeError("Project not found")
    work_dir = os.path.join(out_root, job_id)
    source = os.path.join(work_dir, "source.mp4")
    if not os.path.exists(source):
        raise RuntimeError("Source video no longer available on disk")

    options = {**(project.get("options") or {}), "custom_prompt": custom_prompt}
    transcript = project.get("transcript") or {"segments": []}
    if not (transcript.get("segments") or []):
        raise RuntimeError("Transcript missing — re-pick needs the stored transcript")

    sink and sink(f"🧠 Re-picking moments (custom criteria)...")
    clips = select_clips(
        source, transcript, work_dir, sink,
        custom_prompt=custom_prompt,
        clip_length=options.get("clip_length", "cerita"),
    )
    if not clips:
        raise RuntimeError("AI returned no clips for these criteria")

    # Re-render every clip with the stored options (same as pipeline).
    crop_mode = options.get("layout", options.get("crop", "general"))
    aspect = options.get("aspect", "9:16")
    anim = options.get("animation", "karaoke")
    use_emoji = bool(options.get("emoji", True))
    caption_font = options.get("caption_font", "Montserrat")
    caption_size = int(options.get("caption_size", 84))
    caption_style = options.get("caption_style", "solid")
    watermark = options.get("watermark", "")
    title_overlay = options.get("title_overlay", "")

    ass_files = build_ass(
        transcript, clips, work_dir,
        animation=anim, color=options.get("color"),
        karaoke_color=options.get("karaoke_color"),
        emoji=use_emoji, font=caption_font,
        size=caption_size, style=caption_style,
        bg=options.get("caption_bg", "solid"), log=sink,
        emoji_lang=options.get("translate_to", ""),
        words_per_line=options.get("words_per_line", 3),
        **_subtitle_editor_options(options),
    )

    results = []
    for i, clip in enumerate(clips):
        sink and sink(f"✂️ Re-render {i + 1}/{len(clips)}: "
                      f"{clip['start']:.0f}s-{clip['end']:.0f}s")
        raw = os.path.join(work_dir, f"repick_raw_{i + 1}.mp4")
        cropped = os.path.join(work_dir, f"repick_vertical_{i + 1}.mp4")
        final = os.path.join(work_dir, f"clip_{i + 1}.mp4")

        _extract_clip_with_segments(source, clip, raw, sink, work_dir)
        _render_crop(raw, cropped, options, aspect, sink, work_dir)
        if options.get("auto_zoom"):
            zoomed = os.path.join(work_dir, f"repick_zoom_{i + 1}.mp4")
            apply_zoom(cropped, zoomed,
                       zoom=float(options.get("zoom_strength", 1.15)),
                       punch_zoom=bool(options.get("punch_zoom")),
                       log=sink)
            cropped = zoomed
        burn_subtitles(cropped, ass_files[i], final, sink,
                       watermark=watermark, **_title_editor_options(options),
                       hook_overlay=clip.get("hook", "")
                       if options.get("hook_overlay") else "",
                       free_tier=_is_free_tier(project.get("user_id", ""), options.get("show_klipin_watermark", True)))

        bgm_mood = options.get("bgm") or ""
        if bgm_mood and bgm_mood != "none":
            from bgm import mix_bgm, render_bgm
            bgm_path = os.path.join(work_dir, f"repick_bgm_{i + 1}.m4a")
            render_bgm(bgm_mood, bgm_path,
                       duration=clip["end"] - clip["start"], log=sink)
            bgmed = os.path.join(work_dir, f"repick_bgmed_{i + 1}.mp4")
            mix_bgm(final, bgm_path, bgmed,
                    volume=float(options.get("bgm_volume", 0.15)), log=sink)
            os.replace(bgmed, final)
            for tmp in (bgm_path, bgmed):
                if os.path.exists(tmp):
                    os.remove(tmp)

        for tmp in (raw, cropped):
            if os.path.exists(tmp):
                os.remove(tmp)
        zoomed = os.path.join(work_dir, f"repick_zoom_{i + 1}.mp4")
        if os.path.exists(zoomed):
            os.remove(zoomed)

        _portrait_guardian(final, work_dir, f"rp{i + 1}", aspect, sink)
        results.append({
            "index": i,
            "start": clip["start"],
            "end": clip["end"],
            "title": clip.get("title", f"Clip {i + 1}"),
            "hook": clip.get("hook", ""),
            "hook_score": clip.get("hook_score", 0),
            "predicted_score": clip.get("predicted_score",
                                        clip.get("hook_score", 0)),
            "reason": clip.get("reason", ""),
            "video_url": f"/videos/{job_id}/clip_{i + 1}.mp4",
            "subtitle_url": f"/videos/{job_id}/clip_{i + 1}.ass",
            "size_mb": round(os.path.getsize(final) / 1e6, 1),
            "rev": 0,
        })

    # Auto assets for the new picks (thumbnail + batched hashtags).
    try:
        attach_clip_assets(source, work_dir, results, sink)
    except Exception as e:  # noqa: BLE001
        sink and sink(f"⚠️ clip assets skipped: {e}")

    # Persist: replace clips + update stored options (keep source_label/etc).
    db.replace_clips(job_id, results)
    db.save_project(
        job_id,
        source_label=project.get("source_label", ""),
        status=project.get("status", "completed"),
        created_at=project.get("created_at", time.time()),
        options=options,
        error=project.get("error", ""),
        duration=project.get("duration"),
        transcript=transcript,
        mode=project.get("mode", "viral"),
        cost=project.get("cost"),
    )

    sink and sink(f"✅ Re-pick done: {len(results)} clips with new criteria")
    return {"clips": results, "count": len(results), "custom_prompt": custom_prompt}
