"""Unit tests for TokenService operations."""

from __future__ import annotations

from pathlib import Path

import pytest

from magpie.auth.database import get_connection
from magpie.auth.models import TokenScope
from magpie.auth.service import TokenInfo, TokenService
from magpie.config import MagpieSettings
from magpie.validation import ValidationError


@pytest.fixture
def test_config(tmp_path: Path) -> MagpieSettings:
    """Create test configuration with temporary paths."""
    return MagpieSettings(storage_path=tmp_path)


@pytest.fixture
def token_service(test_config: MagpieSettings) -> TokenService:
    """Create a TokenService instance for testing."""
    return TokenService(test_config)


class TestCreateToken:
    """Tests for TokenService.create_token method."""

    def test_create_token_returns_proper_format(self, token_service: TokenService) -> None:
        """create_token should return token with mgp_ prefix."""
        token = token_service.create_token("test-token", TokenScope.READ)

        assert token.startswith("mgp_")
        assert len(token) > 10  # prefix + random part

    def test_create_token_read_scope_has_mgp_prefix(self, token_service: TokenService) -> None:
        """create_token for READ scope should use mgp_ prefix."""
        token = token_service.create_token("read-token", TokenScope.READ)

        assert token.startswith("mgp_")
        assert not token.startswith("mgp_ADMIN_")

    def test_create_token_write_scope_has_mgp_prefix(self, token_service: TokenService) -> None:
        """create_token for WRITE scope should use mgp_ prefix."""
        token = token_service.create_token("write-token", TokenScope.WRITE)

        assert token.startswith("mgp_")
        assert not token.startswith("mgp_ADMIN_")

    def test_create_token_admin_uses_admin_prefix(self, token_service: TokenService) -> None:
        """create_token for ADMIN scope should use mgp_ADMIN_ prefix."""
        token = token_service.create_token("admin-token", TokenScope.ADMIN)

        assert token.startswith("mgp_ADMIN_")

    def test_create_token_stores_hash_not_plaintext(
        self, token_service: TokenService, test_config: MagpieSettings
    ) -> None:
        """create_token should store hash, not plaintext."""
        plaintext = token_service.create_token("hashed-token", TokenScope.READ)

        # Check database doesn't contain plaintext
        conn = get_connection(test_config.database_path)
        cursor = conn.execute("SELECT token_hash FROM tokens WHERE name = ?", ("hashed-token",))
        row = cursor.fetchone()
        conn.close()

        assert row is not None
        assert row["token_hash"] != plaintext
        assert len(row["token_hash"]) == 64  # SHA-256 hex length

    def test_create_token_duplicate_name_raises(self, token_service: TokenService) -> None:
        """create_token should raise ValueError for duplicate name."""
        token_service.create_token("duplicate", TokenScope.READ)

        with pytest.raises(ValueError, match="already exists"):
            token_service.create_token("duplicate", TokenScope.WRITE)

    def test_create_token_generates_unique_tokens(self, token_service: TokenService) -> None:
        """create_token should generate unique tokens each time."""
        token1 = token_service.create_token("token1", TokenScope.READ)
        token2 = token_service.create_token("token2", TokenScope.READ)

        assert token1 != token2


class TestValidateToken:
    """Tests for TokenService.validate_token method."""

    def test_validate_token_success_returns_token_info(self, token_service: TokenService) -> None:
        """validate_token should return TokenInfo for valid token."""
        plaintext = token_service.create_token("valid-token", TokenScope.WRITE)

        result = token_service.validate_token(plaintext)

        assert result is not None
        assert isinstance(result, TokenInfo)
        assert result.name == "valid-token"
        assert result.scope == TokenScope.WRITE

    def test_validate_token_invalid_returns_none(self, token_service: TokenService) -> None:
        """validate_token should return None for invalid token."""
        result = token_service.validate_token("mgp_invalid_token_xyz")

        assert result is None

    def test_validate_token_disabled_returns_none(
        self, token_service: TokenService, test_config: MagpieSettings
    ) -> None:
        """validate_token should return None for disabled token."""
        plaintext = token_service.create_token("disabled-token", TokenScope.READ)

        # Disable the token directly in database
        conn = get_connection(test_config.database_path)
        conn.execute("UPDATE tokens SET enabled = 0 WHERE name = ?", ("disabled-token",))
        conn.commit()
        conn.close()

        result = token_service.validate_token(plaintext)

        assert result is None

    def test_validate_token_preserves_scope(self, token_service: TokenService) -> None:
        """validate_token should return correct scope."""
        read_token = token_service.create_token("read", TokenScope.READ)
        write_token = token_service.create_token("write", TokenScope.WRITE)
        admin_token = token_service.create_token("admin", TokenScope.ADMIN)

        assert token_service.validate_token(read_token).scope == TokenScope.READ
        assert token_service.validate_token(write_token).scope == TokenScope.WRITE
        assert token_service.validate_token(admin_token).scope == TokenScope.ADMIN

    def test_validate_token_empty_string_returns_none(self, token_service: TokenService) -> None:
        """validate_token should return None for empty string."""
        result = token_service.validate_token("")

        assert result is None


