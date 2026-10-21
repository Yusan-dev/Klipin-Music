"""Google Sign-In for Klipin — OAuth2 authorization-code flow + sessions.

Security notes
--------------
- Session tokens are `secrets.token_urlsafe(32)` (256 bits of entropy), stored
  ONLY as a SHA-256 hash in SQLite. A database leak cannot be replayed.
- The raw token is returned to the browser once, over HTTPS, via a one-time
  claim endpoint keyed by the OAuth `state` (CSRF token). `state` is generated
  server-side, short-lived, and single-use.
- Every mutating API route requires `Authorization: Bearer <token>`; auth
  endpoints are rate-limited per IP to slow brute-force / enumeration.
- Token expiry: AUTH_SESSION_HOURS (default 168h = 7 days). The frontend
  stores the token in localStorage; on 401 it is dropped and the user is
  treated as signed out.
"""
import hashlib
import os
import secrets
import time
import urllib.parse

import httpx

import db

AUTH_STATE_TTL = 600          # OAuth `state` lifetime (seconds)
CLAIM_TTL = 120               # how long a fresh session may be claimed via state
RATE_LIMIT_WINDOW = 60        # seconds
RATE_LIMIT_MAX = 30           # auth requests per IP per window
_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
SCOPE = "openid email profile"

# pending one-time claims: state -> (raw_token, expires_at)
_claims: dict = {}
# simple per-IP sliding rate limiter: ip -> [(ts, count), ...]
_hits: dict = {}


def is_configured() -> bool:
    return bool(os.environ.get("GOOGLE_CLIENT_ID")
                and os.environ.get("GOOGLE_CLIENT_SECRET"))


def base_url(request=None) -> str:
    """Origin used for the Google login redirect_uri.

    Resolution order:
      1. If `request` carries a Host, try in order:
         a. any subdomain of a known ad-hoc tunnel (trycloudflare.com,
            ngrok-free.app / .dev, ngrok.io) → infer https://{host}
            directly, so login on a phone works via a fresh tunnel WITHOUT
            having to pre-register each random subdomain.
         b. a Host matching AUTH_ALLOWED_ORIGINS (localhost / klip-in.id).
      2. AUTH_BASE_URL explicit override.
      3. PUBLIC_BASE_URL (the tunnel used for publishing).
    """
    allowed = [
        o.strip().rstrip("/") for o in
        os.environ.get("AUTH_ALLOWED_ORIGINS", "").split(",") if o.strip()
    ]
    if request is not None:
        # Trust X-Forwarded-Host first (vite dev proxy / nginx), then Host.
        host = request.headers.get("x-forwarded-host") or \
            request.headers.get("host", "")
        if host:
            host = host.strip().lower().split("/", 1)[0]
            # (1a) live tunnel subdomains — treat as trusted public origin
            if host.endswith(".trycloudflare.com") or \
               host.endswith(".ngrok-free.app") or \
               host.endswith(".ngrok-free.dev") or \
               host.endswith(".ngrok.io"):
                return f"https://{host}"
            # (1b) explicit allow-listed origin (localhost / klip-in.id / www)
            for origin in allowed:
                netloc = origin.split("//", 1)[-1].split("/", 1)[0]
                if netloc == host:
                    return origin.rstrip("/")
    return (os.environ.get("AUTH_BASE_URL") or
            os.environ.get("PUBLIC_BASE_URL") or "").rstrip("/")


def redirect_uri(request=None) -> str:
    return f"{base_url(request)}/api/auth/callback/google"


def session_hours() -> int:
    return max(1, int(os.environ.get("AUTH_SESSION_HOURS", "168")))


def _client() -> tuple[str, str]:
    return (os.environ.get("GOOGLE_CLIENT_ID", ""),
            os.environ.get("GOOGLE_CLIENT_SECRET", ""))


# ─── rate limiting ────────────────────────────────────────────────────────

def rate_limited(ip: str | None) -> bool:
    """True if `ip` has exceeded the auth rate limit (caller should 429)."""
    now = time.time()
    ip = ip or "unknown"
    hits = [t for t in _hits.get(ip, []) if t > now - RATE_LIMIT_WINDOW]
    if len(hits) >= RATE_LIMIT_MAX:
        _hits[ip] = hits
        return True
    hits.append(now)
    _hits[ip] = hits
    return False


# ─── OAuth URL + callback ────────────────────────────────────────────────

def new_state() -> str:
    """Fresh CSRF state (stored server-side by the caller)."""
    return secrets.token_urlsafe(24)


def auth_url(state: str, request=None) -> str:
    client_id, _ = _client()
    params = urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri(request),
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "online",
        "prompt": "select_account",
        "state": state,
    })
    return f"https://accounts.google.com/o/oauth2/v2/auth?{params}"


def handle_callback(code: str, request=None) -> str:
    """Exchange the code for tokens, upsert the user, return google_sub."""
    client_id, client_secret = _client()
    data = httpx.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri(request),
            "grant_type": "authorization_code",
        },
        timeout=_TIMEOUT,
    ).json()
    if not data.get("access_token"):
        err = data.get("error_description") or data.get("error") or "unknown error"
        raise RuntimeError(f"Google token exchange failed: {err}")
    # The access token is short-lived and only used to fetch identity — we do
    # NOT persist it. Identity comes from the OpenID userinfo endpoint.
    info = httpx.get(
        "https://openidconnect.googleapis.com/v1/userinfo",
        headers={"Authorization": f"Bearer {data['access_token']}"},
        timeout=_TIMEOUT,
    ).json()
    sub = info.get("sub") or ""
    if not sub:
        raise RuntimeError("Google userinfo missing `sub`")
    db.upsert_user(
        sub,
        email=info.get("email", ""),
        name=info.get("name", ""),
        picture=info.get("picture", ""),
    )
    return sub


# ─── sessions ─────────────────────────────────────────────────────────────

def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(google_sub: str) -> str:
    """Create a session and return the raw bearer token (given out once)."""
    token = secrets.token_urlsafe(32)
    db.save_session(_hash(token), google_sub,
                    time.time() + session_hours() * 3600)
    return token


def claim_session(state: str) -> str | None:
    """One-time claim: hand over the fresh token for `state`, then burn it."""
    item = _claims.pop(state, None)
    if not item or item[1] < time.time():
        return None
    return item[0]


def stash_claim(state: str, token: str) -> None:
    _claims[state] = (token, time.time() + CLAIM_TTL)


def user_from_token(token: str) -> dict | None:
    if not token:
        return None
    return db.get_session_user(_hash(token))


def revoke(token: str) -> None:
    if token:
        db.delete_session(_hash(token))


def cleanup() -> None:
    """Drop expired claims and rate-limit buckets (called on a timer)."""
    now = time.time()
    for k in [k for k, (_, exp) in _claims.items() if exp < now]:
        _claims.pop(k, None)
    for ip in [ip for ip, ts in _hits.items()
               if not any(t > now - RATE_LIMIT_WINDOW for t in ts)]:
        _hits.pop(ip, None)
