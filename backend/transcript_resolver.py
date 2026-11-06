"""Fast, validated transcript resolution for long-form media.

Priority: cache -> permitted native caption track -> Groq audio ASR.  A native
track is accepted only when it is complete enough for clip selection and has
word timing precise enough for karaoke; otherwise Groq remains the safe path.
"""
from __future__ import annotations

import hashlib
import os
import re
import math
from typing import Callable

import requests

import db
from transcribe import pick_first_audio, transcribe
from video import download_audio_url


def cache_key(source: str, media_id: str, language: str = "") -> str:
    identity = f"{source.strip().lower()}:{media_id.strip()}:{language.strip().lower()}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def file_media_id(path: str, chunk_size: int = 1024 * 1024) -> str:
    """Content identity for uploaded files so repeat uploads can reuse ASR."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _language_candidates(language_hint: str = "") -> list[str]:
    hint = (language_hint or "").strip().lower()
    result = [hint] if hint else []
    for lang in ("id", "id-ID", "en", "en-US"):
        if lang and lang not in result:
            result.append(lang)
    return result


def _pick_caption(info: dict, language_hint: str = "") -> tuple[str, dict, str] | None:
    """Prefer creator subtitles, then automatic captions, in JSON3 format."""
    for source_name, tracks in (
        ("platform_caption", info.get("subtitles") or {}),
        ("platform_auto_caption", info.get("automatic_captions") or {}),
    ):
        if not isinstance(tracks, dict):
            continue
        keys = list(tracks)
        chosen = None
        for wanted in _language_candidates(language_hint):
            chosen = next((k for k in keys if k.lower() == wanted.lower()), None)
            if not chosen:
                chosen = next((k for k in keys
                               if k.lower().split("-", 1)[0] == wanted.lower().split("-", 1)[0]), None)
            if chosen:
                break
        if not chosen and keys:
            chosen = keys[0]
        formats = tracks.get(chosen) if chosen else None
        if not isinstance(formats, list):
            continue
        track = next((f for f in formats if f.get("ext") == "json3"), None)
        if track and track.get("url"):
            return chosen, track, source_name
    return None


def _clean_caption_text(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text or "")
    return re.sub(r"\s+", " ", text).strip()


def parse_youtube_json3(payload: dict, language: str,
                        transcript_source: str) -> dict:
    segments = []
    last_text = ""
    last_end = -1.0
    for event in payload.get("events") or []:
        pieces = event.get("segs") or []
        if not pieces:
            continue
        event_start = float(event.get("tStartMs") or 0) / 1000.0
        event_dur = max(0.05, float(event.get("dDurationMs") or 0) / 1000.0)
        usable = []
        for piece in pieces:
            text = _clean_caption_text(piece.get("utf8") or "")
            if not text or text == "\n":
                continue
            usable.append((text, float(piece.get("tOffsetMs") or 0) / 1000.0))
        text = _clean_caption_text(" ".join(p[0] for p in usable))
        # YouTube rolling captions may repeat the same visible line in
        # adjacent overlapping events. Drop only that rolling duplicate;
        # preserve a genuinely repeated sentence after a pause so subtitle
        # text is not silently lost.
        if (not text or (text == last_text
                         and event_start <= last_end + 0.15)):
            continue
        words = []
        for index, (piece_text, offset) in enumerate(usable):
            start = event_start + offset
            next_offset = usable[index + 1][1] if index + 1 < len(usable) else event_dur
            end = max(start + 0.04, event_start + min(event_dur, next_offset))
            tokens = piece_text.split()
            if not tokens:
                continue
            span = max(0.04, end - start)
            for token_index, token in enumerate(tokens):
                token_start = start + span * token_index / len(tokens)
                token_end = start + span * (token_index + 1) / len(tokens)
                words.append({"word": token, "start": round(token_start, 3),
                              "end": round(token_end, 3)})
        segments.append({
            "start": round(event_start, 3),
            "end": round(event_start + event_dur, 3),
            "text": text,
            "words": words,
        })
        last_text = text
        last_end = max(last_end, event_start + event_dur)
    return {
        "source": transcript_source,
        "language": language or "",
        "has_word_timestamps": bool(segments and all(s.get("words") for s in segments)),
        "segments": segments,
        "text": " ".join(s["text"] for s in segments),
    }


def sanitize_transcript(result: dict) -> tuple[dict, int]:
    """Return a timestamp-ordered transcript with unusable segments removed.

    Long chunked ASR responses occasionally contain one empty segment, a
    non-finite timestamp, or a small out-of-order tail even though thousands
    of surrounding segments are valid. Rejecting the complete paid job for
    that single provider artifact is wasteful. This sanitizer is deliberately
    conservative: it does not invent text or timing; it only drops malformed
    entries, normalizes valid word timing, and sorts by timestamp.
    """
    cleaned = []
    source_segments = result.get("segments") or []
    for segment in source_segments if isinstance(source_segments, list) else []:
        if not isinstance(segment, dict):
            continue
        try:
            start = float(segment.get("start"))
            end = float(segment.get("end"))
        except (TypeError, ValueError):
            continue
        text = str(segment.get("text") or "").strip()
        if (not math.isfinite(start) or not math.isfinite(end)
                or start < 0 or end <= start or not text):
            continue

        words = []
        for word in segment.get("words") or []:
            if not isinstance(word, dict):
                continue
            token = str(word.get("word") or "").strip()
            try:
                word_start = float(word.get("start"))
                word_end = float(word.get("end"))
            except (TypeError, ValueError):
                continue
            if (not token or not math.isfinite(word_start)
                    or not math.isfinite(word_end) or word_end <= word_start):
                continue
            words.append({
                "word": token,
                "start": round(max(start, word_start), 3),
                "end": round(min(end, word_end), 3),
            })
        words = [word for word in words if word["end"] > word["start"]]
        words.sort(key=lambda word: (word["start"], word["end"]))

        normalized = dict(segment)
        normalized.update({
            "start": round(start, 3),
            "end": round(end, 3),
            "text": text,
            "words": words,
        })
        cleaned.append(normalized)

    cleaned.sort(key=lambda segment: (segment["start"], segment["end"]))
    normalized_result = dict(result)
    normalized_result["segments"] = cleaned
    normalized_result["text"] = " ".join(
        segment["text"] for segment in cleaned).strip()
    normalized_result["has_word_timestamps"] = bool(
        cleaned and all(segment.get("words") for segment in cleaned))
    return normalized_result, max(0, len(source_segments) - len(cleaned))


def validate_transcript(result: dict, duration: float = 0.0,
                        require_words: bool = True) -> tuple[bool, str]:
    """Reject truncated, unordered or karaoke-inaccurate caption tracks."""
    segments = result.get("segments") or []
    if not isinstance(segments, list) or not segments:
        return False, "no timestamped segments"
    previous = -1.0
    word_count = 0
    valid_segments = 0
    for segment in segments:
        try:
            start = float(segment.get("start"))
            end = float(segment.get("end"))
        except (TypeError, ValueError):
            return False, "invalid timestamp"
        text = str(segment.get("text") or "").strip()
        if start < previous - 0.05 or end <= start or not text:
            return False, "unordered or empty segment"
        previous = start
        valid_segments += 1
        word_count += len(text.split())
    minimum_words = 3 if duration < 30 else max(12, int(duration / 120.0))
    if word_count < minimum_words:
        return False, "too little caption text"
    if duration >= 60:
        first = float(segments[0].get("start") or 0)
        last = float(segments[-1].get("end") or 0)
        if first > duration * 0.15 or last < duration * 0.70:
            return False, "caption track appears truncated"
    has_words = bool(result.get("has_word_timestamps"))
    if require_words and not has_words:
        return False, "word timestamps unavailable"
    return valid_segments > 0, "ok"


def transcript_overlap_ratio(result: dict) -> float:
    """Fraction of caption display time that overlaps an earlier segment."""
    segments = sorted(
        (segment for segment in (result.get("segments") or [])
         if isinstance(segment, dict)),
        key=lambda segment: float(segment.get("start", 0) or 0),
    )
    total = 0.0
    overlap = 0.0
    previous_end = -1.0
    for segment in segments:
        try:
            start = float(segment.get("start"))
            end = float(segment.get("end"))
        except (TypeError, ValueError):
            continue
        if end <= start:
            continue
        total += end - start
        if previous_end > start:
            overlap += min(end, previous_end) - start
        previous_end = max(previous_end, end)
    return overlap / total if total > 0 else 0.0


def normalize_rolling_caption_timing(result: dict) -> tuple[dict, int]:
    """Turn YouTube rolling-display captions into sequential speech cues.

    Auto-caption lines often remain visible while the next line starts. Those
    display windows are valid for a browser player, but treating both as
    independent ASR speech intervals interleaves their words and can make ASS
    karaoke discard the later line.  End each rolling line at the next onset
    and redistribute only that line's existing words; no text is invented.
    """
    normalized = dict(result)
    source = sorted(
        (dict(segment) for segment in (result.get("segments") or [])
         if isinstance(segment, dict)),
        key=lambda segment: (float(segment.get("start", 0) or 0),
                             float(segment.get("end", 0) or 0)),
    )
    adjusted = 0
    for index, segment in enumerate(source[:-1]):
        start = float(segment.get("start", 0) or 0)
        end = float(segment.get("end", start) or start)
        next_start = float(source[index + 1].get("start", end) or end)
        if next_start >= end - 0.04 or next_start <= start + 0.04:
            continue
        new_end = next_start
        tokens = [str(word.get("word") or "").strip()
                  for word in (segment.get("words") or [])]
        tokens = [token for token in tokens if token]
        if not tokens:
            tokens = str(segment.get("text") or "").split()
        span = new_end - start
        segment["end"] = round(new_end, 3)
        segment["words"] = [
            {
                "word": token,
                "start": round(start + span * word_index / len(tokens), 3),
                "end": round(start + span * (word_index + 1) / len(tokens), 3),
            }
            for word_index, token in enumerate(tokens)
        ] if tokens else []
        adjusted += 1
    normalized["segments"] = source
    normalized["has_word_timestamps"] = bool(
        source and all(segment.get("words") for segment in source))
    normalized["rolling_timing_normalized"] = adjusted > 0
    return normalized, adjusted


def _normalize_native_overlap(result: dict, log=None) -> dict | None:
    try:
        maximum = max(0.05, min(0.80, float(os.environ.get(
            "NATIVE_CAPTION_MAX_OVERLAP_RATIO", "0.18"))))
    except (TypeError, ValueError):
        maximum = 0.18
    before = transcript_overlap_ratio(result)
    if before <= maximum:
        return result
    result, adjusted = normalize_rolling_caption_timing(result)
    after = transcript_overlap_ratio(result)
    if adjusted:
        log and log(
            f"💬 Normalized {adjusted} rolling-caption cue(s) "
            f"(overlap {before:.0%} → {after:.0%})")
    if after > maximum:
        log and log(
            f"⚠️ Native caption rejected: unresolved timing overlap "
            f"{after:.0%} — using ASR fallback")
        return None
    return result


def resolve_native_transcript(info: dict, duration: float,
                              language_hint: str = "", log=None) -> dict | None:
    choice = _pick_caption(info, language_hint)
    if not choice:
        return None
    language, track, source_name = choice
    try:
        response = requests.get(track["url"], timeout=30)
        response.raise_for_status()
        result = parse_youtube_json3(response.json(), language, source_name)
        result, removed = sanitize_transcript(result)
        if removed:
            log and log(
                f"🧹 Cleaned {removed} invalid native-caption segment(s)")
        result = _normalize_native_overlap(result, log=log)
        if result is None:
            return None
    except Exception as exc:  # noqa: BLE001 - a bad track must fall back to ASR
        log and log(f"⚠️ Native caption fetch failed ({exc}) — using Groq")
        return None
    ok, reason = validate_transcript(result, duration=duration, require_words=True)
    if not ok:
        log and log(f"⚠️ Native caption rejected: {reason} — using Groq")
        return None
    return result


def resolve_transcript(*, source: str, media_id: str, duration: float,
                       language_hint: str = "", info: dict | None = None,
                       video_path: str = "", source_url: str = "",
                       work_dir: str, platform: str = "", log=None,
                       audio_downloader: Callable[..., str] = download_audio_url) -> tuple[dict, dict]:
    """Resolve/cache one normalized transcript and return it with provenance."""
    effective_language = (language_hint or os.environ.get("GROQ_ASR_LANGUAGE", "id")).strip()
    key = cache_key(source, media_id, effective_language)
    cached = db.get_cached_transcript(key)
    if cached:
        result, removed = sanitize_transcript(cached["transcript"])
        cached_source = str(
            cached.get("transcript_source") or result.get("source") or "")
        if cached_source.startswith("platform_"):
            result = _normalize_native_overlap(result, log=log)
            if result is None:
                result = {"segments": []}
        ok, _ = validate_transcript(result, duration=duration, require_words=True)
        if ok:
            if removed:
                log and log(
                    f"🧹 Cleaned {removed} invalid cached transcript segment(s)")
            log and log("⚡ Transcript cache hit — ASR skipped")
            return result, {
                "source": cached.get("transcript_source") or result.get("source") or "cache",
                "cache_hit": True,
                "cache_key": key,
            }

    result = None
    if source == "youtube" and info:
        result = resolve_native_transcript(info, duration, effective_language, log)
        if result:
            log and log("⚡ Valid YouTube caption found — Whisper skipped")

    if result is None:
        if video_path:
            audio_path = pick_first_audio(video_path, work_dir, log)
        else:
            audio_path = audio_downloader(source_url, work_dir, log=log,
                                          platform=platform or source)
        result = transcribe(audio_path, log)
        result.setdefault("source", "groq_whisper")
        result.setdefault("language", effective_language)
        result, removed = sanitize_transcript(result)
        if removed:
            log and log(f"🧹 Cleaned {removed} invalid ASR segment(s)")
        ok, reason = validate_transcript(result, duration=duration, require_words=True)
        if not ok:
            raise RuntimeError(f"TRANSCRIPT_QUALITY_FAILED: {reason}")

    transcript_source = str(result.get("source") or "whisper")
    db.put_cached_transcript(key, source, media_id,
                             str(result.get("language") or language_hint),
                             transcript_source, result)
    return result, {"source": transcript_source, "cache_hit": False,
                    "cache_key": key}
