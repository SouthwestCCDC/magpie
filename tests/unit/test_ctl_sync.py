"""Unit tests for magpie-ctl sync command."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from magpie.config import MagpieSettings
from magpie.ctl import cli as ctl_cli
from magpie.ctl.commands.sync import (
    _build_s3_path,
    _find_sync_tool,
    _get_blobs_for_manifest,
    _get_metadata_for_manifest,
    _has_existing_data,
    _iter_tagged_artifacts,
    _verify_restored_data,
)
from magpie.storage.manifest import Manifest, write_manifest


class TestFindSyncTool:
    """Tests for _find_sync_tool function."""

    def test_prefers_rclone(self) -> None:
        """Should prefer rclone over aws cli when both are available."""
        with patch("shutil.which") as mock_which:
            mock_which.side_effect = lambda cmd: cmd in ["rclone", "aws"]
            result = _find_sync_tool()
            assert result == "rclone"

    def test_falls_back_to_aws(self) -> None:
        """Should use aws cli when rclone is not available."""
        with patch("shutil.which") as mock_which:
            mock_which.side_effect = lambda cmd: cmd == "aws"
            result = _find_sync_tool()
            assert result == "aws"

    def test_returns_none_when_neither_available(self) -> None:
        """Should return None when neither tool is available."""
        with patch("shutil.which", return_value=None):
            result = _find_sync_tool()
            assert result is None


class TestBuildS3Path:
    """Tests for _build_s3_path function."""

    def test_basic_path(self) -> None:
        """Should build basic S3 path without prefix."""
        result = _build_s3_path("my-bucket", "", "path/to/file")
        assert result == "s3://my-bucket/path/to/file"

    def test_with_prefix(self) -> None:
        """Should include prefix in S3 path."""
        result = _build_s3_path("my-bucket", "backup", "path/to/file")
        assert result == "s3://my-bucket/backup/path/to/file"

    def test_strips_slashes(self) -> None:
        """Should strip leading/trailing slashes from prefix and path."""
        result = _build_s3_path("my-bucket", "/backup/", "/path/to/file/")
        assert result == "s3://my-bucket/backup/path/to/file"

    def test_empty_relative_path(self) -> None:
        """Should handle empty relative path."""
        result = _build_s3_path("my-bucket", "backup", "")
        assert result == "s3://my-bucket/backup"


class TestIterTaggedArtifacts:
    """Tests for _iter_tagged_artifacts function."""

    def test_finds_tagged_artifacts(self, tmp_path: Path) -> None:
        """Should yield artifacts that have tags."""
        # Create artifact with tags
        artifact_dir = tmp_path / "project" / "artifact"
        artifact_dir.mkdir(parents=True)
        manifest = Manifest(tags={"latest": "@abc12345"})
        write_manifest(artifact_dir, manifest)

        results = list(_iter_tagged_artifacts(tmp_path))
        assert len(results) == 1
        assert results[0][0] == artifact_dir
        assert results[0][1].tags == {"latest": "@abc12345"}

    def test_skips_untagged_artifacts(self, tmp_path: Path) -> None:
        """Should skip artifacts without tags."""
        # Create artifact without tags
        artifact_dir = tmp_path / "project" / "artifact"
        artifact_dir.mkdir(parents=True)
        manifest = Manifest(tags={})
        write_manifest(artifact_dir, manifest)

        results = list(_iter_tagged_artifacts(tmp_path))
        assert len(results) == 0

    def test_handles_nested_artifacts(self, tmp_path: Path) -> None:
        """Should find artifacts at different nesting levels."""
        # Create multiple artifacts
        for path in ["a", "b/c", "d/e/f"]:
            artifact_dir = tmp_path / path
            artifact_dir.mkdir(parents=True)
            manifest = Manifest(tags={"v1": "@12345678"})
            write_manifest(artifact_dir, manifest)

        results = list(_iter_tagged_artifacts(tmp_path))
        assert len(results) == 3


class TestGetBlobsForManifest:
    """Tests for _get_blobs_for_manifest function."""

    def test_gets_referenced_blobs(self, tmp_path: Path) -> None:
        """Should return blob paths for tags in manifest."""
        artifact_dir = tmp_path / "artifact"
        blobs_dir = artifact_dir / "blobs"
        blobs_dir.mkdir(parents=True)

        # Create blob files
        (blobs_dir / "abc12345").write_bytes(b"content1")
        (blobs_dir / "def67890").write_bytes(b"content2")

        manifest = Manifest(tags={"latest": "@abc12345", "v1": "@def67890"})

        blobs = _get_blobs_for_manifest(artifact_dir, manifest)
        assert len(blobs) == 2
        blob_names = {b.name for b in blobs}
        assert blob_names == {"abc12345", "def67890"}

    def test_skips_missing_blobs(self, tmp_path: Path) -> None:
        """Should skip blobs that don't exist."""
        artifact_dir = tmp_path / "artifact"
        blobs_dir = artifact_dir / "blobs"
        blobs_dir.mkdir(parents=True)

        # Only create one blob
        (blobs_dir / "abc12345").write_bytes(b"content")

        manifest = Manifest(tags={"latest": "@abc12345", "missing": "@notfound"})

        blobs = _get_blobs_for_manifest(artifact_dir, manifest)
        assert len(blobs) == 1
        assert blobs[0].name == "abc12345"

    def test_handles_no_blobs_dir(self, tmp_path: Path) -> None:
        """Should return empty list when blobs dir doesn't exist."""
        artifact_dir = tmp_path / "artifact"
        artifact_dir.mkdir(parents=True)

        manifest = Manifest(tags={"latest": "@abc12345"})

        blobs = _get_blobs_for_manifest(artifact_dir, manifest)
        assert blobs == []


