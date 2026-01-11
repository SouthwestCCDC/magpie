"""Unit tests for symlink management operations."""

from __future__ import annotations

from pathlib import Path

import pytest

from magpie.storage.manifest import Manifest
from magpie.storage.symlinks import create_symlink, reconcile_symlinks, remove_symlink


@pytest.fixture
def artifact_dir(tmp_path: Path) -> Path:
    """Create a test artifact directory with blobs subdirectory."""
    path = tmp_path / "artifact"
    path.mkdir()
    (path / "blobs").mkdir()
    return path


class TestCreateSymlink:
    """Tests for create_symlink function."""

    def test_create_symlink_creates_link(self, artifact_dir: Path) -> None:
        """create_symlink should create a working symlink."""
        # Create a blob file to link to
        blob_hash = "abc12345"
        blob_file = artifact_dir / "blobs" / blob_hash
        blob_file.write_text("blob content")

        create_symlink(artifact_dir, "latest", blob_hash)

        symlink_path = artifact_dir / "latest"
        assert symlink_path.is_symlink()
        # Verify it resolves to the blob
        assert symlink_path.read_text() == "blob content"

    def test_create_symlink_uses_relative_target(self, artifact_dir: Path) -> None:
        """create_symlink should use relative path as target."""
        blob_hash = "abc12345"
        (artifact_dir / "blobs" / blob_hash).write_text("content")

        create_symlink(artifact_dir, "latest", blob_hash)

        symlink_path = artifact_dir / "latest"
        target = symlink_path.readlink()
        assert target == Path("blobs/abc12345")

    def test_create_symlink_with_at_prefix(self, artifact_dir: Path) -> None:
        """create_symlink should handle hash refs with @ prefix."""
        blob_hash = "abc12345"
        (artifact_dir / "blobs" / blob_hash).write_text("content")

        create_symlink(artifact_dir, "latest", f"@{blob_hash}")

        symlink_path = artifact_dir / "latest"
        target = symlink_path.readlink()
        # @ prefix should be stripped from target
        assert target == Path("blobs/abc12345")

    def test_create_symlink_updates_existing(self, artifact_dir: Path) -> None:
        """create_symlink should atomically update existing symlink."""
        hash1 = "abc12345"
        hash2 = "def67890"
        (artifact_dir / "blobs" / hash1).write_text("content1")
        (artifact_dir / "blobs" / hash2).write_text("content2")

        # Create initial symlink
        create_symlink(artifact_dir, "latest", hash1)
        assert (artifact_dir / "latest").read_text() == "content1"

        # Update to new target
        create_symlink(artifact_dir, "latest", hash2)
        assert (artifact_dir / "latest").read_text() == "content2"

    def test_create_symlink_creates_artifact_dir(self, tmp_path: Path) -> None:
        """create_symlink should create artifact directory if missing."""
        artifact_dir = tmp_path / "new" / "artifact"
        # Don't create it, let create_symlink handle it

        create_symlink(artifact_dir, "latest", "abc12345")

        assert artifact_dir.exists()
        assert (artifact_dir / "latest").is_symlink()


class TestRemoveSymlink:
    """Tests for remove_symlink function."""

    def test_remove_symlink_removes_existing(self, artifact_dir: Path) -> None:
        """remove_symlink should remove an existing symlink."""
        # Create a symlink
        create_symlink(artifact_dir, "latest", "abc12345")
        assert (artifact_dir / "latest").is_symlink()

        remove_symlink(artifact_dir, "latest")

        assert not (artifact_dir / "latest").exists()

    def test_remove_symlink_no_op_nonexistent(self, artifact_dir: Path) -> None:
        """remove_symlink should not raise error for non-existent symlink."""
        # Should not raise
        remove_symlink(artifact_dir, "nonexistent")

    def test_remove_symlink_does_not_remove_regular_file(
        self, artifact_dir: Path
    ) -> None:
        """remove_symlink should only remove symlinks, not regular files."""
        regular_file = artifact_dir / "regular"
        regular_file.write_text("regular content")

        remove_symlink(artifact_dir, "regular")

        # Regular file should still exist
        assert regular_file.exists()
        assert regular_file.read_text() == "regular content"


