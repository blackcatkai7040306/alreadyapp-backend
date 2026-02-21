"""Voice cloning and TTS endpoints."""

import httpx
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from typing import Literal

from pydantic import BaseModel, Field

from app.core.elevenlabs import NARRATION_SPEED_VALUES, add_voice, text_to_speech

router = APIRouter(prefix="/voice", tags=["voice"])


def _raise_http_from_httpx(e: BaseException) -> None:
    if isinstance(e, httpx.HTTPStatusError):
        raise HTTPException(status_code=e.response.status_code, detail=f"ElevenLabs API error: {e.response.text}")
    raise HTTPException(status_code=502, detail="Voice service error")


@router.post("/clone")
async def clone_voice(
    name: str = Form(...),
    remove_background_noise: bool = Form(False),
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
        return await add_voice(name=name, files=file_tuples, remove_background_noise=remove_background_noise)
    except (httpx.HTTPStatusError, httpx.RequestError) as e:
        _raise_http_from_httpx(e)


class SpeakRequest(BaseModel):
    text: str = Field(..., min_length=1)
    voice_id: str = Field(..., min_length=1)
    model_id: str = Field(default="eleven_multilingual_v2")
    narration_speed: Literal["low", "normal", "fast"] = Field(default="normal")


@router.post("/speak", response_class=Response)
async def speak(request: SpeakRequest):
    """Convert text to speech; returns audio (e.g. MP3) for playback."""
    try:
        audio_bytes, content_type = await text_to_speech(
            voice_id=request.voice_id,
            text=request.text,
            model_id=request.model_id,
            speed=NARRATION_SPEED_VALUES[request.narration_speed],
        )
    except (httpx.HTTPStatusError, httpx.RequestError) as e:
        _raise_http_from_httpx(e)
    return Response(content=audio_bytes, media_type=content_type)
