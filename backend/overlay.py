"""Insert-media overlays — ClipperStudio-style manual editor for Klipin.

Three zero-cost insert types on an already-rendered clip:
  text  — burn a styled text line for a time window (drawtext)
  image — overlay an uploaded PNG/JPG for a time window
  audio — mix an uploaded audio file under the original audio at an offset

The result swaps into the clip slot in place, keeping the same /videos URL.
"""
import os
import shutil

from gpu import encode_args
from subtitles import _drawtext_escape
from video import get_duration, run

POSITIONS = {
    "top": "y=120",
    "center": "y=(h-text_h)/2",
    "bottom": "y=h-text_h-220",
}


def run_proxy(job_id: str, clip_idx: int, out_root: str, sink=None) -> dict:
    """Create a lightweight portrait editing proxy without touching export."""
    src = os.path.join(out_root, job_id, f"clip_{clip_idx + 1}.mp4")
    out = os.path.join(out_root, job_id, f"clip_{clip_idx + 1}_proxy.mp4")
    if not os.path.exists(src):
        raise RuntimeError("Clip file no longer available")
    if not os.path.exists(out) or os.path.getmtime(out) < os.path.getmtime(src):
        run(["ffmpeg", "-y", "-i", src, "-vf", "scale=360:640:flags=fast_bilinear,setsar=1",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "30",
             "-c:a", "aac", "-b:a", "64k", "-movflags", "+faststart", out], sink)
    return {"clip_idx": clip_idx,
            "proxy_url": f"/videos/{job_id}/clip_{clip_idx + 1}_proxy.mp4"}


def _ass_color_hex(color: str) -> str:
    # Accept #RRGGBB; drawtext wants the same hex without alpha (0xRRGGBB).
    c = (color or "#FFFFFF").lstrip("#")
    if len(c) != 6:
        c = "FFFFFF"
    return "0x" + c


def _apply_text(src: str, out: str, spec: dict, log=None) -> str:
    text = (spec.get("text") or "").strip()
    if not text:
        raise RuntimeError("Text overlay is empty")
    start = max(0.0, float(spec.get("start", 0.0)))
    end = max(start + 0.5, float(spec.get("end", start + 2.0)))
    size = max(24, min(160, int(spec.get("font_size", 64))))
    color = _ass_color_hex(spec.get("color", "#FFE500"))
    position = spec.get("position", "bottom")
    if spec.get("x") is not None:
        x = f"(w-text_w)*{max(0,min(1,float(spec.get('x',.5)))):.4f}"
    else:
        x = "(w-text_w)/2"
    if spec.get("y") is not None:
        y = f"(h-text_h)*{max(0,min(1,float(spec.get('y',.5)))):.4f}"
    else:
        y = POSITIONS.get(position, POSITIONS["bottom"]).removeprefix("y=")
    alpha = (
        f"if(lt(t,{start:.2f}),0,"
        f"if(lt(t,{start + 0.3:.2f}),(t-{start:.2f})/0.3,"
        f"if(lt(t,{end:.2f}),1,"
        f"if(lt(t,{end + 0.3:.2f}),({end:.2f}+0.3-t)/0.3,0))))"
    )
    vf = (
        f"drawtext=text='{_drawtext_escape(text[:120])}':"
        f"fontsize={size}:fontcolor={color}:borderw=4:bordercolor=black@0.9:"
        f"x={x}:y={y}:alpha='{alpha}'"
    )
    log and log(f"💬 Insert text: {text[:40]}")
    run([
        "ffmpeg", "-y", "-i", src, "-vf", vf,
        *encode_args(log=log),
        "-c:a", "copy", "-movflags", "+faststart", out,
    ], log)
    return out


