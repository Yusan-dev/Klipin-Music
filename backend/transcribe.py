"""Gemini/Groq ASR + resilient fallbacks for Klipin.

When GEMINI_API_KEY is configured, Gemini 3.5 Transcribe is preferred in
verbatim word-timestamp mode. Groq, Deepgram and local faster-whisper remain
fallbacks so transcription never depends on one provider.
"""
import os
import time
from typing import List, Optional


def _groq_client():
    import httpx
    key = os.environ.get("GROQ_API_KEY", "")
    if not key:
        return None
    return httpx.Client(
        base_url="https://api.groq.com/openai/v1",
        headers={"Authorization": f"Bearer {key}"},
        timeout=600,
    )


def _retry_transient(fn, log=None, what: str = "ASR") -> dict:
    """Run an ASR call, retrying transient failures (429 / 5xx / network).

    Non-transient 4xx (bad key, bad request) re-raise immediately so the
    next provider takes over instead of burning retries on config bugs.
    """
    import time

    last = None
    for attempt in range(3):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            status = getattr(getattr(e, "response", None), "status_code", None)
            transient = status is None or status == 429 or status >= 500
            last = e
            if not transient or attempt == 2:
                raise
            delay = (attempt + 1) * 3
            log and log(f"⚠️ {what} transient error "
                        f"(HTTP {status or type(e).__name__}) — retry "
                        f"{attempt + 1}/2 in {delay}s")
            time.sleep(delay)
    raise last


def _groq_post(client, path: str, model: str, language) -> dict:
    fields = {"model": model, "response_format": "verbose_json",
              "timestamp_granularities[]": "word"}
    if language:
        fields["language"] = language

    def call():
        with open(path, "rb") as f:
            resp = client.post(
                "/audio/transcriptions",
                # timestamp_granularities[]=word gives PER-WORD timestamps —
                # without it Groq omits `words` entirely and karaoke/cut/filler
                # fall back to even-distribution estimates (less accurate).
                data=fields,
                files={"file": (os.path.basename(path), f, "audio/mpeg")},
            )
        resp.raise_for_status()
        return resp.json()

    return _retry_transient(call, log=None)


def _ffprobe_duration(path: str) -> float:
    import json
    import subprocess
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", path],
        capture_output=True,
    )
    return float(json.loads(r.stdout)["format"]["duration"])


def _contract_in_global_window(contract: dict, offset: float,
                               left: float, right: float) -> dict:
    """Shift one ASR chunk globally and keep its half-overlap ownership.

    Adjacent chunks share a short context window, but the midpoint seam gives
    each recognized word to exactly one chunk. This prevents both boundary
    word loss and duplicate subtitles after stitching.
    """
    segments = []
    for source_segment in contract.get("segments") or []:
        words = []
        for source_word in source_segment.get("words") or []:
            word = dict(source_word)
            local_start = float(word.get("start", 0) or 0)
            local_end = float(word.get("end", local_start) or local_start)
            word["start"] = local_start + offset
            word["end"] = local_end + offset
            midpoint = (word["start"] + word["end"]) / 2
            if left <= midpoint < right:
                words.append(word)
        if words:
            segments.append({
                "start": words[0]["start"],
                "end": words[-1]["end"],
                "text": " ".join(str(word.get("word") or "").strip()
                                   for word in words).strip(),
                "words": words,
            })
            continue

        # Provider responses without word timestamps still retain their
        # segment when its midpoint belongs to this chunk's seam window.
        segment = dict(source_segment)
        start = float(segment.get("start", 0) or 0) + offset
        end = float(segment.get("end", start) or start) + offset
        if left <= (start + end) / 2 < right:
            segment["start"], segment["end"] = start, end
            segments.append(segment)
    return {"segments": segments,
            "text": " ".join(segment.get("text", "")
                             for segment in segments).strip()}


