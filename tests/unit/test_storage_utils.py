"""Unit tests for storage utilities: hash and paths."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from magpie.storage.hash import HASH_NAME_LENGTH, compute_hash, short_hash
from magpie.storage.paths import (
    artifact_dir_path,
    blob_path,
    manifest_path,
    metadata_path,
)


class TestComputeHash:
    """Tests for compute_hash function."""

    # Known SHA-256 hash for "hello world"
    HELLO_WORLD_HASH = "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"

    def test_compute_hash_bytes(self) -> None:
        """compute_hash should return correct SHA-256 for bytes input."""
        result = compute_hash(b"hello world")
        assert result == self.HELLO_WORLD_HASH

    def test_compute_hash_empty_bytes(self) -> None:
        """compute_hash should handle empty bytes."""
        # SHA-256 of empty string
        expected = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        result = compute_hash(b"")
        assert result == expected

    def test_compute_hash_file_object(self) -> None:
        """compute_hash should work with file-like objects."""
        file_obj = io.BytesIO(b"hello world")
        result = compute_hash(file_obj)
        assert result == self.HELLO_WORLD_HASH

    def test_compute_hash_path(self, tmp_path: Path) -> None:
        """compute_hash should work with Path objects."""
        test_file = tmp_path / "test.bin"
        test_file.write_bytes(b"hello world")
        result = compute_hash(test_file)
        assert result == self.HELLO_WORLD_HASH

    def test_compute_hash_large_file_chunked(self, tmp_path: Path) -> None:
        """compute_hash should handle large files with chunked reading."""
        # Create a file larger than chunk size (8KB)
        large_content = b"x" * 20000
        test_file = tmp_path / "large.bin"
        test_file.write_bytes(large_content)
        result = compute_hash(test_file)
        # Verify it matches bytes computation
        assert result == compute_hash(large_content)

    def test_compute_hash_invalid_type(self) -> None:
        """compute_hash should raise TypeError for invalid input."""
        with pytest.raises(TypeError, match="Expected BinaryIO, Path, or bytes"):
            compute_hash("not a valid input")  # type: ignore[arg-type]

    def test_compute_hash_path_not_found(self, tmp_path: Path) -> None:
        """compute_hash should raise FileNotFoundError for missing file."""
        missing_file = tmp_path / "nonexistent.bin"
        with pytest.raises(FileNotFoundError):
            compute_hash(missing_file)


class TestShortHash:
    """Tests for short_hash function."""

    def test_short_hash_format(self) -> None:
        """short_hash should return @ prefix + HASH_NAME_LENGTH chars."""
        full_hash = "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"
        result = short_hash(full_hash)
        assert result == f"@{full_hash[:HASH_NAME_LENGTH]}"

    def test_short_hash_length(self) -> None:
        """short_hash should always return @ + HASH_NAME_LENGTH characters."""
        full_hash = "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"
        result = short_hash(full_hash)
        assert len(result) == HASH_NAME_LENGTH + 1

    def test_short_hash_starts_with_at(self) -> None:
        """short_hash should always start with @ symbol."""
        full_hash = "1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef"
        result = short_hash(full_hash)
        assert result.startswith("@")

    def test_short_hash_extracts_leading_chars(self) -> None:
        """short_hash should extract exactly the leading hash-name characters."""
        full_hash = "1234567890abcdefxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
        result = short_hash(full_hash)
        assert result == "@1234567890abcdef"
        assert HASH_NAME_LENGTH == 16


class TestArtifactDirPath:
    """Tests for artifact_dir_path function.

    These tests use verify_security=False because they test path construction
    logic with hypothetical paths. Security verification is tested separately
    in test_path_validation.py::TestVerifyPathIsDescendant.
    """

    def test_artifact_dir_path_simple(self) -> None:
        """artifact_dir_path should join base and artifact path."""
        base = Path("/data/artifacts")
        result = artifact_dir_path(base, "project/component", verify_security=False)
        assert result == Path("/data/artifacts/project/component")

    def test_artifact_dir_path_single_segment(self) -> None:
        """artifact_dir_path should work with single-segment paths."""
        base = Path("/storage")
        result = artifact_dir_path(base, "artifact", verify_security=False)
        assert result == Path("/storage/artifact")

    def test_artifact_dir_path_deep_nesting(self) -> None:
        """artifact_dir_path should handle deeply nested paths."""
        base = Path("/data")
        result = artifact_dir_path(base, "a/b/c/d/e", verify_security=False)
        assert result == Path("/data/a/b/c/d/e")


class TestBlobPath:
    """Tests for blob_path function."""

    def test_blob_path_with_at_prefix(self) -> None:
        """blob_path should strip @ prefix from hash_ref."""
        artifact_dir = Path("/data/artifacts/project")
        result = blob_path(artifact_dir, "@abc12345")
        assert result == Path("/data/artifacts/project/blobs/abc12345")

    def test_blob_path_without_prefix(self) -> None:
        """blob_path should work with hash_ref without @ prefix."""
        artifact_dir = Path("/data/artifacts/project")
        result = blob_path(artifact_dir, "abc12345")
        assert result == Path("/data/artifacts/project/blobs/abc12345")

    def test_blob_path_full_hash(self) -> None:
        """blob_path should truncate a full hash to the stored hash-name width."""
        artifact_dir = Path("/data/artifacts/project")
        full_hash = "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"
        result = blob_path(artifact_dir, full_hash)
        assert result == Path(f"/data/artifacts/project/blobs/{full_hash[:HASH_NAME_LENGTH]}")


class TestMetadataPath:
    """Tests for metadata_path function."""

    def test_metadata_path_with_at_prefix(self) -> None:
        """metadata_path should strip @ prefix and add .json extension."""
        artifact_dir = Path("/data/artifacts/project")
        result = metadata_path(artifact_dir, "@abc12345")
        assert result == Path("/data/artifacts/project/metadata/abc12345.json")

    def test_metadata_path_without_prefix(self) -> None:
        """metadata_path should work with hash_ref without @ prefix."""
        artifact_dir = Path("/data/artifacts/project")
        result = metadata_path(artifact_dir, "abc12345")
        assert result == Path("/data/artifacts/project/metadata/abc12345.json")

    def test_metadata_path_in_metadata_subdir(self) -> None:
        """metadata_path should always be in metadata subdirectory."""
        artifact_dir = Path("/storage/test")
        result = metadata_path(artifact_dir, "hash123")
        assert "metadata" in result.parts


class TestManifestPath:
    """Tests for manifest_path function."""

    def test_manifest_path_returns_dotmagpie(self) -> None:
        """manifest_path should return .magpie file in artifact dir."""
        artifact_dir = Path("/data/artifacts/project")
        result = manifest_path(artifact_dir)
        assert result == Path("/data/artifacts/project/.magpie")

    def test_manifest_path_name(self) -> None:
        """manifest_path should always return file named .magpie."""
        artifact_dir = Path("/any/path")
        result = manifest_path(artifact_dir)
        assert result.name == ".magpie"
