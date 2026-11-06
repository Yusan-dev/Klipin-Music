"""Speech cleanup — filler words, dead air, and bad takes removal.

Ported from QuClippers `filler_removal.py`. Klipin's Groq transcript only has
segment-level timestamps (no per-word times), so:
  - word timestamps are estimated by distributing each segment's words evenly
    across the segment (accurate enough to locate single-word fillers), and
  - silences are detected from the REAL gaps between transcript segments
    (Whisper segments are separated by actual pauses).

Bad takes (stumbles, false starts, repetitions, self-corrections) are detected
by Gemini analyzing the transcript text + timestamps, matching Opus Clip's
"Speech cleanup (bad takes removal)" feature.

Removal is done with ffmpeg `select`/`aselect` filters — no re-transcription,
no ML, runs in seconds.
"""
import json
import os
import shutil
import subprocess

from gpu import encode_args


def _extract_json(text: str) -> dict:
    """Pull a JSON object out of a model response (tolerant of fences)."""
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1]
        t = t.lstrip("json").strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    start, end = t.find("{"), t.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(t[start:end + 1])
        except json.JSONDecodeError:
            pass
    return {}

FILLER_WORDS = {
    # English
    "um", "uh", "like", "you", "know", "i", "mean", "so", "basically",
    "literally", "right", "actually", "hmm", "umm", "uhh", "ahh",
    # Indonesian
    "eh", "anu", "gitu", "kan", "ya", "kan", "emm", "nah", "tuh",
    "apa", "ya", "gimana", "ya", "hmm", "ah", "oh", "ehm", "ee",
}


def _word_timestamps(segments: list) -> list:
    """Build [{word, start, end}] from segments, distributing words evenly."""
    out = []
    for seg in segments:
        start = seg.get("start", 0)
        end = seg.get("end", 0)
        if end <= start:
            continue
        words = (seg.get("text") or "").split()
        if not words:
            continue
        step = (end - start) / len(words)
        for i, w in enumerate(words):
            w0 = start + i * step
            out.append({"word": w, "start": w0, "end": w0 + step})
    return out


def detect_fillers(words: list) -> list:
    fillers = []
    for item in words:
        word = item.get("word", "").strip().lower().strip(".,!?;:…")
        clean = "".join(c for c in word if c.isalnum() or c.isspace())
        if clean in FILLER_WORDS:
            fillers.append({"start": item["start"], "end": item["end"]})
    return fillers


def detect_silences(segments: list, threshold_sec: float = 1.0) -> list:
    """Real pauses = gaps between consecutive transcript segments."""
    silences = []
    for i in range(len(segments) - 1):
        end_current = segments[i].get("end", 0)
        start_next = segments[i + 1].get("start", 0)
        if start_next - end_current >= threshold_sec:
            silences.append({"start": end_current, "end": start_next})
    return silences


