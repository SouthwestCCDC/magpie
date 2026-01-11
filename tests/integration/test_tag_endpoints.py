"""Integration tests for the tag management endpoints."""

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


def upload_artifact(client: TestClient, artifact_path: str, content: bytes) -> dict:
    """Helper to upload an artifact and return the response data."""
    files = {"file": ("artifact.bin", io.BytesIO(content), "application/octet-stream")}
    response = client.post(
        f"/api/v1/upload/{artifact_path}",
        files=files,
        params={"uploaded_by": "test-user"},
    )
    assert response.status_code == 200
    return response.json()


class TestCreateTagOnExistingArtifact:
    """Tests for creating tags on existing artifacts."""

    def test_create_tag_on_existing_artifact(self, client: TestClient) -> None:
        """Create a tag on an existing artifact version."""
        artifact_path = "tag-test/create"
        upload_data = upload_artifact(client, artifact_path, b"tag test content")
        hash_ref = upload_data["hash_ref"]

        response = client.post(
            f"/api/v1/artifacts/{artifact_path}/{hash_ref}/tags",
            json={"tag_name": "v1.0"},
        )

        assert response.status_code == 200
        data = response.json()

        assert data["artifact_path"] == artifact_path
        assert data["tag_name"] == "v1.0"
        assert data["hash_ref"] == hash_ref
        assert "v1.0" in data["tags"]
        assert "latest" in data["tags"]

    def test_create_tag_by_existing_tag_name(self, client: TestClient) -> None:
        """Create a new tag using existing tag name as ref."""
        artifact_path = "tag-test/by-tag"
        upload_artifact(client, artifact_path, b"tag by name content")

        response = client.post(
            f"/api/v1/artifacts/{artifact_path}/latest/tags",
            json={"tag_name": "stable"},
        )

        assert response.status_code == 200
        data = response.json()

        assert data["tag_name"] == "stable"
        assert "stable" in data["tags"]
        assert "latest" in data["tags"]


class TestCreateTagUpdatesExisting:
    """Tests for updating existing tags."""

    def test_create_tag_updates_existing_tag(self, client: TestClient) -> None:
        """Creating a tag that already exists updates it to point to new version."""
        artifact_path = "tag-test/update"

        # Upload first version
        upload_data_v1 = upload_artifact(client, artifact_path, b"version 1 content")
        hash_ref_v1 = upload_data_v1["hash_ref"]

        # Create a custom tag on v1
        client.post(
            f"/api/v1/artifacts/{artifact_path}/{hash_ref_v1}/tags",
            json={"tag_name": "release"},
        )

        # Upload second version
        upload_data_v2 = upload_artifact(client, artifact_path, b"version 2 content")
        hash_ref_v2 = upload_data_v2["hash_ref"]

        # Update the "release" tag to point to v2
        response = client.post(
            f"/api/v1/artifacts/{artifact_path}/{hash_ref_v2}/tags",
            json={"tag_name": "release"},
        )

        assert response.status_code == 200
        data = response.json()

        # Tag should now point to v2
        assert data["hash_ref"] == hash_ref_v2
        assert "release" in data["tags"]


class TestCreateTagNonexistentRef:
    """Tests for creating tags with nonexistent refs."""

    def test_create_tag_nonexistent_ref_returns_404(self, client: TestClient) -> None:
        """Creating a tag with nonexistent ref returns 404."""
        artifact_path = "tag-test/nonexistent"
        upload_artifact(client, artifact_path, b"some content")

        response = client.post(
            f"/api/v1/artifacts/{artifact_path}/@00000000/tags",
            json={"tag_name": "invalid"},
        )

        assert response.status_code == 404
        data = response.json()
        assert data["error"] == "ArtifactNotFoundError"

    def test_create_tag_nonexistent_path_returns_404(self, client: TestClient) -> None:
        """Creating a tag on nonexistent artifact path returns 404."""
        response = client.post(
            "/api/v1/artifacts/nonexistent/path/latest/tags",
            json={"tag_name": "invalid"},
        )

        assert response.status_code == 404
        data = response.json()
        assert data["error"] == "ArtifactNotFoundError"


class TestRemoveTag:
    """Tests for removing tags from artifacts."""

    def test_remove_tag_removes_existing_tag(self, client: TestClient) -> None:
        """Removing an existing tag succeeds with 204."""
        artifact_path = "tag-test/remove"
        upload_data = upload_artifact(client, artifact_path, b"remove tag content")

        # Create a tag first
        client.post(
            f"/api/v1/artifacts/{artifact_path}/{upload_data['hash_ref']}/tags",
            json={"tag_name": "to-remove"},
        )

        # Remove the tag
        response = client.delete(f"/api/v1/artifacts/{artifact_path}/tags/to-remove")

        assert response.status_code == 204

        # Verify tag is gone by checking info endpoint
        info_response = client.get(
            f"/api/v1/artifacts/{artifact_path}/{upload_data['hash_ref']}/info"
        )
        assert info_response.status_code == 200
        assert "to-remove" not in info_response.json()["tags"]


class TestRemoveTagNonexistent:
    """Tests for removing nonexistent tags."""

    def test_remove_nonexistent_tag_returns_404(self, client: TestClient) -> None:
        """Removing a tag that doesn't exist returns 404."""
        artifact_path = "tag-test/remove-nonexistent"
        upload_artifact(client, artifact_path, b"content for nonexistent tag test")

        response = client.delete(f"/api/v1/artifacts/{artifact_path}/tags/nonexistent-tag")

        assert response.status_code == 404
        data = response.json()
        assert data["error"] == "ArtifactNotFoundError"


class TestTagResponseIncludesTags:
    """Tests for TagResponse including updated tags list."""

    def test_tag_response_includes_all_tags(self, client: TestClient) -> None:
        """TagResponse includes all tags pointing to the version."""
        artifact_path = "tag-test/all-tags"
        upload_data = upload_artifact(client, artifact_path, b"multi-tag content")
        hash_ref = upload_data["hash_ref"]

        # Create multiple tags
        client.post(
            f"/api/v1/artifacts/{artifact_path}/{hash_ref}/tags",
            json={"tag_name": "v1.0"},
        )

        response = client.post(
            f"/api/v1/artifacts/{artifact_path}/{hash_ref}/tags",
            json={"tag_name": "stable"},
        )

        assert response.status_code == 200
        data = response.json()

        # Should include all tags
        assert "latest" in data["tags"]
        assert "v1.0" in data["tags"]
        assert "stable" in data["tags"]
        assert len(data["tags"]) == 3

    def test_tag_response_has_correct_structure(self, client: TestClient) -> None:
        """TagResponse has all expected fields."""
        artifact_path = "tag-test/structure"
        upload_data = upload_artifact(client, artifact_path, b"structure test content")

        response = client.post(
            f"/api/v1/artifacts/{artifact_path}/latest/tags",
            json={"tag_name": "checked"},
        )

        assert response.status_code == 200
        data = response.json()

        expected_fields = {"artifact_path", "tag_name", "hash_ref", "tags"}
        assert expected_fields == set(data.keys())
