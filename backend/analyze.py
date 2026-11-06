"""Gemini-powered viral clip selection for Klipin.

Two-stage transcript-first scoring (borrowed from the original Klipin engine):
  Stage 1 — coarse pass: timestamped transcript only, Gemini returns
            candidate windows with a coarse `predicted_score` (0-100).
  Stage 2 — detail pass: the top candidates are elaborated (no images, so
            the extra call is cheap) into final titles/hooks/timings with a
            refined score, using a JSON schema the API enforces.
  Word snapping — every candidate's start/end is snapped to the nearest
  transcript word boundary so subtitles never start/end mid-word.

Duration policy (ported from QuClippers build_duration_preference / OpenShorts):
the length preset is a *preference*, never a hard cut. If a viral moment needs
more seconds to land its payoff (e.g. everything from 5:30 to 6:15), the
selector MUST keep the WHOLE moment — extending past the preference rather
than truncating it. This keeps subtitles in sync and information complete.
"""

import json
import os
import re
import subprocess
import time
from typing import List, Optional

from subtitles import reconcile_segment_words

# Import OpenShorts clip selection utilities
from clip_selection import (
    build_transcript_windows,
    clip_count_targets,
    clip_duration_bounds,
    compact_words,
    snap_clip_to_words,
)

STAGE1_PROMPT = """You are a viral short-clip selector for a video editor.

Read the complete timestamped transcript below.
Pick the 3-15 most viral moments. Each moment must:
- Use the COMPLETE viral moment — if a great segment runs from 1:30 to 2:45,
  or 5:30 to 6:15, include ALL of it. Do NOT artificially shorten or split it.
- Stand alone COMPLETELY (a viewer who never saw the full video understands
  the beginning, middle, and payoff — never cut a thought or sentence off)
- Start with a strong hook (surprise, controversy, payoff, emotion, question)
- End on a completed thought (resolution, punchline, reveal, or clear takeaway)
- Not cut mid-sentence at either boundary
- When a moment's climax/payoff needs a few more seconds to land, extend the
  clip up to 180s rather than cutting the payoff off — a complete moment beats
  a short one. Splitting one continuous viral moment into several short clips
  is FORBIDDEN: it desyncs subtitles and loses information.

Rate every candidate's virality with `predicted_score` (0-100):
- 90+: shocking reveal, huge emotional payoff, highly quotable
- 70-89: strong hook, clear curiosity gap, likely to be shared
- 50-69: interesting but needs a better title/hook to pop
- <50: low viral potential (usually skip these)

Also return:
- topic: 2-5 words describing what the clip is ABOUT (e.g. "investasi properti", "tips parenting")
- speech_density: 0-100 how tightly packed the spoken content is (higher = fewer dead-air gaps, better retention)

HOOK WINDOW (bukan otomatis di awal klip):
- hook_start_time / hook_end_time adalah MOMEN TERKUAT (0.5-3s), BISA di tengah klip.
- hook_start_time >= start_time, hook_end_time <= end_time, span <= 2.5s.

KEEP_SEGMENTS (trim jaringan mati):
- Jika jeda diam > 0.8s, pecah jadi keep_segments [{start_time, end_time}].
- Total >= 30s. Kalau padat, kirim [].

Return JSON matching this schema:
{"clips": [{"start": 30.0, "end": 75.0, "predicted_score": 87, "reason": "...", "topic": "topic", "speech_density": 80, "hook_start_time": 30.0, "hook_end_time": 32.0, "keep_segments": [{"start_time": 30.0, "end_time": 60.0}]}]}
"""

STAGE2_PROMPT = """You are finishing the editorial pass on candidate viral clips.

Below are the top candidate windows (transcript text included) plus the
source's overall vibe from the transcript. For EACH candidate return:
- start, end: refine the boundaries (do not move by more than a few seconds)
  — DO NOT cut mid-sentence; move the boundary to the nearest complete sentence
- title: a short punchy title (max 12 words, social-media style)
- hook: the one-line hook/caption for the clip (max 20 words)
- hook_score: your refined 0-100 virality rating (weight hook, retention, payoff)
- predicted_score: repeat the candidate's predicted_score
- reason: one short phrase on WHAT the clip is about and WHY it pops

Return JSON matching this schema:
{"clips": [{"start": 30.0, "end": 75.0, "title": "...", "hook": "...", "hook_score": 87, "predicted_score": 87, "reason": "..."}]}
"""