def detect_bad_takes(segments: list, log=None) -> list:
    """Use Gemini to identify bad takes in the transcript.

    Bad takes include: stumbles, false starts, self-corrections,
    repeated phrases, aborted sentences, and "let me rephrase" moments.

    Returns [{start, end, reason}] for each bad-take region.
    """
    if not segments:
        return []

    # Build a numbered transcript with timestamps for Gemini
    numbered = []
    for i, seg in enumerate(segments):
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        s = seg.get("start", 0)
        e = seg.get("end", 0)
        m_s, s_s = divmod(int(s), 60)
        m_e, s_e = divmod(int(e), 60)
        numbered.append(
            f"[{i}] {m_s}:{s_s:02d}-{m_e}:{s_e:02d} {text}"
        )

    if len(numbered) < 3:
        return []

    try:
        from google import genai
        key = os.environ.get("GEMINI_API_KEY", "")
        if not key:
            log and log("⚠️ Bad takes: GEMINI_API_KEY not set, skipping")
            return []
        client = genai.Client(api_key=key)
        model = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")
    except Exception:
        return []

    log and log(f"🧠 Bad takes: analyzing {len(numbered)} transcript segments...")

    prompt = (
        "You are a professional video editor identifying BAD TAKES in a transcript.\n"
        "\n"
        "A bad take is any section where the speaker:\n"
        "- Stumbles or restarts a sentence (e.g. 'So I... so basically I went')\n"
        "- Repeats themselves unnecessarily (e.g. 'It was great it was really great')\n"
        "- Self-corrects mid-sentence (e.g. 'The blue... I mean red car')\n"
        "- Has a false start (e.g. 'What I... never mind, the thing is')\n"
        "- Says 'let me start over' or equivalent\n"
        "\n"
        "Do NOT flag:\n"
        "- Natural pauses or thinking sounds (handled separately)\n"
        "- Deliberate repetition for emphasis\n"
        "- Normal speech patterns or filler words (handled separately)\n"
        "- Brief hesitations that are natural in conversation\n"
        "\n"
        "For EACH bad take found, return the segment index range [start_idx, end_idx]\n"
        "that should be removed, and a brief reason.\n"
        "\n"
        "RULES:\n"
        "- Each region must be at least 2 consecutive segments (0.5s+)\n"
        "- Do NOT remove the ENTIRE transcript — only clear mistakes\n"
        "- Keep all segments that form a coherent thought\n"
        "\n"
        "Return STRICT JSON: {\"bad_takes\": [{\"start_idx\": N, \"end_idx\": M, \"reason\": \"...\"}]}\n"
        "If there are no bad takes, return: {\"bad_takes\": []}\n\n"
        "TRANSCRIPT:\n\n"
        + "\n".join(numbered)
    )

    try:
        resp = client.models.generate_content(
            model=model, contents=prompt,
            config={"response_mime_type": "application/json"},
        )
        data = _extract_json(resp.text)
    except Exception as e:  # noqa: BLE001
        log and log(f"⚠️ Bad takes detection failed: {e}")
        return []

    bad_takes_raw = data.get("bad_takes") or []
    if not isinstance(bad_takes_raw, list):
        return []

    # Convert index ranges to time ranges using the segments list
    results = []
    for bt in bad_takes_raw:
        si = int(bt.get("start_idx", -1))
        ei = int(bt.get("end_idx", -1))
        reason = bt.get("reason", "bad take")
        if si < 0 or ei < 0 or si > ei or si >= len(segments):
            continue
        ei = min(ei, len(segments) - 1)
        start = segments[si].get("start", 0)
        end = segments[ei].get("end", 0)
        if end - start >= 0.5:
            results.append({"start": start, "end": end, "reason": reason})

    log and log(f"🧠 Bad takes: found {len(results)} region(s) to remove")
    return results


def _clean_filter(fillers: list, silences: list, bad_takes: list = None,
                  offset: float = 0.0) -> str:
    to_remove = fillers + silences + (bad_takes or [])
    if not to_remove:
        return ""
    # Timestamps are source-relative; the clip timeline starts at 0 after
    # extraction from c0, so shift by `offset` (the clip start).
    exprs = [f"between(t,{r['start'] - offset:.3f},{r['end'] - offset:.3f})"
             for r in to_remove]
    select_expr = f"not({' + '.join(exprs)})"
    return (
        f"[0:v]select='{select_expr}',setpts=N/FRAME_RATE/TB[vout];"
        f"[0:a]aselect='{select_expr}',asetpts=N/SR/TB[aout]"
    )


def clip_start_end(clips: list, clip_idx: int) -> tuple:
    clip = clips[clip_idx] if 0 <= clip_idx < len(clips) else {}
    return (float(clip.get("start", 0)), float(clip.get("end", 60)))


def _clamp(ranges: list, lo: float, hi: float) -> list:
    out = []
    for r in ranges:
        s, e = max(lo, r["start"]), min(hi, r["end"])
        if e - s >= 0.08:  # keep only meaningful removals
            out.append({"start": s, "end": e})
    return out