def _apply_image(src: str, out: str, image_path: str, spec: dict,
                 log=None) -> str:
    if not os.path.exists(image_path):
        raise RuntimeError("Insert image file no longer available")
    start = max(0.0, float(spec.get("start", 0.0)))
    end = max(start + 0.5, float(spec.get("end", start + 2.0)))
    width_pct = max(5.0, min(100.0, float(
        spec.get("width_pct", float(spec.get("width", .6)) * 100))))
    x_norm = max(0.0, min(1.0, float(spec.get("x", .5))))
    y_norm = max(0.0, min(1.0, float(spec.get("y", .5))))
    x = f"(W-w)*{x_norm:.4f}"
    y = f"(H-h)*{y_norm:.4f}"
    opacity = max(0.0, min(1.0, float(spec.get("opacity", 1.0))))
    rotation = max(-180.0, min(180.0, float(spec.get("rotation", 0.0))))
    log and log(f"🖼️ Insert image: {os.path.basename(image_path)}")
    dur = get_duration(src) or 5.0
    run([
        "ffmpeg", "-y", "-i", src, "-loop", "1", "-i", image_path,
        "-filter_complex",
        (f"[1:v]scale={width_pct:.0f}*iw/100:-1,format=rgba,"
         f"colorchannelmixer=aa={opacity:.3f},"
         f"rotate={rotation:.2f}*PI/180:ow=rotw(iw):oh=roth(ih):c=none[img];"
         f"[0:v][img]overlay={x}:{y}:"
         f"enable='between(t,{start:.2f},{end:.2f})':shortest=1[v]"),
        "-map", "[v]", "-map", "0:a",
        "-t", f"{dur:.3f}",
        *encode_args(log=log),
        "-c:a", "copy", "-movflags", "+faststart", out,
    ], log)
    return out


def _apply_audio(src: str, out: str, audio_path: str, spec: dict,
                 log=None) -> str:
    if not os.path.exists(audio_path):
        raise RuntimeError("Insert audio file no longer available")
    start = max(0.0, float(spec.get("start", 0.0)))
    volume = max(0.0, min(1.5, float(spec.get("volume", 0.3))))
    keep_original = bool(spec.get("keep_original", True))
    log and log(f"🔊 Insert audio: {os.path.basename(audio_path)}")
    dur = get_duration(src) or 5.0
    if keep_original:
        filt = (
            f"[1:a]adelay={int(start * 1000)}|{int(start * 1000)},"
            f"volume={volume:.2f}[ins];"
            f"[0:a][ins]amix=inputs=2:duration=first:normalize=0[a]"
        )
    else:
        filt = (
            f"[1:a]adelay={int(start * 1000)}|{int(start * 1000)},"
            f"volume={volume:.2f}[a]"
        )
    run([
        "ffmpeg", "-y", "-i", src, "-i", audio_path,
        "-filter_complex", filt,
        "-map", "0:v", "-map", "[a]",
        "-t", f"{dur:.3f}",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart", out,
    ], log)
    return out


def _apply_video(src: str, out: str, video_path: str, spec: dict,
                 log=None) -> str:
    if not os.path.exists(video_path):
        raise RuntimeError("B-roll file no longer available")
    start = max(0.0, float(spec.get("start", 0.0)))
    end = max(start + 0.5, float(spec.get("end", start + 4.0)))
    x = max(0.0, min(1.0, float(spec.get("x", .5))))
    y = max(0.0, min(1.0, float(spec.get("y", .5))))
    width = max(.1, min(1.0, float(spec.get("width", 1.0))))
    opacity = max(0.0, min(1.0, float(spec.get("opacity", 1.0))))
    fade = min(.3, max(.05, (end - start) / 4))
    dur = get_duration(src) or 5.0
    filt = (
        f"[1:v]setpts=PTS-STARTPTS,scale=1080*{width:.4f}:-2,"
        f"format=rgba,colorchannelmixer=aa={opacity:.4f},"
        f"fade=t=in:st=0:d={fade:.2f}:alpha=1,"
        f"fade=t=out:st={max(0.0,end-start-fade):.2f}:d={fade:.2f}:alpha=1[b];"
        f"[0:v][b]overlay=(W-w)*{x:.4f}:(H-h)*{y:.4f}:"
        f"enable='between(t,{start:.3f},{end:.3f})':eof_action=pass[v]"
    )
    log and log(f"🎞️ B-roll layer: {os.path.basename(video_path)}")
    run(["ffmpeg", "-y", "-i", src, "-stream_loop", "-1", "-i", video_path,
         "-filter_complex", filt, "-map", "[v]", "-map", "0:a?",
         "-t", f"{dur:.3f}", *encode_args(log=log), "-c:a", "copy",
         "-movflags", "+faststart", out], log)
    return out


