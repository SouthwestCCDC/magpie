"""Integration tests for token management endpoints."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from magpie.auth.models import TokenScope
from magpie.auth.service import TokenService
from magpie.server.app import app
from magpie.server.deps import get_token_service


@pytest.fixture
def client(token_service: TokenService) -> TestClient:
    """Create test client with overridden token service dependency.

    Note: This fixture removes auth overrides from the autouse conftest fixture
    so that actual token authentication is tested.
    """
    # Clear any auth overrides from the autouse conftest fixture
    # so that real authentication is tested
    app.dependency_overrides.clear()

    def override_token_service() -> TokenService:
        return token_service

    app.dependency_overrides[get_token_service] = override_token_service
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


class TestCreateTokenEndpoint:
    """Tests for POST /api/v1/tokens endpoint."""

    def test_create_token_returns_plaintext(self, client: TestClient, admin_token: str) -> None:
        """Create token returns plaintext token (only time visible)."""
        response = client.post(
            "/api/v1/tokens",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"name": "new-service", "scope": "read"},
        )

        assert response.status_code == 200
        data = response.json()

        assert data["name"] == "new-service"
        assert data["scope"] == "read"
        assert "token" in data
        assert data["token"].startswith("mgp_")

    def test_create_token_requires_admin_scope(self, client: TestClient, admin_token: str) -> None:
        """Create token requires admin scope."""
        response = client.post(
            "/api/v1/tokens",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"name": "admin-created", "scope": "write"},
        )

        assert response.status_code == 200

    def test_create_token_with_read_scope_returns_403(
        self, client: TestClient, read_token: str
    ) -> None:
        """Create token with non-admin (read) scope returns 403."""
        response = client.post(
            "/api/v1/tokens",
            headers={"Authorization": f"Bearer {read_token}"},
            json={"name": "should-fail", "scope": "read"},
        )

        assert response.status_code == 403
        assert "Admin scope required" in response.json()["detail"]

    def test_create_token_with_write_scope_returns_403(
        self, client: TestClient, write_token: str
    ) -> None:
        """Create token with non-admin (write) scope returns 403."""
        response = client.post(
            "/api/v1/tokens",
            headers={"Authorization": f"Bearer {write_token}"},
            json={"name": "should-fail", "scope": "read"},
        )

        assert response.status_code == 403

    def test_create_token_without_auth_returns_401(self, client: TestClient) -> None:
        """Create token without authorization returns 401."""
        response = client.post(
            "/api/v1/tokens",
            json={"name": "no-auth", "scope": "read"},
        )

        assert response.status_code == 401

    def test_create_admin_token_uses_admin_prefix(
        self, client: TestClient, admin_token: str
    ) -> None:
        """Create admin token returns token with mgp_ADMIN_ prefix."""
        response = client.post(
            "/api/v1/tokens",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"name": "new-admin", "scope": "admin"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["token"].startswith("mgp_ADMIN_")

    def test_create_token_invalid_scope_returns_400(
        self, client: TestClient, admin_token: str
    ) -> None:
        """Create token with invalid scope returns 400."""
        response = client.post(
            "/api/v1/tokens",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"name": "invalid", "scope": "superadmin"},
        )

        assert response.status_code == 400
        assert "Invalid scope" in response.json()["detail"]

    def test_create_token_duplicate_name_returns_400(
        self, client: TestClient, admin_token: str
    ) -> None:
        """Create token with duplicate name returns 400."""
        # Create first token
        client.post(
            "/api/v1/tokens",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"name": "duplicate", "scope": "read"},
        )

        # Try to create duplicate
        response = client.post(
            "/api/v1/tokens",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"name": "duplicate", "scope": "write"},
        )

        assert response.status_code == 400
        assert "already exists" in response.json()["detail"]


class TestCreateTokenNameValidation:
    """Tests for token name validation in POST /api/v1/tokens endpoint."""

    def test_valid_alphanumeric_name(self, client: TestClient, admin_token: str) -> None:
        """Valid alphanumeric token name is accepted."""
        response = client.post(
            "/api/v1/tokens",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"name": "myservice123", "scope": "read"},
        )

        assert response.status_code == 200
        assert response.json()["name"] == "myservice123"

    def test_valid_name_with_dots(self, client: TestClient, admin_token: str) -> None:
        """Token name with dots is accepted."""
        response = client.post(
            "/api/v1/tokens",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"name": "my.service.name", "scope": "read"},
        )

        assert response.status_code == 200
        assert response.json()["name"] == "my.service.name"

    def test_valid_name_with_underscores(self, client: TestClient, admin_token: str) -> None:
        """Token name with underscores is accepted."""
        response = client.post(
            "/api/v1/tokens",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"name": "my_service_name", "scope": "read"},
        )

        assert response.status_code == 200
        assert response.json()["name"] == "my_service_name"

    def test_valid_name_with_hyphens(self, client: TestClient, admin_token: str) -> None:
        """Token name with hyphens is accepted."""
        response = client.post(
            "/api/v1/tokens",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"name": "my-service-name", "scope": "read"},
        )

        assert response.status_code == 200
        assert response.json()["name"] == "my-service-name"

    def test_valid_single_character_name(self, client: TestClient, admin_token: str) -> None:
        """Single alphanumeric character token name is accepted."""
        response = client.post(
            "/api/v1/tokens",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"name": "a", "scope": "read"},
        )

        assert response.status_code == 200
        assert response.json()["name"] == "a"

    def test_invalid_name_starting_with_hyphen_returns_422(
        self, client: TestClient, admin_token: str
    ) -> None:
        """Token name starting with hyphen returns 422."""
        response = client.post(
            "/api/v1/tokens",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"name": "-invalid", "scope": "read"},
        )

        assert response.status_code == 422

    def test_invalid_name_starting_with_dot_returns_422(
        self, client: TestClient, admin_token: str
    ) -> None:
        """Token name starting with dot returns 422."""
        response = client.post(
            "/api/v1/tokens",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"name": ".invalid", "scope": "read"},
        )

        assert response.status_code == 422

    def test_invalid_name_starting_with_underscore_returns_422(
        self, client: TestClient, admin_token: str
    ) -> None:
        """Token name starting with underscore returns 422."""
        response = client.post(
            "/api/v1/tokens",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"name": "_invalid", "scope": "read"},
        )

        assert response.status_code == 422

    def test_invalid_name_with_spaces_returns_422(
        self, client: TestClient, admin_token: str
    ) -> None:
        """Token name with spaces returns 422."""
        response = client.post(
            "/api/v1/tokens",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"name": "invalid name", "scope": "read"},
        )

        assert response.status_code == 422

    def test_invalid_name_with_special_chars_returns_422(
        self, client: TestClient, admin_token: str
    ) -> None:
        """Token name with special characters returns 422."""
        response = client.post(
            "/api/v1/tokens",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"name": "invalid@name!", "scope": "read"},
        )

        assert response.status_code == 422

    def test_invalid_empty_name_returns_422(self, client: TestClient, admin_token: str) -> None:
        """Empty token name returns 422."""
        response = client.post(
            "/api/v1/tokens",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"name": "", "scope": "read"},
        )

        assert response.status_code == 422

    def test_invalid_name_too_long_returns_422(self, client: TestClient, admin_token: str) -> None:
        """Token name exceeding 64 characters returns 422."""
        long_name = "a" * 65  # 65 characters, exceeds max of 64
        response = client.post(
            "/api/v1/tokens",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"name": long_name, "scope": "read"},
        )

        assert response.status_code == 422

    def test_valid_max_length_name(self, client: TestClient, admin_token: str) -> None:
        """Token name at max length (64 chars) is accepted."""
        max_name = "a" * 64  # Exactly 64 characters
        response = client.post(
            "/api/v1/tokens",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"name": max_name, "scope": "read"},
        )

        assert response.status_code == 200
        assert response.json()["name"] == max_name


class TestListTokensEndpoint:
    """Tests for GET /api/v1/tokens endpoint."""

    def test_list_tokens_returns_all_tokens(
        self, client: TestClient, admin_token: str, token_service: TokenService
    ) -> None:
        """List tokens returns all tokens."""
        # Create additional tokens
        token_service.create_token("extra1", TokenScope.READ)
        token_service.create_token("extra2", TokenScope.WRITE)

        response = client.get(
            "/api/v1/tokens",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        assert response.status_code == 200
        data = response.json()

        # Should have admin + 2 extra tokens
        assert len(data["tokens"]) >= 3

        names = [t["name"] for t in data["tokens"]]
        assert "test-admin" in names
        assert "extra1" in names
        assert "extra2" in names

    def test_list_tokens_does_not_include_hashes(
        self, client: TestClient, admin_token: str
    ) -> None:
        """List tokens response does not include token_hash field."""
        response = client.get(
            "/api/v1/tokens",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        assert response.status_code == 200
        data = response.json()

        for token in data["tokens"]:
            assert "token_hash" not in token
            assert "hash" not in token

    def test_list_tokens_includes_expected_fields(
        self, client: TestClient, admin_token: str
    ) -> None:
        """List tokens includes name, scope, enabled, created_at."""
        response = client.get(
            "/api/v1/tokens",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        assert response.status_code == 200
        data = response.json()

        for token in data["tokens"]:
            assert "name" in token
            assert "scope" in token
            assert "enabled" in token
            assert "created_at" in token

    def test_list_tokens_requires_admin_scope(self, client: TestClient, read_token: str) -> None:
        """List tokens requires admin scope."""
        response = client.get(
            "/api/v1/tokens",
            headers={"Authorization": f"Bearer {read_token}"},
        )

        assert response.status_code == 403

    def test_list_tokens_without_auth_returns_401(self, client: TestClient) -> None:
        """List tokens without authorization returns 401."""
        response = client.get("/api/v1/tokens")

        assert response.status_code == 401


class TestRevokeTokenEndpoint:
    """Tests for DELETE /api/v1/tokens/{name} endpoint."""

    def test_revoke_token_removes_token(
        self, client: TestClient, admin_token: str, token_service: TokenService
    ) -> None:
        """Revoke token removes the token."""
        # Create a token to revoke
        to_revoke = token_service.create_token("to-revoke", TokenScope.READ)

        response = client.delete(
            "/api/v1/tokens/to-revoke",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        assert response.status_code == 204

        # Verify token is gone
        assert token_service.validate_token(to_revoke) is None

    def test_revoke_token_requires_admin_scope(
        self, client: TestClient, read_token: str, token_service: TokenService
    ) -> None:
        """Revoke token requires admin scope."""
        token_service.create_token("target", TokenScope.READ)

        response = client.delete(
            "/api/v1/tokens/target",
            headers={"Authorization": f"Bearer {read_token}"},
        )

        assert response.status_code == 403

    def test_revoke_token_with_write_scope_returns_403(
        self, client: TestClient, write_token: str, token_service: TokenService
    ) -> None:
        """Revoke token with write scope returns 403."""
        token_service.create_token("target2", TokenScope.READ)

        response = client.delete(
            "/api/v1/tokens/target2",
            headers={"Authorization": f"Bearer {write_token}"},
        )

        assert response.status_code == 403

    def test_revoke_nonexistent_token_returns_404(
        self, client: TestClient, admin_token: str
    ) -> None:
        """Revoke nonexistent token returns 404."""
        response = client.delete(
            "/api/v1/tokens/nonexistent-token",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        assert response.status_code == 404
        assert "not found" in response.json()["detail"]

    def test_revoke_token_without_auth_returns_401(
        self, client: TestClient, token_service: TokenService
    ) -> None:
        """Revoke token without authorization returns 401."""
        token_service.create_token("no-auth-target", TokenScope.READ)

        response = client.delete("/api/v1/tokens/no-auth-target")

        assert response.status_code == 401

    def test_revoked_token_no_longer_validates(
        self, client: TestClient, admin_token: str, token_service: TokenService
    ) -> None:
        """Revoked token can no longer be used for authentication."""
        # Create a token
        victim_token = token_service.create_token("victim", TokenScope.ADMIN)

        # Verify it works
        check_response = client.get(
            "/api/v1/auth/validate",
            headers={"Authorization": f"Bearer {victim_token}"},
        )
        assert check_response.status_code == 200

        # Revoke it
        client.delete(
            "/api/v1/tokens/victim",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        # Verify it no longer works
        check_response = client.get(
            "/api/v1/auth/validate",
            headers={"Authorization": f"Bearer {victim_token}"},
        )
        assert check_response.status_code == 401


class TestRotateTokenEndpoint:
    """Tests for POST /api/v1/tokens/{name}/rotate endpoint."""

    def test_rotate_token_returns_new_plaintext(
        self, client: TestClient, admin_token: str, token_service: TokenService
    ) -> None:
        """Rotate token returns new plaintext token."""
        original_token = token_service.create_token("to-rotate", TokenScope.WRITE)

        response = client.post(
            "/api/v1/tokens/to-rotate/rotate",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        assert response.status_code == 200
        data = response.json()

        assert data["name"] == "to-rotate"
        assert data["scope"] == "write"
        assert "token" in data
        assert data["token"].startswith("mgp_")
        assert data["token"] != original_token

    def test_rotate_token_invalidates_old_token(
        self, client: TestClient, admin_token: str, token_service: TokenService
    ) -> None:
        """Rotate token invalidates the old token."""
        original_token = token_service.create_token("invalidate-test", TokenScope.READ)

        # Verify original works
        assert token_service.validate_token(original_token) is not None

        # Rotate
        response = client.post(
            "/api/v1/tokens/invalidate-test/rotate",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200

        # Verify original no longer works
        assert token_service.validate_token(original_token) is None

    def test_rotate_token_preserves_scope(
        self, client: TestClient, admin_token: str, token_service: TokenService
    ) -> None:
        """Rotate token preserves the original scope."""
        token_service.create_token("scope-test", TokenScope.ADMIN)

        response = client.post(
            "/api/v1/tokens/scope-test/rotate",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["scope"] == "admin"
        assert data["token"].startswith("mgp_ADMIN_")

    def test_rotate_token_supports_self_rotation(
        self, client: TestClient, token_service: TokenService
    ) -> None:
        """Rotate token supports rotating the token currently being used."""
        # Create an admin token that will rotate itself
        self_rotate_token = token_service.create_token("self-rotate", TokenScope.ADMIN)

        # Use the token to rotate itself
        response = client.post(
            "/api/v1/tokens/self-rotate/rotate",
            headers={"Authorization": f"Bearer {self_rotate_token}"},
        )

        assert response.status_code == 200
        data = response.json()

        # New token should work
        new_token = data["token"]
        assert new_token != self_rotate_token

        # Old token should not work
        assert token_service.validate_token(self_rotate_token) is None

        # New token should work
        assert token_service.validate_token(new_token) is not None

    def test_rotate_token_requires_admin_scope(
        self, client: TestClient, read_token: str, token_service: TokenService
    ) -> None:
        """Rotate token requires admin scope."""
        token_service.create_token("target", TokenScope.READ)

        response = client.post(
            "/api/v1/tokens/target/rotate",
            headers={"Authorization": f"Bearer {read_token}"},
        )

        assert response.status_code == 403

    def test_rotate_token_with_write_scope_returns_403(
        self, client: TestClient, write_token: str, token_service: TokenService
    ) -> None:
        """Rotate token with write scope returns 403."""
        token_service.create_token("target2", TokenScope.READ)

        response = client.post(
            "/api/v1/tokens/target2/rotate",
            headers={"Authorization": f"Bearer {write_token}"},
        )

        assert response.status_code == 403

    def test_rotate_nonexistent_token_returns_404(
        self, client: TestClient, admin_token: str
    ) -> None:
        """Rotate nonexistent token returns 404."""
        response = client.post(
            "/api/v1/tokens/nonexistent/rotate",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        assert response.status_code == 404
        assert "not found" in response.json()["detail"]

    def test_rotate_token_without_auth_returns_401(self, client: TestClient) -> None:
        """Rotate token without authorization returns 401."""
        response = client.post("/api/v1/tokens/some-token/rotate")

        assert response.status_code == 401

    def test_rotate_token_works_for_all_scopes(
        self, client: TestClient, admin_token: str, token_service: TokenService
    ) -> None:
        """Rotate token works for all scope levels."""
        scopes = [
            (TokenScope.READ, "read", "mgp_"),
            (TokenScope.WRITE, "write", "mgp_"),
            (TokenScope.ADMIN, "admin", "mgp_ADMIN_"),
        ]

        for scope, scope_str, prefix in scopes:
            name = f"rotate-{scope_str}"
            token_service.create_token(name, scope)

            response = client.post(
                f"/api/v1/tokens/{name}/rotate",
                headers={"Authorization": f"Bearer {admin_token}"},
            )

            assert response.status_code == 200
            data = response.json()
            assert data["name"] == name
            assert data["scope"] == scope_str
            assert data["token"].startswith(prefix)
