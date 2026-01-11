"""Tests for CLI config command."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from magpie.cli import cli


@pytest.fixture
def runner() -> CliRunner:
    """Create a CLI test runner."""
    return CliRunner()


@pytest.fixture
def config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a temporary config directory and patch the default path."""
    config_path = tmp_path / ".magpie" / "config.toml"
    monkeypatch.setattr(
        "magpie.cli.config.DEFAULT_CONFIG_PATH",
        config_path,
    )
    return config_path


class TestConfigShow:
    """Tests for 'magpie config --show' command."""

    def test_show_no_config_file(self, runner: CliRunner, config_dir: Path) -> None:
        """Test --show with no config file."""
        result = runner.invoke(cli, ["config", "--show"])

        assert result.exit_code == 0
        assert "No configuration file found" in result.output
        assert "magpie config --server" in result.output

    def test_show_with_config(self, runner: CliRunner, config_dir: Path) -> None:
        """Test --show with existing config."""
        config_dir.parent.mkdir(parents=True, exist_ok=True)
        config_dir.write_text(
            '[client]\nserver = "https://example.com"\ntoken = "mgp_testtoken123"\n'
        )

        result = runner.invoke(cli, ["config", "--show"])

        assert result.exit_code == 0
        assert "https://example.com" in result.output
        # Token should be masked
        assert "mgp_testtoken123" not in result.output
        assert "n123" in result.output  # Last 4 chars visible
        assert "********" in result.output or "****" in result.output

    def test_show_partial_config(self, runner: CliRunner, config_dir: Path) -> None:
        """Test --show with only server configured."""
        config_dir.parent.mkdir(parents=True, exist_ok=True)
        config_dir.write_text('[client]\nserver = "https://partial.example.com"\n')

        result = runner.invoke(cli, ["config", "--show"])

        assert result.exit_code == 0
        assert "https://partial.example.com" in result.output
        assert "(not set)" in result.output

    def test_default_behavior_shows_config(self, runner: CliRunner, config_dir: Path) -> None:
        """Test that running 'config' without options shows config."""
        result = runner.invoke(cli, ["config"])

        assert result.exit_code == 0
        assert "No configuration file found" in result.output


class TestConfigSet:
    """Tests for setting config values."""

    def test_set_server(self, runner: CliRunner, config_dir: Path) -> None:
        """Test setting server URL."""
        result = runner.invoke(cli, ["config", "--server", "https://myserver.com"])

        assert result.exit_code == 0
        assert "Server set to: https://myserver.com" in result.output
        assert config_dir.exists()

        content = config_dir.read_text()
        assert 'server = "https://myserver.com"' in content

    def test_set_token(self, runner: CliRunner, config_dir: Path) -> None:
        """Test setting token."""
        result = runner.invoke(cli, ["config", "--token", "mgp_secrettoken"])

        assert result.exit_code == 0
        assert "Token set to:" in result.output
        # Token should be masked in output
        assert "mgp_secrettoken" not in result.output
        assert config_dir.exists()

        content = config_dir.read_text()
        assert 'token = "mgp_secrettoken"' in content

    def test_set_both(self, runner: CliRunner, config_dir: Path) -> None:
        """Test setting both server and token."""
        result = runner.invoke(
            cli, ["config", "--server", "https://both.example.com", "--token", "mgp_both"]
        )

        assert result.exit_code == 0
        assert "Server set to: https://both.example.com" in result.output
        assert "Token set to:" in result.output
        assert config_dir.exists()

        content = config_dir.read_text()
        assert 'server = "https://both.example.com"' in content
        assert 'token = "mgp_both"' in content

    def test_update_preserves_existing(self, runner: CliRunner, config_dir: Path) -> None:
        """Test that updating one value preserves other values."""
        config_dir.parent.mkdir(parents=True, exist_ok=True)
        config_dir.write_text('[client]\nserver = "https://old.com"\ntoken = "mgp_oldtoken"\n')

        result = runner.invoke(cli, ["config", "--server", "https://new.com"])

        assert result.exit_code == 0
        content = config_dir.read_text()
        assert 'server = "https://new.com"' in content
        assert 'token = "mgp_oldtoken"' in content

    def test_creates_parent_directory(self, runner: CliRunner, config_dir: Path) -> None:
        """Test that config command creates parent directory if needed."""
        assert not config_dir.parent.exists()

        result = runner.invoke(cli, ["config", "--server", "https://new.com"])

        assert result.exit_code == 0
        assert config_dir.parent.exists()
        assert config_dir.exists()