def _heuristic_select(transcript: dict, video_duration: float, log=None,
                      clip_length: str | None = None,
                      target_count: int | None = None) -> List[dict]:
    """Provider-free moment selection used when every LLM is unavailable.

    It intentionally prefers dense, emotionally marked Indonesian speech and
    returns the same clip contract as Gemini so rendering can continue.
    """
    min_dur, max_dur = clip_duration_bounds()
    segments = [s for s in (transcript.get("segments") or [])
                if isinstance(s, dict) and str(s.get("text") or "").strip()]
    if not segments or video_duration <= 0:
        return []
    target = max(1, int(target_count or 3))
    window = min(max_dur, max(min_dur, video_duration))
    step = max(15.0, window * 0.55)
    markers = ("tapi", "jangan", "kenapa", "bukan", "harus", "ternyata",
               "bahaya", "penting", "korup", "rakyat", "krisis", "stop")
    scored = []
    start = 0.0
    while start < video_duration:
        end = min(video_duration, start + window)
        text = " ".join(str(s.get("text") or "") for s in segments
                         if float(s.get("end", 0) or 0) > start
                         and float(s.get("start", 0) or 0) < end)
        if len(text.split()) >= 8:
            lower = text.lower()
            density = min(100, int(len(text.split()) / max(1.0, end - start) * 18))
            marker_score = sum(lower.count(word) for word in markers)
            score = min(100, 45 + marker_score * 6 + density // 3)
            scored.append((score, start, end, text))
        if end >= video_duration:
            break
        start += step
    scored.sort(reverse=True)
    chosen = []
    for score, start, end, text in scored:
        if any(min(end, c["end"]) - max(start, c["start"]) > 0.45 * min(end - start, c["end"] - c["start"])
               for c in chosen):
            continue
        chosen.append({"start": round(start, 2), "end": round(end, 2),
                       "title": "Momen penting", "hook": "Jangan lewatkan momen ini",
                       "hook_score": score, "predicted_score": score,
                       "reason": "Heuristic fallback: kepadatan ucapan dan penanda emosi",
                       "topic": "topik utama", "speech_density": min(100, score),
                       "hook_start_time": round(start, 2),
                       "hook_end_time": round(min(end, start + 2.5), 2),
                       "keep_segments": []})
        if len(chosen) >= target:
            break
    log and log(f"🧠 Local heuristic fallback selected {len(chosen)} moments")
    return chosen


def sample_frames(video_path: str, work_dir: str, n: int = 12, log=None) -> tuple:
    """Extract n evenly-spaced frames at 1024px width. Returns (paths, duration)."""
    os.makedirs(work_dir, exist_ok=True)
    frames = []
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", video_path],
        capture_output=True, text=True,
    )
    try:
        duration = float(probe.stdout.strip())
    except ValueError:
        duration = 600.0
    if duration <= 0:
        duration = 600.0

    step = duration / (n + 1)
    log and log(f"🖼️ Sampling {n} frames from {duration:.0f}s source...")
    import concurrent.futures

    def _grab(i):
        t = step * (i + 1)
        path = os.path.join(work_dir, f"frame_{i:02d}.jpg")
        subprocess.run(
            ["ffmpeg", "-y", "-ss", f"{t:.2f}", "-i", video_path,
             "-frames:v", "1", "-vf", "scale=1024:-2", "-q:v", "4", path],
            capture_output=True, check=True,
        )
        return path

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        frames = [p for p in pool.map(_grab, range(n))]
    return frames, duration


