"""Unit tests for StorageService.amend_metadata operation."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from magpie.config import MagpieSettings
from magpie.storage.exceptions import ArtifactNotFoundError
from magpie.storage.metadata import read_metadata
from magpie.storage.paths import artifact_dir_path
from magpie.storage.service import StorageService


@pytest.fixture
def test_config(tmp_path: Path) -> MagpieSettings:
    """Create test configuration with temporary paths."""
    return MagpieSettings(storage_path=tmp_path)


@pytest.fixture
def storage_service(test_config: MagpieSettings) -> StorageService:
    """Create a StorageService instance for testing."""
    return StorageService(test_config)


def store_test_artifact(
    storage_service: StorageService,
    artifact_path: str,
    content: bytes,
    source_uri: str | None = None,
) -> tuple[str, str]:
    """Helper to store a test artifact and return (full_hash, hash_ref)."""
    info, _ = storage_service.store_artifact(
        artifact_path=artifact_path,
        file_stream=io.BytesIO(content),
        uploaded_by="test-user",
        source_uri=source_uri,
    )
    return info.hash, info.hash_ref


class TestAmendMetadataBasic:
    """Basic tests for amend_metadata functionality."""

    def test_amend_metadata_updates_source_uri(
        self, storage_service: StorageService, test_config: MagpieSettings
    ) -> None:
        """amend_metadata should update source_uri."""
        artifact_path = "test/amend"
        full_hash, hash_ref = store_test_artifact(
            storage_service, artifact_path, b"content", source_uri="original-uri"
        )

        # Amend metadata with new source_uri
        result = storage_service.amend_metadata(artifact_path, hash_ref, source_uri="new-uri")

        assert result.source_uri == "new-uri"

        # Verify persisted in file
        artifact_dir = artifact_dir_path(test_config.storage_path, artifact_path)
        metadata = read_metadata(artifact_dir, full_hash)
        assert metadata.source_uri == "new-uri"

    def test_amend_metadata_preserves_hash(self, storage_service: StorageService) -> None:
        """amend_metadata should preserve hash field."""
        artifact_path = "test/preserve-hash"
        full_hash, hash_ref = store_test_artifact(storage_service, artifact_path, b"content")

        result = storage_service.amend_metadata(artifact_path, hash_ref, source_uri="new-uri")

        assert result.hash == full_hash

    def test_amend_metadata_preserves_uploaded_by(
        self, storage_service: StorageService, test_config: MagpieSettings
    ) -> None:
        """amend_metadata should preserve uploaded_by field."""
        artifact_path = "test/preserve-uploader"
        full_hash, hash_ref = store_test_artifact(storage_service, artifact_path, b"content")

        result = storage_service.amend_metadata(artifact_path, hash_ref, source_uri="new-uri")

        assert result.uploaded_by == "test-user"

        # Verify persisted
        artifact_dir = artifact_dir_path(test_config.storage_path, artifact_path)
        metadata = read_metadata(artifact_dir, full_hash)
        assert metadata.uploaded_by == "test-user"

    def test_amend_metadata_preserves_uploaded_at(
        self, storage_service: StorageService, test_config: MagpieSettings
    ) -> None:
        """amend_metadata should preserve uploaded_at field."""
        artifact_path = "test/preserve-timestamp"
        full_hash, hash_ref = store_test_artifact(storage_service, artifact_path, b"content")

        # Get original timestamp
        artifact_dir = artifact_dir_path(test_config.storage_path, artifact_path)
        original_metadata = read_metadata(artifact_dir, full_hash)
        original_timestamp = original_metadata.uploaded_at

        # Amend metadata
        result = storage_service.amend_metadata(artifact_path, hash_ref, source_uri="new-uri")

        assert result.uploaded_at == original_timestamp

        # Verify persisted
        updated_metadata = read_metadata(artifact_dir, full_hash)
        assert updated_metadata.uploaded_at == original_timestamp

    def test_amend_metadata_with_none_leaves_unchanged(
        self, storage_service: StorageService, test_config: MagpieSettings
    ) -> None:
        """amend_metadata with None source_uri should leave it unchanged."""
        artifact_path = "test/none-unchanged"
        full_hash, hash_ref = store_test_artifact(
            storage_service, artifact_path, b"content", source_uri="original-uri"
        )

        # Amend with None (should not change source_uri)
        result = storage_service.amend_metadata(artifact_path, hash_ref, source_uri=None)

        assert result.source_uri == "original-uri"

        # Verify persisted
        artifact_dir = artifact_dir_path(test_config.storage_path, artifact_path)
        metadata = read_metadata(artifact_dir, full_hash)
        assert metadata.source_uri == "original-uri"

    def test_amend_metadata_raises_for_missing_ref(self, storage_service: StorageService) -> None:
        """amend_metadata should raise ArtifactNotFoundError for missing ref."""
        artifact_path = "test/missing"
        # Store an artifact so the directory exists
        store_test_artifact(storage_service, artifact_path, b"content")

        with pytest.raises(ArtifactNotFoundError):
            storage_service.amend_metadata(artifact_path, "@deadbeef", source_uri="uri")

    def test_amend_metadata_raises_for_missing_path(self, storage_service: StorageService) -> None:
        """amend_metadata should raise for non-existent artifact path."""
        with pytest.raises(ArtifactNotFoundError):
            storage_service.amend_metadata("nonexistent/path", "@abc123", source_uri="uri")


class TestAmendMetadataMultiple:
    """Tests for multiple amend operations."""

    def test_multiple_amends_work_correctly(
        self, storage_service: StorageService, test_config: MagpieSettings
    ) -> None:
        """Multiple amend operations should work correctly."""
        artifact_path = "test/multiple-amends"
        full_hash, hash_ref = store_test_artifact(
            storage_service, artifact_path, b"content", source_uri="uri-1"
        )

        # First amend
        result1 = storage_service.amend_metadata(artifact_path, hash_ref, source_uri="uri-2")
        assert result1.source_uri == "uri-2"

        # Second amend
        result2 = storage_service.amend_metadata(artifact_path, hash_ref, source_uri="uri-3")
        assert result2.source_uri == "uri-3"

        # Third amend
        result3 = storage_service.amend_metadata(artifact_path, hash_ref, source_uri="uri-4")
        assert result3.source_uri == "uri-4"

        # Verify final state
        artifact_dir = artifact_dir_path(test_config.storage_path, artifact_path)
        metadata = read_metadata(artifact_dir, full_hash)
        assert metadata.source_uri == "uri-4"

        # Immutable fields should be preserved through all amends
        assert metadata.hash == full_hash
        assert metadata.uploaded_by == "test-user"

    def test_amend_can_set_and_change_source_uri(self, storage_service: StorageService) -> None:
        """amend_metadata can set source_uri when originally None, then change it."""
        artifact_path = "test/set-then-change"
        _, hash_ref = store_test_artifact(
            storage_service, artifact_path, b"content", source_uri=None
        )

        # Initially None
        info = storage_service.get_artifact_info(artifact_path, hash_ref)
        assert info.source_uri is None

        # Set source_uri
        result1 = storage_service.amend_metadata(artifact_path, hash_ref, source_uri="first-uri")
        assert result1.source_uri == "first-uri"

        # Change source_uri
        result2 = storage_service.amend_metadata(artifact_path, hash_ref, source_uri="second-uri")
        assert result2.source_uri == "second-uri"


class TestAmendMetadataArtifactInfo:
    """Tests for ArtifactInfo returned by amend_metadata."""

    def test_amend_returns_complete_artifact_info(self, storage_service: StorageService) -> None:
        """amend_metadata should return complete ArtifactInfo."""
        artifact_path = "test/complete-info"
        full_hash, hash_ref = store_test_artifact(
            storage_service, artifact_path, b"content", source_uri="original"
        )

        result = storage_service.amend_metadata(artifact_path, hash_ref, source_uri="updated")

        assert result.hash == full_hash
        assert result.hash_ref == hash_ref
        assert result.uploaded_by == "test-user"
        assert result.uploaded_at is not None
        assert result.source_uri == "updated"
        assert "latest" in result.tags

    def test_amend_preserves_tags_in_result(self, storage_service: StorageService) -> None:
        """amend_metadata should include all tags in returned ArtifactInfo."""
        artifact_path = "test/with-tags"
        _, hash_ref = store_test_artifact(storage_service, artifact_path, b"content")

        # Add additional tags
        storage_service.create_tag(artifact_path, hash_ref, "stable")
        storage_service.create_tag(artifact_path, hash_ref, "v1.0")

        result = storage_service.amend_metadata(artifact_path, hash_ref, source_uri="uri")

        assert "latest" in result.tags
        assert "stable" in result.tags
        assert "v1.0" in result.tags