class TestConfigClear:
    """Tests for 'magpie config --clear' command."""

    def test_clear_existing_config(self, runner: CliRunner, config_dir: Path) -> None:
        """Test clearing existing config."""
        config_dir.parent.mkdir(parents=True, exist_ok=True)
        config_dir.write_text('[client]\nserver = "https://example.com"\n')

        result = runner.invoke(cli, ["config", "--clear"])

        assert result.exit_code == 0
        assert "Configuration cleared" in result.output
        assert not config_dir.exists()

    def test_clear_no_config(self, runner: CliRunner, config_dir: Path) -> None:
        """Test clearing when no config exists."""
        result = runner.invoke(cli, ["config", "--clear"])

        assert result.exit_code == 0
        assert "No configuration file found" in result.output


class TestConfigValidation:
    """Tests for config command option validation."""

    def test_clear_with_server_fails(self, runner: CliRunner, config_dir: Path) -> None:
        """Test that --clear with --server fails."""
        result = runner.invoke(cli, ["config", "--clear", "--server", "https://example.com"])

        assert result.exit_code != 0
        assert "Cannot use --clear with --server or --token" in result.output

    def test_clear_with_token_fails(self, runner: CliRunner, config_dir: Path) -> None:
        """Test that --clear with --token fails."""
        result = runner.invoke(cli, ["config", "--clear", "--token", "mgp_test"])

        assert result.exit_code != 0
        assert "Cannot use --clear with --server or --token" in result.output

    def test_show_with_server_fails(self, runner: CliRunner, config_dir: Path) -> None:
        """Test that --show with --server fails."""
        result = runner.invoke(cli, ["config", "--show", "--server", "https://example.com"])

        assert result.exit_code != 0
        assert "Cannot use --show with other options" in result.output

    def test_show_with_clear_fails(self, runner: CliRunner, config_dir: Path) -> None:
        """Test that --show with --clear fails."""
        result = runner.invoke(cli, ["config", "--show", "--clear"])

        assert result.exit_code != 0
        assert "Cannot use --show with other options" in result.output


class TestConfigTomlOutput:
    """Tests for TOML output format."""

    def test_toml_format_valid(self, runner: CliRunner, config_dir: Path) -> None:
        """Test that output is valid TOML."""
        runner.invoke(cli, ["config", "--server", "https://test.com", "--token", "mgp_test123"])

        # Try to parse the output with tomllib
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib  # type: ignore[import-not-found,no-redef]

        content = config_dir.read_bytes()
        data = tomllib.loads(content.decode())

        assert data["client"]["server"] == "https://test.com"
        assert data["client"]["token"] == "mgp_test123"

    def test_special_chars_escaped(self, runner: CliRunner, config_dir: Path) -> None:
        """Test that special characters are properly escaped."""
        # Test with a token containing quotes
        runner.invoke(cli, ["config", "--token", 'mgp_test"quote'])

        try:
            import tomllib
        except ImportError:
            import tomli as tomllib  # type: ignore[import-not-found,no-redef]

        content = config_dir.read_bytes()
        data = tomllib.loads(content.decode())

        assert data["client"]["token"] == 'mgp_test"quote'

    def test_backslash_escaped(self, runner: CliRunner, config_dir: Path) -> None:
        """Test that backslashes are properly escaped."""
        runner.invoke(cli, ["config", "--server", "https://test.com\\path"])

        try:
            import tomllib
        except ImportError:
            import tomli as tomllib  # type: ignore[import-not-found,no-redef]

        content = config_dir.read_bytes()
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