def run_overlay(job_id: str, clip_idx: int, out_root: str, spec: dict,
                sink=None) -> dict:
    """Apply an insert-media overlay to a clip. Returns updated clip dict."""
    import db
    clips = db.get_clips(job_id)
    if clip_idx < 0 or clip_idx >= len(clips):
        raise RuntimeError(f"Clip index out of range: {clip_idx}")
    src = os.path.join(out_root, job_id, f"clip_{clip_idx + 1}.mp4")
    if not os.path.exists(src):
        raise RuntimeError("Clip file no longer available on disk")

    kind = spec.get("kind", "text")
    out = os.path.join(out_root, job_id, f"clip_{clip_idx + 1}_ov.mp4")
    if kind == "image":
        _apply_image(src, out, spec.get("media_path", ""), spec, sink)
    elif kind == "audio":
        _apply_audio(src, out, spec.get("media_path", ""), spec, sink)
    else:
        _apply_text(src, out, spec, sink)

    if not os.path.exists(out):
        raise RuntimeError("Overlay render produced no file")

    final = os.path.join(out_root, job_id, f"clip_{clip_idx + 1}.mp4")
    os.replace(out, final)
    rev = db.bump_clip_rev(job_id, clip_idx)
    size_mb = round(os.path.getsize(final) / 1e6, 1)
    db.update_clip(job_id, clip_idx, size_mb=size_mb, rev=rev)
    sink and sink("✅ Insert media applied")
    return {
        "clip_idx": clip_idx,
        "kind": kind,
        "rev": rev,
        "size_mb": size_mb,
        "video_url": f"/videos/{job_id}/clip_{clip_idx + 1}.mp4",
    }


def run_layer_plan(job_id: str, clip_idx: int, out_root: str, sink=None) -> dict:
    """Render the saved non-destructive layer plan.

    The first invocation snapshots a clean base clip. Subsequent layer edits
    rebuild from that base, so moving a text layer never burns duplicates.
    B-roll slots without an asset remain metadata until Auto B-roll resolves
    them; text/channel and uploaded image/audio layers render immediately.
    """
    import db
    clips = db.get_clips(job_id)
    if clip_idx < 0 or clip_idx >= len(clips):
        raise RuntimeError(f"Clip index out of range: {clip_idx}")
    clip = clips[clip_idx]
    layers = clip.get("layer_plan") or []
    final = os.path.join(out_root, job_id, f"clip_{clip_idx + 1}.mp4")
    base = os.path.join(out_root, job_id, f"clip_{clip_idx + 1}_layer_base.mp4")
    if not os.path.exists(final):
        raise RuntimeError("Clip file no longer available on disk")
    if not os.path.exists(base):
        shutil.copy2(final, base)
    current = base
    generated = []
    for index, layer in enumerate(layers):
        if not layer.get("visible", True):
            continue
        kind = layer.get("kind")
        if kind == "broll" and not layer.get("media_path"):
            sink and sink(f"⏭️ B-roll slot {index + 1} menunggu footage")
            continue
        if kind not in ("text", "channel", "image", "audio", "broll"):
            continue
        out = os.path.join(out_root, job_id, f".layer_{clip_idx + 1}_{index}.mp4")
        spec = dict(layer)
        if kind in ("text", "channel"):
            _apply_text(current, out, spec, sink)
        elif kind == "image":
            _apply_image(current, out, spec.get("media_path", ""), spec, sink)
        elif kind == "audio":
            _apply_audio(current, out, spec.get("media_path", ""), spec, sink)
        elif kind == "broll":
            _apply_video(current, out, spec.get("media_path", ""), spec, sink)
        else:
            continue
        generated.append(out)
        current = out
    if current == base:
        shutil.copy2(base, final)
    else:
        os.replace(current, final)
    for path in generated[:-1]:
        if os.path.exists(path):
            os.remove(path)
    rev = db.bump_clip_rev(job_id, clip_idx)
    size_mb = round(os.path.getsize(final) / 1e6, 1)
    db.update_clip(job_id, clip_idx, size_mb=size_mb, rev=rev)
    sink and sink("✅ Layer plan rendered at 1080×1920")
    return {"clip_idx": clip_idx, "rev": rev, "size_mb": size_mb,
            "layers": layers,
            "video_url": f"/videos/{job_id}/clip_{clip_idx + 1}.mp4"}
