"""Hook overlay system (ported from OpenShorts).

Professional hook/title overlays with multiple styles, emoji support,
and pixel-perfect text wrapping.
"""
import os
import re
import subprocess
import urllib.request
import uuid
from PIL import Image, ImageDraw, ImageFont, ImageFilter

# Codepoint ranges NotoSerif has no glyphs for (would render as tofu boxes).
_EMOJI_RE = re.compile(
    "["
    "\U0001F000-\U0001FAFF"  # emoticons, symbols, transport, supplemental
    "\U00002600-\U000027BF"  # misc symbols + dingbats
    "\U0001F1E6-\U0001F1FF"  # regional indicators (flags)
    "\U00002B00-\U00002BFF"  # arrows, stars
    "\U0000FE0E\U0000FE0F"   # variation selectors
    "\U0000200D"             # zero-width joiner
    "\U000020E3"             # combining keycap
    "]+"
)

# Emoji-capable fonts, probed at runtime (Windows, WSL, Linux/Docker, macOS).
_EMOJI_FONT_CANDIDATES = [
    "C:\\Windows\\Fonts\\seguiemj.ttf",
    "/mnt/c/Windows/Fonts/seguiemj.ttf",
    "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
    "/usr/share/fonts/noto-emoji/NotoColorEmoji.ttf",
    "/System/Library/Fonts/Apple Color Emoji.ttc",
]

# Bitmap strike sizes color-emoji fonts ship with. NotoColorEmoji (the font
# the Docker image installs) ONLY loads at its strike size — asking for an
# arbitrary size raises "invalid pixel size" — so glyphs are rendered at the
# native size and rescaled at draw time.
_EMOJI_BITMAP_SIZES = [109, 128, 136, 160, 96, 72, 64, 32]


def _load_emoji_font(font_size):
    """Return (font, native_size) for an emoji-capable font, or None.

    native_size == font_size for scalable fonts (Segoe UI Emoji); for
    fixed-bitmap fonts (NotoColorEmoji) it is the strike size the font
    actually loaded at, and the renderer scales glyphs down from it.
    """
    for path in _EMOJI_FONT_CANDIDATES:
        if not os.path.exists(path):
            continue
        try:
            return ImageFont.truetype(path, font_size), font_size
        except Exception:
            pass
        for native in _EMOJI_BITMAP_SIZES:
            try:
                return ImageFont.truetype(path, native), native
            except Exception:
                continue
    return None


def _split_emoji_runs(text):
    """Split text into (is_emoji, chunk) runs."""
    runs = []
    pos = 0
    for m in _EMOJI_RE.finditer(text):
        if m.start() > pos:
            runs.append((False, text[pos:m.start()]))
        runs.append((True, m.group()))
        pos = m.end()
    if pos < len(text):
        runs.append((False, text[pos:]))
    return runs


def _emoji_scale(font, emoji_font):
    """Draw-time scale factor from the emoji font's native size to the text
    size. 1.0 for scalable emoji fonts; < 1 for fixed-bitmap strikes.
    """
    efont, native = emoji_font
    target = getattr(font, "size", native)
    return target / float(native) if native else 1.0


def _measure_width(draw, text, font, emoji_font):
    """Pixel width of a line, measuring emoji runs with the emoji font.

    emoji_font is the (font, native_size) pair from _load_emoji_font, or None.
    """
    width = 0.0
    for is_emoji, chunk in _split_emoji_runs(text):
        if is_emoji and emoji_font:
            width += (draw.textlength(chunk, font=emoji_font[0])
                      * _emoji_scale(font, emoji_font))
        else:
            width += draw.textlength(chunk, font=font)
    return width


