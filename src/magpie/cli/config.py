"""CLI configuration module for Magpie client.

Handles loading configuration from ~/.magpie/config.toml with environment
variable overrides. Precedence: env vars > config file > defaults.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[import-not-found,no-redef]


DEFAULT_CONFIG_PATH = Path.home() / ".magpie" / "config.toml"


@dataclass
class ClientConfig:
    """Client configuration for connecting to Magpie server."""

    server: str = ""
    token: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ClientConfig:
        """Create ClientConfig from dictionary (typically from TOML)."""
        client_data = data.get("client", {})
        return cls(
            server=client_data.get("server", ""),
            token=client_data.get("token", ""),
        )


@dataclass
class CLIConfig:
    """Complete CLI configuration with client settings."""

    client: ClientConfig = field(default_factory=ClientConfig)


def load_config(config_path: Path | None = None) -> CLIConfig:
    """Load configuration from TOML file.

    Args:
        config_path: Path to config file. Defaults to ~/.magpie/config.toml.

    Returns:
        CLIConfig with values from file or defaults if file doesn't exist.
    """
    path = config_path or DEFAULT_CONFIG_PATH

    if not path.exists():
        return CLIConfig()

    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
        return CLIConfig(client=ClientConfig.from_dict(data))
    except (OSError, tomllib.TOMLDecodeError):
        return CLIConfig()


def get_server(
    cli_override: str | None = None,
    config_path: Path | None = None,
) -> str:
    """Get server URL with proper precedence.

    Precedence: CLI override > MAGPIE_SERVER env var > config file > default.

    Args:
        cli_override: Value from --server CLI option.
        config_path: Optional config file path for testing.

    Returns:
        Server URL string (may be empty if not configured).
    """
    if cli_override:
        return cli_override

    env_server = os.environ.get("MAGPIE_SERVER")
    if env_server:
        return env_server

    config = load_config(config_path)
    return config.client.server


def get_token(
    cli_override: str | None = None,
    config_path: Path | None = None,
) -> str:
    """Get auth token with proper precedence.

    Precedence: CLI override > MAGPIE_TOKEN env var > config file > default.

    Args:
        cli_override: Value from --token CLI option.
        config_path: Optional config file path for testing.

    Returns:
        Token string (may be empty if not configured).
    """
    if cli_override:
        return cli_override

    env_token = os.environ.get("MAGPIE_TOKEN")
    if env_token:
        return env_token

    config = load_config(config_path)
    return config.client.token


# Default timeout in seconds (10 minutes) for large file uploads
DEFAULT_TIMEOUT = 600.0

# Pattern for parsing duration strings like "30s", "5m", "1h", "1h30m"
# Matches one or more groups of: number followed by unit (s/m/h)
_DURATION_PATTERN = re.compile(r"^(\d+[smh])+$", re.IGNORECASE)
_DURATION_COMPONENT = re.compile(r"(\d+)([smh])", re.IGNORECASE)


class DurationParseError(ValueError):
    """Raised when a duration string cannot be parsed."""


def parse_duration(value: str) -> int:
    """Parse a duration string to seconds.

    Supports human-readable duration formats:
    - "30s" -> 30 seconds
    - "5m" -> 300 seconds
    - "1h" -> 3600 seconds
    - "1h30m" -> 5400 seconds (compound durations)
    - "3600" -> 3600 seconds (plain number = seconds)

    Args:
        value: Duration string or plain number in seconds.

    Returns:
        Duration in seconds as an integer.

    Raises:
        DurationParseError: If the value cannot be parsed.
    """
    value = value.strip()
    if not value:
        raise DurationParseError("Empty duration string")

    # Try plain number first (seconds)
    try:
        seconds = int(float(value))
    except ValueError:
        pass
    else:
        if seconds < 0:
            raise DurationParseError("Negative durations are not allowed")
        return seconds

    # Try duration pattern (e.g., "30s", "5m", "1h", "1h30m")
    if not _DURATION_PATTERN.match(value):
        raise DurationParseError(
            f"Invalid duration format: {value!r}. "
            "Expected formats: 30s, 5m, 1h, 1h30m, or plain seconds."
        )

    total_seconds = 0
    for match in _DURATION_COMPONENT.finditer(value):
        amount = int(match.group(1))
        unit = match.group(2).lower()
        if unit == "s":
            total_seconds += amount
        elif unit == "m":
            total_seconds += amount * 60
        elif unit == "h":
            total_seconds += amount * 3600

    return total_seconds


def get_timeout(cli_override: float | None = None) -> float:
    """Get HTTP client timeout with proper precedence.

    Precedence: CLI override > MAGPIE_TIMEOUT env var > default (600 seconds).

    Unlike other CLI settings (server, token), timeout is intentionally not read
    from the config file. This ensures long-lived global configuration cannot
    silently affect network behavior; only explicit CLI flags or the
    MAGPIE_TIMEOUT environment variable can override the default.

    The MAGPIE_TIMEOUT environment variable supports human-readable duration
    formats: "30s", "5m", "1h", "1h30m", or plain seconds like "3600".

    Args:
        cli_override: Value from --timeout CLI option.

    Returns:
        Timeout in seconds as a float.
    """
    if cli_override is not None:
        return cli_override

    env_timeout = os.environ.get("MAGPIE_TIMEOUT")
    if env_timeout:
        try:
            return float(parse_duration(env_timeout))
        except DurationParseError:
            # Invalid env var value, fall back to default
            pass

    return DEFAULT_TIMEOUT


def get_ca_cert(cli_override: str | None = None) -> str | None:
    """Get CA certificate path with proper precedence.

    Precedence: CLI override > MAGPIE_CA_CERT env var > None.

    Args:
        cli_override: Value from --ca-cert CLI option.

    Returns:
        Path to CA certificate file or None if not configured.
    """
    if cli_override:
        return cli_override

    env_ca_cert = os.environ.get("MAGPIE_CA_CERT")
    if env_ca_cert:
        return env_ca_cert

    return None
