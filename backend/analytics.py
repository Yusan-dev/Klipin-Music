"""Lightweight per-clip analytics from connected platforms.

For every published post of a clip (rows in db.publishes with a post_id),
fetch live metrics straight from the platform API using the stored OAuth
tokens:

  youtube   -> Videos.list(part=statistics)   viewCount/likeCount/commentCount
  tiktok    -> POST /v2/video/query/          view_count/like_count/comment_count/share_count
  instagram -> GET /{media-id}                play_count/like_count/comments_count/reach

All responses are normalized to {views, likes, comments, shares?, reach?} so
the frontend renders one shape regardless of platform. A per-entry `error`
string is returned instead of failing the whole call when one platform is
down / not connected.
"""
import httpx

import db

_TIMEOUT = httpx.Timeout(30.0, connect=15.0)

METRIC_LABELS = {
    "views": "Views",
    "likes": "Likes",
    "comments": "Comments",
    "shares": "Shares",
    "reach": "Reach",
}


def clip_analytics(job_id: str, clip_idx: int, user_id: str = "") -> list:
    """Live metrics for every published post of a clip.

    Returns [{platform, post_id, post_url, published_at, metrics, error}]
    sorted newest-first. `metrics` is None while `error` is set when the
    platform call failed.
    """
    rows = db.list_publishes(job_id, clip_idx)
    out = []
    for row in rows:
        if row.get("status") != "published":
            continue
        post_id = row.get("post_id") or ""
        if not post_id:
            continue
        entry = {
            "platform": row["platform"],
            "post_id": post_id,
            "post_url": row.get("post_url", ""),
            "published_at": row.get("created_at"),
            "metrics": None,
            "error": None,
        }
        try:
            entry["metrics"] = _fetch(user_id, row["platform"], post_id)
        except Exception as e:  # noqa: BLE001
            entry["error"] = str(e)
        out.append(entry)
    return out


def _fetch(user_id: str, platform: str, post_id: str) -> dict:
    if platform == "youtube":
        return _yt_stats(user_id, post_id)
    if platform == "tiktok":
        return _tt_stats(user_id, post_id)
    if platform == "instagram":
        return _ig_stats(user_id, post_id)
    raise ValueError(f"Unknown platform: {platform}")


def _yt_stats(user_id: str, post_id: str) -> dict:
    from publish import access_token

    token = access_token(user_id, "youtube")
    r = httpx.get(
        "https://www.googleapis.com/youtube/v3/videos",
        params={"part": "statistics", "id": post_id},
        headers={"Authorization": f"Bearer {token}"},
        timeout=_TIMEOUT,
    )
    r.raise_for_status()
    items = r.json().get("items") or []
    if not items:
        raise RuntimeError("Video not found on YouTube")
    st = items[0].get("statistics") or {}
    return {
        "views": _int(st.get("viewCount")),
        "likes": _int(st.get("likeCount")),
        "comments": _int(st.get("commentCount")),
    }


def _tt_stats(user_id: str, post_id: str) -> dict:
    from publish import access_token

    token = access_token(user_id, "tiktok")
    r = httpx.post(
        "https://open.tiktokapis.com/v2/video/query/",
        params={"fields": "id,view_count,like_count,comment_count,share_count"},
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"},
        json={"filters": {"video_ids": [post_id]}},
        timeout=_TIMEOUT,
    )
    r.raise_for_status()
    body = r.json()
    err = body.get("error") or {}
    if err.get("code") not in (None, "", "ok"):
        raise RuntimeError(f"TikTok: {err.get('message') or err.get('code')}")
    videos = ((body.get("data") or {}).get("videos")) or []
    if not videos:
        raise RuntimeError("Video not found on TikTok")
    v = videos[0]
    return {
        "views": _int(v.get("view_count")),
        "likes": _int(v.get("like_count")),
        "comments": _int(v.get("comment_count")),
        "shares": _int(v.get("share_count")),
    }


def _ig_stats(user_id: str, post_id: str) -> dict:
    from publish import access_token

    token = access_token(user_id, "instagram")
    r = httpx.get(
        f"https://graph.facebook.com/v18.0/{post_id}",
        params={
            "fields": "like_count,comments_count,play_count,reach",
            "access_token": token,
        },
        timeout=_TIMEOUT,
    )
    r.raise_for_status()
    d = r.json()
    if d.get("error"):
        raise RuntimeError(f"Instagram: {d['error'].get('message')}")
    return {
        "views": _int(d.get("play_count")),
        "likes": _int(d.get("like_count")),
        "comments": _int(d.get("comments_count")),
        "reach": _int(d.get("reach")),
    }


def _int(v) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0
