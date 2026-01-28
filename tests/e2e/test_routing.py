"""E2E tests for basic Caddy routing and redirects.

Tests fundamental Caddy routing behavior:
1. Root path (/) redirects to /artifacts/
2. HTTP status codes are correct
3. Redirect headers are properly set

These tests verify issues like #350 where the root redirect was broken.
"""

from __future__ import annotations

import httpx
import pytest


@pytest.mark.e2e
@pytest.mark.slow
class TestRootRedirect:
    """Tests for root path redirect behavior.

    Verifies that GET / correctly redirects to /artifacts/ as configured
    in the Caddyfile (issue #350).
    """

    def test_root_redirects_to_artifacts(
        self,
        http_client: httpx.Client,
    ) -> None:
        """Verify GET / returns redirect to /artifacts/.

        The Caddyfile configures: redir * /artifacts/ permanent
        This should return a 301 (permanent) or 308 redirect.
        """
        # Use follow_redirects=False to check the redirect itself
        response = http_client.get("/", follow_redirects=False)

        # Should be a redirect (301 or 308 for permanent)
        assert response.status_code in (301, 308), (
            f"Root path should redirect to /artifacts/. "
            f"Expected 301 or 308, got {response.status_code}"
        )

        # Verify Location header points to /artifacts/
        assert "location" in response.headers, "Redirect must include Location header"
        location = response.headers["location"]
        assert location.endswith("/artifacts/"), (
            f"Redirect should point to /artifacts/, got {location}"
        )

    def test_root_redirect_is_permanent(
        self,
        http_client: httpx.Client,
    ) -> None:
        """Verify root redirect uses permanent status code (301 or 308).

        Permanent redirects are cacheable and indicate the resource has moved
        permanently, which is appropriate for the root -> artifacts redirect.
        """
        response = http_client.get("/", follow_redirects=False)

        # 301 = Moved Permanently (HTTP/1.0 compatible)
        # 308 = Permanent Redirect (HTTP/1.1, preserves method)
        # Both are valid for permanent redirects
        assert response.status_code in (301, 308), (
            f"Root redirect should use permanent status code. Got {response.status_code}"
        )

    def test_root_redirect_follows_to_artifacts(
        self,
        http_client: httpx.Client,
        authenticated_client: httpx.Client,
    ) -> None:
        """Verify following root redirect leads to /artifacts/ (with auth).

        The redirect should successfully lead to the artifacts browser,
        though /artifacts/ itself requires authentication.
        """
        # Following redirects without auth will hit the auth requirement
        response = http_client.get("/", follow_redirects=True)
        # Should get 401 at /artifacts/ (requires auth)
        assert response.status_code == 401

        # With auth, should reach /artifacts/ successfully and serve the browser HTML
        auth_response = authenticated_client.get("/", follow_redirects=True)
        # In the E2E docker setup, /artifacts/ must exist and return 200
        assert auth_response.status_code == 200, (
            f"With auth, following redirect should reach /artifacts/ (200). Got {auth_response.status_code}"
        )


@pytest.mark.e2e
@pytest.mark.slow
class TestRouteNotFound:
    """Tests for 404 handling on non-existent routes."""

    def test_nonexistent_api_route_returns_404(
        self,
        http_client: httpx.Client,
    ) -> None:
        """Verify non-existent API routes return 404, not 401.

        Routes that don't match any handler should return 404 from the
        Caddyfile fallback handler, not require authentication.
        """
        response = http_client.get("/api/v1/nonexistent")

        # Should be 404 (not found), not 401 (auth required)
        assert response.status_code == 404

    def test_nonexistent_root_route_returns_404(
        self,
        http_client: httpx.Client,
    ) -> None:
        """Verify non-existent root-level routes return 404."""
        response = http_client.get("/nonexistent-path")

        # Should be 404 (not found)
        assert response.status_code == 404


@pytest.mark.e2e
@pytest.mark.slow
class TestPublicRoutes:
    """Tests for public routes that don't require authentication."""

    def test_health_endpoint_is_public(
        self,
        http_client: httpx.Client,
    ) -> None:
        """Verify /health endpoint is accessible without authentication."""
        response = http_client.get("/health")

        assert response.status_code == 200
        # Verify it returns valid JSON health status
        data = response.json()
        assert "status" in data

    def test_public_artifacts_path_is_public(
        self,
        http_client: httpx.Client,
    ) -> None:
        """Verify /artifacts/public/* is accessible without authentication.

        This path is configured as public in the Caddyfile and should
        never require authentication.
        """
        response = http_client.get("/artifacts/public/")

        # Should return 200 (exists) or 404 (empty), NOT 401 (auth required)
        assert response.status_code in (200, 404)
        assert response.status_code != 401, "Public path should not require auth"