def _render_emoji_chunk(chunk, emoji_font, scale, fill):
    """Rasterize an emoji run at the font's native size, scaled to the text
    size. Returns an RGBA image ready to alpha-composite, or None.
    """
    efont, native = emoji_font
    probe = ImageDraw.Draw(Image.new('RGBA', (1, 1)))
    w = int(probe.textlength(chunk, font=efont))
    if w <= 0:
        return None
    # Emoji glyphs can overshoot the em box slightly; 1.3x covers it.
    tmp = Image.new('RGBA', (w, int(native * 1.3)), (0, 0, 0, 0))
    d = ImageDraw.Draw(tmp)
    try:
        d.text((0, 0), chunk, font=efont, embedded_color=True)
    except TypeError:
        d.text((0, 0), chunk, font=efont, fill=fill)
    if scale == 1.0:
        return tmp
    return tmp.resize((max(int(w * scale), 1), max(int(tmp.height * scale), 1)),
                      Image.LANCZOS)


def _draw_mixed(img, draw, xy, text, font, emoji_font, fill, outline=None):
    """Draw a line onto img, rendering emoji runs with the emoji font (in
    color if supported, rescaled when the font is a fixed-size bitmap).
    outline: optional (color, px) stroke drawn under the text.
    """
    x, y = xy
    stroke_w = outline[1] if outline else 0
    stroke_fill = outline[0] if outline else None
    for is_emoji, chunk in _split_emoji_runs(text):
        if is_emoji and emoji_font:
            scale = _emoji_scale(font, emoji_font)
            rendered = _render_emoji_chunk(chunk, emoji_font, scale, fill)
            if rendered is not None:
                img.alpha_composite(rendered, (int(x), int(y)))
                x += draw.textlength(chunk, font=emoji_font[0]) * scale
        else:
            if stroke_w:
                draw.text((x, y), chunk, font=font, fill=fill,
                          stroke_width=stroke_w, stroke_fill=stroke_fill)
            else:
                draw.text((x, y), chunk, font=font, fill=fill)
            x += draw.textlength(chunk, font=font)


def _break_long_word(draw, word, font, emoji_font, max_width):
    """Character-level hard wrap for a single word wider than max_width."""
    pieces = []
    current = ""
    for ch in word:
        if current and _measure_width(draw, current + ch, font, emoji_font) > max_width:
            pieces.append(current)
            current = ch
        else:
            current += ch
    if current:
        pieces.append(current)
    return pieces


FONT_DIR = "fonts"
FONT_PATH = os.path.join(FONT_DIR, "NotoSerif-Bold.ttf")

# Keep hook typography aligned with captions. Production images install
# Montserrat Bold; Windows development can use Arial Bold. The old bundled
# Noto Serif remains the final offline fallback, never the preferred face.
HOOK_FONT_CANDIDATES = [
    "/usr/local/share/fonts/klipin/Montserrat-Bold.ttf",
    "/usr/local/share/fonts/openshorts/Montserrat-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSerif-Bold.ttf",
    "C:\\Windows\\Fonts\\arialbd.ttf",
    "/mnt/c/Windows/Fonts/arialbd.ttf",
    FONT_PATH,
]


def download_font_if_needed():
    """Downloads a serif font for the hook text if not present."""
    # Production images already install Noto. Do not spend cold-start time or
    # network bandwidth downloading a duplicate fallback font.
    if any(os.path.isfile(path) for path in HOOK_FONT_CANDIDATES[:-1]):
        return
    if not os.path.exists(FONT_DIR):
        os.makedirs(FONT_DIR)
    if not os.path.exists(FONT_PATH):
        print(f"⬇️ Downloading font from {FONT_URL}...")
        try:
            # Add user agent to avoid 403s slightly
            req = urllib.request.Request(
                FONT_URL,
                headers={'User-Agent': 'Mozilla/5.0'}
            )
            with urllib.request.urlopen(req) as response, open(FONT_PATH, 'wb') as out_file:
                out_file.write(response.read())
            print("✅ Font downloaded.")
        except Exception as e:
            print(f"❌ Failed to download font: {e}")


