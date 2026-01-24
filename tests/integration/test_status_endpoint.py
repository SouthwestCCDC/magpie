"""Integration tests for the status endpoint."""

from __future__ import annotations

import io

from fastapi.testclient import TestClient

from magpie import __version__


class TestStatusEndpointAuth:
    """Tests for status endpoint authentication and authorization."""

    def test_status_requires_admin_token(
        self, api_client_no_auth_override: TestClient, admin_token: str
    ) -> None:
        """Status endpoint requires admin token."""
        response = api_client_no_auth_override.get(
            "/api/v1/status",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"

    def test_status_with_read_token_returns_403(
        self, api_client_no_auth_override: TestClient, read_token: str
    ) -> None:
        """Status with read token returns 403."""
        response = api_client_no_auth_override.get(
            "/api/v1/status",
            headers={"Authorization": f"Bearer {read_token}"},
        )

        assert response.status_code == 403
        assert "Admin scope required" in response.json()["detail"]

    def test_status_with_write_token_returns_403(
        self, api_client_no_auth_override: TestClient, write_token: str
    ) -> None:
        """Status with write token returns 403."""
        response = api_client_no_auth_override.get(
            "/api/v1/status",
            headers={"Authorization": f"Bearer {write_token}"},
        )

        assert response.status_code == 403
        assert "Admin scope required" in response.json()["detail"]

    def test_status_without_auth_returns_401(self, api_client_no_auth_override: TestClient) -> None:
        """Status without authorization returns 401."""
        response = api_client_no_auth_override.get("/api/v1/status")

        assert response.status_code == 401


class TestStatusEndpoint:
    """Tests for GET /api/v1/status endpoint.

    Note: Authentication is mocked via autouse fixture in conftest.py.
    These tests verify endpoint logic, not authentication behavior.
    """

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
        assert data["version"] == __version__

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
