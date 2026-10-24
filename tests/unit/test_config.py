"""Unit tests for configuration and secret handling."""

from __future__ import annotations

from pydantic import SecretStr

from doclayout.core.config import Settings


def test_settings_secret_masking() -> None:
    """Verify GEMINI_API_KEY is masked in string representations and checked properly."""
    empty_settings = Settings(GEMINI_API_KEY=SecretStr(""))
    assert empty_settings.has_gemini_api_key is False

    placeholder_settings = Settings(GEMINI_API_KEY=SecretStr("tu_clave_de_gemini_aqui"))
    assert placeholder_settings.has_gemini_api_key is False

    valid_settings = Settings(GEMINI_API_KEY=SecretStr("test-secret-key-12345"))
    assert valid_settings.has_gemini_api_key is True
    assert "test-secret-key-12345" not in repr(valid_settings)
    assert "test-secret-key-12345" not in str(valid_settings.GEMINI_API_KEY)
