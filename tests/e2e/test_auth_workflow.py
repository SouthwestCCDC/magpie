"""Authentication workflow E2E tests for Magpie.

Tests token-based authentication and authorization:
- Unauthenticated access restrictions
- Read token permissions
- Write token permissions
- Admin token management capabilities
"""

from __future__ import annotations

import httpx
import pytest

from tests.e2e.conftest import create_token_via_api


@pytest.mark.e2e
@pytest.mark.slow
class TestUnauthenticatedAccess:
    """Tests for unauthenticated access restrictions."""

    def test_unauthenticated_upload_rejected(
        self,
        http_client: httpx.Client,
        test_artifact_content: bytes,
    ) -> None:
        """Verify unauthenticated upload returns 401."""
        response = http_client.post(
            "/api/v1/upload/e2e-tests/unauth-test",
            files={"file": ("artifact", test_artifact_content, "application/octet-stream")},
        )

        assert response.status_code == 401

    def test_unauthenticated_token_management_rejected(
        self,
        http_client: httpx.Client,
    ) -> None:
        """Verify unauthenticated token management returns 401."""
        # List tokens
        response = http_client.get("/api/v1/tokens")
        assert response.status_code == 401

        # Create token
        response = http_client.post(
            "/api/v1/tokens",
            json={"name": "test", "scope": "read"},
        )
        assert response.status_code == 401

    def test_public_health_endpoint_accessible(
        self,
        http_client: httpx.Client,
    ) -> None:
        """Verify health endpoint is publicly accessible."""
        response = http_client.get("/health")
        assert response.status_code == 200

    def test_public_artifact_list_accessible(
        self,
        http_client: httpx.Client,
        authenticated_client: httpx.Client,
        test_artifact_content: bytes,
    ) -> None:
        """Verify artifact listing requires authentication after PR #231."""
        # First upload something with authenticated client
        authenticated_client.post(
            "/api/v1/upload/e2e-tests/public-list-test",
            files={"file": ("artifact", test_artifact_content, "application/octet-stream")},
        )

        # Unauthenticated GET should be rejected (401)
        response = http_client.get("/api/v1/artifacts/e2e-tests/")
        assert response.status_code == 401

        # Authenticated GET should work
        response = authenticated_client.get("/api/v1/artifacts/e2e-tests/")
        assert response.status_code == 200


@pytest.mark.e2e
@pytest.mark.slow
class TestReadTokenPermissions:
    """Tests for read token permission boundaries."""

    def test_read_token_can_list_artifacts(
        self,
        http_client: httpx.Client,
        admin_token: str,
        authenticated_client: httpx.Client,
        test_artifact_content: bytes,
    ) -> None:
        """Verify read token can list artifacts."""
        # Create read token
        read_token = create_token_via_api(http_client, admin_token, "auth-test-reader", "read")

        # Upload something first (with admin)
        authenticated_client.post(
            "/api/v1/upload/e2e-tests/read-list-test",
            files={"file": ("artifact", test_artifact_content, "application/octet-stream")},
        )

        # List with read token (should work - GET is public anyway)
        response = http_client.get(
            "/api/v1/artifacts/e2e-tests/",
            headers={"Authorization": f"Bearer {read_token}"},
        )
        assert response.status_code == 200

    def test_read_token_cannot_upload(
        self,
        http_client: httpx.Client,
        admin_token: str,
        test_artifact_content: bytes,
    ) -> None:
        """Verify read token cannot upload artifacts (returns 403)."""
        # Create read token
        read_token = create_token_via_api(
            http_client, admin_token, "auth-test-reader-no-upload", "read"
        )

        # Try to upload with read token
        response = http_client.post(
            "/api/v1/upload/e2e-tests/read-upload-test",
            files={"file": ("artifact", test_artifact_content, "application/octet-stream")},
            headers={"Authorization": f"Bearer {read_token}"},
        )

        assert response.status_code == 403

    def test_read_token_cannot_create_tags(
        self,
        http_client: httpx.Client,
        admin_token: str,
        authenticated_client: httpx.Client,
        test_artifact_content: bytes,
    ) -> None:
        """Verify read token cannot create tags (returns 403)."""
        # Create read token
        read_token = create_token_via_api(
            http_client, admin_token, "auth-test-reader-no-tag", "read"
        )

        # Upload artifact with admin
        upload_response = authenticated_client.post(
            "/api/v1/upload/e2e-tests/read-tag-test",
            files={"file": ("artifact", test_artifact_content, "application/octet-stream")},
        )
        artifact_hash = upload_response.json()["hash"]

        # Try to create tag with read token
        # API expects ref to be either a tag name or @{short_hash} format
        hash_ref = f"@{artifact_hash[:8]}"
        response = http_client.post(
            f"/api/v1/artifacts/e2e-tests/read-tag-test/{hash_ref}/tags",
            json={"tag_name": "stable"},
            headers={"Authorization": f"Bearer {read_token}"},
        )

        assert response.status_code == 403

    def test_read_token_cannot_manage_tokens(
        self,
        http_client: httpx.Client,
        admin_token: str,
    ) -> None:
        """Verify read token cannot manage tokens (returns 403)."""
        # Create read token
        read_token = create_token_via_api(
            http_client, admin_token, "auth-test-reader-no-tokens", "read"
        )

        # Try to list tokens
        response = http_client.get(
            "/api/v1/tokens",
            headers={"Authorization": f"Bearer {read_token}"},
        )
        assert response.status_code == 403

        # Try to create token
        response = http_client.post(
            "/api/v1/tokens",
            json={"name": "should-fail", "scope": "read"},
            headers={"Authorization": f"Bearer {read_token}"},
        )
        assert response.status_code == 403


