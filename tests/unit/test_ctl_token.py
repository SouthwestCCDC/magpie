"""Unit tests for CTL token commands."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from magpie.config import MagpieSettings
from magpie.ctl import cli


@pytest.fixture
def cli_runner() -> CliRunner:
    """Create Click CLI test runner."""
    return CliRunner()


@pytest.fixture
def test_settings(tmp_path: Path) -> MagpieSettings:
    """Create test MagpieSettings with temporary paths."""
    settings = MagpieSettings(storage_path=tmp_path / "storage")
    # Ensure database directory exists
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    return settings


class TestTokenCreate:
    """Tests for token create command."""

    def test_token_create_returns_plaintext_with_prefix(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """Token create returns plaintext token with mgp_ prefix."""
        with patch("magpie.ctl.get_settings", return_value=test_settings):
            result = cli_runner.invoke(
                cli, ["token", "create", "--name", "test-token", "--scope", "read"]
            )

        assert result.exit_code == 0, f"Output: {result.output}"
        assert "mgp_" in result.output
        assert "test-token" in result.output
        assert "TOKEN CREATED" in result.output

    def test_token_create_admin_has_admin_prefix(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """Token create with admin scope has mgp_ADMIN_ prefix."""
        with patch("magpie.ctl.get_settings", return_value=test_settings):
            result = cli_runner.invoke(
                cli, ["token", "create", "--name", "admin-token", "--scope", "admin"]
            )

        assert result.exit_code == 0, f"Output: {result.output}"
        assert "mgp_ADMIN_" in result.output

    def test_token_create_validates_scope(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """Token create validates scope parameter."""
        with patch("magpie.ctl.get_settings", return_value=test_settings):
            result = cli_runner.invoke(
                cli, ["token", "create", "--name", "test", "--scope", "invalid"]
            )

        assert result.exit_code != 0
        assert "Invalid value" in result.output or "invalid" in result.output.lower()

    def test_token_create_requires_name(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """Token create requires --name option."""
        with patch("magpie.ctl.get_settings", return_value=test_settings):
            result = cli_runner.invoke(cli, ["token", "create", "--scope", "read"])

        assert result.exit_code != 0
        assert "Missing option" in result.output or "--name" in result.output

    def test_token_create_requires_scope(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """Token create requires --scope option."""
        with patch("magpie.ctl.get_settings", return_value=test_settings):
            result = cli_runner.invoke(cli, ["token", "create", "--name", "test"])

        assert result.exit_code != 0
        assert "Missing option" in result.output or "--scope" in result.output

    def test_token_create_name_uniqueness_enforced(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """Token create enforces name uniqueness."""
        with patch("magpie.ctl.get_settings", return_value=test_settings):
            # Create first token
            result1 = cli_runner.invoke(
                cli, ["token", "create", "--name", "duplicate", "--scope", "read"]
            )
            assert result1.exit_code == 0

            # Try to create duplicate
            result2 = cli_runner.invoke(
                cli, ["token", "create", "--name", "duplicate", "--scope", "read"]
            )
            assert result2.exit_code != 0
            assert "already exists" in result2.output

    def test_token_create_all_scopes_work(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """Token create works with all valid scopes."""
        with patch("magpie.ctl.get_settings", return_value=test_settings):
            for scope in ["read", "write", "admin"]:
                result = cli_runner.invoke(
                    cli,
                    ["token", "create", "--name", f"token-{scope}", "--scope", scope],
                )
                assert result.exit_code == 0, f"Failed for scope {scope}: {result.output}"
                assert "TOKEN CREATED" in result.output


class TestTokenList:
    """Tests for token list command."""

    def test_token_list_displays_tokens(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """Token list displays all tokens."""
        with patch("magpie.ctl.get_settings", return_value=test_settings):
            # Create some tokens first
            cli_runner.invoke(cli, ["token", "create", "--name", "reader", "--scope", "read"])
            cli_runner.invoke(cli, ["token", "create", "--name", "writer", "--scope", "write"])

            # List tokens
            result = cli_runner.invoke(cli, ["token", "list"])

        assert result.exit_code == 0, f"Output: {result.output}"
        assert "reader" in result.output
        assert "writer" in result.output
        assert "read" in result.output
        assert "write" in result.output

    def test_token_list_shows_enabled_status(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """Token list shows enabled status."""
        with patch("magpie.ctl.get_settings", return_value=test_settings):
            cli_runner.invoke(cli, ["token", "create", "--name", "active-token", "--scope", "read"])

            result = cli_runner.invoke(cli, ["token", "list"])

        assert result.exit_code == 0
        assert "yes" in result.output  # enabled=yes

    def test_token_list_never_shows_hashes(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """Token list never displays token hashes."""
        with patch("magpie.ctl.get_settings", return_value=test_settings):
            # Create a token
            create_result = cli_runner.invoke(
                cli, ["token", "create", "--name", "secret", "--scope", "read"]
            )
            # Extract the token from create output
            token_line = None
            for line in create_result.output.split("\n"):
                if line.startswith("mgp_"):
                    token_line = line.strip()
                    break

            # List tokens
            result = cli_runner.invoke(cli, ["token", "list"])

        assert result.exit_code == 0
        # Token value should not appear in list
        if token_line:
            assert token_line not in result.output
        # No 64-character hex strings (SHA-256 hashes)
        import re

        hash_pattern = re.compile(r"[a-f0-9]{64}", re.IGNORECASE)
        assert not hash_pattern.search(result.output)

    def test_token_list_empty_database(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """Token list handles empty database gracefully."""
        from magpie.auth.database import init_database

        # Initialize empty database
        init_database(test_settings.database_path)

        with patch("magpie.ctl.get_settings", return_value=test_settings):
            result = cli_runner.invoke(cli, ["token", "list"])

        assert result.exit_code == 0, f"Output: {result.output}"
        assert "No tokens found" in result.output

    def test_token_list_shows_table_headers(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """Token list shows proper table headers."""
        with patch("magpie.ctl.get_settings", return_value=test_settings):
            cli_runner.invoke(cli, ["token", "create", "--name", "test", "--scope", "read"])
            result = cli_runner.invoke(cli, ["token", "list"])

        assert result.exit_code == 0
        assert "NAME" in result.output
        assert "SCOPE" in result.output
        assert "ENABLED" in result.output
        assert "CREATED_AT" in result.output


class TestTokenRevoke:
    """Tests for token revoke command."""

    def test_token_revoke_removes_token(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """Token revoke removes the token."""
        with patch("magpie.ctl.get_settings", return_value=test_settings):
            # Create a token
            cli_runner.invoke(cli, ["token", "create", "--name", "to-revoke", "--scope", "read"])

            # Verify it exists
            list_result1 = cli_runner.invoke(cli, ["token", "list"])
            assert "to-revoke" in list_result1.output

            # Revoke it
            result = cli_runner.invoke(cli, ["token", "revoke", "to-revoke"])
            assert result.exit_code == 0
            assert "revoked" in result.output.lower()

            # Verify it's gone
            list_result2 = cli_runner.invoke(cli, ["token", "list"])
            assert "to-revoke" not in list_result2.output

    def test_token_revoke_handles_nonexistent(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """Token revoke handles nonexistent token gracefully."""
        with patch("magpie.ctl.get_settings", return_value=test_settings):
            result = cli_runner.invoke(cli, ["token", "revoke", "nonexistent"])

        assert result.exit_code != 0
        assert "not found" in result.output.lower()

    def test_token_revoke_requires_name_argument(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """Token revoke requires name argument."""
        with patch("magpie.ctl.get_settings", return_value=test_settings):
            result = cli_runner.invoke(cli, ["token", "revoke"])

        assert result.exit_code != 0
        assert "Missing argument" in result.output or "NAME" in result.output

    def test_token_revoke_success_message(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """Token revoke shows success message with token name."""
        with patch("magpie.ctl.get_settings", return_value=test_settings):
            cli_runner.invoke(cli, ["token", "create", "--name", "my-token", "--scope", "write"])

            result = cli_runner.invoke(cli, ["token", "revoke", "my-token"])

        assert result.exit_code == 0
        assert "my-token" in result.output
        assert "revoked" in result.output.lower()
