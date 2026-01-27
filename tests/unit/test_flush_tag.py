"""Unit tests for StorageService.flush_tag operation."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from magpie.config import MagpieSettings
from magpie.storage.manifest import read_manifest
from magpie.storage.paths import artifact_dir_path
from magpie.storage.service import FlushResult, StorageService
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


class TestFlushTagBasic:
    """Basic tests for flush_tag functionality."""

    def test_flush_tag_affects_multiple_artifacts(
        self, storage_service: StorageService, test_config: MagpieSettings
    ) -> None:
        """flush_tag should remove tag from multiple artifacts."""
        # Create multiple artifacts with the same tag
        paths = ["project/artifact1", "project/artifact2", "other/artifact3"]
        for path in paths:
            _, hash_ref = store_test_artifact(storage_service, path, f"content for {path}".encode())
            storage_service.create_tag(path, hash_ref, "release")

        # Verify tags exist
        for path in paths:
            artifact_dir = artifact_dir_path(test_config.storage_path, path)
            manifest = read_manifest(artifact_dir)
            assert "release" in manifest.tags

        # Flush the tag
        result = storage_service.flush_tag("release")

        # Verify result
        assert isinstance(result, FlushResult)
        assert result.tag_name == "release"
        assert result.count == 3
        assert len(result.affected_artifacts) == 3
        for path in paths:
            assert path in result.affected_artifacts

        # Verify tags removed from manifests
        for path in paths:
            artifact_dir = artifact_dir_path(test_config.storage_path, path)
            manifest = read_manifest(artifact_dir)
            assert "release" not in manifest.tags

    def test_flush_tag_with_unknown_tag_returns_empty(
        self, storage_service: StorageService
    ) -> None:
        """flush_tag with non-existent tag should return empty result."""
        # Create some artifacts without the target tag
        store_test_artifact(storage_service, "project/artifact", b"content")

        result = storage_service.flush_tag("nonexistent-tag")

        assert result.tag_name == "nonexistent-tag"
        assert result.count == 0
        assert result.affected_artifacts == []

    def test_flush_tag_only_affects_artifacts_with_tag(
        self, storage_service: StorageService, test_config: MagpieSettings
    ) -> None:
        """flush_tag should only affect artifacts that have the tag."""
        # Create artifacts - some with target tag, some without
        _, ref1 = store_test_artifact(storage_service, "with-tag/artifact1", b"content1")
        storage_service.create_tag("with-tag/artifact1", ref1, "target-tag")

        _, ref2 = store_test_artifact(storage_service, "with-tag/artifact2", b"content2")
        storage_service.create_tag("with-tag/artifact2", ref2, "target-tag")

        # This artifact does NOT have the target tag
        store_test_artifact(storage_service, "without-tag/artifact", b"content3")

        # Flush the tag
        result = storage_service.flush_tag("target-tag")

        # Only artifacts with the tag should be affected
        assert result.count == 2
        assert "with-tag/artifact1" in result.affected_artifacts
        assert "with-tag/artifact2" in result.affected_artifacts
        assert "without-tag/artifact" not in result.affected_artifacts

        # Verify "latest" tag still exists on artifact without target tag
        artifact_dir = artifact_dir_path(test_config.storage_path, "without-tag/artifact")
        manifest = read_manifest(artifact_dir)
        assert "latest" in manifest.tags

    def test_flush_tag_count_matches_affected_artifacts_length(
        self, storage_service: StorageService
    ) -> None:
        """flush_tag count should match length of affected_artifacts."""
        # Create several artifacts with the tag
        for i in range(5):
            _, ref = store_test_artifact(
                storage_service, f"project/artifact{i}", f"content{i}".encode()
            )
            storage_service.create_tag(f"project/artifact{i}", ref, "countable")

        result = storage_service.flush_tag("countable")

        assert result.count == len(result.affected_artifacts)
        assert result.count == 5


class TestFlushTagDryRun:
    """Tests for flush_tag dry_run mode."""

    def test_flush_tag_dry_run_is_read_only(
        self, storage_service: StorageService, test_config: MagpieSettings
    ) -> None:
        """flush_tag with dry_run=True should not modify any manifests."""
        # Create artifacts with a tag
        paths = ["project/a", "project/b"]
        for path in paths:
            _, ref = store_test_artifact(storage_service, path, f"content for {path}".encode())
            storage_service.create_tag(path, ref, "to-flush")

        # Dry run
        result = storage_service.flush_tag("to-flush", dry_run=True)

        # Result should show affected artifacts
        assert result.count == 2
        assert len(result.affected_artifacts) == 2

        # But tags should still exist in manifests
        for path in paths:
            artifact_dir = artifact_dir_path(test_config.storage_path, path)
            manifest = read_manifest(artifact_dir)
            assert "to-flush" in manifest.tags, f"Tag should still exist in {path}"

    def test_flush_tag_dry_run_then_actual_flush(
        self, storage_service: StorageService, test_config: MagpieSettings
    ) -> None:
        """dry_run should preview, then actual flush should remove tags."""
        # Create artifact with tag
        _, ref = store_test_artifact(storage_service, "test/artifact", b"content")
        storage_service.create_tag("test/artifact", ref, "preview-tag")

        # Dry run first
        dry_result = storage_service.flush_tag("preview-tag", dry_run=True)
        assert dry_result.count == 1

        # Tag should still exist
        artifact_dir = artifact_dir_path(test_config.storage_path, "test/artifact")
        manifest = read_manifest(artifact_dir)
        assert "preview-tag" in manifest.tags

        # Actual flush
        actual_result = storage_service.flush_tag("preview-tag", dry_run=False)
        assert actual_result.count == 1

        # Tag should now be removed
        manifest = read_manifest(artifact_dir)
        assert "preview-tag" not in manifest.tags

    def test_flush_tag_dry_run_preserves_symlinks(
        self, storage_service: StorageService, test_config: MagpieSettings
    ) -> None:
        """flush_tag with dry_run=True should not remove symlinks."""
        # Create artifact with tag
        _, ref = store_test_artifact(storage_service, "symlink/test", b"content")
        storage_service.create_tag("symlink/test", ref, "keep-symlink")

        artifact_dir = artifact_dir_path(test_config.storage_path, "symlink/test")
        symlink_path = artifact_dir / "keep-symlink"
        assert symlink_path.is_symlink()

        # Dry run
        storage_service.flush_tag("keep-symlink", dry_run=True)

        # Symlink should still exist
        assert symlink_path.is_symlink()


class TestFlushTagEdgeCases:
    """Edge case tests for flush_tag."""

    def test_flush_tag_empty_storage(self, storage_service: StorageService) -> None:
        """flush_tag on empty storage should return empty result."""
        result = storage_service.flush_tag("any-tag")

        assert result.count == 0
        assert result.affected_artifacts == []

    def test_flush_tag_nested_artifact_paths(self, storage_service: StorageService) -> None:
        """flush_tag should work with deeply nested artifact paths."""
        nested_paths = [
            "a/b/c/artifact",
            "a/b/artifact",
            "x/y/z/w/artifact",
        ]

        for path in nested_paths:
            _, ref = store_test_artifact(storage_service, path, f"content for {path}".encode())
            storage_service.create_tag(path, ref, "deep-tag")

        result = storage_service.flush_tag("deep-tag")

        assert result.count == 3
        for path in nested_paths:
            assert path in result.affected_artifacts

    def test_flush_tag_preserves_other_tags(
        self, storage_service: StorageService, test_config: MagpieSettings
    ) -> None:
        """flush_tag should not affect other tags on the same artifacts."""
        # Create artifact with multiple tags
        _, ref = store_test_artifact(storage_service, "multi-tag/artifact", b"content")
        storage_service.create_tag("multi-tag/artifact", ref, "flush-me")
        storage_service.create_tag("multi-tag/artifact", ref, "keep-me")
        storage_service.create_tag("multi-tag/artifact", ref, "also-keep")

        # Flush only one tag
        storage_service.flush_tag("flush-me")

        # Other tags should remain
        artifact_dir = artifact_dir_path(test_config.storage_path, "multi-tag/artifact")
        manifest = read_manifest(artifact_dir)
        assert "flush-me" not in manifest.tags
        assert "keep-me" in manifest.tags
        assert "also-keep" in manifest.tags
        assert "latest" in manifest.tags

    def test_flush_result_dataclass_fields(self, storage_service: StorageService) -> None:
        """FlushResult should have correct field values."""
        _, ref = store_test_artifact(storage_service, "result/test", b"content")
        storage_service.create_tag("result/test", ref, "check-result")

        result = storage_service.flush_tag("check-result")

        assert hasattr(result, "tag_name")
        assert hasattr(result, "affected_artifacts")
        assert hasattr(result, "count")
        assert result.tag_name == "check-result"
        assert isinstance(result.affected_artifacts, list)
        assert isinstance(result.count, int)


class TestFlushTagValidation:
    """Tests for tag name validation in flush_tag.

    Defense-in-depth validation at service layer ensures CLI and other
    non-API callers also get proper validation.
    """

    def test_flush_tag_with_invalid_name_raises_validation_error(
        self, storage_service: StorageService
    ) -> None:
        """flush_tag should raise ValidationError for invalid tag name."""
        with pytest.raises(ValidationError, match="must start with alphanumeric"):
            storage_service.flush_tag("-invalid-tag")

    def test_flush_tag_with_empty_name_raises_validation_error(
        self, storage_service: StorageService
    ) -> None:
        """flush_tag should raise ValidationError for empty tag name."""
        with pytest.raises(ValidationError, match="cannot be empty"):
            storage_service.flush_tag("")

    def test_flush_tag_with_too_long_name_raises_validation_error(
        self, storage_service: StorageService
    ) -> None:
        """flush_tag should raise ValidationError for tag name exceeding max length."""
        long_name = "a" * 129  # Max is 128
        with pytest.raises(ValidationError, match="exceeds maximum length"):
            storage_service.flush_tag(long_name)

    def test_flush_tag_with_special_chars_raises(self, storage_service: StorageService) -> None:
        """flush_tag should raise ValidationError for tag with special chars."""
        with pytest.raises(ValidationError):
            storage_service.flush_tag("v1@beta!")
