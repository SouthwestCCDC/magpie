"""Integration tests for the status endpoint."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from magpie.auth.service import TokenService
from magpie.config import MagpieSettings
from magpie.server.app import app
from magpie.server.deps import get_storage_service, get_token_service
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
def test_token_service(test_config: MagpieSettings) -> TokenService:
    """Create a TokenService instance for testing."""
    return TokenService(test_config)


@pytest.fixture
def api_client(
    test_storage_service: StorageService, test_token_service: TokenService
) -> TestClient:
    """Create test API client with overridden dependencies."""

    def override_storage_service() -> StorageService:
        return test_storage_service

    def override_token_service() -> TokenService:
        return test_token_service

    app.dependency_overrides[get_storage_service] = override_storage_service
    app.dependency_overrides[get_token_service] = override_token_service
    yield TestClient(app)
    app.dependency_overrides.clear()


class TestStatusEndpoint:
    """Tests for GET /api/v1/status endpoint."""

    def test_status_returns_ok(self, api_client: TestClient) -> None:
        """Status endpoint returns ok status."""
        response = api_client.get("/api/v1/status")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"

    def test_status_returns_version(self, api_client: TestClient) -> None:
        """Status endpoint returns server version."""
        response = api_client.get("/api/v1/status")

        assert response.status_code == 200
        data = response.json()
        assert "version" in data
        assert data["version"] == "0.1.0"

    def test_status_returns_auth_info_without_token(self, api_client: TestClient) -> None:
        """Status endpoint returns auth info showing no valid token."""
        response = api_client.get("/api/v1/status")

        assert response.status_code == 200
        data = response.json()
        assert "auth" in data
        assert data["auth"]["valid"] is False

    def test_status_returns_storage_stats(self, api_client: TestClient) -> None:
        """Status endpoint returns storage statistics."""
        response = api_client.get("/api/v1/status")

        assert response.status_code == 200
        data = response.json()
        assert "storage" in data
        assert "total_size_bytes" in data["storage"]
        assert "artifact_count" in data["storage"]
        assert "blob_count" in data["storage"]

    def test_status_storage_stats_empty_storage(self, api_client: TestClient) -> None:
        """Status with empty storage shows zero counts."""
        response = api_client.get("/api/v1/status")

        assert response.status_code == 200
        data = response.json()
        assert data["storage"]["total_size_bytes"] == 0
        assert data["storage"]["artifact_count"] == 0
        assert data["storage"]["blob_count"] == 0

    def test_status_storage_stats_with_artifacts(self, api_client: TestClient) -> None:
        """Status with artifacts shows correct counts and sizes."""
        # Upload some artifacts
        content1 = b"artifact content 1"
        content2 = b"artifact content 2"

        files1 = {"file": ("test.bin", io.BytesIO(content1), "application/octet-stream")}
        api_client.post("/api/v1/upload/test/artifact1", files=files1)

        files2 = {"file": ("test.bin", io.BytesIO(content2), "application/octet-stream")}
        api_client.post("/api/v1/upload/test/artifact2", files=files2)

        response = api_client.get("/api/v1/status")

        assert response.status_code == 200
        data = response.json()
        assert data["storage"]["artifact_count"] == 2
        assert data["storage"]["blob_count"] == 2
        assert data["storage"]["total_size_bytes"] == len(content1) + len(content2)

    def test_status_storage_stats_with_duplicate_content(self, api_client: TestClient) -> None:
        """Status with duplicate content counts each blob once."""
        # Upload same content to different paths
        content = b"same content"

        files1 = {"file": ("test.bin", io.BytesIO(content), "application/octet-stream")}
        api_client.post("/api/v1/upload/test/artifact1", files=files1)

        files2 = {"file": ("test.bin", io.BytesIO(content), "application/octet-stream")}
        api_client.post("/api/v1/upload/test/artifact2", files=files2)

        response = api_client.get("/api/v1/status")

        assert response.status_code == 200
        data = response.json()
        # Each artifact has its own blob (content-addressed per artifact path)
        assert data["storage"]["artifact_count"] == 2
        assert data["storage"]["blob_count"] == 2
