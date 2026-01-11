"""Unit tests for cleanup utilities."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from magpie.storage.cleanup import cleanup_empty_artifact_dir


@pytest.fixture
def artifact_dir(tmp_path: Path) -> Path:
    """Create a temporary artifact directory."""
    return tmp_path / "test-artifact"


class TestCleanupEmptyArtifactDir:
    """Tests for cleanup_empty_artifact_dir function."""

    def test_cleanup_empty_blobs_directory(self, artifact_dir: Path) -> None:
        """Cleanup removes empty blobs/ directory."""
        blobs_dir = artifact_dir / "blobs"
        blobs_dir.mkdir(parents=True)

        assert blobs_dir.exists()

        result = cleanup_empty_artifact_dir(artifact_dir)

        assert result is True
        assert not blobs_dir.exists()

    def test_cleanup_empty_metadata_directory(self, artifact_dir: Path) -> None:
        """Cleanup removes empty metadata/ directory."""
        metadata_dir = artifact_dir / "metadata"
        metadata_dir.mkdir(parents=True)

        assert metadata_dir.exists()

        result = cleanup_empty_artifact_dir(artifact_dir)

        assert result is True
        assert not metadata_dir.exists()

    def test_cleanup_manifest_with_no_tags(self, artifact_dir: Path) -> None:
        """Cleanup removes .magpie manifest when it has no tags."""
        artifact_dir.mkdir(parents=True)
        manifest_file = artifact_dir / ".magpie"

        manifest_data = {"version": 1, "tags": {}}
        manifest_file.write_text(json.dumps(manifest_data), encoding="utf-8")

        assert manifest_file.exists()

        result = cleanup_empty_artifact_dir(artifact_dir)

        assert result is True
        assert not manifest_file.exists()

    def test_cleanup_preserves_manifest_with_tags(self, artifact_dir: Path) -> None:
        """Cleanup preserves .magpie manifest when it has tags."""
        artifact_dir.mkdir(parents=True)
        manifest_file = artifact_dir / ".magpie"

        manifest_data = {"version": 1, "tags": {"latest": "@abc12345"}}
        manifest_file.write_text(json.dumps(manifest_data), encoding="utf-8")

        assert manifest_file.exists()

        result = cleanup_empty_artifact_dir(artifact_dir)

        # Should not clean up because manifest has tags
        assert result is False
        assert manifest_file.exists()

    def test_cleanup_removes_empty_artifact_directory(self, artifact_dir: Path) -> None:
        """Cleanup removes artifact directory when completely empty."""
        artifact_dir.mkdir(parents=True)

        assert artifact_dir.exists()

        result = cleanup_empty_artifact_dir(artifact_dir)

        assert result is True
        assert not artifact_dir.exists()

    def test_cleanup_preserves_non_empty_blobs_directory(self, artifact_dir: Path) -> None:
        """Cleanup preserves blobs/ directory with files."""
        blobs_dir = artifact_dir / "blobs"
        blobs_dir.mkdir(parents=True)

        blob_file = blobs_dir / "abc12345"
        blob_file.write_bytes(b"test content")

        assert blobs_dir.exists()
        assert blob_file.exists()

        result = cleanup_empty_artifact_dir(artifact_dir)

        # Should not clean up because blobs directory has files
        assert result is False
        assert blobs_dir.exists()
        assert blob_file.exists()

    def test_cleanup_preserves_non_empty_metadata_directory(self, artifact_dir: Path) -> None:
        """Cleanup preserves metadata/ directory with files."""
        metadata_dir = artifact_dir / "metadata"
        metadata_dir.mkdir(parents=True)

        metadata_file = metadata_dir / "abc12345.json"
        metadata_file.write_text("{}", encoding="utf-8")

        assert metadata_dir.exists()
        assert metadata_file.exists()

        result = cleanup_empty_artifact_dir(artifact_dir)

        # Should not clean up because metadata directory has files
        assert result is False
        assert metadata_dir.exists()
        assert metadata_file.exists()

    def test_cleanup_full_sequence(self, artifact_dir: Path) -> None:
        """Cleanup removes all empty components in sequence."""
        artifact_dir.mkdir(parents=True)

        # Create empty blobs and metadata directories
        blobs_dir = artifact_dir / "blobs"
        blobs_dir.mkdir()

        metadata_dir = artifact_dir / "metadata"
        metadata_dir.mkdir()

        # Create manifest with no tags
        manifest_file = artifact_dir / ".magpie"
        manifest_data = {"version": 1, "tags": {}}
        manifest_file.write_text(json.dumps(manifest_data), encoding="utf-8")

        assert artifact_dir.exists()
        assert blobs_dir.exists()
        assert metadata_dir.exists()
        assert manifest_file.exists()

        result = cleanup_empty_artifact_dir(artifact_dir)

        assert result is True
        assert not artifact_dir.exists()
        assert not blobs_dir.exists()
        assert not metadata_dir.exists()
        assert not manifest_file.exists()

    def test_cleanup_dry_run_does_not_delete(self, artifact_dir: Path) -> None:
        """Cleanup with dry_run=True does not actually delete."""
        artifact_dir.mkdir(parents=True)

        blobs_dir = artifact_dir / "blobs"
        blobs_dir.mkdir()

        assert artifact_dir.exists()
        assert blobs_dir.exists()

        result = cleanup_empty_artifact_dir(artifact_dir, dry_run=True)

        # Should return True (would clean up) but not actually delete
        assert result is True
        assert artifact_dir.exists()
        assert blobs_dir.exists()

    def test_cleanup_nonexistent_directory_returns_false(self, artifact_dir: Path) -> None:
        """Cleanup of nonexistent directory returns False."""
        assert not artifact_dir.exists()

        result = cleanup_empty_artifact_dir(artifact_dir)

        assert result is False

    def test_cleanup_removes_empty_parent_directories(self, tmp_path: Path) -> None:
        """Cleanup recursively removes empty parent directories."""
        # Create nested structure: parent/child/artifact
        parent_dir = tmp_path / "parent"
        child_dir = parent_dir / "child"
        artifact_dir = child_dir / "artifact"
        artifact_dir.mkdir(parents=True)

        # Make artifact directory empty
        assert artifact_dir.exists()
        assert child_dir.exists()
        assert parent_dir.exists()

        result = cleanup_empty_artifact_dir(artifact_dir)

        assert result is True
        # All directories should be removed up to tmp_path (storage root heuristic)
        assert not artifact_dir.exists()

        # Parent directories should also be removed if empty
        # (unless they hit the storage root heuristic)

    def test_cleanup_preserves_parent_with_siblings(self, tmp_path: Path) -> None:
        """Cleanup preserves parent directory when it has other children."""
        parent_dir = tmp_path / "parent"
        artifact_dir = parent_dir / "artifact"
        sibling_dir = parent_dir / "sibling"

        artifact_dir.mkdir(parents=True)
        sibling_dir.mkdir(parents=True)

        # Create a file in sibling to make parent non-empty
        sibling_file = sibling_dir / "file.txt"
        sibling_file.write_text("content")

        assert artifact_dir.exists()
        assert parent_dir.exists()
        assert sibling_dir.exists()

        result = cleanup_empty_artifact_dir(artifact_dir)

        assert result is True
        assert not artifact_dir.exists()
        # Parent should remain because sibling exists
        assert parent_dir.exists()
        assert sibling_dir.exists()

    def test_cleanup_handles_corrupt_manifest(self, artifact_dir: Path) -> None:
        """Cleanup preserves corrupt manifest file (safe default)."""
        artifact_dir.mkdir(parents=True)
        manifest_file = artifact_dir / ".magpie"

        # Write invalid JSON
        manifest_file.write_text("not valid json", encoding="utf-8")

        assert manifest_file.exists()

        result = cleanup_empty_artifact_dir(artifact_dir)

        # Should not clean up because we can't safely determine if manifest has tags
        assert result is False
        assert manifest_file.exists()