def clean_clip(input_path: str, output_path: str, segments: list,
               remove_fillers: bool = True, remove_silences: bool = True,
               remove_bad_takes: bool = False, silence_threshold: float = 1.0,
               log=None, clip_bounds: tuple | None = None) -> str:
    """Remove filler words, dead air, and/or bad takes from a clip video."""
    log = log or (lambda m: None)
    lo, hi = clip_bounds or (0.0, 1e9)
    words = _word_timestamps(segments or [])
    fillers = detect_fillers(words) if remove_fillers else []
    silences = (detect_silences(segments or [], silence_threshold)
                if remove_silences else [])
    bad_takes = detect_bad_takes(segments or [], log=log) if remove_bad_takes else []
    fillers = _clamp(fillers, lo, hi)
    silences = _clamp(silences, lo, hi)
    bad_takes = _clamp(bad_takes, lo, hi)
    log(f"✂️ Speech cleanup: {len(fillers)} filler(s), "
        f"{len(silences)} silence(s), {len(bad_takes)} bad take(s)")

    if not fillers and not silences and not bad_takes:
        shutil.copy2(input_path, output_path)
        log("✂️ Nothing to remove — clip copied unchanged")
        return output_path

    filtergraph = _clean_filter(fillers, silences, bad_takes, offset=lo)
    if not filtergraph:
        shutil.copy2(input_path, output_path)
        return output_path

    log("🎞️ Rendering cleaned clip...")
    r = subprocess.run(
        ["ffmpeg", "-y", "-i", input_path, "-filter_complex", filtergraph,
         "-map", "[vout]", "-map", "[aout]",
         "-vsync", "vfr",
         *encode_args(crf=23, log=None),
         "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
         output_path],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise RuntimeError(f"speech cleanup failed: {r.stderr[-500:]}")
    log("✅ Clip cleaned")
    return output_path


def run_clean(job_id: str, clip_idx: int, out_root: str = "",
              remove_fillers: bool = True, remove_silences: bool = True,
              remove_bad_takes: bool = False, sink=None) -> dict:
    """Job entrypoint: produce a speech-cleaned copy of one clip.

    Toggles:
      remove_fillers  — cut filler words (um, eh, anu, gitu…)
      remove_silences — cut dead air (pauses ≥ 1s between segments)
      remove_bad_takes — cut stumbles, false starts, repetitions (Gemini AI)
    """
    import db

    project = db.get_project(job_id)
    if not project:
        raise RuntimeError("Project not found")
    clips = project.get("clips") or []
    if clip_idx < 0 or clip_idx >= len(clips):
        raise RuntimeError(f"Clip index out of range: {clip_idx}")

    log = sink or (lambda m: None)
    src = os.path.join(out_root, job_id, f"clip_{clip_idx + 1}.mp4")
    if not os.path.exists(src):
        raise RuntimeError("Clip video no longer available on disk")
    out = os.path.join(out_root, job_id, f"clip_{clip_idx + 1}_clean.mp4")
    segments = (project.get("transcript") or {}).get("segments") or []

    # Only consider transcript material inside the clip's window (with a
    # 1s margin), so fillers/silences from elsewhere never cut clip edges.
    c0, c1 = clip_start_end(clips, clip_idx)
    segs = [s for s in segments if s.get("end", 0) > c0 - 1
            and s.get("start", 0) < c1 + 1]
    clean_clip(src, out, segs, remove_fillers=remove_fillers,
               remove_silences=remove_silences, remove_bad_takes=remove_bad_takes,
               log=log, clip_bounds=(c0, c1))
    return {
        "clip_idx": clip_idx,
        "video_url": f"/videos/{job_id}/clip_{clip_idx + 1}_clean.mp4",
        "size_mb": round(os.path.getsize(out) / 1e6, 1),
    }
