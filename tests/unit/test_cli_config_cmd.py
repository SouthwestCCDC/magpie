"""Tests for CLI config command."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from magpie.cli import cli


@pytest.fixture
def runner() -> CliRunner:
    """Create a CLI test runner."""
    return CliRunner()


@pytest.fixture
def config_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a temporary config path and patch the default path."""
    path = tmp_path / ".magpie" / "config.toml"
    monkeypatch.setattr(
        "magpie.cli.config.DEFAULT_CONFIG_PATH",
        path,
    )
    return path


class TestConfigShow:
    """Tests for 'magpie config --show' command."""

    def test_show_no_config_file(self, runner: CliRunner, config_path: Path) -> None:
        """Test --show with no config file."""
        result = runner.invoke(cli, ["config", "--show"])

        assert result.exit_code == 0
        assert "No configuration file found" in result.output
        assert "magpie config --server" in result.output

    def test_show_with_config(self, runner: CliRunner, config_path: Path) -> None:
        """Test --show with existing config."""
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            '[client]\nserver = "https://example.com"\ntoken = "mgp_testtoken123"\n'
        )

        result = runner.invoke(cli, ["config", "--show"])

        assert result.exit_code == 0
        assert "https://example.com" in result.output
        # Token should be masked
        assert "mgp_testtoken123" not in result.output
        assert "n123" in result.output  # Last 4 chars visible
        assert "********" in result.output or "****" in result.output

    def test_show_partial_config(self, runner: CliRunner, config_path: Path) -> None:
        """Test --show with only server configured."""
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text('[client]\nserver = "https://partial.example.com"\n')

        result = runner.invoke(cli, ["config", "--show"])

        assert result.exit_code == 0
        assert "https://partial.example.com" in result.output
        assert "(not set)" in result.output

    def test_default_behavior_shows_config(self, runner: CliRunner, config_path: Path) -> None:
        """Test that running 'config' without options shows config."""
        result = runner.invoke(cli, ["config"])

        assert result.exit_code == 0
        assert "No configuration file found" in result.output

    def test_show_prints_effective_configuration_header(
        self, runner: CliRunner, config_path: Path
    ) -> None:
        """Test that --show introduces the resolved settings with a header."""
        result = runner.invoke(cli, ["config", "--show"])

        assert result.exit_code == 0
        assert "Effective configuration:" in result.output


