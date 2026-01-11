"""Unit tests for blob storage operations."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from magpie.config import MagpieSettings
from magpie.storage.blob import (
    check_blob_exists,
    get_temp_path,
    read_blob,
    store_blob,
)
from magpie.storage.exceptions import ArtifactNotFoundError
from magpie.storage.hash import compute_hash, short_hash


@pytest.fixture
def test_config(tmp_path: Path) -> MagpieSettings:
    """Create test configuration with temporary paths."""
    return MagpieSettings(storage_path=tmp_path)


@pytest.fixture
def artifact_dir(tmp_path: Path) -> Path:
    """Create a test artifact directory."""
    path = tmp_path / "artifacts" / "test-project"
    path.mkdir(parents=True)
    return path


class TestGetTempPath:
    """Tests for get_temp_path function."""

    def test_returns_temp_path(self, test_config: MagpieSettings) -> None:
        """get_temp_path should return config.temp_path."""
        result = get_temp_path(test_config)
        assert result == test_config.temp_path

    def test_creates_temp_dir(self, tmp_path: Path) -> None:
        """get_temp_path should create temp directory if missing."""
        config = MagpieSettings(storage_path=tmp_path)
        # Ensure temp dir doesn't exist
        assert not config.temp_path.exists()

        get_temp_path(config)

        assert config.temp_path.exists()
        assert config.temp_path.is_dir()


class TestStoreBlob:
    """Tests for store_blob function."""

    def test_store_blob_creates_file(
        self, artifact_dir: Path, test_config: MagpieSettings
    ) -> None:
        """store_blob should create blob file at correct path."""
        content = b"test blob content"
        stream = io.BytesIO(content)

        full_hash, hash_ref, is_duplicate = store_blob(artifact_dir, stream, test_config)

        # Verify file was created (blob uses first 8 chars of hash)
        expected_path = artifact_dir / "blobs" / full_hash[:8]
        assert expected_path.exists()
        assert expected_path.read_bytes() == content

    def test_store_blob_returns_correct_hash(
        self, artifact_dir: Path, test_config: MagpieSettings
    ) -> None:
        """store_blob should return correct full hash and short hash reference."""
        content = b"test blob content"
        stream = io.BytesIO(content)

        full_hash, hash_ref, is_duplicate = store_blob(artifact_dir, stream, test_config)

        expected_full = compute_hash(content)
        expected_short = short_hash(expected_full)
        assert full_hash == expected_full
        assert hash_ref == expected_short
        assert hash_ref.startswith("@")

    def test_store_blob_new_returns_not_duplicate(
        self, artifact_dir: Path, test_config: MagpieSettings
    ) -> None:
        """store_blob should return is_duplicate=False for new content."""
        content = b"unique content"
        stream = io.BytesIO(content)

        full_hash, hash_ref, is_duplicate = store_blob(artifact_dir, stream, test_config)

        assert is_duplicate is False

    def test_store_blob_duplicate_detection(
        self, artifact_dir: Path, test_config: MagpieSettings
    ) -> None:
        """store_blob should detect duplicate content."""
        content = b"duplicate content test"

        # First store
        stream1 = io.BytesIO(content)
        full_hash1, hash_ref1, is_duplicate1 = store_blob(artifact_dir, stream1, test_config)
        assert is_duplicate1 is False

        # Second store of same content
        stream2 = io.BytesIO(content)
        full_hash2, hash_ref2, is_duplicate2 = store_blob(artifact_dir, stream2, test_config)
        assert is_duplicate2 is True
        assert hash_ref1 == hash_ref2
        assert full_hash1 == full_hash2

    def test_store_blob_temp_file_cleanup_on_duplicate(
        self, artifact_dir: Path, test_config: MagpieSettings
    ) -> None:
        """store_blob should clean up temp file when duplicate detected."""
        content = b"content for cleanup test"

        # First store
        stream1 = io.BytesIO(content)
        store_blob(artifact_dir, stream1, test_config)

        # Second store
        stream2 = io.BytesIO(content)
        store_blob(artifact_dir, stream2, test_config)

        # Check no temp files left
        temp_files = list(test_config.temp_path.glob("blob_*.tmp"))
        assert len(temp_files) == 0

    def test_store_blob_temp_file_cleanup_on_success(
        self, artifact_dir: Path, test_config: MagpieSettings
    ) -> None:
        """store_blob should not leave temp files after successful store."""
        content = b"content for success test"
        stream = io.BytesIO(content)

        store_blob(artifact_dir, stream, test_config)

        # Check no temp files left
        temp_files = list(test_config.temp_path.glob("blob_*.tmp"))
        assert len(temp_files) == 0

    def test_store_blob_creates_blobs_dir(
        self, tmp_path: Path, test_config: MagpieSettings
    ) -> None:
        """store_blob should create blobs directory if missing."""
        artifact_dir = tmp_path / "new-artifact"
        # Don't create artifact_dir, let store_blob handle it
        content = b"test content"
        stream = io.BytesIO(content)

        store_blob(artifact_dir, stream, test_config)

        assert (artifact_dir / "blobs").exists()

    def test_store_blob_large_content_chunked(
        self, artifact_dir: Path, test_config: MagpieSettings
    ) -> None:
        """store_blob should handle large content with chunked streaming."""
        # Content larger than chunk size (8KB)
        content = b"x" * 50000
        stream = io.BytesIO(content)

        full_hash, hash_ref, is_duplicate = store_blob(artifact_dir, stream, test_config)

        # Verify content integrity (blob uses first 8 chars of hash)
        blob_file = artifact_dir / "blobs" / full_hash[:8]
        assert blob_file.read_bytes() == content


class TestReadBlob:
    """Tests for read_blob function."""

    def test_read_blob_returns_path(
        self, artifact_dir: Path, test_config: MagpieSettings
    ) -> None:
        """read_blob should return path for existing blob."""
        content = b"readable content"
        stream = io.BytesIO(content)
        full_hash, hash_ref, _ = store_blob(artifact_dir, stream, test_config)

        # read_blob accepts full hash and converts to short internally
        result = read_blob(artifact_dir, full_hash)

        assert result.exists()
        assert result.read_bytes() == content

    def test_read_blob_with_short_hash(
        self, artifact_dir: Path, test_config: MagpieSettings
    ) -> None:
        """read_blob should work with short hash reference."""
        content = b"short hash content"
        stream = io.BytesIO(content)
        full_hash, hash_ref, _ = store_blob(artifact_dir, stream, test_config)

        # read_blob works with short hash (strips @ prefix, uses first 8 chars)
        result = read_blob(artifact_dir, hash_ref)

        assert result.exists()

    def test_read_blob_missing_raises_error(self, artifact_dir: Path) -> None:
        """read_blob should raise ArtifactNotFoundError for missing blob."""
        with pytest.raises(ArtifactNotFoundError, match="Blob not found"):
            read_blob(artifact_dir, "nonexistent123")

    def test_read_blob_with_at_prefix(
        self, artifact_dir: Path, test_config: MagpieSettings
    ) -> None:
        """read_blob should handle hash refs with @ prefix."""
        content = b"at prefix content"
        stream = io.BytesIO(content)
        full_hash, hash_ref, _ = store_blob(artifact_dir, stream, test_config)

        # Use @ prefix - blob_path strips it and uses first 8 chars
        result = read_blob(artifact_dir, hash_ref)

        assert result.exists()


class TestCheckBlobExists:
    """Tests for check_blob_exists function."""

    def test_check_existing_blob_returns_true(
        self, artifact_dir: Path, test_config: MagpieSettings
    ) -> None:
        """check_blob_exists should return True for existing blob."""
        content = b"existing content"
        stream = io.BytesIO(content)
        full_hash, hash_ref, _ = store_blob(artifact_dir, stream, test_config)

        # check_blob_exists works with full hash (converts to short internally)
        result = check_blob_exists(artifact_dir, full_hash)

        assert result is True

    def test_check_missing_blob_returns_false(self, artifact_dir: Path) -> None:
        """check_blob_exists should return False for missing blob."""
        result = check_blob_exists(artifact_dir, "nonexistent123")
        assert result is False

    def test_check_with_at_prefix(
        self, artifact_dir: Path, test_config: MagpieSettings
    ) -> None:
        """check_blob_exists should handle @ prefix."""
        content = b"prefix check content"
        stream = io.BytesIO(content)
        full_hash, hash_ref, _ = store_blob(artifact_dir, stream, test_config)

        # Works with @ prefix (strips prefix and uses first 8 chars)
        result = check_blob_exists(artifact_dir, hash_ref)

        assert result is True