# KlipAja-style clip length presets (v4.4): snappy / story / full.
# Minimum 30s is enforced so a clip always finishes its thought (never cuts
# off a hook before the payoff).
CLIP_LENGTHS = {
    "padat": "clips 30-45s — snappy moments, one strong beat each, still complete",
    "cerita": "clips 45-90s — short arc with context and payoff",
    "lengkap": "clips 90-180s — longer segments up to 3 minutes, full story",
    "viral": "clips 90-120s — complete story arc with context and payoff "
             "(recommended default)",
}
DEFAULT_CLIP_LENGTH = os.environ.get("DEFAULT_CLIP_LENGTH", "viral")

CLIP_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "clips": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "start": {"type": "NUMBER"},
                    "end": {"type": "NUMBER"},
                    "title": {"type": "STRING"},
                    "hook": {"type": "STRING"},
                    "hook_score": {"type": "INTEGER"},
                    "predicted_score": {"type": "INTEGER"},
                    "reason": {"type": "STRING"},
                    "topic": {"type": "STRING"},
                    "speech_density": {"type": "INTEGER"},
                    "hook_start_time": {"type": "NUMBER"},
                    "hook_end_time": {"type": "NUMBER"},
                    "keep_segments": {
                        "type": "ARRAY",
                        "items": {
                            "type": "OBJECT",
                            "properties": {
                                "start_time": {"type": "NUMBER"},
                                "end_time": {"type": "NUMBER"},
                            },
                            "required": ["start_time", "end_time"],
                        },
                    },
                    "hook_v2": {
                        "type": "OBJECT",
                        "properties": {
                            "enabled": {"type": "BOOLEAN"},
                            "items": {
                                "type": "ARRAY",
                                "items": {
                                    "type": "OBJECT",
                                    "properties": {
                                        "start_time": {"type": "NUMBER"},
                                        "end_time": {"type": "NUMBER"},
                                        "text": {"type": "STRING"},
                                    },
                                    "required": ["start_time", "end_time", "text"],
                                },
                            },
                            "transition": {
                                "type": "OBJECT",
                                "properties": {
                                    "type": {"type": "STRING", "enum": ["white_flash", "glitch", "cut"]},
                                },
                                "required": ["type"],
                            },
                        },
                        "required": ["enabled"],
                    },
                },
                "required": ["start", "end"],
            },
        }
    },
    "required": ["clips"],
}

_WORD_RE = re.compile(r"[A-Za-z0-9']+|[^\sA-Za-z0-9']+")


def _word_boundaries(transcript: dict) -> List[dict]:
    """Flatten transcript words (or segments) into (start, end) word units."""
    segs = transcript.get("segments") or []
    words = []
    for s in segs:
        # Rebuild segments whose word list is shorter than their text
        # (whisper drops tokens at segment boundaries) before snapping.
        reconcile_segment_words(s)
        sw = s.get("words")
        if sw:
            for w in sw:
                try:
                    words.append({
                        "start": float(w.get("start", 0)),
                        "end": float(w.get("end", w.get("start", 0) or 0)),
                    })
                except (TypeError, ValueError):
                    continue
        else:
            # Estimate word boundaries inside the segment for snapping.
            text = s.get("text", "") or ""
            span = float(s.get("end", 0) or 0) - float(s.get("start", 0) or 0)
            toks = _WORD_RE.findall(text)
            if toks and span > 0:
                step = span / len(toks)
                t0 = float(s.get("start", 0) or 0)
                for i in range(len(toks)):
                    words.append({"start": t0 + i * step,
                                  "end": t0 + (i + 1) * step})
    return words


def _extract_json(text: str) -> dict:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    return json.loads(text)


def _is_transient(exc: Exception) -> bool:
    """True if the error is likely temporary (503, 429, connection)."""
    msg = str(exc).lower()
    return any(code in msg for code in ("503", "429", "unavailable", "overloaded", "deadline", "connection"))


