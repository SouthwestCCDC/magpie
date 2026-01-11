"""Unit tests for cleanup utilities."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from magpie.storage.cleanup import (
    CleanupStats,
    cleanup_artifact_directories,
    is_empty_directory,
)


@pytest.fixture
def storage_root(tmp_path: Path) -> Path:
    """Create a temporary storage root directory."""
    root = tmp_path / "storage"
    root.mkdir()
    return root


def create_artifact_structure(
    storage_root: Path,
    artifact_path: str,
    blobs: list[str] | None = None,
    metadata: list[str] | None = None,
    tags: dict[str, str] | None = None,
) -> Path:
    """Create an artifact directory structure for testing.

    Args:
        storage_root: Base storage directory.
        artifact_path: Relative path to artifact.
        blobs: List of blob hashes to create (creates actual files).
        metadata: List of metadata file names to create.
        tags: Dict of tag_name -> hash for manifest.

    Returns:
        Path to the artifact directory.
    """
    artifact_dir = storage_root / artifact_path
    artifact_dir.mkdir(parents=True, exist_ok=True)

    if blobs:
        blobs_dir = artifact_dir / "blobs"
        blobs_dir.mkdir(exist_ok=True)
        for blob_hash in blobs:
            (blobs_dir / blob_hash[:8]).write_bytes(b"blob content")

    if metadata:
        metadata_dir = artifact_dir / "metadata"
        metadata_dir.mkdir(exist_ok=True)
        for meta_hash in metadata:
            meta_file = metadata_dir / f"{meta_hash[:8]}.json"
            meta_content = {
                "hash": meta_hash,
                "uploaded_by": "test",
                "uploaded_at": datetime.now(timezone.utc).isoformat(),
                "source_uri": None,
            }
            meta_file.write_text(json.dumps(meta_content), encoding="utf-8")

    if tags is not None:
        manifest = {"version": 1, "tags": tags}
        (artifact_dir / ".magpie").write_text(json.dumps(manifest), encoding="utf-8")

    return artifact_dir


class TestIsEmptyDirectory:
    """Tests for is_empty_directory function."""

    def test_empty_directory_returns_true(self, tmp_path: Path) -> None:
        """Empty directory returns True."""
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        assert is_empty_directory(empty_dir) is True

    def test_directory_with_file_returns_false(self, tmp_path: Path) -> None:
        """Directory with a file returns False."""
        dir_with_file = tmp_path / "has_file"
        dir_with_file.mkdir()
        (dir_with_file / "file.txt").write_text("content")
        assert is_empty_directory(dir_with_file) is False

    def test_directory_with_subdir_returns_false(self, tmp_path: Path) -> None:
        """Directory with a subdirectory returns False."""
        dir_with_subdir = tmp_path / "has_subdir"
        dir_with_subdir.mkdir()
        (dir_with_subdir / "subdir").mkdir()
        assert is_empty_directory(dir_with_subdir) is False

    def test_file_returns_false(self, tmp_path: Path) -> None:
        """Regular file returns False."""
        file_path = tmp_path / "file.txt"
        file_path.write_text("content")
        assert is_empty_directory(file_path) is False

    def test_nonexistent_path_returns_false(self, tmp_path: Path) -> None:
        """Non-existent path returns False."""
        nonexistent = tmp_path / "does_not_exist"
        assert is_empty_directory(nonexistent) is False


class TestCleanupStats:
    """Tests for CleanupStats dataclass."""

    def test_default_values(self) -> None:
        """Default values are all zero."""
        stats = CleanupStats()
        assert stats.empty_blobs_dirs == 0
        assert stats.empty_metadata_dirs == 0
        assert stats.empty_manifests == 0
        assert stats.empty_artifact_dirs == 0
        assert stats.empty_parent_dirs == 0
        assert stats.removed_paths == []

    def test_total_removed_calculation(self) -> None:
        """total_removed sums all counts correctly."""
        stats = CleanupStats(
            empty_blobs_dirs=1,
            empty_metadata_dirs=2,
            empty_manifests=3,
            empty_artifact_dirs=4,
            empty_parent_dirs=5,
        )
        assert stats.total_removed == 15


class TestCleanupArtifactDirectories:
    """Tests for cleanup_artifact_directories function."""

    def test_removes_empty_blobs_dir(self, storage_root: Path) -> None:
        """Removes empty blobs/ directory."""
        artifact_dir = create_artifact_structure(
            storage_root, "test/artifact", blobs=["abc12345"], tags={"latest": "abc12345"}
        )

        # Manually empty the blobs directory
        blobs_dir = artifact_dir / "blobs"
        for f in blobs_dir.iterdir():
            f.unlink()

        stats = cleanup_artifact_directories(artifact_dir, storage_root, dry_run=False)

        assert stats.empty_blobs_dirs == 1
        assert not blobs_dir.exists()

    def test_removes_empty_metadata_dir(self, storage_root: Path) -> None:
        """Removes empty metadata/ directory."""
        artifact_dir = create_artifact_structure(
            storage_root, "test/artifact", metadata=["abc12345"], tags={"latest": "abc12345"}
        )

        # Manually empty the metadata directory
        metadata_dir = artifact_dir / "metadata"
        for f in metadata_dir.iterdir():
            f.unlink()

        stats = cleanup_artifact_directories(artifact_dir, storage_root, dry_run=False)

        assert stats.empty_metadata_dirs == 1
        assert not metadata_dir.exists()

    def test_removes_manifest_with_no_tags(self, storage_root: Path) -> None:
        """Removes .magpie file when no tags remain."""
        artifact_dir = create_artifact_structure(
            storage_root, "test/artifact", tags={}  # Empty tags
        )

        manifest_file = artifact_dir / ".magpie"
        assert manifest_file.exists()

        stats = cleanup_artifact_directories(artifact_dir, storage_root, dry_run=False)

        assert stats.empty_manifests == 1
        assert not manifest_file.exists()

    def test_preserves_manifest_with_tags(self, storage_root: Path) -> None:
        """Does not remove .magpie file when tags exist."""
        artifact_dir = create_artifact_structure(
            storage_root, "test/artifact", tags={"latest": "abc12345"}
        )

        manifest_file = artifact_dir / ".magpie"
        assert manifest_file.exists()

        stats = cleanup_artifact_directories(artifact_dir, storage_root, dry_run=False)

        assert stats.empty_manifests == 0
        assert manifest_file.exists()

    def test_removes_empty_artifact_dir(self, storage_root: Path) -> None:
        """Removes artifact directory when completely empty."""
        artifact_dir = create_artifact_structure(storage_root, "test/artifact", tags={})

        # Remove manifest to make it truly empty
        (artifact_dir / ".magpie").unlink()

        stats = cleanup_artifact_directories(artifact_dir, storage_root, dry_run=False)

        assert stats.empty_artifact_dirs == 1
        assert not artifact_dir.exists()

    def test_removes_empty_parent_dirs(self, storage_root: Path) -> None:
        """Removes empty parent directories up to storage root."""
        artifact_dir = create_artifact_structure(
            storage_root, "deep/nested/path/artifact", tags={}
        )

        # Remove manifest to make artifact dir empty
        (artifact_dir / ".magpie").unlink()

        stats = cleanup_artifact_directories(artifact_dir, storage_root, dry_run=False)

        assert stats.empty_artifact_dirs == 1
        assert stats.empty_parent_dirs == 3  # path, nested, deep
        assert not (storage_root / "deep").exists()

    def test_stops_at_storage_root(self, storage_root: Path) -> None:
        """Does not remove storage root directory."""
        artifact_dir = create_artifact_structure(storage_root, "single", tags={})

        # Remove manifest
        (artifact_dir / ".magpie").unlink()

        stats = cleanup_artifact_directories(artifact_dir, storage_root, dry_run=False)

        assert stats.empty_artifact_dirs == 1
        assert storage_root.exists()

    def test_preserves_non_empty_parent(self, storage_root: Path) -> None:
        """Stops parent cleanup when reaching non-empty directory."""
        # Create two artifacts under same parent
        artifact_dir1 = create_artifact_structure(
            storage_root, "parent/artifact1", tags={}
        )
        artifact_dir2 = create_artifact_structure(
            storage_root, "parent/artifact2", tags={"latest": "abc12345"}
        )

        # Remove manifest from first artifact
        (artifact_dir1 / ".magpie").unlink()

        stats = cleanup_artifact_directories(artifact_dir1, storage_root, dry_run=False)

        assert stats.empty_artifact_dirs == 1
        assert stats.empty_parent_dirs == 0  # Parent not empty (has artifact2)
        assert not artifact_dir1.exists()
        assert artifact_dir2.exists()
        assert (storage_root / "parent").exists()

    def test_dry_run_does_not_remove(self, storage_root: Path) -> None:
        """Dry run reports but does not remove directories."""
        artifact_dir = create_artifact_structure(
            storage_root, "test/artifact", blobs=["abc12345"], tags={}
        )

        # Empty the blobs dir
        blobs_dir = artifact_dir / "blobs"
        for f in blobs_dir.iterdir():
            f.unlink()

        stats = cleanup_artifact_directories(artifact_dir, storage_root, dry_run=True)

        # Should report what would be removed
        # Note: In dry run, blobs_dir and manifest are reported as removable,
        # but artifact_dir is not empty (still has .magpie until we "remove" it)
        # because we don't actually remove anything in dry run mode
        assert stats.empty_blobs_dirs == 1
        assert stats.empty_manifests == 1
        # artifact_dir still contains .magpie (not actually removed), so not empty
        assert stats.empty_artifact_dirs == 0
        assert len(stats.removed_paths) >= 2

        # But nothing should actually be removed
        assert blobs_dir.exists()
        assert (artifact_dir / ".magpie").exists()
        assert artifact_dir.exists()

    def test_removed_paths_contains_relative_paths(self, storage_root: Path) -> None:
        """removed_paths contains paths relative to storage root."""
        artifact_dir = create_artifact_structure(storage_root, "test/artifact", tags={})

        # Remove manifest - now artifact_dir is truly empty
        (artifact_dir / ".magpie").unlink()

        # In dry run mode, we report artifact_dir as empty but don't remove it
        # Since we don't remove it, the parent "test" still has content
        stats = cleanup_artifact_directories(artifact_dir, storage_root, dry_run=True)

        assert "test/artifact" in stats.removed_paths
        # Parent "test" is not empty in dry-run because artifact_dir still exists
        assert stats.empty_parent_dirs == 0

    def test_handles_missing_directories(self, storage_root: Path) -> None:
        """Handles case where subdirectories don't exist."""
        artifact_dir = create_artifact_structure(storage_root, "test/artifact", tags={})

        # No blobs/ or metadata/ directories created
        stats = cleanup_artifact_directories(artifact_dir, storage_root, dry_run=False)

        # Should still process the empty manifest
        assert stats.empty_blobs_dirs == 0  # Didn't exist
        assert stats.empty_metadata_dirs == 0  # Didn't exist
        assert stats.empty_manifests == 1
        assert stats.empty_artifact_dirs == 1

    def test_full_cleanup_scenario(self, storage_root: Path) -> None:
        """Tests a complete cleanup scenario after all blobs deleted."""
        # Create artifact with blobs and metadata
        artifact_dir = create_artifact_structure(
            storage_root,
            "project/component/artifact",
            blobs=["hash1234", "hash5678"],
            metadata=["hash1234", "hash5678"],
            tags={},  # No tags (simulates after untag)
        )

        # Simulate GC having deleted all blobs and metadata
        blobs_dir = artifact_dir / "blobs"
        for f in blobs_dir.iterdir():
            f.unlink()

        metadata_dir = artifact_dir / "metadata"
        for f in metadata_dir.iterdir():
            f.unlink()

        stats = cleanup_artifact_directories(artifact_dir, storage_root, dry_run=False)

        assert stats.empty_blobs_dirs == 1
        assert stats.empty_metadata_dirs == 1
        assert stats.empty_manifests == 1
        assert stats.empty_artifact_dirs == 1
        assert stats.empty_parent_dirs == 2  # component, project
        assert stats.total_removed == 6

        # Everything should be cleaned up
        assert not (storage_root / "project").exists()
