"""Claude API client for story generation."""

import logging
import re
from anthropic import AsyncAnthropic

from app.core.config import settings

# Story length: 1000–2600 characters (letters). Enforced in code.
STORY_MIN_CHARS = 1000
STORY_MAX_CHARS = 2600

# Fallback title by category when model doesn't output TITLE: line (style: "A Love That Was Already Yours", "The Abundance That Arrived")
TITLE_BY_CATEGORY = {
    "Love": "A Love That Was Already Yours",
    "Money": "The Abundance That Arrived",
    "Career": "The Career That Was Already Yours",
    "Health": "The Vitality That Was Already Yours",
    "Home": "The Home That Was Already Yours",
}

# Output format and the rule that describe_whats_already_yours is the primary engine (no default prompt; use config STORY_SYSTEM_PROMPT only).
DESCRIBE_ENGINE_INSTRUCTION = """

**PRIMARY ENGINE — "Describe what's already theirs" (user's words):**
This is the most important input. The story AND the title MUST be driven directly by this text. Do not substitute a generic or beautiful narrative. Every core idea, feeling, and detail in the story must come from what the user wrote. If their words are short, vague, or unusual, the story must still reflect and expand only from those words — never invent a different desire. The title must also reflect this same specific desire, not a generic category headline."""

OUTPUT_FORMAT_INSTRUCTION = f"""

**Output format (follow exactly):**
1. First line: TITLE: <your title>
   Title style: "A Love That Was Already Yours", "The Love You'd Always Known", "The Abundance That Arrived" — short, evocative. The title MUST reflect the user's specific "describe what's already theirs" content, not a generic category.
2. One blank line.
3. Then the story body only (no headers). The story must be between {STORY_MIN_CHARS} and {STORY_MAX_CHARS} characters. Do not exceed {STORY_MAX_CHARS} characters."""


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