class TestGetMetadataForManifest:
    """Tests for _get_metadata_for_manifest function."""

    def test_gets_referenced_metadata(self, tmp_path: Path) -> None:
        """Should return metadata paths for tags in manifest."""
        artifact_dir = tmp_path / "artifact"
        metadata_dir = artifact_dir / "metadata"
        metadata_dir.mkdir(parents=True)

        # Create metadata files
        (metadata_dir / "abc12345.json").write_text('{"key": "value1"}')
        (metadata_dir / "def67890.json").write_text('{"key": "value2"}')

        manifest = Manifest(tags={"latest": "@abc12345", "v1": "@def67890"})

        metadata = _get_metadata_for_manifest(artifact_dir, manifest)
        assert len(metadata) == 2
        metadata_names = {m.name for m in metadata}
        assert metadata_names == {"abc12345.json", "def67890.json"}

    def test_handles_no_metadata_dir(self, tmp_path: Path) -> None:
        """Should return empty list when metadata dir doesn't exist."""
        artifact_dir = tmp_path / "artifact"
        artifact_dir.mkdir(parents=True)

        manifest = Manifest(tags={"latest": "@abc12345"})

        metadata = _get_metadata_for_manifest(artifact_dir, manifest)
        assert metadata == []


class TestHasExistingData:
    """Tests for _has_existing_data function."""

    def test_returns_false_for_missing_path(self, tmp_path: Path) -> None:
        """Should return False when storage path doesn't exist."""
        missing_path = tmp_path / "nonexistent"
        assert _has_existing_data(missing_path) is False

    def test_returns_false_for_empty_storage(self, tmp_path: Path) -> None:
        """Should return False when no manifests exist."""
        assert _has_existing_data(tmp_path) is False

    def test_returns_true_when_manifests_exist(self, tmp_path: Path) -> None:
        """Should return True when manifests exist."""
        artifact_dir = tmp_path / "artifact"
        artifact_dir.mkdir(parents=True)
        write_manifest(artifact_dir, Manifest())
        assert _has_existing_data(tmp_path) is True


