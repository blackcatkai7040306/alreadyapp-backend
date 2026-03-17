"""Generate TTS for a story and store in Supabase. Used by voice API and by deepen flow."""

import io
import logging
import os
import re
import tempfile
import uuid
from datetime import datetime, timezone

from app.core.config import settings
from app.core.elevenlabs import NARRATION_SPEED_VALUES, text_to_speech
from app.core.supabase_client import get_supabase

try:
    from mutagen import File as MutagenFile
except ImportError:
    MutagenFile = None

# ElevenLabs SSML: natural pauses so the listener can process (supported by eleven_multilingual_v2)
# Longer sentence pause so it doesn't feel like "one long sentence"; comma pause for natural flow.
BREAK_AFTER_SENTENCE_S = 0.8
BREAK_AFTER_COMMA_S = 0.7


def _insert_ssml_breaks(text: str) -> str:
    """Insert ElevenLabs <break time="Xs" /> after sentence endings and commas for natural narration pacing."""
    if not text or not text.strip():
        return text
    # After sentence-ending punctuation: add a short pause
    text = re.sub(r"([.!?])(\s+|$)", r"\1 <break time=\"" + str(BREAK_AFTER_SENTENCE_S) + r"s\" /> \2", text)
    # After commas (when followed by space): add a shorter pause; avoid excessive breaks
    text = re.sub(r",(\s+)", r", <break time=\"" + str(BREAK_AFTER_COMMA_S) + r"s\" />\1", text)
    return text.strip()


async def generate_and_store_story_audio(
    *,
    story_id: int,
    voice_id: str,
    text: str | None = None,
    model_id: str = "eleven_multilingual_v2",
    speed: float | None = None,
    narration_speed: str = "slow",
) -> dict | None:
    """
    Generate TTS for a story and store in Supabase. If text is not provided, load from Stories by story_id.
    Returns {"url": ..., "content_type": ...} or None if story not found / no text / upload skipped.
    Raises on TTS errors.
    """
    if not text or not text.strip():
        supabase = get_supabase()
        r = supabase.table("Stories").select("story").eq("id", story_id).or_("is_deleted.eq.false,is_deleted.is.null").execute()
        rows = r.data or []
        row = rows[0] if rows else {}
        text = (row.get("story") or row.get("Story") or "").strip()
    if not text:
        return None

    text_with_breaks = _insert_ssml_breaks(text)
    speed_val = 0.7
    audio_bytes, content_type = await text_to_speech(
        voice_id=voice_id,
        text=text_with_breaks,
        model_id=model_id,
        speed=speed_val,
    )

    play_length = None
    if MutagenFile:
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
        path = f"{voice_id}/{uuid.uuid4().hex}.{ext}"
        now_iso = datetime.now(timezone.utc).isoformat()
        try:
            with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as tmp:
                tmp.write(audio_bytes)
                tmp_path = tmp.name
            try:
                supabase.storage.from_(bucket).upload(
                    path,
                    tmp_path,
                    file_options={"contentType": str(content_type), "upsert": "true"},
                )
                public_url = supabase.storage.from_(bucket).get_public_url(path)
                update_payload = {"storage": path, "playUrl": public_url, "last_played": now_iso, "voice_id": voice_id}
                if play_length is not None:
                    update_payload["play_length"] = play_length
                supabase.table("Stories").update(update_payload).eq("id", story_id).execute()
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
        except Exception as e:
            logging.exception("Supabase storage upload failed for story %s: %s", story_id, e)

    return {"url": public_url, "content_type": content_type}
