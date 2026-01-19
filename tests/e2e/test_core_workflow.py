"""Core workflow E2E tests for Magpie.

Tests the primary artifact storage workflows:
- System initialization
- Token creation
- Artifact upload/download
- Listing and metadata
- Tagging operations
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
from pathlib import Path

import httpx
import pytest

from tests.e2e.conftest import PROJECT_ROOT, create_token_via_api


@pytest.mark.e2e
@pytest.mark.slow
class TestSystemInitialization:
    """Tests for magpie-ctl init command."""

    def test_init_generates_admin_token(
        self,
        docker_services: dict[str, str],
        admin_token: str,
    ) -> None:
        """Verify magpie-ctl init generates a valid admin token."""
        # Token should be non-empty and have expected format
        assert admin_token
        assert admin_token.startswith("mgp_")
        assert len(admin_token) > 20


@pytest.mark.e2e
@pytest.mark.slow
class TestTokenManagement:
    """Tests for token creation via API."""

    def test_create_read_token(
        self,
        http_client: httpx.Client,
        admin_token: str,
    ) -> None:
        """Create a read-only token via API."""
        token = create_token_via_api(http_client, admin_token, "e2e-read-test", "read")
        assert token
        assert token.startswith("mgp_")

    def test_create_write_token(
        self,
        http_client: httpx.Client,
        admin_token: str,
    ) -> None:
        """Create a write token via API."""
        token = create_token_via_api(http_client, admin_token, "e2e-write-test", "write")
        assert token
        assert token.startswith("mgp_")


@pytest.mark.e2e
@pytest.mark.slow
class TestArtifactUpload:
    """Tests for artifact upload workflow."""

    def test_push_uploads_artifact(
        self,
        docker_services: dict[str, str],
        write_token: str,
        test_artifact_content: bytes,
    ) -> None:
        """Test that magpie push uploads an artifact."""
        base_url = docker_services["base_url"]

        # Create a temp file with test content
        with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as f:
            f.write(test_artifact_content)
            temp_file = f.name

        try:
            # Run magpie push command
            env = os.environ.copy()
            env["MAGPIE_SERVER"] = base_url
            env["MAGPIE_TOKEN"] = write_token

            result = subprocess.run(
                [
                    "uv",
                    "run",
                    "magpie",
                    "push",
                    temp_file,
                    "--to",
                    "e2e-tests/push-test",
                ],
                cwd=PROJECT_ROOT,
                env=env,
                capture_output=True,
                text=True,
            )

            assert result.returncode == 0, f"Push failed: {result.stderr}"
            # Output should contain the hash (64 hex chars) after "Uploaded:" or "Duplicate:"
            assert "uploaded:" in result.stdout.lower() or "duplicate:" in result.stdout.lower()
        finally:
            os.unlink(temp_file)

    def test_upload_via_api(
        self,
        authenticated_client: httpx.Client,
        test_artifact_content: bytes,
    ) -> None:
        """Test artifact upload via HTTP API."""
        # Calculate expected hash
        expected_hash = hashlib.sha256(test_artifact_content).hexdigest()

        response = authenticated_client.post(
            "/api/v1/upload/e2e-tests/api-upload-test",
            files={"file": ("artifact", test_artifact_content, "application/octet-stream")},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["hash"] == expected_hash


@pytest.mark.e2e
@pytest.mark.slow
class TestArtifactListing:
    """Tests for artifact listing workflow."""

    def test_ls_lists_uploaded_artifact(
        self,
        docker_services: dict[str, str],
        authenticated_client: httpx.Client,
        write_token: str,
        test_artifact_content: bytes,
    ) -> None:
        """Test that magpie ls lists uploaded artifacts."""
        base_url = docker_services["base_url"]

        # First upload an artifact
        authenticated_client.post(
            "/api/v1/upload/e2e-tests/ls-test",
            files={"file": ("artifact", test_artifact_content, "application/octet-stream")},
        )

        # Run magpie ls command
        env = os.environ.copy()
        env["MAGPIE_SERVER"] = base_url
        env["MAGPIE_TOKEN"] = write_token

        result = subprocess.run(
            ["uv", "run", "magpie", "ls", "e2e-tests/"],
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, f"ls failed: {result.stderr}"
        assert "ls-test" in result.stdout

    def test_list_via_api(
        self,
        authenticated_client: httpx.Client,
        test_artifact_content: bytes,
    ) -> None:
        """Test artifact listing via HTTP API."""
        # Upload an artifact first
        authenticated_client.post(
            "/api/v1/upload/e2e-tests/api-ls-test",
            files={"file": ("artifact", test_artifact_content, "application/octet-stream")},
        )

        # List artifacts
        response = authenticated_client.get("/api/v1/artifacts/e2e-tests/")

        assert response.status_code == 200
        data = response.json()
        assert "versions" in data or isinstance(data, list)


@pytest.mark.e2e
@pytest.mark.slow
class TestArtifactDownload:
    """Tests for artifact download workflow."""

    def test_get_downloads_with_verification(
        self,
        docker_services: dict[str, str],
        authenticated_client: httpx.Client,
        write_token: str,
        test_artifact_content: bytes,
    ) -> None:
        """Test that magpie get downloads and verifies artifact."""
        base_url = docker_services["base_url"]

        # Upload artifact first
        upload_response = authenticated_client.post(
            "/api/v1/upload/e2e-tests/get-test",
            files={"file": ("artifact", test_artifact_content, "application/octet-stream")},
        )
        artifact_hash = upload_response.json()["hash"]

        # Create temp output directory
        with tempfile.TemporaryDirectory() as temp_dir:
            output_file = Path(temp_dir) / "downloaded.bin"

            env = os.environ.copy()
            env["MAGPIE_SERVER"] = base_url
            env["MAGPIE_TOKEN"] = write_token

            result = subprocess.run(
                [
                    "uv",
                    "run",
                    "magpie",
                    "get",
                    "e2e-tests/get-test:latest",
                    "-o",
                    str(output_file),
                ],
                cwd=PROJECT_ROOT,
                env=env,
                capture_output=True,
                text=True,
            )

            assert result.returncode == 0, f"get failed: {result.stderr}"
            assert output_file.exists()

            # Verify content integrity
            downloaded_content = output_file.read_bytes()
            assert downloaded_content == test_artifact_content

            # Verify hash
            downloaded_hash = hashlib.sha256(downloaded_content).hexdigest()
            assert downloaded_hash == artifact_hash


@pytest.mark.e2e
@pytest.mark.slow
class TestTagging:
    """Tests for artifact tagging workflow."""

    def test_tag_creates_tag(
        self,
        docker_services: dict[str, str],
        authenticated_client: httpx.Client,
        write_token: str,
        test_artifact_content: bytes,
    ) -> None:
        """Test that magpie tag creates a tag."""
        base_url = docker_services["base_url"]

        # Upload artifact first
        upload_response = authenticated_client.post(
            "/api/v1/upload/e2e-tests/tag-test",
            files={"file": ("artifact", test_artifact_content, "application/octet-stream")},
        )
        artifact_hash = upload_response.json()["hash"]

        env = os.environ.copy()
        env["MAGPIE_SERVER"] = base_url
        env["MAGPIE_TOKEN"] = write_token

        # Create tag via CLI
        # Hash ref format: @{first 8 chars of hash}
        hash_ref = f"@{artifact_hash[:8]}"
        result = subprocess.run(
            [
                "uv",
                "run",
                "magpie",
                "tag",
                f"e2e-tests/tag-test:{hash_ref}",
                "--as",
                "v1.0",
            ],
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, f"tag failed: {result.stderr}"

    def test_tag_via_api(
        self,
        authenticated_client: httpx.Client,
        test_artifact_content: bytes,
    ) -> None:
        """Test tag creation via HTTP API."""
        # Upload artifact
        upload_response = authenticated_client.post(
            "/api/v1/upload/e2e-tests/api-tag-test",
            files={"file": ("artifact", test_artifact_content, "application/octet-stream")},
        )
        artifact_hash = upload_response.json()["hash"]

        # Create tag via API
        # API expects ref to be either a tag name or @{short_hash} format
        hash_ref = f"@{artifact_hash[:8]}"
        response = authenticated_client.post(
            f"/api/v1/artifacts/e2e-tests/api-tag-test/{hash_ref}/tags",
            json={"tag_name": "v1.0"},
        )

        assert response.status_code in (200, 201)


@pytest.mark.e2e
@pytest.mark.slow
class TestArtifactInfo:
    """Tests for artifact metadata retrieval."""

    def test_info_shows_metadata(
        self,
        docker_services: dict[str, str],
        authenticated_client: httpx.Client,
        write_token: str,
        test_artifact_content: bytes,
    ) -> None:
        """Test that magpie info shows artifact metadata."""
        base_url = docker_services["base_url"]

        # Upload artifact
        upload_response = authenticated_client.post(
            "/api/v1/upload/e2e-tests/info-test",
            files={"file": ("artifact", test_artifact_content, "application/octet-stream")},
        )
        artifact_hash = upload_response.json()["hash"]

        env = os.environ.copy()
        env["MAGPIE_SERVER"] = base_url
        env["MAGPIE_TOKEN"] = write_token

        # Info command takes artifact_ref in path:ref format
        # Use the hash ref format: @{first 8 chars of hash}
        hash_ref = f"@{artifact_hash[:8]}"
        result = subprocess.run(
            [
                "uv",
                "run",
                "magpie",
                "info",
                f"e2e-tests/info-test:{hash_ref}",
            ],
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, f"info failed: {result.stderr}"
        # Should show hash (either full or partial) and metadata
        assert (
            artifact_hash in result.stdout or hash_ref in result.stdout or "Hash" in result.stdout
        )

    def test_info_via_api(
        self,
        authenticated_client: httpx.Client,
        test_artifact_content: bytes,
    ) -> None:
        """Test artifact info via HTTP API."""
        # Upload artifact
        upload_response = authenticated_client.post(
            "/api/v1/upload/e2e-tests/api-info-test",
            files={"file": ("artifact", test_artifact_content, "application/octet-stream")},
        )
        artifact_hash = upload_response.json()["hash"]

        # Get info via API
        # API expects ref to be either a tag name or @{short_hash} format
        hash_ref = f"@{artifact_hash[:8]}"
        response = authenticated_client.get(
            f"/api/v1/artifacts/e2e-tests/api-info-test/{hash_ref}/info"
        )

        assert response.status_code == 200
        data = response.json()
        assert "size" in data or "hash" in data