class TestVerifyRestoredData:
    """Tests for _verify_restored_data function."""

    def test_verifies_valid_data(self, tmp_path: Path) -> None:
        """Should verify artifacts and their blobs."""
        artifact_dir = tmp_path / "project" / "artifact"
        blobs_dir = artifact_dir / "blobs"
        blobs_dir.mkdir(parents=True)

        # Create blob
        (blobs_dir / "abc12345").write_bytes(b"content")

        # Create manifest referencing the blob
        manifest = Manifest(tags={"latest": "@abc12345"})
        write_manifest(artifact_dir, manifest)

        artifacts, blobs, errors = _verify_restored_data(tmp_path)
        assert artifacts == 1
        assert blobs == 1
        assert errors == []

    def test_reports_missing_blobs(self, tmp_path: Path) -> None:
        """Should report missing blobs as errors."""
        artifact_dir = tmp_path / "artifact"
        artifact_dir.mkdir(parents=True)

        # Create manifest referencing non-existent blob
        manifest = Manifest(tags={"latest": "@abc12345"})
        write_manifest(artifact_dir, manifest)

        artifacts, blobs, errors = _verify_restored_data(tmp_path)
        assert artifacts == 1
        assert blobs == 0
        assert len(errors) == 1
        assert "Missing blob" in errors[0]

    def test_reports_invalid_manifests(self, tmp_path: Path) -> None:
        """Should report invalid manifests as errors."""
        artifact_dir = tmp_path / "artifact"
        artifact_dir.mkdir(parents=True)

        # Create invalid manifest
        (artifact_dir / ".magpie").write_text("invalid json{")

        artifacts, blobs, errors = _verify_restored_data(tmp_path)
        assert artifacts == 0
        assert len(errors) == 1
        assert "Invalid manifest" in errors[0]


