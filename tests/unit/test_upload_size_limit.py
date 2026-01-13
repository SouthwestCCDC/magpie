"""Unit tests for upload size limit enforcement."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from magpie.config import MagpieSettings
from magpie.server.app import app
from magpie.server.deps import get_magpie_settings, get_storage_service, require_write_scope
from magpie.server.routes.upload import SizeLimitedReader, UploadSizeExceededError
from magpie.storage.service import StorageService


def _noop_require_write_scope() -> None:
    """No-op override for require_write_scope in tests."""
    return None


class TestSizeLimitedReader:
    """Tests for SizeLimitedReader helper class."""

    def test_read_within_limit(self) -> None:
        """Reading within size limit should succeed."""
        data = b"test data"
        stream = io.BytesIO(data)
        reader = SizeLimitedReader(stream, max_size=100)

        result = reader.read()
        assert result == data

    def test_read_exactly_at_limit(self) -> None:
        """Reading exactly at size limit should succeed."""
        data = b"x" * 100
        stream = io.BytesIO(data)
        reader = SizeLimitedReader(stream, max_size=100)

        result = reader.read()
        assert result == data

    def test_read_exceeds_limit(self) -> None:
        """Reading more than size limit should raise UploadSizeExceededError."""
        data = b"x" * 101
        stream = io.BytesIO(data)
        reader = SizeLimitedReader(stream, max_size=100)

        with pytest.raises(UploadSizeExceededError) as exc_info:
            reader.read()

        assert "exceeds maximum size of 100 bytes" in str(exc_info.value)

    def test_incremental_read_exceeds_limit(self) -> None:
        """Cumulative reads exceeding limit should raise error."""
        data = b"x" * 150
        stream = io.BytesIO(data)
        reader = SizeLimitedReader(stream, max_size=100)

        # First read within limit
        result1 = reader.read(50)
        assert len(result1) == 50

        # Second read crosses limit
        with pytest.raises(UploadSizeExceededError):
            reader.read(100)

    def test_seek_passthrough(self) -> None:
        """Seek should pass through to underlying stream."""
        data = b"test data"
        stream = io.BytesIO(data)
        reader = SizeLimitedReader(stream, max_size=100)

        reader.seek(5)
        assert reader.tell() == 5
        result = reader.read()
        assert result == b"data"

    def test_tell_passthrough(self) -> None:
        """Tell should return current position from underlying stream."""
        data = b"test data"
        stream = io.BytesIO(data)
        reader = SizeLimitedReader(stream, max_size=100)

        assert reader.tell() == 0
        reader.read(4)
        assert reader.tell() == 4


class TestMaxUploadSizeConfig:
    """Tests for max_upload_size configuration setting."""

    def test_default_max_upload_size_is_none(self) -> None:
        """Default max_upload_size should be None (unlimited)."""
        settings = MagpieSettings()
        assert settings.max_upload_size is None

    def test_max_upload_size_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """MAGPIE_MAX_UPLOAD_SIZE env var should override default."""
        monkeypatch.setenv("MAGPIE_MAX_UPLOAD_SIZE", "1048576")  # 1 MB
        settings = MagpieSettings()
        assert settings.max_upload_size == 1048576

    def test_max_upload_size_explicit_value(self) -> None:
        """Explicitly set max_upload_size should be used."""
        settings = MagpieSettings(max_upload_size=5000000)
        assert settings.max_upload_size == 5000000


class TestUploadSizeLimitEndpoint:
    """Integration tests for upload size limit enforcement at the endpoint."""

    @pytest.fixture
    def test_config_with_limit(self, tmp_path: Path) -> MagpieSettings:
        """Create test configuration with size limit."""
        config = MagpieSettings(storage_path=tmp_path, max_upload_size=1000)
        config.temp_path.mkdir(parents=True, exist_ok=True)
        return config

    @pytest.fixture
    def test_config_no_limit(self, tmp_path: Path) -> MagpieSettings:
        """Create test configuration without size limit."""
        config = MagpieSettings(storage_path=tmp_path, max_upload_size=None)
        config.temp_path.mkdir(parents=True, exist_ok=True)
        return config

    @pytest.fixture
    def client_with_limit(self, test_config_with_limit: MagpieSettings) -> TestClient:
        """Create test client with size limit configured."""
        storage_service = StorageService(test_config_with_limit)

        def override_storage_service() -> StorageService:
            return storage_service

        def override_settings() -> MagpieSettings:
            return test_config_with_limit

        app.dependency_overrides[get_storage_service] = override_storage_service
        app.dependency_overrides[get_magpie_settings] = override_settings
        app.dependency_overrides[require_write_scope] = _noop_require_write_scope
        yield TestClient(app)
        app.dependency_overrides.clear()

    @pytest.fixture
    def client_no_limit(self, test_config_no_limit: MagpieSettings) -> TestClient:
        """Create test client without size limit."""
        storage_service = StorageService(test_config_no_limit)

        def override_storage_service() -> StorageService:
            return storage_service

        def override_settings() -> MagpieSettings:
            return test_config_no_limit

        app.dependency_overrides[get_storage_service] = override_storage_service
        app.dependency_overrides[get_magpie_settings] = override_settings
        app.dependency_overrides[require_write_scope] = _noop_require_write_scope
        yield TestClient(app)
        app.dependency_overrides.clear()

    def test_upload_within_limit_succeeds(self, client_with_limit: TestClient) -> None:
        """Upload within size limit should succeed."""
        content = b"x" * 500  # 500 bytes, limit is 1000
        files = {"file": ("artifact.bin", io.BytesIO(content), "application/octet-stream")}

        response = client_with_limit.post("/api/v1/upload/test/artifact", files=files)

        assert response.status_code == 200
        data = response.json()
        assert data["artifact_path"] == "test/artifact"

    def test_upload_exceeding_limit_returns_413(self, client_with_limit: TestClient) -> None:
        """Upload exceeding size limit should return HTTP 413."""
        content = b"x" * 1500  # 1500 bytes, limit is 1000
        files = {"file": ("artifact.bin", io.BytesIO(content), "application/octet-stream")}

        response = client_with_limit.post("/api/v1/upload/test/artifact", files=files)

        assert response.status_code == 413
        data = response.json()
        assert "exceeds maximum" in data["detail"]

    def test_upload_without_limit_allows_large_files(self, client_no_limit: TestClient) -> None:
        """Upload without size limit should allow any size."""
        content = b"x" * 5000  # 5000 bytes, no limit
        files = {"file": ("artifact.bin", io.BytesIO(content), "application/octet-stream")}

        response = client_no_limit.post("/api/v1/upload/test/artifact", files=files)

        assert response.status_code == 200
        data = response.json()
        assert data["artifact_path"] == "test/artifact"

    def test_upload_just_under_limit_succeeds(self, client_with_limit: TestClient) -> None:
        """Upload just under size limit should succeed.

        Note: Multipart form encoding adds overhead (headers, boundaries),
        so Content-Length is larger than file content. We test with content
        well under the limit to verify boundary behavior.
        """
        # Use 800 bytes to stay under the 1000 byte limit accounting for overhead
        content = b"x" * 800
        files = {"file": ("artifact.bin", io.BytesIO(content), "application/octet-stream")}

        response = client_with_limit.post("/api/v1/upload/test/just-under", files=files)

        assert response.status_code == 200
