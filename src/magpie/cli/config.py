"""CLI configuration module for Magpie client.

Handles loading configuration from ~/.magpie/config.toml with environment
variable overrides. Precedence: env vars > config file > defaults.
"""

from __future__ import annotations

import os
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


def get_timeout(cli_override: float | None = None) -> float:
    """Get HTTP client timeout with proper precedence.

    Precedence: CLI override > MAGPIE_TIMEOUT env var > default (600 seconds).

    Unlike other CLI settings (server, token), timeout is intentionally not read
    from the config file. This ensures long-lived global configuration cannot
    silently affect network behavior; only explicit CLI flags or the
    MAGPIE_TIMEOUT environment variable can override the default.

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
            return float(env_timeout)
        except ValueError:
            # Invalid env var value, fall back to default
            pass

    return DEFAULT_TIMEOUT
