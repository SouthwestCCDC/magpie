"""Unit tests for upload security hardening features.

Tests for the adversarial review fixes including:
- Header size limits
- Content-Disposition validation
- Boundary validation
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from magpie.config import MagpieSettings
from magpie.server.app import app
from magpie.server.deps import get_magpie_settings, get_storage_service, require_write_scope
from magpie.storage.service import StorageService


def _noop_require_write_scope() -> None:
    """No-op override for require_write_scope in tests."""
    return None


class TestBoundaryValidation:
    """Tests for RFC 2046 boundary validation."""

    @pytest.fixture
    def client(self, tmp_path: Path) -> TestClient:
        """Create test client with temp storage."""
        storage_path = tmp_path / "storage"
        temp_path_dir = tmp_path / "temp"
        storage_path.mkdir(parents=True, exist_ok=True)
        temp_path_dir.mkdir(parents=True, exist_ok=True)

        settings = MagpieSettings(
            storage_path=storage_path,
            temp_path=temp_path_dir,
        )
        storage_service = StorageService(settings)

        app.dependency_overrides[get_magpie_settings] = lambda: settings
        app.dependency_overrides[get_storage_service] = lambda: storage_service
        app.dependency_overrides[require_write_scope] = _noop_require_write_scope

        return TestClient(app)

    def test_valid_boundary_accepted(self, client: TestClient) -> None:
        """Valid RFC 2046 boundary should be accepted."""
        boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
        body = (
            "------WebKitFormBoundary7MA4YWxkTrZu0gW\r\n"
            'Content-Disposition: form-data; name="file"; filename="test.bin"\r\n'
            "\r\n"
            "test content\r\n"
            "------WebKitFormBoundary7MA4YWxkTrZu0gW--\r\n"
        ).encode()

        response = client.post(
            "/api/v1/upload/test/boundary",
            content=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        assert response.status_code == 200

    def test_quoted_boundary_accepted(self, client: TestClient) -> None:
        """Quoted boundary per RFC 2046 should be accepted."""
        boundary = "simple-boundary"
        body = (
            "--simple-boundary\r\n"
            'Content-Disposition: form-data; name="file"; filename="test.bin"\r\n'
            "\r\n"
            "test content\r\n"
            "--simple-boundary--\r\n"
        ).encode()

        response = client.post(
            "/api/v1/upload/test/quoted-boundary",
            content=body,
            headers={"Content-Type": f'multipart/form-data; boundary="{boundary}"'},
        )
        assert response.status_code == 200

    def test_boundary_too_long_rejected(self, client: TestClient) -> None:
        """Boundary longer than 70 characters should be rejected per RFC 2046."""
        # 71 characters - exceeds RFC 2046 limit
        boundary = "a" * 71
        body = b"test content"

        response = client.post(
            "/api/v1/upload/test/long-boundary",
            content=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        assert response.status_code == 400
        assert "must be 1-70 characters" in response.json()["detail"]

    def test_empty_boundary_rejected(self, client: TestClient) -> None:
        """Empty boundary should be rejected."""
        response = client.post(
            "/api/v1/upload/test/empty-boundary",
            content=b"test",
            headers={"Content-Type": "multipart/form-data; boundary="},
        )
        assert response.status_code == 400


class TestContentDispositionValidation:
    """Tests for Content-Disposition header validation."""

    @pytest.fixture
    def client(self, tmp_path: Path) -> TestClient:
        """Create test client with temp storage."""
        storage_path = tmp_path / "storage"
        temp_path_dir = tmp_path / "temp"
        storage_path.mkdir(parents=True, exist_ok=True)
        temp_path_dir.mkdir(parents=True, exist_ok=True)

        settings = MagpieSettings(
            storage_path=storage_path,
            temp_path=temp_path_dir,
        )
        storage_service = StorageService(settings)

        app.dependency_overrides[get_magpie_settings] = lambda: settings
        app.dependency_overrides[get_storage_service] = lambda: storage_service
        app.dependency_overrides[require_write_scope] = _noop_require_write_scope

        return TestClient(app)

    def test_missing_content_disposition_rejected(self, client: TestClient) -> None:
        """Multipart part without Content-Disposition should be rejected."""
        boundary = "test-boundary"
        # Part with no Content-Disposition header
        body = ("--test-boundary\r\n\r\ntest content\r\n--test-boundary--\r\n").encode()

        response = client.post(
            "/api/v1/upload/test/no-cd",
            content=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        assert response.status_code == 400
        assert "Content-Disposition" in response.json()["detail"]

    def test_content_disposition_without_name_rejected(self, client: TestClient) -> None:
        """Content-Disposition without 'name' parameter should be rejected."""
        boundary = "test-boundary"
        # Content-Disposition without name parameter
        body = (
            "--test-boundary\r\n"
            'Content-Disposition: form-data; filename="test.bin"\r\n'
            "\r\n"
            "test content\r\n"
            "--test-boundary--\r\n"
        ).encode()

        response = client.post(
            "/api/v1/upload/test/no-name",
            content=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        assert response.status_code == 400
        # The error will be "Missing 'file' part" because the malformed CD caused
        # the field to not be recognized, which is the desired security behavior
        detail = response.json()["detail"].lower()
        assert "name" in detail or "file" in detail


class TestHeaderSizeLimits:
    """Tests for header size limit enforcement."""

    @pytest.fixture
    def client(self, tmp_path: Path) -> TestClient:
        """Create test client with temp storage."""
        storage_path = tmp_path / "storage"
        temp_path_dir = tmp_path / "temp"
        storage_path.mkdir(parents=True, exist_ok=True)
        temp_path_dir.mkdir(parents=True, exist_ok=True)

        settings = MagpieSettings(
            storage_path=storage_path,
            temp_path=temp_path_dir,
        )
        storage_service = StorageService(settings)

        app.dependency_overrides[get_magpie_settings] = lambda: settings
        app.dependency_overrides[get_storage_service] = lambda: storage_service
        app.dependency_overrides[require_write_scope] = _noop_require_write_scope

        return TestClient(app)

    def test_oversized_header_rejected(self, client: TestClient) -> None:
        """Header exceeding 16KB limit should be rejected."""
        boundary = "test-boundary"
        # Create a header value that exceeds 16KB (16384 bytes)
        huge_value = "x" * 20000
        body = (
            f"--test-boundary\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{huge_value}"\r\n'
            f"\r\n"
            f"test content\r\n"
            f"--test-boundary--\r\n"
        ).encode()

        response = client.post(
            "/api/v1/upload/test/huge-header",
            content=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        assert response.status_code == 400
        assert "header" in response.json()["detail"].lower()

    def test_reasonable_header_accepted(self, client: TestClient) -> None:
        """Reasonable header size should be accepted."""
        boundary = "test-boundary"
        # Normal-sized header (well under 16KB)
        body = (
            "--test-boundary\r\n"
            'Content-Disposition: form-data; name="file"; filename="test.bin"\r\n'
            "Content-Type: application/octet-stream\r\n"
            "\r\n"
            "test content\r\n"
            "--test-boundary--\r\n"
        ).encode()

        response = client.post(
            "/api/v1/upload/test/normal-header",
            content=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        assert response.status_code == 200
