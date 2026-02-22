"""Voice cloning and TTS endpoints."""

import io
import uuid
import httpx
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
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
        return await add_voice(name=name, files=file_tuples, remove_background_noise= 'false')
    except (httpx.HTTPStatusError, httpx.RequestError) as e:
        _raise_http_from_httpx(e)


class SpeakRequest(BaseModel):
    text: str = Field(..., min_length=1)
    voice_id: str = Field(..., min_length=1)
    model_id: str = Field(default="eleven_multilingual_v2")
    narration_speed: Literal["low", "normal", "fast"] = Field(default="normal")


@router.post("/speak", response_class=Response)
async def speak(request: SpeakRequest):
    """Convert text to speech; store in Supabase Storage; return audio for playback."""
    try:
        audio_bytes, content_type = await text_to_speech(
            voice_id=request.voice_id,
            text=request.text,
            model_id='eleven_multilingual_v2',
            speed=NARRATION_SPEED_VALUES[request.narration_speed],
        )
    except (httpx.HTTPStatusError, httpx.RequestError) as e:
        _raise_http_from_httpx(e)

    if settings.SUPABASE_URL and settings.SUPABASE_KEY:
        bucket = settings.SUPABASE_STORAGE_BUCKET
        ext = "mp3" if "mpeg" in content_type or "mp3" in content_type else "mp4"
        path = f"Record-Stories/{request.voice_id}/{uuid.uuid4().hex}.{ext}"
        try:
            supabase = get_supabase()
            supabase.storage.from_(bucket).upload(
                path=path,
                file=io.BytesIO(audio_bytes),
                file_options={"content-type": content_type, "upsert": True},
            )
        except Exception:
            pass  # don't fail the request if storage upload fails

    return Response(content=audio_bytes, media_type=content_type)
