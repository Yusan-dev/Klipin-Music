"""Custom Cut — "editor bebas potong" (Canva-style).

The user picks any number of keep-ranges from the SOURCE video (e.g. remove
intro, ads, dead air — or stitch several moments into one clip). Klipin:
  1. extracts each range,
  2. crops each to the target layout/aspect,
  3. concatenates them into one clip,
  4. burns captions with TIMES SHIFTED to the cut timeline, so the karaoke
     stays perfectly in sync even though segments were removed.

Keep-ranges must be ascending and non-overlapping; min 3s each, total ≤ 5 min.
"""
import os
import shutil
import subprocess

from subtitles import (ANIMATION_TAGS, FONT_ALIASES, STYLE_SPECS, _ass_escape,
                       _ass_color, _back_colour, _phrase_blocks,
                       default_color, default_karaoke_color, inject_emoji,
                       merge_continuation_words, reconcile_segment_words,
                       seconds_to_ass)

MIN_SEG = 3.0
MAX_TOTAL = 180.0  # custom cut result is a single clip — max 3 minutes


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


def _shift_map(ranges: list) -> tuple:
    """Build a source→cut time mapping for caption remapping.

    Returns (mapper_fn, cut_duration). For a source time inside kept range
    [a,b] → cut time = offset + (t - a). Outside any range → None.
    """
    def mapper(t: float):
        offset = 0.0
        for a, b in ranges:
            if t < a:
                return None
            if a <= t <= b:
                return offset + (t - a)
            offset += b - a
        return None
    return mapper, sum(b - a for a, b in ranges)


def _build_shifted_ass(transcript: dict, ranges: list, work_dir: str,
                       animation: str = "none", color: str | None = None,
                       karaoke_color: str | None = None,
                       emoji: bool = True, font: str = "Inter",
                       size: int = 72, style: str = "solid",
                       bg: str = "solid", emoji_lang: str = "",
                       words_per_line: int = 3) -> str:
    """Build a karaoke .ass whose timings are remapped to the cut timeline.

    Groups words into `words_per_line`-sized blocks (pauses break early),
    remaps each word's source time to the cut timeline, and emits word-locked
    `{\\k}` tags so the spoken word highlights while the rest of the line
    stays in the base colour.
    """
    base = _ass_color(color or default_color())
    highlight = _ass_color(karaoke_color or default_karaoke_color())
    anim = ANIMATION_TAGS.get(animation, "")
    spec = STYLE_SPECS.get(style, STYLE_SPECS["solid"])
    back = _back_colour(bg)
    mapper, _ = _shift_map(ranges)

    # Rebuild segment word lists (whisper drops tokens at boundaries) and honor
    # the leading-space word convention, mirroring subtitles._all_words.
    segs = transcript.get("segments") or []
    has_leading_space = any(
        str(w.get("word", "") or "").startswith(" ")
        for seg in segs
        for w in (seg.get("words") or [])
    )
    for seg in segs:
        reconcile_segment_words(seg)
    words = []
    for seg in segs:
        words.extend(seg.get("words") or [])
    if has_leading_space:
        words = merge_continuation_words(words)
    if not words:
        words = [{"word": seg.get("text", "").strip(),
                  "start": seg.get("start", 0),
                  "end": seg.get("end", seg.get("start", 0) + 2)}
                 for seg in segs]

    wpl = max(1, int(words_per_line or 3))
    blocks = _phrase_blocks(words, max_words=wpl, pause=0.5)
    events = []
    for block in blocks:
        if not block:
            continue
        line_start = mapper(float(block[0].get("start", 0)))
        line_end = mapper(float(block[-1].get("end",
                                              block[-1].get("start", 0) + 0.4)))
        if line_start is None or line_end is None or line_end <= line_start:
            continue
        parts = []
        for j, w in enumerate(block):
            w_start = mapper(float(w.get("start", line_start)))
            w_end = mapper(float(w.get("end", w_start + 0.2)))
            if w_start is None or w_end is None:
                continue
            nxt = (mapper(float(block[j + 1].get("start", line_end)))
                   if j + 1 < len(block) else line_end)
            if nxt is None:
                nxt = line_end
            dur = max(0.08, min(w_end - w_start, max(0.08, nxt - w_start)))
            dur_cs = int(round(dur * 100))
            text = _ass_escape(str(w.get("word", "")))
            if emoji:
                text = inject_emoji(text, emoji_lang)
            sep = " " if j > 0 else ""
            parts.append(f"{sep}{{\\k{dur_cs}}}{text}")
        if not parts:
            continue
        events.append(
            f"Dialogue: 0,{seconds_to_ass(line_start)},{seconds_to_ass(line_end)},"
            f"Caption,,0,0,0,,{anim}{''.join(parts)}"
        )

    ass = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n\n"
        "[V4+ Styles]\n"
        f"Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        f"OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        f"ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        f"Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Caption, {FONT_ALIASES.get(font, 'Inter')}, {size}, "
        f"{highlight}, {base}, &H000000&, {back}, 0, 0, 0, 0, "
        f"100, 100, 0, 0, {spec['borderstyle']}, {spec['outline']}, "
        f"{spec['shadow']}, 2, 60, 60, {spec['margin']}, 1\n\n[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text\n"
    )
    path = os.path.join(work_dir, "cut.ass")
    with open(path, "w", encoding="utf-8") as f:
        f.write(ass + "\n".join(events))
    return path


