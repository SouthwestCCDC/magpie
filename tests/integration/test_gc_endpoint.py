"""Integration tests for the GC endpoint."""

from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from magpie.auth.models import TokenScope
from magpie.auth.service import TokenService
from magpie.config import MagpieSettings, get_settings
from magpie.server.app import app
from magpie.server.deps import get_storage_service, get_token_service
from magpie.storage.service import StorageService


@pytest.fixture
def test_config(tmp_path: Path) -> MagpieSettings:
    """Create test configuration with temporary paths."""
    config = MagpieSettings(storage_path=tmp_path, retention_days=90)
    config.temp_path.mkdir(parents=True, exist_ok=True)
    return config


@pytest.fixture
def token_service(test_config: MagpieSettings) -> TokenService:
    """Create a TokenService instance for testing."""
    return TokenService(test_config)


@pytest.fixture
def storage_service(test_config: MagpieSettings) -> StorageService:
    """Create a StorageService instance for testing."""
    return StorageService(test_config)


@pytest.fixture
def admin_token(token_service: TokenService) -> str:
    """Create an admin token for authentication."""
    return token_service.create_token("test-admin", TokenScope.ADMIN)


@pytest.fixture
def read_token(token_service: TokenService) -> str:
    """Create a read-only token for testing non-admin access."""
    return token_service.create_token("test-reader", TokenScope.READ)


@pytest.fixture
def write_token(token_service: TokenService) -> str:
    """Create a write token for testing non-admin access."""
    return token_service.create_token("test-writer", TokenScope.WRITE)


@pytest.fixture
def client(
    token_service: TokenService, storage_service: StorageService, test_config: MagpieSettings
) -> TestClient:
    """Create test client with overridden dependencies.

    Note: This fixture removes auth overrides from the autouse conftest fixture
    so that actual token authentication is tested.
    """
    # Clear any auth overrides from the autouse conftest fixture
    # so that real authentication is tested
    app.dependency_overrides.clear()

    def override_token_service() -> TokenService:
        return token_service

    def override_storage_service() -> StorageService:
        return storage_service

    def override_settings() -> MagpieSettings:
        return test_config

    app.dependency_overrides[get_token_service] = override_token_service
    app.dependency_overrides[get_storage_service] = override_storage_service
    app.dependency_overrides[get_settings] = override_settings
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def _upload_artifact(
    client: TestClient,
    path: str,
    content: bytes,
    uploaded_by: str = "test-user",
    scope: str = "write",
) -> dict:
    """Helper to upload an artifact and return the response data.

    Uses X-Magpie-Scope header to simulate Caddy forward_auth for scope checking.
    """
    files = {"file": ("artifact.bin", io.BytesIO(content), "application/octet-stream")}
    headers = {"X-Magpie-Scope": scope}

    response = client.post(
        f"/api/v1/upload/{path}",
        files=files,
        params={"uploaded_by": uploaded_by},
        headers=headers,
    )
    assert response.status_code == 200
    return response.json()


