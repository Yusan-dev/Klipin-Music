"""Auto-publish to TikTok / YouTube / Instagram.

Each platform uses its own OAuth2 flow (credentials in env) and its own
upload endpoint. Tokens are persisted in SQLite (db.tokens) so a user only
connects once; expired access tokens are refreshed with the stored refresh
token where the platform supports it (YouTube, TikTok).

Instagram (Reels) requires the video to be reachable at a public URL, so
PUBLIC_BASE_URL must point at a tunnel / public origin serving the FastAPI app.
"""
import math
import os
import time
import urllib.parse

import httpx

import db

PLATFORMS = ("youtube", "tiktok", "instagram")

_TIMEOUT = httpx.Timeout(300.0, connect=30.0)


# ─── configuration ────────────────────────────────────────────────────────────

def base_url() -> str:
    return os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")


def is_configured(platform: str) -> bool:
    creds = _credentials(platform)
    return bool(creds and all(creds.values()))


def _credentials(platform: str) -> dict | None:
    if platform == "youtube":
        return {
            "client_id": os.environ.get("YOUTUBE_CLIENT_ID", ""),
            "client_secret": os.environ.get("YOUTUBE_CLIENT_SECRET", ""),
        }
    if platform == "tiktok":
        return {
            "client_key": os.environ.get("TIKTOK_CLIENT_KEY", ""),
            "client_secret": os.environ.get("TIKTOK_CLIENT_SECRET", ""),
        }
    if platform == "instagram":
        return {
            "app_id": os.environ.get("FACEBOOK_APP_ID", ""),
            "app_secret": os.environ.get("FACEBOOK_APP_SECRET", ""),
        }
    return None


def redirect_uri(platform: str) -> str:
    return f"{base_url()}/api/publish/callback/{platform}"


def config_status(user_id: str) -> dict:
    return {
        p: {
            "configured": is_configured(p),
            "base_url_ok": bool(base_url()),
            "connected": bool(db.get_token(user_id, p) and
                              db.get_token(user_id, p).get("access_token")),
        }
        for p in PLATFORMS
    }


# ─── OAuth helpers ────────────────────────────────────────────────────────────

def auth_url(platform: str, state: str) -> str:
    redirect = redirect_uri(platform)
    if platform == "youtube":
        params = urllib.parse.urlencode({
            "client_id": _credentials("youtube")["client_id"],
            "redirect_uri": redirect,
            "response_type": "code",
            "scope": "https://www.googleapis.com/auth/youtube.upload "
                     "https://www.googleapis.com/auth/youtube.readonly",
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        })
        return f"https://accounts.google.com/o/oauth2/v2/auth?{params}"
    if platform == "tiktok":
        params = urllib.parse.urlencode({
            "client_key": _credentials("tiktok")["client_key"],
            "redirect_uri": redirect,
            "response_type": "code",
            "scope": "user.info.basic,video.upload,video.publish,video.list",
            "state": state,
        })
        return f"https://www.tiktok.com/v2/auth/authorize/?{params}"
    # instagram (via Facebook login)
    params = urllib.parse.urlencode({
        "client_id": _credentials("instagram")["app_id"],
        "redirect_uri": redirect,
        "response_type": "code",
        "scope": "instagram_business_basic,instagram_business_content_publish,"
                 "business_management",
        "state": state,
    })
    return f"https://www.facebook.com/v18.0/dialog/oauth?{params}"


