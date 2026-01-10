"""Integration tests for the artifact info endpoint."""

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
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


class TestGetInfoByHashRef:
    """Tests for getting artifact info by hash reference."""

    def test_get_info_by_hash_ref(self, client: TestClient) -> None:
        """Get artifact info by hash ref returns correct data."""
        # Upload an artifact first
        content = b"hash ref test content"
        files = {"file": ("artifact.bin", io.BytesIO(content), "application/octet-stream")}
        upload_response = client.post(
            "/api/v1/upload/info-test/hash-ref",
            files=files,
            params={"uploaded_by": "test-user", "source_uri": "http://example.com"},
        )
        assert upload_response.status_code == 200
        upload_data = upload_response.json()
        hash_ref = upload_data["hash_ref"]

        # Get info by hash ref
        response = client.get(f"/api/v1/artifacts/info-test/hash-ref/{hash_ref}/info")

        assert response.status_code == 200
        data = response.json()

        assert data["hash"] == upload_data["hash"]
        assert data["hash_ref"] == hash_ref
        assert data["uploaded_by"] == "test-user"
        assert data["source_uri"] == "http://example.com"
        assert "latest" in data["tags"]
        assert "uploaded_at" in data


class TestGetInfoByTagName:
    """Tests for getting artifact info by tag name."""

    def test_get_info_by_latest_tag(self, client: TestClient) -> None:
        """Get artifact info by 'latest' tag returns correct data."""
        # Upload an artifact first
        content = b"latest tag test content"
        files = {"file": ("artifact.bin", io.BytesIO(content), "application/octet-stream")}
        upload_response = client.post(
            "/api/v1/upload/info-test/tag",
            files=files,
            params={"uploaded_by": "tag-user"},
        )
        assert upload_response.status_code == 200
        upload_data = upload_response.json()

        # Get info by tag name
        response = client.get("/api/v1/artifacts/info-test/tag/latest/info")

        assert response.status_code == 200
        data = response.json()

        assert data["hash"] == upload_data["hash"]
        assert data["hash_ref"] == upload_data["hash_ref"]
        assert data["uploaded_by"] == "tag-user"
        assert "latest" in data["tags"]


class TestNonexistentRef:
    """Tests for nonexistent references."""

    def test_nonexistent_ref_returns_404(self, client: TestClient) -> None:
        """Nonexistent ref returns 404 error."""
        # Upload an artifact first so the path exists
        content = b"existing content"
        files = {"file": ("artifact.bin", io.BytesIO(content), "application/octet-stream")}
        client.post("/api/v1/upload/info-test/nonexistent-ref", files=files)

        # Try to get info with nonexistent ref
        response = client.get("/api/v1/artifacts/info-test/nonexistent-ref/nonexistent-tag/info")

        assert response.status_code == 404
        data = response.json()
        assert data["error"] == "ArtifactNotFoundError"

    def test_nonexistent_hash_ref_returns_404(self, client: TestClient) -> None:
        """Nonexistent hash ref returns 404 error."""
        # Upload an artifact first so the path exists
        content = b"existing content for hash test"
        files = {"file": ("artifact.bin", io.BytesIO(content), "application/octet-stream")}
        client.post("/api/v1/upload/info-test/nonexistent-hash", files=files)

        # Try to get info with nonexistent hash ref
        response = client.get("/api/v1/artifacts/info-test/nonexistent-hash/@00000000/info")

        assert response.status_code == 404
        data = response.json()
        assert data["error"] == "ArtifactNotFoundError"


class TestNonexistentPath:
    """Tests for nonexistent artifact paths."""

    def test_nonexistent_path_returns_404(self, client: TestClient) -> None:
        """Nonexistent artifact path returns 404 error."""
        response = client.get("/api/v1/artifacts/nonexistent/path/latest/info")

        assert response.status_code == 404
        data = response.json()
        assert data["error"] == "ArtifactNotFoundError"


class TestResponseIncludesTags:
    """Tests for tag inclusion in response."""

    def test_response_includes_all_tags(
        self, client: TestClient, test_storage_service: StorageService
    ) -> None:
        """Response includes all tags pointing to the version."""
        artifact_path = "info-test/multi-tag"

        # Upload an artifact
        content = b"multi-tag content"
        files = {"file": ("artifact.bin", io.BytesIO(content), "application/octet-stream")}
        upload_response = client.post(f"/api/v1/upload/{artifact_path}", files=files)
        upload_data = upload_response.json()

        # Add another tag manually
        from magpie.storage.manifest import update_tag
        from magpie.storage.paths import artifact_dir_path
        from magpie.storage.symlinks import reconcile_symlinks

        artifact_dir = artifact_dir_path(
            test_storage_service.config.storage_path, artifact_path
        )
        manifest = update_tag(artifact_dir, "v1.0", upload_data["hash"])
        reconcile_symlinks(artifact_dir, manifest)

        # Get info - should include both tags
        response = client.get(f"/api/v1/artifacts/{artifact_path}/latest/info")

        assert response.status_code == 200
        data = response.json()

        assert "latest" in data["tags"]
        assert "v1.0" in data["tags"]


class TestResponseFormat:
    """Tests for response format."""

    def test_response_content_type(self, client: TestClient) -> None:
        """Response should be JSON."""
        # Upload first
        content = b"format test content"
        files = {"file": ("artifact.bin", io.BytesIO(content), "application/octet-stream")}
        client.post("/api/v1/upload/format-test/info", files=files)

        response = client.get("/api/v1/artifacts/format-test/info/latest/info")

        assert response.status_code == 200
        assert response.headers["content-type"] == "application/json"

    def test_response_has_all_fields(self, client: TestClient) -> None:
        """Response has all expected fields."""
        # Upload first
        content = b"fields test content"
        files = {"file": ("artifact.bin", io.BytesIO(content), "application/octet-stream")}
        client.post(
            "/api/v1/upload/fields-test/info",
            files=files,
            params={"source_uri": "http://test.com"},
        )

        response = client.get("/api/v1/artifacts/fields-test/info/latest/info")

        assert response.status_code == 200
        data = response.json()

        required_fields = {"hash", "hash_ref", "uploaded_by", "uploaded_at", "source_uri", "tags"}
        assert required_fields == set(data.keys())

    def test_uploaded_at_is_iso_format(self, client: TestClient) -> None:
        """uploaded_at should be ISO format datetime."""
        # Upload first
        content = b"datetime test content"
        files = {"file": ("artifact.bin", io.BytesIO(content), "application/octet-stream")}
        client.post("/api/v1/upload/datetime-test/info", files=files)

        response = client.get("/api/v1/artifacts/datetime-test/info/latest/info")

        data = response.json()
        # ISO format has T separator
        assert "T" in data["uploaded_at"]
