"""Integration tests for the amend metadata endpoint."""

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


def _upload_artifact(
    client: TestClient, path: str, content: bytes, source_uri: str | None = None
) -> dict:
    """Helper to upload an artifact and return the response data."""
    files = {"file": ("artifact.bin", io.BytesIO(content), "application/octet-stream")}
    params = {"uploaded_by": "test-user"}
    if source_uri:
        params["source_uri"] = source_uri

    response = client.post(f"/api/v1/upload/{path}", files=files, params=params)
    assert response.status_code == 200
    return response.json()


class TestAmendMetadataEndpoint:
    """Tests for PATCH /api/v1/artifacts/{path}/{ref} endpoint."""

    def test_amend_source_uri_by_tag(self, client: TestClient) -> None:
        """Amend source_uri using tag name as ref."""
        # Upload artifact without source_uri
        upload_data = _upload_artifact(client, "amend/tag-test", b"test content")

        # Amend source_uri using "latest" tag
        response = client.patch(
            "/api/v1/artifacts/amend/tag-test/latest",
            json={"source_uri": "https://example.com/source"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["source_uri"] == "https://example.com/source"
        assert data["hash"] == upload_data["hash"]
        assert data["uploaded_by"] == "test-user"

    def test_amend_source_uri_by_hash_ref(self, client: TestClient) -> None:
        """Amend source_uri using hash reference."""
        # Upload artifact
        upload_data = _upload_artifact(client, "amend/hash-test", b"hash test content")
        hash_ref = upload_data["hash_ref"]

        # Amend using hash ref
        response = client.patch(
            f"/api/v1/artifacts/amend/hash-test/{hash_ref}",
            json={"source_uri": "https://example.com/hash-source"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["source_uri"] == "https://example.com/hash-source"
        assert data["hash_ref"] == hash_ref

    def test_amend_preserves_immutable_fields(self, client: TestClient) -> None:
        """Amend preserves uploaded_by and uploaded_at."""
        # Upload artifact with initial source_uri
        upload_data = _upload_artifact(
            client,
            "amend/preserve-test",
            b"preserve test",
            source_uri="https://original.com",
        )

        # Get original info
        info_response = client.get("/api/v1/artifacts/amend/preserve-test/latest/info")
        original_info = info_response.json()

        # Amend source_uri
        response = client.patch(
            "/api/v1/artifacts/amend/preserve-test/latest",
            json={"source_uri": "https://updated.com"},
        )

        assert response.status_code == 200
        data = response.json()

        # Verify mutable field updated
        assert data["source_uri"] == "https://updated.com"

        # Verify immutable fields preserved
        assert data["hash"] == original_info["hash"]
        assert data["uploaded_by"] == original_info["uploaded_by"]
        assert data["uploaded_at"] == original_info["uploaded_at"]

    def test_amend_returns_all_tags(self, client: TestClient) -> None:
        """Amend response includes all tags on the blob."""
        # Upload artifact
        upload_data = _upload_artifact(client, "amend/tags-test", b"tags test")
        hash_ref = upload_data["hash_ref"]

        # Create additional tag
        client.post(
            f"/api/v1/artifacts/amend/tags-test/{hash_ref}/tags",
            json={"tag_name": "stable"},
        )

        # Amend and check tags in response
        response = client.patch(
            "/api/v1/artifacts/amend/tags-test/latest",
            json={"source_uri": "https://example.com"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "latest" in data["tags"]
        assert "stable" in data["tags"]

    def test_amend_with_null_source_uri_no_change(
        self, client: TestClient, test_storage_service: StorageService
    ) -> None:
        """Amend with null source_uri leaves existing value unchanged."""
        # Upload with source_uri
        _upload_artifact(
            client,
            "amend/null-test",
            b"null test",
            source_uri="https://original.com",
        )

        # Amend with null (should not change)
        response = client.patch(
            "/api/v1/artifacts/amend/null-test/latest",
            json={"source_uri": None},
        )

        assert response.status_code == 200
        data = response.json()
        # source_uri should remain unchanged
        assert data["source_uri"] == "https://original.com"

    def test_amend_nonexistent_artifact_returns_404(self, client: TestClient) -> None:
        """Amending nonexistent artifact returns 404."""
        response = client.patch(
            "/api/v1/artifacts/nonexistent/path/latest",
            json={"source_uri": "https://example.com"},
        )

        assert response.status_code == 404

    def test_amend_nonexistent_tag_returns_404(self, client: TestClient) -> None:
        """Amending with nonexistent tag returns 404."""
        # Upload artifact
        _upload_artifact(client, "amend/missing-tag", b"missing tag test")

        # Try to amend using nonexistent tag
        response = client.patch(
            "/api/v1/artifacts/amend/missing-tag/nonexistent",
            json={"source_uri": "https://example.com"},
        )

        assert response.status_code == 404

    def test_amend_nonexistent_hash_returns_404(self, client: TestClient) -> None:
        """Amending with nonexistent hash ref returns 404."""
        # Upload artifact
        _upload_artifact(client, "amend/missing-hash", b"missing hash test")

        # Try to amend using nonexistent hash
        response = client.patch(
            "/api/v1/artifacts/amend/missing-hash/@deadbeef",
            json={"source_uri": "https://example.com"},
        )

        assert response.status_code == 404

    def test_amend_nested_path(self, client: TestClient) -> None:
        """Amend works with deeply nested artifact paths."""
        # Upload artifact at nested path
        _upload_artifact(client, "deep/nested/path/artifact", b"nested content")

        # Amend
        response = client.patch(
            "/api/v1/artifacts/deep/nested/path/artifact/latest",
            json={"source_uri": "https://nested.example.com"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["source_uri"] == "https://nested.example.com"

    def test_amend_response_format(self, client: TestClient) -> None:
        """Amend response includes all expected fields."""
        _upload_artifact(client, "amend/format-test", b"format test")

        response = client.patch(
            "/api/v1/artifacts/amend/format-test/latest",
            json={"source_uri": "https://example.com"},
        )

        assert response.status_code == 200
        data = response.json()

        required_fields = {
            "hash",
            "hash_ref",
            "uploaded_by",
            "uploaded_at",
            "source_uri",
            "tags",
        }
        assert required_fields == set(data.keys())
