"""Mock storage implementation for unit tests."""

from __future__ import annotations

from typing import Any


class MockStorage:
    """In-memory mock storage for testing.

    Provides dict-based storage for blobs and manifests without
    requiring filesystem access.
    """

    def __init__(self) -> None:
        """Initialize empty storage dictionaries."""
        self.blobs: dict[str, bytes] = {}
        self.manifests: dict[str, dict[str, Any]] = {}

    def store_blob(self, content_hash: str, data: bytes) -> None:
        """Store blob data by its content hash."""
        self.blobs[content_hash] = data

    def get_blob(self, content_hash: str) -> bytes | None:
        """Retrieve blob data by its content hash."""
        return self.blobs.get(content_hash)

    def has_blob(self, content_hash: str) -> bool:
        """Check if a blob exists."""
        return content_hash in self.blobs

    def delete_blob(self, content_hash: str) -> bool:
        """Delete a blob by its content hash. Returns True if deleted."""
        if content_hash in self.blobs:
            del self.blobs[content_hash]
            return True
        return False

    def store_manifest(self, path: str, manifest: dict[str, Any]) -> None:
        """Store a manifest by its path."""
        self.manifests[path] = manifest

    def get_manifest(self, path: str) -> dict[str, Any] | None:
        """Retrieve a manifest by its path."""
        return self.manifests.get(path)

    def has_manifest(self, path: str) -> bool:
        """Check if a manifest exists."""
        return path in self.manifests

    def delete_manifest(self, path: str) -> bool:
        """Delete a manifest by its path. Returns True if deleted."""
        if path in self.manifests:
            del self.manifests[path]
            return True
        return False

    def list_blobs(self) -> list[str]:
        """List all blob hashes."""
        return list(self.blobs.keys())

    def list_manifests(self) -> list[str]:
        """List all manifest paths."""
        return list(self.manifests.keys())

    def clear(self) -> None:
        """Clear all stored data."""
        self.blobs.clear()
        self.manifests.clear()