def _groq_chunked(audio_path: str, model: str, language, log=None) -> dict:
    """Transcribe long audio in overlap-safe ~10-minute chunks."""
    import math
    import shutil
    import subprocess

    client = _groq_client()
    tmp = audio_path + ".chunks"
    os.makedirs(tmp, exist_ok=True)
    try:
        duration = _ffprobe_duration(audio_path)
        chunk_seconds = 600.0
        try:
            overlap = max(0.0, min(10.0, float(os.environ.get(
                "ASR_CHUNK_OVERLAP_SECONDS", "2"))))
        except (TypeError, ValueError):
            overlap = 2.0
        step = max(60.0, chunk_seconds - overlap)
        starts = [index * step for index in range(
            max(1, int(math.ceil(max(0.0, duration - overlap) / step))))]
        chunks = []
        for index, start in enumerate(starts):
            chunk = os.path.join(tmp, f"chunk_{index:03d}.mp3")
            subprocess.run(
                ["ffmpeg", "-y", "-ss", f"{start:.3f}", "-i", audio_path,
                 "-t", f"{min(chunk_seconds, duration - start):.3f}",
                 "-c", "copy", chunk],
                capture_output=True, check=True,
            )
            chunks.append(chunk)
        log and log(
            f"🎙️ Audio exceeds Groq upload cap — splitting into "
            f"{len(chunks)} chunks ({overlap:.1f}s overlap-safe seam)")
        segments, texts = [], []
        half_overlap = overlap / 2
        for i, (chunk, offset) in enumerate(zip(chunks, starts), 1):
            log and log(f"🎙️ Transcribing chunk {i}/{len(chunks)}...")
            contract = _groq_to_contract(_groq_post(client, chunk, model, language))
            left = -float("inf") if i == 1 else offset + half_overlap
            right = (float("inf") if i == len(chunks)
                     else offset + chunk_seconds - half_overlap)
            owned = _contract_in_global_window(
                contract, offset=offset, left=left, right=right)
            segments.extend(owned["segments"])
            texts.append(owned.get("text", "").strip())
        return {"text": " ".join(t for t in texts if t), "segments": segments}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def transcribe_groq(audio_path: str, log=None) -> dict:
    """Transcribe an audio file via Groq. Returns {"segments": [...], "text": ...}."""
    model = os.environ.get("GROQ_ASR_MODEL", "whisper-large-v3")
    # Language hint (ISO 639-1). Forcing the market's language prevents
    # whisper auto-detect from misclassifying short/ambiguous audio (e.g.
    # Indonesian heard as Malay/English) — the biggest single lever on
    # transcription accuracy for this product.
    language = os.environ.get("GROQ_ASR_LANGUAGE", "id").strip() or None
    client = _groq_client()
    if client is None:
        raise RuntimeError("GROQ_API_KEY not set")
    log and log(f"🎙️ Transcribing with Groq {model} ({language or 'auto-detect language'})...")
    # ponytail: cap mirrors Groq's ~25MB free-tier upload limit; bump via env if tier changes.
    max_bytes = int(os.environ.get("GROQ_MAX_UPLOAD_MB", "24")) * 1024 * 1024
    if os.path.getsize(audio_path) > max_bytes:
        return _groq_chunked(audio_path, model, language, log)
    return _groq_to_contract(_groq_post(client, audio_path, model, language))


