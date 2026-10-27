"""Configuration settings for Agastya Assist."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment or .env file."""

    SARVAM_API_KEY: str | None = None
    AGENT_MODEL: str = "claude-sonnet-5-5"
    DATA_DIR: Path = Path("./data")
    HEADLESS: bool = True
    # "" = Playwright's bundled Chromium; "chrome" = the installed Google Chrome
    BROWSER_CHANNEL: str = ""
    DEFAULT_LANGUAGE: str = "hi-IN"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings."""
    return Settings()