def handle_callback(user_id: str, platform: str, code: str) -> dict:
    """Exchange the OAuth code for tokens, then store + return account info."""
    if platform == "youtube":
        data = httpx.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": _credentials("youtube")["client_id"],
                "client_secret": _credentials("youtube")["client_secret"],
                "redirect_uri": redirect_uri("youtube"),
                "grant_type": "authorization_code",
            },
            timeout=_TIMEOUT,
        ).json()
        _require_token(data, "youtube")
        account = _yt_channel(data["access_token"])
        db.save_token(
            user_id, "youtube", data["access_token"], data.get("refresh_token", ""),
            expires_at=time.time() + int(data.get("expires_in", 3600)),
            account_name=account.get("name", ""),
            account_id=account.get("id", ""),
        )
        return account

    if platform == "tiktok":
        data = httpx.post(
            "https://open.tiktokapis.com/v2/oauth/token/",
            data={
                "client_key": _credentials("tiktok")["client_key"],
                "client_secret": _credentials("tiktok")["client_secret"],
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri("tiktok"),
            },
            timeout=_TIMEOUT,
        ).json()
        _require_token(data, "tiktok")
        account = _tt_user(data["access_token"])
        db.save_token(
            user_id, "tiktok", data["access_token"], data.get("refresh_token", ""),
            expires_at=time.time() + int(data.get("expires_in", 86400)),
            account_name=account.get("display_name", ""),
            account_id=account.get("open_id", ""),
        )
        return account

    # instagram: short-lived token -> long-lived -> find IG business account
    token = httpx.get(
        "https://graph.facebook.com/v18.0/oauth/access_token",
        params={
            "client_id": _credentials("instagram")["app_id"],
            "client_secret": _credentials("instagram")["app_secret"],
            "redirect_uri": redirect_uri("instagram"),
            "code": code,
        },
        timeout=_TIMEOUT,
    ).json()
    _require_token(token, "instagram")
    long = httpx.get(
        "https://graph.facebook.com/v18.0/oauth/access_token",
        params={
            "grant_type": "fb_exchange_token",
            "client_id": _credentials("instagram")["app_id"],
            "client_secret": _credentials("instagram")["app_secret"],
            "fb_exchange_token": token["access_token"],
        },
        timeout=_TIMEOUT,
    ).json()
    _require_token(long, "instagram")
    account = _ig_account(long["access_token"])
    db.save_token(
        user_id, "instagram", long["access_token"], "",
        expires_at=time.time() + 60 * 24 * 3600,  # long-lived (~60 days)
        account_name=account.get("username", ""),
        account_id=account.get("id", ""),
    )
    return account


def _require_token(data: dict, platform: str) -> None:
    if not data.get("access_token"):
        err = data.get("error_description") or data.get("error") or \
            data.get("message") or "unknown error"
        raise RuntimeError(f"{platform} OAuth failed: {err}")


def _yt_channel(access_token: str) -> dict:
    r = httpx.get(
        "https://www.googleapis.com/youtube/v3/channels",
        params={"part": "snippet", "mine": "true"},
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=_TIMEOUT,
    )
    items = r.json().get("items") or []
    if items:
        sn = items[0].get("snippet") or {}
        return {"id": items[0].get("id", ""), "name": sn.get("title", "")}
    return {"id": "", "name": ""}


def _tt_user(access_token: str) -> dict:
    r = httpx.get(
        "https://open.tiktokapis.com/v2/user/info/",
        params={"fields": "display_name,avatar_url"},
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=_TIMEOUT,
    )
    user = ((r.json().get("data") or {}).get("user") or {})
    return {"open_id": user.get("open_id", ""),
            "display_name": user.get("display_name", "")}


def _ig_account(access_token: str) -> dict:
    """Find the first Instagram business account linked to a managed page."""
    r = httpx.get(
        "https://graph.facebook.com/v18.0/me/accounts",
        params={
            "access_token": access_token,
            "fields": "id,name,instagram_business_account{id,username}",
        },
        timeout=_TIMEOUT,
    )
    for page in r.json().get("data") or []:
        ig = (page.get("instagram_business_account") or {})
        if ig.get("id"):
            return {"id": ig["id"], "username": ig.get("username", "")}
    raise RuntimeError("No Instagram business account found — link one to a "
                       "Facebook Page first")