class TestSyncToS3Command:
    """Integration tests for sync to-s3 command."""

    @pytest.fixture
    def cli_runner(self) -> CliRunner:
        """Create Click CLI test runner."""
        return CliRunner()

    @pytest.fixture
    def mock_settings(self, tmp_path: Path) -> MagpieSettings:
        """Create mock settings with S3 config."""
        return MagpieSettings(
            storage_path=tmp_path,
            s3_bucket="test-bucket",
            s3_prefix="backup",
        )

    def test_requires_s3_bucket(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        """Should fail when S3 bucket is not configured."""
        with patch("magpie.ctl.commands.sync.CTLContext") as mock_ctx_class:
            mock_ctx = MagicMock()
            mock_ctx.settings = MagpieSettings(storage_path=tmp_path, s3_bucket=None)
            mock_ctx.debug = False
            mock_ctx_class.return_value = mock_ctx

            result = cli_runner.invoke(ctl_cli, ["sync", "to-s3"])

            assert result.exit_code != 0
            assert "MAGPIE_S3_BUCKET" in result.output

    def test_requires_sync_tool(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        """Should fail when no sync tool is available."""
        # Create storage path
        tmp_path.mkdir(parents=True, exist_ok=True)

        settings = MagpieSettings(storage_path=tmp_path, s3_bucket="test-bucket", s3_prefix="")

        with patch("magpie.ctl.commands.sync.shutil.which", return_value=None):
            with patch("magpie.ctl.get_settings", return_value=settings):
                result = cli_runner.invoke(
                    ctl_cli,
                    ["sync", "to-s3"],
                )

                # Check for the error message about missing tools
                assert "rclone" in result.output.lower() or "aws" in result.output.lower()

    def test_dry_run_shows_preview(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        """Should show what would be synced in dry-run mode."""
        # Create artifact with tag
        artifact_dir = tmp_path / "test" / "artifact"
        blobs_dir = artifact_dir / "blobs"
        blobs_dir.mkdir(parents=True)
        (blobs_dir / "abc12345").write_bytes(b"test content")
        manifest = Manifest(tags={"latest": "@abc12345"})
        write_manifest(artifact_dir, manifest)

        settings = MagpieSettings(storage_path=tmp_path, s3_bucket="test-bucket", s3_prefix="")

        with patch("magpie.ctl.commands.sync.shutil.which", return_value="/usr/bin/rclone"):
            with patch("magpie.ctl.commands.sync.subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
                with patch("magpie.ctl.get_settings", return_value=settings):
                    result = cli_runner.invoke(
                        ctl_cli,
                        ["sync", "to-s3", "--dry-run"],
                    )

                    assert result.exit_code == 0, f"Output: {result.output}"
                    assert "Preview" in result.output or "dry run" in result.output.lower()


class TestSyncFromS3Command:
    """Integration tests for sync from-s3 command."""

    @pytest.fixture
    def cli_runner(self) -> CliRunner:
        """Create Click CLI test runner."""
        return CliRunner()

    def test_requires_s3_bucket(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        """Should fail when S3 bucket is not configured."""
        result = cli_runner.invoke(
            ctl_cli,
            ["sync", "from-s3"],
            env={"MAGPIE_STORAGE_PATH": str(tmp_path)},
        )

        assert result.exit_code != 0
        assert "MAGPIE_S3_BUCKET" in result.output

    def test_refuses_restore_with_existing_data(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        """Should refuse to restore when data already exists."""
        # Create existing data
        artifact_dir = tmp_path / "existing" / "artifact"
        artifact_dir.mkdir(parents=True)
        write_manifest(artifact_dir, Manifest(tags={"v1": "@12345678"}))

        settings = MagpieSettings(storage_path=tmp_path, s3_bucket="test-bucket", s3_prefix="")

        with patch("magpie.ctl.commands.sync.shutil.which", return_value="/usr/bin/rclone"):
            with patch("magpie.ctl.get_settings", return_value=settings):
                result = cli_runner.invoke(
                    ctl_cli,
                    ["sync", "from-s3"],
                )

                assert result.exit_code != 0
                assert "already contains data" in result.output or "--force" in result.output

    def test_allows_restore_with_force(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        """Should allow restore with --force when data exists."""
        # Create existing data
        artifact_dir = tmp_path / "existing" / "artifact"
        artifact_dir.mkdir(parents=True)
        write_manifest(artifact_dir, Manifest(tags={"v1": "@12345678"}))

        settings = MagpieSettings(storage_path=tmp_path, s3_bucket="test-bucket", s3_prefix="")

        with patch("magpie.ctl.commands.sync.shutil.which", return_value="/usr/bin/rclone"):
            with patch("magpie.ctl.commands.sync.subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
                with patch("magpie.ctl.get_settings", return_value=settings):
                    result = cli_runner.invoke(
                        ctl_cli,
                        ["sync", "from-s3", "--force", "--skip-verify"],
                    )

                    # Should not fail due to existing data
                    assert "already contains data" not in result.output


class TestSyncJsonOutput:
    """Tests for JSON output mode."""

    @pytest.fixture
    def cli_runner(self) -> CliRunner:
        """Create Click CLI test runner."""
        return CliRunner()

    def test_to_s3_json_output_no_artifacts(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        """Should output valid JSON when no artifacts to sync."""
        settings = MagpieSettings(storage_path=tmp_path, s3_bucket="test-bucket", s3_prefix="")

        with patch("magpie.ctl.commands.sync.shutil.which", return_value="/usr/bin/rclone"):
            with patch("magpie.ctl.get_settings", return_value=settings):
                result = cli_runner.invoke(
                    ctl_cli,
                    ["--format", "json", "sync", "to-s3"],
                )

                assert result.exit_code == 0, f"Output: {result.output}"
                data = json.loads(result.output)
                assert data["status"] == "ok"
                assert data["data"]["artifacts_found"] == 0
