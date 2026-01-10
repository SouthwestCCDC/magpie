"""Integration tests for token management endpoints."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from magpie.auth.models import TokenScope
from magpie.auth.service import TokenService
from magpie.config import MagpieSettings
from magpie.server.app import app
from magpie.server.deps import get_token_service


@pytest.fixture
def test_config(tmp_path: Path) -> MagpieSettings:
    """Create test configuration with temporary paths."""
    config = MagpieSettings(storage_path=tmp_path)
    config.temp_path.mkdir(parents=True, exist_ok=True)
    return config


@pytest.fixture
def token_service(test_config: MagpieSettings) -> TokenService:
    """Create a TokenService instance for testing."""
    return TokenService(test_config)


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
def client(token_service: TokenService) -> TestClient:
    """Create test client with overridden token service dependency."""

    def override_token_service() -> TokenService:
        return token_service

    app.dependency_overrides[get_token_service] = override_token_service
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


class TestCreateTokenEndpoint:
    """Tests for POST /api/v1/tokens endpoint."""

    def test_create_token_returns_plaintext(
        self, client: TestClient, admin_token: str
    ) -> None:
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

    def test_create_token_requires_admin_scope(
        self, client: TestClient, admin_token: str
    ) -> None:
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

    def test_create_token_without_auth_returns_401(
        self, client: TestClient
    ) -> None:
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

    def test_list_tokens_requires_admin_scope(
        self, client: TestClient, read_token: str
    ) -> None:
        """List tokens requires admin scope."""
        response = client.get(
            "/api/v1/tokens",
            headers={"Authorization": f"Bearer {read_token}"},
        )

        assert response.status_code == 403

    def test_list_tokens_without_auth_returns_401(
        self, client: TestClient
    ) -> None:
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
