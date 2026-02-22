"""ElevenLabs API client for voice cloning."""

from __future__ import annotations

import httpx

from app.core.config import settings
from app.core.supabase_client import get_supabase

ELEVENLABS_ADD_VOICE_URL = "/v1/voices/add"


async def add_voice(
    *,
    name: str,
    user_id: int = None,
    files: list[tuple[str, bytes, str]],
    description: str | None = None,
    remove_background_noise: bool = False,
) -> dict:
    """
    Create an instant voice clone (IVC) via ElevenLabs API.

    Args:
        name: Display name for the voice.
        files: List of (filename, file_bytes, content_type) for audio samples.
        description: Optional voice description.
        remove_background_noise: Whether to remove background noise from samples.

    Returns:
        {"voice_id": "...", "requires_verification": bool}

    Raises:
        httpx.HTTPStatusError: On API error (e.g. 401, 422).
    """
    headers = {
        "xi-api-key": settings.ELEVENLABS_API_KEY,
        "Accept": "application/json",
    }
    data: dict = {
        "name": name,
        "remove_background_noise": str(remove_background_noise).lower(),
    }
    if description is not None:
        data["description"] = description

    # Build multipart: form fields + file(s)
    files_payload: list[tuple[str, tuple[str, bytes, str]]] = [
        ("files", (filename, content, content_type))
        for filename, content, content_type in files
    ]

    async with httpx.AsyncClient(base_url=settings.ELEVENLABS_BASE_URL, timeout=60.0) as client:
        response = await client.post(
            ELEVENLABS_ADD_VOICE_URL,
            headers=headers,
            data=data,
            files=files_payload,
        )
        response.raise_for_status()
        result = response.json()
        generated_voice_id = result.get("voice_id")
        if user_id is not None and generated_voice_id:
            supabase = get_supabase()
            supabase.table("Users").update({"Voice_id": generated_voice_id}).eq("id", user_id).execute()
        return result


def _tts_url(voice_id: str) -> str:
    return f"/v1/text-to-speech/{voice_id}"


# Narration speed: ElevenLabs uses a numeric speed (1.0 = normal).
NARRATION_SPEED_VALUES = {"low": 0.8, "normal": 1.0, "fast": 1.2}


async def text_to_speech(
    *,
    voice_id: str,
    text: str,
    model_id: str = "eleven_multilingual_v2",
    speed: float = 1.0,
) -> tuple[bytes, str]:
    """
    Convert text to speech using ElevenLabs TTS.

    Args:
        voice_id: ElevenLabs voice ID (from clone or prebuilt).
        text: Text to speak.
        model_id: Model to use (default: eleven_multilingual_v2).
        speed: Speech speed (1.0 = normal; <1 slower, >1 faster).

    Returns:
        (audio_bytes, content_type) e.g. (b"...", "audio/mpeg").

    Raises:
        httpx.HTTPStatusError: On API error.
    """
    headers = {
        "xi-api-key": settings.ELEVENLABS_API_KEY,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    payload = {
        "text": text,
        "model_id": model_id,
        "voice_settings": {"speed": speed},
    }

    async with httpx.AsyncClient(base_url=settings.ELEVENLABS_BASE_URL, timeout=60.0) as client:
        response = await client.post(
            _tts_url(voice_id),
            headers=headers,
            json=payload,
        )
        response.raise_for_status()
        content_type = response.headers.get("content-type", "audio/mpeg")
        return (response.content, content_type)
