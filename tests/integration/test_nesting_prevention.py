"""Integration tests for artifact nesting prevention."""

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


class TestReservedPaths:
    """Tests for rejecting reserved path segments."""

    def test_reject_blobs_segment(self, client: TestClient) -> None:
        """Reject upload to path containing 'blobs' segment."""
        content = b"test content"
        files = {"file": ("test.bin", io.BytesIO(content), "application/octet-stream")}

        response = client.post(
            "/api/v1/upload/test/artifact/blobs",
            files=files,
            params={"uploaded_by": "test-user"},
        )

        assert response.status_code == 400
        data = response.json()
        assert data["error"] == "InvalidArtifactPathError"
        assert "blobs" in data["message"].lower()
        assert "reserved" in data["message"].lower()

    def test_reject_metadata_segment(self, client: TestClient) -> None:
        """Reject upload to path containing 'metadata' segment."""
        content = b"test content"
        files = {"file": ("test.bin", io.BytesIO(content), "application/octet-stream")}

        response = client.post(
            "/api/v1/upload/test/metadata/file",
            files=files,
            params={"uploaded_by": "test-user"},
        )

        assert response.status_code == 400
        data = response.json()
        assert data["error"] == "InvalidArtifactPathError"
        assert "metadata" in data["message"].lower()
        assert "reserved" in data["message"].lower()

    def test_reject_magpie_segment(self, client: TestClient) -> None:
        """Reject upload to path containing '.magpie' segment."""
        content = b"test content"
        files = {"file": ("test.bin", io.BytesIO(content), "application/octet-stream")}

        response = client.post(
            "/api/v1/upload/test/.magpie",
            files=files,
            params={"uploaded_by": "test-user"},
        )

        assert response.status_code == 400
        data = response.json()
        assert data["error"] == "InvalidArtifactPathError"
        assert ".magpie" in data["message"].lower()
        assert "reserved" in data["message"].lower()

    def test_reject_hidden_segment(self, client: TestClient) -> None:
        """Reject upload to path with hidden segment."""
        content = b"test content"
        files = {"file": ("test.bin", io.BytesIO(content), "application/octet-stream")}

        response = client.post(
            "/api/v1/upload/test/.hidden/file",
            files=files,
            params={"uploaded_by": "test-user"},
        )

        assert response.status_code == 400
        data = response.json()
        assert data["error"] == "InvalidArtifactPathError"
        assert "cannot start with '.'" in data["message"]


