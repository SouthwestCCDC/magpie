"""Integration tests for magpie-ctl admin tool.

These tests verify that magpie-ctl commands work correctly with actual filesystem
and database operations (no mocking of storage or database access).
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Generator

import pytest
from click.testing import CliRunner

from magpie.auth.database import get_connection, init_database, list_tokens
from magpie.auth.models import TokenScope
from magpie.config import get_settings
from magpie.ctl import cli as ctl_cli


@pytest.fixture
def ctl_runner(tmp_path: Path) -> Generator[tuple[CliRunner, Path], None, None]:
    """Create CLI runner with isolated environment for magpie-ctl tests.

    Returns CliRunner configured to use tmp_path for storage.
    The runner will set MAGPIE_STORAGE_PATH and MAGPIE_DATABASE_PATH environment
    variables to point to the temporary directory.

    Clears the get_settings() cache before and after each test to ensure
    environment variables are properly read.
    """
    # Clear settings cache before test
    get_settings.cache_clear()

    runner = CliRunner(
        env={
            "MAGPIE_STORAGE_PATH": str(tmp_path),
            "MAGPIE_DATABASE_PATH": str(tmp_path / "magpie.db"),
        }
    )

    yield runner, tmp_path

    # Clear settings cache after test
    get_settings.cache_clear()


class TestInit:
    """Integration tests for magpie-ctl init command."""

    def test_init_creates_directory_structure(self, ctl_runner: tuple[CliRunner, Path]) -> None:
        """Init creates storage directory, temp directory, and database."""
        runner, tmp_path = ctl_runner
        result = runner.invoke(ctl_cli, ["init"])

        assert result.exit_code == 0, f"Output: {result.output}"
        assert "Storage initialized at:" in result.output
        assert "Database initialized at:" in result.output
        assert "ADMIN TOKEN" in result.output

        # Verify directory structure
        assert tmp_path.exists()
        assert (tmp_path / ".tmp").exists()
        assert (tmp_path / "magpie.db").exists()

    def test_init_generates_admin_token(self, ctl_runner: tuple[CliRunner, Path]) -> None:
        """Init generates admin token on first run."""
        runner, tmp_path = ctl_runner
        result = runner.invoke(ctl_cli, ["init"])

        assert result.exit_code == 0
        assert "ADMIN TOKEN (store securely, only shown once!):" in result.output

        # Verify admin token was created in database
        conn = get_connection(tmp_path / "magpie.db")
        try:
            tokens = list_tokens(conn)
            admin_tokens = [t for t in tokens if t.name == "admin"]
            assert len(admin_tokens) == 1
            assert admin_tokens[0].scope == TokenScope.ADMIN
        finally:
            conn.close()

    def test_init_already_initialized(self, ctl_runner: tuple[CliRunner, Path]) -> None:
        """Init handles already-initialized state correctly."""
        runner, tmp_path = ctl_runner

        # First init
        result1 = runner.invoke(ctl_cli, ["init"])
        assert result1.exit_code == 0

        # Second init should detect existing token
        result2 = runner.invoke(ctl_cli, ["init"])
        assert result2.exit_code == 0
        assert "Admin token already exists" in result2.output

    def test_init_reset_admin_token(self, ctl_runner: tuple[CliRunner, Path]) -> None:
        """Init --reset-admin-token regenerates admin token."""
        runner, tmp_path = ctl_runner

        # First init
        result1 = runner.invoke(ctl_cli, ["init"])
        assert result1.exit_code == 0
        # Extract token from output using regex
        match1 = re.search(r"mgp_ADMIN_\w+", result1.output)
        assert match1 is not None, "Failed to find admin token in output"
        token1 = match1.group(0)
        assert token1.startswith("mgp_ADMIN_")

        # Reset admin token
        result2 = runner.invoke(ctl_cli, ["init", "--reset-admin-token"])
        assert result2.exit_code == 0
        assert "Revoked existing admin token" in result2.output
        assert "NEW ADMIN TOKEN" in result2.output

        # Extract new token using regex
        match2 = re.search(r"mgp_ADMIN_\w+", result2.output)
        assert match2 is not None, "Failed to find new admin token in output"
        token2 = match2.group(0)
        assert token2.startswith("mgp_ADMIN_")
        assert token1 != token2

    def test_init_custom_admin_token(self, ctl_runner: tuple[CliRunner, Path]) -> None:
        """Init --admin-token uses provided token."""
        runner, tmp_path = ctl_runner
        custom_token = "mgp_ADMIN_custom_test_token"

        result = runner.invoke(ctl_cli, ["init", "--admin-token", custom_token])

        assert result.exit_code == 0
        assert custom_token in result.output

    def test_init_custom_admin_token_invalid_prefix(
        self, ctl_runner: tuple[CliRunner, Path]
    ) -> None:
        """Init --admin-token rejects tokens without admin prefix."""
        runner, tmp_path = ctl_runner
        invalid_token = "mgp_READ_invalid_scope"

        result = runner.invoke(ctl_cli, ["init", "--admin-token", invalid_token])

        assert result.exit_code != 0
        assert "must start with 'mgp_ADMIN_'" in result.output

    def test_init_custom_admin_token_too_short(self, ctl_runner: tuple[CliRunner, Path]) -> None:
        """Init --admin-token rejects tokens that are too short."""
        runner, tmp_path = ctl_runner
        short_token = "mgp_ADMIN_"

        result = runner.invoke(ctl_cli, ["init", "--admin-token", short_token])

        assert result.exit_code != 0
        assert "too short" in result.output

    def test_init_json_output(self, ctl_runner: tuple[CliRunner, Path]) -> None:
        """Init with --format json outputs structured JSON."""
        runner, tmp_path = ctl_runner
        result = runner.invoke(ctl_cli, ["--format", "json", "init"])

        assert result.exit_code == 0
        response = json.loads(result.output)
        assert response["status"] == "ok"
        data = response["data"]
        assert "admin_token" in data
        assert data["admin_token"].startswith("mgp_")
        assert "storage_path" in data
        assert "database_path" in data


class TestGC:
    """Integration tests for magpie-ctl gc command."""

    def test_gc_nonexistent_storage_path(self, ctl_runner: tuple[CliRunner, Path]) -> None:
        """GC with nonexistent storage path shows error."""
        runner, tmp_path = ctl_runner
        # Remove the storage path so it doesn't exist
        if tmp_path.exists():
            shutil.rmtree(tmp_path)

        result = runner.invoke(ctl_cli, ["gc"])

        assert result.exit_code != 0
        assert "Storage path does not exist" in result.output

    def test_gc_dry_run_with_no_artifacts(self, ctl_runner: tuple[CliRunner, Path]) -> None:
        """GC --dry-run with empty storage shows zero stats."""
        runner, tmp_path = ctl_runner
        # Initialize storage
        runner.invoke(ctl_cli, ["init"])

        result = runner.invoke(ctl_cli, ["gc", "--dry-run"])

        assert result.exit_code == 0
        assert "GC Summary:" in result.output
        assert "Artifacts scanned: 0" in result.output

    def test_gc_reconcile_only(self, ctl_runner: tuple[CliRunner, Path]) -> None:
        """GC --reconcile-only fixes symlinks without deleting blobs."""
        runner, tmp_path = ctl_runner
        # Initialize storage
        runner.invoke(ctl_cli, ["init"])

        result = runner.invoke(ctl_cli, ["gc", "--reconcile-only"])

        assert result.exit_code == 0
        assert "GC Summary:" in result.output
        # Should not show deletion stats in reconcile-only mode
        assert "Symlinks checked:" in result.output

    def test_gc_retention_days_override(self, ctl_runner: tuple[CliRunner, Path]) -> None:
        """GC --retention-days overrides config value."""
        runner, tmp_path = ctl_runner
        # Initialize storage
        runner.invoke(ctl_cli, ["init"])

        result = runner.invoke(ctl_cli, ["gc", "--dry-run", "--retention-days", "7"])

        assert result.exit_code == 0
        assert "GC Summary:" in result.output

    def test_gc_json_output(self, ctl_runner: tuple[CliRunner, Path]) -> None:
        """GC with --format json outputs structured JSON."""
        runner, tmp_path = ctl_runner
        # Initialize storage
        runner.invoke(ctl_cli, ["init"])

        result = runner.invoke(ctl_cli, ["--format", "json", "gc", "--dry-run"])

        assert result.exit_code == 0
        response = json.loads(result.output)
        assert response["status"] == "ok"
        data = response["data"]
        assert "artifacts_scanned" in data
        assert "blobs_found" in data
        assert "blobs_removed" in data
        assert "bytes_reclaimed" in data
        assert "symlinks_checked" in data

    def test_gc_json_output_flag(self, ctl_runner: tuple[CliRunner, Path]) -> None:
        """GC --json-output outputs JSON (for subprocess integration)."""
        runner, tmp_path = ctl_runner
        # Initialize storage
        runner.invoke(ctl_cli, ["init"])

        result = runner.invoke(ctl_cli, ["gc", "--json-output", "--dry-run"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "dry_run" in data
        assert "artifacts_scanned" in data
        assert "blobs_deleted" in data
        assert "space_reclaimed_bytes" in data
        assert "symlinks_checked" in data
        assert "symlinks_fixed" in data
        assert "items_removed" in data
        assert "errors" in data

    def test_gc_quiet_suppresses_progress(self, ctl_runner: tuple[CliRunner, Path]) -> None:
        """GC --quiet suppresses progress output."""
        runner, tmp_path = ctl_runner
        # Initialize storage
        runner.invoke(ctl_cli, ["init"])

        result = runner.invoke(ctl_cli, ["gc", "--quiet"])

        assert result.exit_code == 0
        # Should show summary but no progress bars
        assert "GC Summary:" in result.output


class TestToken:
    """Integration tests for magpie-ctl token commands."""

    def test_token_create(self, ctl_runner: tuple[CliRunner, Path]) -> None:
        """Token create generates new token."""
        runner, tmp_path = ctl_runner
        # Initialize storage
        runner.invoke(ctl_cli, ["init"])

        result = runner.invoke(
            ctl_cli, ["token", "create", "--name", "test-reader", "--scope", "read"]
        )

        assert result.exit_code == 0
        assert "TOKEN CREATED: test-reader" in result.output
        assert "mgp_" in result.output

        # Verify token was created in database
        conn = get_connection(tmp_path / "magpie.db")
        try:
            tokens = list_tokens(conn)
            test_tokens = [t for t in tokens if t.name == "test-reader"]
            assert len(test_tokens) == 1
            assert test_tokens[0].scope == TokenScope.READ
        finally:
            conn.close()

    def test_token_create_all_scopes(self, ctl_runner: tuple[CliRunner, Path]) -> None:
        """Token create works with all scope types."""
        runner, tmp_path = ctl_runner
        runner.invoke(ctl_cli, ["init"])

        # Create read token
        result = runner.invoke(ctl_cli, ["token", "create", "--name", "reader", "--scope", "read"])
        assert result.exit_code == 0
        assert "mgp_" in result.output

        # Create write token
        result = runner.invoke(ctl_cli, ["token", "create", "--name", "writer", "--scope", "write"])
        assert result.exit_code == 0
        assert "mgp_" in result.output

        # Create admin token
        result = runner.invoke(
            ctl_cli, ["token", "create", "--name", "ops-admin", "--scope", "admin"]
        )
        assert result.exit_code == 0
        assert "mgp_" in result.output

    def test_token_create_duplicate_name(self, ctl_runner: tuple[CliRunner, Path]) -> None:
        """Token create rejects duplicate names."""
        runner, tmp_path = ctl_runner
        runner.invoke(ctl_cli, ["init"])

        # Create first token
        result1 = runner.invoke(
            ctl_cli, ["token", "create", "--name", "duplicate", "--scope", "read"]
        )
        assert result1.exit_code == 0

        # Try to create duplicate
        result2 = runner.invoke(
            ctl_cli, ["token", "create", "--name", "duplicate", "--scope", "read"]
        )
        assert result2.exit_code != 0
        assert "already exists" in result2.output or "duplicate" in result2.output

    def test_token_list(self, ctl_runner: tuple[CliRunner, Path]) -> None:
        """Token list shows all tokens."""
        runner, tmp_path = ctl_runner
        runner.invoke(ctl_cli, ["init"])
        runner.invoke(ctl_cli, ["token", "create", "--name", "reader1", "--scope", "read"])
        runner.invoke(ctl_cli, ["token", "create", "--name", "writer1", "--scope", "write"])

        result = runner.invoke(ctl_cli, ["token", "list"])

        assert result.exit_code == 0
        assert "reader1" in result.output
        assert "writer1" in result.output
        assert "admin" in result.output  # From init
        # Check for table headers
        assert "NAME" in result.output
        assert "SCOPE" in result.output
        assert "ENABLED" in result.output
        assert "Total: 3 token(s)" in result.output

    def test_token_list_empty(self, ctl_runner: tuple[CliRunner, Path]) -> None:
        """Token list with no tokens shows appropriate message."""
        runner, tmp_path = ctl_runner
        # Initialize database but don't create admin token

        tmp_path.mkdir(parents=True, exist_ok=True)
        init_database(tmp_path / "magpie.db")

        result = runner.invoke(ctl_cli, ["token", "list"])

        assert result.exit_code == 0
        assert "No tokens found" in result.output

    def test_token_revoke(self, ctl_runner: tuple[CliRunner, Path]) -> None:
        """Token revoke removes token."""
        runner, tmp_path = ctl_runner
        runner.invoke(ctl_cli, ["init"])
        runner.invoke(ctl_cli, ["token", "create", "--name", "to-revoke", "--scope", "read"])

        result = runner.invoke(ctl_cli, ["token", "revoke", "to-revoke"])

        assert result.exit_code == 0
        assert "revoked" in result.output

        # Verify token was removed from database
        conn = get_connection(tmp_path / "magpie.db")
        try:
            tokens = list_tokens(conn)
            revoked_tokens = [t for t in tokens if t.name == "to-revoke"]
            assert len(revoked_tokens) == 0
        finally:
            conn.close()

    def test_token_revoke_nonexistent(self, ctl_runner: tuple[CliRunner, Path]) -> None:
        """Token revoke with nonexistent token shows error."""
        runner, tmp_path = ctl_runner
        runner.invoke(ctl_cli, ["init"])

        result = runner.invoke(ctl_cli, ["token", "revoke", "nonexistent"])

        assert result.exit_code != 0
        assert "not found" in result.output

    def test_token_rotate(self, ctl_runner: tuple[CliRunner, Path]) -> None:
        """Token rotate generates new token with same name and scope."""
        runner, tmp_path = ctl_runner
        runner.invoke(ctl_cli, ["init"])

        # Create token
        result1 = runner.invoke(
            ctl_cli, ["token", "create", "--name", "rotate-test", "--scope", "write"]
        )
        assert result1.exit_code == 0
        # Extract original token using regex
        match1 = re.search(r"mgp_\w+", result1.output)
        assert match1 is not None, "Failed to find token in create output"
        token1 = match1.group(0)

        # Rotate token
        result2 = runner.invoke(ctl_cli, ["token", "rotate", "rotate-test"])
        assert result2.exit_code == 0
        assert "TOKEN ROTATED: rotate-test" in result2.output

        # Extract new token using regex
        match2 = re.search(r"mgp_\w+", result2.output)
        assert match2 is not None, "Failed to find token in rotate output"
        token2 = match2.group(0)

        # Verify tokens are different
        assert token1 != token2

        # Verify only one token with that name exists
        conn = get_connection(tmp_path / "magpie.db")
        try:
            tokens = list_tokens(conn)
            rotate_tokens = [t for t in tokens if t.name == "rotate-test"]
            assert len(rotate_tokens) == 1
            assert rotate_tokens[0].scope == TokenScope.WRITE
        finally:
            conn.close()

    def test_token_rotate_nonexistent(self, ctl_runner: tuple[CliRunner, Path]) -> None:
        """Token rotate with nonexistent token shows error."""
        runner, tmp_path = ctl_runner
        runner.invoke(ctl_cli, ["init"])

        result = runner.invoke(ctl_cli, ["token", "rotate", "nonexistent"])

        assert result.exit_code != 0
        assert "not found" in result.output

    def test_token_create_json_output(self, ctl_runner: tuple[CliRunner, Path]) -> None:
        """Token create with --format json outputs structured JSON."""
        runner, tmp_path = ctl_runner
        runner.invoke(ctl_cli, ["init"])

        result = runner.invoke(
            ctl_cli,
            ["--format", "json", "token", "create", "--name", "json-test", "--scope", "read"],
        )

        assert result.exit_code == 0
        response = json.loads(result.output)
        assert response["status"] == "ok"
        data = response["data"]
        assert "token" in data
        assert data["token"].startswith("mgp_")
        assert data["name"] == "json-test"
        assert data["scope"] == "read"

    def test_token_list_json_output(self, ctl_runner: tuple[CliRunner, Path]) -> None:
        """Token list with --format json outputs structured JSON."""
        runner, tmp_path = ctl_runner
        runner.invoke(ctl_cli, ["init"])
        runner.invoke(ctl_cli, ["token", "create", "--name", "list-test", "--scope", "read"])

        result = runner.invoke(ctl_cli, ["--format", "json", "token", "list"])

        assert result.exit_code == 0
        response = json.loads(result.output)
        assert response["status"] == "ok"
        data = response["data"]
        assert "tokens" in data
        assert len(data["tokens"]) >= 2  # admin + list-test
        # Check token structure
        for token in data["tokens"]:
            assert "name" in token
            assert "scope" in token
            assert "created" in token
            assert "enabled" in token


class TestDebugFlag:
    """Integration tests for --debug flag."""

    def test_debug_flag_shows_paths(self, ctl_runner: tuple[CliRunner, Path]) -> None:
        """Debug flag shows storage and database paths."""
        runner, tmp_path = ctl_runner

        result = runner.invoke(ctl_cli, ["--debug", "init"])

        assert result.exit_code == 0
        assert "Storage path:" in result.output
        assert "Database path:" in result.output


class TestVersion:
    """Integration tests for version command."""

    def test_version_command(self, ctl_runner: tuple[CliRunner, Path]) -> None:
        """Version command shows magpie-ctl version."""
        runner, tmp_path = ctl_runner

        result = runner.invoke(ctl_cli, ["version"])

        assert result.exit_code == 0
        assert "magpie-ctl" in result.output
