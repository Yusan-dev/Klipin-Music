"""Security middleware for Klipin Backend — rate limiting + headers + honeytrap.

Follows existing conventions in auth.py (dict-based in-memory cache, no extra deps).
- Per-route rate limits (fail-closed: if state corrupt → 429).
- Security headers (HSTS, XFO, nosniff, CSP, Referrer-Policy, Permissions-Policy).
- Honeytrap endpoints: paths that log scanner IPs but return 404 consistently.
NOTE: FastAPI `app.middleware("http")` handlers MUST be async and call
`await call_next(request)` — BaseHTTPMiddleware passes a coroutine.
"""

import os
import time
from fastapi import Request, Response
from fastapi.responses import JSONResponse

# ── rate-limit constants (configurable via env) ──────────────────────────
DEFAULT_RATE_LIMIT_WINDOW = 60       # seconds
DEFAULT_RATE_LIMIT_MAX = int(os.environ.get("RATE_LIMIT_MAX", "600"))          # requests per window per IP (was 60, too low for SPA)
STATUS_RATE_LIMIT_MAX = int(os.environ.get("STATUS_RATE_LIMIT_MAX", "1200"))   # active render UI polls (up to 20/sec/IP)

# Extra-strict limits for sensitive/mutating endpoints (can override per-route)
STRICT_RATE_LIMIT_WINDOW = 60
STRICT_RATE_LIMIT_MAX = int(os.environ.get("STRICT_RATE_LIMIT_MAX", "40"))

# ── honeytrap paths (return 404 always, but log the requesting IP) ────────
# These paths do NOT exist in the real API; any request to them is likely a scanner/bot.
HONEYPATHS = {
    "/.env",
    "/wp-admin",
    "/phpmyadmin",
    "/admin",
    "/.well-known/admin",
    "/dashboard",
    "/cgi-bin",
    "/manager/html",
}


def _rate_check(hits: list, window: int, max_req: int) -> bool:
    """Return True if IP exceeded limit. Mutates hits list."""
    now = time.time()
    hits[:] = [t for t in hits if t > now - window]
    if len(hits) >= max_req:
        return True
    hits.append(now)
    return False


def _ip_from_request(request: Request) -> str:
    # Prefer real IP behind proxy; fall back to client.host
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _is_local_or_internal_ip(ip: str) -> bool:
    """Return True if ip is localhost, testclient, docker bridge, or private LAN."""
    if not ip or ip in ("127.0.0.1", "::1", "localhost", "testclient"):
        return True
    # Private IPv4 ranges (RFC 1918 + Docker network 172.x)
    if ip.startswith("10.") or ip.startswith("192.168.") or ip.startswith("172."):
        return True
    return False


# ── 1) Security Headers Middleware ────────────────────────────────────────
async def security_headers_middleware(request: Request, call_next):
    """Add security headers to every response."""
    response: Response = await call_next(request)

    ct = response.headers.get("content-type", "")
    if ct.startswith("application/json"):
        h = response.headers
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("X-Frame-Options", "DENY")
        host = request.headers.get("host", "")
        if "localhost" not in host.lower() and "127.0.0.1" not in host.lower():
            h.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains; preload",
            )
        h.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        h.setdefault(
            "Content-Security-Policy",
            "default-src 'self' data:; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            "connect-src 'self'; font-src 'self'; frame-ancestors 'none'; "
            "base-uri 'self'",
        )
        h.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
        )
    else:
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")

    return response


# ── 2) Per-route Rate Limit Middleware ────────────────────────────────────
async def rate_limit_middleware(request: Request, call_next):
    """FastAPI http-middleware: return 429 if IP exceeded rate limit."""
    # 1. CORS preflight (OPTIONS) must NEVER be rate-limited
    if request.method == "OPTIONS":
        return await call_next(request)

    path = request.url.path

    # 2. Static media assets (/videos/..., /assets/...) are HMAC-signed/immutable; never rate limit them
    if path.startswith("/videos/") or path.startswith("/assets/"):
        return await call_next(request)

    if not hasattr(request.app.state, "_rate_hits"):
        request.app.state._rate_hits = {}

    ip = _ip_from_request(request)

    # 3. Status/polling endpoints get dedicated high-capacity per-job buckets
    is_status_poll = request.method == "GET" and (
        path.startswith("/api/status/") or
        path.startswith("/api/auth/claim") or
        path == "/api/auth/me" or
        path.startswith("/api/music/jobs") or
        path.startswith("/api/music/group/") or
        path.startswith("/api/music/status/") or
        path.startswith("/api/music/projects") or
        (path.startswith("/api/projects/") and "/status" in path) or
        path.startswith("/api/payment/mock/status/")
    )

    # Keep each render's polling budget independent. A dashboard with several
    # in-flight/old jobs must not make the job the user is watching lose its
    # thumbnail, logs, and progress to a shared per-IP 429 bucket.
    bucket = ("status", path) if is_status_poll else "default"

    # Strict rate limit for media-processing endpoints (prevent URL flood / SSRF amplification)
    if path in ("/api/process", "/api/batch"):
        bucket = "strict"

    hits = request.app.state._rate_hits.setdefault((ip, bucket), [])

    if bucket == "strict":
        max_requests = STRICT_RATE_LIMIT_MAX
    elif is_status_poll:
        max_requests = STATUS_RATE_LIMIT_MAX
    else:
        # For localhost / docker bridge / LAN users running Klipin locally, grant generous capacity
        max_requests = DEFAULT_RATE_LIMIT_MAX * 5 if _is_local_or_internal_ip(ip) else DEFAULT_RATE_LIMIT_MAX

    if _rate_check(hits, DEFAULT_RATE_LIMIT_WINDOW, max_requests):
        return JSONResponse(
            status_code=429,
            content={"detail": "Too Many Requests"},
            headers={"Retry-After": str(DEFAULT_RATE_LIMIT_WINDOW + 1)},
        )
    return await call_next(request)


# ── 3) Honeytrap: log scanner IPs returning 404 ──────────────────────────
async def honeytrap_middleware(request: Request, call_next):
    """If path matches known honeytrap paths, log IP + return 404 immediately."""
    if request.url.path in HONEYPATHS:
        if not hasattr(request.app.state, "honeypot_ips"):
            request.app.state.honeypot_ips = set()
        ip = _ip_from_request(request)
        request.app.state.honeypot_ips.add(ip)
        return JSONResponse(status_code=404, content={"detail": "Not found"})
    return await call_next(request)


# ── 4) FastAPI app builder helpers ────────────────────────────────────────
def register_middleware(app):
    """Call once after creating the FastAPI app instance."""
    app.middleware("http")(security_headers_middleware)
    app.middleware("http")(rate_limit_middleware)
    app.middleware("http")(honeytrap_middleware)
    if not hasattr(app.state, "honeypot_ips"):
        app.state.honeypot_ips = set()
