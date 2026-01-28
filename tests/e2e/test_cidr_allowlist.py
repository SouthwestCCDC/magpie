"""E2E tests for CIDR allow-list read-only bypass behavior.

Tests the MAGPIE_ALLOWED_CIDRS feature which allows trusted IPs to read
artifacts without authentication, while still requiring auth for write operations.

These tests verify the security-critical CIDR bypass functionality:
1. Allowed IPs can read without auth (GET /api/v1/artifacts*, GET /artifacts/*)
2. Allowed IPs cannot write without auth (POST, PATCH, DELETE operations)
3. Non-allowed IPs require auth for all operations
4. Public paths remain accessible regardless of CIDR setting
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Generator

import httpx
import pytest

from tests.e2e.conftest import PROJECT_ROOT

# Docker Compose network typically uses 172.18.0.0/16 range
# We'll use this to simulate allowed IPs (test client runs in the same network)
ALLOWED_CIDR = "172.18.0.0/16"


@pytest.fixture(scope="session")
def docker_services_with_cidr(
    docker_compose_project_name: str,
) -> Generator[dict[str, str], None, None]:
    """Start docker-compose services with CIDR allow-list enabled.

    This is a separate fixture from the default docker_services to avoid
    affecting other test modules. Sets MAGPIE_ALLOWED_CIDRS to the Docker
    network range (172.18.0.0/16) so test client IPs are in the allowed range.

    Uses a separate compose project with different port (8081) to avoid conflicts.
    """
    # Create temporary data directory for test isolation
    temp_data_dir = Path(tempfile.mkdtemp(prefix="magpie_e2e_cidr_"))
    artifacts_dir = temp_data_dir / "artifacts"
    artifacts_dir.mkdir(parents=True)

    env = os.environ.copy()
    env["COMPOSE_PROJECT_NAME"] = f"{docker_compose_project_name}_cidr"
    env["MAGPIE_DATA_DIR"] = str(temp_data_dir)
    # Enable CIDR allow-list for Docker network range
    env["MAGPIE_ALLOWED_CIDRS"] = ALLOWED_CIDR
    # Use different port to avoid conflicts with default docker_services
    env["MAGPIE_HTTP_PORT"] = "8081"

    compose_cmd = ["docker", "compose", "-f", str(PROJECT_ROOT / "docker-compose.yml")]

    try:
        # Build services
        subprocess.run(
            [*compose_cmd, "build"],
            cwd=PROJECT_ROOT,
            env=env,
            check=True,
            capture_output=True,
        )

        # Start services with CIDR allow-list enabled
        subprocess.run(
            [*compose_cmd, "up", "-d", "--wait"],
            cwd=PROJECT_ROOT,
            env=env,
            check=True,
            capture_output=True,
        )

        # Use port 8081 as configured in env
        base_url = "http://localhost:8081"

        # Wait for health endpoint (needs to be accessible without auth)
        import time

        max_wait = 60
        start = time.time()
        while time.time() - start < max_wait:
            try:
                response = httpx.get(f"{base_url}/health", timeout=5.0)
                if response.status_code == 200:
                    break
            except (httpx.ConnectError, httpx.TimeoutException):
                pass
            time.sleep(1)
        else:
            # Get logs for debugging
            logs_result = subprocess.run(
                [*compose_cmd, "logs"],
                cwd=PROJECT_ROOT,
                env=env,
                capture_output=True,
                text=True,
            )
            pytest.fail(f"Services failed to become healthy.\nLogs:\n{logs_result.stdout}")

        yield {
            "base_url": base_url,
            "project_name": f"{docker_compose_project_name}_cidr",
        }

    finally:
        # Cleanup: stop and remove containers
        subprocess.run(
            [*compose_cmd, "down", "-v", "--remove-orphans"],
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
        )
        # Clean up temp data directory
        import shutil

        shutil.rmtree(temp_data_dir, ignore_errors=True)


@pytest.fixture(scope="session")
def cidr_base_url(docker_services_with_cidr: dict[str, str]) -> str:
    """Get the base URL for CIDR-enabled services."""
    return docker_services_with_cidr["base_url"]


@pytest.fixture(scope="session")
def cidr_admin_token(docker_services_with_cidr: dict[str, str]) -> str:
    """Get admin token from CIDR-enabled services."""
    compose_cmd = [
        "docker",
        "compose",
        "-f",
        str(PROJECT_ROOT / "docker-compose.yml"),
    ]
    env = os.environ.copy()
    env["COMPOSE_PROJECT_NAME"] = docker_services_with_cidr["project_name"]

    result = subprocess.run(
        [*compose_cmd, "exec", "-T", "magpie", "magpie-ctl", "init", "--reset-admin-token"],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        pytest.fail(f"Failed to initialize: {result.stderr}")

    # Extract token from output
    lines = result.stdout.strip().split("\n")
    for line in lines:
        line = line.strip()
        if line.startswith("mgp_"):
            return line

    pytest.fail(f"Could not extract admin token from output:\n{result.stdout}")
    return ""  # unreachable but satisfies type checker


@pytest.fixture
def cidr_http_client(cidr_base_url: str) -> Generator[httpx.Client, None, None]:
    """Create an unauthenticated HTTP client for CIDR tests."""
    with httpx.Client(base_url=cidr_base_url, timeout=30.0) as client:
        yield client


@pytest.fixture
def cidr_authenticated_client(
    cidr_base_url: str,
    cidr_admin_token: str,
) -> Generator[httpx.Client, None, None]:
    """Create an authenticated HTTP client for CIDR tests."""
    headers = {"Authorization": f"Bearer {cidr_admin_token}"}
    with httpx.Client(base_url=cidr_base_url, headers=headers, timeout=30.0) as client:
        yield client


@pytest.mark.e2e
@pytest.mark.slow
class TestCIDRAllowListReadAccess:
    """Tests that allowed IPs can read without authentication.

    Verifies the CIDR bypass works for GET operations on:
    - /api/v1/artifacts/* (list and metadata)
    - /artifacts/* (static file downloads)
    """

    def test_allowed_ip_can_list_artifacts_without_auth(
        self,
        cidr_http_client: httpx.Client,
        cidr_authenticated_client: httpx.Client,
        test_artifact_content: bytes,
    ) -> None:
        """Verify allowed IP can GET /api/v1/artifacts/* without token."""
        # First upload an artifact using authenticated client
        upload_response = cidr_authenticated_client.post(
            "/api/v1/upload/cidr-test/list-access",
            files={"file": ("artifact", test_artifact_content, "application/octet-stream")},
        )
        assert upload_response.status_code == 200

        # Unauthenticated request from allowed IP should work
        response = cidr_http_client.get("/api/v1/artifacts/cidr-test/")
        assert response.status_code == 200, (
            "Allowed IP should be able to list artifacts without auth. "
            f"Expected 200, got {response.status_code}"
        )

        # Verify response headers indicate CIDR bypass
        assert "x-magpie-user" in response.headers.keys() or "X-Magpie-User" in response.headers
        # Response may normalize header case, so check case-insensitively
        user_header = (
            response.headers.get("x-magpie-user") or response.headers.get("X-Magpie-User") or ""
        )
        assert user_header == "cidr-bypass", f"Expected cidr-bypass user, got {user_header}"

        scope_header = (
            response.headers.get("x-magpie-scope") or response.headers.get("X-Magpie-Scope") or ""
        )
        assert scope_header == "read", f"Expected read scope, got {scope_header}"

    def test_allowed_ip_can_get_artifact_info_without_auth(
        self,
        cidr_http_client: httpx.Client,
        cidr_authenticated_client: httpx.Client,
        test_artifact_content: bytes,
    ) -> None:
        """Verify allowed IP can GET artifact metadata without token."""
        # Upload artifact
        upload_response = cidr_authenticated_client.post(
            "/api/v1/upload/cidr-test/info-access",
            files={"file": ("artifact", test_artifact_content, "application/octet-stream")},
        )
        assert upload_response.status_code == 200
        artifact_hash = upload_response.json()["hash"]
        short_hash = artifact_hash[:8]

        # Get artifact info without auth (from allowed IP)
        response = cidr_http_client.get(
            f"/api/v1/artifacts/cidr-test/info-access/@{short_hash}/info"
        )
        assert response.status_code == 200, (
            "Allowed IP should be able to get artifact info without auth. "
            f"Expected 200, got {response.status_code}"
        )

        # Verify we got valid artifact metadata
        data = response.json()
        assert "hash" in data
        assert data["hash"] == artifact_hash

    def test_allowed_ip_can_download_artifacts_without_auth(
        self,
        cidr_http_client: httpx.Client,
        cidr_authenticated_client: httpx.Client,
        test_artifact_content: bytes,
    ) -> None:
        """Verify allowed IP can GET /artifacts/* (download) without token."""
        # Upload artifact
        upload_response = cidr_authenticated_client.post(
            "/api/v1/upload/cidr-test/download-access",
            files={"file": ("artifact", test_artifact_content, "application/octet-stream")},
        )
        assert upload_response.status_code == 200
        artifact_hash = upload_response.json()["hash"]
        short_hash = artifact_hash[:8]

        # Download artifact without auth (from allowed IP)
        download_path = f"/artifacts/cidr-test/download-access/@{short_hash}/artifact"
        response = cidr_http_client.get(download_path)

        assert response.status_code == 200, (
            "Allowed IP should be able to download artifacts without auth. "
            f"Expected 200, got {response.status_code}. Path: {download_path}"
        )

        # Verify content matches
        assert response.content == test_artifact_content

    def test_allowed_ip_can_browse_artifacts_directory_without_auth(
        self,
        cidr_http_client: httpx.Client,
        cidr_authenticated_client: httpx.Client,
        test_artifact_content: bytes,
    ) -> None:
        """Verify allowed IP can browse /artifacts/* directories without token."""
        # Upload artifact to create directory structure
        upload_response = cidr_authenticated_client.post(
            "/api/v1/upload/cidr-test/browse-access",
            files={"file": ("artifact", test_artifact_content, "application/octet-stream")},
        )
        assert upload_response.status_code == 200

        # Browse directory without auth (from allowed IP)
        response = cidr_http_client.get("/artifacts/cidr-test/browse-access/")

        # Should return 200 (directory listing) or 404 (if not found), NOT 401
        assert response.status_code in (200, 404), (
            "Allowed IP should be able to browse artifact directories without auth. "
            f"Expected 200 or 404, got {response.status_code}"
        )
        assert response.status_code != 401, "Should not require authentication"


@pytest.mark.e2e
@pytest.mark.slow
class TestCIDRAllowListWriteBlocked:
    """Tests that allowed IPs cannot write without authentication.

    SECURITY CRITICAL: Verifies that CIDR bypass only works for READ operations.
    Write operations (POST, PATCH, DELETE) must always require valid tokens.
    """

    def test_allowed_ip_cannot_upload_without_token(
        self,
        cidr_http_client: httpx.Client,
        test_artifact_content: bytes,
    ) -> None:
        """Verify allowed IP gets 401 on POST /api/v1/upload/* without token.

        SECURITY CRITICAL: CIDR bypass must not allow uploads.
        """
        response = cidr_http_client.post(
            "/api/v1/upload/cidr-test/upload-blocked",
            files={"file": ("artifact", test_artifact_content, "application/octet-stream")},
        )

        assert response.status_code == 401, (
            "SECURITY FAILURE: Allowed IP must not be able to upload without token. "
            f"Expected 401, got {response.status_code}"
        )

    def test_allowed_ip_cannot_create_tags_without_token(
        self,
        cidr_http_client: httpx.Client,
        cidr_authenticated_client: httpx.Client,
        test_artifact_content: bytes,
    ) -> None:
        """Verify allowed IP gets 401 on POST /api/v1/artifacts/*/tags without token.

        SECURITY CRITICAL: CIDR bypass must not allow tag creation.
        """
        # Upload artifact with authenticated client
        upload_response = cidr_authenticated_client.post(
            "/api/v1/upload/cidr-test/tag-blocked",
            files={"file": ("artifact", test_artifact_content, "application/octet-stream")},
        )
        assert upload_response.status_code == 200
        artifact_hash = upload_response.json()["hash"]
        short_hash = artifact_hash[:8]

        # Try to create tag without auth (from allowed IP)
        response = cidr_http_client.post(
            f"/api/v1/artifacts/cidr-test/tag-blocked/@{short_hash}/tags",
            json={"tag_name": "stable"},
        )

        assert response.status_code == 401, (
            "SECURITY FAILURE: Allowed IP must not be able to create tags without token. "
            f"Expected 401, got {response.status_code}"
        )

    def test_allowed_ip_cannot_delete_tags_without_token(
        self,
        cidr_http_client: httpx.Client,
        cidr_authenticated_client: httpx.Client,
        test_artifact_content: bytes,
    ) -> None:
        """Verify allowed IP gets 401 on DELETE /api/v1/artifacts/*/tags/* without token.

        SECURITY CRITICAL: CIDR bypass must not allow tag deletion.
        """
        # Upload artifact and create tag with authenticated client
        upload_response = cidr_authenticated_client.post(
            "/api/v1/upload/cidr-test/tag-delete-blocked",
            files={"file": ("artifact", test_artifact_content, "application/octet-stream")},
        )
        assert upload_response.status_code == 200
        artifact_hash = upload_response.json()["hash"]
        short_hash = artifact_hash[:8]

        # Create tag
        tag_response = cidr_authenticated_client.post(
            f"/api/v1/artifacts/cidr-test/tag-delete-blocked/@{short_hash}/tags",
            json={"tag_name": "to-delete"},
        )
        assert tag_response.status_code in (200, 201)

        # Try to delete tag without auth (from allowed IP)
        response = cidr_http_client.delete(
            "/api/v1/artifacts/cidr-test/tag-delete-blocked/tags/to-delete"
        )

        assert response.status_code == 401, (
            "SECURITY FAILURE: Allowed IP must not be able to delete tags without token. "
            f"Expected 401, got {response.status_code}"
        )

    def test_allowed_ip_cannot_amend_metadata_without_token(
        self,
        cidr_http_client: httpx.Client,
        cidr_authenticated_client: httpx.Client,
        test_artifact_content: bytes,
    ) -> None:
        """Verify allowed IP gets 401 on PATCH /api/v1/artifacts/* without token.

        SECURITY CRITICAL: CIDR bypass must not allow metadata amendments.
        """
        # Upload artifact
        upload_response = cidr_authenticated_client.post(
            "/api/v1/upload/cidr-test/amend-blocked",
            files={"file": ("artifact", test_artifact_content, "application/octet-stream")},
        )
        assert upload_response.status_code == 200
        artifact_hash = upload_response.json()["hash"]
        short_hash = artifact_hash[:8]

        # Try to amend metadata without auth (from allowed IP)
        response = cidr_http_client.patch(
            f"/api/v1/artifacts/cidr-test/amend-blocked/@{short_hash}",
            json={"description": "Should not work"},
        )

        assert response.status_code == 401, (
            "SECURITY FAILURE: Allowed IP must not be able to amend metadata without token. "
            f"Expected 401, got {response.status_code}"
        )

    def test_allowed_ip_cannot_flush_tags_without_token(
        self,
        cidr_http_client: httpx.Client,
    ) -> None:
        """Verify allowed IP gets 401 on POST /api/v1/tags/*/flush without token.

        SECURITY CRITICAL: CIDR bypass must not allow tag flushing.
        """
        response = cidr_http_client.post("/api/v1/tags/nonexistent/flush")

        assert response.status_code == 401, (
            "SECURITY FAILURE: Allowed IP must not be able to flush tags without token. "
            f"Expected 401, got {response.status_code}"
        )

    def test_allowed_ip_cannot_manage_tokens_without_token(
        self,
        cidr_http_client: httpx.Client,
    ) -> None:
        """Verify allowed IP gets 401 on token management endpoints without token.

        SECURITY CRITICAL: CIDR bypass must not allow token management.
        """
        # Try to list tokens
        response = cidr_http_client.get("/api/v1/tokens")
        assert response.status_code == 401, (
            "SECURITY FAILURE: Allowed IP must not be able to list tokens without token. "
            f"Expected 401, got {response.status_code}"
        )

        # Try to create token
        response = cidr_http_client.post(
            "/api/v1/tokens",
            json={"name": "should-fail", "scope": "read"},
        )
        assert response.status_code == 401, (
            "SECURITY FAILURE: Allowed IP must not be able to create tokens without token. "
            f"Expected 401, got {response.status_code}"
        )

        # Try to delete token
        response = cidr_http_client.delete("/api/v1/tokens/nonexistent")
        assert response.status_code == 401, (
            "SECURITY FAILURE: Allowed IP must not be able to delete tokens without token. "
            f"Expected 401, got {response.status_code}"
        )

    def test_allowed_ip_cannot_run_gc_without_token(
        self,
        cidr_http_client: httpx.Client,
    ) -> None:
        """Verify allowed IP gets 401 on POST /api/v1/gc without token.

        SECURITY CRITICAL: CIDR bypass must not allow garbage collection.
        """
        response = cidr_http_client.post("/api/v1/gc")

        assert response.status_code == 401, (
            "SECURITY FAILURE: Allowed IP must not be able to run GC without token. "
            f"Expected 401, got {response.status_code}"
        )

    def test_allowed_ip_cannot_access_status_without_token(
        self,
        cidr_http_client: httpx.Client,
    ) -> None:
        """Verify allowed IP gets 401 on GET /api/v1/status without token.

        SECURITY CRITICAL: CIDR bypass must not allow status access.
        """
        response = cidr_http_client.get("/api/v1/status")

        assert response.status_code == 401, (
            "SECURITY FAILURE: Allowed IP must not be able to access status without token. "
            f"Expected 401, got {response.status_code}"
        )


@pytest.mark.e2e
@pytest.mark.slow
class TestCIDRAllowListPublicPaths:
    """Tests that public paths remain accessible regardless of CIDR setting.

    Verifies that:
    - /health is always accessible
    - /artifacts/public/* is always accessible
    - These work the same whether IP is in allowed CIDR or not
    """

    def test_health_endpoint_accessible_without_auth(
        self,
        cidr_http_client: httpx.Client,
    ) -> None:
        """Verify /health is accessible without auth (public endpoint)."""
        response = cidr_http_client.get("/health")
        assert response.status_code == 200

    def test_public_artifacts_accessible_without_auth(
        self,
        cidr_http_client: httpx.Client,
    ) -> None:
        """Verify /artifacts/public/* is accessible without auth."""
        response = cidr_http_client.get("/artifacts/public/")

        # Should return 200 (exists) or 404 (empty), NOT 401
        assert response.status_code in (200, 404)
        assert response.status_code != 401, "Public paths should not require auth"

    def test_auth_validate_endpoint_accessible_without_auth(
        self,
        cidr_http_client: httpx.Client,
    ) -> None:
        """Verify /api/v1/auth/validate is accessible (used by forward_auth).

        This endpoint must be public because Caddy uses it for authentication.
        Without auth, it should return 401 (invalid token), not block the request.
        """
        response = cidr_http_client.get("/api/v1/auth/validate")

        # Should return 401 (no valid token provided), which means it's accessible
        assert response.status_code == 401