class TestConfigShowSourceAttribution:
    """Tests for source attribution in 'magpie config --show'."""

    def test_default_source_when_nothing_configured(
        self, runner: CliRunner, config_path: Path
    ) -> None:
        """Test that unset values are attributed to 'default'."""
        result = runner.invoke(cli, ["config", "--show"])

        assert result.exit_code == 0
        assert "server" in result.output
        assert "(default)" in result.output
        assert "timeout = 600" in result.output

    def test_file_source_when_only_config_file_set(
        self, runner: CliRunner, config_path: Path
    ) -> None:
        """Test that config-file values are attributed to 'file: <path>'."""
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text('[client]\nserver = "https://example.com"\ntoken = "mgp_abc123"\n')

        result = runner.invoke(cli, ["config", "--show"])

        assert result.exit_code == 0
        assert f"(file: {config_path})" in result.output

    def test_env_source_overrides_file(self, runner: CliRunner, config_path: Path) -> None:
        """Test that MAGPIE_SERVER/MAGPIE_TOKEN env vars are attributed to 'env' and
        take precedence over the config file's values in both value and source.
        """
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            '[client]\nserver = "https://file.example.com"\ntoken = "mgp_filetoken"\n'
        )

        result = runner.invoke(
            cli,
            ["config", "--show"],
            env={"MAGPIE_SERVER": "https://env.example.com", "MAGPIE_TOKEN": "mgp_envtoken1234"},
        )

        assert result.exit_code == 0
        assert "https://env.example.com" in result.output
        assert "https://file.example.com" not in result.output
        assert "(env: MAGPIE_SERVER)" in result.output
        assert "(env: MAGPIE_TOKEN)" in result.output
        # The env-sourced token should still be masked.
        assert "mgp_envtoken1234" not in result.output

    def test_empty_env_var_falls_through_to_file(
        self, runner: CliRunner, config_path: Path
    ) -> None:
        """Test that an empty MAGPIE_SERVER is treated as unset, not as 'env'.

        get_server() uses a truthy check (`if cli_override:`), so `MAGPIE_SERVER=`
        is ignored and the config file value is what's actually used. Attribution
        must agree: reporting 'env' here would misrepresent the effective source.
        """
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text('[client]\nserver = "https://file.example.com"\n')

        result = runner.invoke(
            cli,
            ["config", "--show"],
            env={"MAGPIE_SERVER": ""},
        )

        assert result.exit_code == 0
        assert "https://file.example.com" in result.output
        assert f"(file: {config_path})" in result.output
        assert "(env: MAGPIE_SERVER)" not in result.output

    def test_cli_source_overrides_env_and_file(self, runner: CliRunner, config_path: Path) -> None:
        """Test that a top-level --server flag is attributed to 'cli' and wins over
        both the env var and the config file.
        """
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text('[client]\nserver = "https://file.example.com"\n')

        result = runner.invoke(
            cli,
            ["--server", "https://cli.example.com", "config", "--show"],
            env={"MAGPIE_SERVER": "https://env.example.com"},
        )

        assert result.exit_code == 0
        assert "https://cli.example.com" in result.output
        assert "(cli: --server)" in result.output

    def test_timeout_env_source(self, runner: CliRunner, config_path: Path) -> None:
        """Test that MAGPIE_TIMEOUT is attributed to 'env', including duration strings."""
        result = runner.invoke(cli, ["config", "--show"], env={"MAGPIE_TIMEOUT": "5m"})

        assert result.exit_code == 0
        assert "timeout = 300" in result.output
        assert "(env: MAGPIE_TIMEOUT)" in result.output

    def test_timeout_cli_source(self, runner: CliRunner, config_path: Path) -> None:
        """Test that a top-level --timeout flag is attributed to 'cli'."""
        result = runner.invoke(cli, ["--timeout", "45", "config", "--show"])

        assert result.exit_code == 0
        assert "timeout = 45" in result.output
        assert "(cli: --timeout)" in result.output

    def test_json_output_includes_source_and_masks_token(
        self, runner: CliRunner, config_path: Path
    ) -> None:
        """Test that --format json exposes value/source/origin per field, with the
        token masked just like human output.
        """
        result = runner.invoke(
            cli,
            ["--format", "json", "config", "--show"],
            env={"MAGPIE_TOKEN": "mgp_supersecret1234"},
        )

        assert result.exit_code == 0
        payload = json.loads(result.output)
        data = payload["data"]

        assert data["server"]["source"] == "default"
        assert data["server"]["value"] is None

        assert data["token"]["source"] == "env"
        assert data["token"]["origin"] == "MAGPIE_TOKEN"
        assert data["token"]["value"] != "mgp_supersecret1234"
        assert "mgp_supersecret1234" not in result.output
        assert data["token"]["value"].endswith("1234")

        assert data["timeout"]["source"] == "default"
        assert data["timeout"]["value"] == 600.0


class TestConfigSet:
    """Tests for setting config values."""

    def test_set_server(self, runner: CliRunner, config_path: Path) -> None:
        """Test setting server URL."""
        result = runner.invoke(cli, ["config", "--server", "https://myserver.com"])

        assert result.exit_code == 0
        assert "Server set to: https://myserver.com" in result.output
        assert config_path.exists()

        content = config_path.read_text()
        assert 'server = "https://myserver.com"' in content

    def test_set_token(self, runner: CliRunner, config_path: Path) -> None:
        """Test setting token."""
        result = runner.invoke(cli, ["config", "--token", "mgp_secrettoken"])

        assert result.exit_code == 0
        assert "Token set to:" in result.output
        # Token should be masked in output
        assert "mgp_secrettoken" not in result.output
        assert config_path.exists()

        content = config_path.read_text()
        assert 'token = "mgp_secrettoken"' in content

    def test_set_both(self, runner: CliRunner, config_path: Path) -> None:
        """Test setting both server and token."""
        result = runner.invoke(
            cli, ["config", "--server", "https://both.example.com", "--token", "mgp_both"]
        )

        assert result.exit_code == 0
        assert "Server set to: https://both.example.com" in result.output
        assert "Token set to:" in result.output
        assert config_path.exists()

        content = config_path.read_text()
        assert 'server = "https://both.example.com"' in content
        assert 'token = "mgp_both"' in content

    def test_update_preserves_existing(self, runner: CliRunner, config_path: Path) -> None:
        """Test that updating one value preserves other values."""
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text('[client]\nserver = "https://old.com"\ntoken = "mgp_oldtoken"\n')

        result = runner.invoke(cli, ["config", "--server", "https://new.com"])

        assert result.exit_code == 0
        content = config_path.read_text()
        assert 'server = "https://new.com"' in content
        assert 'token = "mgp_oldtoken"' in content

    def test_creates_parent_directory(self, runner: CliRunner, config_path: Path) -> None:
        """Test that config command creates parent directory if needed."""
        assert not config_path.parent.exists()

        result = runner.invoke(cli, ["config", "--server", "https://new.com"])

        assert result.exit_code == 0
        assert config_path.parent.exists()
        assert config_path.exists()