@pytest.mark.e2e
@pytest.mark.slow
class TestProtectedRoutes:
    """Tests for routes that require authentication."""

    def test_artifacts_root_requires_auth(
        self,
        http_client: httpx.Client,
    ) -> None:
        """Verify /artifacts/ requires authentication (when CIDR not set)."""
        response = http_client.get("/artifacts/")

        assert response.status_code == 401, (
            "/artifacts/ should require authentication when CIDR allow-list is not set"
        )

    def test_api_artifacts_requires_auth(
        self,
        http_client: httpx.Client,
    ) -> None:
        """Verify /api/v1/artifacts/* requires authentication (when CIDR not set)."""
        response = http_client.get("/api/v1/artifacts/")

        assert response.status_code == 401, (
            "/api/v1/artifacts/ should require authentication when CIDR allow-list is not set"
        )

    def test_upload_requires_auth(
        self,
        http_client: httpx.Client,
        test_artifact_content: bytes,
    ) -> None:
        """Verify /api/v1/upload/* requires authentication."""
        response = http_client.post(
            "/api/v1/upload/test/artifact",
            files={"file": ("artifact", test_artifact_content, "application/octet-stream")},
        )

        assert response.status_code == 401, "Upload endpoint should require authentication"

    def test_token_management_requires_auth(
        self,
        http_client: httpx.Client,
    ) -> None:
        """Verify /api/v1/tokens* requires authentication."""
        # List tokens
        response = http_client.get("/api/v1/tokens")
        assert response.status_code == 401, "Token listing should require authentication"

        # Create token
        response = http_client.post(
            "/api/v1/tokens",
            json={"name": "test", "scope": "read"},
        )
        assert response.status_code == 401, "Token creation should require authentication"

    def test_gc_requires_auth(
        self,
        http_client: httpx.Client,
    ) -> None:
        """Verify /api/v1/gc requires authentication."""
        response = http_client.post("/api/v1/gc")

        assert response.status_code == 401, "GC endpoint should require authentication"

    def test_status_requires_auth(
        self,
        http_client: httpx.Client,
    ) -> None:
        """Verify /api/v1/status requires authentication."""
        response = http_client.get("/api/v1/status")

        assert response.status_code == 401, "Status endpoint should require authentication"


@pytest.mark.e2e
@pytest.mark.slow
class TestCaddyForwardAuth:
    """Tests for Caddy forward_auth behavior.

    Verifies that the forward_auth integration with /api/v1/auth/validate
    works correctly for protected routes.
    """

    def test_invalid_token_rejected(
        self,
        http_client: httpx.Client,
    ) -> None:
        """Verify invalid Bearer token is rejected by forward_auth."""
        response = http_client.get(
            "/api/v1/artifacts/",
            headers={"Authorization": "Bearer invalid_token_xyz"},
        )

        assert response.status_code == 401, "Invalid token should be rejected"

    def test_malformed_auth_header_rejected(
        self,
        http_client: httpx.Client,
    ) -> None:
        """Verify malformed Authorization header is rejected."""
        # Missing "Bearer" prefix
        response = http_client.get(
            "/api/v1/artifacts/",
            headers={"Authorization": "mgp_some_token"},
        )

        assert response.status_code == 401, "Malformed auth header should be rejected"

    def test_valid_token_accepted(
        self,
        authenticated_client: httpx.Client,
    ) -> None:
        """Verify valid Bearer token is accepted by forward_auth and backend.

        GET /api/v1/artifacts should reliably return 200 with JSON body (even when empty),
        not 400/404. Accepting error codes would let broken routes slip through while
        still "passing" the forward_auth test.
        """
        response = authenticated_client.get("/api/v1/artifacts")

        # Auth should succeed and artifacts endpoint should return a valid JSON body
        assert response.status_code == 200, (
            "Valid token should be accepted and route should succeed"
        )

        data = response.json()
        assert "paths" in data, "Artifacts response should include a 'paths' field"

    def test_auth_headers_propagated_to_backend(
        self,
        authenticated_client: httpx.Client,
        test_artifact_content: bytes,
    ) -> None:
        """Verify X-Magpie-User and X-Magpie-Scope headers are propagated.

        The Caddyfile configures forward_auth to copy these headers from the
        auth validation response to the upstream request.
        """
        # Upload an artifact to get a response that includes metadata
        response = authenticated_client.post(
            "/api/v1/upload/routing-test/auth-headers",
            files={"file": ("artifact", test_artifact_content, "application/octet-stream")},
        )

        assert response.status_code == 200

        # The backend should have received X-Magpie-User and X-Magpie-Scope
        # We can't directly observe these in the response, but we can verify
        # the request succeeded (which requires the headers for authorization)
        data = response.json()
        assert "hash" in data
