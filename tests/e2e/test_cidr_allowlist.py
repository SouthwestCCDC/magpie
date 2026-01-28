"""E2E tests for CIDR allow-list read-only bypass behavior.

Tests the MAGPIE_ALLOWED_CIDRS feature which allows trusted IPs to read
artifacts without authentication, while still requiring auth for write operations.

IMPORTANT: These tests must run from within the Docker network to work correctly.
See tests/e2e/conftest.py for CIDR testing infrastructure details.

To run these tests, use the helper script (recommended):
    ./scripts/run-cidr-tests.sh

Or manually:
    docker compose -f docker-compose.yml -f docker-compose.cidr-test.yml up -d --build
    docker compose exec test-runner-inside pytest tests/e2e/test_cidr_allowlist.py -k "not OutsideIP" -v
    docker compose exec test-runner-outside pytest tests/e2e/test_cidr_allowlist.py::TestCIDRAllowListOutsideIPDenied -v
    docker compose -f docker-compose.yml -f docker-compose.cidr-test.yml down

These tests verify security-critical CIDR bypass functionality:
1. Allowed IPs can read without auth (GET /api/v1/artifacts*, GET /artifacts/*)
2. Allowed IPs cannot write without auth (POST, PATCH, DELETE operations)
3. Public paths remain accessible regardless of CIDR setting
"""

from __future__ import annotations

import httpx
import pytest


