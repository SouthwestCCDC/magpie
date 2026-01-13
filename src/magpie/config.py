"""Magpie configuration module with Pydantic settings management."""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Literal, Self

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class MagpieSettings(BaseSettings):
    """Configuration settings for Magpie artifact storage.

    Settings can be configured via environment variables with MAGPIE_ prefix.
    Derived paths (temp_path, database_path) are computed from storage_path
    unless explicitly overridden.
    """

    model_config = SettingsConfigDict(
        env_prefix="MAGPIE_",
        env_file=".env",
        env_file_encoding="utf-8",
    )

    storage_path: Path = Path("/data/artifacts")
    temp_path: Path | None = None
    database_path: Path | None = None
    retention_days: int = 90
    debug: bool = False

    # GC settings
    gc_lock_path: Path = Path("/var/run/magpie-gc.lock")  # MAGPIE_GC_LOCK_PATH

    # Upload limits
    max_upload_size: int | None = None  # MAGPIE_MAX_UPLOAD_SIZE (bytes)

    # S3 backup settings (optional)
    s3_bucket: str | None = None  # MAGPIE_S3_BUCKET

    # Logging settings
    log_format: Literal["json", "console"] = "console"  # MAGPIE_LOG_FORMAT

    # Observability settings
    sentry_dsn: str | None = None  # MAGPIE_SENTRY_DSN
    otel_enabled: bool = False  # MAGPIE_OTEL_ENABLED
    otel_endpoint: str | None = None  # MAGPIE_OTEL_ENDPOINT
    otel_service_name: str = "magpie"  # MAGPIE_OTEL_SERVICE_NAME

    @model_validator(mode="after")
    def derive_paths(self) -> Self:
        """Derive temp_path and database_path from storage_path if not set."""
        if self.temp_path is None:
            self.temp_path = self.storage_path / ".tmp"
        if self.database_path is None:
            self.database_path = self.storage_path / ".magpie.db"
        return self


@functools.lru_cache(maxsize=1)
def get_settings() -> MagpieSettings:
    """Get cached MagpieSettings instance (singleton pattern).

    Returns:
        Cached MagpieSettings instance with values from environment.
    """
    return MagpieSettings()
