"""Authenticated persona audition API; media is never put in /videos."""
import asyncio

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

import db
import persona_previews as previews
import voice_personas


class DesignRequest(BaseModel):
    seed: int = Field(ge=0, lt=2**31)
    vocal_gender: str = "auto"
    warmth: int = Field(default=0, ge=-100, le=100)
    brightness: int = Field(default=0, ge=-100, le=100)
    texture: str = "smooth"
    energy: str = "balanced"
    vocal_register: str = Field(default="mid", alias="register")


class SaveRequest(BaseModel):
    name: str = Field(default="Penyanyi saya", max_length=80)


async def _call(fn, *args):
    try:
        return await asyncio.to_thread(fn, *args)
    except (LookupError, FileNotFoundError) as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


def router_for(require_user):
    router = APIRouter(prefix="/api/music")

    @router.get("/voice-previews")
    async def listing(user=Depends(require_user)):
        return {"previews": await _call(previews.recent, user["google_sub"]),
                "capability": await _call(previews.capability)}

    @router.post("/voice-previews")
    async def create(req: DesignRequest, user=Depends(require_user)):
        return await _call(previews.begin, user["google_sub"], req.model_dump(by_alias=True))

    @router.get("/voice-previews/{preview_id}")
    async def status(preview_id: str, user=Depends(require_user)):
        return await _call(previews.poll, preview_id, user["google_sub"])

    @router.get("/voice-previews/{preview_id}/audio")
    async def audio(preview_id: str, user=Depends(require_user)):
        data = await _call(previews.audio, preview_id, user["google_sub"])
        return Response(data, media_type="audio/wav", headers={"Cache-Control": "private, no-store"})

    @router.post("/voice-previews/{preview_id}/save")
    async def save(preview_id: str, req: SaveRequest, user=Depends(require_user)):
        row = await _call(previews.save, preview_id, user["google_sub"], req.name)
        row.pop("voice_ref_path", None)
        row["has_voice_sample"] = True
        row["voice_conversion_ready"] = (await _call(previews.capability))["ready"]
        return {"persona": row}

    @router.get("/personas/{persona_id}/audio")
    async def persona_audio(persona_id: str, user=Depends(require_user)):
        user_id = user.get("google_sub") or user.get("id") or str(user.get("email") or "")
        row = db.get_music_persona(persona_id, user_id)
        if not row:
            raise HTTPException(404, "Persona tidak ditemukan.")
        ref_path = row.get("voice_ref_path")
        if not ref_path:
            try:
                row = previews.materialize_persona_voice(user_id, row)
                ref_path = row.get("voice_ref_path")
            except Exception:
                pass
        if not ref_path:
            raise HTTPException(404, "Persona ini belum memiliki contoh suara.")
        data = await _call(voice_personas.read_sample, user_id, persona_id, ref_path)
        return Response(
            data,
            media_type="audio/wav",
            headers={
                "Cache-Control": "private, no-store",
                "Content-Disposition": f'inline; filename="{persona_id}.wav"',
            },
        )

    return router
