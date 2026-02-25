"""Claude API client for story generation."""

import logging
import re
from anthropic import AsyncAnthropic

from app.core.config import (
    settings,
    STORY_MAX_CHARS,
    TITLE_BY_CATEGORY,
    DESCRIBE_ENGINE_INSTRUCTION,
    OUTPUT_FORMAT_INSTRUCTION,
)


def _build_user_message(
    *,
    first_name: str,
    dream_place: str,
    energy_word: str,
    category: str,
    describe_whats_already_yours: str,
    someone_you_love: str | None = None,
) -> str:
    parts = [
        f"Describe what's already theirs (user's words — PRIMARY SOURCE for story and title):\n{describe_whats_already_yours}",
        f"First name: {first_name}",
        f"Where their dream life takes place: {dream_place}",
        f"Their energy word: {energy_word}",
        f"Category: {category}",
    ]
    if someone_you_love:
        parts.append(f"Someone they love (include in the story if it fits): {someone_you_love}")
    return "\n\n".join(parts)


def _parse_title_and_body(raw: str) -> tuple[str, str]:
    """Parse 'TITLE: ...' from first line; rest is body."""
    raw = raw.strip()
    title_match = re.match(r"^TITLE:\s*(.+?)(?:\n|$)", raw, re.IGNORECASE | re.DOTALL)
    if title_match:
        title = title_match.group(1).strip()
        body = raw[title_match.end() :].strip().lstrip("\n").strip()
    else:
        title = ""
        body = raw
    return title, body


def _cap_to_chars(text: str, max_chars: int) -> str:
    """Return text truncated to at most max_chars (by character)."""
    if len(text) <= max_chars:
        return text
    return text[: max_chars].rstrip()


async def generate_story(
    first_name: str,
    dream_place: str,
    energy_word: str,
    category: str,
    describe_whats_already_yours: str,
    someone_you_love: str | None = None,
    system_prompt: str | None = None,
) -> tuple[str, str]:
    """Generate a past-tense personal story. Returns (title, content). Content is capped at 2600 characters."""
    if not settings.ANTHROPIC_API_KEY:
        raise ValueError("ANTHROPIC_API_KEY is not set")
    base_prompt = (system_prompt or settings.STORY_SYSTEM_PROMPT or "").strip()
    if not base_prompt:
        raise ValueError("STORY_SYSTEM_PROMPT is not set; configure it in config.py or .env")
    prompt = base_prompt.rstrip() + DESCRIBE_ENGINE_INSTRUCTION + OUTPUT_FORMAT_INSTRUCTION
    user_message = _build_user_message(
        first_name=first_name,
        dream_place=dream_place,
        energy_word=energy_word,
        category=category,
        describe_whats_already_yours=describe_whats_already_yours,
        someone_you_love=someone_you_love,
    )

    client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    try:
        message = await client.messages.create(
            model=settings.CLAUDE_STORY_MODEL,
            max_tokens=settings.CLAUDE_STORY_MAX_TOKENS,
            system=prompt,
            messages=[{"role": "user", "content": user_message}],
        )
    except Exception as e:
        logging.exception("Claude API error: %s", e)
        raise

    if not message.content or not message.content[0].text:
        raise ValueError("Claude returned no text")

    raw = message.content[0].text.strip()
    title, body = _parse_title_and_body(raw)
    if not title and category in TITLE_BY_CATEGORY:
        title = TITLE_BY_CATEGORY[category]
    body = _cap_to_chars(body, STORY_MAX_CHARS)
    return title, body
