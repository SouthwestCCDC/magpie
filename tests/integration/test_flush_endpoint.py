"""Integration tests for the flush tag endpoint."""

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


def create_tag(client: TestClient, artifact_path: str, ref: str, tag_name: str) -> None:
    """Helper to create a tag on an artifact."""
    response = client.post(
        f"/api/v1/artifacts/{artifact_path}/{ref}/tags",
        json={"tag_name": tag_name},
    )
    assert response.status_code == 200


class TestDryRunPreview:
    """Tests for dry_run mode returning preview without modification."""

    def test_dry_run_returns_preview_without_modification(
        self, client: TestClient
    ) -> None:
        """Dry run returns affected artifacts without actually removing tags."""
        # Upload artifacts and create "release" tag on both
        upload_data1 = upload_artifact(client, "flush-test/artifact1", b"content1")
        upload_data2 = upload_artifact(client, "flush-test/artifact2", b"content2")

        create_tag(client, "flush-test/artifact1", upload_data1["hash_ref"], "release")
        create_tag(client, "flush-test/artifact2", upload_data2["hash_ref"], "release")

        # Dry run flush
        response = client.post(
            "/api/v1/tags/release/flush",
            params={"confirm_walk_filesystem": True, "dry_run": True},
        )

        assert response.status_code == 200
        data = response.json()

        assert data["tag_name"] == "release"
        assert data["count"] == 2
        assert data["dry_run"] is True
        assert "flush-test/artifact1" in data["affected_artifacts"]
        assert "flush-test/artifact2" in data["affected_artifacts"]

        # Verify tags still exist (not actually removed)
        info1 = client.get(
            f"/api/v1/artifacts/flush-test/artifact1/{upload_data1['hash_ref']}/info"
        )
        assert "release" in info1.json()["tags"]

        info2 = client.get(
            f"/api/v1/artifacts/flush-test/artifact2/{upload_data2['hash_ref']}/info"
        )
        assert "release" in info2.json()["tags"]


class TestConfirmedFlush:
    """Tests for confirmed flush actually removing tags."""

    def test_confirmed_flush_removes_tags(self, client: TestClient) -> None:
        """Confirmed flush actually removes tags from all artifacts."""
        # Upload artifacts and create "to-flush" tag on both
        upload_data1 = upload_artifact(client, "flush-confirm/art1", b"content1")
        upload_data2 = upload_artifact(client, "flush-confirm/art2", b"content2")

        create_tag(client, "flush-confirm/art1", upload_data1["hash_ref"], "to-flush")
        create_tag(client, "flush-confirm/art2", upload_data2["hash_ref"], "to-flush")

        # Flush without dry_run
        response = client.post(
            "/api/v1/tags/to-flush/flush",
            params={"confirm_walk_filesystem": True, "dry_run": False},
        )

        assert response.status_code == 200
        data = response.json()

        assert data["tag_name"] == "to-flush"
        assert data["count"] == 2
        assert data["dry_run"] is False

        # Verify tags are actually removed
        info1 = client.get(
            f"/api/v1/artifacts/flush-confirm/art1/{upload_data1['hash_ref']}/info"
        )
        assert "to-flush" not in info1.json()["tags"]

        info2 = client.get(
            f"/api/v1/artifacts/flush-confirm/art2/{upload_data2['hash_ref']}/info"
        )
        assert "to-flush" not in info2.json()["tags"]


class TestMissingConfirmation:
    """Tests for missing confirmation parameter."""

    def test_missing_confirmation_returns_400(self, client: TestClient) -> None:
        """Missing confirm_walk_filesystem parameter returns 400."""
        response = client.post("/api/v1/tags/some-tag/flush")

        assert response.status_code == 400
        data = response.json()
        assert "confirm_walk_filesystem" in data["detail"].lower()

    def test_confirm_walk_filesystem_false_returns_400(
        self, client: TestClient
    ) -> None:
        """confirm_walk_filesystem=false returns 400."""
        response = client.post(
            "/api/v1/tags/some-tag/flush",
            params={"confirm_walk_filesystem": False},
        )

        assert response.status_code == 400
        data = response.json()
        assert "confirm_walk_filesystem" in data["detail"].lower()


class TestFlushUnknownTag:
    """Tests for flushing unknown tags."""

    def test_flush_unknown_tag_returns_empty_result(self, client: TestClient) -> None:
        """Flushing a tag that doesn't exist returns empty result, not error."""
        # Upload an artifact so storage exists but without the target tag
        upload_artifact(client, "flush-unknown/artifact", b"some content")

        response = client.post(
            "/api/v1/tags/nonexistent-tag/flush",
            params={"confirm_walk_filesystem": True},
        )

        assert response.status_code == 200
        data = response.json()

        assert data["tag_name"] == "nonexistent-tag"
        assert data["count"] == 0
        assert data["affected_artifacts"] == []


class TestResponseFormat:
    """Tests for response format and content."""

    def test_response_includes_correct_count_and_artifacts(
        self, client: TestClient
    ) -> None:
        """Response includes correct count and affected_artifacts list."""
        # Upload 3 artifacts, only tag 2 of them with "partial"
        upload_data1 = upload_artifact(client, "flush-count/art1", b"content1")
        upload_data2 = upload_artifact(client, "flush-count/art2", b"content2")
        upload_artifact(client, "flush-count/art3", b"content3")  # No tag

        create_tag(client, "flush-count/art1", upload_data1["hash_ref"], "partial")
        create_tag(client, "flush-count/art2", upload_data2["hash_ref"], "partial")

        response = client.post(
            "/api/v1/tags/partial/flush",
            params={"confirm_walk_filesystem": True, "dry_run": True},
        )

        assert response.status_code == 200
        data = response.json()

        assert data["count"] == 2
        assert len(data["affected_artifacts"]) == 2
        assert "flush-count/art1" in data["affected_artifacts"]
        assert "flush-count/art2" in data["affected_artifacts"]
        assert "flush-count/art3" not in data["affected_artifacts"]

    def test_response_has_all_fields(self, client: TestClient) -> None:
        """Response has all expected fields."""
        response = client.post(
            "/api/v1/tags/any-tag/flush",
            params={"confirm_walk_filesystem": True},
        )

        assert response.status_code == 200
        data = response.json()

        expected_fields = {"tag_name", "affected_artifacts", "count", "dry_run"}
        assert expected_fields == set(data.keys())

    def test_dry_run_defaults_to_false(self, client: TestClient) -> None:
        """dry_run parameter defaults to false when not specified."""
        response = client.post(
            "/api/v1/tags/default-test/flush",
            params={"confirm_walk_filesystem": True},
        )

        assert response.status_code == 200
        data = response.json()

        assert data["dry_run"] is False