@pytest.mark.e2e
@pytest.mark.slow
class TestCIDRAllowListReadAccess:
    """Tests that allowed IPs can read without authentication.

    Verifies the CIDR bypass works for GET operations on:
    - GET /api/v1/artifacts/* (list and metadata)
    - GET /artifacts/* (static file downloads)

    All tests in this class use cidr_http_client which makes requests from
    within the Docker network (IP in 172.18.0.0/24 range), appearing as an
    "allowed IP" to Caddy's CIDR bypass logic.
    """

    def test_allowed_ip_can_list_artifacts_without_auth(
        self,
        cidr_http_client: httpx.Client,
        cidr_authenticated_client: httpx.Client,
        test_artifact_content: bytes,
    ) -> None:
        """Verify allowed IP can GET /api/v1/artifacts/* without token.

        SECURITY CRITICAL: Validates that CIDR bypass allows read access
        but properly injects X-Magpie-User: cidr-bypass and X-Magpie-Scope: read
        headers for backend authorization.
        """
        # Setup: Upload artifact using authenticated client
        upload_response = cidr_authenticated_client.post(
            "/api/v1/upload/cidr-test/list-access",
            files={"file": ("artifact", test_artifact_content, "application/octet-stream")},
        )
        assert upload_response.status_code in (200, 201), f"Setup failed: {upload_response.text}"

        # Test: List artifact paths without authentication (CIDR bypass)
        list_response = cidr_http_client.get("/api/v1/artifacts")
        assert list_response.status_code == 200, (
            f"CIDR bypass failed for GET /api/v1/artifacts. "
            f"Expected 200, got {list_response.status_code}. "
            f"Response: {list_response.text}"
        )

        # Verify the uploaded artifact appears in the list
        data = list_response.json()
        assert "paths" in data
        assert "cidr-test/list-access" in data["paths"]

    def test_allowed_ip_can_get_artifact_metadata_without_auth(
        self,
        cidr_http_client: httpx.Client,
        cidr_authenticated_client: httpx.Client,
        test_artifact_content: bytes,
    ) -> None:
        """Verify allowed IP can GET /api/v1/artifacts/{path} without token."""
        # Setup: Upload artifact
        upload_response = cidr_authenticated_client.post(
            "/api/v1/upload/cidr-test/metadata-access",
            files={"file": ("artifact", test_artifact_content, "application/octet-stream")},
        )
        assert upload_response.status_code in (200, 201)
        artifact_hash = upload_response.json()["hash"]

        # Test: Get artifact metadata without authentication (CIDR bypass)
        metadata_response = cidr_http_client.get("/api/v1/artifacts/cidr-test/metadata-access")
        assert metadata_response.status_code == 200, (
            f"CIDR bypass failed for GET /api/v1/artifacts/{{path}}. "
            f"Expected 200, got {metadata_response.status_code}"
        )

        # Verify metadata includes the uploaded version
        data = metadata_response.json()
        assert "versions" in data
        version_hashes = [v["hash"] for v in data["versions"]]
        assert artifact_hash in version_hashes

    def test_allowed_ip_can_get_artifact_info_without_auth(
        self,
        cidr_http_client: httpx.Client,
        cidr_authenticated_client: httpx.Client,
        test_artifact_content: bytes,
    ) -> None:
        """Verify allowed IP can GET /api/v1/artifacts/{path}/{ref}/info without token."""
        # Setup: Upload artifact
        upload_response = cidr_authenticated_client.post(
            "/api/v1/upload/cidr-test/info-access",
            files={"file": ("artifact", test_artifact_content, "application/octet-stream")},
        )
        assert upload_response.status_code in (200, 201)
        artifact_hash = upload_response.json()["hash"]
        hash_ref = upload_response.json()["hash_ref"]

        # Test: Get artifact info without authentication (CIDR bypass)
        # Use the hash_ref (e.g., @abc12345) for the ref parameter
        info_response = cidr_http_client.get(
            f"/api/v1/artifacts/cidr-test/info-access/{hash_ref}/info"
        )
        assert info_response.status_code == 200, (
            f"CIDR bypass failed for GET /api/v1/artifacts/{{path}}/{{ref}}/info. "
            f"Expected 200, got {info_response.status_code}"
        )

        # Verify info includes expected fields
        data = info_response.json()
        assert data["hash"] == artifact_hash
        # Note: path field is not returned by info endpoint, only in list endpoint

    def test_allowed_ip_can_download_artifact_without_auth(
        self,
        cidr_http_client: httpx.Client,
        cidr_authenticated_client: httpx.Client,
        test_artifact_content: bytes,
    ) -> None:
        """Verify allowed IP can GET /artifacts/* without token.

        SECURITY CRITICAL: Validates CIDR bypass for static file downloads.
        Files are served directly by Caddy after forward_auth check.
        """
        # Setup: Upload artifact
        upload_response = cidr_authenticated_client.post(
            "/api/v1/upload/cidr-test/download-access",
            files={"file": ("artifact", test_artifact_content, "application/octet-stream")},
        )
        assert upload_response.status_code in (200, 201)
        download_url = upload_response.json()["download_url"]

        # Test: Download artifact without authentication (CIDR bypass)
        download_response = cidr_http_client.get(download_url)
        assert download_response.status_code == 200, (
            f"CIDR bypass failed for GET /artifacts/*. "
            f"Expected 200, got {download_response.status_code}. "
            f"URL: {download_url}"
        )

        # Verify content matches uploaded data
        assert download_response.content == test_artifact_content


