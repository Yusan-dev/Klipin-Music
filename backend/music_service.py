"""Standalone Klipin Music Studio API service (`uvicorn music_service:app`).

Reads the exact same `backend/.env` as the main backend, but exposes ONLY:
  - `/api/music/*` (Suno Studio generation, cover arrangement, personas, lyrics, SSE)
  - `/api/auth/*` (Google OAuth & session verification)
  - `/api/pricing`, `/api/billing/*`, `/api/payment/*` (shared credits & checkout)
  - `/videos/*` (signed audio, WAV, LRC, stems, cover art streaming)
  - `/health`

All Video Clipper routes (`/api/process`, `/api/batch`, `/api/projects/*`, etc.)
are stripped so Klipin Music can be hosted on its own container/domain without
mixing with video clipping workloads.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
os.environ.setdefault("KLIPIN_SERVICE_MODE", "music")

from fastapi.routing import APIRoute, Mount  # noqa: E402
import app as _main_app  # noqa: E402

ALLOWED_PREFIXES = (
    "/api/music",
    "/api/auth",
    "/api/pricing",
    "/api/billing",
    "/api/payment",
    "/videos",
    "/health",
    "/docs",
    "/openapi.json",
    "/redoc",
)


def _is_music_route(route) -> bool:
    path = getattr(route, "path", "") or ""
    if any(path == prefix or path.startswith(prefix + "/") for prefix in ALLOWED_PREFIXES):
        return True
    if isinstance(route, Mount) and path.startswith("/videos"):
        return True
    return False


app = _main_app.app
app.title = "Klipin Music Studio API"
app.description = "Standalone Suno-style AI Music & Cover Studio (decoupled from Video Clipper)."
app.router.routes = [r for r in app.router.routes if _is_music_route(r)]


@app.get("/api/music/service-info")
async def music_service_info():
    return {
        "service": "klipin-music-standalone",
        "mode": os.environ.get("KLIPIN_SERVICE_MODE", "music"),
        "env_loaded": (ROOT / ".env").is_file(),
        "video_clipper_routes_enabled": False,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8002")))
