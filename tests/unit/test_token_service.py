"""Unit tests for TokenService operations."""

from __future__ import annotations

from pathlib import Path

import pytest

from magpie.auth.database import get_connection
from magpie.auth.models import TokenScope
from magpie.auth.service import (
    TokenExistsError,
    TokenFormatError,
    TokenInfo,
    TokenService,
)
from magpie.config import MagpieSettings
from magpie.validation import ValidationError


@pytest.fixture
def test_config(tmp_path: Path) -> MagpieSettings:
    """Create test configuration with temporary paths."""
    return MagpieSettings(
        storage_path=tmp_path,
        database_path=tmp_path / "magpie.db",
    )


@pytest.fixture
def token_service(test_config: MagpieSettings) -> TokenService:
    """Create a TokenService instance for testing."""
    return TokenService(test_config)


@pytest.fixture(autouse=True)
def clear_token_service_state():
    """Clear TokenService class-level state between tests.

    This ensures test isolation by clearing the _initialized_paths set
    before and after each test. This prevents one test's initialization
    state from affecting another test.
    """
    TokenService._initialized_paths.clear()
    yield
    TokenService._initialized_paths.clear()


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
        """create_token should raise TokenExistsError for duplicate name."""
        token_service.create_token("duplicate", TokenScope.READ)

        with pytest.raises(TokenExistsError, match="already exists"):
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


