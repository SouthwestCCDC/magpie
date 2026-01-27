"""Unit tests for auth validation endpoint."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from magpie.auth.database import get_connection
from magpie.auth.models import TokenScope
from magpie.auth.service import TokenService
from magpie.config import MagpieSettings
from magpie.server.app import app
from magpie.server.deps import get_token_service


@pytest.fixture
def test_config(tmp_path: Path) -> MagpieSettings:
    """Create test configuration with temporary paths."""
    config = MagpieSettings(
        storage_path=tmp_path,
        database_path=tmp_path / "magpie.db",
    )
    config.temp_path.mkdir(parents=True, exist_ok=True)
    return config


@pytest.fixture
def token_service(test_config: MagpieSettings) -> TokenService:
    """Create a TokenService instance for testing."""
    return TokenService(test_config)


@pytest.fixture
def client(token_service: TokenService) -> TestClient:
    """Create test client with overridden token service dependency."""

    def override_token_service() -> TokenService:
        return token_service

    app.dependency_overrides[get_token_service] = override_token_service
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


class TestValidTokenReturns200:
    """Tests for valid token returning 200 with correct headers."""

    def test_valid_token_returns_200(self, client: TestClient, token_service: TokenService) -> None:
        """Valid token should return 200 OK."""
        plaintext = token_service.create_token("test-user", TokenScope.WRITE)

        response = client.get(
            "/api/v1/auth/validate",
            headers={"Authorization": f"Bearer {plaintext}"},
        )

        assert response.status_code == 200

    def test_valid_token_returns_user_header(
        self, client: TestClient, token_service: TokenService
    ) -> None:
        """Valid token should return X-Magpie-User header with token name."""
        plaintext = token_service.create_token("my-service", TokenScope.READ)

        response = client.get(
            "/api/v1/auth/validate",
            headers={"Authorization": f"Bearer {plaintext}"},
        )

        assert response.status_code == 200
        assert response.headers["X-Magpie-User"] == "my-service"

    def test_valid_token_returns_scope_header(
        self, client: TestClient, token_service: TokenService
    ) -> None:
        """Valid token should return X-Magpie-Scope header with scope value."""
        plaintext = token_service.create_token("admin-svc", TokenScope.ADMIN)

        response = client.get(
            "/api/v1/auth/validate",
            headers={"Authorization": f"Bearer {plaintext}"},
        )

        assert response.status_code == 200
        assert response.headers["X-Magpie-Scope"] == "admin"


class TestInvalidTokenReturns401:
    """Tests for invalid token returning 401."""

    def test_invalid_token_returns_401(self, client: TestClient) -> None:
        """Invalid token should return 401 Unauthorized."""
        response = client.get(
            "/api/v1/auth/validate",
            headers={"Authorization": "Bearer mgp_invalid_token_xyz123"},
        )

        assert response.status_code == 401

    def test_invalid_token_returns_json_error(self, client: TestClient) -> None:
        """Invalid token should return JSON error body."""
        response = client.get(
            "/api/v1/auth/validate",
            headers={"Authorization": "Bearer mgp_invalid_token_xyz123"},
        )

        assert response.status_code == 401
        data = response.json()
        assert "detail" in data


class TestMissingAuthorizationHeader:
    """Tests for missing Authorization header returning 401."""

    def test_missing_authorization_returns_401(self, client: TestClient) -> None:
        """Missing Authorization header should return 401."""
        response = client.get("/api/v1/auth/validate")

        assert response.status_code == 401

    def test_missing_authorization_returns_json_error(self, client: TestClient) -> None:
        """Missing Authorization header should return JSON error with message."""
        response = client.get("/api/v1/auth/validate")

        assert response.status_code == 401
        data = response.json()
        assert "detail" in data
        assert "Authorization" in data["detail"] or "header" in data["detail"].lower()


class TestMalformedAuthorizationHeader:
    """Tests for malformed Authorization header returning 401."""

    def test_malformed_no_bearer_returns_401(self, client: TestClient) -> None:
        """Authorization header without 'Bearer ' prefix should return 401."""
        response = client.get(
            "/api/v1/auth/validate",
            headers={"Authorization": "mgp_some_token_here"},
        )

        assert response.status_code == 401

    def test_malformed_basic_auth_returns_401(self, client: TestClient) -> None:
        """Basic auth header instead of Bearer should return 401."""
        response = client.get(
            "/api/v1/auth/validate",
            headers={"Authorization": "Basic dXNlcjpwYXNz"},
        )

        assert response.status_code == 401

    def test_malformed_returns_json_error(self, client: TestClient) -> None:
        """Malformed header should return JSON error with message."""
        response = client.get(
            "/api/v1/auth/validate",
            headers={"Authorization": "Token abc123"},
        )

        assert response.status_code == 401
        data = response.json()
        assert "detail" in data
        assert "Bearer" in data["detail"] or "malformed" in data["detail"].lower()


class TestDisabledTokenReturns401:
    """Tests for disabled token returning 401."""

    def test_disabled_token_returns_401(
        self, client: TestClient, token_service: TokenService, test_config: MagpieSettings
    ) -> None:
        """Disabled token should return 401 Unauthorized."""
        plaintext = token_service.create_token("disabled-token", TokenScope.READ)

        # Disable the token directly in database
        conn = get_connection(test_config.database_path)
        conn.execute("UPDATE tokens SET enabled = 0 WHERE name = ?", ("disabled-token",))
        conn.commit()
        conn.close()

        response = client.get(
            "/api/v1/auth/validate",
            headers={"Authorization": f"Bearer {plaintext}"},
        )

        assert response.status_code == 401

    def test_disabled_token_returns_json_error(
        self, client: TestClient, token_service: TokenService, test_config: MagpieSettings
    ) -> None:
        """Disabled token should return JSON error body."""
        plaintext = token_service.create_token("disabled-json", TokenScope.WRITE)

        # Disable the token
        conn = get_connection(test_config.database_path)
        conn.execute("UPDATE tokens SET enabled = 0 WHERE name = ?", ("disabled-json",))
        conn.commit()
        conn.close()

        response = client.get(
            "/api/v1/auth/validate",
            headers={"Authorization": f"Bearer {plaintext}"},
        )

        assert response.status_code == 401
        data = response.json()
        assert "detail" in data


class TestResponseHeadersCorrectValues:
    """Tests for response headers containing correct user and scope values."""

    def test_read_scope_in_header(self, client: TestClient, token_service: TokenService) -> None:
        """READ scope token should return 'read' in X-Magpie-Scope header."""
        plaintext = token_service.create_token("read-svc", TokenScope.READ)

        response = client.get(
            "/api/v1/auth/validate",
            headers={"Authorization": f"Bearer {plaintext}"},
        )

        assert response.status_code == 200
        assert response.headers["X-Magpie-Scope"] == "read"

    def test_write_scope_in_header(self, client: TestClient, token_service: TokenService) -> None:
        """WRITE scope token should return 'write' in X-Magpie-Scope header."""
        plaintext = token_service.create_token("write-svc", TokenScope.WRITE)

        response = client.get(
            "/api/v1/auth/validate",
            headers={"Authorization": f"Bearer {plaintext}"},
        )

        assert response.status_code == 200
        assert response.headers["X-Magpie-Scope"] == "write"

    def test_admin_scope_in_header(self, client: TestClient, token_service: TokenService) -> None:
        """ADMIN scope token should return 'admin' in X-Magpie-Scope header."""
        plaintext = token_service.create_token("admin-svc", TokenScope.ADMIN)

        response = client.get(
            "/api/v1/auth/validate",
            headers={"Authorization": f"Bearer {plaintext}"},
        )

        assert response.status_code == 200
        assert response.headers["X-Magpie-Scope"] == "admin"

    def test_user_header_matches_token_name(
        self, client: TestClient, token_service: TokenService
    ) -> None:
        """X-Magpie-User header should match the token name exactly."""
        token_name = "my-special-service-token"
        plaintext = token_service.create_token(token_name, TokenScope.READ)

        response = client.get(
            "/api/v1/auth/validate",
            headers={"Authorization": f"Bearer {plaintext}"},
        )

        assert response.status_code == 200
        assert response.headers["X-Magpie-User"] == token_name

    def test_both_headers_present(self, client: TestClient, token_service: TokenService) -> None:
        """Both X-Magpie-User and X-Magpie-Scope headers should be present."""
        plaintext = token_service.create_token("dual-header", TokenScope.WRITE)

        response = client.get(
            "/api/v1/auth/validate",
            headers={"Authorization": f"Bearer {plaintext}"},
        )

        assert response.status_code == 200
        assert "X-Magpie-User" in response.headers
        assert "X-Magpie-Scope" in response.headers
        assert response.headers["X-Magpie-User"] == "dual-header"
        assert response.headers["X-Magpie-Scope"] == "write"