@pytest.mark.e2e
@pytest.mark.slow
class TestCIDRAllowListWriteBlocked:
    """Tests that allowed IPs CANNOT write without authentication.

    Verifies that even IPs in MAGPIE_ALLOWED_CIDRS must authenticate for:
    - POST /api/v1/upload/* (uploads)
    - PATCH /api/v1/artifacts/* (metadata amendments)
    - POST /api/v1/artifacts/{path}/{ref}/tags (add tags)
    - DELETE /api/v1/artifacts/{path}/tags/{tag_name} (remove tags)
    - POST /api/v1/tags/{tag_name}/flush (tag flushing)
    - Admin operations (tokens, GC, status)

    SECURITY CRITICAL: These tests ensure CIDR bypass is read-only.
    """

    def test_allowed_ip_cannot_upload_without_auth(
        self,
        cidr_http_client: httpx.Client,
        test_artifact_content: bytes,
    ) -> None:
        """Verify allowed IP gets 401 for POST /api/v1/upload/* without token.

        SECURITY CRITICAL: Upload operations must always require authentication,
        even from allowed CIDR ranges.
        """
        response = cidr_http_client.post(
            "/api/v1/upload/cidr-test/unauthorized-upload",
            files={"file": ("artifact", test_artifact_content, "application/octet-stream")},
        )
        assert response.status_code == 401, (
            f"SECURITY FAILURE: Allowed IP uploaded without auth. "
            f"Expected 401, got {response.status_code}. "
            f"CIDR bypass should be read-only."
        )

    def test_allowed_ip_cannot_amend_metadata_without_auth(
        self,
        cidr_http_client: httpx.Client,
        cidr_authenticated_client: httpx.Client,
        test_artifact_content: bytes,
    ) -> None:
        """Verify allowed IP gets 401 for PATCH /api/v1/artifacts/* without token."""
        # Setup: Upload artifact to amend
        upload_response = cidr_authenticated_client.post(
            "/api/v1/upload/cidr-test/amend-blocked",
            files={"file": ("artifact", test_artifact_content, "application/octet-stream")},
        )
        assert upload_response.status_code in (200, 201)
        artifact_hash = upload_response.json()["hash"]

        # Test: Attempt to amend without authentication
        amend_response = cidr_http_client.patch(
            f"/api/v1/artifacts/cidr-test/amend-blocked/@{artifact_hash[:8]}",
            json={"description": "Should fail"},
        )
        assert amend_response.status_code == 401, (
            f"SECURITY FAILURE: Allowed IP amended metadata without auth. "
            f"Expected 401, got {amend_response.status_code}"
        )

    def test_allowed_ip_cannot_add_tags_without_auth(
        self,
        cidr_http_client: httpx.Client,
        cidr_authenticated_client: httpx.Client,
        test_artifact_content: bytes,
    ) -> None:
        """Verify allowed IP gets 401 for POST /api/v1/artifacts/{path}/{ref}/tags without token."""
        # Setup: Upload artifact
        upload_response = cidr_authenticated_client.post(
            "/api/v1/upload/cidr-test/tag-add-blocked",
            files={"file": ("artifact", test_artifact_content, "application/octet-stream")},
        )
        assert upload_response.status_code in (200, 201)
        artifact_hash = upload_response.json()["hash"]

        # Test: Attempt to add tag without authentication
        tag_response = cidr_http_client.post(
            f"/api/v1/artifacts/cidr-test/tag-add-blocked/@{artifact_hash[:8]}/tags",
            json={"tag_name": "unauthorized-tag"},
        )
        assert tag_response.status_code == 401, (
            f"SECURITY FAILURE: Allowed IP added tag without auth. "
            f"Expected 401, got {tag_response.status_code}"
        )

    def test_allowed_ip_cannot_delete_tags_without_auth(
        self,
        cidr_http_client: httpx.Client,
        cidr_authenticated_client: httpx.Client,
        test_artifact_content: bytes,
    ) -> None:
        """Verify allowed IP gets 401 for DELETE /api/v1/artifacts/{path}/tags/{tag} without token."""
        # Setup: Upload artifact and add tag
        upload_response = cidr_authenticated_client.post(
            "/api/v1/upload/cidr-test/tag-delete-blocked",
            files={"file": ("artifact", test_artifact_content, "application/octet-stream")},
        )
        assert upload_response.status_code in (200, 201)
        artifact_hash = upload_response.json()["hash"]

        tag_response = cidr_authenticated_client.post(
            f"/api/v1/artifacts/cidr-test/tag-delete-blocked/@{artifact_hash[:8]}/tags",
            json={"tag_name": "temp-tag"},
        )
        assert tag_response.status_code in (200, 201)

        # Test: Attempt to delete tag without authentication
        delete_response = cidr_http_client.delete(
            "/api/v1/artifacts/cidr-test/tag-delete-blocked/tags/temp-tag"
        )
        assert delete_response.status_code == 401, (
            f"SECURITY FAILURE: Allowed IP deleted tag without auth. "
            f"Expected 401, got {delete_response.status_code}"
        )

    def test_allowed_ip_cannot_flush_tags_without_auth(
        self,
        cidr_http_client: httpx.Client,
    ) -> None:
        """Verify allowed IP gets 401 for POST /api/v1/tags/{tag}/flush without token."""
        response = cidr_http_client.post(
            "/api/v1/tags/some-tag/flush",
            json={"confirm": True},
        )
        assert response.status_code == 401, (
            f"SECURITY FAILURE: Allowed IP flushed tag without auth. "
            f"Expected 401, got {response.status_code}"
        )

    def test_allowed_ip_cannot_list_tokens_without_auth(
        self,
        cidr_http_client: httpx.Client,
    ) -> None:
        """Verify allowed IP gets 401 for GET /api/v1/tokens without token."""
        response = cidr_http_client.get("/api/v1/tokens")
        assert response.status_code == 401, (
            f"SECURITY FAILURE: Allowed IP listed tokens without auth. "
            f"Expected 401, got {response.status_code}"
        )

    def test_allowed_ip_cannot_create_tokens_without_auth(
        self,
        cidr_http_client: httpx.Client,
    ) -> None:
        """Verify allowed IP gets 401 for POST /api/v1/tokens without token."""
        response = cidr_http_client.post(
            "/api/v1/tokens",
            json={"name": "unauthorized-token", "scope": "read"},
        )
        assert response.status_code == 401, (
            f"SECURITY FAILURE: Allowed IP created token without auth. "
            f"Expected 401, got {response.status_code}"
        )

    def test_allowed_ip_cannot_run_gc_without_auth(
        self,
        cidr_http_client: httpx.Client,
    ) -> None:
        """Verify allowed IP gets 401 for POST /api/v1/gc without token."""
        response = cidr_http_client.post("/api/v1/gc")
        assert response.status_code == 401, (
            f"SECURITY FAILURE: Allowed IP triggered GC without auth. "
            f"Expected 401, got {response.status_code}"
        )

    def test_allowed_ip_cannot_get_status_without_auth(
        self,
        cidr_http_client: httpx.Client,
    ) -> None:
        """Verify allowed IP gets 401 for GET /api/v1/status without token."""
        response = cidr_http_client.get("/api/v1/status")
        assert response.status_code == 401, (
            f"SECURITY FAILURE: Allowed IP accessed status without auth. "
            f"Expected 401, got {response.status_code}"
        )