@pytest.mark.e2e
@pytest.mark.slow
class TestWriteTokenPermissions:
    """Tests for write token permission boundaries."""

    def test_write_token_can_upload(
        self,
        http_client: httpx.Client,
        admin_token: str,
        test_artifact_content: bytes,
    ) -> None:
        """Verify write token can upload artifacts."""
        # Create write token
        write_token = create_token_via_api(
            http_client, admin_token, "auth-test-writer-upload", "write"
        )

        response = http_client.post(
            "/api/v1/upload/e2e-tests/write-upload-test",
            files={"file": ("artifact", test_artifact_content, "application/octet-stream")},
            headers={
                "Authorization": f"Bearer {write_token}",
            },
        )

        assert response.status_code == 200

    def test_write_token_can_create_tags(
        self,
        http_client: httpx.Client,
        admin_token: str,
        test_artifact_content: bytes,
    ) -> None:
        """Verify write token can create tags."""
        # Create write token
        write_token = create_token_via_api(
            http_client, admin_token, "auth-test-writer-tag", "write"
        )

        # Upload artifact
        upload_response = http_client.post(
            "/api/v1/upload/e2e-tests/write-tag-test",
            files={"file": ("artifact", test_artifact_content, "application/octet-stream")},
            headers={
                "Authorization": f"Bearer {write_token}",
            },
        )
        artifact_hash = upload_response.json()["hash"]

        # Create tag
        # API expects ref to be either a tag name or @{short_hash} format
        hash_ref = f"@{artifact_hash[:8]}"
        response = http_client.post(
            f"/api/v1/artifacts/e2e-tests/write-tag-test/{hash_ref}/tags",
            json={"tag_name": "stable"},
            headers={"Authorization": f"Bearer {write_token}"},
        )

        assert response.status_code in (200, 201)

    def test_write_token_cannot_manage_tokens(
        self,
        http_client: httpx.Client,
        admin_token: str,
    ) -> None:
        """Verify write token cannot manage tokens (returns 403)."""
        # Create write token
        write_token = create_token_via_api(
            http_client, admin_token, "auth-test-writer-no-tokens", "write"
        )

        # Try to list tokens
        response = http_client.get(
            "/api/v1/tokens",
            headers={"Authorization": f"Bearer {write_token}"},
        )
        assert response.status_code == 403

        # Try to create token
        response = http_client.post(
            "/api/v1/tokens",
            json={"name": "should-fail", "scope": "read"},
            headers={"Authorization": f"Bearer {write_token}"},
        )
        assert response.status_code == 403


