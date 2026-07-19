"""Magpie configuration module with Pydantic settings management."""

from __future__ import annotations

import functools
import ipaddress
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class MagpieSettings(BaseSettings):
    """Configuration settings for Magpie artifact storage.

    Settings can be configured via environment variables with MAGPIE_ prefix.
    temp_path is derived from storage_path unless explicitly overridden.
    database_path defaults to /data/magpie.db and can be overridden via MAGPIE_DATABASE_PATH.
    """

    model_config = SettingsConfigDict(
        env_prefix="MAGPIE_",
        env_file=".env",
        env_file_encoding="utf-8",
        # Docker Compose's `${VAR:-}` (no default value after the `-`)
        # resolves an unset shell var to a present-but-blank env entry, not
        # an absent one -- every optional MAGPIE_* var passed through
        # docker-compose.yml this way (MAGPIE_MAX_UPLOAD_SIZE, MAGPIE_
        # ADMIN_TOKEN_SINK, etc.) hits this. Without env_ignore_empty, a
        # blank string fails Literal/int field validation outright instead
        # of falling through to the field's own default -- for
        # admin_token_sink specifically, that also means bypassing
        # deliver_admin_token's own clean "no sink configured" error in
        # favor of a raw pydantic traceback.
        env_ignore_empty=True,
    )

    storage_path: Path = Path("/data/artifacts")
    temp_path: Path | None = None
    database_path: Path = Path("/data/magpie.db")
    retention_days: int = 90
    debug: bool = False

    # Upload limits - enforced in two places for defense-in-depth:
    # 1. Content-Length header check: Includes multipart overhead (~200 bytes), provides early rejection
    # 2. StreamingMultipartHandler: Counts all part body content bytes (file field + other form fields)
    #    during streaming to prevent DoS. Catches malicious clients that omit/lie about Content-Length.
    #    Note: Does NOT count multipart headers or boundaries (parser strips these before callbacks).
    # Some valid uploads near the limit may be rejected early due to multipart overhead in Content-Length.
    max_upload_size: int | None = None  # MAGPIE_MAX_UPLOAD_SIZE (bytes, None = unlimited)

    # S3 backup settings (optional)
    s3_bucket: str | None = None  # MAGPIE_S3_BUCKET
    s3_prefix: str = ""  # MAGPIE_S3_PREFIX - optional prefix for S3 keys

    # Logging settings
    log_format: Literal["json", "console"] = "json"  # MAGPIE_LOG_FORMAT

    # Observability settings
    sentry_dsn: str | None = None  # MAGPIE_SENTRY_DSN (server-side only)
    sentry_traces_sample_rate: float = Field(
        default=1.0, ge=0.0, le=1.0
    )  # MAGPIE_SENTRY_TRACES_SAMPLE_RATE
    otel_enabled: bool = False  # MAGPIE_OTEL_ENABLED
    otel_endpoint: str | None = None  # MAGPIE_OTEL_ENDPOINT
    otel_service_name: str = "magpie"  # MAGPIE_OTEL_SERVICE_NAME

    # IP allow-listing (used by Caddy via environment variable passthrough)
    # Comma-separated list of CIDR ranges allowed to bypass bearer token authentication
    # Example: "10.0.0.0/8,192.168.1.0/24"
    # NOTE: This setting is consumed by Caddy, not the Python application
    allowed_cidrs: str = ""  # MAGPIE_ALLOWED_CIDRS

    # Test endpoints (NEVER enable in production)
    enable_test_endpoints: bool = False  # MAGPIE_ENABLE_TEST_ENDPOINTS

    # Admin bootstrap token delivery (see docs/installation.md "Admin Token Delivery")
    # No default: an explicit sink choice is required (fail-closed). See
    # magpie.auth.token_sink for the sinks themselves and their failure semantics.
    admin_token_sink: Literal["file", "exec", "discard", "stdout"] | None = (
        None  # MAGPIE_ADMIN_TOKEN_SINK
    )
    # Path for sink=file. Defaults to a sibling of database_path (typically /data)
    # if unset; see derive_paths().
    admin_token_sink_file_path: Path | None = None  # MAGPIE_ADMIN_TOKEN_SINK_FILE_PATH
    # Shell-style command line for sink=exec (parsed with shlex, never run through a
    # shell). The token is piped to the command's stdin only -- never argv or env.
    admin_token_sink_exec_command: str | None = None  # MAGPIE_ADMIN_TOKEN_SINK_EXEC_COMMAND
    # NOTE: explicit validation_alias because "timeout" alone (the field name,
    # which env_prefix + field-name derivation would produce as
    # MAGPIE_ADMIN_TOKEN_SINK_EXEC_TIMEOUT) is ambiguous about units; the
    # documented/compose-file variable name carries the unit instead.
    admin_token_sink_exec_timeout: float = Field(
        default=30.0, gt=0.0, validation_alias="MAGPIE_ADMIN_TOKEN_SINK_EXEC_TIMEOUT_SECONDS"
    )  # MAGPIE_ADMIN_TOKEN_SINK_EXEC_TIMEOUT_SECONDS

    @field_validator("allowed_cidrs")
    @classmethod
    def validate_cidrs(cls, v: str) -> str:
        """Validate CIDR notation for IP allow-listing.

        Args:
            v: Comma-separated CIDR ranges (e.g., "10.0.0.0/8,192.168.1.0/24")

        Returns:
            The validated CIDR string

        Raises:
            ValueError: If any CIDR range has invalid notation
        """
        if not v:
            return v

        for cidr in v.split(","):
            cidr = cidr.strip()
            if not cidr:
                continue
            try:
                ipaddress.ip_network(cidr, strict=False)
            except ValueError as e:
                raise ValueError(f"Invalid CIDR notation '{cidr}': {e}") from e

        return v

    @model_validator(mode="after")
    def derive_paths(self) -> Self:
        """Derive temp_path and admin_token_sink_file_path if not set."""
        if self.temp_path is None:
            self.temp_path = self.storage_path / ".tmp"
        if self.admin_token_sink_file_path is None:
            self.admin_token_sink_file_path = self.database_path.parent / "admin-token"
        return self


@functools.lru_cache(maxsize=1)
def get_settings() -> MagpieSettings:
    """Get cached MagpieSettings instance (singleton pattern).

    Returns:
        Cached MagpieSettings instance with values from environment.
    """
    return MagpieSettings()
