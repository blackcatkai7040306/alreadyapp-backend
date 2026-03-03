import io
import logging
import os
import tempfile
import uuid
from datetime import datetime, timezone

import httpx
from mutagen import File as MutagenFile
from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
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


# Folder in project root where clone voice uploads are stored for inspection
VOICE_CLONE_UPLOADS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "uploads", "voice_clone")


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
    os.makedirs(VOICE_CLONE_UPLOADS_DIR, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    for i, f in enumerate(files):
        ct = f.content_type or "application/octet-stream"
        if not ct.startswith("audio/"):
            raise HTTPException(status_code=400, detail=f"Invalid file type: {f.filename or 'unknown'}. Use audio (MP3, WAV, etc.).")
        content = await f.read()
        if not content:
            raise HTTPException(status_code=400, detail=f"File is empty: {f.filename or 'unknown'}.")
        file_tuples.append((f.filename or "audio", content, ct))
        # Store copy in project folder for inspection
        base = (f.filename or "audio").rsplit(".", 1)[0] if (f.filename or "").find(".") >= 0 else (f.filename or "audio")
        ext = (f.filename or "audio").rsplit(".", 1)[-1].lower() if (f.filename or "").find(".") >= 0 else "bin"
        if ext in ("mp3", "wav", "m4a", "ogg", "webm", "flac"):
            pass
        else:
            ext = "bin"
        save_name = f"user{user_id}_{ts}_{i}_{base}.{ext}"
        save_path = os.path.join(VOICE_CLONE_UPLOADS_DIR, save_name)
        try:
            with open(save_path, "wb") as out:
                out.write(content)
        except OSError as e:
            logging.warning("Could not save clone audio to %s: %s", save_path, e)

    try:
        return await add_voice(name=name, files=file_tuples, user_id=user_id, remove_background_noise= 'false')
    except (httpx.HTTPStatusError, httpx.RequestError) as e:
        _raise_http_from_httpx(e)


# Default sentence used to generate a voice preview from ElevenLabs
VOICE_PREVIEW_TEXT = "Hello, how are you?"


@router.get("/preview")
async def voice_preview(voice_id: str = Query(..., min_length=1, description="ElevenLabs voice_id")):
    """Generate and return a short audio preview for the given ElevenLabs voice_id."""
    try:
        audio_bytes, content_type = await text_to_speech(
            voice_id=voice_id,
            text=VOICE_PREVIEW_TEXT,
            model_id="eleven_multilingual_v2",
            speed=1.0,
        )
    except (httpx.HTTPStatusError, httpx.RequestError) as e:
        _raise_http_from_httpx(e)
    return Response(content=audio_bytes, media_type=content_type)


class SpeakRequest(BaseModel):
    voice_id: str = Field(..., min_length=1)
    story_id: int = Field(..., description="Story id; story text is read from Stories.story")
    model_id: str = Field(default="eleven_multilingual_v2")
    narration_speed: Literal["slow", "normal", "very_fast", "fast"] = Field(
        default="normal",
        description="Slow (0.85x), Normal (1.0x), Very Fast (1.15x), Fast (1.2x); ElevenLabs max 1.2x",
    )

@router.get("/speak/{story_id}")
async def get_story_play_url(story_id: int):
    """Return the playUrl for the given story_id. 404 if story not found or playUrl not set."""
    supabase = get_supabase()
    r = supabase.table("Stories").select("playUrl").eq("id", story_id).execute()
    rows = list(r.data or [])
    if not rows:
        raise HTTPException(status_code=404, detail="Story not found")
    row = rows[0]
    play_url = (row.get("playUrl")).strip()
    if not play_url:
        raise HTTPException(status_code=404, detail="Story has no play URL yet")
    return {"playUrl": play_url}


@router.post("/generate_audio")
async def speak(request: SpeakRequest):
    print(request)
    """Get story text from Stories by story_id; return existing playUrl if already played, else TTS, store, return URL."""
    supabase = get_supabase()
    r = supabase.table("Stories").select("story").eq("id", request.story_id).execute()
    rows = r.data or []
    row = rows[0] if rows else {}
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
                supabase.table("Stories").update(update_payload).eq("id", request.story_id).execute()
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
        except Exception as e:
            logging.exception("Supabase storage upload failed: %s", e)
    return {"url": public_url, "content_type": content_type}