@pytest.mark.e2e
@pytest.mark.slow
class TestCIDRAllowListPublicPaths:
    """Tests that public paths remain accessible regardless of CIDR setting.

    Verifies that:
    - GET /health (health check)
    - GET /api/v1/auth/validate (auth validation endpoint)
    - GET /artifacts/public/* (public static files)

    These paths should be accessible without authentication, regardless of
    whether the client IP is in MAGPIE_ALLOWED_CIDRS.
    """

    def test_health_endpoint_accessible_without_auth(
        self,
        cidr_http_client: httpx.Client,
    ) -> None:
        """Verify GET /health works without auth (CIDR setting irrelevant)."""
        response = cidr_http_client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_auth_validate_endpoint_accessible_without_auth(
        self,
        cidr_http_client: httpx.Client,
    ) -> None:
        """Verify GET /api/v1/auth/validate returns 401 without token (public endpoint).

        This endpoint must be public (no forward_auth) because it IS the
        forward_auth endpoint. It should return 401 when no token is provided.
        """
        response = cidr_http_client.get("/api/v1/auth/validate")
        # Endpoint is accessible (not 404), but returns 401 for missing token
        assert response.status_code == 401

    def test_public_artifacts_accessible_without_auth(
        self,
        cidr_http_client: httpx.Client,
        cidr_authenticated_client: httpx.Client,
    ) -> None:
        """Verify GET /artifacts/public/* works without auth (CIDR setting irrelevant)."""
        # Setup: Create a public artifact via direct upload to the public directory
        # Note: This requires uploading to the "public" path
        test_content = b"Public artifact content"
        upload_response = cidr_authenticated_client.post(
            "/api/v1/upload/public/test-file",
            files={"file": ("artifact", test_content, "application/octet-stream")},
        )
        assert upload_response.status_code in (200, 201)
        download_url = upload_response.json()["download_url"]

        # Test: Download public artifact without authentication
        download_response = cidr_http_client.get(download_url)
        assert download_response.status_code == 200
        assert download_response.content == test_content