def _validate_ranges(ranges: list, duration: float) -> list:
    """Sort, dedupe overlaps, enforce min/max, clamp to duration."""
    out = []
    for r in ranges or []:
        try:
            a, b = float(r[0]), float(r[1])
        except (TypeError, ValueError, IndexError):
            continue
        a, b = max(0.0, min(a, b)), max(0.0, max(a, b))
        if b - a < MIN_SEG:
            continue
        out.append([round(a, 2), round(b, 2)])
    out.sort()
    merged = []
    for a, b in out:
        if merged and a < merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    total = sum(b - a for a, b in merged)
    if total > MAX_TOTAL:
        raise RuntimeError(f"Total cut duration {total:.0f}s exceeds "
                           f"{MAX_TOTAL:.0f}s limit")
    return merged


def run_cut(job_id: str, ranges: list, out_root: str = "",
            options: dict | None = None, sink=None) -> dict:
    """Job entrypoint: build a custom-cut clip from keep-ranges of the source.

    Returns {video_url, size_mb, duration, ranges}.
    """
    import db
    from video import (apply_zoom, concat_clips, crop_video,
                       extract_clip, get_duration, get_resolution,
                       has_square_pixels)
    from reframe import clip_scene_plan, reframe_face, reframe_scenes
    from subtitles import burn_subtitles

    options = options or {}
    project = db.get_project(job_id)
    if not project:
        raise RuntimeError("Project not found")
    log = sink or (lambda m: None)

    source = os.path.join(out_root, job_id, "source.mp4")
    if not os.path.exists(source):
        raise RuntimeError("Source video no longer available on disk")
    src_dur = get_duration(source)
    kept = _validate_ranges(ranges, src_dur)
    if not kept:
        raise RuntimeError("No valid keep-ranges (min 3s each)")

    layout = options.get("layout", options.get("crop", "auto"))
    aspect = options.get("aspect", "9:16")
    if aspect == "16:9":
        log("⚠️ legacy 16:9 cut overridden → 9:16")
        aspect = "9:16"
    animation = options.get("animation", "karaoke")
    watermark = options.get("watermark", "")
    title_overlay = options.get("title_overlay", "")
    color = options.get("color")
    emoji = bool(options.get("emoji", True))
    font = options.get("caption_font", "Montserrat")
    size = int(options.get("caption_size", 84))
    style = options.get("caption_style", "solid")

    work = os.path.join(out_root, job_id, "cut_work")
    os.makedirs(work, exist_ok=True)
    raws, vids = [], []
    log(f"✂️ Custom cut: {len(kept)} segment(s), "
        f"total {sum(b - a for a, b in kept):.0f}s")
    for i, (a, b) in enumerate(kept):
        raw = os.path.join(work, f"raw_{i}.mp4")
        vid = os.path.join(work, f"vert_{i}.mp4")
        extract_clip(source, a, b, raw, log)
        if layout in ("auto", "face", "track"):
            try:
                if layout == "auto":
                    plan = clip_scene_plan(raw, log=log)
                    reframe_scenes(raw, vid, aspect=aspect, plan=plan,
                                   clip_start=0.0, clip_end=get_duration(raw),
                                   log=log, work_dir=work)
                else:
                    from reframe import reframe_face
                    reframe_face(raw, vid, aspect=aspect, log=log,
                                 work_dir=work)
            except Exception as e:
                log(f"⚠️ reframe error — fallback center: {e}")
                crop_video(raw, vid, mode="center", aspect=aspect, log=log)
        else:
            crop_video(raw, vid, mode=layout, aspect=aspect, log=log)
        if options.get("auto_zoom"):
            zoomed = os.path.join(work, f"zoom_{i}.mp4")
            apply_zoom(vid, zoomed,
                       zoom=float(options.get("zoom_strength", 1.15)),
                       punch_zoom=options.get("punch_zoom"),
                       log=log)
            vid = zoomed
        raws.append(raw)
        vids.append(vid)
        log(f"  segment {i + 1}: {a:.0f}s-{b:.0f}s")

    concat = os.path.join(work, "cut_raw.mp4")
    concat_clips(vids, concat, log)

    ass_path = _build_shifted_ass(project.get("transcript") or {}, kept,
                                  work, animation=animation, color=color,
                                  karaoke_color=options.get("karaoke_color"),
                                  emoji=emoji, font=font, size=size,
                                  style=style,
                                  bg=options.get("caption_bg", "solid"),
                                  emoji_lang=options.get("translate_to", ""),
                                  words_per_line=options.get("words_per_line", 3))
    final = os.path.join(out_root, job_id, "cut_1.mp4")
    burn_subtitles(concat, ass_path, final, log, watermark=watermark,
                   title_overlay=title_overlay,
                   free_tier=_is_free_tier(project.get("user_id", ""), options.get("show_klipin_watermark", True)))

    if aspect == "9:16":
        from video import scale_clip
        fw, fh = get_resolution(final)
        if (fw, fh) != (1080, 1920) or not has_square_pixels(final):
            log(f"⚠️ Portrait guardian repaired {fw}x{fh} → 1080x1920 SAR 1:1")
            repaired = os.path.join(work, "guardian_cut.mp4")
            try:
                crop_video(concat if os.path.exists(concat) else final, repaired,
                           mode="center", aspect="9:16", log=log)
                # Re-burn captions on the repaired base so karaoke stays.
                burn_subtitles(repaired, ass_path, final, log, watermark=watermark,
                               title_overlay=title_overlay,
                               free_tier=_is_free_tier(project.get("user_id", ""), options.get("show_klipin_watermark", True)))
            except Exception:
                scale_clip(final, repaired, "1080", log)
                os.replace(repaired, final)
        fw, fh = get_resolution(final)
        if (fw, fh) != (1080, 1920) or not has_square_pixels(final):
            raise RuntimeError(
                f"LAYOUT_LOCK_VIOLATION: expected 1080x1920 SAR 1:1, got {fw}x{fh}")

    shutil.rmtree(work, ignore_errors=True)
    log(f"✅ Custom cut done: {get_duration(final):.0f}s")
    return {
        "video_url": f"/videos/{job_id}/cut_1.mp4",
        "size_mb": round(os.path.getsize(final) / 1e6, 1),
        "duration": round(get_duration(final), 2),
        "ranges": kept,
    }
