"""Unit tests for blob storage operations."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from magpie.config import MagpieSettings
from magpie.storage.blob import (
    check_blob_exists,
    get_temp_path,
    read_blob,
    store_blob,
    store_blob_from_temp,
)
from magpie.storage.exceptions import (
    AmbiguousHashRefError,
    ArtifactNotFoundError,
    HashPrefixCollisionError,
    InvalidArtifactPathError,
)
from magpie.storage.hash import HASH_NAME_LENGTH, compute_hash, short_hash


@pytest.fixture
def test_config(tmp_path: Path) -> MagpieSettings:
    """Create test configuration with temporary paths."""
    return MagpieSettings(
        storage_path=tmp_path,
        database_path=tmp_path / "magpie.db",
    )


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

    def test_store_blob_creates_file(self, artifact_dir: Path, test_config: MagpieSettings) -> None:
        """store_blob should create blob file at correct path."""
        content = b"test blob content"
        stream = io.BytesIO(content)

        full_hash, hash_ref, is_duplicate = store_blob(artifact_dir, stream, test_config)

        # Verify file was created (blob is named for the leading hash chars)
        expected_path = artifact_dir / "blobs" / full_hash[:HASH_NAME_LENGTH]
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

        # Verify content integrity (blob is named for the leading hash chars)
        blob_file = artifact_dir / "blobs" / full_hash[:HASH_NAME_LENGTH]
        assert blob_file.read_bytes() == content


class TestReadBlob:
    """Tests for read_blob function."""

    def test_read_blob_returns_path(self, artifact_dir: Path, test_config: MagpieSettings) -> None:
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

    def test_check_with_at_prefix(self, artifact_dir: Path, test_config: MagpieSettings) -> None:
        """check_blob_exists should handle @ prefix."""
        content = b"prefix check content"
        stream = io.BytesIO(content)
        full_hash, hash_ref, _ = store_blob(artifact_dir, stream, test_config)

        # Works with @ prefix (strips prefix and uses first 8 chars)
        result = check_blob_exists(artifact_dir, hash_ref)

        assert result is True


def _write_blob_file(artifact_dir: Path, name: str, content: bytes) -> Path:
    """Plant a blob file directly on disk under an arbitrary filename.

    Used to build synthetic prefix collisions and pre-widening (8-char)
    layouts without grinding real SHA-256 prefixes.
    """
    blobs_dir = artifact_dir / "blobs"
    blobs_dir.mkdir(parents=True, exist_ok=True)
    path = blobs_dir / name
    path.write_bytes(content)
    return path


def _write_sidecar(artifact_dir: Path, name: str, full_hash: str) -> Path:
    """Plant a metadata sidecar recording a given full hash."""
    metadata_dir = artifact_dir / "metadata"
    metadata_dir.mkdir(parents=True, exist_ok=True)
    path = metadata_dir / f"{name}.json"
    path.write_text(json.dumps({"hash": full_hash}))
    return path


class TestStoreBlobPrefixCollision:
    """Both store paths must verify the full hash, not just the filename.

    Regression tests for issue #529: a filename match alone was treated as
    a duplicate by store_blob() and as a bare ValueError (opaque HTTP 500)
    by store_blob_from_temp().
    """

    def test_store_blob_refuses_collision_on_current_width(
        self, artifact_dir: Path, test_config: MagpieSettings
    ) -> None:
        """store_blob must refuse, not silently report a duplicate."""
        content = b"the content being uploaded"
        squatter = b"unrelated content already stored here"
        full_hash = compute_hash(content)
        planted = _write_blob_file(artifact_dir, full_hash[:HASH_NAME_LENGTH], squatter)

        with pytest.raises(HashPrefixCollisionError) as exc_info:
            store_blob(artifact_dir, io.BytesIO(content), test_config)

        assert full_hash in str(exc_info.value)
        # The stored blob is never overwritten.
        assert planted.read_bytes() == squatter

    def test_store_blob_from_temp_refuses_collision_on_current_width(
        self, artifact_dir: Path, tmp_path: Path
    ) -> None:
        """store_blob_from_temp must raise the same typed error as store_blob."""
        content = b"the content being uploaded"
        squatter = b"unrelated content already stored here"
        full_hash = compute_hash(content)
        planted = _write_blob_file(artifact_dir, full_hash[:HASH_NAME_LENGTH], squatter)

        temp_file = tmp_path / "upload.tmp"
        temp_file.write_bytes(content)

        with pytest.raises(HashPrefixCollisionError):
            store_blob_from_temp(artifact_dir, temp_file, full_hash)

        assert planted.read_bytes() == squatter

    def test_collision_cleans_up_temp_file(self, artifact_dir: Path, tmp_path: Path) -> None:
        """A refused write must not leak the temp file."""
        content = b"the content being uploaded"
        full_hash = compute_hash(content)
        _write_blob_file(artifact_dir, full_hash[:HASH_NAME_LENGTH], b"squatter")

        temp_file = tmp_path / "upload.tmp"
        temp_file.write_bytes(content)

        with pytest.raises(HashPrefixCollisionError):
            store_blob_from_temp(artifact_dir, temp_file, full_hash)

        assert not temp_file.exists()

    def test_store_blob_collision_cleans_up_temp_dir(
        self, artifact_dir: Path, test_config: MagpieSettings
    ) -> None:
        """store_blob's own temp file must not survive a refused write."""
        content = b"the content being uploaded"
        full_hash = compute_hash(content)
        _write_blob_file(artifact_dir, full_hash[:HASH_NAME_LENGTH], b"squatter")

        with pytest.raises(HashPrefixCollisionError):
            store_blob(artifact_dir, io.BytesIO(content), test_config)

        assert list(test_config.temp_path.glob("blob_*.tmp")) == []

    def test_narrow_prefix_collision_does_not_block_upload(
        self, artifact_dir: Path, test_config: MagpieSettings
    ) -> None:
        """The availability half of #529.

        A pre-widening blob that shares only the first 8 characters is
        unrelated content: the upload gets its own current-width filename
        rather than being permanently refused.
        """
        content = b"the content being uploaded"
        full_hash = compute_hash(content)
        legacy = _write_blob_file(artifact_dir, full_hash[:8], b"different content, same 8 chars")

        stored_hash, hash_ref, is_duplicate = store_blob(
            artifact_dir, io.BytesIO(content), test_config
        )

        assert stored_hash == full_hash
        assert is_duplicate is False
        assert hash_ref == f"@{full_hash[:HASH_NAME_LENGTH]}"
        stored = artifact_dir / "blobs" / full_hash[:HASH_NAME_LENGTH]
        assert stored.read_bytes() == content
        assert legacy.read_bytes() == b"different content, same 8 chars"

    def test_store_blob_from_temp_true_duplicate(
        self, artifact_dir: Path, test_config: MagpieSettings, tmp_path: Path
    ) -> None:
        """Identical content is a duplicate once the full hash verifies."""
        content = b"duplicate content"
        full_hash, _, _ = store_blob(artifact_dir, io.BytesIO(content), test_config)

        temp_file = tmp_path / "upload.tmp"
        temp_file.write_bytes(content)

        hash_ref, is_duplicate = store_blob_from_temp(artifact_dir, temp_file, full_hash)

        assert is_duplicate is True
        assert hash_ref == short_hash(full_hash)
        assert not temp_file.exists()