class TestConfigClear:
    """Tests for 'magpie config --clear' command."""

    def test_clear_existing_config(self, runner: CliRunner, config_path: Path) -> None:
        """Test clearing existing config."""
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text('[client]\nserver = "https://example.com"\n')

        result = runner.invoke(cli, ["config", "--clear"])

        assert result.exit_code == 0
        assert "Configuration cleared" in result.output
        assert not config_path.exists()

    def test_clear_no_config(self, runner: CliRunner, config_path: Path) -> None:
        """Test clearing when no config exists."""
        result = runner.invoke(cli, ["config", "--clear"])

        assert result.exit_code == 0
        assert "No configuration file found" in result.output


class TestConfigValidation:
    """Tests for config command option validation."""

    def test_clear_with_server_fails(self, runner: CliRunner, config_path: Path) -> None:
        """Test that --clear with --server fails."""
        result = runner.invoke(cli, ["config", "--clear", "--server", "https://example.com"])

        assert result.exit_code != 0
        assert "Cannot use --clear with --server or --token" in result.output

    def test_clear_with_token_fails(self, runner: CliRunner, config_path: Path) -> None:
        """Test that --clear with --token fails."""
        result = runner.invoke(cli, ["config", "--clear", "--token", "mgp_test"])

        assert result.exit_code != 0
        assert "Cannot use --clear with --server or --token" in result.output

    def test_show_with_server_fails(self, runner: CliRunner, config_path: Path) -> None:
        """Test that --show with --server fails."""
        result = runner.invoke(cli, ["config", "--show", "--server", "https://example.com"])

        assert result.exit_code != 0
        assert "Cannot use --show with other options" in result.output

    def test_show_with_clear_fails(self, runner: CliRunner, config_path: Path) -> None:
        """Test that --show with --clear fails."""
        result = runner.invoke(cli, ["config", "--show", "--clear"])

        assert result.exit_code != 0
        assert "Cannot use --show with other options" in result.output


class TestConfigTomlOutput:
    """Tests for TOML output format."""

    def test_toml_format_valid(self, runner: CliRunner, config_path: Path) -> None:
        """Test that output is valid TOML."""
        runner.invoke(cli, ["config", "--server", "https://test.com", "--token", "mgp_test123"])

        # Try to parse the output with tomllib
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib  # type: ignore[import-not-found,no-redef]

        content = config_path.read_bytes()
        data = tomllib.loads(content.decode())

        assert data["client"]["server"] == "https://test.com"
        assert data["client"]["token"] == "mgp_test123"

    def test_special_chars_escaped(self, runner: CliRunner, config_path: Path) -> None:
        """Test that special characters are properly escaped."""
        # Test with a token containing quotes
        runner.invoke(cli, ["config", "--token", 'mgp_test"quote'])

        try:
            import tomllib
        except ImportError:
            import tomli as tomllib  # type: ignore[import-not-found,no-redef]

        content = config_path.read_bytes()
        data = tomllib.loads(content.decode())

        assert data["client"]["token"] == 'mgp_test"quote'

    def test_backslash_escaped(self, runner: CliRunner, config_path: Path) -> None:
        """Test that backslashes are properly escaped."""
        runner.invoke(cli, ["config", "--server", "https://test.com\\path"])

        try:
            import tomllib
        except ImportError:
            import tomli as tomllib  # type: ignore[import-not-found,no-redef]

        content = config_path.read_bytes()
        data = tomllib.loads(content.decode())

        assert data["client"]["server"] == "https://test.com\\path"


class TestConfigHelpText:
    """Tests for config command help text."""

    def test_help_shows_examples(self, runner: CliRunner) -> None:
        """Test that help text includes examples."""
        result = runner.invoke(cli, ["config", "--help"])

        assert result.exit_code == 0
        assert "--server" in result.output
        assert "--token" in result.output
        assert "--show" in result.output
        assert "--clear" in result.output
