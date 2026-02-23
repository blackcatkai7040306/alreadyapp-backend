"""Claude API client for story generation."""

import logging
from anthropic import AsyncAnthropic

from app.core.config import settings

# Default system prompt when STORY_SYSTEM_PROMPT is not set (Already Done past-tense narrative).
DEFAULT_STORY_SYSTEM_PROMPT = """You are a writer for "Already Done" — a manifestation app. Your job is to write a short, personal story (past tense, 1–3 minutes when read aloud) that feels like a memory of the user's dream life already fulfilled.

Rules:
- Write entirely in the past tense, as if it has already happened.
- Use the first name, location, energy word, category, optional loved one, and the user's own description of what's already theirs.
- Tone: warm, specific, sensory, emotional. Not generic affirmations.
- No meta-commentary; no "you had always wanted" — write as the lived experience.
- Output only the story text, no titles or section headers unless they ask for one."""

# Always appended to the system prompt so the model never exceeds the limit.
WORD_LIMIT_INSTRUCTION = "\n\n**Strict length rule:** The story must be between 400 and 600 words. Do not exceed 600 words. If you are near 600 words, conclude the story within that limit."


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
        f"First name: {first_name}",
        f"Where their dream life takes place: {dream_place}",
        f"Their energy word: {energy_word}",
        f"Category: {category}",
        f"Describe what's already theirs (user's words): {describe_whats_already_yours}",
    ]
    if someone_you_love:
        parts.append(f"Someone they love (include in the story if it fits): {someone_you_love}")
    return "\n\n".join(parts)


async def generate_story(
    first_name: str,
    dream_place: str,
    energy_word: str,
    category: str,
    describe_whats_already_yours: str,
    someone_you_love: str | None = None,
    system_prompt: str | None = None,
) -> str:
    """Call Claude to generate a past-tense personal story from onboarding inputs. Returns the story text."""
    if not settings.ANTHROPIC_API_KEY:
        raise ValueError("ANTHROPIC_API_KEY is not set")

    base_prompt = system_prompt or settings.STORY_SYSTEM_PROMPT or DEFAULT_STORY_SYSTEM_PROMPT
    prompt = base_prompt.rstrip() + WORD_LIMIT_INSTRUCTION
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

    return message.content[0].text.strip()