def _access_token(user_id: str, platform: str) -> str:
    token = db.get_token(user_id, platform)
    if not token or not token.get("access_token"):
        raise RuntimeError(f"{platform} is not connected")
    if token.get("expires_at") and token["expires_at"] < time.time() + 60:
        if not token.get("refresh_token"):
            raise RuntimeError(f"{platform} token expired — reconnect the account")
        refresh_token(user_id, platform)
        token = db.get_token(user_id, platform)
    return token["access_token"]


def access_token(user_id: str, platform: str) -> str:
    """Public wrapper around `_access_token` (used by analytics.py)."""
    return _access_token(user_id, platform)


def refresh_token(user_id: str, platform: str) -> None:
    token = db.get_token(user_id, platform)
    if not token or not token.get("refresh_token"):
        raise RuntimeError(f"{platform} has no refresh token")
    if platform == "youtube":
        data = httpx.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": _credentials("youtube")["client_id"],
                "client_secret": _credentials("youtube")["client_secret"],
                "refresh_token": token["refresh_token"],
                "grant_type": "refresh_token",
            },
            timeout=_TIMEOUT,
        ).json()
    else:  # tiktok
        data = httpx.post(
            "https://open.tiktokapis.com/v2/oauth/token/",
            data={
                "client_key": _credentials("tiktok")["client_key"],
                "client_secret": _credentials("tiktok")["client_secret"],
                "grant_type": "refresh_token",
                "refresh_token": token["refresh_token"],
            },
            timeout=_TIMEOUT,
        ).json()
    _require_token(data, platform)
    db.save_token(
        user_id, platform, data["access_token"],
        refresh_token=data.get("refresh_token", token["refresh_token"]),
        expires_at=time.time() + int(data.get("expires_in", 3600)),
        account_name=token.get("account_name", ""),
        account_id=token.get("account_id", ""),
    )


def connection_info(user_id: str, platform: str) -> dict:
    token = db.get_token(user_id, platform)
    return {
        "platform": platform,
        "configured": is_configured(platform),
        "connected": bool(token and token.get("access_token")),
        "account_name": (token or {}).get("account_name", ""),
        "account_id": (token or {}).get("account_id", ""),
        "expires_at": (token or {}).get("expires_at"),
    }


# ─── publishing ───────────────────────────────────────────────────────────────

def publish(user_id: str, platform: str, video_path: str, title: str,
            description: str = "", privacy: str = "") -> dict:
    """Upload a clip to the platform. Returns {post_url, post_id}."""
    if platform == "youtube":
        return _pub_youtube(user_id, video_path, title, description,
                            privacy or "public")
    if platform == "tiktok":
        return _pub_tiktok(user_id, video_path, title, privacy or "SELF_ONLY")
    if platform == "instagram":
        return _pub_instagram(user_id, video_path, title,
                              privacy or "public")
    raise ValueError(f"Unknown platform: {platform}")


def _pub_youtube(user_id: str, video_path: str, title: str, description: str,
                 privacy: str) -> dict:
    token = _access_token(user_id, "youtube")
    meta = {
        "snippet": {"title": title[:100], "description": description[:5000],
                    "categoryId": "22"},
        "status": {"privacyStatus": privacy},
    }
    # Resumable upload: POST metadata -> Location header = upload URL
    init = httpx.post(
        "https://www.googleapis.com/upload/youtube/v3/videos"
        "?uploadType=resumable&part=snippet,status",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json; charset=UTF-8"},
        json=meta,
        timeout=_TIMEOUT,
    )
    init.raise_for_status()
    upload_url = init.headers.get("Location")
    if not upload_url:
        raise RuntimeError("YouTube upload failed: no upload URL returned")

    size = os.path.getsize(video_path)
    with open(video_path, "rb") as fh:
        upload = httpx.put(
            upload_url,
            headers={"Content-Length": str(size), "Content-Type": "video/*"},
            content=fh.read(),
            timeout=_TIMEOUT,
        )
    upload.raise_for_status()
    vid = (upload.json().get("id") or "")
    return {"post_id": vid, "post_url": f"https://youtu.be/{vid}"}


