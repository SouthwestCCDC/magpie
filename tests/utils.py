"""Test utilities for Magpie tests."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any


def create_test_artifact(content: bytes | None = None) -> bytes:
    """Generate test artifact content.

    Args:
        content: Optional specific content. If None, generates default test content.

    Returns:
        Bytes representing test artifact content.
    """
    if content is not None:
        return content
    return b"default test artifact content"


def verify_blob_integrity(blob_path: Path, expected_hash: str) -> bool:
    """Verify that a blob file's SHA-256 hash matches the expected value.

    Args:
        blob_path: Path to the blob file.
        expected_hash: Expected SHA-256 hash (hex string).

    Returns:
        True if the hash matches, False otherwise.
    """
    if not blob_path.exists():
        return False

    actual_hash = hashlib.sha256(blob_path.read_bytes()).hexdigest()
    return actual_hash == expected_hash


def assert_manifest_valid(manifest: dict[str, Any]) -> None:
    """Assert that a manifest has the required structure.

    A valid manifest must contain:
    - 'hash': Content hash of the artifact (str)
    - 'size': Size of the artifact in bytes (int)
    - 'created_at': Timestamp of creation (str, ISO format)

    Args:
        manifest: The manifest dictionary to validate.

    Raises:
        AssertionError: If the manifest is missing required fields or has invalid types.
    """
    required_fields = {"hash", "size", "created_at"}
    actual_fields = set(manifest.keys())
    missing = required_fields - actual_fields
    assert not missing, f"Manifest missing required fields: {missing}"

    assert isinstance(manifest["hash"], str), "Manifest 'hash' must be a string"
    assert isinstance(manifest["size"], int), "Manifest 'size' must be an integer"
    assert isinstance(manifest["created_at"], str), "Manifest 'created_at' must be a string"
    assert manifest["size"] >= 0, "Manifest 'size' must be non-negative"