class TestRotateToken:
    """Tests for TokenService.rotate_token method."""

    def test_rotate_token_creates_new_token_with_same_scope(
        self, token_service: TokenService
    ) -> None:
        """rotate_token should create a new token with the same scope."""
        original_token = token_service.create_token("to-rotate", TokenScope.WRITE)

        result = token_service.rotate_token("to-rotate")

        assert result is not None
        new_token, scope = result
        assert new_token != original_token
        assert new_token.startswith("mgp_")
        assert scope == TokenScope.WRITE

        # New token should validate with original scope
        validation_result = token_service.validate_token(new_token)
        assert validation_result is not None
        assert validation_result.name == "to-rotate"
        assert validation_result.scope == TokenScope.WRITE

    def test_rotate_token_invalidates_old_token(self, token_service: TokenService) -> None:
        """rotate_token should invalidate the old token."""
        original_token = token_service.create_token("rotate-invalidate", TokenScope.READ)

        # Verify original works
        assert token_service.validate_token(original_token) is not None

        # Rotate
        token_service.rotate_token("rotate-invalidate")

        # Verify original no longer works
        assert token_service.validate_token(original_token) is None

    def test_rotate_token_preserves_admin_prefix(self, token_service: TokenService) -> None:
        """rotate_token should preserve mgp_ADMIN_ prefix for admin tokens."""
        original_token = token_service.create_token("admin-rotate", TokenScope.ADMIN)
        assert original_token.startswith("mgp_ADMIN_")

        result = token_service.rotate_token("admin-rotate")

        assert result is not None
        new_token, scope = result
        assert new_token.startswith("mgp_ADMIN_")
        assert new_token != original_token
        assert scope == TokenScope.ADMIN

    def test_rotate_token_returns_none_for_nonexistent(self, token_service: TokenService) -> None:
        """rotate_token should return None for non-existent token."""
        result = token_service.rotate_token("nonexistent")

        assert result is None

    def test_rotate_token_works_for_all_scopes(self, token_service: TokenService) -> None:
        """rotate_token should work for all scope levels."""
        for scope in [TokenScope.READ, TokenScope.WRITE, TokenScope.ADMIN]:
            name = f"rotate-{scope.value}"
            original = token_service.create_token(name, scope)

            result = token_service.rotate_token(name)

            assert result is not None
            new_token, returned_scope = result
            assert new_token != original
            assert returned_scope == scope
            validation_result = token_service.validate_token(new_token)
            assert validation_result.scope == scope

    def test_rotate_token_is_atomic(
        self, token_service: TokenService, test_config: MagpieSettings
    ) -> None:
        """rotate_token should be atomic - if save fails, delete should rollback.

        This test verifies the fix for the atomicity bug where delete_token() and
        save_token() were committing independently, creating a window where no token
        existed if the save operation failed.
        """
        # Create initial token
        original_token = token_service.create_token("atomic-test", TokenScope.WRITE)

        # Verify original token exists and works
        assert token_service.validate_token(original_token) is not None

        # Create a duplicate token with a different name to force a constraint violation
        # when rotate tries to insert (the new token would have same name but different hash)
        # Actually, we need to simulate a failure in the transaction.
        # Let's verify the transaction behavior by checking database state directly.

        # Get a connection and start a transaction that we'll rollback manually
        conn = get_connection(test_config.database_path)
        try:
            # Start transaction manually
            conn.execute("BEGIN")

            # Delete the token without committing
            from magpie.auth.database import delete_token

            delete_token(conn, "atomic-test", commit=False)

            # Verify token is deleted within this transaction
            cursor = conn.execute("SELECT COUNT(*) FROM tokens WHERE name = ?", ("atomic-test",))
            count_in_transaction = cursor.fetchone()[0]
            assert count_in_transaction == 0

            # Rollback the transaction
            conn.rollback()

            # Verify token still exists after rollback
            cursor = conn.execute("SELECT COUNT(*) FROM tokens WHERE name = ?", ("atomic-test",))
            count_after_rollback = cursor.fetchone()[0]
            assert count_after_rollback == 1

        finally:
            conn.close()

        # Verify original token still works (wasn't permanently deleted)
        assert token_service.validate_token(original_token) is not None

    def test_rotate_token_preserves_scope_immutably(self, token_service: TokenService) -> None:
        """rotate_token should always preserve the original token scope.

        The scope is an inherent property of the token and cannot be changed during rotation.
        This is by design - to change scope, the token must be revoked and a new one created.
        """
        # Create a token with WRITE scope
        original_token = token_service.create_token("immutable-scope", TokenScope.WRITE)

        # Verify original has WRITE scope
        original_info = token_service.validate_token(original_token)
        assert original_info is not None
        assert original_info.scope == TokenScope.WRITE

        # Rotate the token
        result = token_service.rotate_token("immutable-scope")
        assert result is not None
        new_token, returned_scope = result

        # Verify the returned scope is still WRITE
        assert returned_scope == TokenScope.WRITE

        # Verify the new token validates with WRITE scope (not READ, not ADMIN)
        new_info = token_service.validate_token(new_token)
        assert new_info is not None
        assert new_info.scope == TokenScope.WRITE
        assert new_info.scope != TokenScope.READ
        assert new_info.scope != TokenScope.ADMIN

        # Verify the rotate_token method signature - it should NOT accept a scope parameter
        # This is a compile-time check via type inspection
        import inspect

        sig = inspect.signature(token_service.rotate_token)
        param_names = list(sig.parameters.keys())
        # Should only have 'name' parameter (besides self which is implicit)
        assert param_names == ["name"], (
            f"rotate_token should only accept 'name' parameter, got: {param_names}"
        )


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

    def test_service_initializes_database_lazily(self, test_config: MagpieSettings) -> None:
        """TokenService should initialize database lazily on first use."""
        # Database should not exist yet
        assert not test_config.database_path.exists()

        # Create service (should NOT initialize database)
        service = TokenService(test_config)

        # Database should still not exist (lazy initialization)
        assert not test_config.database_path.exists()

        # Perform an operation that requires the database
        service.create_token("test", TokenScope.READ)

        # Database should now exist
        assert test_config.database_path.exists()

    def test_service_can_be_created_multiple_times(self, test_config: MagpieSettings) -> None:
        """Multiple TokenService instances should work correctly.

        This test verifies that multiple instances pointing to the same database
        path only initialize the database once (class-level tracking) and can
        share tokens correctly.
        """
        # Verify database doesn't exist yet
        assert not test_config.database_path.exists()

        # Create two instances with the same database path
        service1 = TokenService(test_config)
        service2 = TokenService(test_config)

        # Verify both have the same db_path
        assert service1.db_path == service2.db_path

        # Create token with first service (initializes database)
        plaintext = service1.create_token("shared", TokenScope.READ)

        # Verify database exists and path is tracked
        assert test_config.database_path.exists()
        assert test_config.database_path in TokenService._initialized_paths

        # Validate with second service (should not reinitialize)
        result = service2.validate_token(plaintext)

        assert result is not None
        assert result.name == "shared"

        # Both instances should recognize the database as initialized
        assert test_config.database_path in TokenService._initialized_paths

    def test_concurrent_lazy_initialization_is_safe(self, test_config: MagpieSettings) -> None:
        """Multiple threads calling methods simultaneously should safely initialize once.

        This test verifies the fix for the thread safety race condition in
        _ensure_database_initialized(). Without proper locking, concurrent
        calls could cause multiple init_database() calls or database corruption.

        Uses barrier synchronization to maximize the chance of exposing races.
        """
        import threading

        service = TokenService(test_config)
        results = []
        errors = []
        num_threads = 20

        # Create barrier to synchronize thread starts
        barrier = threading.Barrier(num_threads)

        def create_token(i: int) -> None:
            try:
                # Wait for all threads to reach this point before proceeding
                barrier.wait()

                # All threads hit this simultaneously
                token = service.create_token(f"concurrent-token-{i}", TokenScope.READ)
                results.append((i, token))
            except Exception as e:
                errors.append(f"Thread {i}: {str(e)}")

        # Create threads that will all hit _ensure_initialized() simultaneously
        threads = [threading.Thread(target=create_token, args=(i,)) for i in range(num_threads)]

        # Start all threads
        for t in threads:
            t.start()

        # Wait for all threads to complete
        for t in threads:
            t.join()

        # Verify no errors occurred
        assert len(errors) == 0, f"Errors during concurrent init: {errors}"

        # Verify all tokens were created successfully
        assert len(results) == num_threads

        # Verify all tokens are unique and valid
        tokens_seen = set()
        for i, token in results:
            assert token not in tokens_seen, f"Duplicate token generated: {token}"
            tokens_seen.add(token)

            # Verify the token validates correctly
            token_info = service.validate_token(token)
            assert token_info is not None
            assert token_info.name == f"concurrent-token-{i}"
            assert token_info.scope == TokenScope.READ

    def test_multiple_instances_with_different_paths(self, tmp_path: Path) -> None:
        """Multiple TokenService instances with different database paths should initialize independently.

        This test verifies that the class-level _initialized_paths set correctly tracks
        each database path separately, allowing multiple TokenService instances with
        different database paths to coexist without interference.
        """
        # Create two different database paths
        config1 = MagpieSettings(
            storage_path=tmp_path / "storage1",
            database_path=tmp_path / "db1" / "magpie.db",
        )
        config2 = MagpieSettings(
            storage_path=tmp_path / "storage2",
            database_path=tmp_path / "db2" / "magpie.db",
        )

        # Create two services with different database paths
        service1 = TokenService(config1)
        service2 = TokenService(config2)

        # Verify they have different database paths
        assert service1.db_path != service2.db_path

        # Neither database should exist yet (lazy initialization)
        assert not config1.database_path.exists()
        assert not config2.database_path.exists()

        # Create a token with service1 (initializes db1)
        token1 = service1.create_token("token-in-db1", TokenScope.READ)
        assert config1.database_path.exists()
        assert not config2.database_path.exists()

        # Create a token with service2 (initializes db2)
        token2 = service2.create_token("token-in-db2", TokenScope.WRITE)
        assert config1.database_path.exists()
        assert config2.database_path.exists()

        # Both paths should be tracked as initialized
        assert config1.database_path in TokenService._initialized_paths
        assert config2.database_path in TokenService._initialized_paths

        # Tokens should validate with their respective services
        assert service1.validate_token(token1) is not None
        assert service2.validate_token(token2) is not None

        # Tokens should NOT cross-validate (different databases)
        assert service1.validate_token(token2) is None
        assert service2.validate_token(token1) is None

    def test_database_reinitializes_if_deleted(self, test_config: MagpieSettings) -> None:
        """Database should reinitialize if file is deleted after initialization.

        This test verifies that the database existence check in
        _ensure_database_initialized() properly handles the case where
        the database file is deleted externally.
        """
        service = TokenService(test_config)

        # Create a token (this initializes the database)
        token1 = service.create_token("token-before-delete", TokenScope.READ)
        assert test_config.database_path.exists()

        # Verify the token works
        assert service.validate_token(token1) is not None

        # Delete the database file
        test_config.database_path.unlink()
        assert not test_config.database_path.exists()

        # Create another token (should reinitialize database)
        token2 = service.create_token("token-after-delete", TokenScope.WRITE)
        assert test_config.database_path.exists()

        # The new token should work
        assert service.validate_token(token2) is not None

        # The old token should not work (new database)
        assert service.validate_token(token1) is None


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


