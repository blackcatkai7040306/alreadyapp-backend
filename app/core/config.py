from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings from environment."""
  

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
