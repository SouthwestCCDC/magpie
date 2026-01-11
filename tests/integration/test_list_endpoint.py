"""Integration tests for the list artifacts endpoint."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from magpie.config import MagpieSettings
from magpie.server.app import app
from magpie.server.deps import get_storage_service
from magpie.storage.service import StorageService


@pytest.fixture
def test_config(tmp_path: Path) -> MagpieSettings:
    """Create test configuration with temporary paths."""
    config = MagpieSettings(storage_path=tmp_path)
    config.temp_path.mkdir(parents=True, exist_ok=True)
    return config


@pytest.fixture
def test_storage_service(test_config: MagpieSettings) -> StorageService:
    """Create a StorageService instance for testing."""
    return StorageService(test_config)


@pytest.fixture
def client(test_storage_service: StorageService) -> TestClient:
    """Create test client with overridden storage service dependency."""

    def override_storage_service() -> StorageService:
        return test_storage_service

    app.dependency_overrides[get_storage_service] = override_storage_service
    yield TestClient(app)
    app.dependency_overrides.clear()


class TestListArtifactsEndpoint:
    """Tests for GET /api/v1/artifacts/{path} endpoint."""

    def test_list_existing_artifact_single_version(self, client: TestClient) -> None:
        """List artifact with single version returns correct data."""
        # Upload an artifact first
        content = b"test content for listing"
        files = {"file": ("artifact.bin", io.BytesIO(content), "application/octet-stream")}
        upload_response = client.post(
            "/api/v1/upload/list-test/single",
            files=files,
            params={"uploaded_by": "test-user", "source_uri": "http://example.com"},
        )
        assert upload_response.status_code == 200
        upload_data = upload_response.json()

        # List artifacts
        response = client.get("/api/v1/artifacts/list-test/single")

        assert response.status_code == 200
        data = response.json()

        assert data["artifact_path"] == "list-test/single"
        assert len(data["versions"]) == 1

        version = data["versions"][0]
        assert version["hash"] == upload_data["hash"]
        assert version["hash_ref"] == upload_data["hash_ref"]
        assert "latest" in version["tags"]
        assert version["uploaded_by"] == "test-user"
        assert version["source_uri"] == "http://example.com"
        assert "uploaded_at" in version

    def test_list_nonexistent_path_returns_empty(self, client: TestClient) -> None:
        """List nonexistent artifact path returns empty versions list."""
        response = client.get("/api/v1/artifacts/nonexistent/path")

        assert response.status_code == 200
        data = response.json()

        assert data["artifact_path"] == "nonexistent/path"
        assert data["versions"] == []

    def test_list_empty_artifact_returns_empty(self, client: TestClient) -> None:
        """List artifact path with no uploads returns empty versions list."""
        # This is the same as nonexistent - both return empty
        response = client.get("/api/v1/artifacts/never-uploaded/artifact")

        assert response.status_code == 200
        data = response.json()

        assert data["artifact_path"] == "never-uploaded/artifact"
        assert data["versions"] == []


class TestMultipleVersions:
    """Tests for artifacts with multiple versions."""

    def test_list_multiple_versions(
        self, client: TestClient, test_storage_service: StorageService
    ) -> None:
        """List artifact with multiple distinct versions shows all versions.

        All blobs are listed, including those that have lost their tags.
        The version with "latest" tag shows it, untagged versions show empty tags.
        """
        artifact_path = "multi/version"

        # Upload first version
        files1 = {"file": ("v1.bin", io.BytesIO(b"version 1 content"), "application/octet-stream")}
        response1 = client.post(f"/api/v1/upload/{artifact_path}", files=files1)
        assert response1.status_code == 200
        data1 = response1.json()

        # Upload second version (different content)
        files2 = {"file": ("v2.bin", io.BytesIO(b"version 2 content"), "application/octet-stream")}
        response2 = client.post(f"/api/v1/upload/{artifact_path}", files=files2)
        assert response2.status_code == 200
        data2 = response2.json()

        # List should show all versions
        response = client.get(f"/api/v1/artifacts/{artifact_path}")
        assert response.status_code == 200
        data = response.json()

        # Both versions should be visible
        assert len(data["versions"]) == 2

        # Build lookup by hash
        versions_by_hash = {v["hash"]: v for v in data["versions"]}

        # v1 should be untagged (empty list)
        assert data1["hash"] in versions_by_hash
        assert versions_by_hash[data1["hash"]]["tags"] == []

        # v2 should have "latest" tag
        assert data2["hash"] in versions_by_hash
        assert "latest" in versions_by_hash[data2["hash"]]["tags"]

    def test_duplicate_upload_same_version(self, client: TestClient) -> None:
        """Duplicate upload doesn't create additional versions."""
        artifact_path = "dup/version"
        content = b"duplicate content"

        # Upload twice
        files1 = {"file": ("f1.bin", io.BytesIO(content), "application/octet-stream")}
        files2 = {"file": ("f2.bin", io.BytesIO(content), "application/octet-stream")}

        response1 = client.post(f"/api/v1/upload/{artifact_path}", files=files1)
        response2 = client.post(f"/api/v1/upload/{artifact_path}", files=files2)

        assert response1.status_code == 200
        assert response2.status_code == 200
        assert response2.json()["is_duplicate"] is True

        # List should show single version
        response = client.get(f"/api/v1/artifacts/{artifact_path}")
        data = response.json()

        assert len(data["versions"]) == 1


