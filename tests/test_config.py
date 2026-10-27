"""Tests for agastya.config."""

from pathlib import Path

from agastya.config import Settings, get_settings


def test_default_settings() -> None:
    settings = Settings(_env_file=None)
    assert settings.SARVAM_API_KEY is None
    assert settings.AGENT_MODEL == "claude-sonnet-5-5"
    assert settings.DATA_DIR == Path("./data")
    assert settings.HEADLESS is True
    assert settings.DEFAULT_LANGUAGE == "hi-IN"


def test_get_settings_cached() -> None:
    s1 = get_settings()
    s2 = get_settings()
    assert s1 is s2


def test_settings_custom_values(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        SARVAM_API_KEY="test-key-123",
        AGENT_MODEL="claude-3-5-sonnet",
        DATA_DIR=tmp_path / "custom_data",
        HEADLESS=False,
        DEFAULT_LANGUAGE="en-IN",
    )
    assert settings.SARVAM_API_KEY == "test-key-123"
    assert settings.AGENT_MODEL == "claude-3-5-sonnet"
    assert settings.DATA_DIR == tmp_path / "custom_data"
    assert settings.HEADLESS is False
    assert settings.DEFAULT_LANGUAGE == "en-IN"


def test_settings_loads_from_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("AGENT_MODEL=custom-agent\nDEFAULT_LANGUAGE=bn-IN\n")
    settings = Settings(_env_file=env_file)
    assert settings.AGENT_MODEL == "custom-agent"
    assert settings.DEFAULT_LANGUAGE == "bn-IN"


def test_settings_env_override(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SARVAM_API_KEY", "env-api-key")
    monkeypatch.setenv("AGENT_MODEL", "custom-model")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "env_data"))
    monkeypatch.setenv("HEADLESS", "false")
    monkeypatch.setenv("DEFAULT_LANGUAGE", "ta-IN")

    get_settings.cache_clear()
    settings = get_settings()
    try:
        assert settings.SARVAM_API_KEY == "env-api-key"
        assert settings.AGENT_MODEL == "custom-model"
        assert settings.DATA_DIR == tmp_path / "env_data"
        assert settings.HEADLESS is False
        assert settings.DEFAULT_LANGUAGE == "ta-IN"
    finally:
        get_settings.cache_clear()