class TestLegacyHashNameLayout:
    """Blobs written under the pre-widening 8-char layout keep resolving."""

    def test_read_blob_resolves_legacy_name(self, artifact_dir: Path) -> None:
        """A legacy blob is found by full hash, current-width ref, and its own name."""
        content = b"stored by an older release"
        full_hash = compute_hash(content)
        legacy = _write_blob_file(artifact_dir, full_hash[:8], content)

        assert read_blob(artifact_dir, full_hash) == legacy
        assert read_blob(artifact_dir, short_hash(full_hash)) == legacy
        assert read_blob(artifact_dir, f"@{full_hash[:8]}") == legacy
        assert check_blob_exists(artifact_dir, full_hash) is True

    def test_legacy_blob_is_a_duplicate_not_a_second_copy(
        self, artifact_dir: Path, test_config: MagpieSettings
    ) -> None:
        """Re-uploading content already stored under the legacy name stores nothing new."""
        content = b"stored by an older release"
        full_hash = compute_hash(content)
        _write_blob_file(artifact_dir, full_hash[:8], content)

        stored_hash, hash_ref, is_duplicate = store_blob(
            artifact_dir, io.BytesIO(content), test_config
        )

        assert stored_hash == full_hash
        assert is_duplicate is True
        assert hash_ref == short_hash(full_hash)
        assert {p.name for p in (artifact_dir / "blobs").iterdir()} == {full_hash[:8]}

    def test_long_ref_does_not_adopt_an_unrelated_legacy_blob(self, artifact_dir: Path) -> None:
        """Sharing the legacy 8 chars is not enough to satisfy a longer ref.

        The characters past the legacy width are what distinguish the two
        blobs, so they must be verified against the stored blob rather than
        truncated away.
        """
        content = b"the content being asked for"
        full_hash = compute_hash(content)
        planted = _write_blob_file(artifact_dir, full_hash[:8], b"an unrelated older blob")

        with pytest.raises(ArtifactNotFoundError):
            read_blob(artifact_dir, full_hash)
        assert check_blob_exists(artifact_dir, full_hash) is False
        assert planted.read_bytes() == b"an unrelated older blob"

    def test_long_ref_trusts_the_sidecar_digest(self, artifact_dir: Path) -> None:
        """A legacy sidecar's recorded digest resolves the ref without rehashing."""
        content = b"stored by an older release"
        full_hash = compute_hash(content)
        legacy = _write_blob_file(artifact_dir, full_hash[:8], content)
        _write_sidecar(artifact_dir, full_hash[:8], full_hash)

        assert read_blob(artifact_dir, full_hash) == legacy

        other_hash = compute_hash(b"a different blob entirely")
        _write_sidecar(artifact_dir, full_hash[:8], other_hash)

        with pytest.raises(ArtifactNotFoundError):
            read_blob(artifact_dir, full_hash)

    def test_traversal_ref_is_refused(self, artifact_dir: Path) -> None:
        """A reference is a filename component, never a path."""
        for ref in ("@../../../../etc/passwd", "@..", "@ab/cd"):
            with pytest.raises(InvalidArtifactPathError):
                read_blob(artifact_dir, ref)

    def test_ambiguous_abbreviated_ref_is_rejected(self, artifact_dir: Path) -> None:
        """An abbreviation matching two blobs is an error, not an arbitrary pick."""
        _write_blob_file(artifact_dir, "abcdef0123456789", b"one")
        _write_blob_file(artifact_dir, "abcdef0199999999", b"two")

        with pytest.raises(AmbiguousHashRefError):
            read_blob(artifact_dir, "@abcdef01")