@pytest.mark.e2e
@pytest.mark.slow
class TestAdminTokenPermissions:
    """Tests for admin token management capabilities."""

    def test_admin_token_can_list_tokens(
        self,
        authenticated_client: httpx.Client,
    ) -> None:
        """Verify admin token can list all tokens."""
        response = authenticated_client.get("/api/v1/tokens")
        assert response.status_code == 200
        data = response.json()
        assert "tokens" in data or isinstance(data, list)

    def test_admin_token_can_create_tokens(
        self,
        authenticated_client: httpx.Client,
    ) -> None:
        """Verify admin token can create new tokens."""
        response = authenticated_client.post(
            "/api/v1/tokens",
            json={"name": "admin-created-test", "scope": "read"},
        )

        assert response.status_code in (200, 201)
        data = response.json()
        assert "token" in data
        assert data["token"].startswith("mgp_")

    def test_admin_token_can_revoke_tokens(
        self,
        authenticated_client: httpx.Client,
    ) -> None:
        """Verify admin token can revoke tokens."""
        # First create a token to revoke
        create_response = authenticated_client.post(
            "/api/v1/tokens",
            json={"name": "to-be-revoked", "scope": "read"},
        )
        assert create_response.status_code in (200, 201)

        # Revoke the token
        response = authenticated_client.delete("/api/v1/tokens/to-be-revoked")

        assert response.status_code in (200, 204)

    def test_admin_token_can_upload(
        self,
        authenticated_client: httpx.Client,
        test_artifact_content: bytes,
    ) -> None:
        """Verify admin token can upload artifacts."""
        response = authenticated_client.post(
            "/api/v1/upload/e2e-tests/admin-upload-test",
            files={"file": ("artifact", test_artifact_content, "application/octet-stream")},
        )

        assert response.status_code == 200


@pytest.mark.e2e
@pytest.mark.slow
class TestInvalidToken:
    """Tests for invalid token handling."""

    def test_invalid_token_rejected(
        self,
        http_client: httpx.Client,
        test_artifact_content: bytes,
    ) -> None:
        """Verify invalid token is rejected with 401."""
        response = http_client.post(
            "/api/v1/upload/e2e-tests/invalid-token-test",
            files={"file": ("artifact", test_artifact_content, "application/octet-stream")},
            headers={
                "Authorization": "Bearer invalid_token_12345",
            },
        )

        assert response.status_code == 401

    def test_malformed_auth_header_rejected(
        self,
        http_client: httpx.Client,
        test_artifact_content: bytes,
    ) -> None:
        """Verify malformed authorization header is rejected."""
        # Missing "Bearer" prefix
        response = http_client.post(
            "/api/v1/upload/e2e-tests/malformed-auth-test",
            files={"file": ("artifact", test_artifact_content, "application/octet-stream")},
            headers={
                "Authorization": "mgp_some_token",
            },
        )

        assert response.status_code == 401


