"""Tests for CLI configuration module."""

from __future__ import annotations

from pathlib import Path

import pytest

from magpie.cli.config import (
    DEFAULT_TIMEOUT,
    ClientConfig,
    get_server,
    get_timeout,
    get_token,
    load_config,
)


class TestLoadConfig:
    """Tests for load_config function."""

    def test_load_config_from_file(self, tmp_path: Path) -> None:
        """Test loading config from TOML file."""
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            """
[client]
server = "https://artifacts.example.com"
token = "mgp_testtoken123"
"""
        )

        config = load_config(config_file)

        assert config.client.server == "https://artifacts.example.com"
        assert config.client.token == "mgp_testtoken123"

    def test_load_config_missing_file_returns_defaults(self, tmp_path: Path) -> None:
        """Test that missing config file returns default values."""
        config_file = tmp_path / "nonexistent.toml"

        config = load_config(config_file)

        assert config.client.server == ""
        assert config.client.token == ""

    def test_load_config_empty_file(self, tmp_path: Path) -> None:
        """Test loading from empty config file returns defaults."""
        config_file = tmp_path / "config.toml"
        config_file.write_text("")

        config = load_config(config_file)

        assert config.client.server == ""
        assert config.client.token == ""

    def test_load_config_partial_client_section(self, tmp_path: Path) -> None:
        """Test loading config with partial client section."""
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            """
[client]
server = "https://partial.example.com"
"""
        )

        config = load_config(config_file)

        assert config.client.server == "https://partial.example.com"
        assert config.client.token == ""

    def test_load_config_invalid_toml(self, tmp_path: Path) -> None:
        """Test that invalid TOML returns defaults."""
        config_file = tmp_path / "config.toml"
        config_file.write_text("this is not valid toml [[[")

        config = load_config(config_file)

        assert config.client.server == ""
        assert config.client.token == ""


class TestGetServer:
    """Tests for get_server function."""

    def test_cli_override_takes_precedence(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that CLI override takes precedence over env and config."""
        config_file = tmp_path / "config.toml"
        config_file.write_text('[client]\nserver = "https://config.example.com"')
        monkeypatch.setenv("MAGPIE_SERVER", "https://env.example.com")

        result = get_server(cli_override="https://cli.example.com", config_path=config_file)

        assert result == "https://cli.example.com"

    def test_env_var_takes_precedence_over_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that env var takes precedence over config file."""
        config_file = tmp_path / "config.toml"
        config_file.write_text('[client]\nserver = "https://config.example.com"')
        monkeypatch.setenv("MAGPIE_SERVER", "https://env.example.com")

        result = get_server(cli_override=None, config_path=config_file)

        assert result == "https://env.example.com"

    def test_config_file_used_when_no_override(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that config file is used when no CLI or env override."""
        config_file = tmp_path / "config.toml"
        config_file.write_text('[client]\nserver = "https://config.example.com"')
        monkeypatch.delenv("MAGPIE_SERVER", raising=False)

        result = get_server(cli_override=None, config_path=config_file)

        assert result == "https://config.example.com"

    def test_returns_empty_when_nothing_configured(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that empty string returned when nothing is configured."""
        config_file = tmp_path / "nonexistent.toml"
        monkeypatch.delenv("MAGPIE_SERVER", raising=False)

        result = get_server(cli_override=None, config_path=config_file)

        assert result == ""


class TestGetToken:
    """Tests for get_token function."""

    def test_cli_override_takes_precedence(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that CLI override takes precedence over env and config."""
        config_file = tmp_path / "config.toml"
        config_file.write_text('[client]\ntoken = "mgp_config"')
        monkeypatch.setenv("MAGPIE_TOKEN", "mgp_env")

        result = get_token(cli_override="mgp_cli", config_path=config_file)

        assert result == "mgp_cli"

    def test_env_var_takes_precedence_over_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that env var takes precedence over config file."""
        config_file = tmp_path / "config.toml"
        config_file.write_text('[client]\ntoken = "mgp_config"')
        monkeypatch.setenv("MAGPIE_TOKEN", "mgp_env")

        result = get_token(cli_override=None, config_path=config_file)

        assert result == "mgp_env"

    def test_config_file_used_when_no_override(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that config file is used when no CLI or env override."""
        config_file = tmp_path / "config.toml"
        config_file.write_text('[client]\ntoken = "mgp_config"')
        monkeypatch.delenv("MAGPIE_TOKEN", raising=False)

        result = get_token(cli_override=None, config_path=config_file)

        assert result == "mgp_config"

    def test_returns_empty_when_nothing_configured(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that empty string returned when nothing is configured."""
        config_file = tmp_path / "nonexistent.toml"
        monkeypatch.delenv("MAGPIE_TOKEN", raising=False)

        result = get_token(cli_override=None, config_path=config_file)

        assert result == ""


class TestClientConfig:
    """Tests for ClientConfig dataclass."""

    def test_from_dict_with_client_section(self) -> None:
        """Test creating ClientConfig from dict with client section."""
        data = {"client": {"server": "https://test.com", "token": "abc123"}}

        config = ClientConfig.from_dict(data)

        assert config.server == "https://test.com"
        assert config.token == "abc123"

    def test_from_dict_missing_client_section(self) -> None:
        """Test creating ClientConfig from dict without client section."""
        data = {"other": {"key": "value"}}

        config = ClientConfig.from_dict(data)

        assert config.server == ""
        assert config.token == ""

    def test_from_dict_empty(self) -> None:
        """Test creating ClientConfig from empty dict."""
        config = ClientConfig.from_dict({})

        assert config.server == ""
        assert config.token == ""


class TestGetTimeout:
    """Tests for get_timeout function."""

    def test_cli_override_takes_precedence(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that CLI override takes precedence over env var."""
        monkeypatch.setenv("MAGPIE_TIMEOUT", "120")

        result = get_timeout(cli_override=300.0)

        assert result == 300.0

    def test_env_var_used_when_no_cli_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that env var is used when no CLI override."""
        monkeypatch.setenv("MAGPIE_TIMEOUT", "180")

        result = get_timeout(cli_override=None)

        assert result == 180.0

    def test_default_used_when_nothing_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that default value is used when nothing configured."""
        monkeypatch.delenv("MAGPIE_TIMEOUT", raising=False)

        result = get_timeout(cli_override=None)

        assert result == DEFAULT_TIMEOUT
        assert result == 600.0

    def test_invalid_env_var_falls_back_to_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that invalid env var value falls back to default."""
        monkeypatch.setenv("MAGPIE_TIMEOUT", "not_a_number")

        result = get_timeout(cli_override=None)

        assert result == DEFAULT_TIMEOUT

    def test_cli_override_zero_is_valid(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that CLI override of 0 is valid (disables timeout)."""
        monkeypatch.setenv("MAGPIE_TIMEOUT", "120")

        result = get_timeout(cli_override=0.0)

        assert result == 0.0

    def test_env_var_float_values(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that env var accepts float values."""
        monkeypatch.setenv("MAGPIE_TIMEOUT", "45.5")

        result = get_timeout(cli_override=None)

        assert result == 45.5
