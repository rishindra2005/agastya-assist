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
    # Chrome remote debugging (CDP) port shared by our driver, the Playwright MCP server and the live stream.
    # 0 = pick a free port per session.
    CDP_PORT: int = 0
    PLAYWRIGHT_MCP: bool = True
    # Agent reasoning effort: lower = faster replies
    AGENT_EFFORT: str = "low"
    # Which brain runs the in-app agent: "claude" (Claude Agent SDK) or "agy" (Antigravity CLI)
    AGENT_BACKEND: str = "claude"
    AGY_MODEL: str = "gemini-3.8-flash-high"
    LIVE_QUALITY: int = 60
    LIVE_MAX_WIDTH: int = 1280
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