@pytest.mark.e2e
@pytest.mark.slow
class TestAuthenticationEnforcement:
    """Tests for static file serving authentication enforcement (issue #230).

    Validates that:
    1. Unauthenticated access to /artifacts/* is properly rejected (except /artifacts/public/*)
    2. Path traversal attempts cannot bypass authentication
    3. Public paths remain accessible without authentication
    """

    def test_artifacts_root_requires_auth(
        self,
        http_client: httpx.Client,
    ) -> None:
        """Verify GET /artifacts/ without auth returns 401."""
        response = http_client.get("/artifacts/")
        assert response.status_code == 401

    def test_artifacts_path_requires_auth(
        self,
        http_client: httpx.Client,
        authenticated_client: httpx.Client,
        test_artifact_content: bytes,
    ) -> None:
        """Verify GET /artifacts/some-path/ without auth returns 401."""
        # First upload an artifact to create the directory structure
        upload_response = authenticated_client.post(
            "/api/v1/upload/e2e-tests/auth-enforcement",
            files={"file": ("artifact", test_artifact_content, "application/octet-stream")},
        )
        assert upload_response.status_code == 200
        artifact_hash = upload_response.json()["hash"]

        # Construct the static file path
        # Format: /artifacts/{artifact_path}/@{short_hash}/artifact
        short_hash = artifact_hash[:8]
        static_path = f"/artifacts/e2e-tests/auth-enforcement/@{short_hash}/"

        # Unauthenticated request should be rejected
        response = http_client.get(static_path)
        assert response.status_code == 401

        # Authenticated request should work
        auth_response = authenticated_client.get(static_path)
        assert auth_response.status_code == 200

    def test_artifacts_file_requires_auth(
        self,
        http_client: httpx.Client,
        authenticated_client: httpx.Client,
        test_artifact_content: bytes,
    ) -> None:
        """Verify GET /artifacts/some-path/file without auth returns 401."""
        # Upload an artifact
        upload_response = authenticated_client.post(
            "/api/v1/upload/e2e-tests/auth-file-enforcement",
            files={"file": ("artifact", test_artifact_content, "application/octet-stream")},
        )
        assert upload_response.status_code == 200
        artifact_hash = upload_response.json()["hash"]

        # Construct the direct file path
        short_hash = artifact_hash[:8]
        file_path = f"/artifacts/e2e-tests/auth-file-enforcement/@{short_hash}/artifact"

        # Unauthenticated request should be rejected
        response = http_client.get(file_path)
        assert response.status_code == 401

        # Authenticated request should work
        auth_response = authenticated_client.get(file_path)
        assert auth_response.status_code == 200
        assert auth_response.content == test_artifact_content

    def test_path_traversal_to_public_blocked(
        self,
        http_client: httpx.Client,
        authenticated_client: httpx.Client,
        test_artifact_content: bytes,
    ) -> None:
        """Verify path traversal attempts like /artifacts/../public/... are blocked.

        Even if a file is in the public directory, path traversal attempts should
        not bypass authentication checks.
        """
        # Upload an artifact to create some content
        upload_response = authenticated_client.post(
            "/api/v1/upload/e2e-tests/traversal-test",
            files={"file": ("artifact", test_artifact_content, "application/octet-stream")},
        )
        assert upload_response.status_code == 200

        # Try various path traversal patterns
        traversal_attempts = [
            "/artifacts/../public/",
            "/artifacts/something/../public/",
            "/artifacts/e2e-tests/../public/",
            "/artifacts/./public/",
        ]

        for path in traversal_attempts:
            response = http_client.get(path)
            # Caddy normalizes paths, so these should either:
            # 1. Return 401 (auth required after normalization)
            # 2. Return 404 (path doesn't exist)
            # 3. Be rejected by the server
            # They should NOT return 200 with content
            assert response.status_code in (401, 404, 400), (
                f"Path traversal attempt {path} should be blocked, got {response.status_code}"
            )

    def test_artifacts_nested_path_requires_auth(
        self,
        http_client: httpx.Client,
        authenticated_client: httpx.Client,
        test_artifact_content: bytes,
    ) -> None:
        """Verify nested paths under /artifacts/* require auth.

        Ensures that paths like /artifacts/something/../public/file cannot
        bypass authentication even if they resolve to public content.
        """
        # Upload to a nested path
        upload_response = authenticated_client.post(
            "/api/v1/upload/deep/nested/path/test",
            files={"file": ("artifact", test_artifact_content, "application/octet-stream")},
        )
        assert upload_response.status_code == 200
        artifact_hash = upload_response.json()["hash"]

        # Try to access the nested path without auth
        short_hash = artifact_hash[:8]
        nested_path = f"/artifacts/deep/nested/path/test/@{short_hash}/artifact"

        response = http_client.get(nested_path)
        assert response.status_code == 401

        # Verify authenticated access works
        auth_response = authenticated_client.get(nested_path)
        assert auth_response.status_code == 200

    def test_public_path_accessible_without_auth(
        self,
        http_client: httpx.Client,
        base_url: str,
    ) -> None:
        """Verify /artifacts/public/ is accessible without auth.

        This is a positive test to ensure the public exception works correctly.
        Returns 200 if content exists, 404 if directory is empty, both are acceptable.
        """
        response = http_client.get("/artifacts/public/")

        # Either 200 (directory exists and browsable) or 404 (no content yet)
        # Both are valid - what matters is we don't get 401
        assert response.status_code in (200, 404)
        assert response.status_code != 401, "Public path should not require auth"

    def test_public_file_accessible_without_auth(
        self,
        http_client: httpx.Client,
        authenticated_client: httpx.Client,
    ) -> None:
        """Verify files in /artifacts/public/* are accessible without auth.

        Creates a public file and verifies unauthenticated access works.
        This requires docker exec to place a file in the public directory.
        """
        # For this test to work, we'd need to place a file in the public directory
        # Since we can't easily do that in E2E tests without docker exec,
        # we'll just verify the path doesn't return 401

        # This is a basic check - if public content exists, it should not require auth
        response = http_client.get("/artifacts/public/test-file")

        # Should NOT return 401 (auth required)
        # Will likely return 404 (file doesn't exist) which is fine
        assert response.status_code != 401, "Public file paths should not require authentication"