class TestRevokeToken:
    """Tests for TokenService.revoke_token method."""

    def test_revoke_token_removes_token(self, token_service: TokenService) -> None:
        """revoke_token should remove the token from database."""
        plaintext = token_service.create_token("to-revoke", TokenScope.READ)

        result = token_service.revoke_token("to-revoke")

        assert result is True
        assert token_service.validate_token(plaintext) is None

    def test_revoke_token_returns_true_on_success(self, token_service: TokenService) -> None:
        """revoke_token should return True when token is revoked."""
        token_service.create_token("revokable", TokenScope.READ)

        result = token_service.revoke_token("revokable")

        assert result is True

    def test_revoke_token_returns_false_for_nonexistent(self, token_service: TokenService) -> None:
        """revoke_token should return False for non-existent token."""
        result = token_service.revoke_token("nonexistent")

        assert result is False

    def test_revoked_token_no_longer_validates(self, token_service: TokenService) -> None:
        """A revoked token should no longer validate."""
        plaintext = token_service.create_token("revoke-validate", TokenScope.WRITE)

        # Verify token works before revocation
        assert token_service.validate_token(plaintext) is not None

        # Revoke
        token_service.revoke_token("revoke-validate")

        # Verify token no longer works
        assert token_service.validate_token(plaintext) is None


class TestHasScope:
    """Tests for TokenService.has_scope method."""

    def test_has_scope_admin_has_all_scopes(self, token_service: TokenService) -> None:
        """ADMIN scope should satisfy all required scopes."""
        assert token_service.has_scope(TokenScope.ADMIN, TokenScope.READ) is True
        assert token_service.has_scope(TokenScope.ADMIN, TokenScope.WRITE) is True
        assert token_service.has_scope(TokenScope.ADMIN, TokenScope.ADMIN) is True

    def test_has_scope_write_has_read_and_write(self, token_service: TokenService) -> None:
        """WRITE scope should satisfy READ and WRITE requirements."""
        assert token_service.has_scope(TokenScope.WRITE, TokenScope.READ) is True
        assert token_service.has_scope(TokenScope.WRITE, TokenScope.WRITE) is True
        assert token_service.has_scope(TokenScope.WRITE, TokenScope.ADMIN) is False

    def test_has_scope_read_only_has_read(self, token_service: TokenService) -> None:
        """READ scope should only satisfy READ requirement."""
        assert token_service.has_scope(TokenScope.READ, TokenScope.READ) is True
        assert token_service.has_scope(TokenScope.READ, TokenScope.WRITE) is False
        assert token_service.has_scope(TokenScope.READ, TokenScope.ADMIN) is False

    def test_has_scope_hierarchy_is_correct(self, token_service: TokenService) -> None:
        """Scope hierarchy should be: admin > write > read."""
        # Each scope should satisfy itself
        for scope in TokenScope:
            assert token_service.has_scope(scope, scope) is True

        # Lower scopes cannot access higher
        assert token_service.has_scope(TokenScope.READ, TokenScope.WRITE) is False
        assert token_service.has_scope(TokenScope.READ, TokenScope.ADMIN) is False
        assert token_service.has_scope(TokenScope.WRITE, TokenScope.ADMIN) is False


class TestTokenServiceInitialization:
    """Tests for TokenService initialization."""

    def test_service_initializes_database(self, test_config: MagpieSettings) -> None:
        """TokenService should initialize database on creation."""
        # Database should not exist yet
        assert not test_config.database_path.exists()

        # Create service
        TokenService(test_config)

        # Database should now exist
        assert test_config.database_path.exists()

    def test_service_can_be_created_multiple_times(self, test_config: MagpieSettings) -> None:
        """Multiple TokenService instances should work correctly."""
        service1 = TokenService(test_config)
        service2 = TokenService(test_config)

        # Create token with one service
        plaintext = service1.create_token("shared", TokenScope.READ)

        # Validate with another service
        result = service2.validate_token(plaintext)

        assert result is not None
        assert result.name == "shared"


class TestTokenNameValidation:
    """Tests for token name validation in TokenService.create_token.

    Defense-in-depth validation at service layer ensures CLI and other
    non-API callers also get proper validation.
    """

    def test_create_token_with_invalid_name_raises_validation_error(
        self, token_service: TokenService
    ) -> None:
        """create_token should raise ValidationError for invalid name."""
        with pytest.raises(ValidationError, match="must start with alphanumeric"):
            token_service.create_token("-invalid-name", TokenScope.READ)

    def test_create_token_with_empty_name_raises_validation_error(
        self, token_service: TokenService
    ) -> None:
        """create_token should raise ValidationError for empty name."""
        with pytest.raises(ValidationError, match="cannot be empty"):
            token_service.create_token("", TokenScope.READ)

    def test_create_token_with_too_long_name_raises_validation_error(
        self, token_service: TokenService
    ) -> None:
        """create_token should raise ValidationError for name exceeding max length."""
        long_name = "a" * 65  # Max is 64
        with pytest.raises(ValidationError, match="exceeds maximum length"):
            token_service.create_token(long_name, TokenScope.READ)

    def test_create_token_with_name_starting_with_dot_raises(
        self, token_service: TokenService
    ) -> None:
        """create_token should raise ValidationError for name starting with dot."""
        with pytest.raises(ValidationError):
            token_service.create_token(".hidden", TokenScope.READ)

    def test_create_token_with_name_containing_spaces_raises(
        self, token_service: TokenService
    ) -> None:
        """create_token should raise ValidationError for name with spaces."""
        with pytest.raises(ValidationError):
            token_service.create_token("my token", TokenScope.READ)

    def test_create_token_with_special_chars_raises(self, token_service: TokenService) -> None:
        """create_token should raise ValidationError for name with special chars."""
        with pytest.raises(ValidationError):
            token_service.create_token("token@123!", TokenScope.READ)

    def test_validation_error_is_catchable_as_value_error(
        self, token_service: TokenService
    ) -> None:
        """ValidationError should be catchable as ValueError for backward compatibility."""
        try:
            token_service.create_token("-invalid", TokenScope.READ)
            assert False, "Should have raised an exception"
        except ValueError:
            pass  # ValidationError is a subclass of ValueError
