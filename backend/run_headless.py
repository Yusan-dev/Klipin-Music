"""Headless Klipin renderer — run the full pipeline from the CLI with no web app.

Works on any CPU machine: ffmpeg always encodes on CPU (libx264) — local
GPU encoding is contract-forbidden, NVENC runs only on the Modal L4 worker.
Face tracking uses OpenCV haarcascade, transcription
uses Groq/whisper (cloud API) and clip selection uses Gemini (cloud API).
Designed for local runs and VPS batch jobs.

Usage:
    python run_headless.py <video> [options]          # render a local file
    python run_headless.py --youtube <URL> [options]  # download YouTube then render

Options (same keys as the web UI):
    --layout auto|general|center|face|split|panel|alternate|gameplay|screencast|square
    --aspect 9:16|1:1|16:9
    --animation none|karaoke|...        (see subtitles.ANIMATION_TAGS)
    --color #FFFFFF
    --karaoke-color #FFE500
    --caption-style solid|highlight|outline|gaming|box
    --caption-bg solid|glass|none
    --caption-font Inter|Anton|Bebas Neue|...
    --caption-size 96
    --emoji 0|1
    --translate-to id|en|original
    --no-emoji
    --bgm none|upbeat|...              (see bgm module moods)
    --hook-overlay
    --watermark <text>
    --out <dir>                        (default: ./klipin_out_<timestamp>)

Env vars (recommended for servers):
    GEMINI_API_KEY   required for clip selection
    GROQ_API_KEY     optional — falls back to local faster-whisper if unset
    WHISPER_MODEL    small|base|medium (default small)
    TRANSCRIBE_BACKEND groq|whisper

Outputs: clip_N.mp4 (1080p vertical), clip_N.ass, plus raw/vertical
intermediates and a result.json describing every clip.
"""
import argparse
import json
import os
import sys
import time

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

# Ensure ffmpeg/ffprobe from imageio-ffmpeg are on PATH (for systems without a
# system-wide ffmpeg install). Without this, every subprocess.run(["ffmpeg", ...])
# raises FileNotFoundError on Windows where ffmpeg is not on the global PATH.
try:
    import imageio_ffmpeg
    _ffdir = os.path.dirname(imageio_ffmpeg.get_ffmpeg_exe())
    os.environ["PATH"] = _ffdir + os.pathsep + os.environ.get("PATH", "")
except Exception:
    pass

from pipeline import run_pipeline  # noqa: E402


def _json_flag(v):
    if v is None:
        return None
    return bool(int(v))


def main():
    p = argparse.ArgumentParser(description="Headless Klipin render")
    p.add_argument("video", nargs="?", default="",
                   help="Path to the source video file (skip when --youtube is used)")
    p.add_argument("--youtube", default="",
                   help="YouTube URL to download & render instead of a local file")
    p.add_argument("--layout", default="auto")
    p.add_argument("--aspect", default="9:16")
    p.add_argument("--animation", default="karaoke")
    p.add_argument("--color", default=None)
    p.add_argument("--karaoke-color", default=None)
    p.add_argument("--caption-style", default="solid")
    p.add_argument("--caption-bg", default="solid")
    p.add_argument("--caption-font", default="Inter")
    p.add_argument("--caption-size", type=int, default=96)
    p.add_argument("--emoji", type=_json_flag, default=None)
    p.add_argument("--translate-to", default="")
    p.add_argument("--bgm", default="none")
    p.add_argument("--hook-overlay", type=_json_flag, default=False)
    p.add_argument("--watermark", default="")
    p.add_argument("--out", default="")
    args = p.parse_args()

    if args.youtube:
        source = {"type": "youtube", "url": args.youtube}
        print(f"🔗 YouTube source: {args.youtube}")
    else:
        if not args.video:
            print("❌ Provide <video> path or --youtube URL", file=sys.stderr)
            sys.exit(2)
        if not os.path.exists(args.video):
            print(f"❌ Video not found: {args.video}", file=sys.stderr)
            sys.exit(2)
        source = {"type": "upload", "path": args.video}
        print(f"🎞️ Local source: {args.video}")

    out_dir = args.out or f"klipin_out_{int(time.time())}"
    os.makedirs(out_dir, exist_ok=True)

    options = {
        "layout": args.layout,
        "aspect": args.aspect,
        "animation": args.animation,
        "caption_style": args.caption_style,
        "caption_bg": args.caption_bg,
        "caption_font": args.caption_font,
        "caption_size": args.caption_size,
        "bgm": args.bgm,
        "hook_overlay": args.hook_overlay,
        "watermark": args.watermark,
        "translate_to": args.translate_to,
    }
    if args.color:
        options["color"] = args.color
    if args.karaoke_color:
        options["karaoke_color"] = args.karaoke_color
    if args.emoji is not None:
        options["emoji"] = args.emoji

    def sink(msg):
        print(msg, flush=True)

    print(f"🚀 Rendering → {out_dir}  (CPU-only, this may take a while)")
    t0 = time.time()
    try:
        result = run_pipeline(source, out_dir, options, sink=sink)
    except Exception as e:  # noqa: BLE001
        print(f"❌ Pipeline failed: {e}", file=sys.stderr)
        sys.exit(1)

    meta_path = os.path.join(out_dir, "result.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)

    print(f"\n✅ Done in {time.time() - t0:.0f}s → {out_dir}")
    for c in result.get("clips", []):
        url = c.get("video_url", "")
        name = os.path.basename(url)
        print(f"   • {name}  {c.get('size_mb', 0)} MB  score={c.get('hook_score', c.get('predicted_score', '?'))}")
    print(f"   Transcript + clips meta: {meta_path}")


if __name__ == "__main__":
    main()