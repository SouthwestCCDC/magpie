"""Unit tests for metadata sidecar operations."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from magpie.storage.exceptions import ArtifactNotFoundError
from magpie.storage.metadata import BlobMetadata, read_metadata, write_metadata
from magpie.storage.paths import metadata_path


class TestBlobMetadataModel:
    """Tests for BlobMetadata Pydantic model."""

    def test_required_fields(self) -> None:
        """BlobMetadata should require hash, uploaded_by, uploaded_at."""
        now = datetime.now(timezone.utc)
        metadata = BlobMetadata(
            hash="abc123",
            uploaded_by="test-user",
            uploaded_at=now,
        )
        assert metadata.hash == "abc123"
        assert metadata.uploaded_by == "test-user"
        assert metadata.uploaded_at == now

    def test_optional_source_uri_none(self) -> None:
        """source_uri should default to None."""
        metadata = BlobMetadata(
            hash="abc123",
            uploaded_by="test-user",
            uploaded_at=datetime.now(timezone.utc),
        )
        assert metadata.source_uri is None

    def test_optional_source_uri_with_value(self) -> None:
        """source_uri should accept a string value."""
        metadata = BlobMetadata(
            hash="abc123",
            uploaded_by="test-user",
            uploaded_at=datetime.now(timezone.utc),
            source_uri="s3://bucket/key",
        )
        assert metadata.source_uri == "s3://bucket/key"


class TestWriteReadMetadata:
    """Tests for write_metadata and read_metadata functions."""

    def test_write_read_roundtrip(self, tmp_path: Path) -> None:
        """Writing and reading metadata should preserve all fields."""
        artifact_dir = tmp_path / "artifact"
        artifact_dir.mkdir()
        hash_ref = "abc12345"
        now = datetime.now(timezone.utc)

        metadata = BlobMetadata(
            hash="abc12345full",
            uploaded_by="test-user",
            uploaded_at=now,
            source_uri="http://example.com/file",
        )

        write_metadata(artifact_dir, hash_ref, metadata)
        loaded = read_metadata(artifact_dir, hash_ref)

        assert loaded.hash == metadata.hash
        assert loaded.uploaded_by == metadata.uploaded_by
        assert loaded.source_uri == metadata.source_uri

    def test_write_creates_metadata_dir(self, tmp_path: Path) -> None:
        """write_metadata should create metadata directory if missing."""
        artifact_dir = tmp_path / "artifact"
        # Don't create artifact_dir, let write_metadata handle it
        hash_ref = "abc12345"

        metadata = BlobMetadata(
            hash="abc12345full",
            uploaded_by="test-user",
            uploaded_at=datetime.now(timezone.utc),
        )

        write_metadata(artifact_dir, hash_ref, metadata)

        path = metadata_path(artifact_dir, hash_ref)
        assert path.exists()
        assert path.parent.name == "metadata"

    def test_read_missing_raises_artifact_not_found(self, tmp_path: Path) -> None:
        """read_metadata should raise ArtifactNotFoundError for missing file."""
        artifact_dir = tmp_path / "artifact"
        artifact_dir.mkdir()

        with pytest.raises(ArtifactNotFoundError, match="Metadata not found"):
            read_metadata(artifact_dir, "nonexistent")


class TestDatetimeSerialization:
    """Tests for datetime ISO 8601 serialization."""

    def test_datetime_serialized_as_iso8601(self, tmp_path: Path) -> None:
        """uploaded_at should serialize to ISO 8601 format in JSON."""
        artifact_dir = tmp_path / "artifact"
        hash_ref = "abc12345"
        now = datetime(2024, 6, 15, 12, 30, 45, tzinfo=timezone.utc)

        metadata = BlobMetadata(
            hash="abc12345full",
            uploaded_by="test-user",
            uploaded_at=now,
        )

        write_metadata(artifact_dir, hash_ref, metadata)

        # Read raw JSON to verify format
        path = metadata_path(artifact_dir, hash_ref)
        content = path.read_text()
        data = json.loads(content)

        # Pydantic v2 serializes datetime to ISO 8601 string
        assert isinstance(data["uploaded_at"], str)
        assert "2024-06-15" in data["uploaded_at"]

    def test_datetime_roundtrip_preserves_value(self, tmp_path: Path) -> None:
        """Datetime should survive write/read roundtrip."""
        artifact_dir = tmp_path / "artifact"
        hash_ref = "abc12345"
        now = datetime(2024, 6, 15, 12, 30, 45, tzinfo=timezone.utc)

        metadata = BlobMetadata(
            hash="abc12345full",
            uploaded_by="test-user",
            uploaded_at=now,
        )

        write_metadata(artifact_dir, hash_ref, metadata)
        loaded = read_metadata(artifact_dir, hash_ref)

        # Compare as timestamps to avoid timezone representation differences
        assert loaded.uploaded_at.timestamp() == now.timestamp()

    def test_datetime_parsed_from_iso_string(self, tmp_path: Path) -> None:
        """read_metadata should parse ISO 8601 strings back to datetime."""
        artifact_dir = tmp_path / "artifact"
        hash_ref = "abc12345"

        metadata = BlobMetadata(
            hash="abc12345full",
            uploaded_by="test-user",
            uploaded_at=datetime.now(timezone.utc),
        )

        write_metadata(artifact_dir, hash_ref, metadata)
        loaded = read_metadata(artifact_dir, hash_ref)

        assert isinstance(loaded.uploaded_at, datetime)


class TestWriteOnceBehavior:
    """Tests for write-once metadata behavior."""

    def test_second_write_does_not_overwrite(self, tmp_path: Path) -> None:
        """Second write_metadata call should not overwrite existing file."""
        artifact_dir = tmp_path / "artifact"
        hash_ref = "abc12345"
        original_time = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        new_time = datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)

        # First write
        original_metadata = BlobMetadata(
            hash="abc12345full",
            uploaded_by="original-user",
            uploaded_at=original_time,
            source_uri="original-source",
        )
        write_metadata(artifact_dir, hash_ref, original_metadata)

        # Second write with different data
        new_metadata = BlobMetadata(
            hash="abc12345full",
            uploaded_by="new-user",
            uploaded_at=new_time,
            source_uri="new-source",
        )
        write_metadata(artifact_dir, hash_ref, new_metadata)

        # Read should return original data
        loaded = read_metadata(artifact_dir, hash_ref)
        assert loaded.uploaded_by == "original-user"
        assert loaded.source_uri == "original-source"
        assert loaded.uploaded_at.timestamp() == original_time.timestamp()

    def test_write_once_preserves_original_timestamp(self, tmp_path: Path) -> None:
        """Write-once should preserve original upload timestamp."""
        artifact_dir = tmp_path / "artifact"
        hash_ref = "abc12345"

        # Original with specific timestamp
        original = BlobMetadata(
            hash="abc12345full",
            uploaded_by="user1",
            uploaded_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
        )
        write_metadata(artifact_dir, hash_ref, original)

        # Attempt to overwrite with new timestamp
        newer = BlobMetadata(
            hash="abc12345full",
            uploaded_by="user2",
            uploaded_at=datetime(2024, 12, 31, tzinfo=timezone.utc),
        )
        write_metadata(artifact_dir, hash_ref, newer)

        loaded = read_metadata(artifact_dir, hash_ref)
        assert loaded.uploaded_at.year == 2020


class TestHashRefHandling:
    """Tests for hash reference format handling."""

    def test_hash_ref_with_at_prefix(self, tmp_path: Path) -> None:
        """write/read should work with @ prefixed hash refs."""
        artifact_dir = tmp_path / "artifact"
        hash_ref = "@abc12345"

        metadata = BlobMetadata(
            hash="abc12345full",
            uploaded_by="test-user",
            uploaded_at=datetime.now(timezone.utc),
        )

        write_metadata(artifact_dir, hash_ref, metadata)
        loaded = read_metadata(artifact_dir, hash_ref)

        assert loaded.hash == metadata.hash

    def test_hash_ref_without_prefix(self, tmp_path: Path) -> None:
        """write/read should work with hash refs without @ prefix."""
        artifact_dir = tmp_path / "artifact"
        hash_ref = "abc12345"

        metadata = BlobMetadata(
            hash="abc12345full",
            uploaded_by="test-user",
            uploaded_at=datetime.now(timezone.utc),
        )

        write_metadata(artifact_dir, hash_ref, metadata)
        loaded = read_metadata(artifact_dir, hash_ref)

        assert loaded.hash == metadata.hash
