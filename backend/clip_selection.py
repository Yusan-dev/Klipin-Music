"""
Pure helpers for the Gemini clip-selection pipeline (ported from OpenShorts).

Standard-library only so both main.py and gemini_worker.py can import it and
the logic stays unit-testable without the heavy video dependencies.
"""

# USD per 1M tokens (input, output incl. thinking), from ai.google.dev pricing.
MODEL_PRICES = {
    "gemini-3.5-flash": (1.50, 9.00),
    "gemini-3.1-flash-lite": (0.25, 1.50),
    "gemini-3-flash-preview": (0.50, 3.00),
    "gemini-2.5-flash-lite": (0.10, 0.40),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.0-flash": (0.10, 0.40),  # deprecated (shut down 2026-06-01)
}


def lookup_model_prices(model_name):
    """Longest-prefix match against MODEL_PRICES; None if unknown."""
    name = str(model_name or "").lower()
    best_key = None
    for key in MODEL_PRICES:
        if name.startswith(key) and (best_key is None or len(key) > len(best_key)):
            best_key = key
    return MODEL_PRICES[best_key] if best_key else None


def clip_count_targets(n_windows):
    """How many clips to ask the detail pass for, given the shortlist size.

    Free tier default: exactly 3 clips (matches OpenShorts/QuClippers parity).
    Klipin's old dynamic floor (2-6) / ceiling (4-12) produced 1-9 clips
    depending on material — not what free users expect. Fixed 3 clips
    guarantees consistent output length and retention.
    ``CLIP_TARGET_MIN`` / ``CLIP_TARGET_MAX`` still override for A/B runs.
    """
    import os
    n = max(1, int(n_windows or 1))

    def _override(name, current):
        raw = os.environ.get(name)
        if not raw:
            return current
        try:
            return max(1, int(raw))
        except ValueError:
            return current

    # Free tier: 3 clips fixed, dynamic range 30-180s.
    # To fix "selalu dapat 3", we allow dynamic count again
    # based on window count, but keep the floor/ceiling reasonable.
    low = max(2, min(5, n // 2 + 1))
    high = min(10, max(3, n))
    low = min(low, high)

    # Free tier: 3-8 clips, scales with material length (video 30min ->
    # many windows -> more clips). Not a fixed 3.
    low = _override("CLIP_TARGET_MIN", 3)
    high = _override("CLIP_TARGET_MAX", 8)
    # Let the ceiling follow the window count so a 30-min upload yields more
    # than 3 clips; keep it sane for short clips.
    if not os.environ.get("CLIP_TARGET_MAX"):
        high = min(8, max(3, n))
    return low, max(low, high)


def clip_duration_bounds():
    """The clip length band (seconds) the selection prompts and word-snapping
    enforce. ``CLIP_MIN_SECONDS`` / ``CLIP_MAX_SECONDS`` override the classic
    15-60 — set per job by /api/process when the user asks for a specific
    length, or by hand for A/B runs. Values are clamped to platform-sane
    limits and re-ordered so bad input degrades instead of breaking the job.
    """
    import os

    def _read(name, default):
        try:
            return float(os.environ.get(name, ""))
        except ValueError:
            return default

    # Product default: 90–120s gives free-trial users a complete story arc
    # with context and payoff, while remaining a focused short-form clip.
    # Environment overrides remain available for controlled A/B benchmarks.
    lo = _read("CLIP_MIN_SECONDS", 90.0)
    hi = _read("CLIP_MAX_SECONDS", 120.0)
    lo = min(max(lo, 5.0), 175.0)
    hi = min(max(hi, 10.0), 180.0)
    if hi < lo + 5.0:  # keep a real band: degenerate ranges starve the model
        hi = min(180.0, lo + 5.0)
    return round(lo, 3), round(hi, 3)


def rank_clip_candidates(clips, warning_score=60):
    """Rank candidates and remove near-duplicate editorial ideas.

    Overlapping windows or clips whose topic/title vocabulary is almost the
    same compete for one slot, so a batch covers different talking points.
    """
    ranked = sorted(
        list(clips or []),
        key=lambda c: quality_score(c),
        reverse=True,
    )
    unique = []
    for clip in ranked:
        text = f"{clip.get('topic', '')} {clip.get('title', '')} {clip.get('hook', '')}"
        words = {w for w in ''.join(
            ch.lower() if ch.isalnum() else ' ' for ch in text).split()
                 if len(w) >= 4}
        duplicate = False
        for kept in unique:
            overlap = max(0.0, min(float(clip.get("end", 0)), float(kept.get("end", 0))) -
                          max(float(clip.get("start", 0)), float(kept.get("start", 0))))
            shorter = max(1.0, min(
                float(clip.get("end", 0)) - float(clip.get("start", 0)),
                float(kept.get("end", 0)) - float(kept.get("start", 0))))
            kept_text = f"{kept.get('topic', '')} {kept.get('title', '')} {kept.get('hook', '')}"
            kept_words = {w for w in ''.join(
                ch.lower() if ch.isalnum() else ' ' for ch in kept_text).split()
                          if len(w) >= 4}
            union = words | kept_words
            similarity = len(words & kept_words) / len(union) if union else 0.0
            if overlap / shorter >= 0.40 or similarity >= 0.72:
                duplicate = True
                break
        if not duplicate:
            unique.append(clip)
    ranked = unique
    low_confidence = sum(
        1 for c in ranked
        if quality_score(c) < int(warning_score)
    )
    return ranked, low_confidence


def quality_score(clip):
    """Final user-facing AI score used by the strict render quality gate."""
    raw = clip.get("hook_score")
    if raw is None:
        raw = clip.get("predicted_score", 0)
    try:
        return max(0, min(100, int(float(raw or 0))))
    except (TypeError, ValueError):
        return 0


def order_by_layout_fit(clips, fits):
    """Reorder candidates by visual layout fit, then viral score.

    Pure helper for layout-aware moment finding: an explicit split/panel
    request prefers ranges where the subjects actually share the frame.
    Stable: equal fits keep viral-score order; returns (ordered, order).
    """
    indexed = list(zip(list(clips or []), list(fits or [])))
    order = sorted(range(len(indexed)),
                   key=lambda i: (indexed[i][1], quality_score(indexed[i][0])),
                   reverse=True)
    return [indexed[i][0] for i in order], order


def high_quality_candidates(clips, threshold=90):
    """Keep candidates whose final score is strictly above ``threshold``."""
    return [clip for clip in clips or [] if quality_score(clip) > int(threshold)]


def select_output_candidates(clips, limit, threshold=90):
    """Fill requested slots while preserving honest candidate scores.

    Strong candidates remain first. If too few clear the quality bar, the
    best remaining unique candidates fill the order instead of silently
    turning a six-video request into one output.
    """
    ranked = list(clips or [])
    strong = high_quality_candidates(ranked, threshold)
    strong_ids = {id(clip) for clip in strong}
    fallback = [clip for clip in ranked if id(clip) not in strong_ids]
    selected = (strong + fallback)[:max(1, int(limit or 1))]
    fallback_count = sum(id(clip) not in strong_ids for clip in selected)
    return selected, fallback_count


def compact_words(words, precision=2):
    """Round word timestamps for prompts — full float precision wastes tokens."""
    return [
        {
            "w": w.get("w", ""),
            "s": round(float(w.get("s", 0)), precision),
            "e": round(float(w.get("e", 0)), precision),
        }
        for w in words
    ]


def build_transcript_windows(transcript_result, video_duration,
                             window_seconds=90, overlap_seconds=30):
    """
    Build scoring windows aligned to Whisper segment boundaries, so a sentence
    (and usually a viral moment) is never cut in half mid-window. Windows grow
    segment by segment to roughly window_seconds (up to 1.25x for the closing
    segment) and the next window starts ~overlap_seconds before the previous
    end, also snapped to a segment start.
    """
    segments = []
    for segment in transcript_result.get("segments", []):
        text = str(segment.get("text") or "").strip()
        if not text:
            continue
        segments.append((float(segment.get("start", 0)), float(segment.get("end", 0)), text))

    windows = []
    window_index = 1
    i = 0
    n = len(segments)
    while i < n:
        w_start = segments[i][0]
        j = i
        # Extend while the NEXT segment still fits within a tolerant cap, so the
        # window closes on a segment boundary near window_seconds.
        while j + 1 < n and segments[j + 1][1] - w_start <= window_seconds * 1.25:
            j += 1
            if segments[j][1] - w_start >= window_seconds:
                break
        w_end = segments[j][1]
        windows.append({
            "id": f"window_{window_index:03d}",
            "start": round(w_start, 3),
            "end": round(w_end, 3),
            "text": " ".join(seg[2] for seg in segments[i:j + 1]),
        })
        window_index += 1

        if j >= n - 1:
            break
        # Next window starts at the first segment beginning after (end - overlap),
        # but always makes progress.
        target = w_end - overlap_seconds
        k = i + 1
        while k <= j and segments[k][0] < target:
            k += 1
        i = max(k, i + 1)

    if not windows:
        windows.append({
            "id": "window_001",
            "start": 0.0,
            "end": round(float(video_duration), 3),
            "text": str(transcript_result.get("text", "") or ""),
        })
    return windows


def snap_clip_to_words(start, end, words, video_duration,
                       min_duration=15.0, max_duration=60.0,
                       search_window=1.5, max_lead=0.35, max_tail=0.45):
    """
    Snap Gemini-proposed clip boundaries onto real word boundaries plus a bit
    of the surrounding silence. LLMs are bad at millisecond arithmetic; the
    word-level timestamps are ground truth, so cuts land in pauses instead of
    mid-word.

    words: [{'w','s','e'}, ...] for the whole video, sorted by start.
    Returns (start, end); falls back to the input if no words are nearby or
    snapping cannot satisfy the duration bounds.
    """
    original = (round(float(start), 3), round(float(end), 3))
    if not words:
        return original

    starts = [float(w.get("s", 0)) for w in words]
    ends = [float(w.get("e", 0)) for w in words]

    # START: snap to the nearest word start, then lead into the silence before it.
    new_start = float(start)
    candidates = [s for s in starts if abs(s - new_start) <= search_window]
    if candidates:
        word_start = min(candidates, key=lambda s: abs(s - new_start))
        prev_ends = [e for e in ends if e <= word_start]
        if prev_ends:
            gap = max(0.0, word_start - max(prev_ends))
            lead = min(max_lead, gap / 2)
        else:
            lead = max_lead
        new_start = max(0.0, word_start - lead)

    # END: snap to the nearest word end, then trail into the silence after it.
    new_end = float(end)
    candidates = [e for e in ends if abs(e - new_end) <= search_window]
    if candidates:
        word_end = min(candidates, key=lambda e: abs(e - new_end))
        next_starts = [s for s in starts if s >= word_end]
        if next_starts:
            gap = max(0.0, min(next_starts) - word_end)
            tail = min(max_tail, gap / 2)
        else:
            tail = max_tail
        new_end = min(float(video_duration), word_end + tail)

    # Repair duration bounds while staying on word boundaries.
    if new_end - new_start < min_duration:
        target = new_start + min_duration
        later = sorted(e for e in ends if e >= target)
        if later and later[0] - new_start <= max_duration:
            new_end = min(float(video_duration), later[0] + 0.2)
        else:
            new_end = min(float(video_duration), target)
    if new_end - new_start > max_duration:
        target = new_start + max_duration
        earlier = [e for e in ends if new_start < e <= target]
        new_end = (max(earlier) + 0.2) if earlier else target
        new_end = min(new_end, new_start + max_duration, float(video_duration))

    if new_end <= new_start or new_end - new_start < min_duration:
        new_end = min(float(video_duration), new_start + min_duration)
    return (round(new_start, 3), round(new_end, 3))