def _ask(client, model: str, contents: list, log,
         retries: int = 4, base_wait: float = 3.0) -> dict:
    """Schema-enforced generate_content with retries and plain-JSON fallback.

    Retries on transient errors (503 / 429) with exponential back-off.
    If the schema call still fails after retries, falls back to a plain
    (non-schema) call, also with retries.
    """
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            resp = client.models.generate_content(
                model=model, contents=contents,
                config={"response_mime_type": "application/json",
                        "response_schema": CLIP_SCHEMA},
            )
            return _extract_json(resp.text)
        except Exception as e:
            last_exc = e
            if not _is_transient(e) or attempt == retries:
                break
            wait = base_wait * (2 ** (attempt - 1))
            log and log(f"⚠️ schema call attempt {attempt}/{retries} failed ({e}) — retrying in {wait:.0f}s")
            time.sleep(wait)

    log and log(f"⚠️ schema call failed ({last_exc}) — plain JSON fallback")
    for attempt in range(1, retries + 1):
        try:
            resp = client.models.generate_content(model=model, contents=contents)
            return _extract_json(resp.text)
        except Exception as e:
            last_exc = e
            if not _is_transient(e) or attempt == retries:
                break
            wait = base_wait * (2 ** (attempt - 1))
            log and log(f"⚠️ fallback attempt {attempt}/{retries} failed ({e}) — retrying in {wait:.0f}s")
            time.sleep(wait)

    raise last_exc


