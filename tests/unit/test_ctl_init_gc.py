"""Unit tests for CTL init and gc commands."""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta, timezone
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
    return MagpieSettings(
        storage_path=tmp_path / "storage",
        database_path=tmp_path / "magpie.db",
        retention_days=30,
    )


def create_artifact_with_blobs(
    storage_path: Path,
    artifact_path: str,
    tagged_hashes: dict[str, str],
    untagged_hashes: list[str],
    blob_ages_days: dict[str, int] | None = None,
) -> None:
    """Helper to create an artifact directory with blobs and manifest.

    Simulates the actual storage scheme where:
    - Manifest stores full hashes
    - Blob files use first 8 chars of hash
    - Metadata files use first 8 chars of hash (but contain full hash inside)

    Args:
        storage_path: Base storage path.
        artifact_path: Relative artifact path.
        tagged_hashes: Dict of tag_name -> hash for tagged blobs.
        untagged_hashes: List of hashes for untagged blobs.
        blob_ages_days: Optional dict of hash -> age in days for metadata.
    """
    artifact_dir = storage_path / artifact_path
    blobs_dir = artifact_dir / "blobs"
    metadata_dir = artifact_dir / "metadata"
    blobs_dir.mkdir(parents=True, exist_ok=True)
    metadata_dir.mkdir(parents=True, exist_ok=True)

    # Create manifest (stores full hashes)
    manifest = {
        "version": 1,
        "tags": tagged_hashes,
    }
    manifest_file = artifact_dir / ".magpie"
    manifest_file.write_text(json.dumps(manifest), encoding="utf-8")

    # Create all blobs and their metadata
    all_hashes = list(tagged_hashes.values()) + untagged_hashes
    blob_ages_days = blob_ages_days or {}

    for blob_hash in all_hashes:
        # Blob and metadata files use short hash (first 8 chars, @ prefix stripped)
        # This matches blob_path() behavior in src/magpie/storage/paths.py
        short_hash = blob_hash.lstrip("@")[:8]

        # Create blob file with short hash name (no @ prefix)
        blob_file = blobs_dir / short_hash
        blob_file.write_bytes(b"test content for " + blob_hash.encode())

        # Create metadata with upload timestamp (short hash filename, full hash inside)
        age_days = blob_ages_days.get(blob_hash, 0)
        upload_time = datetime.now(timezone.utc) - timedelta(days=age_days)
        metadata = {
            "hash": blob_hash,  # Full hash stored inside metadata
            "uploaded_by": "test",
            "uploaded_at": upload_time.isoformat(),
            "source_uri": None,
        }
        metadata_file = metadata_dir / f"{short_hash}.json"
        metadata_file.write_text(json.dumps(metadata), encoding="utf-8")


