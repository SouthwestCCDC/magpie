"""Integration tests for the upload endpoint."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from magpie.config import MagpieSettings, get_settings
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


class TestUploadEndpoint:
    """Tests for POST /api/v1/upload/{path} endpoint."""

    def test_successful_upload(self, client: TestClient) -> None:
        """Successful upload returns correct response structure."""
        content = b"test artifact content"
        files = {"file": ("artifact.bin", io.BytesIO(content), "application/octet-stream")}

        response = client.post(
            "/api/v1/upload/project/component",
            files=files,
            params={"uploaded_by": "test-user", "source_uri": "http://example.com/source"},
        )

        assert response.status_code == 200
        data = response.json()

        assert "hash" in data
        assert len(data["hash"]) == 64  # SHA-256 hex length
        assert "hash_ref" in data
        assert data["hash_ref"].startswith("@")
        assert data["artifact_path"] == "project/component"
        assert data["is_duplicate"] is False
        assert data["download_url"] == f"/artifacts/project/component/{data['hash_ref']}"

    def test_upload_default_uploaded_by(self, client: TestClient) -> None:
        """Upload without uploaded_by defaults to 'anonymous'."""
        content = b"anonymous upload content"
        files = {"file": ("artifact.bin", io.BytesIO(content), "application/octet-stream")}

        response = client.post("/api/v1/upload/anon/test", files=files)

        assert response.status_code == 200
        # Artifact should be stored successfully with default uploader

    def test_upload_without_source_uri(self, client: TestClient) -> None:
        """Upload without source_uri should succeed."""
        content = b"no source uri content"
        files = {"file": ("artifact.bin", io.BytesIO(content), "application/octet-stream")}

        response = client.post(
            "/api/v1/upload/no-source/artifact",
            files=files,
            params={"uploaded_by": "test-user"},
        )

        assert response.status_code == 200


class TestDuplicateDetection:
    """Tests for duplicate artifact detection."""

    def test_duplicate_upload_returns_is_duplicate_true(self, client: TestClient) -> None:
        """Uploading same content twice returns is_duplicate=True."""
        content = b"duplicate detection test content"
        files = {"file": ("artifact.bin", io.BytesIO(content), "application/octet-stream")}

        # First upload
        response1 = client.post(
            "/api/v1/upload/dup/test",
            files=files,
            params={"uploaded_by": "user1"},
        )
        assert response1.status_code == 200
        data1 = response1.json()
        assert data1["is_duplicate"] is False

        # Second upload of same content
        files2 = {"file": ("artifact.bin", io.BytesIO(content), "application/octet-stream")}
        response2 = client.post(
            "/api/v1/upload/dup/test",
            files=files2,
            params={"uploaded_by": "user2"},
        )
        assert response2.status_code == 200
        data2 = response2.json()
        assert data2["is_duplicate"] is True

        # Both should have same hash
        assert data1["hash"] == data2["hash"]
        assert data1["hash_ref"] == data2["hash_ref"]

    def test_different_content_not_duplicate(self, client: TestClient) -> None:
        """Different content should not be detected as duplicate."""
        files1 = {"file": ("artifact.bin", io.BytesIO(b"content v1"), "application/octet-stream")}
        files2 = {"file": ("artifact.bin", io.BytesIO(b"content v2"), "application/octet-stream")}

        response1 = client.post("/api/v1/upload/diff/test", files=files1)
        response2 = client.post("/api/v1/upload/diff/test", files=files2)

        assert response1.status_code == 200
        assert response2.status_code == 200

        data1 = response1.json()
        data2 = response2.json()

        # Different content = different hash = not duplicate
        assert data1["hash"] != data2["hash"]
        assert data2["is_duplicate"] is False


class TestLargeFileHandling:
    """Tests for large file handling."""

    def test_large_file_upload(self, client: TestClient) -> None:
        """Large file (> 1MB) should upload successfully."""
        # Create 1.5MB of content
        large_content = b"x" * (1024 * 1024 + 512 * 1024)  # 1.5 MB
        files = {"file": ("large.bin", io.BytesIO(large_content), "application/octet-stream")}

        response = client.post(
            "/api/v1/upload/large/file",
            files=files,
            params={"uploaded_by": "test-user"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["is_duplicate"] is False
        assert len(data["hash"]) == 64


class TestPathHandling:
    """Tests for artifact path handling."""

    def test_nested_path(self, client: TestClient) -> None:
        """Deeply nested paths should work."""
        content = b"nested path content"
        files = {"file": ("artifact.bin", io.BytesIO(content), "application/octet-stream")}

        response = client.post(
            "/api/v1/upload/deep/nested/path/to/artifact",
            files=files,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["artifact_path"] == "deep/nested/path/to/artifact"

    def test_simple_path(self, client: TestClient) -> None:
        """Single segment path should work."""
        content = b"simple path content"
        files = {"file": ("artifact.bin", io.BytesIO(content), "application/octet-stream")}

        response = client.post("/api/v1/upload/simple", files=files)

        assert response.status_code == 200
        data = response.json()
        assert data["artifact_path"] == "simple"


class TestResponseFormat:
    """Tests for response format validation."""

    def test_response_content_type(self, client: TestClient) -> None:
        """Response should be JSON."""
        content = b"content type test"
        files = {"file": ("artifact.bin", io.BytesIO(content), "application/octet-stream")}

        response = client.post("/api/v1/upload/ct/test", files=files)

        assert response.headers["content-type"] == "application/json"

    def test_response_has_all_fields(self, client: TestClient) -> None:
        """Response should include all expected fields."""
        content = b"all fields test"
        files = {"file": ("artifact.bin", io.BytesIO(content), "application/octet-stream")}

        response = client.post("/api/v1/upload/fields/test", files=files)

        assert response.status_code == 200
        data = response.json()

        required_fields = {"hash", "hash_ref", "artifact_path", "is_duplicate", "download_url"}
        assert required_fields == set(data.keys())

    def test_download_url_format(self, client: TestClient) -> None:
        """Download URL should follow expected format."""
        content = b"download url test"
        files = {"file": ("artifact.bin", io.BytesIO(content), "application/octet-stream")}

        response = client.post("/api/v1/upload/download/url", files=files)

        data = response.json()
        expected_prefix = f"/artifacts/download/url/{data['hash_ref']}"
        assert data["download_url"] == expected_prefix


class TestAuthHeaderHandling:
    """Tests for X-Magpie-User header handling in uploads."""

    def test_x_magpie_user_header_used_when_present(
        self, client: TestClient, test_storage_service: StorageService
    ) -> None:
        """X-Magpie-User header is used as uploaded_by when present."""
        content = b"auth header test content"
        files = {"file": ("artifact.bin", io.BytesIO(content), "application/octet-stream")}

        response = client.post(
            "/api/v1/upload/auth/header-test",
            files=files,
            headers={"X-Magpie-User": "authenticated-service"},
        )

        assert response.status_code == 200

        # Verify the uploaded_by was set from header
        info = test_storage_service.get_artifact_info("auth/header-test", "latest")
        assert info.uploaded_by == "authenticated-service"

    def test_query_param_fallback_when_header_missing(
        self, client: TestClient, test_storage_service: StorageService
    ) -> None:
        """Query param uploaded_by is used as fallback when header missing."""
        content = b"fallback test content"
        files = {"file": ("artifact.bin", io.BytesIO(content), "application/octet-stream")}

        response = client.post(
            "/api/v1/upload/auth/fallback-test",
            files=files,
            params={"uploaded_by": "query-param-user"},
        )

        assert response.status_code == 200

        # Verify the uploaded_by was set from query param
        info = test_storage_service.get_artifact_info("auth/fallback-test", "latest")
        assert info.uploaded_by == "query-param-user"

    def test_header_takes_precedence_over_query_param(
        self, client: TestClient, test_storage_service: StorageService
    ) -> None:
        """X-Magpie-User header takes precedence over uploaded_by query param."""
        content = b"precedence test content"
        files = {"file": ("artifact.bin", io.BytesIO(content), "application/octet-stream")}

        response = client.post(
            "/api/v1/upload/auth/precedence-test",
            files=files,
            headers={"X-Magpie-User": "header-user"},
            params={"uploaded_by": "query-param-user"},
        )

        assert response.status_code == 200

        # Verify the header user was used, not the query param
        info = test_storage_service.get_artifact_info("auth/precedence-test", "latest")
        assert info.uploaded_by == "header-user"

    def test_anonymous_default_when_no_header_or_param(
        self, client: TestClient, test_storage_service: StorageService
    ) -> None:
        """Default 'anonymous' is used when no header or query param provided."""
        content = b"anonymous test content"
        files = {"file": ("artifact.bin", io.BytesIO(content), "application/octet-stream")}

        response = client.post(
            "/api/v1/upload/auth/anonymous-test",
            files=files,
        )

        assert response.status_code == 200

        # Verify anonymous was used as default
        info = test_storage_service.get_artifact_info("auth/anonymous-test", "latest")
        assert info.uploaded_by == "anonymous"


class TestPathValidation:
    """Tests for artifact path validation."""

    def test_reserved_segment_blobs_rejected(self, client: TestClient) -> None:
        """Upload with 'blobs' in path is rejected."""
        content = b"test content"
        files = {"file": ("artifact.bin", io.BytesIO(content), "application/octet-stream")}

        response = client.post("/api/v1/upload/test/blobs/evil", files=files)

        assert response.status_code == 400
        assert "reserved" in response.json()["detail"].lower()
        assert "blobs" in response.json()["detail"]

    def test_reserved_segment_metadata_rejected(self, client: TestClient) -> None:
        """Upload with 'metadata' in path is rejected."""
        content = b"test content"
        files = {"file": ("artifact.bin", io.BytesIO(content), "application/octet-stream")}

        response = client.post("/api/v1/upload/metadata/test", files=files)

        assert response.status_code == 400
        assert "reserved" in response.json()["detail"].lower()

    def test_reserved_segment_dotmagpie_rejected(self, client: TestClient) -> None:
        """Upload with '.magpie' in path is rejected."""
        content = b"test content"
        files = {"file": ("artifact.bin", io.BytesIO(content), "application/octet-stream")}

        response = client.post("/api/v1/upload/test/.magpie/config", files=files)

        assert response.status_code == 400
        assert "reserved" in response.json()["detail"].lower()

    def test_valid_path_accepted(self, client: TestClient) -> None:
        """Valid paths without reserved segments are accepted."""
        content = b"test content"
        files = {"file": ("artifact.bin", io.BytesIO(content), "application/octet-stream")}

        response = client.post("/api/v1/upload/project/component/artifact", files=files)

        assert response.status_code == 200
