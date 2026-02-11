"""Token service for authentication operations."""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from magpie.auth.database import (
    delete_token,
    get_connection,
    get_token_by_hash,
    get_token_by_name,
    init_database,
    save_token,
)
from magpie.auth.models import Token, TokenScope
from magpie.validation import validate_token_name

if TYPE_CHECKING:
    from magpie.config import MagpieSettings


logger = logging.getLogger(__name__)

# Minimum recommended token length (including prefix)
# Auto-generated tokens are 54+ chars (10 char prefix + 43 char token_urlsafe(32))
RECOMMENDED_MIN_TOKEN_LENGTH = 32

# Scope hierarchy levels (higher number = more permissions)
_SCOPE_LEVELS = {
    TokenScope.READ: 1,
    TokenScope.WRITE: 2,
    TokenScope.ADMIN: 3,
}


@dataclass
class TokenInfo:
    """Validation result containing token identity and scope.

    Returned by validate_token when a token is valid and enabled.
    Does not contain sensitive information like the token hash.
    """

    name: str
    scope: TokenScope


class TokenError(ValueError):
    """Base exception for token-related errors.

    Inherits from ValueError for backward compatibility.
    """

    pass


class TokenExistsError(TokenError):
    """Raised when attempting to create a token that already exists."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"Token name '{name}' already exists")


class TokenFormatError(TokenError):
    """Raised when a token has invalid format (wrong prefix, too short, etc.)."""

    pass


class TokenService:
    """Service for token management operations.

    Provides create, validate, and revoke operations for authentication tokens.
    Handles secure token generation, hashing, and scope validation.

    Token Prefix Convention:
        - Regular tokens (read/write): mgp_ prefix
        - Admin tokens: mgp_ADMIN_ prefix
    """

    def __init__(self, config: MagpieSettings) -> None:
        """Initialize the token service and database.

        Args:
            config: MagpieSettings instance with database_path configuration.
        """
        self.config = config
        self.db_path = config.database_path
        init_database(self.db_path)

    def create_token(self, name: str, scope: TokenScope, plaintext_token: str | None = None) -> str:
        """Create a new token and return the plaintext (only visible once).

        Uses secrets.token_urlsafe(32) for secure random generation by default.
        Stores SHA-256 hash of token, never the plaintext.

        Token Prefix Convention:
            - Regular tokens (read/write): mgp_ prefix
            - Admin tokens: mgp_ADMIN_ prefix

        Args:
            name: Human-readable name for the token (must be unique).
            scope: Permission level (read/write/admin).
            plaintext_token: Optional pre-generated token. If provided, must match
                the expected prefix for the scope. Used for admin token initialization.

        Returns:
            Plaintext token string (mgp_... format) - only returned once.

        Raises:
            TokenExistsError: If token name already exists.
            TokenFormatError: If provided token has incorrect format.
            ValidationError: If token name fails validation (invalid format/length).
        """
        # Validate token name (defense in depth - also validated at API layer)
        validate_token_name(name)

        if plaintext_token is None:
            # Generate secure random token
            plaintext_token = self._generate_plaintext_token(scope)
        else:
            # Validate provided token has correct prefix
            if scope == TokenScope.ADMIN:
                # Admin tokens must have mgp_ADMIN_ prefix
                if not plaintext_token.startswith("mgp_ADMIN_"):
                    raise TokenFormatError(
                        "Provided token must start with 'mgp_ADMIN_' for admin scope"
                    )
                expected_prefix = "mgp_ADMIN_"
            else:
                # Read/write tokens must have mgp_ prefix but NOT mgp_ADMIN_
                if not plaintext_token.startswith("mgp_"):
                    raise TokenFormatError(
                        f"Provided token must start with 'mgp_' for {scope.value} scope"
                    )
                if plaintext_token.startswith("mgp_ADMIN_"):
                    raise TokenFormatError(
                        f"Provided token must start with 'mgp_' (not 'mgp_ADMIN_') for {scope.value} scope"
                    )
                expected_prefix = "mgp_"

            # Validate token format (must be non-empty after prefix)
            if len(plaintext_token) <= len(expected_prefix):
                raise TokenFormatError(
                    f"Provided token is too short (must have content after '{expected_prefix}' prefix, "
                    f"minimum length: {len(expected_prefix) + 1})"
                )

            # Warn if custom token is shorter than recommended
            # (we don't reject to avoid breaking changes, but log a warning)
            if len(plaintext_token) < RECOMMENDED_MIN_TOKEN_LENGTH:
                logger.warning(
                    "Custom token for '%s' is shorter than recommended (%d chars). "
                    "Consider using at least %d characters for adequate entropy.",
                    name,
                    len(plaintext_token),
                    RECOMMENDED_MIN_TOKEN_LENGTH,
                )

        # Hash the token for storage
        token_hash = self._hash_token(plaintext_token)

        # Create token model
        token = Token(
            name=name,
            token_hash=token_hash,
            scope=scope,
            enabled=True,
            created_at=datetime.now(timezone.utc),
        )

        # Save to database
        conn = get_connection(self.db_path)
        try:
            save_token(conn, token)
        except sqlite3.IntegrityError as e:
            raise TokenExistsError(name) from e
        finally:
            conn.close()

        # Return plaintext (only time it's visible)
        return plaintext_token

    def validate_token(self, token: str) -> TokenInfo | None:
        """Validate a token and return its info if valid.

        Uses timing-safe comparison via hmac.compare_digest() as defense
        in depth against timing attacks.

        Args:
            token: Plaintext token to validate.

        Returns:
            TokenInfo if valid and enabled, None otherwise.
        """
        # Hash the input token
        token_hash = self._hash_token(token)

        # Lookup by hash
        conn = get_connection(self.db_path)
        try:
            stored_token = get_token_by_hash(conn, token_hash)
        finally:
            conn.close()

        # Check if token exists
        if stored_token is None:
            return None

        # Use timing-safe comparison (defense in depth)
        if not hmac.compare_digest(stored_token.token_hash, token_hash):
            return None

        # Check if token is enabled
        if not stored_token.enabled:
            return None

        return TokenInfo(
            name=stored_token.name,
            scope=stored_token.scope,
        )

    def revoke_token(self, name: str) -> bool:
        """Revoke a token by name.

        Permanently deletes the token from the database.

        Args:
            name: Name of the token to revoke.

        Returns:
            True if token was revoked, False if not found.
        """
        conn = get_connection(self.db_path)
        try:
            return delete_token(conn, name)
        finally:
            conn.close()

    def rotate_token(self, name: str) -> tuple[str, TokenScope] | None:
        """Rotate a token by revoking and creating a new one with the same scope.

        This is an atomic operation that:
        1. Looks up the existing token by name
        2. Deletes the old token
        3. Creates a new token with the same name and scope
        4. Returns the new plaintext token and its scope

        If the token doesn't exist, returns None.

        Both the deletion and creation happen within a single database transaction
        to ensure atomicity - there is no window where the token doesn't exist.

        Args:
            name: Name of the token to rotate.

        Returns:
            Tuple of (plaintext_token, scope) if rotation succeeded, None if token not found.

        Raises:
            TokenError: If hash collision occurs during token creation (extremely unlikely).
        """
        conn = get_connection(self.db_path)
        try:
            with conn:
                # Look up existing token to get its scope
                existing_token = get_token_by_name(conn, name)
                if existing_token is None:
                    # Nothing to rotate; no changes committed
                    return None

                # Store scope before deletion
                scope = existing_token.scope

                # Delete old token (without committing - part of transaction)
                delete_token(conn, name, commit=False)

                # Create and persist new token with same name and scope (without committing)
                try:
                    new_plaintext = self._create_and_save_token(conn, name, scope)
                except sqlite3.IntegrityError as e:
                    # Extremely unlikely hash collision during rotation
                    raise TokenError(
                        f"Failed to rotate token '{name}' due to hash collision. Please try again."
                    ) from e

            # Transaction commits here when exiting the 'with conn:' context
            return (new_plaintext, scope)
        finally:
            conn.close()

    def _create_and_save_token(
        self,
        conn: sqlite3.Connection,
        name: str,
        scope: TokenScope,
    ) -> str:
        """Create a new token with the given name and scope using an existing connection.

        This helper is used by rotate_token to ensure that token deletion and creation
        happen within a single database transaction.

        Args:
            conn: Existing database connection.
            name: Token name.
            scope: Token scope.

        Returns:
            Plaintext token string.
        """
        # Validate token name defensively (defense in depth)
        validate_token_name(name)

        # Generate a new secure plaintext token
        plaintext = self._generate_plaintext_token(scope)

        # Hash the token for storage
        token_hash = self._hash_token(plaintext)

        # Persist the new token record (without committing - part of transaction)
        token = Token(
            name=name,
            token_hash=token_hash,
            scope=scope,
            created_at=datetime.now(timezone.utc),
            enabled=True,
        )
        save_token(conn, token, commit=False)
        return plaintext

    def has_scope(self, token_scope: TokenScope, required_scope: TokenScope) -> bool:
        """Check if token_scope satisfies required_scope.

        Hierarchy: admin > write > read
        - ADMIN can access everything
        - WRITE can access write and read operations
        - READ can only access read operations

        Args:
            token_scope: The scope of the token being checked.
            required_scope: The minimum scope required for the operation.

        Returns:
            True if token_scope has sufficient permissions.
        """
        return _SCOPE_LEVELS[token_scope] >= _SCOPE_LEVELS[required_scope]

    def _generate_plaintext_token(self, scope: TokenScope) -> str:
        """Generate a new plaintext token with appropriate prefix.

        Args:
            scope: Token scope (determines prefix).

        Returns:
            Plaintext token string with mgp_ or mgp_ADMIN_ prefix.
        """
        random_part = secrets.token_urlsafe(32)
        if scope == TokenScope.ADMIN:
            return f"mgp_ADMIN_{random_part}"
        else:
            return f"mgp_{random_part}"

    def _hash_token(self, token: str) -> str:
        """Hash a token using SHA-256.

        Args:
            token: Plaintext token to hash.

        Returns:
            Hexadecimal SHA-256 hash of the token.
        """
        return hashlib.sha256(token.encode()).hexdigest()
