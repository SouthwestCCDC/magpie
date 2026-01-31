"""Integration tests for magpie-ctl admin tool.

These tests verify that magpie-ctl commands work correctly with actual filesystem
and database operations (no mocking of storage or database access).
"""

from __future__ import annotations

import json
import re
import shutil
import threading
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
    environment variables are properly read. Uses try/finally to guarantee
    cleanup even if test fails.
    """
    # Clear settings cache before test
    get_settings.cache_clear()

    runner = CliRunner(
        env={
            "MAGPIE_STORAGE_PATH": str(tmp_path),
            "MAGPIE_DATABASE_PATH": str(tmp_path / "magpie.db"),
        }
    )

    try:
        yield runner, tmp_path
    finally:
        # Clear settings cache after test (even if test fails)
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

        # Verify second init did NOT create duplicate admin tokens
        conn = get_connection(tmp_path / "magpie.db")
        try:
            tokens = list_tokens(conn)
            admin_tokens = [t for t in tokens if t.name == "admin"]
            assert len(admin_tokens) == 1, "Second init should not create duplicate admin token"
        finally:
            conn.close()

    def test_init_reset_admin_token(self, ctl_runner: tuple[CliRunner, Path]) -> None:
        """Init --reset-admin-token regenerates admin token."""
        runner, tmp_path = ctl_runner

        # First init
        result1 = runner.invoke(ctl_cli, ["init"])
        assert result1.exit_code == 0
        # Extract token from output using regex
        match1 = re.search(r"mgp_ADMIN_[A-Za-z0-9_-]+", result1.output)
        assert match1 is not None, "Failed to find admin token in output"
        token1 = match1.group(0)
        assert token1.startswith("mgp_ADMIN_")

        # Reset admin token
        result2 = runner.invoke(ctl_cli, ["init", "--reset-admin-token"])
        assert result2.exit_code == 0
        assert "Revoked existing admin token" in result2.output
        assert "NEW ADMIN TOKEN" in result2.output

        # Extract new token using regex
        match2 = re.search(r"mgp_ADMIN_[A-Za-z0-9_-]+", result2.output)
        assert match2 is not None, "Failed to find new admin token in output"
        token2 = match2.group(0)
        assert token2.startswith("mgp_ADMIN_")
        assert token1 != token2

        # Verify old token was removed from database (only one admin token exists)
        conn = get_connection(tmp_path / "magpie.db")
        try:
            tokens = list_tokens(conn)
            admin_tokens = [t for t in tokens if t.name == "admin"]
            assert len(admin_tokens) == 1, "Old admin token should be removed after reset"
        finally:
            conn.close()

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
        """Init with --format json outputs structured JSON with correct types."""
        runner, tmp_path = ctl_runner
        result = runner.invoke(ctl_cli, ["--format", "json", "init"])

        assert result.exit_code == 0
        response = json.loads(result.output)

        # Validate top-level structure
        assert isinstance(response, dict), "Response must be a dict"
        assert set(response.keys()) == {
            "status",
            "data",
        }, "Response should only contain 'status' and 'data'"
        assert response["status"] == "ok"
        assert isinstance(response["status"], str), "status must be string"

        # Validate data structure and types
        data = response["data"]
        assert isinstance(data, dict), "data must be a dict"

        # Required fields
        required_keys = {"admin_token", "storage_path", "database_path"}
        assert required_keys.issubset(data.keys()), (
            f"data must contain required keys: {required_keys}"
        )

        # No unexpected fields (allow token_already_existed as optional)
        allowed_keys = required_keys | {"token_already_existed"}
        assert set(data.keys()).issubset(allowed_keys), (
            f"data contains unexpected keys: {set(data.keys()) - allowed_keys}"
        )

        # Validate required field types
        assert isinstance(data["admin_token"], str), "admin_token must be string"
        assert data["admin_token"].startswith("mgp_"), "admin_token must have mgp_ prefix"
        assert isinstance(data["storage_path"], str), "storage_path must be string"
        assert isinstance(data["database_path"], str), "database_path must be string"

        # Validate optional field types if present
        if "token_already_existed" in data:
            assert isinstance(data["token_already_existed"], bool), (
                "token_already_existed must be boolean"
            )

    def test_init_concurrent_safety(self, ctl_runner: tuple[CliRunner, Path]) -> None:
        """Init handles concurrent execution safely (only one admin token created).

        This test uses the service layer directly (not CLI) to test concurrent token
        creation with the same name. Database constraints should prevent duplicate
        tokens even with concurrent attempts.
        """
        runner, tmp_path = ctl_runner

        # Initialize database and storage
        init_database(tmp_path / "magpie.db")

        # Simulate concurrent admin token creation using service layer
        from magpie.auth.service import TokenService
        from magpie.config import MagpieSettings

        # Create settings with test database
        settings = MagpieSettings(storage_path=tmp_path, database_path=tmp_path / "magpie.db")

        successes = []
        failures = []

        def try_create_admin_token():
            """Attempt to create admin token named 'admin'."""
            try:
                token_service = TokenService(settings)
                token = token_service.create_token("admin", TokenScope.ADMIN)
                successes.append(token)
            except Exception as e:
                failures.append(e)

        # Run two threads trying to create the same admin token
        thread1 = threading.Thread(target=try_create_admin_token)
        thread2 = threading.Thread(target=try_create_admin_token)

        thread1.start()
        thread2.start()

        thread1.join()
        thread2.join()

        # Exactly one should succeed, one should fail with IntegrityError/TokenExistsError
        assert len(successes) == 1, f"Exactly one thread should succeed, got {len(successes)}"
        assert len(failures) == 1, f"Exactly one thread should fail, got {len(failures)}"

        # Critical: verify only ONE admin token exists in database
        conn = get_connection(tmp_path / "magpie.db")
        try:
            tokens = list_tokens(conn)
            admin_tokens = [t for t in tokens if t.name == "admin"]
            assert len(admin_tokens) == 1, (
                "Concurrent token creation should result in exactly one admin token (race condition safety)"
            )
        finally:
            conn.close()


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
        """GC with --format json outputs structured JSON with correct types."""
        runner, tmp_path = ctl_runner
        # Initialize storage
        runner.invoke(ctl_cli, ["init"])

        result = runner.invoke(ctl_cli, ["--format", "json", "gc", "--dry-run"])

        assert result.exit_code == 0
        response = json.loads(result.output)

        # Validate top-level structure
        assert isinstance(response, dict), "Response must be a dict"
        assert set(response.keys()) == {
            "status",
            "data",
        }, "Response should only contain 'status' and 'data'"
        assert response["status"] == "ok"
        assert isinstance(response["status"], str), "status must be string"

        # Validate data structure and types
        data = response["data"]
        assert isinstance(data, dict), "data must be a dict"

        # Required fields with type validation
        required_fields = {
            "artifacts_scanned": int,
            "blobs_found": int,
            "symlinks_checked": int,
        }

        for field, expected_type in required_fields.items():
            assert field in data, f"Missing required field: {field}"
            assert isinstance(data[field], expected_type), (
                f"{field} must be {expected_type.__name__}"
            )

        # Reject completely unexpected keys (but allow additional documented fields)
        known_fields = required_fields.keys() | {
            "blobs_removed",
            "bytes_reclaimed",
            "symlinks_fixed",
            "items_removed",
            "blobs_deleted",
            "space_reclaimed_bytes",
            "dry_run",
            "reconcile_only",
            "errors",
        }
        unexpected = set(data.keys()) - known_fields
        assert not unexpected, f"Unexpected fields in response: {unexpected}"

        # Validate types of optional fields if present
        if "dry_run" in data:
            assert isinstance(data["dry_run"], bool), "dry_run must be boolean"
        if "reconcile_only" in data:
            assert isinstance(data["reconcile_only"], bool), "reconcile_only must be boolean"
        if "errors" in data:
            assert isinstance(data["errors"], list), "errors must be list"

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

    def test_gc_corrupt_manifest_handling(self, ctl_runner: tuple[CliRunner, Path]) -> None:
        """GC handles corrupt .magpie manifest files gracefully."""
        runner, tmp_path = ctl_runner
        # Initialize storage
        runner.invoke(ctl_cli, ["init"])

        # Create an artifact directory with corrupt manifest
        artifact_dir = tmp_path / "test-artifact"
        artifact_dir.mkdir()
        manifest_file = artifact_dir / ".magpie"

        # Write invalid JSON to manifest
        manifest_file.write_text("{ this is not valid JSON }", encoding="utf-8")

        # GC should handle this gracefully (either skip or report error, but not crash)
        result = runner.invoke(ctl_cli, ["gc", "--dry-run"])

        # Should not crash - either succeeds with error handling or reports error gracefully
        # We accept either exit code 0 (graceful skip) or non-zero (reported error)
        # but must not raise unhandled exception
        assert result.exit_code in (
            0,
            1,
        ), f"GC should handle corrupt manifest gracefully, got: {result.output}"

        # If it succeeded, verify it logged/reported the issue
        if result.exit_code == 0:
            # Should either mention the error in output or silently skip
            # We just verify it didn't crash
            assert "GC Summary:" in result.output or "error" in result.output.lower()


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
        assert "already exists" in result2.output

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
        """Token revoke removes token and prevents authentication."""
        runner, tmp_path = ctl_runner
        runner.invoke(ctl_cli, ["init"])

        # Create token and extract plaintext
        create_result = runner.invoke(
            ctl_cli, ["token", "create", "--name", "to-revoke", "--scope", "read"]
        )
        assert create_result.exit_code == 0
        # Extract token from output using regex
        match = re.search(r"mgp_[A-Za-z0-9_-]+", create_result.output)
        assert match is not None, "Failed to find token in create output"
        plaintext_token = match.group(0)

        # Verify token works before revocation
        from magpie.auth.service import TokenService
        from magpie.config import get_settings

        settings = get_settings()
        token_service = TokenService(settings)
        token_info = token_service.validate_token(plaintext_token)
        assert token_info is not None, "Token should be valid before revocation"
        assert token_info.name == "to-revoke"
        assert token_info.scope == TokenScope.READ

        # Revoke token
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

        # Critical security property: verify revoked token can no longer authenticate
        token_info_after_revoke = token_service.validate_token(plaintext_token)
        assert token_info_after_revoke is None, (
            "Revoked token should fail authentication (security requirement)"
        )

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
        match1 = re.search(r"mgp_[A-Za-z0-9_-]+", result1.output)
        assert match1 is not None, "Failed to find token in create output"
        token1 = match1.group(0)

        # Rotate token
        result2 = runner.invoke(ctl_cli, ["token", "rotate", "rotate-test"])
        assert result2.exit_code == 0
        assert "TOKEN ROTATED: rotate-test" in result2.output

        # Extract new token using regex
        match2 = re.search(r"mgp_[A-Za-z0-9_-]+", result2.output)
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

    def test_token_rotate_preserves_read_scope(self, ctl_runner: tuple[CliRunner, Path]) -> None:
        """Token rotate preserves READ scope (does not accidentally upgrade)."""
        runner, tmp_path = ctl_runner
        runner.invoke(ctl_cli, ["init"])

        # Create READ token
        runner.invoke(ctl_cli, ["token", "create", "--name", "reader", "--scope", "read"])

        # Rotate token
        result = runner.invoke(ctl_cli, ["token", "rotate", "reader"])
        assert result.exit_code == 0

        # Verify scope is still READ (not upgraded to WRITE or ADMIN)
        conn = get_connection(tmp_path / "magpie.db")
        try:
            tokens = list_tokens(conn)
            reader_tokens = [t for t in tokens if t.name == "reader"]
            assert len(reader_tokens) == 1
            assert reader_tokens[0].scope == TokenScope.READ, (
                "Rotating READ token should preserve READ scope"
            )
        finally:
            conn.close()

    def test_token_rotate_preserves_write_scope(self, ctl_runner: tuple[CliRunner, Path]) -> None:
        """Token rotate preserves WRITE scope (does not downgrade or upgrade)."""
        runner, tmp_path = ctl_runner
        runner.invoke(ctl_cli, ["init"])

        # Create WRITE token
        runner.invoke(ctl_cli, ["token", "create", "--name", "writer", "--scope", "write"])

        # Rotate token
        result = runner.invoke(ctl_cli, ["token", "rotate", "writer"])
        assert result.exit_code == 0

        # Verify scope is still WRITE (not downgraded to READ or upgraded to ADMIN)
        conn = get_connection(tmp_path / "magpie.db")
        try:
            tokens = list_tokens(conn)
            writer_tokens = [t for t in tokens if t.name == "writer"]
            assert len(writer_tokens) == 1
            assert writer_tokens[0].scope == TokenScope.WRITE, (
                "Rotating WRITE token should preserve WRITE scope"
            )
        finally:
            conn.close()

    def test_token_rotate_preserves_admin_scope(self, ctl_runner: tuple[CliRunner, Path]) -> None:
        """Token rotate preserves ADMIN scope (does not downgrade)."""
        runner, tmp_path = ctl_runner
        runner.invoke(ctl_cli, ["init"])

        # Create another ADMIN token (in addition to the one from init)
        runner.invoke(ctl_cli, ["token", "create", "--name", "ops-admin", "--scope", "admin"])

        # Rotate token
        result = runner.invoke(ctl_cli, ["token", "rotate", "ops-admin"])
        assert result.exit_code == 0

        # Verify scope is still ADMIN (not downgraded)
        conn = get_connection(tmp_path / "magpie.db")
        try:
            tokens = list_tokens(conn)
            admin_tokens = [t for t in tokens if t.name == "ops-admin"]
            assert len(admin_tokens) == 1
            assert admin_tokens[0].scope == TokenScope.ADMIN, (
                "Rotating ADMIN token should preserve ADMIN scope"
            )
        finally:
            conn.close()

    def test_token_create_json_output(self, ctl_runner: tuple[CliRunner, Path]) -> None:
        """Token create with --format json outputs structured JSON with correct types."""
        runner, tmp_path = ctl_runner
        runner.invoke(ctl_cli, ["init"])

        result = runner.invoke(
            ctl_cli,
            ["--format", "json", "token", "create", "--name", "json-test", "--scope", "read"],
        )

        assert result.exit_code == 0
        response = json.loads(result.output)

        # Validate top-level structure
        assert isinstance(response, dict), "Response must be a dict"
        assert set(response.keys()) == {
            "status",
            "data",
        }, "Response should only contain 'status' and 'data'"
        assert response["status"] == "ok"
        assert isinstance(response["status"], str), "status must be string"

        # Validate data structure and types
        data = response["data"]
        assert isinstance(data, dict), "data must be a dict"
        assert set(data.keys()) == {
            "token",
            "name",
            "scope",
        }, "data should only contain expected keys"
        assert isinstance(data["token"], str), "token must be string"
        assert data["token"].startswith("mgp_"), "token must have mgp_ prefix"
        assert isinstance(data["name"], str), "name must be string"
        assert data["name"] == "json-test"
        assert isinstance(data["scope"], str), "scope must be string"
        assert data["scope"] == "read"

    def test_token_list_json_output(self, ctl_runner: tuple[CliRunner, Path]) -> None:
        """Token list with --format json outputs structured JSON with correct types."""
        runner, tmp_path = ctl_runner
        runner.invoke(ctl_cli, ["init"])
        runner.invoke(ctl_cli, ["token", "create", "--name", "list-test", "--scope", "read"])

        result = runner.invoke(ctl_cli, ["--format", "json", "token", "list"])

        assert result.exit_code == 0
        response = json.loads(result.output)

        # Validate top-level structure
        assert isinstance(response, dict), "Response must be a dict"
        assert set(response.keys()) == {
            "status",
            "data",
        }, "Response should only contain 'status' and 'data'"
        assert response["status"] == "ok"
        assert isinstance(response["status"], str), "status must be string"

        # Validate data structure and types
        data = response["data"]
        assert isinstance(data, dict), "data must be a dict"
        assert set(data.keys()) == {"tokens"}, "data should only contain 'tokens'"
        assert isinstance(data["tokens"], list), "tokens must be a list"
        assert len(data["tokens"]) >= 2  # admin + list-test

        # Check token structure and types
        for token in data["tokens"]:
            assert isinstance(token, dict), "Each token must be a dict"
            assert set(token.keys()) == {
                "name",
                "scope",
                "created",
                "enabled",
            }, "Token should only contain expected keys"
            assert isinstance(token["name"], str), "name must be string"
            assert isinstance(token["scope"], str), "scope must be string"
            assert isinstance(token["created"], str), "created must be string (ISO timestamp)"
            assert isinstance(token["enabled"], bool), "enabled must be boolean"


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
