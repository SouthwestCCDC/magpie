"""Unit tests for Magpie API error handling."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from magpie.config import get_settings
from magpie.server.errors import ErrorResponse, register_exception_handlers
from magpie.storage.exceptions import (
    ArtifactNotFoundError,
    BlobExistsError,
    HashMismatchError,
    ManifestCorruptError,
    StorageError,
)


class TestErrorResponseModel:
    """Tests for ErrorResponse Pydantic model."""

    def test_serialization_basic(self) -> None:
        """ErrorResponse serializes to dict with required fields."""
        response = ErrorResponse(error="TestError", message="Test message")
        data = response.model_dump()
        assert data["error"] == "TestError"
        assert data["message"] == "Test message"
        assert data["detail"] is None

    def test_serialization_with_detail(self) -> None:
        """ErrorResponse serializes with optional detail field."""
        response = ErrorResponse(error="TestError", message="Test message", detail={"key": "value"})
        data = response.model_dump()
        assert data["detail"] == {"key": "value"}

    def test_json_serialization(self) -> None:
        """ErrorResponse produces valid JSON."""
        response = ErrorResponse(error="TestError", message="Test message")
        json_str = response.model_dump_json()
        assert '"error":"TestError"' in json_str
        assert '"message":"Test message"' in json_str


class TestExceptionHandlers:
    """Tests for exception handler status code mappings."""

    @pytest.fixture
    def test_app(self) -> FastAPI:
        """Create a test FastAPI app with exception handlers."""
        app = FastAPI()
        register_exception_handlers(app)

        @app.get("/raise-artifact-not-found")
        async def raise_artifact_not_found() -> None:
            raise ArtifactNotFoundError("Artifact abc123 not found")

        @app.get("/raise-blob-exists")
        async def raise_blob_exists() -> None:
            raise BlobExistsError("Blob already exists")

        @app.get("/raise-hash-mismatch")
        async def raise_hash_mismatch() -> None:
            raise HashMismatchError("Hash does not match")

        @app.get("/raise-manifest-corrupt")
        async def raise_manifest_corrupt() -> None:
            raise ManifestCorruptError("Manifest is corrupted")

        @app.get("/raise-storage-error")
        async def raise_storage_error() -> None:
            raise StorageError("Generic storage error")

        @app.get("/raise-generic")
        async def raise_generic() -> None:
            raise RuntimeError("Unexpected error")

        return app

    @pytest.fixture
    def client(self, test_app: FastAPI) -> TestClient:
        """Create test client that doesn't re-raise server exceptions."""
        return TestClient(test_app, raise_server_exceptions=False)

    def test_artifact_not_found_returns_404(self, client: TestClient) -> None:
        """ArtifactNotFoundError maps to HTTP 404."""
        response = client.get("/raise-artifact-not-found")
        assert response.status_code == 404
        data = response.json()
        assert data["error"] == "ArtifactNotFoundError"
        assert "abc123" in data["message"]

    def test_blob_exists_returns_409(self, client: TestClient) -> None:
        """BlobExistsError maps to HTTP 409."""
        response = client.get("/raise-blob-exists")
        assert response.status_code == 409
        data = response.json()
        assert data["error"] == "BlobExistsError"

    def test_hash_mismatch_returns_400(self, client: TestClient) -> None:
        """HashMismatchError maps to HTTP 400."""
        response = client.get("/raise-hash-mismatch")
        assert response.status_code == 400
        data = response.json()
        assert data["error"] == "HashMismatchError"

    def test_manifest_corrupt_returns_500(self, client: TestClient) -> None:
        """ManifestCorruptError maps to HTTP 500."""
        response = client.get("/raise-manifest-corrupt")
        assert response.status_code == 500
        data = response.json()
        assert data["error"] == "ManifestCorruptError"

    def test_storage_error_returns_500(self, client: TestClient) -> None:
        """StorageError (catch-all) maps to HTTP 500."""
        response = client.get("/raise-storage-error")
        assert response.status_code == 500
        data = response.json()
        assert data["error"] == "StorageError"

    def test_generic_exception_returns_500(self, client: TestClient) -> None:
        """Generic Exception maps to HTTP 500."""
        response = client.get("/raise-generic")
        assert response.status_code == 500
        data = response.json()
        assert data["error"] == "InternalServerError"
        assert data["detail"] is None


class TestErrorResponseFormat:
    """Tests for error response JSON structure."""

    @pytest.fixture
    def test_app(self) -> FastAPI:
        """Create a test FastAPI app with exception handlers."""
        app = FastAPI()
        register_exception_handlers(app)

        @app.get("/raise-error")
        async def raise_error() -> None:
            raise ArtifactNotFoundError("Test error message")

        return app

    @pytest.fixture
    def client(self, test_app: FastAPI) -> TestClient:
        """Create test client."""
        return TestClient(test_app)

    def test_response_has_error_field(self, client: TestClient) -> None:
        """Response contains 'error' field with exception type."""
        response = client.get("/raise-error")
        data = response.json()
        assert "error" in data
        assert isinstance(data["error"], str)

    def test_response_has_message_field(self, client: TestClient) -> None:
        """Response contains 'message' field with error message."""
        response = client.get("/raise-error")
        data = response.json()
        assert "message" in data
        assert isinstance(data["message"], str)

    def test_response_has_detail_field(self, client: TestClient) -> None:
        """Response contains 'detail' field (can be null)."""
        response = client.get("/raise-error")
        data = response.json()
        assert "detail" in data

    def test_response_content_type_is_json(self, client: TestClient) -> None:
        """Response content-type is application/json."""
        response = client.get("/raise-error")
        assert response.headers["content-type"] == "application/json"


class TestDebugMode:
    """Tests for debug mode exception details."""

    @pytest.fixture
    def debug_app(self, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
        """Create test app with debug mode enabled."""
        monkeypatch.setenv("MAGPIE_DEBUG", "true")
        get_settings.cache_clear()

        app = FastAPI()
        register_exception_handlers(app)

        @app.get("/raise-generic")
        async def raise_generic() -> None:
            raise RuntimeError("Debug test error")

        return app

    @pytest.fixture
    def debug_client(self, debug_app: FastAPI) -> TestClient:
        """Create test client for debug app that doesn't re-raise server exceptions."""
        return TestClient(debug_app, raise_server_exceptions=False)

    def test_debug_mode_includes_traceback(
        self, debug_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Debug mode includes traceback in detail field."""
        response = debug_client.get("/raise-generic")
        data = response.json()
        assert data["detail"] is not None
        assert "traceback" in data["detail"]
        assert "RuntimeError" in data["detail"]["traceback"]
        # Clean up
        get_settings.cache_clear()

    def test_non_debug_mode_excludes_traceback(self) -> None:
        """Non-debug mode excludes traceback from detail field."""
        get_settings.cache_clear()

        app = FastAPI()
        register_exception_handlers(app)

        @app.get("/raise-generic")
        async def raise_generic() -> None:
            raise RuntimeError("Non-debug test error")

        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/raise-generic")
        data = response.json()
        assert data["detail"] is None