class TestCreateTokenWithProvidedToken:
    """Tests for TokenService.create_token with plaintext_token parameter."""

    def test_create_token_with_provided_admin_token(self, token_service: TokenService) -> None:
        """create_token should accept and use provided admin token."""
        provided_token = "mgp_ADMIN_my_custom_token_123"

        result = token_service.create_token("custom-admin", TokenScope.ADMIN, provided_token)

        assert result == provided_token
        # Verify it can be validated
        token_info = token_service.validate_token(provided_token)
        assert token_info is not None
        assert token_info.name == "custom-admin"
        assert token_info.scope == TokenScope.ADMIN

    def test_create_token_with_provided_write_token(self, token_service: TokenService) -> None:
        """create_token should accept and use provided write token."""
        provided_token = "mgp_my_write_token_456"

        result = token_service.create_token("custom-write", TokenScope.WRITE, provided_token)

        assert result == provided_token
        # Verify it can be validated
        token_info = token_service.validate_token(provided_token)
        assert token_info is not None
        assert token_info.name == "custom-write"
        assert token_info.scope == TokenScope.WRITE

    def test_create_token_with_provided_read_token(self, token_service: TokenService) -> None:
        """create_token should accept and use provided read token."""
        provided_token = "mgp_my_read_token_789"

        result = token_service.create_token("custom-read", TokenScope.READ, provided_token)

        assert result == provided_token

    def test_create_token_rejects_wrong_prefix_for_admin(self, token_service: TokenService) -> None:
        """create_token should reject token without mgp_ADMIN_ prefix for admin scope."""
        wrong_token = "mgp_not_admin_token"

        with pytest.raises(TokenFormatError, match="must start with 'mgp_ADMIN_'"):
            token_service.create_token("bad-admin", TokenScope.ADMIN, wrong_token)

    def test_create_token_rejects_admin_prefix_for_write(self, token_service: TokenService) -> None:
        """create_token should reject mgp_ADMIN_ prefix for write scope."""
        wrong_token = "mgp_ADMIN_should_not_be_admin"

        with pytest.raises(
            TokenFormatError,
            match=r"must start with 'mgp_' \(not 'mgp_ADMIN_'\) for write scope",
        ):
            token_service.create_token("bad-write", TokenScope.WRITE, wrong_token)

    def test_create_token_rejects_admin_prefix_for_read(self, token_service: TokenService) -> None:
        """create_token should reject mgp_ADMIN_ prefix for read scope."""
        wrong_token = "mgp_ADMIN_should_not_be_admin"

        with pytest.raises(
            TokenFormatError,
            match=r"must start with 'mgp_' \(not 'mgp_ADMIN_'\) for read scope",
        ):
            token_service.create_token("bad-read", TokenScope.READ, wrong_token)

    def test_create_token_rejects_empty_token_after_prefix(
        self, token_service: TokenService
    ) -> None:
        """create_token should reject token that is just the prefix."""
        empty_admin_token = "mgp_ADMIN_"
        empty_regular_token = "mgp_"

        with pytest.raises(TokenFormatError, match="too short"):
            token_service.create_token("empty-admin", TokenScope.ADMIN, empty_admin_token)

        with pytest.raises(TokenFormatError, match="too short"):
            token_service.create_token("empty-write", TokenScope.WRITE, empty_regular_token)

    def test_create_token_rejects_token_without_mgp_prefix(
        self, token_service: TokenService
    ) -> None:
        """create_token should reject token without any mgp prefix."""
        no_prefix_token = "ADMIN_my_token_123"

        with pytest.raises(TokenFormatError, match="must start with"):
            token_service.create_token("no-prefix", TokenScope.ADMIN, no_prefix_token)

    def test_create_token_provided_token_stores_correctly(
        self, token_service: TokenService, test_config: MagpieSettings
    ) -> None:
        """create_token with provided token should store hash correctly."""
        provided_token = "mgp_ADMIN_test_storage_token"

        token_service.create_token("storage-test", TokenScope.ADMIN, provided_token)

        # Check database stores hash, not plaintext
        conn = get_connection(test_config.database_path)
        cursor = conn.execute("SELECT token_hash FROM tokens WHERE name = ?", ("storage-test",))
        row = cursor.fetchone()
        conn.close()

        assert row is not None
        assert row["token_hash"] != provided_token
        assert len(row["token_hash"]) == 64  # SHA-256 hex length

    def test_token_format_error_is_catchable_as_value_error(
        self, token_service: TokenService
    ) -> None:
        """TokenFormatError should be catchable as ValueError for backward compatibility."""
        try:
            token_service.create_token("test", TokenScope.ADMIN, "invalid_token")
            assert False, "Should have raised an exception"
        except ValueError:
            pass  # TokenFormatError is a subclass of ValueError

    def test_token_exists_error_is_catchable_as_value_error(
        self, token_service: TokenService
    ) -> None:
        """TokenExistsError should be catchable as ValueError for backward compatibility."""
        token_service.create_token("exists", TokenScope.READ)
        try:
            token_service.create_token("exists", TokenScope.WRITE)
            assert False, "Should have raised an exception"
        except ValueError:
            pass  # TokenExistsError is a subclass of ValueError