def _select_clips_with_gemini(video_path: str | None, transcript: dict, work_dir: str, log=None,
                  custom_prompt: str = "",
                  clip_length: str | None = None,
                  video_duration: float | None = None,
                  target_count: int | None = None) -> List[dict]:
    """Two-stage viral clip selection using OpenShorts pipeline.
    Returns sanitized clip dicts {start, end, title, hook, hook_score, predicted_score}.
    """
    key = os.environ.get("GEMINI_API_KEY", "")
    segs = transcript.get("segments") or []
    if not video_duration or video_duration <= 0:
        video_duration = max(
            (float(segment.get("end") or 0) for segment in segs),
            default=0.0,
        )
    if video_duration <= 0:
        raise RuntimeError("Transcript has no usable duration")

    # Keep the render pipeline alive when hosted scoring is unavailable.  The
    # heuristic returns the normal clip contract; downstream dedupe, snapping,
    # layout, captions, and portrait guardian remain unchanged.
    if not key:
        return _heuristic_select(transcript, video_duration, log=log,
                                 clip_length=clip_length,
                                 target_count=target_count)

    from google import genai
    words = _word_boundaries(transcript)

    # OpenShorts: Build transcript windows aligned to segment boundaries
    windows = build_transcript_windows(transcript, video_duration)
    log and log(f"📊 {len(windows)} transcript windows for scoring")

    # OpenShorts: Get clip duration bounds from preset (or env override)
    min_dur, max_dur = clip_duration_bounds()
    log and log(f"⏱️ Clip duration bounds: {min_dur:.0f}s - {max_dur:.0f}s")

    # OpenShorts: Target clip count based on window count
    clip_min, clip_max = clip_count_targets(len(windows))
    try:
        requested_outputs = max(1, min(6, int(target_count or 3)))
    except (TypeError, ValueError):
        requested_outputs = 3
    # Ask for a wider editorial pool than the final output count. The strict
    # >90 quality gate and duplicate detector run later, so six requested
    # outputs need more than six raw candidates to avoid collapsing to one.
    candidate_target = min(
        max(1, len(windows)), max(clip_min, requested_outputs * 2))
    clip_max = max(clip_max, candidate_target)
    log and log(f"🎯 Target clip count: {clip_min}-{clip_max}")

    client = genai.Client(api_key=key)
    model = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")
    log and log(f"🧠 Gemini stage 1 — scoring candidate windows ({model})...")

    # Build compact transcript for window scoring
    # The previous implementation silently stopped at segment 400, which can
    # discard the final half of an 80-minute podcast. Transcript-first means
    # the entire normalized transcript participates in selection.
    compact = "\n".join(
        f"[{s.get('start', 0):.1f}-{s.get('end', 0):.1f}] {s.get('text', '').strip()}"
        for s in segs
    )

    contents = [
        STAGE1_PROMPT,
        (
            f"SELECTION QUOTA: The user requested up to {requested_outputs} "
            f"final clips. Return at least {candidate_target} distinct candidate "
            "moments when the transcript contains them; do not stop at three. "
            "Cover different topics/timestamps and score every candidate "
            "honestly—never inflate a weak moment merely to fill the quota."
        ),
        f"TRANSCRIPT:\n{compact}",
    ]
    if custom_prompt.strip():
        log and log(f"✍️ Custom instruction: {custom_prompt.strip()[:80]}")
        contents.insert(0, (
            f"CREATOR'S CUSTOM INSTRUCTION (highest priority — it overrides "
            f"the default rules where they conflict):\n{custom_prompt.strip()[:600]}"
        ))

    if not clip_length:
        clip_length = DEFAULT_CLIP_LENGTH
    length_note = CLIP_LENGTHS.get(clip_length)
    if length_note:
        log and log(f"⏱️ Clip length preset: {clip_length} ({length_note})")
        contents.insert(0, f"LENGTH PRESET (override the default 90-120s): {length_note}")

    data = _ask(client, model, contents, log)
    candidates = data.get("clips", [])

    # Sanitize stage-1 candidates using OpenShorts bounds
    clean = []
    for c in candidates:
        try:
            start = max(0.0, float(c.get("start", 0)))
            # Use OpenShorts duration bounds (respects user preset via env)
            end = max(start + min_dur, float(c.get("end", start + min_dur)))
            if end - start > max_dur:
                end = start + max_dur
            try:
                score = max(0, min(100, int(float(c.get("predicted_score", 0)))))
            except (TypeError, ValueError):
                score = 0
            # Hook window: independent emotional peak, usually 0.5-2.5s.
            # Clamp to clip bounds so we never point outside [start, end].
            try:
                hs = max(start, float(c.get("hook_start_time", start)))
            except (TypeError, ValueError):
                hs = start
            try:
                he = min(end, float(c.get("hook_end_time", hs + 2.0)))
            except (TypeError, ValueError):
                he = min(hs + 2.5, end)
            if he < hs:
                he = hs
            # --- fix: extend end to comfortably include the hook/punch-in ---
            # window so the clip does not cut off just before the moment that
            # matters. OpenShorts: the hook is the clip, so it must end safely
            # past hook_end_time, not flush against it.
            hook_end = max(he, hs + 2.5)
            if end < hook_end + 3.0:
                end = min(video_duration, hook_end + 3.0)
            # keep end >= start + min_dur so short clips still meet the floor
            end = max(end, start + min_dur)
            if end > start + max_dur:
                end = start + max_dur
            # keep_segments: dense sub-windows to concat (trim dead air).
            raw_segs = c.get("keep_segments") or []
            segs_clean = []
            if isinstance(raw_segs, list):
                for seg in raw_segs:
                    try:
                        s = max(0.0, float(seg["start_time"]))
                        e = min(end, float(seg["end_time"]))
                        if e - s < 1.0:
                            e = min(s + 30.0, end)
                        if e > s and e <= end and s >= start:
                            segs_clean.append({"start": round(s, 2), "end": round(e, 2)})
                    except (KeyError, TypeError, ValueError):
                        continue
                segs_clean.sort(key=lambda z: z["start"])
            clean.append({
                "start": round(start, 2),
                "end": round(end, 2),
                "title": "",
                "hook": "",
                "hook_score": score,
                "predicted_score": score,
                "reason": str(c.get("reason", "")).strip()[:200],
                "topic": str(c.get("topic", "")).strip()[:80],
                "speech_density": max(0, min(100, int(
                    float(c.get("speech_density", 50))))),
                "hook_start_time": round(hs, 2),
                "hook_end_time": round(he, 2),
                "keep_segments": segs_clean,
                "hook_v2": c.get("hook_v2"),
            })
        except (TypeError, ValueError):
            continue
    clean.sort(key=lambda c: c["predicted_score"], reverse=True)

    # Stage 2 — detail pass on the best candidates (transcript-only, cheap).
    top = clean[:candidate_target]
    if top:
        log and log(f"🧠 Gemini stage 2 — polishing top {len(top)} moments...")
        windows = "\n\n".join(
            f"CANDIDATE {i + 1}: [{c['start']:.1f}-{c['end']:.1f}] "
            f"predicted_score={c['predicted_score']} topic='{c.get('topic', '')}' "
            f"speech_density={c.get('speech_density', 50)} reason='{c['reason']}'\n"
            f"TEXT:\n" + "\n".join(
                s.get("text", "").strip()
                for s in segs
                if float(s.get("end", 0) or 0) > c["start"]
                and float(s.get("start", 0) or 0) < c["end"]
            )
            for i, c in enumerate(top)
        )
        try:
            data2 = _ask(client, model, [
                STAGE2_PROMPT,
                (f"DETAIL QUOTA: Return exactly {len(top)} entries, one for "
                 "each numbered candidate and in the same order. Strengthen "
                 "the title/hook, but keep every score candid."),
                f"CANDIDATES:\n{windows}",
            ], log)
            for i, c in enumerate(data2.get("clips", [])[:len(top)]):
                try:
                    top[i]["title"] = str(c.get("title", "")).strip()[:120]
                    top[i]["hook"] = str(c.get("hook", "")).strip()[:200]
                    try:
                        top[i]["hook_score"] = max(0, min(100, int(float(
                            c.get("hook_score", top[i]["predicted_score"])))))
                    except (TypeError, ValueError):
                        pass
                    # Stage 2 may refine the boundary, respect OpenShorts bounds
                    try:
                        ns = max(0.0, float(c.get("start", top[i]["start"])))
                        ne = max(ns + min_dur, float(c.get("end", top[i]["end"])))
                        if ne - ns <= max_dur:
                            top[i]["start"] = round(ns, 2)
                            top[i]["end"] = round(ne, 2)
                    except (TypeError, ValueError):
                        pass
                    if str(c.get("reason", "")).strip():
                        top[i]["reason"] = str(c["reason"]).strip()[:200]
                except (TypeError, ValueError):
                    continue
        except Exception as e:
            log and log(f"⚠️ stage 2 failed ({e}) — keeping stage 1 titles")

    # Word-snap boundaries so subtitles start/end on word edges — plus a
    # little lead/tail of the surrounding silence so cuts land in pauses,
    # not mid-word (Ported from OpenShorts clip_selection.snap_clip_to_words).
    for c in clean:
        c["start"], c["end"] = snap_clip_to_words(
            c["start"], c["end"], words, video_duration,
            min_duration=min_dur, max_duration=max_dur)
    clean.sort(key=lambda c: c["predicted_score"], reverse=True)
    log and log(f"🎯 {len(clean)} viral moments found")
    return clean


def select_clips(video_path: str | None, transcript: dict, work_dir: str, log=None,
                 custom_prompt: str = "", clip_length: str | None = None,
                 video_duration: float | None = None,
                 target_count: int | None = None) -> List[dict]:
    """Select clips with a provider fallback that preserves pipeline progress."""
    segments = transcript.get("segments") or []
    if not video_duration or video_duration <= 0:
        video_duration = max((float(s.get("end") or 0) for s in segments
                              if isinstance(s, dict)), default=0.0)
    if not os.environ.get("GEMINI_API_KEY", "").strip():
        return _heuristic_select(transcript, video_duration, log=log,
                                 clip_length=clip_length,
                                 target_count=target_count)
    try:
        return _select_clips_with_gemini(
            video_path, transcript, work_dir, log=log,
            custom_prompt=custom_prompt, clip_length=clip_length,
            video_duration=video_duration, target_count=target_count)
    except Exception as exc:  # noqa: BLE001
        log and log(f"⚠️ Hosted moment scoring unavailable ({exc}) — local heuristic fallback")
        return _heuristic_select(transcript, video_duration, log=log,
                                 clip_length=clip_length,
                                 target_count=target_count)