@pytest.mark.e2e
@pytest.mark.slow
class TestCIDRAllowListTokenInteraction:
    """Tests verifying token authentication works correctly with CIDR settings.

    These tests ensure that:
    1. Token authentication still functions from inside the CIDR allow-list
    2. Invalid tokens are rejected even from trusted IPs
    3. Token authentication works from outside the CIDR allow-list (overrides denial)
    4. Token scope enforcement works regardless of CIDR setting

    SECURITY CRITICAL: Validates that CIDR bypass and token auth work together correctly.
    """

    def test_inside_cidr_with_valid_read_token(
        self,
        cidr_http_client: httpx.Client,
        cidr_authenticated_client: httpx.Client,
        cidr_admin_token: str,
        test_artifact_content: bytes,
    ) -> None:
        """Verify inside CIDR + valid read token works for read operations.

        Token authentication should still function from inside the CIDR range.
        """
        from tests.e2e.conftest import create_token_via_api

        # Create a read-only token
        read_token = create_token_via_api(
            cidr_authenticated_client,
            cidr_admin_token,
            "cidr-read-token-test",
            "read",
        )

        # Setup: Upload artifact
        upload_response = cidr_authenticated_client.post(
            "/api/v1/upload/cidr-test/token-read-test",
            files={"file": ("artifact", test_artifact_content, "application/octet-stream")},
        )
        assert upload_response.status_code in (200, 201)

        # Test: List artifacts with read token (should work)
        list_response = cidr_http_client.get(
            "/api/v1/artifacts",
            headers={"Authorization": f"Bearer {read_token}"},
        )
        assert list_response.status_code == 200, (
            f"Read token should work from inside CIDR. "
            f"Expected 200, got {list_response.status_code}"
        )

        data = list_response.json()
        assert "cidr-test/token-read-test" in data["paths"]

    def test_inside_cidr_with_valid_write_token(
        self,
        cidr_http_client: httpx.Client,
        cidr_authenticated_client: httpx.Client,
        cidr_admin_token: str,
        test_artifact_content: bytes,
    ) -> None:
        """Verify inside CIDR + valid write token works for write operations.

        Token authentication should still function from inside the CIDR range.
        """
        from tests.e2e.conftest import create_token_via_api

        # Create a write token
        write_token = create_token_via_api(
            cidr_authenticated_client,
            cidr_admin_token,
            "cidr-write-token-test",
            "write",
        )

        # Test: Upload with write token (should work)
        upload_response = cidr_http_client.post(
            "/api/v1/upload/cidr-test/token-write-test",
            files={"file": ("artifact", test_artifact_content, "application/octet-stream")},
            headers={"Authorization": f"Bearer {write_token}"},
        )
        assert upload_response.status_code in (200, 201), (
            f"Write token should work from inside CIDR. "
            f"Expected 200/201, got {upload_response.status_code}"
        )

    def test_inside_cidr_with_invalid_token(
        self,
        cidr_http_client: httpx.Client,
        cidr_authenticated_client: httpx.Client,
        test_artifact_content: bytes,
    ) -> None:
        """Verify inside CIDR + invalid token falls back to CIDR bypass.

        When an invalid token is provided from inside the CIDR, the CIDR bypass
        takes precedence and allows read access. This is because Caddy's CIDR
        check happens before token validation, and the CIDR bypass essentially
        says "trust this IP for read operations regardless of auth state".
        """
        # Setup: Upload artifact to verify read access
        upload_response = cidr_authenticated_client.post(
            "/api/v1/upload/cidr-test/invalid-token-test",
            files={"file": ("artifact", test_artifact_content, "application/octet-stream")},
        )
        assert upload_response.status_code in (200, 201)

        # Test: Use invalid token (CIDR bypass allows read access)
        response = cidr_http_client.get(
            "/api/v1/artifacts",
            headers={"Authorization": "Bearer mgp_invalid_token_12345"},
        )
        assert response.status_code == 200, (
            f"CIDR bypass should allow read access even with invalid token. "
            f"Expected 200, got {response.status_code}"
        )

        # Verify the artifact is in the list (confirming read worked)
        data = response.json()
        assert "cidr-test/invalid-token-test" in data["paths"]

    def test_outside_cidr_with_valid_read_token(
        self,
        cidr_outside_http_client: httpx.Client,
        cidr_authenticated_client: httpx.Client,
        cidr_admin_token: str,
        test_artifact_content: bytes,
    ) -> None:
        """Verify outside CIDR + valid read token works.

        Token auth should override CIDR denial for valid tokens.
        """
        from tests.e2e.conftest import create_token_via_api

        # Create a read-only token
        read_token = create_token_via_api(
            cidr_authenticated_client,
            cidr_admin_token,
            "outside-cidr-read-token",
            "read",
        )

        # Setup: Upload artifact
        upload_response = cidr_authenticated_client.post(
            "/api/v1/upload/cidr-test/outside-read-test",
            files={"file": ("artifact", test_artifact_content, "application/octet-stream")},
        )
        assert upload_response.status_code in (200, 201)

        # Test: List artifacts from outside CIDR with valid token (should work)
        list_response = cidr_outside_http_client.get(
            "/api/v1/artifacts",
            headers={"Authorization": f"Bearer {read_token}"},
        )
        assert list_response.status_code == 200, (
            f"Valid read token should work from outside CIDR. "
            f"Expected 200, got {list_response.status_code}"
        )

        data = list_response.json()
        assert "cidr-test/outside-read-test" in data["paths"]

    def test_outside_cidr_with_valid_write_token(
        self,
        cidr_outside_http_client: httpx.Client,
        cidr_authenticated_client: httpx.Client,
        cidr_admin_token: str,
        test_artifact_content: bytes,
    ) -> None:
        """Verify outside CIDR + valid write token works.

        Token auth should override CIDR denial for valid tokens.
        """
        from tests.e2e.conftest import create_token_via_api

        # Create a write token
        write_token = create_token_via_api(
            cidr_authenticated_client,
            cidr_admin_token,
            "outside-cidr-write-token",
            "write",
        )

        # Test: Upload from outside CIDR with valid token (should work)
        upload_response = cidr_outside_http_client.post(
            "/api/v1/upload/cidr-test/outside-write-test",
            files={"file": ("artifact", test_artifact_content, "application/octet-stream")},
            headers={"Authorization": f"Bearer {write_token}"},
        )
        assert upload_response.status_code in (200, 201), (
            f"Valid write token should work from outside CIDR. "
            f"Expected 200/201, got {upload_response.status_code}"
        )

    def test_inside_cidr_read_token_cannot_write(
        self,
        cidr_http_client: httpx.Client,
        cidr_authenticated_client: httpx.Client,
        cidr_admin_token: str,
        test_artifact_content: bytes,
    ) -> None:
        """Verify inside CIDR + read token attempting write is denied.

        SECURITY CRITICAL: Token scope enforcement must work regardless of CIDR.
        """
        from tests.e2e.conftest import create_token_via_api

        # Create a read-only token
        read_token = create_token_via_api(
            cidr_authenticated_client,
            cidr_admin_token,
            "cidr-read-scope-test",
            "read",
        )

        # Test: Attempt upload with read token (should fail)
        upload_response = cidr_http_client.post(
            "/api/v1/upload/cidr-test/scope-violation-test",
            files={"file": ("artifact", test_artifact_content, "application/octet-stream")},
            headers={"Authorization": f"Bearer {read_token}"},
        )
        assert upload_response.status_code == 403, (
            f"SECURITY FAILURE: Read token allowed write from inside CIDR. "
            f"Expected 403, got {upload_response.status_code}"
        )