class TestNestingPrevention:
    """Tests for preventing artifact nesting."""

    def test_reject_child_of_existing_artifact(self, client: TestClient) -> None:
        """Reject creating artifact as child of existing artifact."""
        # Create parent artifact
        content = b"parent artifact"
        files = {"file": ("test.bin", io.BytesIO(content), "application/octet-stream")}

        response = client.post(
            "/api/v1/upload/test/myartifact",
            files=files,
            params={"uploaded_by": "test-user"},
        )
        assert response.status_code == 200

        # Try to create child artifact
        child_content = b"child artifact"
        child_files = {"file": ("test.bin", io.BytesIO(child_content), "application/octet-stream")}

        response = client.post(
            "/api/v1/upload/test/myartifact/nested",
            files=child_files,
            params={"uploaded_by": "test-user"},
        )

        assert response.status_code == 400
        data = response.json()
        assert data["error"] == "InvalidArtifactPathError"
        assert "nested under existing artifact" in data["message"]
        assert "test/myartifact" in data["message"]

    def test_reject_deep_child_of_existing(self, client: TestClient) -> None:
        """Reject creating deeply nested artifact under existing artifact."""
        # Create parent artifact
        content = b"parent artifact"
        files = {"file": ("test.bin", io.BytesIO(content), "application/octet-stream")}

        response = client.post(
            "/api/v1/upload/project/component",
            files=files,
            params={"uploaded_by": "test-user"},
        )
        assert response.status_code == 200

        # Try to create deeply nested child
        child_content = b"deeply nested"
        child_files = {"file": ("test.bin", io.BytesIO(child_content), "application/octet-stream")}

        response = client.post(
            "/api/v1/upload/project/component/sub/nested/deep",
            files=child_files,
            params={"uploaded_by": "test-user"},
        )

        assert response.status_code == 400
        data = response.json()
        assert data["error"] == "InvalidArtifactPathError"
        assert "nested under existing artifact" in data["message"]

    def test_reject_parent_of_existing_artifact(self, client: TestClient) -> None:
        """Reject creating artifact as parent of existing artifact."""
        # Create child artifact first
        content = b"child artifact"
        files = {"file": ("test.bin", io.BytesIO(content), "application/octet-stream")}

        response = client.post(
            "/api/v1/upload/test/myartifact/nested",
            files=files,
            params={"uploaded_by": "test-user"},
        )
        assert response.status_code == 200

        # Try to create parent artifact
        parent_content = b"parent artifact"
        parent_files = {
            "file": ("test.bin", io.BytesIO(parent_content), "application/octet-stream")
        }

        response = client.post(
            "/api/v1/upload/test/myartifact",
            files=parent_files,
            params={"uploaded_by": "test-user"},
        )

        assert response.status_code == 400
        data = response.json()
        assert data["error"] == "InvalidArtifactPathError"
        assert "would be nested under it" in data["message"]
        assert "test/myartifact/nested" in data["message"]

    def test_allow_sibling_artifacts(self, client: TestClient) -> None:
        """Allow creating sibling artifacts in same directory."""
        # Create first artifact
        content1 = b"artifact 1"
        files1 = {"file": ("test.bin", io.BytesIO(content1), "application/octet-stream")}

        response = client.post(
            "/api/v1/upload/project/artifact1",
            files=files1,
            params={"uploaded_by": "test-user"},
        )
        assert response.status_code == 200

        # Create sibling artifact (should succeed)
        content2 = b"artifact 2"
        files2 = {"file": ("test.bin", io.BytesIO(content2), "application/octet-stream")}

        response = client.post(
            "/api/v1/upload/project/artifact2",
            files=files2,
            params={"uploaded_by": "test-user"},
        )
        assert response.status_code == 200

    def test_allow_artifacts_in_different_trees(self, client: TestClient) -> None:
        """Allow creating artifacts in completely different directory trees."""
        # Create artifact in first tree
        content1 = b"tree 1 artifact"
        files1 = {"file": ("test.bin", io.BytesIO(content1), "application/octet-stream")}

        response = client.post(
            "/api/v1/upload/org/team1/project",
            files=files1,
            params={"uploaded_by": "test-user"},
        )
        assert response.status_code == 200

        # Create artifact in different tree (should succeed)
        content2 = b"tree 2 artifact"
        files2 = {"file": ("test.bin", io.BytesIO(content2), "application/octet-stream")}

        response = client.post(
            "/api/v1/upload/org/team2/project",
            files=files2,
            params={"uploaded_by": "test-user"},
        )
        assert response.status_code == 200

    def test_allow_reupload_to_same_path(self, client: TestClient) -> None:
        """Allow re-uploading to the same artifact path."""
        # Create initial artifact
        content1 = b"version 1"
        files1 = {"file": ("test.bin", io.BytesIO(content1), "application/octet-stream")}

        response = client.post(
            "/api/v1/upload/test/artifact",
            files=files1,
            params={"uploaded_by": "test-user"},
        )
        assert response.status_code == 200

        # Re-upload to same path (should succeed)
        content2 = b"version 2"
        files2 = {"file": ("test.bin", io.BytesIO(content2), "application/octet-stream")}

        response = client.post(
            "/api/v1/upload/test/artifact",
            files=files2,
            params={"uploaded_by": "test-user"},
        )
        assert response.status_code == 200
