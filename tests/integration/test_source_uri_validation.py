"""Integration tests for source_uri length validation."""

from __future__ import annotations

import io

from fastapi.testclient import TestClient


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


class TestSourceUriValidationUpload:
    """Tests for source_uri length validation in upload endpoint."""

    def test_upload_with_valid_source_uri(self, client: TestClient) -> None:
        """Upload with a valid source_uri succeeds."""
        content = b"valid source uri test"
        files = {"file": ("artifact.bin", io.BytesIO(content), "application/octet-stream")}
        valid_uri = "https://example.com/path/to/resource"

        response = client.post(
            "/api/v1/upload/source-uri/valid",
            files=files,
            params={"source_uri": valid_uri},
        )

        assert response.status_code == 200

    def test_upload_with_max_length_source_uri(self, client: TestClient) -> None:
        """Upload with source_uri at exactly 2048 chars succeeds."""
        content = b"max length source uri test"
        files = {"file": ("artifact.bin", io.BytesIO(content), "application/octet-stream")}
        # Create a URI that is exactly 2048 characters
        base_uri = "https://example.com/"
        padding = "x" * (2048 - len(base_uri))
        max_uri = base_uri + padding
        assert len(max_uri) == 2048

        response = client.post(
            "/api/v1/upload/source-uri/maxlen",
            files=files,
            params={"source_uri": max_uri},
        )

        assert response.status_code == 200

    def test_upload_with_too_long_source_uri(self, client: TestClient) -> None:
        """Upload with source_uri exceeding 2048 chars is rejected."""
        content = b"too long source uri test"
        files = {"file": ("artifact.bin", io.BytesIO(content), "application/octet-stream")}
        # Create a URI that exceeds 2048 characters
        too_long_uri = "https://example.com/" + "x" * 2040

        response = client.post(
            "/api/v1/upload/source-uri/toolong",
            files=files,
            params={"source_uri": too_long_uri},
        )

        assert response.status_code == 422  # Validation error

    def test_upload_without_source_uri(self, client: TestClient) -> None:
        """Upload without source_uri succeeds (optional field)."""
        content = b"no source uri test"
        files = {"file": ("artifact.bin", io.BytesIO(content), "application/octet-stream")}

        response = client.post(
            "/api/v1/upload/source-uri/none",
            files=files,
        )

        assert response.status_code == 200


class TestSourceUriValidationAmend:
    """Tests for source_uri length validation in amend endpoint."""

    def test_amend_with_valid_source_uri(self, client: TestClient) -> None:
        """Amend with a valid source_uri succeeds."""
        _upload_artifact(client, "amend-uri/valid", b"test content")
        valid_uri = "https://example.com/updated/path"

        response = client.patch(
            "/api/v1/artifacts/amend-uri/valid/latest",
            json={"source_uri": valid_uri},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["source_uri"] == valid_uri

    def test_amend_with_max_length_source_uri(self, client: TestClient) -> None:
        """Amend with source_uri at exactly 2048 chars succeeds."""
        _upload_artifact(client, "amend-uri/maxlen", b"test content")
        # Create a URI that is exactly 2048 characters
        base_uri = "https://example.com/"
        padding = "x" * (2048 - len(base_uri))
        max_uri = base_uri + padding
        assert len(max_uri) == 2048

        response = client.patch(
            "/api/v1/artifacts/amend-uri/maxlen/latest",
            json={"source_uri": max_uri},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["source_uri"] == max_uri

    def test_amend_with_too_long_source_uri(self, client: TestClient) -> None:
        """Amend with source_uri exceeding 2048 chars is rejected."""
        _upload_artifact(client, "amend-uri/toolong", b"test content")
        # Create a URI that exceeds 2048 characters
        too_long_uri = "https://example.com/" + "x" * 2040

        response = client.patch(
            "/api/v1/artifacts/amend-uri/toolong/latest",
            json={"source_uri": too_long_uri},
        )

        assert response.status_code == 422  # Validation error

    def test_amend_with_null_source_uri(self, client: TestClient) -> None:
        """Amend with null source_uri succeeds (to preserve value)."""
        _upload_artifact(
            client,
            "amend-uri/null",
            b"test content",
            source_uri="https://original.com",
        )

        response = client.patch(
            "/api/v1/artifacts/amend-uri/null/latest",
            json={"source_uri": None},
        )

        assert response.status_code == 200
        # Should preserve original value
        data = response.json()
        assert data["source_uri"] == "https://original.com"