def _groq_to_contract(data: dict) -> dict:
    """Normalize Groq verbose_json into the pipeline transcript contract.

    Groq's segment `text` is model-tokenized: two words are sometimes GLUED
    together with no space (the "2 words merged into 1" bug), which corrupts
    subtitle text. The top-level `words` array carries the REAL per-word
    tokens + timestamps, so we rebuild every segment's text from those words
    (space-joined) and group them back onto Groq's segments by time-overlap.
    Word tokens are stripped so empty/stray-whitespace tokens never reach a
    caption. (Same strategy as QuClippers/OpenShorts `_groq_to_contract`.)
    """
    text = (data.get("text") or "").strip()

    words = []
    for w in data.get("words") or []:
        wtext = str(w.get("word") or "").strip()
        if not wtext:
            continue
        try:
            ws = float(w["start"])
            we = float(w["end"])
        except (KeyError, TypeError, ValueError):
            continue
        words.append({"word": wtext, "start": ws, "end": we})

    if not words:
        # No top-level words: last resort, interpolate from segment text.
        segments = []
        for seg in data.get("segments", []) or []:
            seg_text = (seg.get("text") or "").strip()
            if not seg_text:
                continue
            try:
                start = float(seg["start"])
                end = float(seg["end"])
            except (KeyError, TypeError, ValueError):
                continue
            sw = seg_text.split()
            wd = []
            if sw and end > start:
                step = (end - start) / len(sw)
                for i, w in enumerate(sw):
                    wd.append({"word": w, "start": round(start + i * step, 3),
                               "end": round(start + (i + 1) * step, 3)})
            segments.append({"start": start, "end": end,
                             "text": seg_text, "words": wd})
        out = {"text": text, "segments": segments}
        if data.get("language"):
            out["language"] = data["language"]
        return out

    raw = data.get("segments") or []
    if not raw:
        # Groq returned ONLY a flat `words` array — split on pauses so
        # subtitle timing/karaoke/filler detection still work.
        out = {"text": text, "segments": _segments_from_words(words)}
        if data.get("language"):
            out["language"] = data["language"]
        return out

    # Group each word into the Groq segment whose time window contains it
    # (index-based so repeated identical words stay distinct).
    segments = []
    assigned = [False] * len(words)
    for seg in raw:
        try:
            ss = float(seg["start"])
            se = float(seg["end"])
        except (KeyError, TypeError, ValueError):
            continue
        idxs = [i for i, w in enumerate(words)
                if not assigned[i] and ss - 0.05 <= w["start"] <= se + 0.05]
        if not idxs:
            continue
        for i in idxs:
            assigned[i] = True
        segments.append(_segment_from_words([words[i] for i in idxs]))

    leftover = [w for i, w in enumerate(words) if not assigned[i]]
    if leftover:
        # Provider segment windows can leave sparse words unmatched across the
        # whole chunk. Appending them as one tail segment makes its start time
        # jump backwards after later segments (TRANSCRIPT_QUALITY_FAILED).
        # Preserve their natural pause boundaries and restore chronological
        # order instead.
        segments.extend(_segments_from_words(leftover))
    segments.sort(key=lambda segment: (
        float(segment.get("start", 0)), float(segment.get("end", 0))))

    out = {"text": text, "segments": segments}
    if data.get("language"):
        out["language"] = data["language"]
    return out


def _segment_from_words(ws: list) -> dict:
    """Build a segment dict from an ordered word list (used by _groq_to_contract)."""
    return {
        "start": ws[0]["start"],
        "end": ws[-1]["end"],
        "text": " ".join(w["word"] for w in ws).strip(),
        "words": [
            {"word": w["word"], "start": round(w["start"], 3),
             "end": round(w["end"], 3)}
            for w in ws
        ],
    }


def _segments_from_words(words: list, gap: float = 0.8) -> list:
    """Rebuild transcript segments from a flat `words` array (Groq variant).

    Words are split into segments whenever the silence between them exceeds
    `gap` seconds — the same pause heuristic whisper uses for segmenting.
    """
    ordered = sorted(words, key=lambda w: float(w.get("start", 0)))
    buckets, cur, prev_end = [], [], None
    for w in ordered:
        s = float(w.get("start", 0))
        e = float(w.get("end", s + 0.2))
        if cur and prev_end is not None and s - prev_end > gap:
            buckets.append(cur)
            cur = []
        cur.append({"word": str(w.get("word", "")), "start": s, "end": e})
        prev_end = e
    if cur:
        buckets.append(cur)
    out = []
    for i, ws in enumerate(buckets):
        out.append({
            "id": i,
            "start": ws[0]["start"],
            "end": ws[-1]["end"],
            "text": " ".join(w["word"] for w in ws).strip(),
            "words": ws,
        })
    return out