def _pub_tiktok(user_id: str, video_path: str, title: str, privacy: str) -> dict:
    token = _access_token(user_id, "tiktok")
    size = os.path.getsize(video_path)
    chunk_size = 5 * 1024 * 1024  # 5 MiB chunks (max allowed)
    total_chunks = max(1, math.ceil(size / chunk_size))

    init = httpx.post(
        "https://open.tiktokapis.com/v2/post/publish/video/init/",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"},
        json={
            "post_info": {"title": title[:2200], "privacy_level": privacy},
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": size,
                "chunk_size": chunk_size,
                "total_chunk_count": total_chunks,
            },
        },
        timeout=_TIMEOUT,
    )
    init.raise_for_status()
    data = init.json().get("data") or {}
    publish_id = data.get("publish_id")
    upload_url = data.get("upload_url")
    if not publish_id or not upload_url:
        raise RuntimeError(f"TikTok init failed: {init.json()}")

    with open(video_path, "rb") as fh:
        upload = httpx.put(
            upload_url,
            headers={"Content-Type": "video/mp4"},
            content=fh.read(),
            timeout=_TIMEOUT,
        )
    upload.raise_for_status()

    fin = httpx.post(
        "https://open.tiktokapis.com/v2/post/publish/video/finalize/",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"},
        json={"publish_id": publish_id},
        timeout=_TIMEOUT,
    )
    fin.raise_for_status()
    data = fin.json().get("data") or {}
    post_id = data.get("post_id") or data.get("share_id") or ""
    share_url = data.get("share_url") or ""
    return {"post_id": post_id, "post_url": share_url or
            (f"https://www.tiktok.com/@{''}video/{post_id}" if post_id else "")}


def _pub_instagram(user_id: str, video_path: str, title: str, _privacy: str) -> dict:
    token = _access_token(user_id, "instagram")
    ig_id = db.get_token(user_id, "instagram")["account_id"]
    base = base_url()
    video_url = f"{base}/videos/{os.path.basename(os.path.dirname(video_path))}/" \
                 f"{os.path.basename(video_path)}"
    # The media route serves /videos only with a short-lived HMAC signature; an
    # unsigned URL returns 401. IG fetchs it server-side, so give it a long
    # TTL (24 h) and keep the path unsigned only throw a helper that can issue
    # freshly-signed URLs (same secret as the API host's app._sign_media_url).
    try:
        from app import _sign_media_url, MEDIA_TTL
        video_url = _sign_media_url(video_url, ttl=86400)
    except Exception:  # noqa: BLE001 — fall through to the raw URL if signing imports fail
        pass

    container = httpx.post(
        f"https://graph.facebook.com/v18.0/{ig_id}/media",
        params={"access_token": token},
        json={
            "media_type": "REELS",
            "video_url": video_url,
            "caption": title[:2200],
            "share_to_feed": "true",
        },
        timeout=_TIMEOUT,
    )
    container.raise_for_status()
    container_id = container.json().get("id")
    if not container_id:
        raise RuntimeError(f"Instagram container failed: {container.json()}")

    # Wait for the video to finish processing server-side.
    for _ in range(60):
        st = httpx.get(
            f"https://graph.facebook.com/v18.0/{container_id}",
            params={"fields": "status_code", "access_token": token},
            timeout=_TIMEOUT,
        ).json()
        code = st.get("status_code")
        if code == "FINISHED":
            break
        if code == "ERROR":
            raise RuntimeError(f"Instagram rejected the video: {st}")
        time.sleep(5)

    pub = httpx.post(
        f"https://graph.facebook.com/v18.0/{ig_id}/media_publish",
        params={"access_token": token},
        json={"creation_id": container_id},
        timeout=_TIMEOUT,
    )
    pub.raise_for_status()
    media_id = pub.json().get("id", "")
    return {"post_id": media_id,
            "post_url": f"https://www.instagram.com/reel/{media_id}/"}