class TestVersionMetadata:
    """Tests for version metadata correctness."""

    def test_versions_include_correct_tags(self, client: TestClient) -> None:
        """Version info includes correct tags."""
        content = b"tag test content"
        files = {"file": ("artifact.bin", io.BytesIO(content), "application/octet-stream")}

        client.post("/api/v1/upload/tag/test", files=files)

        response = client.get("/api/v1/artifacts/tag/test")
        data = response.json()

        assert len(data["versions"]) == 1
        # Default tag is "latest"
        assert "latest" in data["versions"][0]["tags"]

    def test_versions_include_uploaded_at(self, client: TestClient) -> None:
        """Version info includes uploaded_at timestamp."""
        content = b"timestamp test content"
        files = {"file": ("artifact.bin", io.BytesIO(content), "application/octet-stream")}

        client.post("/api/v1/upload/timestamp/test", files=files)

        response = client.get("/api/v1/artifacts/timestamp/test")
        data = response.json()

        assert len(data["versions"]) == 1
        # Should be ISO format datetime string
        assert "uploaded_at" in data["versions"][0]
        assert "T" in data["versions"][0]["uploaded_at"]  # ISO format has T separator

    def test_versions_include_hash_ref(self, client: TestClient) -> None:
        """Version info includes hash_ref starting with @."""
        content = b"hash ref test content"
        files = {"file": ("artifact.bin", io.BytesIO(content), "application/octet-stream")}

        client.post("/api/v1/upload/hashref/test", files=files)

        response = client.get("/api/v1/artifacts/hashref/test")
        data = response.json()

        assert len(data["versions"]) == 1
        assert data["versions"][0]["hash_ref"].startswith("@")
        assert len(data["versions"][0]["hash_ref"]) == 9  # @ + 8 chars


class TestResponseFormat:
    """Tests for response format."""

    def test_response_content_type(self, client: TestClient) -> None:
        """Response should be JSON."""
        response = client.get("/api/v1/artifacts/format/test")
        assert response.headers["content-type"] == "application/json"

    def test_response_structure(self, client: TestClient) -> None:
        """Response has correct structure."""
        response = client.get("/api/v1/artifacts/structure/test")
        data = response.json()

        assert "artifact_path" in data
        assert "versions" in data
        assert isinstance(data["versions"], list)


class TestListPathsEndpoint:
    """Tests for GET /api/v1/artifacts (no path - list artifact paths)."""

    def test_list_all_artifact_paths(self, client: TestClient) -> None:
        """List all artifact paths returns all paths with manifests."""
        # Upload artifacts at different paths
        files1 = {"file": ("f1.bin", io.BytesIO(b"content1"), "application/octet-stream")}
        files2 = {"file": ("f2.bin", io.BytesIO(b"content2"), "application/octet-stream")}
        files3 = {"file": ("f3.bin", io.BytesIO(b"content3"), "application/octet-stream")}

        client.post("/api/v1/upload/test/artifact1", files=files1)
        client.post("/api/v1/upload/test/artifact2", files=files2)
        client.post("/api/v1/upload/images/ubuntu", files=files3)

        # List all paths
        response = client.get("/api/v1/artifacts")

        assert response.status_code == 200
        data = response.json()

        assert "paths" in data
        assert len(data["paths"]) == 3
        assert "test/artifact1" in data["paths"]
        assert "test/artifact2" in data["paths"]
        assert "images/ubuntu" in data["paths"]
        # Should be sorted
        assert data["paths"] == sorted(data["paths"])

    def test_list_paths_with_prefix(self, client: TestClient) -> None:
        """List paths with prefix filters correctly."""
        # Upload artifacts
        files1 = {"file": ("f1.bin", io.BytesIO(b"content1"), "application/octet-stream")}
        files2 = {"file": ("f2.bin", io.BytesIO(b"content2"), "application/octet-stream")}
        files3 = {"file": ("f3.bin", io.BytesIO(b"content3"), "application/octet-stream")}

        client.post("/api/v1/upload/test/artifact1", files=files1)
        client.post("/api/v1/upload/test/artifact2", files=files2)
        client.post("/api/v1/upload/images/ubuntu", files=files3)

        # List with prefix
        response = client.get("/api/v1/artifacts", params={"prefix": "test"})

        assert response.status_code == 200
        data = response.json()

        assert len(data["paths"]) == 2
        assert "test/artifact1" in data["paths"]
        assert "test/artifact2" in data["paths"]
        assert "images/ubuntu" not in data["paths"]

    def test_list_paths_normalizes_leading_slash(self, client: TestClient) -> None:
        """List paths normalizes leading slash in prefix."""
        files = {"file": ("f.bin", io.BytesIO(b"content"), "application/octet-stream")}
        client.post("/api/v1/upload/test/artifact", files=files)

        # Leading slash should be normalized
        response = client.get("/api/v1/artifacts", params={"prefix": "/test"})

        assert response.status_code == 200
        data = response.json()

        assert len(data["paths"]) == 1
        assert "test/artifact" in data["paths"]

    def test_list_paths_empty_storage(self, client: TestClient) -> None:
        """List paths with no artifacts returns empty list."""
        response = client.get("/api/v1/artifacts")

        assert response.status_code == 200
        data = response.json()

        assert data["paths"] == []

    def test_list_paths_no_matching_prefix(self, client: TestClient) -> None:
        """List paths with non-matching prefix returns empty list."""
        files = {"file": ("f.bin", io.BytesIO(b"content"), "application/octet-stream")}
        client.post("/api/v1/upload/test/artifact", files=files)

        response = client.get("/api/v1/artifacts", params={"prefix": "images"})

        assert response.status_code == 200
        data = response.json()

        assert data["paths"] == []

    def test_list_paths_response_structure(self, client: TestClient) -> None:
        """List paths response has correct structure."""
        response = client.get("/api/v1/artifacts")

        assert response.status_code == 200
        data = response.json()

        assert "paths" in data
        assert isinstance(data["paths"], list)