class TestInitCommand:
    """Tests for init command.

    Most of these tests use sink=stdout explicitly to exercise the
    delivery path while keeping the token visible in `result.output` for
    assertions. Sink-specific behavior (file/exec/discard/fail-closed) is
    covered in TestInitAdminTokenSink below and in test_token_sink.py.
    """

    def test_init_creates_directories(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """Init creates storage and temp directories."""
        settings = test_settings.model_copy(update={"admin_token_sink": "discard"})
        with patch("magpie.ctl.commands.init.CTLContext") as mock_ctx_class:
            # Setup mock
            mock_ctx = mock_ctx_class.return_value
            mock_ctx.settings = settings
            mock_ctx.debug = False

            with patch("magpie.ctl.get_settings", return_value=settings):
                result = cli_runner.invoke(cli, ["init"])

        assert result.exit_code == 0, f"Output: {result.output}"
        assert settings.storage_path.exists()
        assert settings.temp_path.exists()

    def test_init_creates_database(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """Init creates SQLite database."""
        settings = test_settings.model_copy(update={"admin_token_sink": "discard"})
        with patch("magpie.ctl.get_settings", return_value=settings):
            result = cli_runner.invoke(cli, ["init"])

        assert result.exit_code == 0, f"Output: {result.output}"
        assert settings.database_path.exists()
        assert "Database initialized" in result.output

    def test_init_creates_admin_token(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """Init creates admin token on first run and delivers it via sink=stdout."""
        settings = test_settings.model_copy(update={"admin_token_sink": "stdout"})
        with patch("magpie.ctl.get_settings", return_value=settings):
            result = cli_runner.invoke(cli, ["init"])

        assert result.exit_code == 0, f"Output: {result.output}"
        assert "ADMIN TOKEN" in result.output
        assert "mgp_ADMIN_" in result.output

    def test_init_without_sink_configured_fails_closed(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """Init aborts (non-zero exit) when no sink is configured; nothing is printed."""
        with patch("magpie.ctl.get_settings", return_value=test_settings):
            result = cli_runner.invoke(cli, ["init"])

        assert result.exit_code != 0, f"Output: {result.output}"
        assert "MAGPIE_ADMIN_TOKEN_SINK is not set" in result.output
        assert "mgp_ADMIN_" not in result.output

    def test_init_token_already_exists(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """Init reports existing token when run twice; sink isn't needed on the second run."""
        settings = test_settings.model_copy(update={"admin_token_sink": "stdout"})
        with patch("magpie.ctl.get_settings", return_value=settings):
            # First init creates token
            result1 = cli_runner.invoke(cli, ["init"])
            assert result1.exit_code == 0

            # Second init should report token exists, even with no sink configured,
            # since no new token is generated (first-boot-only).
            no_sink_settings = test_settings.model_copy(
                update={"database_path": settings.database_path}
            )
        with patch("magpie.ctl.get_settings", return_value=no_sink_settings):
            result2 = cli_runner.invoke(cli, ["init"])
            assert result2.exit_code == 0, f"Output: {result2.output}"
            assert "already exists" in result2.output

    def test_init_reset_admin_token_regenerates(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """Init --reset-admin-token regenerates token and delivers it via the sink."""
        settings = test_settings.model_copy(update={"admin_token_sink": "stdout"})
        with patch("magpie.ctl.get_settings", return_value=settings):
            # First init creates token
            result1 = cli_runner.invoke(cli, ["init"])
            assert result1.exit_code == 0
            # Extract first token using regex
            match1 = re.search(r"mgp_ADMIN_[A-Za-z0-9_-]+", result1.output)
            assert match1 is not None, "Failed to find admin token in output"
            first_token = match1.group(0)

            # Reset token
            result2 = cli_runner.invoke(cli, ["init", "--reset-admin-token"])
            assert result2.exit_code == 0
            assert "ADMIN TOKEN" in result2.output
            assert "Revoked existing" in result2.output

            # Extract second token using regex
            match2 = re.search(r"mgp_ADMIN_[A-Za-z0-9_-]+", result2.output)
            assert match2 is not None, "Failed to find new admin token in output"
            second_token = match2.group(0)

            # Tokens should be different
            assert first_token != second_token

    def test_init_reset_admin_token_fails_closed_without_sink(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """Init --reset-admin-token aborts if no sink is configured for delivery."""
        settings = test_settings.model_copy(update={"admin_token_sink": "discard"})
        with patch("magpie.ctl.get_settings", return_value=settings):
            result1 = cli_runner.invoke(cli, ["init"])
            assert result1.exit_code == 0

        no_sink_settings = test_settings.model_copy(
            update={"database_path": settings.database_path}
        )
        with patch("magpie.ctl.get_settings", return_value=no_sink_settings):
            result2 = cli_runner.invoke(cli, ["init", "--reset-admin-token"])

        assert result2.exit_code != 0, f"Output: {result2.output}"
        assert "MAGPIE_ADMIN_TOKEN_SINK is not set" in result2.output

    def test_init_with_custom_admin_token(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """Init accepts custom admin token and delivers it via sink=stdout."""
        custom_token = "mgp_ADMIN_my_custom_token_123"
        settings = test_settings.model_copy(update={"admin_token_sink": "stdout"})

        with patch("magpie.ctl.get_settings", return_value=settings):
            result = cli_runner.invoke(cli, ["init", "--admin-token", custom_token])

        assert result.exit_code == 0, f"Output: {result.output}"
        assert "ADMIN TOKEN" in result.output
        assert custom_token in result.output

        # Verify the token was stored correctly by trying to validate it
        from magpie.auth.service import TokenService

        token_service = TokenService(settings)
        token_info = token_service.validate_token(custom_token)
        assert token_info is not None
        assert token_info.name == "admin"
        assert token_info.scope.value == "admin"

    def test_init_with_invalid_token_prefix(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """Init rejects token without correct prefix (checked before sink delivery)."""
        invalid_token = "mgp_wrong_prefix_123"

        # Deliberately no sink configured: format validation must fail before
        # delivery is ever attempted.
        with patch("magpie.ctl.get_settings", return_value=test_settings):
            result = cli_runner.invoke(cli, ["init", "--admin-token", invalid_token])

        assert result.exit_code == 1, f"Output: {result.output}"
        assert "must start with 'mgp_ADMIN_'" in result.output

    def test_init_with_empty_token(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """Init rejects token that is just the prefix."""
        empty_token = "mgp_ADMIN_"

        with patch("magpie.ctl.get_settings", return_value=test_settings):
            result = cli_runner.invoke(cli, ["init", "--admin-token", empty_token])

        assert result.exit_code == 1, f"Output: {result.output}"
        assert "too short" in result.output

    def test_init_reset_with_custom_token(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """Init --reset-admin-token works with custom token, delivered via sink."""
        custom_token = "mgp_ADMIN_my_new_token_456"
        settings = test_settings.model_copy(update={"admin_token_sink": "stdout"})

        with patch("magpie.ctl.get_settings", return_value=settings):
            # First init with random token
            result1 = cli_runner.invoke(cli, ["init"])
            assert result1.exit_code == 0

            # Reset with custom token
            result2 = cli_runner.invoke(
                cli, ["init", "--reset-admin-token", "--admin-token", custom_token]
            )
            assert result2.exit_code == 0
            assert "ADMIN TOKEN" in result2.output
            assert custom_token in result2.output
            assert "Revoked existing" in result2.output

        # Verify the custom token is now active
        from magpie.auth.service import TokenService

        token_service = TokenService(settings)
        token_info = token_service.validate_token(custom_token)
        assert token_info is not None
        assert token_info.name == "admin"

    def test_init_with_custom_token_when_token_exists(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """Init --admin-token succeeds but reports when admin token already exists."""
        custom_token = "mgp_ADMIN_custom_token_789"
        settings = test_settings.model_copy(update={"admin_token_sink": "stdout"})

        with patch("magpie.ctl.get_settings", return_value=settings):
            # First init creates an admin token
            result1 = cli_runner.invoke(cli, ["init"])
            assert result1.exit_code == 0

            # Second init with custom token should succeed but report existing admin token,
            # even with no sink configured, since nothing new is generated or delivered.
            no_sink_settings = test_settings.model_copy(
                update={"database_path": settings.database_path}
            )
        with patch("magpie.ctl.get_settings", return_value=no_sink_settings):
            result2 = cli_runner.invoke(cli, ["init", "--admin-token", custom_token])
            assert result2.exit_code == 0, "init should succeed but report token exists"
            assert "already exists" in result2.output, "Should report token already exists"
            # Custom token should NOT be created or displayed
            assert custom_token not in result2.output, "Custom token should not be shown"


class TestInitAdminTokenSink:
    """Tests for MAGPIE_ADMIN_TOKEN_SINK delivery, fail-closed behavior, and discard."""

    def test_file_sink_writes_0600_file(
        self, cli_runner: CliRunner, test_settings: MagpieSettings, tmp_path: Path
    ) -> None:
        """sink=file writes the token to a root-only file and doesn't print it."""
        import os
        import stat

        token_file = tmp_path / "admin-token"
        settings = test_settings.model_copy(
            update={"admin_token_sink": "file", "admin_token_sink_file_path": token_file}
        )

        with patch("magpie.ctl.get_settings", return_value=settings):
            result = cli_runner.invoke(cli, ["init"])

        assert result.exit_code == 0, f"Output: {result.output}"
        assert "mgp_ADMIN_" not in result.output
        assert token_file.exists()
        token_value = token_file.read_text().strip()
        assert token_value.startswith("mgp_ADMIN_")
        mode = stat.S_IMODE(os.stat(token_file).st_mode)
        assert mode == 0o600

    def test_file_sink_delivery_failure_aborts_init(
        self, cli_runner: CliRunner, test_settings: MagpieSettings, tmp_path: Path
    ) -> None:
        """A delivery failure for sink=file aborts init with a non-zero exit."""
        blocker = tmp_path / "blocker"
        blocker.write_text("not a directory")
        unwritable_target = blocker / "admin-token"
        settings = test_settings.model_copy(
            update={"admin_token_sink": "file", "admin_token_sink_file_path": unwritable_target}
        )

        with patch("magpie.ctl.get_settings", return_value=settings):
            result = cli_runner.invoke(cli, ["init"])

        assert result.exit_code != 0, f"Output: {result.output}"
        assert "mgp_ADMIN_" not in result.output

    def test_exec_sink_receives_token_on_stdin(
        self, cli_runner: CliRunner, test_settings: MagpieSettings, tmp_path: Path
    ) -> None:
        """sink=exec pipes the token to the configured command's stdin, not to stdout."""
        out_file = tmp_path / "captured"
        command = (
            f"{sys.executable} -c \"import sys; open('{out_file}', 'w').write(sys.stdin.read())\""
        )
        settings = test_settings.model_copy(
            update={"admin_token_sink": "exec", "admin_token_sink_exec_command": command}
        )

        with patch("magpie.ctl.get_settings", return_value=settings):
            result = cli_runner.invoke(cli, ["init"])

        assert result.exit_code == 0, f"Output: {result.output}"
        assert "mgp_ADMIN_" not in result.output
        assert out_file.read_text().strip().startswith("mgp_ADMIN_")

    def test_exec_sink_nonzero_exit_aborts_init(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """A non-zero exit from the exec sink command aborts init with a non-zero exit."""
        command = f'{sys.executable} -c "import sys; sys.exit(1)"'
        settings = test_settings.model_copy(
            update={"admin_token_sink": "exec", "admin_token_sink_exec_command": command}
        )

        with patch("magpie.ctl.get_settings", return_value=settings):
            result = cli_runner.invoke(cli, ["init"])

        assert result.exit_code != 0, f"Output: {result.output}"
        assert "mgp_ADMIN_" not in result.output

    def test_discard_sink_retains_no_usable_token_in_output(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """sink=discard succeeds without printing the token anywhere."""
        settings = test_settings.model_copy(update={"admin_token_sink": "discard"})

        with patch("magpie.ctl.get_settings", return_value=settings):
            result = cli_runner.invoke(cli, ["init"])

        assert result.exit_code == 0, f"Output: {result.output}"
        assert "mgp_ADMIN_" not in result.output
        assert "discard" in result.output.lower()
        assert "magpie-ctl token create" in result.output

    def test_discard_sink_token_recoverable_via_token_create(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """After discard, a new usable admin-scope token can be minted via token create."""
        settings = test_settings.model_copy(update={"admin_token_sink": "discard"})

        with patch("magpie.ctl.get_settings", return_value=settings):
            result = cli_runner.invoke(cli, ["init"])
            assert result.exit_code == 0

            create_result = cli_runner.invoke(
                cli, ["token", "create", "--name", "ops-admin", "--scope", "admin"]
            )

        assert create_result.exit_code == 0, f"Output: {create_result.output}"
        match = re.search(r"mgp_ADMIN_[A-Za-z0-9_-]+", create_result.output)
        assert match is not None

        from magpie.auth.service import TokenService

        token_service = TokenService(settings)
        token_info = token_service.validate_token(match.group(0))
        assert token_info is not None
        assert token_info.scope.value == "admin"

    def test_first_boot_only_second_init_does_not_regenerate(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """A second `init` call (DB already present) never generates/delivers a new token."""
        settings = test_settings.model_copy(update={"admin_token_sink": "stdout"})

        with patch("magpie.ctl.get_settings", return_value=settings):
            result1 = cli_runner.invoke(cli, ["init"])
            match1 = re.search(r"mgp_ADMIN_[A-Za-z0-9_-]+", result1.output)
            assert match1 is not None
            first_token = match1.group(0)

            result2 = cli_runner.invoke(cli, ["init"])

        assert result2.exit_code == 0
        assert "already exists" in result2.output
        assert first_token not in result2.output
        assert "mgp_ADMIN_" not in result2.output

    def test_first_boot_delivery_failure_persists_no_admin_token(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """A failed first-boot delivery leaves no admin token row in the database."""
        from magpie.auth.database import get_connection, list_tokens

        command = f'{sys.executable} -c "import sys; sys.exit(1)"'
        settings = test_settings.model_copy(
            update={"admin_token_sink": "exec", "admin_token_sink_exec_command": command}
        )

        with patch("magpie.ctl.get_settings", return_value=settings):
            result = cli_runner.invoke(cli, ["init"])

        assert result.exit_code != 0

        conn = get_connection(settings.database_path)
        try:
            tokens = list_tokens(conn)
        finally:
            conn.close()
        assert not any(t.name == "admin" for t in tokens)

    def test_discard_first_boot_persists_no_admin_token_row(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """sink=discard on first boot leaves no row for "admin" at all -- not just unusable."""
        from magpie.auth.database import get_connection, list_tokens

        settings = test_settings.model_copy(update={"admin_token_sink": "discard"})

        with patch("magpie.ctl.get_settings", return_value=settings):
            result = cli_runner.invoke(cli, ["init"])

        assert result.exit_code == 0, f"Output: {result.output}"

        conn = get_connection(settings.database_path)
        try:
            tokens = list_tokens(conn)
        finally:
            conn.close()
        assert tokens == []

    def test_reset_delivery_failure_leaves_prior_token_valid(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """A failed --reset-admin-token delivery must not destroy the working admin token.

        This is the critical lockout scenario: --reset-admin-token is the
        documented recovery runbook step, typically run via `docker exec`
        against an already-running server. If a sink failure revoked the
        old token before confirming delivery of the new one, the operator
        would be locked out entirely.
        """
        from magpie.auth.service import TokenService

        stdout_settings = test_settings.model_copy(update={"admin_token_sink": "stdout"})
        with patch("magpie.ctl.get_settings", return_value=stdout_settings):
            result1 = cli_runner.invoke(cli, ["init"])
        match1 = re.search(r"mgp_ADMIN_[A-Za-z0-9_-]+", result1.output)
        assert match1 is not None
        original_token = match1.group(0)

        failing_command = f'{sys.executable} -c "import sys; sys.exit(1)"'
        failing_settings = test_settings.model_copy(
            update={
                "admin_token_sink": "exec",
                "admin_token_sink_exec_command": failing_command,
            }
        )
        with patch("magpie.ctl.get_settings", return_value=failing_settings):
            result2 = cli_runner.invoke(cli, ["init", "--reset-admin-token"])

        assert result2.exit_code != 0, f"Output: {result2.output}"

        token_service = TokenService(test_settings)
        assert token_service.validate_token(original_token) is not None

    def test_reset_discard_revokes_old_but_persists_no_new_token(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """--reset-admin-token with sink=discard revokes the old token but persists nothing new."""
        from magpie.auth.database import get_connection, list_tokens
        from magpie.auth.service import TokenService

        stdout_settings = test_settings.model_copy(update={"admin_token_sink": "stdout"})
        with patch("magpie.ctl.get_settings", return_value=stdout_settings):
            result1 = cli_runner.invoke(cli, ["init"])
        match1 = re.search(r"mgp_ADMIN_[A-Za-z0-9_-]+", result1.output)
        assert match1 is not None
        original_token = match1.group(0)

        discard_settings = test_settings.model_copy(update={"admin_token_sink": "discard"})
        with patch("magpie.ctl.get_settings", return_value=discard_settings):
            result2 = cli_runner.invoke(cli, ["init", "--reset-admin-token"])

        assert result2.exit_code == 0, f"Output: {result2.output}"

        token_service = TokenService(test_settings)
        assert token_service.validate_token(original_token) is None

        conn = get_connection(test_settings.database_path)
        try:
            tokens = list_tokens(conn)
        finally:
            conn.close()
        assert not any(t.name == "admin" for t in tokens)


class TestInitJsonOutput:
    """Tests for init command JSON output with --admin-token."""

    def test_init_with_admin_token_json_output(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """Init --admin-token with --format json and sink=stdout includes the token."""
        custom_token = "mgp_ADMIN_json_test_token_xyz_abcdefgh"
        settings = test_settings.model_copy(update={"admin_token_sink": "stdout"})

        with patch("magpie.ctl.get_settings", return_value=settings):
            result = cli_runner.invoke(
                cli, ["--format", "json", "init", "--admin-token", custom_token]
            )

        assert result.exit_code == 0, f"Output: {result.output}"
        output = json.loads(result.stdout.strip())
        # JSON output wraps in {"status": "ok", "data": {...}}
        assert output["status"] == "ok"
        data = output["data"]
        assert data["admin_token"] == custom_token
        assert data["admin_token_sink"] == "stdout"
        assert "storage_path" in data
        assert "database_path" in data
        assert data["token_already_existed"] is False

    def test_init_json_output_non_stdout_sink_omits_token_value(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """JSON output never includes the token value for non-stdout sinks (it's still stdout)."""
        settings = test_settings.model_copy(update={"admin_token_sink": "discard"})

        with patch("magpie.ctl.get_settings", return_value=settings):
            result = cli_runner.invoke(cli, ["--format", "json", "init"])

        assert result.exit_code == 0, f"Output: {result.output}"
        output = json.loads(result.stdout.strip())
        data = output["data"]
        assert data["admin_token"] is None
        assert data["admin_token_sink"] == "discard"

    def test_init_with_invalid_token_json_output(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """Init --admin-token with invalid token and --format json returns JSON error."""
        invalid_token = "mgp_wrong_prefix_123"

        with patch("magpie.ctl.get_settings", return_value=test_settings):
            result = cli_runner.invoke(
                cli, ["--format", "json", "init", "--admin-token", invalid_token]
            )

        assert result.exit_code == 1, f"Output: {result.output}"
        output = json.loads(result.stdout.strip())
        # Error output uses {"status": "ok", "data": {"error": ...}} structure
        data = output.get("data", output)
        assert "error" in data
        assert "mgp_ADMIN_" in data["error"]

    def test_init_reset_with_custom_token_json_output(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """Init --reset-admin-token --admin-token with --format json returns valid JSON."""
        custom_token = "mgp_ADMIN_reset_json_test_456_abcdefgh"
        settings = test_settings.model_copy(update={"admin_token_sink": "stdout"})

        with patch("magpie.ctl.get_settings", return_value=settings):
            # First init creates a token
            result1 = cli_runner.invoke(cli, ["init"])
            assert result1.exit_code == 0

            # Reset with custom token and JSON output
            result2 = cli_runner.invoke(
                cli,
                ["--format", "json", "init", "--reset-admin-token", "--admin-token", custom_token],
            )

        assert result2.exit_code == 0, f"Output: {result2.output}"
        output = json.loads(result2.stdout.strip())
        # JSON output wraps in {"status": "ok", "data": {...}}
        assert output["status"] == "ok"
        data = output["data"]
        assert data["admin_token"] == custom_token
        assert data["token_already_existed"] is False

    def test_init_empty_token_json_output(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """Init --admin-token with empty suffix and --format json returns JSON error."""
        empty_token = "mgp_ADMIN_"

        with patch("magpie.ctl.get_settings", return_value=test_settings):
            result = cli_runner.invoke(
                cli, ["--format", "json", "init", "--admin-token", empty_token]
            )

        assert result.exit_code == 1, f"Output: {result.output}"
        output = json.loads(result.stdout.strip())
        # Error output uses {"status": "ok", "data": {"error": ...}} structure
        data = output.get("data", output)
        assert "error" in data
        assert "too short" in data["error"]


class TestGCCommand:
    """Tests for gc command."""

    def test_gc_identifies_untagged_blobs(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """GC identifies untagged blobs correctly."""
        # Create artifact with both tagged and untagged blobs
        test_settings.storage_path.mkdir(parents=True, exist_ok=True)
        create_artifact_with_blobs(
            test_settings.storage_path,
            "test/artifact",
            tagged_hashes={"latest": "tagged_hash_abc"},
            untagged_hashes=["untagged_hash_xyz"],
            blob_ages_days={"tagged_hash_abc": 0, "untagged_hash_xyz": 100},
        )

        with patch("magpie.ctl.get_settings", return_value=test_settings):
            result = cli_runner.invoke(cli, ["gc", "--dry-run"])

        assert result.exit_code == 0, f"Output: {result.output}"
        assert "Untagged blobs (eligible for deletion): 1" in result.output

    def test_gc_dry_run_does_not_modify(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """GC with --dry-run doesn't delete anything."""
        test_settings.storage_path.mkdir(parents=True, exist_ok=True)
        create_artifact_with_blobs(
            test_settings.storage_path,
            "test/artifact",
            tagged_hashes={"latest": "tagged_hash_abc"},
            untagged_hashes=["untagged_hash_xyz"],
            blob_ages_days={"tagged_hash_abc": 0, "untagged_hash_xyz": 100},
        )

        # Blob files use short hash (first 8 chars)
        blob_file = test_settings.storage_path / "test/artifact/blobs/untagged"
        assert blob_file.exists()

        with patch("magpie.ctl.get_settings", return_value=test_settings):
            result = cli_runner.invoke(cli, ["gc", "--dry-run"])

        assert result.exit_code == 0
        assert "Would delete" in result.output
        # File should still exist
        assert blob_file.exists()

    def test_gc_deletes_expired_untagged_blobs(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """GC deletes untagged blobs older than retention period."""
        test_settings.storage_path.mkdir(parents=True, exist_ok=True)
        create_artifact_with_blobs(
            test_settings.storage_path,
            "test/artifact",
            tagged_hashes={"latest": "tagged_hash_abc"},
            untagged_hashes=["old_untagged_xyz"],
            blob_ages_days={"tagged_hash_abc": 0, "old_untagged_xyz": 100},
        )

        # Blob files use short hash (first 8 chars)
        blob_file = test_settings.storage_path / "test/artifact/blobs/old_unta"
        assert blob_file.exists()

        with patch("magpie.ctl.get_settings", return_value=test_settings):
            result = cli_runner.invoke(cli, ["gc"])

        assert result.exit_code == 0, f"Output: {result.output}"
        assert "Deleted: 1 blob(s)" in result.output
        # File should be gone
        assert not blob_file.exists()

    def test_gc_respects_retention_days(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """GC doesn't delete blobs younger than retention period."""
        test_settings.storage_path.mkdir(parents=True, exist_ok=True)
        create_artifact_with_blobs(
            test_settings.storage_path,
            "test/artifact",
            tagged_hashes={"latest": "tagged_hash_abc"},
            untagged_hashes=["young_untagged_xyz"],
            blob_ages_days={
                "tagged_hash_abc": 0,
                "young_untagged_xyz": 10,  # Less than 30 day retention
            },
        )

        # Blob files use short hash (first 8 chars)
        blob_file = test_settings.storage_path / "test/artifact/blobs/young_un"
        assert blob_file.exists()

        with patch("magpie.ctl.get_settings", return_value=test_settings):
            result = cli_runner.invoke(cli, ["gc"])

        assert result.exit_code == 0, f"Output: {result.output}"
        assert "Deleted: 0 blob(s)" in result.output
        # File should still exist (not old enough)
        assert blob_file.exists()

    def test_gc_reconcile_only_fixes_symlinks(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """GC --reconcile-only only fixes symlinks, no blob deletion."""
        test_settings.storage_path.mkdir(parents=True, exist_ok=True)
        create_artifact_with_blobs(
            test_settings.storage_path,
            "test/artifact",
            tagged_hashes={"latest": "tagged_hash_abc"},
            untagged_hashes=["old_untagged_xyz"],
            blob_ages_days={"tagged_hash_abc": 0, "old_untagged_xyz": 100},
        )

        artifact_dir = test_settings.storage_path / "test/artifact"
        # Blob files use short hash (first 8 chars)
        blob_file = artifact_dir / "blobs/old_unta"
        assert blob_file.exists()

        with patch("magpie.ctl.get_settings", return_value=test_settings):
            result = cli_runner.invoke(cli, ["gc", "--reconcile-only"])

        assert result.exit_code == 0, f"Output: {result.output}"
        # Should show symlinks checked and fixed counts
        assert "Symlinks checked: 1" in result.output
        assert "Symlinks fixed: 1" in result.output
        # Blob should still exist
        assert blob_file.exists()
        # Symlink should exist pointing to blob
        symlink = artifact_dir / "latest"
        assert symlink.is_symlink()

    def test_gc_no_storage_path_error(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """GC fails if storage path doesn't exist."""
        # Don't create the storage path
        with patch("magpie.ctl.get_settings", return_value=test_settings):
            result = cli_runner.invoke(cli, ["gc"])

        assert result.exit_code != 0
        assert "does not exist" in result.output

    def test_gc_preserves_tagged_blobs(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """GC never deletes tagged blobs regardless of age."""
        test_settings.storage_path.mkdir(parents=True, exist_ok=True)
        create_artifact_with_blobs(
            test_settings.storage_path,
            "test/artifact",
            tagged_hashes={"latest": "old_tagged_hash", "stable": "old_tagged_hash"},
            untagged_hashes=[],
            blob_ages_days={"old_tagged_hash": 365},  # Very old but tagged
        )

        # Blob files use short hash (first 8 chars)
        blob_file = test_settings.storage_path / "test/artifact/blobs/old_tagg"
        assert blob_file.exists()

        with patch("magpie.ctl.get_settings", return_value=test_settings):
            result = cli_runner.invoke(cli, ["gc"])

        assert result.exit_code == 0
        assert "Deleted: 0 blob(s)" in result.output
        # File should still exist (it's tagged)
        assert blob_file.exists()

    def test_gc_preserves_tagged_blobs_with_at_prefix(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """GC correctly handles @-prefixed hash references in tags."""
        test_settings.storage_path.mkdir(parents=True, exist_ok=True)
        # Create artifact with @-prefixed tag references
        create_artifact_with_blobs(
            test_settings.storage_path,
            "test/artifact",
            tagged_hashes={"latest": "@abc12345", "stable": "@def67890"},
            untagged_hashes=["zzz99999"],
            blob_ages_days={"@abc12345": 365, "@def67890": 365, "zzz99999": 365},
        )

        # Blob files use short hash WITHOUT the @ prefix (first 8 chars after stripping @)
        tagged_blob1 = test_settings.storage_path / "test/artifact/blobs/abc12345"
        tagged_blob2 = test_settings.storage_path / "test/artifact/blobs/def67890"
        untagged_blob = test_settings.storage_path / "test/artifact/blobs/zzz99999"
        assert tagged_blob1.exists()
        assert tagged_blob2.exists()
        assert untagged_blob.exists()

        with patch("magpie.ctl.get_settings", return_value=test_settings):
            result = cli_runner.invoke(cli, ["gc"])

        assert result.exit_code == 0
        # Only the untagged blob should be deleted
        assert "Deleted: 1 blob(s)" in result.output
        # Tagged blobs should still exist (@ prefix was correctly stripped)
        assert tagged_blob1.exists()
        assert tagged_blob2.exists()
        # Untagged blob should be deleted
        assert not untagged_blob.exists()

    def test_gc_multiple_artifacts(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """GC processes multiple artifacts correctly."""
        test_settings.storage_path.mkdir(parents=True, exist_ok=True)

        # Create two artifacts
        create_artifact_with_blobs(
            test_settings.storage_path,
            "project1/app",
            tagged_hashes={"latest": "hash_a"},
            untagged_hashes=["old_hash_b"],
            blob_ages_days={"hash_a": 0, "old_hash_b": 100},
        )
        create_artifact_with_blobs(
            test_settings.storage_path,
            "project2/lib",
            tagged_hashes={"latest": "hash_c"},
            untagged_hashes=["old_hash_d"],
            blob_ages_days={"hash_c": 0, "old_hash_d": 100},
        )

        with patch("magpie.ctl.get_settings", return_value=test_settings):
            result = cli_runner.invoke(cli, ["gc"])

        assert result.exit_code == 0, f"Output: {result.output}"
        assert "Artifacts scanned: 2" in result.output
        assert "Deleted: 2 blob(s)" in result.output

    def test_gc_retention_days_flag_overrides_config(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """GC --retention-days flag overrides config retention period."""
        test_settings.storage_path.mkdir(parents=True, exist_ok=True)
        # Config has retention_days=30, but we'll override with flag
        create_artifact_with_blobs(
            test_settings.storage_path,
            "test/artifact",
            tagged_hashes={"latest": "tagged_hash_abc"},
            untagged_hashes=["untagged_hash_xyz"],
            blob_ages_days={
                "tagged_hash_abc": 0,
                "untagged_hash_xyz": 15,  # 15 days old
            },
        )

        blob_file = test_settings.storage_path / "test/artifact/blobs/untagged"
        assert blob_file.exists()

        # With --retention-days=10, the 15-day-old blob should be deleted
        with patch("magpie.ctl.get_settings", return_value=test_settings):
            result = cli_runner.invoke(cli, ["gc", "--retention-days", "10"])

        assert result.exit_code == 0, f"Output: {result.output}"
        assert "Deleted: 1 blob(s)" in result.output
        assert not blob_file.exists()

    def test_gc_retention_days_zero_deletes_all_untagged(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """GC --retention-days=0 deletes all untagged blobs immediately."""
        test_settings.storage_path.mkdir(parents=True, exist_ok=True)
        create_artifact_with_blobs(
            test_settings.storage_path,
            "test/artifact",
            tagged_hashes={"latest": "tagged_hash_abc"},
            untagged_hashes=["brand_new_hash_xyz"],
            blob_ages_days={
                "tagged_hash_abc": 0,
                "brand_new_hash_xyz": 0,  # Just uploaded
            },
        )

        blob_file = test_settings.storage_path / "test/artifact/blobs/brand_ne"
        assert blob_file.exists()

        # With --retention-days=0, even new blobs should be deleted
        with patch("magpie.ctl.get_settings", return_value=test_settings):
            result = cli_runner.invoke(cli, ["gc", "--retention-days", "0"])

        assert result.exit_code == 0, f"Output: {result.output}"
        assert "Deleted: 1 blob(s)" in result.output
        assert not blob_file.exists()

    def test_gc_retention_days_flag_preserves_young_blobs(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """GC --retention-days preserves blobs younger than specified days."""
        test_settings.storage_path.mkdir(parents=True, exist_ok=True)
        create_artifact_with_blobs(
            test_settings.storage_path,
            "test/artifact",
            tagged_hashes={"latest": "tagged_hash_abc"},
            untagged_hashes=["young_hash_xyz"],
            blob_ages_days={
                "tagged_hash_abc": 0,
                "young_hash_xyz": 5,  # 5 days old
            },
        )

        blob_file = test_settings.storage_path / "test/artifact/blobs/young_ha"
        assert blob_file.exists()

        # With --retention-days=7, the 5-day-old blob should be preserved
        with patch("magpie.ctl.get_settings", return_value=test_settings):
            result = cli_runner.invoke(cli, ["gc", "--retention-days", "7"])

        assert result.exit_code == 0, f"Output: {result.output}"
        assert "Deleted: 0 blob(s)" in result.output
        assert blob_file.exists()

    def test_gc_no_retention_days_flag_uses_config_default(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """GC without --retention-days flag uses config default."""
        test_settings.storage_path.mkdir(parents=True, exist_ok=True)
        # test_settings.retention_days = 30
        create_artifact_with_blobs(
            test_settings.storage_path,
            "test/artifact",
            tagged_hashes={"latest": "tagged_hash_abc"},
            untagged_hashes=["old_hash_xyz"],
            blob_ages_days={
                "tagged_hash_abc": 0,
                "old_hash_xyz": 50,  # 50 days old, older than config's 30
            },
        )

        blob_file = test_settings.storage_path / "test/artifact/blobs/old_hash"
        assert blob_file.exists()

        # Without --retention-days, should use config's 30 days
        with patch("magpie.ctl.get_settings", return_value=test_settings):
            result = cli_runner.invoke(cli, ["gc"])

        assert result.exit_code == 0, f"Output: {result.output}"
        assert "Deleted: 1 blob(s)" in result.output
        assert not blob_file.exists()

    def test_gc_retention_days_rejects_negative_values(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """GC --retention-days rejects negative values."""
        test_settings.storage_path.mkdir(parents=True, exist_ok=True)

        with patch("magpie.ctl.get_settings", return_value=test_settings):
            result = cli_runner.invoke(cli, ["gc", "--retention-days", "-1"])

        assert result.exit_code != 0
        assert "Invalid value" in result.output or "is not in the range" in result.output

    def test_gc_quiet_flag_accepted(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """GC --quiet flag is accepted and works."""
        test_settings.storage_path.mkdir(parents=True, exist_ok=True)
        create_artifact_with_blobs(
            test_settings.storage_path,
            "test/artifact",
            tagged_hashes={"latest": "tagged_hash_abc"},
            untagged_hashes=[],
            blob_ages_days={"tagged_hash_abc": 0},
        )

        with patch("magpie.ctl.get_settings", return_value=test_settings):
            result = cli_runner.invoke(cli, ["gc", "--quiet"])

        assert result.exit_code == 0, f"Output: {result.output}"
        # Summary should still be printed
        assert "GC Summary:" in result.output

    def test_gc_quiet_short_flag_accepted(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """GC -q short flag is accepted and works."""
        test_settings.storage_path.mkdir(parents=True, exist_ok=True)
        create_artifact_with_blobs(
            test_settings.storage_path,
            "test/artifact",
            tagged_hashes={"latest": "tagged_hash_abc"},
            untagged_hashes=[],
            blob_ages_days={"tagged_hash_abc": 0},
        )

        with patch("magpie.ctl.get_settings", return_value=test_settings):
            result = cli_runner.invoke(cli, ["gc", "-q"])

        assert result.exit_code == 0, f"Output: {result.output}"
        assert "GC Summary:" in result.output


class TestGCDirectoryCleanup:
    """Tests for GC directory cleanup after blob deletion."""

    def test_gc_removes_empty_blobs_dir(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """GC removes empty blobs/ directory after deleting last blob."""
        test_settings.storage_path.mkdir(parents=True, exist_ok=True)
        # Create artifact with only untagged blob that will be deleted
        create_artifact_with_blobs(
            test_settings.storage_path,
            "test/cleanup",
            tagged_hashes={},  # No tags
            untagged_hashes=["old_hash_xyz"],
            blob_ages_days={"old_hash_xyz": 100},
        )

        blobs_dir = test_settings.storage_path / "test/cleanup/blobs"
        assert blobs_dir.exists()

        with patch("magpie.ctl.get_settings", return_value=test_settings):
            result = cli_runner.invoke(cli, ["gc"])

        assert result.exit_code == 0, f"Output: {result.output}"
        assert "Deleted: 1 blob(s)" in result.output
        # blobs/ directory should be removed
        assert not blobs_dir.exists()

    def test_gc_removes_empty_metadata_dir(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """GC removes empty metadata/ directory after deleting last blob."""
        test_settings.storage_path.mkdir(parents=True, exist_ok=True)
        create_artifact_with_blobs(
            test_settings.storage_path,
            "test/cleanup",
            tagged_hashes={},
            untagged_hashes=["old_hash_xyz"],
            blob_ages_days={"old_hash_xyz": 100},
        )

        metadata_dir = test_settings.storage_path / "test/cleanup/metadata"
        assert metadata_dir.exists()

        with patch("magpie.ctl.get_settings", return_value=test_settings):
            result = cli_runner.invoke(cli, ["gc"])

        assert result.exit_code == 0
        # metadata/ directory should be removed
        assert not metadata_dir.exists()

    def test_gc_removes_manifest_with_no_tags(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """GC removes .magpie file when no tags remain."""
        test_settings.storage_path.mkdir(parents=True, exist_ok=True)
        create_artifact_with_blobs(
            test_settings.storage_path,
            "test/cleanup",
            tagged_hashes={},  # No tags
            untagged_hashes=["old_hash_xyz"],
            blob_ages_days={"old_hash_xyz": 100},
        )

        manifest_file = test_settings.storage_path / "test/cleanup/.magpie"
        assert manifest_file.exists()

        with patch("magpie.ctl.get_settings", return_value=test_settings):
            result = cli_runner.invoke(cli, ["gc"])

        assert result.exit_code == 0
        # .magpie should be removed when no tags remain
        assert not manifest_file.exists()

    def test_gc_preserves_manifest_with_tags(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """GC preserves .magpie file when tags still exist."""
        test_settings.storage_path.mkdir(parents=True, exist_ok=True)
        create_artifact_with_blobs(
            test_settings.storage_path,
            "test/cleanup",
            tagged_hashes={"latest": "tagged_hash_abc"},  # Has tags
            untagged_hashes=["old_hash_xyz"],
            blob_ages_days={"tagged_hash_abc": 0, "old_hash_xyz": 100},
        )

        manifest_file = test_settings.storage_path / "test/cleanup/.magpie"
        assert manifest_file.exists()

        with patch("magpie.ctl.get_settings", return_value=test_settings):
            result = cli_runner.invoke(cli, ["gc"])

        assert result.exit_code == 0
        # .magpie should be preserved because "latest" tag exists
        assert manifest_file.exists()

    def test_gc_removes_empty_artifact_dir(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """GC removes artifact directory when completely empty."""
        test_settings.storage_path.mkdir(parents=True, exist_ok=True)
        create_artifact_with_blobs(
            test_settings.storage_path,
            "test/cleanup",
            tagged_hashes={},
            untagged_hashes=["old_hash_xyz"],
            blob_ages_days={"old_hash_xyz": 100},
        )

        artifact_dir = test_settings.storage_path / "test/cleanup"
        assert artifact_dir.exists()

        with patch("magpie.ctl.get_settings", return_value=test_settings):
            result = cli_runner.invoke(cli, ["gc"])

        assert result.exit_code == 0
        # Artifact directory should be removed when empty
        assert not artifact_dir.exists()

    def test_gc_removes_empty_parent_dirs(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """GC removes empty parent directories up to storage root."""
        test_settings.storage_path.mkdir(parents=True, exist_ok=True)
        create_artifact_with_blobs(
            test_settings.storage_path,
            "deep/nested/path/artifact",
            tagged_hashes={},
            untagged_hashes=["old_hash_xyz"],
            blob_ages_days={"old_hash_xyz": 100},
        )

        parent_dir = test_settings.storage_path / "deep"
        assert parent_dir.exists()

        with patch("magpie.ctl.get_settings", return_value=test_settings):
            result = cli_runner.invoke(cli, ["gc"])

        assert result.exit_code == 0
        # All empty parent directories should be removed
        assert not parent_dir.exists()

    def test_gc_preserves_non_empty_parent(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """GC preserves parent directories that have other content."""
        test_settings.storage_path.mkdir(parents=True, exist_ok=True)
        # Create two artifacts under same parent
        create_artifact_with_blobs(
            test_settings.storage_path,
            "parent/artifact1",
            tagged_hashes={},
            untagged_hashes=["old_hash_xyz"],
            blob_ages_days={"old_hash_xyz": 100},
        )
        create_artifact_with_blobs(
            test_settings.storage_path,
            "parent/artifact2",
            tagged_hashes={"latest": "tagged_hash_abc"},
            untagged_hashes=[],
            blob_ages_days={"tagged_hash_abc": 0},
        )

        parent_dir = test_settings.storage_path / "parent"
        artifact2_dir = test_settings.storage_path / "parent/artifact2"

        with patch("magpie.ctl.get_settings", return_value=test_settings):
            result = cli_runner.invoke(cli, ["gc"])

        assert result.exit_code == 0
        # artifact1 removed but parent preserved for artifact2
        assert not (test_settings.storage_path / "parent/artifact1").exists()
        assert artifact2_dir.exists()
        assert parent_dir.exists()

    def test_gc_dry_run_shows_directories_to_remove(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """GC --dry-run shows directories that would be removed."""
        test_settings.storage_path.mkdir(parents=True, exist_ok=True)
        create_artifact_with_blobs(
            test_settings.storage_path,
            "test/cleanup",
            tagged_hashes={},
            untagged_hashes=["old_hash_xyz"],
            blob_ages_days={"old_hash_xyz": 100},
        )

        with patch("magpie.ctl.get_settings", return_value=test_settings):
            result = cli_runner.invoke(cli, ["gc", "--dry-run"])

        assert result.exit_code == 0
        assert "Would remove" in result.output

    def test_gc_dry_run_does_not_remove_directories(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """GC --dry-run does not actually remove directories."""
        test_settings.storage_path.mkdir(parents=True, exist_ok=True)
        create_artifact_with_blobs(
            test_settings.storage_path,
            "test/cleanup",
            tagged_hashes={},
            untagged_hashes=["old_hash_xyz"],
            blob_ages_days={"old_hash_xyz": 100},
        )

        artifact_dir = test_settings.storage_path / "test/cleanup"
        blobs_dir = artifact_dir / "blobs"
        manifest_file = artifact_dir / ".magpie"

        with patch("magpie.ctl.get_settings", return_value=test_settings):
            result = cli_runner.invoke(cli, ["gc", "--dry-run"])

        assert result.exit_code == 0
        # Nothing should be removed in dry run
        assert blobs_dir.exists()
        assert manifest_file.exists()
        assert artifact_dir.exists()

    def test_gc_reports_directory_cleanup_count(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """GC reports number of items removed in summary."""
        test_settings.storage_path.mkdir(parents=True, exist_ok=True)
        create_artifact_with_blobs(
            test_settings.storage_path,
            "test/cleanup",
            tagged_hashes={},
            untagged_hashes=["old_hash_xyz"],
            blob_ages_days={"old_hash_xyz": 100},
        )

        with patch("magpie.ctl.get_settings", return_value=test_settings):
            result = cli_runner.invoke(cli, ["gc"])

        assert result.exit_code == 0
        # Should report removed items (directories + manifest files)
        assert "Removed empty items:" in result.output


class TestGCJsonOutput:
    """Tests for GC --json-output flag."""

    def test_gc_json_output_returns_valid_json(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """GC --json-output returns valid JSON."""
        test_settings.storage_path.mkdir(parents=True, exist_ok=True)
        create_artifact_with_blobs(
            test_settings.storage_path,
            "test/artifact",
            tagged_hashes={"latest": "tagged_hash_abc"},
            untagged_hashes=[],
            blob_ages_days={"tagged_hash_abc": 0},
        )

        with patch("magpie.ctl.get_settings", return_value=test_settings):
            result = cli_runner.invoke(cli, ["gc", "--json-output"])

        assert result.exit_code == 0, f"Output: {result.output}"
        output = json.loads(result.stdout.strip())
        assert "dry_run" in output
        assert "blobs_deleted" in output
        assert "space_reclaimed_bytes" in output
        assert "artifacts_scanned" in output
        assert "errors" in output

    def test_gc_json_output_dry_run_shows_correct_flag(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """GC --json-output --dry-run shows dry_run=true."""
        test_settings.storage_path.mkdir(parents=True, exist_ok=True)
        create_artifact_with_blobs(
            test_settings.storage_path,
            "test/artifact",
            tagged_hashes={"latest": "tagged_hash_abc"},
            untagged_hashes=["old_untagged_xyz"],
            blob_ages_days={"tagged_hash_abc": 0, "old_untagged_xyz": 100},
        )

        with patch("magpie.ctl.get_settings", return_value=test_settings):
            result = cli_runner.invoke(cli, ["gc", "--json-output", "--dry-run"])

        assert result.exit_code == 0, f"Output: {result.output}"
        output = json.loads(result.stdout.strip())
        assert output["dry_run"] is True
        assert output["blobs_deleted"] == 1

    def test_gc_json_output_reports_deleted_blobs(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """GC --json-output reports blobs_deleted count."""
        test_settings.storage_path.mkdir(parents=True, exist_ok=True)
        create_artifact_with_blobs(
            test_settings.storage_path,
            "test/artifact",
            tagged_hashes={"latest": "tagged_hash_abc"},
            untagged_hashes=["old_untagged_xyz"],
            blob_ages_days={"tagged_hash_abc": 0, "old_untagged_xyz": 100},
        )

        with patch("magpie.ctl.get_settings", return_value=test_settings):
            result = cli_runner.invoke(cli, ["gc", "--json-output"])

        assert result.exit_code == 0, f"Output: {result.output}"
        output = json.loads(result.stdout.strip())
        assert output["dry_run"] is False
        assert output["blobs_deleted"] == 1
        assert output["space_reclaimed_bytes"] > 0

    def test_gc_json_output_storage_not_found_exits_with_error(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """GC --json-output with missing storage returns JSON error."""
        # Don't create the storage path
        with patch("magpie.ctl.get_settings", return_value=test_settings):
            result = cli_runner.invoke(cli, ["gc", "--json-output"])

        assert result.exit_code == 1
        output = json.loads(result.stdout.strip())
        assert "error" in output

    def test_gc_json_output_no_human_readable_text(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """GC --json-output doesn't include human-readable summary."""
        test_settings.storage_path.mkdir(parents=True, exist_ok=True)
        create_artifact_with_blobs(
            test_settings.storage_path,
            "test/artifact",
            tagged_hashes={"latest": "tagged_hash_abc"},
            untagged_hashes=[],
            blob_ages_days={"tagged_hash_abc": 0},
        )

        with patch("magpie.ctl.get_settings", return_value=test_settings):
            result = cli_runner.invoke(cli, ["gc", "--json-output"])

        assert result.exit_code == 0, f"Output: {result.output}"
        assert "GC Summary:" not in result.output
        assert "Artifacts scanned:" not in result.output

    @pytest.mark.parametrize(
        ("exception_type", "error_message"),
        [
            pytest.param(RuntimeError, "Simulated storage failure", id="runtime-error"),
            pytest.param(OSError, "Permission denied", id="os-error"),
        ],
    )
    def test_gc_json_output_exception_returns_json_error(
        self,
        cli_runner: CliRunner,
        test_settings: MagpieSettings,
        exception_type: type[Exception],
        error_message: str,
    ) -> None:
        """GC --json-output returns JSON error when run_gc raises exception.

        This tests the intentional broad exception handler at gc.py:141-150.
        When called with --json-output by the server's GC endpoint, we must
        always output valid JSON so the server can parse the error. The broad
        exception catch is deliberate - see issue #209 for context.
        """
        test_settings.storage_path.mkdir(parents=True, exist_ok=True)
        create_artifact_with_blobs(
            test_settings.storage_path,
            "test/artifact",
            tagged_hashes={"latest": "tagged_hash_abc"},
            untagged_hashes=[],
            blob_ages_days={"tagged_hash_abc": 0},
        )

        # Mock run_gc to raise an exception
        with patch("magpie.ctl.get_settings", return_value=test_settings):
            with patch(
                "magpie.ctl.commands.gc.run_gc",
                side_effect=exception_type(error_message),
            ):
                result = cli_runner.invoke(cli, ["gc", "--json-output"])

        # Should exit with error code 1
        assert result.exit_code == 1, f"Output: {result.output}"

        # Output should be valid JSON with error message
        output = json.loads(result.stdout.strip())
        assert "error" in output
        assert error_message in output["error"]


class TestFlushTagCommand:
    """Tests for flush-tag command."""

    def test_flush_tag_basic(self, cli_runner: CliRunner, test_settings: MagpieSettings) -> None:
        """flush-tag removes tag from artifacts."""
        test_settings.storage_path.mkdir(parents=True, exist_ok=True)
        create_artifact_with_blobs(
            test_settings.storage_path,
            "test/artifact",
            tagged_hashes={"release": "hash_abc12345"},
            untagged_hashes=[],
            blob_ages_days={"hash_abc12345": 0},
        )

        with patch("magpie.ctl.get_settings", return_value=test_settings):
            result = cli_runner.invoke(cli, ["flush-tag", "release"])

        assert result.exit_code == 0, f"Output: {result.output}"
        assert "Removed tag 'release' from 1 artifact(s)" in result.output

    def test_flush_tag_dry_run(self, cli_runner: CliRunner, test_settings: MagpieSettings) -> None:
        """flush-tag --dry-run shows preview without modifying."""
        test_settings.storage_path.mkdir(parents=True, exist_ok=True)
        create_artifact_with_blobs(
            test_settings.storage_path,
            "test/artifact",
            tagged_hashes={"release": "hash_abc12345"},
            untagged_hashes=[],
            blob_ages_days={"hash_abc12345": 0},
        )

        with patch("magpie.ctl.get_settings", return_value=test_settings):
            result = cli_runner.invoke(cli, ["flush-tag", "release", "--dry-run"])

        assert result.exit_code == 0, f"Output: {result.output}"
        assert "Would remove tag 'release' from 1 artifact(s)" in result.output

        # Verify tag still exists
        manifest_file = test_settings.storage_path / "test/artifact/.magpie"
        manifest_content = json.loads(manifest_file.read_text())
        assert "release" in manifest_content["tags"]

    def test_flush_tag_json_output(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """flush-tag --json-output returns valid JSON."""
        test_settings.storage_path.mkdir(parents=True, exist_ok=True)
        create_artifact_with_blobs(
            test_settings.storage_path,
            "test/artifact",
            tagged_hashes={"release": "hash_abc12345"},
            untagged_hashes=[],
            blob_ages_days={"hash_abc12345": 0},
        )

        with patch("magpie.ctl.get_settings", return_value=test_settings):
            result = cli_runner.invoke(cli, ["flush-tag", "release", "--json-output"])

        assert result.exit_code == 0, f"Output: {result.output}"
        output = json.loads(result.stdout.strip())
        assert output["tag_name"] == "release"
        assert output["dry_run"] is False
        assert output["count"] == 1
        assert "test/artifact" in output["affected_artifacts"]

    def test_flush_tag_json_output_dry_run(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """flush-tag --json-output --dry-run shows correct flags."""
        test_settings.storage_path.mkdir(parents=True, exist_ok=True)
        create_artifact_with_blobs(
            test_settings.storage_path,
            "test/artifact",
            tagged_hashes={"release": "hash_abc12345"},
            untagged_hashes=[],
            blob_ages_days={"hash_abc12345": 0},
        )

        with patch("magpie.ctl.get_settings", return_value=test_settings):
            result = cli_runner.invoke(cli, ["flush-tag", "release", "--json-output", "--dry-run"])

        assert result.exit_code == 0, f"Output: {result.output}"
        output = json.loads(result.stdout.strip())
        assert output["tag_name"] == "release"
        assert output["dry_run"] is True
        assert output["count"] == 1

    def test_flush_tag_nonexistent_tag(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """flush-tag with nonexistent tag returns zero count."""
        test_settings.storage_path.mkdir(parents=True, exist_ok=True)
        create_artifact_with_blobs(
            test_settings.storage_path,
            "test/artifact",
            tagged_hashes={"latest": "hash_abc12345"},
            untagged_hashes=[],
            blob_ages_days={"hash_abc12345": 0},
        )

        with patch("magpie.ctl.get_settings", return_value=test_settings):
            result = cli_runner.invoke(cli, ["flush-tag", "nonexistent"])

        assert result.exit_code == 0, f"Output: {result.output}"
        assert "Removed tag 'nonexistent' from 0 artifact(s)" in result.output

    def test_flush_tag_storage_not_found(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """flush-tag fails if storage path doesn't exist."""
        # Don't create storage path
        with patch("magpie.ctl.get_settings", return_value=test_settings):
            result = cli_runner.invoke(cli, ["flush-tag", "release"])

        assert result.exit_code != 0
        assert "does not exist" in result.output

    def test_flush_tag_json_output_storage_not_found(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """flush-tag --json-output with missing storage returns JSON error."""
        # Don't create storage path
        with patch("magpie.ctl.get_settings", return_value=test_settings):
            result = cli_runner.invoke(cli, ["flush-tag", "release", "--json-output"])

        assert result.exit_code == 1
        output = json.loads(result.stdout.strip())
        assert "error" in output
