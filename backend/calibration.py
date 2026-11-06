"""Prediction calibration — compares each clip's predicted score with the
real performance of its published posts, so creators (and eventually Klipin's
own ranking) know whether the AI over- or under-scores clips.

For every clip that has at least one published post with live metrics, the
predicted score (hook_score / predicted_score, 0-100) is compared against a
0-100 "real score" derived from the best post's engagement. The module returns
a per-clip breakdown plus project-level statistics (mean bias, MAE, Pearson r).

When < MIN_SAMPLES clips have real data, real_ready=False so the frontend can
show a "collecting data…" state instead of a misleading verdict.
"""
import math

import analytics as analytics_mod
import db

MIN_SAMPLES = 3


def _predicted(clip: dict) -> int | None:
    v = clip.get("hook_score")
    if v is None:
        v = clip.get("predicted_score")
    try:
        v = int(v or 0)
    except (TypeError, ValueError):
        v = 0
    return v if 1 <= v <= 100 else None


def _best_post_metrics(entries: list) -> dict | None:
    """Best (highest views) publish entry that has real metrics. returns
    {views, likes, comments, shares, reach, platform} or None."""
    best = None
    for e in entries:
        m = e.get("metrics")
        if not m or e.get("error"):
            continue
        views = int(m.get("views") or 0)
        likes = int(m.get("likes") or 0)
        comments = int(m.get("comments") or 0)
        if best is None or views > best["views"]:
            best = {"views": views, "likes": likes, "comments": comments,
                    "shares": int(m.get("shares") or 0),
                    "reach": int(m.get("reach") or 0),
                    "platform": e.get("platform", "")}
    return best


def _real_score(best: dict, max_views: int) -> int | None:
    """Engagement → 0-100. Uses the clip's views relative to the project's
    best-performing clip (views*100/max_views), blended with like ratio so a
    viral clip with few followers still scores above flat-watch filler."""
    views = best["views"]
    likes = best["likes"]
    comments = best["comments"]
    if views <= 0:
        return None
    view_factor = 100.0 * math.sqrt(views / max_views) if max_views > 0 else 50.0
    like_rate = min(1.0, likes / max(1, views * 0.2))          # normalize target ~20%
    comment_rate = min(1.0, comments / max(1, views * 0.03))   # ~3% target
    engagement = (like_rate * 0.6 + comment_rate * 0.4) * 50.0
    return max(0, min(100, int(round(view_factor * 0.6 + engagement))))



def project_calibration(job_id: str, user_id: str = "") -> dict:
    """Compare predicted vs real score for every publishable clip of a project.

    Returns {ready, samples, mean_bias, mae, pearson_r, clips: [...]}.
    Each clip row: {index, title, predicted, real, delta, verdict,
    platform, views, post_url}.
    """
    clips = db.get_clips(job_id)
    per_clip = []
    for c in clips:
        idx = c["index"]
        entries = analytics_mod.clip_analytics(job_id, idx, user_id)
        best = _best_post_metrics(entries)
        per_clip.append({"clip": c, "best": best, "entries": entries})
    max_views = max((b["views"] for _p in per_clip
                     if (b := _p["best"])), default=0)

    pairs = []
    rows = []
    for item in per_clip:
        c, best, entries = item["clip"], item["best"], item["entries"]
        predicted = _predicted(c)
        real = _real_score(best, max_views) if best else None
        entry = {
            "index": c["index"],
            "title": (c.get("title") or "").strip()[:60],
            "predicted": predicted,
            "real": real,
            "delta": (predicted - real) if (predicted is not None and real is not None) else None,
            "verdict": None,
            "platform": best.get("platform", "") if best else "",
            "views": best.get("views", 0) if best else 0,
            "post_url": "",
        }
        for e in entries:
            if e.get("post_url"):
                entry["post_url"] = e["post_url"]
                break
        if predicted is not None and real is not None:
            if entry["delta"] >= 15:
                entry["verdict"] = "over"
            elif entry["delta"] <= -15:
                entry["verdict"] = "under"
            else:
                entry["verdict"] = "on"
            pairs.append((predicted, real))
        rows.append(entry)

    out = {
        "ready": len(pairs) >= MIN_SAMPLES,
        "samples": len(pairs),
        "min_samples": MIN_SAMPLES,
        "mean_bias": None,
        "mae": None,
        "pearson_r": None,
        "clips": rows,
    }
    if pairs:
        biases = [p - r for p, r in pairs]
        out["mean_bias"] = round(sum(biases) / len(biases), 1)
        out["mae"] = round(sum(abs(b) for b in biases) / len(biases), 1)
        # _pearson returns None for a single pair or zero variance (identical
        # scores) — never round(None), that crashed the endpoint with a 500.
        r = _pearson(pairs)
        out["pearson_r"] = round(r, 3) if r is not None else None
    return out


def _pearson(pairs) -> float | None:
    if len(pairs) < 2:
        return None
    xs = [p for p, _ in pairs]
    ys = [r for _, r in pairs]
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in pairs)
    den = math.sqrt(sum((x - mx) ** 2 for x in xs) *
                    sum((y - my) ** 2 for y in ys))
    if den == 0:
        return None
    return num / den
