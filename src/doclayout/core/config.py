"""Application configuration and environment management for DocLayout-Preserve."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Centralized configuration loaded from environment variables or .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    # Gemini Developer API
    GEMINI_API_KEY: SecretStr = Field(default=SecretStr(""))
    GEMINI_MODEL: str = Field(default="gemini-2.5-flash")
    GEMINI_MAX_RPM: int = Field(default=30, ge=1, le=6000)

    # Google Cloud Platform (GCP)
    GCP_PROJECT_ID: str = Field(default="")
    GCP_PROJECT_NUMBER: str = Field(default="")
    GOOGLE_APPLICATION_CREDENTIALS: str = Field(default="private/gcp-sa.json")

    # Local Runtime & Server Settings
    APP_HOST: str = Field(default="127.0.0.1")
    APP_PORT: int = Field(default=8501, ge=1024, le=65535)
    API_HOST: str = Field(default="127.0.0.1")
    API_PORT: int = Field(default=8000, ge=1024, le=65535)
    WORKER_HEALTH_PORT: int = Field(default=8081, ge=1024, le=65535)
    ENVIRONMENT: str = Field(default="local")
    LOG_LEVEL: str = Field(default="INFO")

    # Async Queue & Encrypted TTL Artifact Storage
    REDIS_URL: str = Field(default="")
    ARTIFACT_STORE_DIR: str = Field(default=".tmp/artifacts")
    ARTIFACT_TTL_SECONDS: int = Field(default=3600, ge=60, le=604800)
    ARTIFACT_ENCRYPTION_SECRET: SecretStr = Field(default=SecretStr(""))
    MAX_CONCURRENT_JOBS_PER_SESSION: int = Field(default=5, ge=1, le=100)

    # Document Limits & Text-Fitting Thresholds
    MAX_FILE_SIZE_MB: int = Field(default=20, ge=1, le=100)
    MAX_PAGES: int = Field(default=50, ge=1, le=500)
    MIN_FONT_SCALE_RATIO: float = Field(default=0.75, ge=0.5, le=1.0)

    @property
    def has_gemini_api_key(self) -> bool:
        """Return True if a non-empty Gemini API key is configured."""
        raw = self.GEMINI_API_KEY.get_secret_value().strip()
        return bool(raw and raw != "tu_clave_de_gemini_aqui")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached singleton instance of application settings."""
    return Settings()