def _pad_audio_head(audio_path: str, pad: float = 0.5) -> str:
    """Prepend `pad` seconds of silence so ASR VAD never swallows the first
    word (Deepgram drops onsets when speech starts at t=0). Returns padded
    temp path; timestamps must be shifted back by `pad`."""
    import subprocess
    import tempfile

    fd, out = tempfile.mkstemp(suffix=".mp3")
    os.close(fd)
    subprocess.run(
        ["ffmpeg", "-y", "-i", audio_path,
         "-af", f"adelay={int(pad * 1000)}|{int(pad * 1000)}", "-b:a", "64k", out],
        capture_output=True, check=True,
    )
    return out


def _deepgram(audio_path: str, log=None) -> dict:
    """Deepgram nova-3 fallback (runs when Groq fails/unavailable).

    Returns the same contract as the other backends. Deepgram's word-level
    timestamps come from `words[0].start`/`words[0].end` per alternative.
    """
    import httpx

    key = os.environ.get("DEEPGRAM_API_KEY", "")
    if not key:
        raise RuntimeError("DEEPGRAM_API_KEY not set")
    language = os.environ.get("GROQ_ASR_LANGUAGE", "id").strip() or None
    log and log("🎙️ Transcribing with Deepgram nova-3...")
    params = {
        "model": "nova-3",
        "smart_format": "false",
        "punctuate": "true",
        "filler_words": "true",
    }
    if language:
        params["language"] = language
    # 0.5s silent lead-in: without it Deepgram's VAD eats the first word
    # when speech starts immediately at t=0 (hook lines!). Shifted back below.
    PAD = 0.5
    try:
        padded = _pad_audio_head(audio_path, PAD)
        with open(padded, "rb") as f:
            body = f.read()
    finally:
        pass
    try:
        os.unlink(padded)
    except OSError:
        pass

    def post():
        return httpx.post(
            "https://api.deepgram.com/v1/listen",
            headers={"Authorization": f"Token {key}",
                     "Content-Type": "audio/mpeg"},
            params=params,
            content=body,
            timeout=600,
        )

    resp = _retry_transient(post)
    resp.raise_for_status()
    data = resp.json()
    alt = (((data.get("results") or {}).get("channels") or [{}])[0]
           .get("alternatives") or [{}])[0]
    text = (alt.get("transcript") or "").strip()
    words = []
    for w in alt.get("words") or []:
        wtext = str(w.get("word") or "").strip()
        if not wtext:
            continue
        # Undo the silent lead-in padding so timestamps match the original audio.
        words.append({"word": wtext,
                      "start": max(0.0, float(w["start"]) - PAD),
                      "end": max(0.0, float(w["end"]) - PAD)})
    # Same pause-based segmentation as the Groq flat-words path.
    return {"text": text, "segments": _segments_from_words(words)}


def _whisper_local(audio_path: str, log=None) -> dict:
    """Local faster-whisper fallback (imported lazily — heavy dep)."""
    from faster_whisper import WhisperModel

    model_name = os.environ.get("WHISPER_MODEL", "small")
    device = os.environ.get("WHISPER_DEVICE", "cpu")
    compute = os.environ.get("WHISPER_COMPUTE", "int8")
    log and log(f"🎙️ Transcribing locally with faster-whisper {model_name}...")
    model = WhisperModel(model_name, device=device, compute_type=compute)
    segments, info = model.transcribe(audio_path, word_timestamps=True)
    out = {"text": "", "segments": [], "language": info.language}
    for seg in segments:
        words = [
            {"word": w.word, "start": w.start, "end": w.end}
            for w in (seg.words or [])
        ]
        out["segments"].append({
            "start": seg.start, "end": seg.end,
            "text": seg.text.strip(), "words": words,
        })
        out["text"] += seg.text
    return out