# Hook visual styles. Each maps to box fill (RGBA, alpha 0 = no box), text
# color, and an optional text outline (color, px) for box-less looks.
HOOK_STYLES = {
    # White card, black serif text (original look).
    "classic": {"box": (255, 255, 255, 240), "text": (0, 0, 0), "outline": None, "shadow": True},
    # Dark card, white text.
    "dark":    {"box": (18, 18, 20, 235),    "text": (255, 255, 255), "outline": None, "shadow": True},
    # Bright yellow card, black text (high-contrast TikTok look).
    "yellow":  {"box": (255, 214, 0, 245),   "text": (0, 0, 0), "outline": None, "shadow": True},
    # Red "breaking" card, white text.
    "red":     {"box": (220, 38, 38, 245),   "text": (255, 255, 255), "outline": None, "shadow": True},
    # No box: white text with a thick black outline (caption/MrBeast style).
    "outline": {"box": (0, 0, 0, 0),         "text": (255, 255, 255), "outline": ((0, 0, 0), 8), "shadow": False},
    # No box: yellow text with black outline.
    "outline_yellow": {"box": (0, 0, 0, 0),  "text": (255, 214, 0),   "outline": ((0, 0, 0), 8), "shadow": False},
}


def create_hook_image(text, target_width, output_image_path="hook_overlay.png", font_scale=1.0, style="classic"):
    """
    Generates a hook overlay image using pixel-based wrapping.
    target_width: The max width the box should occupy (e.g. 85% of video)
    style: one of HOOK_STYLES keys, OR a dict
           {"box": (r,g,b,a), "text": (r,g,b), "outline": None|(color,px), "shadow": bool}
    """
    download_font_if_needed()

    if isinstance(style, dict):
        look = style
    else:
        look = HOOK_STYLES.get(style, HOOK_STYLES["classic"])
    box_fill = look["box"]
    text_fill = look["text"]
    outline = look["outline"]
    has_box = box_fill[3] > 0
    draw_shadow = look.get("shadow", True)

    # Configuration
    padding_x = 30  # Balanced padding
    padding_y = 25
    line_spacing = 20  # Increased spacing
    cornerradius = 20
    shadow_offset = (5, 5)
    shadow_blur = 10

    # A compact short-form scale: prominent enough to stop the scroll without
    # turning a two-line hook into a wall of text on a 1080px canvas.
    base_font_size = max(42, int(target_width * 0.062))
    font_size = int(base_font_size * font_scale)

    font = None
    for candidate in HOOK_FONT_CANDIDATES:
        if not os.path.isfile(candidate):
            continue
        try:
            font = ImageFont.truetype(candidate, font_size)
            break
        except Exception:
            continue
    if font is None:
        print("⚠️ Warning: no hook font found; using Pillow default")
        font = ImageFont.load_default()

    # Emoji handling: render with an emoji-capable font if one exists,
    # otherwise strip emoji instead of drawing tofu boxes.
    emoji_font = None
    if _EMOJI_RE.search(text):
        emoji_font = _load_emoji_font(font_size)
        if emoji_font is None:
            text = _EMOJI_RE.sub("", text)
            text = re.sub(r"[ \t]{2,}", " ", text).strip()

    # Wrap text logic (Pixel-based)
    dummy_img = Image.new('RGBA', (1, 1))
    draw = ImageDraw.Draw(dummy_img)

    max_text_width = target_width - (2 * padding_x)

    # Handle manual newlines first
    paragraphs = text.split('\n')
    lines = []

    for p in paragraphs:
        if not p.strip():
            lines.append("")
            continue

        words = p.split()
        current_line = []

        for word in words:
            # Test if adding word fits
            test_line = ' '.join(current_line + [word])
            w = _measure_width(draw, test_line, font, emoji_font)

            if w <= max_text_width:
                current_line.append(word)
                continue

            # Word doesn't fit on the current line
            if current_line:
                lines.append(' '.join(current_line))
                current_line = []

            if _measure_width(draw, word, font, emoji_font) <= max_text_width:
                current_line = [word]
            else:
                # Single word wider than the box: hard-wrap it character-wise
                # so it can't get cut off at the edges.
                pieces = _break_long_word(draw, word, font, emoji_font, max_text_width)
                lines.extend(pieces[:-1])
                current_line = [pieces[-1]] if pieces else []

        if current_line:
            lines.append(' '.join(current_line))

    # Recalculate true width/height
    max_line_width = 0
    text_heights = []

    for line in lines:
        if not line:
            text_heights.append(font_size)  # Use font size for empty line height
            continue

        w = _measure_width(draw, line, font, emoji_font)
        bbox = draw.textbbox((0, 0), line, font=font)
        h = bbox[3] - bbox[1]
        max_line_width = max(max_line_width, int(w))
        text_heights.append(h)

    # Box dimensions
    # We want the box to fit the text exactly + padding
    # Ensure min width for aesthetic reasons if text is short (at least 30% of target)
    box_width = max(max_line_width + (2 * padding_x), int(target_width * 0.3))

    # Total Text Height: sum(heights) + spacing * (n-1)
    if not text_heights:
        total_text_height = font_size
    else:
        total_text_height = sum(text_heights) + (len(text_heights) - 1) * line_spacing

    box_height = total_text_height + (2 * padding_y)

    # Create Final Image with Rounded Corners and Shadow
    # 1. Canvas for Shadow (larger than box)
    canvas_w = box_width + 40
    canvas_h = box_height + 40

    img = Image.new('RGBA', (canvas_w, canvas_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 2. Draw Shadow (only for boxed styles)
    if draw_shadow and has_box:
        shadow_box = [
            (20 + shadow_offset[0], 20 + shadow_offset[1]),
            (20 + box_width + shadow_offset[0], 20 + box_height + shadow_offset[1])
        ]
        draw.rounded_rectangle(shadow_box, radius=cornerradius, fill=(0, 0, 0, 100))
        # 3. Blur Shadow
        img = img.filter(ImageFilter.GaussianBlur(5))

    # 4. Draw Box (sharper, on top of blurred shadow)
    draw_final = ImageDraw.Draw(img)

    if has_box:
        main_box = [
            (20, 20),
            (20 + box_width, 20 + box_height)
        ]
        draw_final.rounded_rectangle(main_box, radius=cornerradius, fill=box_fill)

    # 5. Draw Text
    current_y = 20 + padding_y - 2  # Minor visual adjustment
    for i, line in enumerate(lines):
        if not line:
            current_y += font_size + line_spacing
            continue

        line_w = _measure_width(draw_final, line, font, emoji_font)
        bbox = draw_final.textbbox((0, 0), line, font=font)
        line_h = text_heights[i] if i < len(text_heights) else bbox[3] - bbox[1]

        # Center X
        x = 20 + int(box_width - line_w) // 2

        # Draw text in the style's color (emoji runs use the emoji font)
        _draw_mixed(img, draw_final, (x, current_y), line, font, emoji_font,
                    fill=text_fill, outline=outline)

        current_y += line_h + line_spacing

    img.save(output_image_path)
    return output_image_path, canvas_w, canvas_h


def add_hook_to_video(video_path, text, output_path, position="top", font_scale=1.0, duration=None, style="classic"):
    """
    Overlays text hook onto video.
    position: 'top', 'center', 'bottom'
    font_scale: float multiplier (1.0 = default)
    style: hook look (see HOOK_STYLES)
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video {video_path} not found")

    # 1. Probe video width to scale text properly
    try:
        cmd = ['ffprobe', '-v', 'error', '-show_entries', 'stream=width,height', '-of', 'csv=s=x:p=0', video_path]
        res = subprocess.check_output(cmd, timeout=60).decode().strip()
        # Takes first stream if multiple
        dims = res.split('\n')[0].split('x')
        video_width = int(dims[0])
        video_height = int(dims[1])
    except Exception as e:
        print(f"⚠️ FFprobe failed: {e}. Assuming 1080x1920")
        video_width = 1080
        video_height = 1920

    # 2. Generate Image
    # Box check: Don't let it be wider than 90% of screen
    target_box_width = int(video_width * 0.9)

    # Unique per invocation so parallel jobs can't overwrite each other's overlay.
    # The uuid alone guarantees that; the source name rides along only as a
    # debugging hint, so it is trimmed by BYTES (filesystems cap at 255 bytes,
    # and one Bengali or Arabic character costs three). Embedding it untrimmed
    # raised OSError 36 and killed the endpoint in prod on 26-jul-2026.
    stem = os.path.splitext(os.path.basename(video_path))[0]
    hook_filename = (f"temp_hook_{uuid.uuid4().hex[:8]}_"
                     f"{stem.encode('utf-8')[:80].decode('utf-8', 'ignore')}.png")

    try:
        img_path, box_w, box_h = create_hook_image(text, target_box_width, hook_filename, font_scale=font_scale, style=style)

        # 3. Calculate Overlay Position
        overlay_x = (video_width - box_w) // 2

        if position == "center":
            overlay_y = (video_height - box_h) // 2
        elif position == "bottom":
            # Bottom 20% mark (approx)
            overlay_y = int(video_height * 0.70)
        else:
            # Top 20% mark
            overlay_y = int(video_height * 0.20)

        # 4. FFmpeg Command
        print(f"🎬 Overlaying hook: '{text}' at {overlay_x},{overlay_y}")

        overlay_filter = f"[0:v][1:v]overlay={overlay_x}:{overlay_y}"
        if duration:
            overlay_filter += f":enable='between(t,0,{float(duration)})'"

        ffmpeg_cmd = [
            'ffmpeg', '-y',
            '-i', video_path,
            '-i', img_path,
            '-filter_complex', overlay_filter,
            '-c:a', 'copy',
            '-c:v', 'libx264', '-preset', 'medium', '-crf', '18',
            '-pix_fmt', 'yuv420p',
            '-movflags', '+faststart',
            output_path
        ]

        subprocess.run(ffmpeg_cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=1800)
        print(f"✅ Hook added to {output_path}")
        return True

    except subprocess.TimeoutExpired:
        print("❌ FFmpeg hook overlay timed out after 1800s.")
        raise RuntimeError("FFmpeg hook overlay timed out after 1800s.")
    except subprocess.CalledProcessError as e:
        print(f"❌ FFmpeg Error: {e.stderr.decode() if e.stderr else 'Unknown'}")
        raise e
    except Exception as e:
        print(f"❌ Hook Gen Error: {e}")
        raise e
    finally:
        # Cleanup temp image
        if os.path.exists(hook_filename):
            os.remove(hook_filename)


def build_hook_v2_text(hook_v2: dict) -> str:
    """Build hook text from hook_v2 structure.
    
    hook_v2 format:
    {
        "headline": "Main hook text",
        "subtext": "Optional subtext",
        "style": "classic|dark|yellow|red|outline|outline_yellow"
    }
    """
    if not hook_v2:
        return ""
    headline = hook_v2.get("headline", "")
    subtext = hook_v2.get("subtext", "")
    if subtext:
        return f"{headline}\n{subtext}"
    return headline

# ─── Hook V2 intro (restored — ops.py imports these) ────────────────────
# Removed by the OpenShorts port; ops.py still imports render_hook_v2_intro.
# Adapted to current API: extract the segment with extract_clip() first (the
# old clip_start/clip_end kwargs no longer exist on reframe_clip/crop_video).

def build_transition_filter(trans_type: str, duration: float = 0.12,
                            intensity: float = 1.0) -> str:
    """FFmpeg filtergraph fragment for a Hook V2 transition."""
    if trans_type == "white_flash":
        return (
            f"color=white:d={duration}[wf];"
            f"[0:v][wf]blend=all_mode='addition':all_opacity="
            f"if(lt(T,{duration}),T/{duration},0)[v0];"
            f"[v0][1:v]blend=all_mode='addition':all_opacity="
            f"if(lt(T,{duration}),1-T/{duration},1)"
        )
    if trans_type == "glitch":
        return (
            f"noise=alls={int(intensity * 15)}:allf=t+u;"
            f"rgbshift=r=-{int(intensity * 5)}:g=0:b={int(intensity * 5)};"
            f"spp=quality={int(10 - intensity * 5)}"
        )
    return ""  # cut — no transition


def render_hook_v2_intro(source_video: str, hook_v2: dict, work_dir: str,
                         aspect: str, options: dict, sink=None,
                         rank: int = 1) -> str:
    """Render Hook V2 multi-hook intro; return path to the intro video."""
    if not hook_v2 or not hook_v2.get("enabled"):
        return ""
    items = hook_v2.get("items") or []
    if not items:
        return ""

    from video import run, get_duration, extract_clip

    trans_type = hook_v2.get("transition", {}).get("type", "white_flash")
    trans_dur = float(options.get("white_flash_duration", 0.12))
    trans_intensity = float(options.get("glitch_intensity", 1.0))

    segment_paths = []
    for i, item in enumerate(items):
        start = float(item.get("start_time", 0))
        end = float(item.get("end_time", start + 1.0))
        if end <= start:
            continue
        raw_path = os.path.join(work_dir, f"hook_v2_r{rank}_item{i}_raw.mp4")
        silent_path = os.path.join(work_dir, f"hook_v2_r{rank}_item{i}.mp4")
        extract_clip(source_video, start, end, raw_path, sink)
        from reframe import reframe_clip
        try:
            reframe_clip(raw_path, silent_path, aspect=aspect, strategy="track",
                         log=sink, work_dir=work_dir)
        except Exception as e:
            sink and sink(f"⚠️ HookV2 item {i} reframe failed, fallback center: {e}")
            from video import crop_video
            crop_video(raw_path, silent_path, mode="center", aspect=aspect, log=sink)
        if os.path.exists(raw_path):
            os.remove(raw_path)

        text = (item.get("text", "") or "").strip()
        if text:
            from subtitles import burn_subtitles
            ass_path = os.path.join(work_dir, f"hook_v2_r{rank}_item{i}.ass")
            with open(ass_path, "w", encoding="utf-8") as f:
                f.write(f"""[Script Info]
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: HookV2,Montserrat,104,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,-0.5,0,1,4,2,5,70,70,120,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:{int(end - start):02d}:00.00,HookV2,,0,0,0,,{{\an5\fad(100,100)}}{text}
""")
            txt_path = os.path.join(work_dir, f"hook_v2_r{rank}_item{i}_txt.mp4")
            burn_subtitles(silent_path, ass_path, txt_path, log=sink,
                           watermark=options.get("watermark", ""),
                           title_overlay="", hook_overlay="", free_tier=False)
            segment_paths.append(txt_path)
        else:
            segment_paths.append(silent_path)

    if not segment_paths:
        return ""
    if len(segment_paths) == 1:
        return segment_paths[0]

    final_parts = []
    for i in range(len(segment_paths)):
        final_parts.append(segment_paths[i])
        if i < len(segment_paths) - 1:
            trans_path = os.path.join(work_dir, f"hook_v2_r{rank}_trans{i}.mp4")
            create_transition_clip(segment_paths[i], segment_paths[i + 1],
                                   trans_path, trans_type, trans_dur,
                                   trans_intensity, sink)
            if not os.path.exists(trans_path):
                continue  # cut transitions produce no clip
            final_parts.append(trans_path)

    final_path = os.path.join(work_dir, f"hook_v2_rank{rank}.mp4")
    list_file = os.path.join(work_dir, f"hook_v2_r{rank}_concat.txt")
    with open(list_file, "w", encoding="utf-8") as f:
        for p in final_parts:
            f.write(f"file '{p}'\n")
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "concat", "-safe", "0", "-i", list_file,
         "-c", "copy", "-vsync", "vfr", final_path], sink)
    for p in segment_paths:
        if os.path.exists(p):
            os.remove(p)
    if os.path.exists(list_file):
        os.remove(list_file)
    sink and sink(f"🎬 HookV2 intro rendered: {os.path.basename(final_path)} "
                  f"({len(items)} items)")
    return final_path


def create_transition_clip(prev_path: str, next_path: str, out_path: str,
                           trans_type: str, duration: float, intensity: float,
                           sink=None) -> None:
    """Create a transition clip between two videos (no-op for cut)."""
    if trans_type == "cut":
        return
    from video import run, get_duration
    filter_str = build_transition_filter(trans_type, duration, intensity)
    if not filter_str:
        return
    prev_dur = get_duration(prev_path) or 5.0
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-i", prev_path, "-i", next_path,
         "-filter_complex",
         f"[0:v]trim=end={prev_dur}[v0];[1:v]trim=start=0:end={duration}[v1];"
         f"{filter_str}",
         "-t", str(duration),
         "-c:v", "libx264", "-preset", "fast", "-crf", "23",
         "-an", out_path], sink)
