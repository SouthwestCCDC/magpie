"""Unit tests for CTL init and gc commands."""

from __future__ import annotations

import json
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

    # Create manifest
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
        # Create blob file
        blob_file = blobs_dir / blob_hash
        blob_file.write_bytes(b"test content for " + blob_hash.encode())

        # Create metadata with upload timestamp
        age_days = blob_ages_days.get(blob_hash, 0)
        upload_time = datetime.now(timezone.utc) - timedelta(days=age_days)
        metadata = {
            "hash": blob_hash,
            "uploaded_by": "test",
            "uploaded_at": upload_time.isoformat(),
            "source_uri": None,
        }
        metadata_file = metadata_dir / f"{blob_hash}.json"
        metadata_file.write_text(json.dumps(metadata), encoding="utf-8")


class TestInitCommand:
    """Tests for init command."""

    def test_init_creates_directories(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """Init creates storage and temp directories."""
        with patch("magpie.ctl.commands.init.CTLContext") as mock_ctx_class:
            # Setup mock
            mock_ctx = mock_ctx_class.return_value
            mock_ctx.settings = test_settings
            mock_ctx.debug = False

            with patch("magpie.ctl.get_settings", return_value=test_settings):
                result = cli_runner.invoke(cli, ["init"])

        assert result.exit_code == 0, f"Output: {result.output}"
        assert test_settings.storage_path.exists()
        assert test_settings.temp_path.exists()

    def test_init_creates_database(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """Init creates SQLite database."""
        with patch("magpie.ctl.get_settings", return_value=test_settings):
            result = cli_runner.invoke(cli, ["init"])

        assert result.exit_code == 0, f"Output: {result.output}"
        assert test_settings.database_path.exists()
        assert "Database initialized" in result.output

    def test_init_creates_admin_token(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """Init creates admin token on first run."""
        with patch("magpie.ctl.get_settings", return_value=test_settings):
            result = cli_runner.invoke(cli, ["init"])

        assert result.exit_code == 0, f"Output: {result.output}"
        assert "ADMIN TOKEN" in result.output
        assert "mgp_ADMIN_" in result.output

    def test_init_token_already_exists(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """Init reports existing token when run twice."""
        with patch("magpie.ctl.get_settings", return_value=test_settings):
            # First init creates token
            result1 = cli_runner.invoke(cli, ["init"])
            assert result1.exit_code == 0

            # Second init should report token exists
            result2 = cli_runner.invoke(cli, ["init"])
            assert result2.exit_code == 0
            assert "already exists" in result2.output

    def test_init_reset_admin_token_regenerates(
        self, cli_runner: CliRunner, test_settings: MagpieSettings
    ) -> None:
        """Init --reset-admin-token regenerates token."""
        with patch("magpie.ctl.get_settings", return_value=test_settings):
            # First init creates token
            result1 = cli_runner.invoke(cli, ["init"])
            assert result1.exit_code == 0
            # Extract first token
            first_token = None
            for line in result1.output.split("\n"):
                if line.startswith("mgp_ADMIN_"):
                    first_token = line.strip()
                    break

            # Reset token
            result2 = cli_runner.invoke(cli, ["init", "--reset-admin-token"])
            assert result2.exit_code == 0
            assert "NEW ADMIN TOKEN" in result2.output
            assert "Revoked existing" in result2.output

            # Extract second token
            second_token = None
            for line in result2.output.split("\n"):
                if line.startswith("mgp_ADMIN_"):
                    second_token = line.strip()
                    break

            # Tokens should be different
            assert first_token is not None
            assert second_token is not None
            assert first_token != second_token


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
        assert "Untagged blobs: 1" in result.output

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

        blob_file = test_settings.storage_path / "test/artifact/blobs/untagged_hash_xyz"
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

        blob_file = test_settings.storage_path / "test/artifact/blobs/old_untagged_xyz"
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

        blob_file = test_settings.storage_path / "test/artifact/blobs/young_untagged_xyz"
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
        blob_file = artifact_dir / "blobs/old_untagged_xyz"
        assert blob_file.exists()

        with patch("magpie.ctl.get_settings", return_value=test_settings):
            result = cli_runner.invoke(cli, ["gc", "--reconcile-only"])

        assert result.exit_code == 0, f"Output: {result.output}"
        assert "Symlinks reconciled: 1 artifact(s)" in result.output
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

        blob_file = test_settings.storage_path / "test/artifact/blobs/old_tagged_hash"
        assert blob_file.exists()

        with patch("magpie.ctl.get_settings", return_value=test_settings):
            result = cli_runner.invoke(cli, ["gc"])

        assert result.exit_code == 0
        assert "Deleted: 0 blob(s)" in result.output
        # File should still exist (it's tagged)
        assert blob_file.exists()

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