def _offset_seconds(value) -> float:
    """Parse Gemini duration strings such as ``1.240s``."""
    raw = str(value or "").strip()
    if raw.endswith("s"):
        raw = raw[:-1]
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def transcribe_gemini(audio_path: str, log=None) -> dict:
    """Transcribe with Gemini 3.5 in word-preserving verbatim mode."""
    from google import genai

    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("GEMINI_API_KEY not set")
    model = os.environ.get("GEMINI_TRANSCRIBE_MODEL", "gemini-3.5-transcribe")
    language = os.environ.get(
        "GEMINI_ASR_LANGUAGE", os.environ.get("GROQ_ASR_LANGUAGE", "id-ID")
    ).strip()
    if language == "id":
        language = "id-ID"
    log and log(f"🎙️ Transcribing with {model} (verbatim + word timestamps)...")
    client = genai.Client(api_key=key)
    uploaded = client.files.upload(file=audio_path)
    try:
        interaction = client.interactions.create(
            model=model,
            input=[{"type": "audio", "uri": uploaded.uri,
                    "mime_type": uploaded.mime_type}],
            generation_config={"transcription_config": {
                "language_codes": [language] if language else [],
                "mode": {"type": "verbatim",
                         "timestamp_granularities": ["word"]},
            }},
        )
        # File transcription can be asynchronous. `create()` may return an
        # in-progress interaction with output_text but no annotations yet;
        # parsing that provisional response made Klipin incorrectly conclude
        # that Gemini had omitted every word timestamp.
        deadline = time.monotonic() + 600
        while str(getattr(interaction, "status", "")).lower() not in (
                "completed", "failed", "cancelled"):
            if time.monotonic() >= deadline:
                raise TimeoutError("Gemini transcription did not complete within 10 minutes")
            time.sleep(2)
            interaction = client.interactions.get(id=interaction.id)
        status = str(getattr(interaction, "status", "")).lower()
        if status != "completed":
            raise RuntimeError(f"Gemini transcription ended with status {status}")
        words = []
        for step in getattr(interaction, "steps", []) or []:
            for content in getattr(step, "content", []) or []:
                for annotation in getattr(content, "annotations", []) or []:
                    annotation_type = getattr(annotation, "type", None)
                    annotation_type = getattr(annotation_type, "value", annotation_type)
                    if str(annotation_type).lower().split(".")[-1] != "word_info":
                        continue
                    text = str(getattr(annotation, "text", "") or "").strip()
                    start = _offset_seconds(getattr(annotation, "start_offset", None))
                    end = _offset_seconds(getattr(annotation, "end_offset", None))
                    if text and end > start:
                        words.append({"word": text, "start": start, "end": end,
                                      "speaker": getattr(annotation, "speaker", None)})
        if not words:
            raise RuntimeError("Gemini returned no word timestamp annotations")
        words.sort(key=lambda word: (word["start"], word["end"]))
        return {
            "text": str(getattr(interaction, "output_text", "") or "").strip(),
            "segments": _segments_from_words(words), "language": language,
            "source": "gemini_3_5_transcribe", "has_word_timestamps": True,
        }
    finally:
        try:
            client.files.delete(name=uploaded.name)
        except Exception:
            pass


