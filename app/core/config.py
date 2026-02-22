from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings from environment."""

    ELEVENLABS_API_KEY: str = ""
    ELEVENLABS_BASE_URL: str = "https://api.elevenlabs.io"
    SUPABASE_URL: str = ""
    SUPABASE_KEY: str = ""
    SUPABASE_STORAGE_BUCKET: str = "Record-Stories"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