class TestReconcileSymlinks:
    """Tests for reconcile_symlinks function."""

    def test_reconcile_creates_missing_symlinks(self, artifact_dir: Path) -> None:
        """reconcile_symlinks should create symlinks for all manifest tags."""
        # Create blob files
        (artifact_dir / "blobs" / "abc12345").write_text("content1")
        (artifact_dir / "blobs" / "def67890").write_text("content2")

        manifest = Manifest(
            tags={
                "latest": "@abc12345",
                "v1.0": "@def67890",
            }
        )

        checked, fixed = reconcile_symlinks(artifact_dir, manifest)

        assert (artifact_dir / "latest").is_symlink()
        assert (artifact_dir / "v1.0").is_symlink()
        assert (artifact_dir / "latest").read_text() == "content1"
        assert (artifact_dir / "v1.0").read_text() == "content2"
        # No existing symlinks, 2 created
        assert checked == 0
        assert fixed == 2

    def test_reconcile_removes_orphan_symlinks(self, artifact_dir: Path) -> None:
        """reconcile_symlinks should remove symlinks not in manifest."""
        # Create orphan symlinks
        create_symlink(artifact_dir, "old-tag", "abc12345")
        create_symlink(artifact_dir, "another-old", "def67890")

        manifest = Manifest(tags={})  # Empty manifest

        reconcile_symlinks(artifact_dir, manifest)

        assert not (artifact_dir / "old-tag").exists()
        assert not (artifact_dir / "another-old").exists()

    def test_reconcile_preserves_correct_symlinks(self, artifact_dir: Path) -> None:
        """reconcile_symlinks should preserve symlinks that match manifest."""
        hash_ref = "abc12345"
        (artifact_dir / "blobs" / hash_ref).write_text("content")

        # Create correct symlink
        create_symlink(artifact_dir, "latest", hash_ref)

        manifest = Manifest(tags={"latest": f"@{hash_ref}"})

        reconcile_symlinks(artifact_dir, manifest)

        # Symlink should still exist and be correct
        assert (artifact_dir / "latest").is_symlink()
        assert (artifact_dir / "latest").read_text() == "content"

    def test_reconcile_ignores_regular_files(self, artifact_dir: Path) -> None:
        """reconcile_symlinks should not delete non-symlink files."""
        # Create a regular file (not a symlink)
        regular_file = artifact_dir / "README.txt"
        regular_file.write_text("This is a readme")

        # Create a directory
        subdir = artifact_dir / "subdir"
        subdir.mkdir()

        manifest = Manifest(tags={})  # Empty manifest

        reconcile_symlinks(artifact_dir, manifest)

        # Regular file and directory should still exist
        assert regular_file.exists()
        assert regular_file.read_text() == "This is a readme"
        assert subdir.is_dir()

    def test_reconcile_updates_wrong_symlinks(self, artifact_dir: Path) -> None:
        """reconcile_symlinks should update symlinks pointing to wrong target."""
        (artifact_dir / "blobs" / "old123").write_text("old")
        (artifact_dir / "blobs" / "new456").write_text("new")

        # Create symlink pointing to old blob
        create_symlink(artifact_dir, "latest", "old123")
        assert (artifact_dir / "latest").read_text() == "old"

        # Manifest says latest should point to new blob
        manifest = Manifest(tags={"latest": "@new456"})

        reconcile_symlinks(artifact_dir, manifest)

        # Symlink should now point to new blob
        assert (artifact_dir / "latest").read_text() == "new"

    def test_reconcile_handles_empty_artifact_dir(self, tmp_path: Path) -> None:
        """reconcile_symlinks should handle non-existent artifact directory."""
        artifact_dir = tmp_path / "nonexistent"
        manifest = Manifest(tags={"latest": "@abc12345"})

        # Should not raise, should create symlink
        reconcile_symlinks(artifact_dir, manifest)

        assert (artifact_dir / "latest").is_symlink()

    def test_reconcile_full_scenario(self, artifact_dir: Path) -> None:
        """reconcile_symlinks should handle add, remove, update in one call."""
        # Setup blobs
        (artifact_dir / "blobs" / "hash1").write_text("content1")
        (artifact_dir / "blobs" / "hash2").write_text("content2")
        (artifact_dir / "blobs" / "hash3").write_text("content3")

        # Create initial symlinks
        create_symlink(artifact_dir, "keep", "hash1")  # Will be preserved
        create_symlink(artifact_dir, "update", "hash1")  # Will be updated
        create_symlink(artifact_dir, "remove", "hash1")  # Will be removed

        manifest = Manifest(
            tags={
                "keep": "@hash1",  # Same target
                "update": "@hash2",  # Different target
                "add": "@hash3",  # New tag
                # "remove" not in manifest - should be removed
            }
        )

        checked, fixed = reconcile_symlinks(artifact_dir, manifest)

        # Verify results
        assert (artifact_dir / "keep").read_text() == "content1"
        assert (artifact_dir / "update").read_text() == "content2"
        assert (artifact_dir / "add").read_text() == "content3"
        assert not (artifact_dir / "remove").exists()
        # 3 symlinks checked, 3 fixed (1 added, 1 removed, 1 updated)
        assert checked == 3
        assert fixed == 3

    def test_reconcile_returns_stats(self, artifact_dir: Path) -> None:
        """reconcile_symlinks should return correct statistics."""
        # Create blobs
        (artifact_dir / "blobs" / "hash1").write_text("content1")
        (artifact_dir / "blobs" / "hash2").write_text("content2")

        # Create existing correct symlink
        create_symlink(artifact_dir, "existing", "hash1")

        manifest = Manifest(
            tags={
                "existing": "@hash1",  # Already correct
                "new": "@hash2",  # New symlink
            }
        )

        checked, fixed = reconcile_symlinks(artifact_dir, manifest)

        # 1 existing symlink checked, 1 new created
        assert checked == 1
        assert fixed == 1