def transcribe(audio_path: str, log=None) -> dict:
    """Transcribe with the configured provider and resilient fallbacks.

    After transcription, applies forced alignment to refine word timestamps
    using audio energy analysis (±10-40ms accuracy vs ±50-200ms raw whisper).
    """
    backend = os.environ.get(
        "TRANSCRIBE_BACKEND",
        "gemini" if os.environ.get("GEMINI_API_KEY") else "groq",
    ).lower()
    errors: List[str] = []
    result = None
    local_fallback = False
    provider = ""

    # All hosted providers run before local faster-whisper. In particular,
    # ``economy`` never spends our Modal GPU on ASR unless every API provider
    # fails. Groq uses hosted Whisper, so it does not consume the Klipin GPU.
    provider_orders = {
        "economy": ("groq", "gemini", "deepgram"),
        "groq": ("groq", "gemini", "deepgram"),
        "gemini": ("gemini", "groq", "deepgram"),
        "deepgram": ("deepgram", "groq", "gemini"),
        "whisper": (),
        "local": (),
    }
    order = provider_orders.get(backend, provider_orders["economy"])
    hosted = {
        "groq": (transcribe_groq, "groq_whisper"),
        "gemini": (transcribe_gemini, "gemini_3_5_transcribe"),
        "deepgram": (_deepgram, "deepgram"),
    }
    labels = {"groq": "Groq", "gemini": "Gemini Transcribe", "deepgram": "Deepgram"}

    for name in order:
        if result is not None:
            break
        fn, source_name = hosted[name]
        try:
            result = fn(audio_path, log)
            provider = source_name
        except Exception as e:  # noqa: BLE001
            errors.append(f"{labels[name]} failed: {e}")
            log and log(f"⚠️ {errors[-1]} — trying next hosted ASR")

    if result is None:
        # Local fallback
        local_fallback = True
        try:
            result = _whisper_local(audio_path, log)
            provider = "local_whisper"
        except Exception as e:  # noqa: BLE001
            errors.append(f"Local whisper failed: {e}")
            raise RuntimeError("; ".join(errors) or "No transcription backend available")

    # Forced alignment policy — driven by ALIGN_WORDS:
    #   "off"/"0"/"false"  -> alignment disabled entirely (fastest)
    #   "whisperx"          -> FORCE WhisperX (wav2vec2 CTC) alignment on every
    #                          path, including Groq — this is the A/B switch to
    #                          compare re-aligned timestamps against trusting
    #                          Groq directly. Falls back to the energy heuristic
    #                          when the whisperx/torch libs are not installed.
    #   anything else       -> auto (default): Groq already returns per-word CTC
    #                          timestamps (timestamp_granularities[]=word,
    #                          ~±50ms); the energy heuristic re-aligns those
    #                          through an RMS peak search (±80ms) that is COARSER
    #                          than the CTC output and costs a full audio decode.
    #                          This is exactly why OpenShorts is both faster and
    #                          more accurate: it trusts Groq's timestamps. So in
    #                          auto we skip re-alignment on the Groq path and keep
    #                          it for the local-whisper fallback (small model,
    #                          coarse word timestamps) where it still tightens
    #                          sync.
    align_mode = os.environ.get("ALIGN_WORDS", "auto").strip().lower()
    align_off = align_mode in ("0", "false", "off", "none", "")
    force_whisperx = align_mode == "whisperx"
    has_word_ts = bool(result and any(
        seg.get("words") for seg in (result.get("segments") or [])))
    want_align = (not align_off) and (force_whisperx or local_fallback or not has_word_ts)
    if want_align:
        try:
            if force_whisperx:
                # "whisperx" mode: ensure the lib is present so the A/B test is
                # meaningful (fails loud rather than silently degrading).
                import whisperx_align  # noqa: F401
            # Try WhisperX first (CTC forced alignment - better accuracy),
            # falling back to the energy heuristic when the libs are absent.
            try:
                from whisperx_align import maybe_align_with_whisperx
                result = maybe_align_with_whisperx(result, audio_path, log)
            except ImportError:
                from align import align_transcript
                result = align_transcript(result, audio_path, log)
        except Exception as e:  # noqa: BLE001
            log and log(f"⚠️ Alignment skipped: {e}")

    result.setdefault("source", provider or "whisper")
    return result


def pick_first_audio(video_path: str, work_dir: str, log=None) -> str:
    """Extract the first audio stream to mp3 (mono 16k) for fast transcription."""
    import subprocess

    out = os.path.join(work_dir, "audio.mp3")
    log and log("🎧 Extracting audio...")
    subprocess.run(
        ["ffmpeg", "-y", "-i", video_path, "-vn", "-ac", "1", "-ar", "16000",
         "-b:a", "64k", out],
        capture_output=True, check=True,
    )
    return out