class TestGCEndpointAuth:
    """Tests for GC endpoint authentication and authorization."""

    def test_gc_requires_admin_scope(self, client: TestClient, admin_token: str) -> None:
        """GC endpoint requires admin scope."""
        response = client.post(
            "/api/v1/gc",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        assert response.status_code == 200

    def test_gc_with_read_scope_returns_403(self, client: TestClient, read_token: str) -> None:
        """GC with read scope returns 403."""
        response = client.post(
            "/api/v1/gc",
            headers={"Authorization": f"Bearer {read_token}"},
        )

        assert response.status_code == 403
        assert "Admin scope required" in response.json()["detail"]

    def test_gc_with_write_scope_returns_403(self, client: TestClient, write_token: str) -> None:
        """GC with write scope returns 403."""
        response = client.post(
            "/api/v1/gc",
            headers={"Authorization": f"Bearer {write_token}"},
        )

        assert response.status_code == 403

    def test_gc_without_auth_returns_401(self, client: TestClient) -> None:
        """GC without authorization returns 401."""
        response = client.post("/api/v1/gc")

        assert response.status_code == 401


class TestGCEndpointFunctionality:
    """Tests for GC endpoint functionality."""

    def test_gc_dry_run_returns_preview(self, client: TestClient, admin_token: str) -> None:
        """GC with dry_run returns preview without modification."""
        # Upload an artifact first
        _upload_artifact(client, "gc-test/artifact", b"test content")

        response = client.post(
            "/api/v1/gc",
            headers={"Authorization": f"Bearer {admin_token}"},
            params={"dry_run": "true"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["dry_run"] is True
        assert "artifacts_scanned" in data
        assert "blobs_found" in data
        assert "blobs_deleted" in data
        assert "space_reclaimed_bytes" in data

    def test_gc_returns_zero_for_tagged_blobs(self, client: TestClient, admin_token: str) -> None:
        """GC returns zero deleted blobs when all blobs are tagged."""
        # Upload artifact (auto-tagged as "latest")
        _upload_artifact(client, "gc-test/tagged", b"tagged content")

        response = client.post(
            "/api/v1/gc",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        assert response.status_code == 200
        data = response.json()
        # Should not delete any blobs since they're all tagged
        assert data["blobs_deleted"] == 0

    def test_gc_scans_artifacts(self, client: TestClient, admin_token: str) -> None:
        """GC scans uploaded artifacts."""
        # Upload multiple artifacts
        _upload_artifact(client, "gc-test/a1", b"content a1")
        _upload_artifact(client, "gc-test/a2", b"content a2")
        _upload_artifact(client, "gc-test/a3", b"content a3")

        response = client.post(
            "/api/v1/gc",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["artifacts_scanned"] >= 3
        assert data["blobs_found"] >= 3

    def test_gc_response_format(self, client: TestClient, admin_token: str) -> None:
        """GC response includes all expected fields."""
        response = client.post(
            "/api/v1/gc",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        assert response.status_code == 200
        data = response.json()

        required_fields = {
            "dry_run",
            "artifacts_scanned",
            "blobs_found",
            "blobs_deleted",
            "space_reclaimed_bytes",
            "symlinks_checked",
            "symlinks_fixed",
            "items_removed",
        }
        assert required_fields == set(data.keys())

    def test_gc_empty_storage(self, client: TestClient, admin_token: str) -> None:
        """GC on empty storage returns zero counts."""
        response = client.post(
            "/api/v1/gc",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["artifacts_scanned"] == 0
        assert data["blobs_found"] == 0
        assert data["blobs_deleted"] == 0
        assert data["space_reclaimed_bytes"] == 0
        assert data["symlinks_checked"] == 0
        assert data["symlinks_fixed"] == 0


class TestGCDeletesUntaggedBlobs:
    """Tests for GC deleting untagged blobs."""

    def test_gc_deletes_old_untagged_blob(
        self, client: TestClient, admin_token: str, test_config: MagpieSettings
    ) -> None:
        """GC deletes untagged blobs older than retention period."""
        # Upload artifact
        upload_data = _upload_artifact(client, "gc-delete-test/artifact", b"old content")
        hash_value = upload_data["hash"]

        # Remove the "latest" tag to make blob untagged
        client.delete(
            "/api/v1/artifacts/gc-delete-test/artifact/tags/latest",
            headers={"X-Magpie-Scope": "write"},
        )

        # Manually age the blob by modifying metadata
        # Metadata files use short hash (first 8 chars) as filename
        artifact_dir = test_config.storage_path / "gc-delete-test" / "artifact"
        metadata_file = artifact_dir / "metadata" / f"{hash_value[:8]}.json"

        if metadata_file.exists():
            import json

            with open(metadata_file) as f:
                metadata = json.load(f)

            # Set uploaded_at to 100 days ago
            old_time = datetime.now(timezone.utc) - timedelta(days=100)
            metadata["uploaded_at"] = old_time.isoformat()

            with open(metadata_file, "w") as f:
                json.dump(metadata, f)

        # Run GC
        response = client.post(
            "/api/v1/gc",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["blobs_deleted"] >= 1
        assert data["space_reclaimed_bytes"] > 0

    def test_gc_dry_run_does_not_delete(
        self, client: TestClient, admin_token: str, test_config: MagpieSettings
    ) -> None:
        """GC dry run does not actually delete blobs."""
        # Upload artifact
        upload_data = _upload_artifact(client, "gc-dryrun-test/artifact", b"keep this")
        hash_value = upload_data["hash"]

        # Remove the tag
        client.delete(
            "/api/v1/artifacts/gc-dryrun-test/artifact/tags/latest",
            headers={"X-Magpie-Scope": "write"},
        )

        # Age the blob
        # Metadata files use short hash (first 8 chars) as filename
        artifact_dir = test_config.storage_path / "gc-dryrun-test" / "artifact"
        metadata_file = artifact_dir / "metadata" / f"{hash_value[:8]}.json"

        if metadata_file.exists():
            import json

            with open(metadata_file) as f:
                metadata = json.load(f)

            old_time = datetime.now(timezone.utc) - timedelta(days=100)
            metadata["uploaded_at"] = old_time.isoformat()

            with open(metadata_file, "w") as f:
                json.dump(metadata, f)

        # Run GC in dry run mode
        response = client.post(
            "/api/v1/gc",
            headers={"Authorization": f"Bearer {admin_token}"},
            params={"dry_run": "true"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["dry_run"] is True
        assert data["blobs_deleted"] >= 1

        # Verify blob still exists (blob files use short hash as filename)
        blob_file = artifact_dir / "blobs" / hash_value[:8]
        assert blob_file.exists(), "Blob should not be deleted in dry run mode"


class TestGCDirectoryCleanup:
    """Tests for GC endpoint directory cleanup after blob deletion."""

    def test_gc_response_includes_items_removed(
        self, client: TestClient, admin_token: str
    ) -> None:
        """GC response includes items_removed field."""
        response = client.post(
            "/api/v1/gc",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "items_removed" in data

    def test_gc_removes_empty_artifact_directories(
        self, client: TestClient, admin_token: str, test_config: MagpieSettings
    ) -> None:
        """GC removes empty artifact directories after deleting all blobs."""
        # Upload artifact
        upload_data = _upload_artifact(client, "gc-cleanup-test/artifact", b"cleanup content")
        hash_value = upload_data["hash"]

        # Remove the "latest" tag to make blob untagged
        client.delete(
            "/api/v1/artifacts/gc-cleanup-test/artifact/tags/latest",
            headers={"X-Magpie-Scope": "write"},
        )

        # Age the blob
        artifact_dir = test_config.storage_path / "gc-cleanup-test" / "artifact"
        metadata_file = artifact_dir / "metadata" / f"{hash_value[:8]}.json"

        if metadata_file.exists():
            import json

            with open(metadata_file) as f:
                metadata = json.load(f)

            old_time = datetime.now(timezone.utc) - timedelta(days=100)
            metadata["uploaded_at"] = old_time.isoformat()

            with open(metadata_file, "w") as f:
                json.dump(metadata, f)

        # Run GC
        response = client.post(
            "/api/v1/gc",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["blobs_deleted"] >= 1
        assert data["items_removed"] >= 1

        # Artifact directory should be cleaned up
        assert not artifact_dir.exists()

    def test_gc_dry_run_reports_directories_to_remove(
        self, client: TestClient, admin_token: str, test_config: MagpieSettings
    ) -> None:
        """GC dry run reports directories that would be removed."""
        # Upload artifact
        upload_data = _upload_artifact(client, "gc-dryrun-cleanup/artifact", b"dryrun cleanup")
        hash_value = upload_data["hash"]

        # Remove tag
        client.delete(
            "/api/v1/artifacts/gc-dryrun-cleanup/artifact/tags/latest",
            headers={"X-Magpie-Scope": "write"},
        )

        # Age the blob
        artifact_dir = test_config.storage_path / "gc-dryrun-cleanup" / "artifact"
        metadata_file = artifact_dir / "metadata" / f"{hash_value[:8]}.json"

        if metadata_file.exists():
            import json

            with open(metadata_file) as f:
                metadata = json.load(f)

            old_time = datetime.now(timezone.utc) - timedelta(days=100)
            metadata["uploaded_at"] = old_time.isoformat()

            with open(metadata_file, "w") as f:
                json.dump(metadata, f)

        # Run GC in dry run mode
        response = client.post(
            "/api/v1/gc",
            headers={"Authorization": f"Bearer {admin_token}"},
            params={"dry_run": "true"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["dry_run"] is True
        assert data["items_removed"] >= 1

        # Artifact directory should still exist (dry run)
        assert artifact_dir.exists()

    def test_gc_preserves_artifact_with_tags(
        self, client: TestClient, admin_token: str, test_config: MagpieSettings
    ) -> None:
        """GC preserves artifact directories when tags still exist."""
        # Upload artifact (has "latest" tag)
        _upload_artifact(client, "gc-preserve-test/artifact", b"preserve content")

        artifact_dir = test_config.storage_path / "gc-preserve-test" / "artifact"
        assert artifact_dir.exists()

        # Run GC
        response = client.post(
            "/api/v1/gc",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["blobs_deleted"] == 0

        # Artifact directory should still exist
        assert artifact_dir.exists()
        assert (artifact_dir / ".magpie").exists()
