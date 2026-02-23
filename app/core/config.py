from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings from environment."""

    ELEVENLABS_API_KEY: str = ""
    ELEVENLABS_BASE_URL: str = "https://api.elevenlabs.io"
    SUPABASE_URL: str = ""
    SUPABASE_KEY: str = ""
    SUPABASE_STORAGE_BUCKET: str = "Record-Stories"

    ANTHROPIC_API_KEY: str = ""
    CLAUDE_STORY_MODEL: str = "claude-sonnet-4-20250514"
    CLAUDE_STORY_MAX_TOKENS: int = 900  # ~600 words max; keeps output within 400–600 word target
    STORY_SYSTEM_PROMPT: str = ""
   
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