@pytest.mark.e2e
@pytest.mark.slow
class TestCIDRAllowListOutsideIPDenied:
    """Tests verifying IPs OUTSIDE the CIDR allow-list get 401 on read attempts.

    These tests run from test-runner-outside container (192.168.100.x subnet),
    which is NOT in MAGPIE_ALLOWED_CIDRS (172.18.0.0/24).

    This class tests the negative case: IPs outside the allow-list should be
    denied access to read endpoints just like any other unauthenticated request.

    SECURITY CRITICAL: Ensures the CIDR allow-list doesn't accidentally grant
    access to unintended IPs.
    """

    def test_outside_ip_cannot_list_artifacts_without_auth(
        self,
        cidr_outside_http_client: httpx.Client,
    ) -> None:
        """Verify GET /api/v1/artifacts returns 401 from outside CIDR.

        SECURITY CRITICAL: IPs outside the allow-list should not have read access.
        """
        response = cidr_outside_http_client.get("/api/v1/artifacts")
        assert response.status_code == 401, (
            f"SECURITY FAILURE: IP outside CIDR range accessed artifact list. "
            f"Expected 401, got {response.status_code}. "
            f"CIDR bypass should only work for IPs in 172.18.0.0/24."
        )

    def test_outside_ip_cannot_get_artifact_metadata_without_auth(
        self,
        cidr_outside_http_client: httpx.Client,
    ) -> None:
        """Verify GET /api/v1/artifacts/{path} returns 401 from outside CIDR.

        Note: We don't need to create test artifacts for these negative tests
        because the 401 should happen before the backend checks if the artifact exists.
        """
        response = cidr_outside_http_client.get("/api/v1/artifacts/test/artifact")
        assert response.status_code == 401, (
            f"SECURITY FAILURE: IP outside CIDR range accessed artifact metadata. "
            f"Expected 401, got {response.status_code}"
        )

    def test_outside_ip_cannot_get_artifact_info_without_auth(
        self,
        cidr_outside_http_client: httpx.Client,
    ) -> None:
        """Verify GET /api/v1/artifacts/{path}/{ref}/info returns 401 from outside CIDR."""
        response = cidr_outside_http_client.get("/api/v1/artifacts/test/artifact/@12345678/info")
        assert response.status_code == 401, (
            f"SECURITY FAILURE: IP outside CIDR range accessed artifact info. "
            f"Expected 401, got {response.status_code}"
        )

    def test_outside_ip_cannot_download_artifact_without_auth(
        self,
        cidr_outside_http_client: httpx.Client,
    ) -> None:
        """Verify GET /artifacts/* returns 401 from outside CIDR.

        SECURITY CRITICAL: Static file downloads should be blocked for outside IPs.
        """
        # Try to access a hypothetical artifact download URL
        # The 401 should happen at forward_auth before Caddy tries to serve the file
        response = cidr_outside_http_client.get("/artifacts/test/artifact/@12345678")
        assert response.status_code == 401, (
            f"SECURITY FAILURE: IP outside CIDR range downloaded artifact. "
            f"Expected 401, got {response.status_code}"
        )

    def test_outside_ip_health_endpoint_still_works(
        self,
        cidr_outside_http_client: httpx.Client,
    ) -> None:
        """Verify GET /health works from outside CIDR (public endpoint).

        This test ensures that public endpoints remain accessible regardless
        of CIDR configuration, even from outside the allow-list.
        """
        response = cidr_outside_http_client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
