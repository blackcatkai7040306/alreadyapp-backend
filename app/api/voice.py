import io
import logging
import os
import tempfile
import uuid
from datetime import datetime, timezone

import httpx
from mutagen import File as MutagenFile
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from typing import Literal

from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.elevenlabs import NARRATION_SPEED_VALUES, add_voice, text_to_speech
from app.core.supabase_client import get_supabase

router = APIRouter(prefix="/voice", tags=["voice"])


def _raise_http_from_httpx(e: BaseException) -> None:
    if isinstance(e, httpx.HTTPStatusError):
        raise HTTPException(status_code=e.response.status_code, detail=f"ElevenLabs API error: {e.response.text}")
    raise HTTPException(status_code=502, detail="Voice service error")


@router.post("/clone")
async def clone_voice(
    user_id: int = Form(...),
    name: str = Form(...),
    files: list[UploadFile] = File(...),
):
    """Create a voice clone from uploaded audio; returns ElevenLabs voice_id."""
    if not files:
        raise HTTPException(status_code=400, detail="At least one audio file is required")

    file_tuples: list[tuple[str, bytes, str]] = []
    for f in files:
        ct = f.content_type or "application/octet-stream"
        if not ct.startswith("audio/"):
            raise HTTPException(status_code=400, detail=f"Invalid file type: {f.filename or 'unknown'}. Use audio (MP3, WAV, etc.).")
        content = await f.read()
        if not content:
            raise HTTPException(status_code=400, detail=f"File is empty: {f.filename or 'unknown'}.")
        file_tuples.append((f.filename or "audio", content, ct))

    try:
        return await add_voice(name=name, files=file_tuples, user_id=user_id, remove_background_noise= 'false')
    except (httpx.HTTPStatusError, httpx.RequestError) as e:
        _raise_http_from_httpx(e)


class SpeakRequest(BaseModel):
    voice_id: str = Field(..., min_length=1)
    story_id: int = Field(..., description="Story id; story text is read from Stories.story")
    model_id: str = Field(default="eleven_multilingual_v2")
    narration_speed: Literal["slow", "normal", "very_fast", "fast"] = Field(
        default="normal",
        description="Slow (0.85x), Normal (1.0x), Very Fast (1.15x), Fast (1.2x); ElevenLabs max 1.2x",
    )


@router.post("/speak")
async def speak(request: SpeakRequest):
    """Get story text from Stories by story_id; return existing playUrl if already played, else TTS, store, return URL."""
    supabase = get_supabase()
    r = supabase.table("Stories").select("story, playUrl").eq("id", request.story_id).execute()
    rows = r.data or []
    row = rows[0] if rows else {}
    play_url = (row.get("playUrl") or row.get("playurl") or "").strip()
    now_iso = datetime.now(timezone.utc).isoformat()
    if play_url:
        supabase.table("Stories").update({"last_played": now_iso}).eq("id", request.story_id).execute()
        return {"url": play_url, "content_type": "audio/mpeg"}

    text = (row.get("story") or row.get("Story") or "").strip()
    if not text:
        raise HTTPException(status_code=404 if not rows else 400, detail="Story not found or has no story text")

    try:
        audio_bytes, content_type = await text_to_speech(
            voice_id=request.voice_id,
            text=text,
            model_id=request.model_id,
            speed=NARRATION_SPEED_VALUES[request.narration_speed],
        )
    except (httpx.HTTPStatusError, httpx.RequestError) as e:
        _raise_http_from_httpx(e)

    play_length = None
    try:
        audio = MutagenFile(io.BytesIO(audio_bytes))
        if audio is not None and hasattr(audio, "info") and audio.info is not None:
            play_length = round(audio.info.length, 2)
    except Exception as e:
        logging.warning("Could not get audio duration: %s", e)

    public_url = None
    if settings.SUPABASE_URL and settings.SUPABASE_KEY:
        bucket = settings.SUPABASE_STORAGE_BUCKET
        ext = "mp3" if "mpeg" in content_type or "mp3" in content_type else "mp4"
        path = f"{request.voice_id}/{uuid.uuid4().hex}.{ext}"
        try:
            with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as tmp:
                tmp.write(audio_bytes)
                tmp_path = tmp.name
            try:
                supabase = get_supabase()
                supabase.storage.from_(bucket).upload(
                    path,
                    tmp_path,
                    file_options={"contentType": str(content_type), "upsert": "true"},
                )
                public_url = supabase.storage.from_(bucket).get_public_url(path)
                update_payload = {"storage": path, "playUrl": public_url, "last_played": now_iso}
                if play_length is not None:
                    update_payload["play_length"] = play_length
                print(play_length)
                supabase.table("Stories").update(update_payload).eq("id", request.story_id).execute()
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
        except Exception as e:
            logging.exception("Supabase storage upload failed: %s", e)
    return {"url": public_url, "content_type": content_type}
