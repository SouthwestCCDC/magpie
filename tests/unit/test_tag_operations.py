"""Unit tests for StorageService tag operations (create_tag, remove_tag)."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from magpie.config import MagpieSettings
from magpie.storage.exceptions import ArtifactNotFoundError
from magpie.storage.manifest import read_manifest
from magpie.storage.paths import artifact_dir_path
from magpie.storage.service import StorageService
from magpie.validation import ValidationError


@pytest.fixture
def test_config(tmp_path: Path) -> MagpieSettings:
    """Create test configuration with temporary paths."""
    return MagpieSettings(
        storage_path=tmp_path,
        database_path=tmp_path / "magpie.db",
    )


@pytest.fixture
def storage_service(test_config: MagpieSettings) -> StorageService:
    """Create a StorageService instance for testing."""
    return StorageService(test_config)


def store_test_artifact(
    storage_service: StorageService, artifact_path: str, content: bytes
) -> tuple[str, str]:
    """Helper to store a test artifact and return (full_hash, hash_ref)."""
    info, _ = storage_service.store_artifact(
        artifact_path=artifact_path,
        file_stream=io.BytesIO(content),
        uploaded_by="test-user",
    )
    return info.hash, info.hash_ref


class TestCreateTag:
    """Tests for StorageService.create_tag method."""

    def test_create_tag_creates_new_tag(
        self, storage_service: StorageService, test_config: MagpieSettings
    ) -> None:
        """create_tag should create a new tag pointing to a blob."""
        artifact_path = "test/create-tag"
        full_hash, hash_ref = store_test_artifact(storage_service, artifact_path, b"test content")

        # Create a new tag
        info = storage_service.create_tag(artifact_path, hash_ref, "stable")

        assert info.hash == full_hash
        assert "stable" in info.tags
        assert "latest" in info.tags  # Original tag preserved

        # Verify manifest updated
        artifact_dir = artifact_dir_path(test_config.storage_path, artifact_path)
        manifest = read_manifest(artifact_dir)
        assert manifest.tags["stable"] == full_hash

    def test_create_tag_with_full_hash(self, storage_service: StorageService) -> None:
        """create_tag should accept full SHA-256 hash."""
        artifact_path = "test/full-hash-tag"
        full_hash, _ = store_test_artifact(storage_service, artifact_path, b"content")

        # Create tag using full hash (no @ prefix)
        info = storage_service.create_tag(artifact_path, full_hash, "v1.0")

        assert info.hash == full_hash
        assert "v1.0" in info.tags

    def test_create_tag_updates_existing_tag(
        self, storage_service: StorageService, test_config: MagpieSettings
    ) -> None:
        """create_tag should update an existing tag to point to different hash."""
        artifact_path = "test/update-tag"

        # Store first version
        hash1, ref1 = store_test_artifact(storage_service, artifact_path, b"version 1")

        # Store second version (different content = different hash)
        hash2, ref2 = store_test_artifact(storage_service, artifact_path, b"version 2")

        # Create "stable" tag pointing to first version
        storage_service.create_tag(artifact_path, ref1, "stable")

        # Update "stable" tag to point to second version
        info = storage_service.create_tag(artifact_path, ref2, "stable")

        assert info.hash == hash2
        assert "stable" in info.tags

        # Verify manifest reflects update
        artifact_dir = artifact_dir_path(test_config.storage_path, artifact_path)
        manifest = read_manifest(artifact_dir)
        assert manifest.tags["stable"] == hash2

    def test_create_tag_with_nonexistent_hash_raises(self, storage_service: StorageService) -> None:
        """create_tag should raise ArtifactNotFoundError for non-existent hash."""
        artifact_path = "test/nonexistent-hash"

        # Store an artifact so the artifact directory exists
        store_test_artifact(storage_service, artifact_path, b"content")

        # Try to create tag with non-existent hash
        with pytest.raises(ArtifactNotFoundError):
            storage_service.create_tag(artifact_path, "@deadbeef", "bad-tag")

    def test_create_tag_with_nonexistent_artifact_path_raises(
        self, storage_service: StorageService
    ) -> None:
        """create_tag should raise ArtifactNotFoundError for non-existent path."""
        with pytest.raises(ArtifactNotFoundError):
            storage_service.create_tag("nonexistent/path", "@abc12345", "tag")

    def test_create_tag_returns_complete_artifact_info(
        self, storage_service: StorageService
    ) -> None:
        """create_tag should return complete ArtifactInfo."""
        artifact_path = "test/complete-info"
        full_hash, hash_ref = store_test_artifact(storage_service, artifact_path, b"content")

        info = storage_service.create_tag(artifact_path, hash_ref, "tagged")

        assert info.hash == full_hash
        assert info.hash_ref == hash_ref
        assert info.uploaded_by == "test-user"
        assert info.uploaded_at is not None
        assert "tagged" in info.tags


class TestRemoveTag:
    """Tests for StorageService.remove_tag method."""

    def test_remove_tag_removes_existing_tag(
        self, storage_service: StorageService, test_config: MagpieSettings
    ) -> None:
        """remove_tag should remove an existing tag and return True."""
        artifact_path = "test/remove-tag"
        full_hash, hash_ref = store_test_artifact(storage_service, artifact_path, b"content")

        # Create a tag to remove
        storage_service.create_tag(artifact_path, hash_ref, "to-remove")

        # Verify tag exists
        artifact_dir = artifact_dir_path(test_config.storage_path, artifact_path)
        manifest = read_manifest(artifact_dir)
        assert "to-remove" in manifest.tags

        # Remove the tag
        result = storage_service.remove_tag(artifact_path, "to-remove")

        assert result is True

        # Verify tag removed from manifest
        manifest = read_manifest(artifact_dir)
        assert "to-remove" not in manifest.tags
        # Other tags should remain
        assert "latest" in manifest.tags

    def test_remove_tag_returns_false_for_nonexistent_tag(
        self, storage_service: StorageService
    ) -> None:
        """remove_tag should return False for non-existent tag."""
        artifact_path = "test/remove-nonexistent"
        store_test_artifact(storage_service, artifact_path, b"content")

        result = storage_service.remove_tag(artifact_path, "nonexistent-tag")

        assert result is False

    def test_remove_tag_logs_warning_for_nonexistent_tag(
        self, storage_service: StorageService, caplog: pytest.LogCaptureFixture
    ) -> None:
        """remove_tag should log warning when removing non-existent tag."""
        artifact_path = "test/remove-warning"
        store_test_artifact(storage_service, artifact_path, b"content")

        storage_service.remove_tag(artifact_path, "nonexistent-tag")

        assert "nonexistent-tag" in caplog.text
        assert "not found" in caplog.text.lower()

    def test_remove_tag_on_empty_artifact_returns_false(
        self, storage_service: StorageService, test_config: MagpieSettings
    ) -> None:
        """remove_tag should return False for artifact path with no tags."""
        artifact_path = "test/empty-artifact"

        # Create artifact directory but no manifest
        artifact_dir = artifact_dir_path(test_config.storage_path, artifact_path)
        artifact_dir.mkdir(parents=True)

        result = storage_service.remove_tag(artifact_path, "any-tag")

        assert result is False


class TestTagSymlinks:
    """Tests for symlink creation/removal during tag operations."""

    def test_create_tag_creates_symlink(
        self, storage_service: StorageService, test_config: MagpieSettings
    ) -> None:
        """create_tag should create a symlink for the new tag."""
        artifact_path = "test/tag-symlink"
        _, hash_ref = store_test_artifact(storage_service, artifact_path, b"symlink content")

        storage_service.create_tag(artifact_path, hash_ref, "my-tag")

        artifact_dir = artifact_dir_path(test_config.storage_path, artifact_path)
        symlink_path = artifact_dir / "my-tag"

        assert symlink_path.is_symlink()
        assert symlink_path.read_bytes() == b"symlink content"

    def test_create_tag_updates_symlink_target(
        self, storage_service: StorageService, test_config: MagpieSettings
    ) -> None:
        """create_tag should update symlink when tag is updated."""
        artifact_path = "test/update-symlink"

        # Store two versions
        hash1, ref1 = store_test_artifact(storage_service, artifact_path, b"version 1")
        hash2, ref2 = store_test_artifact(storage_service, artifact_path, b"version 2")

        # Create tag pointing to first version
        storage_service.create_tag(artifact_path, ref1, "switchable")

        artifact_dir = artifact_dir_path(test_config.storage_path, artifact_path)
        symlink_path = artifact_dir / "switchable"
        assert symlink_path.read_bytes() == b"version 1"

        # Update tag to point to second version
        storage_service.create_tag(artifact_path, ref2, "switchable")

        assert symlink_path.read_bytes() == b"version 2"

    def test_remove_tag_removes_symlink(
        self, storage_service: StorageService, test_config: MagpieSettings
    ) -> None:
        """remove_tag should remove the symlink for the tag."""
        artifact_path = "test/remove-symlink"
        _, hash_ref = store_test_artifact(storage_service, artifact_path, b"content")

        # Create a tag with symlink
        storage_service.create_tag(artifact_path, hash_ref, "temp-tag")

        artifact_dir = artifact_dir_path(test_config.storage_path, artifact_path)
        symlink_path = artifact_dir / "temp-tag"
        assert symlink_path.is_symlink()

        # Remove the tag
        storage_service.remove_tag(artifact_path, "temp-tag")

        assert not symlink_path.exists()

    def test_remove_tag_preserves_other_symlinks(
        self, storage_service: StorageService, test_config: MagpieSettings
    ) -> None:
        """remove_tag should not affect other tag symlinks."""
        artifact_path = "test/preserve-symlinks"
        _, hash_ref = store_test_artifact(storage_service, artifact_path, b"content")

        # Create multiple tags
        storage_service.create_tag(artifact_path, hash_ref, "keep-this")
        storage_service.create_tag(artifact_path, hash_ref, "remove-this")

        artifact_dir = artifact_dir_path(test_config.storage_path, artifact_path)

        # Remove one tag
        storage_service.remove_tag(artifact_path, "remove-this")

        # Other symlinks should remain
        assert (artifact_dir / "keep-this").is_symlink()
        assert (artifact_dir / "latest").is_symlink()
        assert not (artifact_dir / "remove-this").exists()


class TestTagNameValidation:
    """Tests for tag name validation in StorageService.

    Defense-in-depth validation at service layer ensures CLI and other
    non-API callers also get proper validation.
    """

    def test_create_tag_with_invalid_name_raises_validation_error(
        self, storage_service: StorageService
    ) -> None:
        """create_tag should raise ValidationError for invalid tag name."""
        artifact_path = "test/invalid-tag-name"
        _, hash_ref = store_test_artifact(storage_service, artifact_path, b"content")

        with pytest.raises(ValidationError, match="must start with alphanumeric"):
            storage_service.create_tag(artifact_path, hash_ref, "-invalid")

    def test_create_tag_with_empty_name_raises_validation_error(
        self, storage_service: StorageService
    ) -> None:
        """create_tag should raise ValidationError for empty tag name."""
        artifact_path = "test/empty-tag-name"
        _, hash_ref = store_test_artifact(storage_service, artifact_path, b"content")

        with pytest.raises(ValidationError, match="cannot be empty"):
            storage_service.create_tag(artifact_path, hash_ref, "")

    def test_create_tag_with_too_long_name_raises_validation_error(
        self, storage_service: StorageService
    ) -> None:
        """create_tag should raise ValidationError for tag name exceeding max length."""
        artifact_path = "test/long-tag-name"
        _, hash_ref = store_test_artifact(storage_service, artifact_path, b"content")
        long_name = "a" * 129  # Max is 128

        with pytest.raises(ValidationError, match="exceeds maximum length"):
            storage_service.create_tag(artifact_path, hash_ref, long_name)

    def test_create_tag_with_special_chars_raises(self, storage_service: StorageService) -> None:
        """create_tag should raise ValidationError for tag with special chars."""
        artifact_path = "test/special-chars"
        _, hash_ref = store_test_artifact(storage_service, artifact_path, b"content")

        with pytest.raises(ValidationError):
            storage_service.create_tag(artifact_path, hash_ref, "v1@beta!")

    def test_remove_tag_with_invalid_name_raises_validation_error(
        self, storage_service: StorageService
    ) -> None:
        """remove_tag should raise ValidationError for invalid tag name."""
        artifact_path = "test/remove-invalid-name"
        store_test_artifact(storage_service, artifact_path, b"content")

        with pytest.raises(ValidationError, match="must start with alphanumeric"):
            storage_service.remove_tag(artifact_path, "-invalid")

    def test_remove_tag_with_empty_name_raises_validation_error(
        self, storage_service: StorageService
    ) -> None:
        """remove_tag should raise ValidationError for empty tag name."""
        artifact_path = "test/remove-empty-name"
        store_test_artifact(storage_service, artifact_path, b"content")

        with pytest.raises(ValidationError, match="cannot be empty"):
            storage_service.remove_tag(artifact_path, "")

    def test_validation_error_is_catchable_as_value_error(
        self, storage_service: StorageService
    ) -> None:
        """ValidationError should be catchable as ValueError for backward compatibility."""
        artifact_path = "test/catch-as-value-error"
        _, hash_ref = store_test_artifact(storage_service, artifact_path, b"content")

        try:
            storage_service.create_tag(artifact_path, hash_ref, "-invalid")
            assert False, "Should have raised an exception"
        except ValueError:
            pass  # ValidationError is a subclass of ValueError
